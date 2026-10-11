#!/usr/bin/env python3
"""Pi Pet: a little raspberry that lives in your terminal and reacts to the Pi's real vitals.

    python3 piPet.py              # live: press p to pet it, q to quit
    python3 piPet.py --once       # print one frame and exit (no curses)
    python3 piPet.py --mood hot   # force a mood (--help lists them all)
    python3 piPet.py --iface eth0 # watch one network interface instead of following the default route
    python3 piPet.py --help       # list the options
"""

import argparse
import curses
import glob
import math
import os
import random
import time

FPS = 4
DEFAULT_INTERFACE = 'wlan0'  # used when there's no default route to follow

# Smallest window that fits the pet and the status line; anything smaller gets a message instead
PET_WIDTH = 13
MIN_WIDTH = 24
MIN_HEIGHT = 11

# Big windows also get a scene: sky across the top and grass under the pet's feet
SKY_HEIGHT = 3
SCENE_MIN_WIDTH = 40
GROUND_PATTERN = "_.,_'_,."

# Thresholds that decide the pet's mood
HOT_TEMP_C = 70
BUSY_LOAD = 2.0
SURF_BYTES_PER_SEC = 200 * 1024
SLEEPY_LOAD = 0.3
SLEEPY_BYTES_PER_SEC = 5 * 1024
STUFFED_DISK_PERCENT = 90   # SD card this full or more
HUNGRY_MEMORY_PERCENT = 10  # less memory than this still available
GROGGY_UPTIME_SEC = 5 * 60  # booted less than this long ago

PET_SECONDS = 3  # how long the pet stays happy after you press p

# Network speed is only measured over at least this long; one packet over a few microseconds reads as a huge speed
MIN_RATE_SECONDS = 0.5

# Faces: (left eye, right eye, mouth). Every character must be exactly one terminal column wide,
# so no emoji or East Asian wide characters, or the body falls out of alignment.
FACES = {
    'content': [('o', 'o', '‿'), ('-', '-', '‿')],  # second frame is a blink
    'sleepy': [('-', '-', '_')],
    'hot': [('>', '<', '~')],
    'busy': [('o', 'o', 'o')],
    'surfing': [('^', '^', 'w')],
    'dizzy': [('@', '@', '~')],
    'hungry': [('o', 'o', 'O'), ('o', 'o', 'o')],  # chomping
    'stuffed': [('u', 'u', '='), ('u', 'u', 'o')],  # second frame is a burp
    'groggy': [('-', 'o', '_'), ('-', '-', 'O')],  # one eye open; second frame is a yawn
    'lonely': [('.', '.', 'n')],
    'happy': [('^', '^', '‿')],  # only when you pet it
}

SAYINGS = {
    'content': ['all good in here', 'just vibing', 'nice and cool', 'hi Bobby!'],
    'sleepy': ['zzz...', 'so quiet...', 'wake me if something happens'],
    'hot': ['it is getting toasty', 'fan, do your thing!', 'need... ice...'],
    'busy': ['working hard!', 'so many processes', 'cpu go brrr'],
    'surfing': ['whee, packets!', "who's downloading?", 'riding the wifi waves'],
    'dizzy': ['power feels low...', 'everything is spinning', 'is that the official 27W charger?'],
    'hungry': ['need... more... RAM', 'feed me memory', 'tummy rumbling', 'too many tabs open?'],
    'stuffed': ['no room for dessert', 'so full...', 'maybe delete some files?', 'I ate the whole SD card'],
    'groggy': ['five more minutes...', 'just woke up', 'is it morning?', 'booting... slowly...'],
    'lonely': ['hello? anyone?', 'where did the internet go?', 'all alone out here', 'is the router okay?'],
    'happy': ['hehe!', 'more please', 'that tickles!', 'best human ever'],
}

# Friends who visit, and what the pet says to them. Only calm moods chat; urgent ones stick to their own news.
CLAUDE_GREETING = 'oh hi Claude!'
CLAUDE_SAYINGS = ['Claude is here!', 'hey Claude, look at my sky', 'Claude, wanna be friends?']
GUARD_SAYINGS = ['the guard is on duty', 'no ads get past us', 'thanks for guarding, buddy']
CHATTY_MOODS = ('content', 'busy', 'surfing')
FRIEND_SAYING_CHANCE = 1 / 3

# Pals are drawn as frames of equal-width lines; every character one column wide, like the pet
CLAUDE_PAL = [
    [' \\|/ ', '-^‿^-', ' /|\\ '],
    [' .|. ', '-^‿^-', " '|' "],  # twinkle
]

# The AdGuard Home guard: on watch while the service runs, asleep if it stops
GUARD_ON_WATCH = [
    [' _^_ ', '(o_o)', '/[+]\\'],
    [' _^_ ', '(<_<)', '/[+]\\'],  # glances left
    [' _^_ ', '(o_o)', '/[+]\\'],
    [' _^_ ', '(>_>)', '/[+]\\'],  # glances right
]
GUARD_ASLEEP = [
    [' _^_z', '(-_-)', '/[+]\\'],
    [' _^_Z', '(-_-)', '/[+]\\'],
]
ADGUARD_INSTALL_PATHS = ('/opt/AdGuardHome/AdGuardHome', '/etc/systemd/system/AdGuardHome.service')


# ---------------------------------------------------------------------------
# Reading the Pi
# ---------------------------------------------------------------------------

def read_number(path):
    try:
        with open(path) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return None


def read_temp_c():
    milli = read_number('/sys/class/thermal/thermal_zone0/temp')
    return None if milli is None else milli / 1000


def read_fan_rpm():
    matches = glob.glob('/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input')
    return read_number(matches[0]) if matches else None


def read_net_bytes(interface):
    """Total bytes received + sent on a network interface so far, or None if it isn't there."""
    try:
        with open('/proc/net/dev') as f:
            for line in f:
                name, _, counters = line.partition(':')
                if name.strip() == interface:
                    fields = counters.split()
                    return int(fields[0]) + int(fields[8])
    except (OSError, ValueError, IndexError):
        pass
    return None


def get_default_interface():
    """The interface that carries the default route (internet traffic), or None.

    If several are up (say Wi-Fi and Ethernet), the lowest metric wins, the same one Linux uses.
    """
    best = None  # (metric, name)
    try:
        with open('/proc/net/route') as f:
            next(f, None)  # header line
            for line in f:
                fields = line.split()
                if len(fields) < 7:
                    continue
                name, destination, flags, metric = fields[0], fields[1], int(fields[3], 16), int(fields[6])
                if destination == '00000000' and flags & 1:  # flag 1 means the route is up
                    if best is None or metric < best[0]:
                        best = (metric, name)
    except (OSError, ValueError):
        return None
    return best[1] if best else None


def read_undervoltage():
    """True if the kernel's undervoltage alarm is set (the power supply can't keep up), None if there's no sensor.

    The Pi's voltage monitor is the hwmon device named rpi_volt; its hwmon number can change between boots.
    """
    for name_path in glob.glob('/sys/class/hwmon/hwmon*/name'):
        try:
            with open(name_path) as f:
                if f.read().strip() != 'rpi_volt':
                    continue
        except OSError:
            continue
        alarm = read_number(os.path.join(os.path.dirname(name_path), 'in0_lcrit_alarm'))
        return None if alarm is None else alarm == 1
    return None


def read_memory_free_percent():
    """Memory programs can still get (MemAvailable) as a percentage of all memory, or None."""
    values = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                key, _, rest = line.partition(':')
                if key in ('MemTotal', 'MemAvailable'):
                    values[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        return None
    if not values.get('MemTotal') or 'MemAvailable' not in values:
        return None
    return values['MemAvailable'] / values['MemTotal'] * 100


def read_disk_used_percent(path='/'):
    """How full the disk is, worked out the same way as df's Use% column, or None."""
    try:
        stats = os.statvfs(path)
    except OSError:
        return None
    used = stats.f_blocks - stats.f_bfree
    usable = used + stats.f_bavail  # df leaves out the blocks reserved for root
    return used / usable * 100 if usable else None


def read_uptime_sec():
    """Seconds since the Pi booted, or None."""
    try:
        with open('/proc/uptime') as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def read_process_names():
    """The names of every running process (from /proc/*/comm, so at most 15 characters each)."""
    names = set()
    for comm_path in glob.glob('/proc/[0-9]*/comm'):
        try:
            with open(comm_path) as f:
                names.add(f.read().strip())
        except OSError:
            continue  # the process ended while we looked
    return names


def is_adguard_installed():
    return any(os.path.exists(path) for path in ADGUARD_INSTALL_PATHS)


class Vitals:
    """Polls the sensors and works out network speed between polls."""

    def __init__(self, forced_mood=None, interface=None):
        self.forced_mood = forced_mood
        self.chosen_interface = interface  # from --iface; None means follow the default route
        self.interface = interface or get_default_interface() or DEFAULT_INTERFACE
        self.last_net = read_net_bytes(self.interface)
        self.last_time = time.monotonic()
        self.net_rate = 0
        self.refresh()

    def refresh(self):
        self.temp_c = read_temp_c()
        self.load = os.getloadavg()[0]
        self.fan_rpm = read_fan_rpm()
        self.undervoltage = read_undervoltage()
        self.memory_free_percent = read_memory_free_percent()
        self.disk_used_percent = read_disk_used_percent()
        self.uptime_sec = read_uptime_sec()
        self.online = get_default_interface() is not None
        processes = read_process_names()
        self.claude_here = 'claude' in processes  # a Claude Code session, in VS Code or the terminal
        # None when AdGuard Home isn't installed, so other Pis get no guard at all
        self.adguard_running = 'AdGuardHome' in processes if is_adguard_installed() else None
        now = time.localtime()
        self.hour = now.tm_hour + now.tm_min / 60  # local time, for the sun and moon
        self.update_net_rate()

    def update_net_rate(self):
        """Bytes per second since the last reading that was at least MIN_RATE_SECONDS ago."""
        if not self.chosen_interface:
            # Follow the default route; a switch (Wi-Fi to Ethernet, say) starts a fresh measurement
            interface = get_default_interface() or DEFAULT_INTERFACE
            if interface != self.interface:
                self.interface, self.last_net = interface, None
        now = time.monotonic()
        net = read_net_bytes(self.interface)
        if net is None or self.last_net is None:
            # Interface missing, or just came back: no speed until there are two readings to compare
            self.net_rate = 0
            self.last_net, self.last_time = net, now
            return
        elapsed = now - self.last_time
        if elapsed < MIN_RATE_SECONDS:
            return
        # The byte counter restarts from 0 if the interface resets, which would read as a negative speed
        self.net_rate = max(0, (net - self.last_net) / elapsed)
        self.last_net, self.last_time = net, now

    def mood(self):
        """The first mood that fits, most urgent first. Missing sensors (None) never trigger a mood."""
        if self.forced_mood:
            return self.forced_mood
        if self.undervoltage:
            return 'dizzy'
        if self.temp_c is not None and self.temp_c >= HOT_TEMP_C:
            return 'hot'
        if self.disk_used_percent is not None and self.disk_used_percent >= STUFFED_DISK_PERCENT:
            return 'stuffed'
        if self.memory_free_percent is not None and self.memory_free_percent < HUNGRY_MEMORY_PERCENT:
            return 'hungry'
        # Groggy beats lonely and busy: right after boot, Wi-Fi isn't up yet and the load is high
        if self.uptime_sec is not None and self.uptime_sec < GROGGY_UPTIME_SEC:
            return 'groggy'
        if not self.online:
            return 'lonely'
        if self.load >= BUSY_LOAD:
            return 'busy'
        if self.net_rate >= SURF_BYTES_PER_SEC:
            return 'surfing'
        if self.load < SLEEPY_LOAD and self.net_rate < SLEEPY_BYTES_PER_SEC:
            return 'sleepy'
        return 'content'


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def format_rate(bytes_per_sec):
    if bytes_per_sec >= 1024 * 1024:
        return f'{bytes_per_sec / 1024 / 1024:.1f} MB/s'
    return f'{bytes_per_sec / 1024:.1f} KB/s'


def pick_face(mood, tick):
    """The face for this frame. Moods with a second face show it on their own schedule."""
    frames = FACES[mood]
    if mood == 'content' and tick % 12 == 0:  # blink every ~3 seconds
        return frames[1]
    if mood == 'hungry':  # chomp, chomp, twice a second
        return frames[tick // 2 % 2]
    if mood == 'stuffed' and tick % 16 >= 14:  # a burp every ~4 seconds
        return frames[1]
    if mood == 'groggy' and tick % 16 >= 12:  # a one-second yawn every ~4 seconds
        return frames[1]
    return frames[0]


def build_pet(mood, tick):
    """Return the pet as a list of (text, color) lines. Colors: leaf, berry, extra."""
    left_eye, right_eye, mouth = pick_face(mood, tick)

    # Busy pet bounces up and down, happy pet hops for joy; surfing pet sways left and right
    bounce = mood in ('busy', 'happy') and tick % 2 == 0
    sway = ' ' * (tick % 4 if mood == 'surfing' else 0)

    lines = [
        ('    \\\\|//    ', 'leaf'),
        ('   .-\'~\'-.   ', 'berry'),
        (f'  / {left_eye}   {right_eye} \\  ', 'berry'),
        (f' |  : {mouth} :  | ', 'berry'),
        ('  \\ :   : /  ', 'berry'),
        ("   '-...-'   ", 'berry'),
    ]

    # Little legs that run when busy
    if mood == 'busy':
        lines.append(('    /   \\    ' if tick % 2 else '     | |     ', 'berry'))
    else:
        lines.append(('     ^ ^     ', 'berry'))

    if mood == 'hot':
        # Sweat drops beside the head
        drop_row = 2 + tick % 3
        text, color = lines[drop_row]
        lines[drop_row] = ("'" + text[1:-1] + "'", color)
    if mood == 'stuffed':
        # Puffed-up cheeks
        lines[3] = (f' | (: {mouth} :) | ', 'berry')
    if mood == 'surfing':
        lines.append(('~^~^~^~^~^~^~', 'extra'))

    lines = [(sway + text, color) for text, color in lines]
    if bounce:
        lines = [('', 'berry')] + lines
    return lines


def build_bubble(text):
    width = len(text) + 2
    return [
        ' .' + '-' * width + '.',
        f'(  {text}  )',
        " '" + '-' * width + "'",
        '      \\',
    ]


def build_status(vitals):
    temp = '--' if vitals.temp_c is None else f'{vitals.temp_c:.1f}°C'
    fan = '--' if vitals.fan_rpm is None else f'{int(vitals.fan_rpm)} rpm'
    return f'temp {temp}  ·  load {vitals.load:.2f}  ·  fan {fan}  ·  {vitals.interface} {format_rate(vitals.net_rate)}'


def build_short_status(vitals):
    """A narrower status line for small windows."""
    temp = '--' if vitals.temp_c is None else f'{vitals.temp_c:.1f}°C'
    fan = '--' if vitals.fan_rpm is None else f'{int(vitals.fan_rpm)}'
    return f'{temp}  load {vitals.load:.2f}  fan {fan}  {format_rate(vitals.net_rate)}'


def build_alerts(vitals):
    """Short notes for readings outside the normal range, or '' when all is well."""
    alerts = []
    if vitals.undervoltage:
        alerts.append('power low')
    if vitals.disk_used_percent is not None and vitals.disk_used_percent >= STUFFED_DISK_PERCENT:
        alerts.append(f'disk {vitals.disk_used_percent:.0f}% full')
    if vitals.memory_free_percent is not None and vitals.memory_free_percent < HUNGRY_MEMORY_PERCENT:
        alerts.append(f'memory {vitals.memory_free_percent:.0f}% free')
    if not vitals.online:
        alerts.append('offline')
    if vitals.adguard_running is False:
        alerts.append('AdGuard stopped')
    return '  ·  '.join(alerts)


def build_zzz(tick):
    """Floating Zs for the sleepy pet, drifting upward.

    Returns (row, col, letter) measured from the pet's top-left corner. The Zs stay beside the
    head and body (rows 0-5, right of the 13-column body), so they never land on the speech bubble.
    """
    phase = tick % 6
    return [(5 - phase, 14 + phase, 'z'), ((8 - phase) % 6, 16 + (phase + 3) % 6, 'Z')]


def build_cloud(tick):
    """A little rain cloud beside the lonely pet's head, with drops falling past its cheek."""
    cells = [(0, 12, '.-~-.'), (1, 11, '(_____)')]
    for i, col in enumerate((13, 15, 17)):
        cells.append((2 + (tick + i) % 3, col, "'"))
    return cells


def build_stars(tick):
    """Two stars circling the dizzy pet's head."""
    orbit = [(0, 2), (0, 10), (2, 12), (4, 12), (4, 0), (2, 0)]  # clockwise around the head
    return [(*orbit[tick % 6], '*'), (*orbit[(tick + 3) % 6], '*')]


def build_hearts(tick):
    """Two hearts floating up beside the happy pet's head."""
    phase = tick % 6
    return [(5 - phase, 14 + phase % 2, '<3'), ((8 - phase) % 6, 18 - phase % 2, '<3')]


def build_extras(mood, tick):
    """Things drawn beside the pet, as (row, col, text) from the pet's top-left corner.

    They stay beside the pet rather than above it, so they never cover the speech bubble.
    """
    if mood == 'sleepy':
        return build_zzz(tick // 2)
    if mood == 'lonely':
        return build_cloud(tick // 2)
    if mood == 'dizzy':
        return build_stars(tick // 2)
    if mood == 'happy':
        return build_hearts(tick // 2)
    return []


def build_sky(width, hour, tick):
    """The sky across the top of the window as (row, col, text, color): a sun by day, a moon and stars by night.

    The sun (06:00-18:00) and the moon (18:00-06:00) cross from left to right, highest halfway through.
    """
    daytime = 6 <= hour < 18
    progress = ((hour - 6) % 12) / 12  # 0 at sunrise or sunset, 1 twelve hours later
    body = '-O-' if daytime else 'C'
    body_col = round(progress * (width - 1 - len(body)))
    body_row = round((SKY_HEIGHT - 1) * (1 - math.sin(math.pi * progress)))

    cells = []
    if not daytime:
        rng = random.Random(width)  # the same stars for the same window width, so they don't jump around
        spots = {(rng.randrange(SKY_HEIGHT), rng.randrange(width - 1)) for _ in range(width // 8)}
        for i, (row, col) in enumerate(sorted(spots)):
            if row == body_row and body_col - 1 <= col <= body_col + len(body):
                continue  # behind the moon
            twinkle = (tick // 4 + i) % 5 == 0
            cells.append((row, col, '*' if twinkle else '.', 'plain'))
    cells.append((body_row, body_col, body, 'sun' if daytime else 'moon'))
    return cells


def build_pals(vitals, tick):
    """The pals visiting right now, nearest the pet first, as (lines, color) for this frame.

    The guard stands nearest because it's always there, so friends coming and going don't make it move.
    """
    pals = []
    if vitals.adguard_running:
        pals.append((GUARD_ON_WATCH[tick // 4 % len(GUARD_ON_WATCH)], 'guard'))  # a glance every second
    elif vitals.adguard_running is False:
        pals.append((GUARD_ASLEEP[tick // 4 % len(GUARD_ASLEEP)], 'dim'))
    if vitals.claude_here:
        pals.append((CLAUDE_PAL[tick // 4 % 2], 'claude'))  # twinkles about once a second
    return pals


def build_ground(width):
    """A line of grass across the window."""
    return (GROUND_PATTERN * (width // len(GROUND_PATTERN) + 1))[:width - 1]


def layout(height, width, mood, tick, saying, vitals):
    """Work out everything on screen as (row, col, text, color) cells, clipped to the window.

    Colors: leaf, berry, extra, plain, dim, alert, sun, moon, grass, claude, guard. Nothing is placed on the bottom row
    or in the last column, because curses raises an error when writing the window's bottom-right corner.
    When space runs out, the pet and status line stay; the scene (sky and ground), the alert line,
    the bubble and the quit hint go first.
    """
    cells = []

    def add(row, col, text, color):
        if not 0 <= row < height - 1:
            return
        col = max(0, col)
        text = text[:max(0, width - 1 - col)]
        if text:
            cells.append((row, col, text, color))

    if height < MIN_HEIGHT or width < MIN_WIDTH:
        message = 'window too small'
        add(height // 2, (width - len(message)) // 2, message, 'dim')
        return cells

    pet = build_pet(mood, tick)
    bounced = pet[0][0] == ''
    if bounced:
        pet = pet[1:]  # lay out the resting pet, then move it a row, so the bounce moves the pet and not the bubble
    pet_height = len(pet)
    bubble = build_bubble(saying)
    extras = build_extras(mood, tick)
    alerts = build_alerts(vitals)

    usable = height - 1
    body_height = pet_height + 2  # pet, blank row, status line
    if alerts and body_height + 1 <= usable:
        body_height += 1  # alert line under the status line
    else:
        alerts = ''
    show_bubble = body_height + len(bubble) <= usable
    block_height = body_height + (len(bubble) if show_bubble else 0)

    # The scene only shows when everything else fits too (plus a hop row above the pet and the ground under it)
    show_scene = width >= SCENE_MIN_WIDTH and show_bubble and SKY_HEIGHT + block_height + 2 <= usable
    if show_scene:
        top = SKY_HEIGHT + (usable - SKY_HEIGHT - block_height - 2) // 2
        for row, col, text, color in build_sky(width, vitals.hour, tick):
            add(row, col, text, color)
    else:
        top = (usable - block_height) // 2

    # Center the pet, but move left if that keeps a long bubble (or the extras) inside the window
    widest = max([len(text) for text, _ in pet]
                 + ([len(line) for line in bubble] if show_bubble else [])
                 + [col + len(text) for _, col, text in extras])
    left = max(0, min((width - PET_WIDTH) // 2, width - 1 - widest))

    pet_top = top
    if show_bubble:
        for i, line in enumerate(bubble):
            add(top + i, left, line, 'plain')
        pet_top += len(bubble)
    if show_scene:
        pet_top += 1  # hop row
    # Standing on the ground, a bouncing pet hops up into the hop row; without ground it drops into the gap below
    drawn_top = pet_top + (0 if not bounced else -1 if show_scene else 1)
    for i, (text, color) in enumerate(pet):
        add(drawn_top + i, left, text, color)
    # Pals stand to the pet's left with their feet level with its feet, as many as fit
    feet_row = pet_top + pet_height - 1
    pal_right = left - 2
    for lines, color in build_pals(vitals, tick):
        pal_left = pal_right - len(lines[0])
        if pal_left < 0:
            break
        for i, line in enumerate(lines):
            add(feet_row - len(lines) + 1 + i, pal_left, line, color)
        pal_right = pal_left - 2

    extras_color = 'berry' if mood == 'happy' else 'extra'  # red hearts
    for row, col, text in extras:
        add(pet_top + row, left + col, text, extras_color)

    status = build_status(vitals)
    if len(status) > width - 1:
        status = build_short_status(vitals)
    status_row = pet_top + pet_height + 1
    if show_scene:
        add(pet_top + pet_height, 0, build_ground(width), 'grass')
        status_row += 1
    add(status_row, (width - 1 - len(status)) // 2, status, 'dim')
    last_row = status_row
    if alerts:
        last_row += 1
        add(last_row, (width - 1 - len(alerts)) // 2, alerts, 'alert')

    if last_row < height - 2:
        add(height - 2, 1, 'p to pet  ·  q to quit', 'dim')
    return cells


def pick_saying(mood, vitals, rng=random):
    """Something to say for this mood; calm moods sometimes talk to a pal instead."""
    friend_sayings = (CLAUDE_SAYINGS if vitals.claude_here else []) + (GUARD_SAYINGS if vitals.adguard_running else [])
    if friend_sayings and mood in CHATTY_MOODS and rng.random() < FRIEND_SAYING_CHANCE:
        return rng.choice(friend_sayings)
    return rng.choice(SAYINGS[mood])


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def check_interface(name):
    """argparse type for --iface: the name must be a network interface on this machine."""
    try:
        names = os.listdir('/sys/class/net')
    except OSError:
        names = []
    if name not in names:
        raise argparse.ArgumentTypeError(f"no network interface called '{name}' (have: {', '.join(sorted(names))})")
    return name


def parse_args(argv=None):
    """Read the command line. Bad options print a usage message and exit before curses starts."""
    parser = argparse.ArgumentParser(
        description="A little raspberry that lives in your terminal and reacts to the Pi's real vitals.",
    )
    parser.add_argument('--once', action='store_true', help='print one frame and exit')
    parser.add_argument('--mood', choices=list(FACES), help="force a mood instead of reading the Pi's vitals")
    parser.add_argument(
        '--iface', type=check_interface, metavar='NAME',
        help='network interface to watch, like wlan0 or eth0 (default: whichever carries internet traffic)',
    )
    return parser.parse_args(argv)


def print_once(forced_mood=None, interface=None):
    vitals = Vitals(forced_mood, interface)
    time.sleep(1)  # one second of network data so speed isn't 0
    vitals.refresh()
    mood = vitals.mood()

    for line in build_bubble(pick_saying(mood, vitals)):
        print(line)
    for text, _ in build_pet(mood, tick=1):
        print(text)
    print()
    print(f'mood: {mood}')
    print(build_status(vitals))
    alerts = build_alerts(vitals)
    if alerts:
        print(alerts)


def run_live(screen, forced_mood=None, interface=None):
    curses.curs_set(0)
    screen.nodelay(True)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_CYAN, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_BLUE, -1)
    colors = {
        'leaf': curses.color_pair(1) | curses.A_BOLD,
        'berry': curses.color_pair(2) | curses.A_BOLD,
        'extra': curses.color_pair(3),
        'plain': 0,
        'dim': curses.A_DIM,
        'alert': curses.color_pair(2),
        'sun': curses.color_pair(4) | curses.A_BOLD,
        'moon': curses.A_BOLD,
        'grass': curses.color_pair(1),
        'claude': curses.color_pair(4),
        'guard': curses.color_pair(5) | curses.A_BOLD,
    }

    vitals = Vitals(forced_mood, interface)
    tick = 0
    mood = vitals.mood()
    saying = CLAUDE_GREETING if vitals.claude_here else pick_saying(mood, vitals)
    petted_until = 0
    was_petted = False
    claude_was_here = vitals.claude_here

    while True:
        key = screen.getch()
        if key in (ord('q'), ord('Q'), 27):
            return
        if key in (ord('p'), ord('P')):
            if not was_petted:
                saying = random.choice(SAYINGS['happy'])
            petted_until = time.monotonic() + PET_SECONDS  # petting again keeps it happy longer
        petted = time.monotonic() < petted_until

        # Sensors every 2 seconds; a new thing to say every ~10 seconds or on a mood change
        if tick % (FPS * 2) == 0:
            vitals.refresh()
            new_mood = vitals.mood()
            time_for_new_saying = tick > 0 and tick % (FPS * 10) == 0  # not at tick 0: keep the opening line
            if not petted and (new_mood != mood or time_for_new_saying):
                saying = pick_saying(new_mood, vitals)
            if vitals.claude_here and not claude_was_here and not petted:
                saying = CLAUDE_GREETING
            claude_was_here = vitals.claude_here
            mood = new_mood
        if was_petted and not petted:
            saying = pick_saying(mood, vitals)  # back to its real mood
        was_petted = petted

        screen.erase()
        height, width = screen.getmaxyx()
        shown_mood = 'happy' if petted else mood
        for row, col, text, color in layout(height, width, shown_mood, tick, saying, vitals):
            screen.addstr(row, col, text, colors[color])
        screen.refresh()
        tick += 1
        time.sleep(1 / FPS)


def main():
    args = parse_args()
    if args.once:
        print_once(args.mood, args.iface)
        return
    try:
        curses.wrapper(run_live, args.mood, args.iface)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
