#!/usr/bin/env python3
"""Pi Pet: a little raspberry that lives in your terminal and reacts to the Pi's real vitals.

    python3 piPet.py              # live: press p to pet it, q to quit
    python3 piPet.py --once       # print a snapshot of the scene and exit (no curses)
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
import shutil
import sys
import textwrap
import time

FPS = 4
DEFAULT_INTERFACE = 'wlan0'  # used when there's no default route to follow

# Smallest window that fits the pet and the status line; anything smaller gets a message instead
PET_WIDTH = 13
MIN_WIDTH = 24
MIN_HEIGHT = 11

# --once prints a snapshot at the terminal's size, capped so a big terminal doesn't get a page of blank rows
ONCE_MAX_WIDTH = 80
ONCE_MAX_HEIGHT = 24

# --once colors, matching the live view's curses colors (ANSI SGR codes; '' means the terminal's default)
ANSI_COLORS = {
    'leaf': '1;32', 'berry': '1;31', 'extra': '36', 'plain': '', 'dim': '2', 'alert': '31',
    'sun': '1;33', 'moon': '1', 'grass': '32', 'claude': '33', 'guard': '1;34', 'pot': '33', 'cloud': '', 'doctor': '1',
    'sunset': '1;31', 'shooting_star': '1;33', 'planet': '35',
}

# Big windows also get a scene: sky across the top and grass under the pet's feet
SKY_HEIGHT = 3
SCENE_MIN_WIDTH = 40
GROUND_PATTERN = "_.,_'_,."
SYNODIC_MONTH_DAYS = 29.530588853  # new moon to new moon
KNOWN_NEW_MOON = 947182440  # 2000-01-06 18:14 UTC
MOON_SHAPES = [None, ')', 'D', 'O', 'O', 'O', 'C', '(']  # by eighth of the cycle; None is the new moon
MOON_PHASE_NAMES = ['new moon', 'waxing crescent', 'first quarter', 'waxing gibbous', 'full moon',
                    'waning gibbous', 'last quarter', 'waning crescent']
CLOUD_SHAPES = [['.--.', '(____)'], ['.-~~-.', '(______)']]  # top line sits centered over the bottom one
TWILIGHT_STAR_SHARE = 0.4  # at sunset (and at sunrise), this share of a normal night's stars is out
CLOUD_TICKS_PER_COLUMN = 8
PLANET = '-()-'  # a ringed planet: decorative, not a real planet's position
PLANET_DRIFT = 3  # columns per day; like a real planet it stays put all night and shifts night to night
SHOOTING_STAR_WINDOW = FPS * 60  # each minute of night has one chance of a shooting star...
SHOOTING_STAR_CHANCE = 1 / 3     # ...so about one every three minutes
SHOOTING_STAR_TICKS = 6          # each one crosses the sky in 1.5 seconds
SHOOTING_STAR_SPEED = 3          # columns per tick
WISH_SAYING = 'make a wish!'     # what the pet says when it sees a shooting star...
WISH_SECONDS = 4                 # ...and for how long  # half a column a second at 4 FPS

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
DOCTOR_SAYINGS = ["the doctor says I'm healthy", 'just a checkup', 'my vitals are at :3141']
PLANT_SAYINGS = ['look, my plant bloomed!', 'a whole week without a reboot!']
CHATTY_MOODS = ('content', 'busy', 'surfing')
FRIEND_SAYING_CHANCE = 1 / 3

# Pals are drawn as frames of equal-width lines; every character one column wide, like the pet
CLAUDE_PAL = [
    [' \\|/ ', '-^‿^-', ' /|\\ '],
    [' .|. ', '-^‿^-', " '|' "],  # twinkle
]

# The AdGuard Home guard: on watch while the service runs and DNS is being served, alarmed if it runs
# but nothing is bound to the DNS port, asleep if it stops
DNS_PORT = 53
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
GUARD_ALARMED = [
    [' _^_!', '(O_O)', '/[+]\\'],
    [' _^_ ', '(O_O)', '/[+]\\'],  # the ! flashes
]
# The plant grows with uptime, so a reboot repots it. Stages: (days of uptime to reach it, lines from the top)
POT = ('\\___/', 'pot')
PLANT_STAGES = [
    (0, [('  v  ', 'leaf'), POT]),  # sprout
    (1, [('  v  ', 'leaf'), ('  |  ', 'leaf'), POT]),  # seedling
    (3, [('  v  ', 'leaf'), (' \\|/ ', 'leaf'), ('  |  ', 'leaf'), POT]),  # leafy
    (7, [(' (@) ', 'berry'), (' \\|/ ', 'leaf'), ('  |  ', 'leaf'), POT]),  # in flower
]
PLANT_STAGE_NAMES = ['sprout', 'seedling', 'leafy', 'in flower']
PLANT_OFFSET = 24  # columns from the pet's left edge, past the Zs, cloud and hearts

# The Pi Vitals doctor: visits while the Pi Vitals dashboard is listening on its port
PI_VITALS_PORT = 3141
DOCTOR_CHECKING = [
    [' _+_ ', '(o-o)', '/|=|\\'],  # checking on the pet
    [' _+_ ', '(.-.)', '/|=|\\'],  # writing notes
]
DOCTOR_WORRIED = [' _+_ ', '(O-O)', '/|=|\\']
URGENT_MOODS = ('dizzy', 'hot', 'stuffed', 'hungry')

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


def is_port_listening(port):
    """True if something on this machine is listening on a TCP port, from /proc/net/tcp and tcp6.

    Only reads the kernel's socket table, so it never connects to (or shows up in the logs of) the server.
    """
    port_hex = f':{port:04X}'
    for path in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            with open(path) as f:
                next(f, None)  # header line
                for line in f:
                    fields = line.split()
                    if len(fields) > 3 and fields[1].endswith(port_hex) and fields[3] == '0A':  # 0A means listening
                        return True
        except OSError:
            continue
    return False


def is_udp_port_bound(port):
    """True if a UDP socket on this machine is bound to a port and waiting for anyone, the way a DNS server
    waits for queries (from /proc/net/udp and udp6). Like is_port_listening, it never sends anything."""
    port_hex = f':{port:04X}'
    for path in ('/proc/net/udp', '/proc/net/udp6'):
        try:
            with open(path) as f:
                next(f, None)  # header line
                for line in f:
                    fields = line.split()
                    if len(fields) < 4 or not fields[1].endswith(port_hex):
                        continue
                    remote_address = fields[2].split(':')[0]
                    # 07 is an unconnected socket; a server's has no remote address (all zeros)
                    if fields[3] == '07' and set(remote_address) == {'0'}:
                        return True
        except OSError:
            continue
    return False


def is_adguard_installed():
    return any(os.path.exists(path) for path in ADGUARD_INSTALL_PATHS)


class Vitals:
    """Polls the sensors and works out network speed between polls."""

    def __init__(self, forced_mood=None, interface=None):
        self.forced_mood = forced_mood
        self.sky_seed = random.randrange(1 << 30)  # this run's shooting stars
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
        self.pi_vitals_running = is_port_listening(PI_VITALS_PORT)
        # Only asked while AdGuard runs: is it actually serving DNS? None means "not asked"
        self.dns_answering = is_udp_port_bound(DNS_PORT) if self.adguard_running else None
        now = time.localtime()
        self.hour = now.tm_hour + now.tm_min / 60  # local time, for the sun and moon
        self.moon_age = moon_age_days(time.time())
        self.day_of_year = now.tm_yday  # moves the planet a little each night
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

def format_duration(seconds):
    """A short, friendly length of time: '45 s', '12 min', '3 h 5 min', '2 days 4 h'."""
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds} s'
    if seconds < 3600:
        return f'{seconds // 60} min'
    if seconds < 86400:
        return f'{seconds // 3600} h {seconds % 3600 // 60} min'
    days = seconds // 86400
    return f'{days} day{"s" if days != 1 else ""} {seconds % 86400 // 3600} h'


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
    elif vitals.adguard_running and vitals.dns_answering is False:
        alerts.append('DNS port 53 closed')
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


def moon_age_days(timestamp):
    """Days since the last new moon (0 to 29.5), counted from a known new moon. Good to about a day."""
    return ((timestamp - KNOWN_NEW_MOON) / 86400) % SYNODIC_MONTH_DAYS


def moon_eighth(age_days):
    """Which eighth of the lunar cycle the moon is in: 0 new, 2 first quarter, 4 full, 6 last quarter."""
    return int(age_days / SYNODIC_MONTH_DAYS * 8 + 0.5) % 8


def moon_glyph(age_days):
    """The moon's shape as seen from the northern hemisphere, or None at new moon (nothing to see)."""
    return MOON_SHAPES[moon_eighth(age_days)]


def cloud_count(load):
    """The busier the CPU, the cloudier the day: 1 cloud when idle, up to 3."""
    if load < 0.5:
        return 1
    return 2 if load < 1.5 else 3


def build_clouds(width, load, tick):
    """Clouds drifting left to right across the sky as (row, col, text, color), wrapping around.

    They all move at the same speed from evenly spaced starts, so they never run into each other.
    """
    count = cloud_count(load)
    period = width + max(len(shape[-1]) for shape in CLOUD_SHAPES)
    cells = []
    for i in range(count):
        shape = CLOUD_SHAPES[i % len(CLOUD_SHAPES)]
        cloud_width = len(shape[-1])
        start = period // 4 + i * period // count
        cloud_left = (start + tick // CLOUD_TICKS_PER_COLUMN) % period - cloud_width
        for line_no, text in enumerate(shape):
            col = cloud_left + (cloud_width - len(text)) // 2
            if col < 0:
                text, col = text[-col:], 0  # drifting in from the left edge
            if text:
                cells.append((i % 2 + line_no, col, text, 'cloud'))
    return cells


def twilight_strength(hour):
    """How deep into twilight it is: 0 in full daylight or at night, rising to 1 through the last hour
    before sunset (17-18) and falling from 1 through the first hour after sunrise (6-7)."""
    if 17 <= hour < 18:
        return hour - 17
    if 6 <= hour < 7:
        return 7 - hour
    return 0.0


def star_spots(width, count):
    """Where the stars are, as (row, col) in the order they come out.

    The same width always gives the same stars, and a smaller count gives the first few of a bigger
    one, so stars come out at dusk and fade at dawn in place instead of jumping around.
    """
    rng = random.Random(width)
    spots = []
    for _ in range(count):
        spot = (rng.randrange(SKY_HEIGHT), rng.randrange(width - 1))
        if spot not in spots:
            spots.append(spot)
    return spots


def build_shooting_star(width, tick, seed):
    """A shooting star streaking across the sky right now, as (row, col, text), or None (most of the time).

    Each SHOOTING_STAR_WINDOW of ticks has a SHOOTING_STAR_CHANCE of one star at a random moment, decided
    from the seed, so a run's sky is random but the same tick always gives the same answer.
    """
    window, tick_in_window = divmod(tick, SHOOTING_STAR_WINDOW)
    rng = random.Random(f'{seed}:{window}')
    if rng.random() >= SHOOTING_STAR_CHANCE:
        return None
    start = rng.randrange(SHOOTING_STAR_WINDOW - SHOOTING_STAR_TICKS)
    step = tick_in_window - start
    if not 0 <= step < SHOOTING_STAR_TICKS:
        return None

    going_right = rng.random() < 0.5
    distance = (SHOOTING_STAR_TICKS - 1) * SHOOTING_STAR_SPEED
    start_col = rng.randrange(max(1, width - 3 - distance))
    row = step * SKY_HEIGHT // SHOOTING_STAR_TICKS  # falls from the top row to the bottom one
    if going_right:
        col, text = start_col + step * SHOOTING_STAR_SPEED, '--*'
    else:
        col, text = start_col + distance - step * SHOOTING_STAR_SPEED, '*--'
    return row, col, text


def planet_spot(width, day_of_year):
    """Where the ringed planet sits tonight: the same all night, 3 columns further along each day."""
    row = day_of_year // 10 % SKY_HEIGHT
    col = day_of_year * PLANET_DRIFT % (width - len(PLANET))
    return row, col


def build_sky(width, hour, tick, load=0.0, moon_age=SYNODIC_MONTH_DAYS / 2, seed=0, day_of_year=0):
    """The sky across the top of the window as (row, col, text, color): a sun and clouds by day,
    the moon in its real phase, a ringed planet, stars and the odd shooting star by night, and a red
    sun, glowing clouds and the first (or last) stars at dusk and dawn.

    The sun (06:00-18:00) and the moon (18:00-06:00) cross from left to right, highest halfway through.
    The moon's shape is real; its path is simplified (a real moon rises later each night).
    """
    daytime = 6 <= hour < 18
    twilight = twilight_strength(hour)
    progress = ((hour - 6) % 12) / 12  # 0 at sunrise or sunset, 1 twelve hours later
    body = '-O-' if daytime else moon_glyph(moon_age)  # None on a new moon night
    body_col = round(progress * (width - 1 - len(body or ' ')))
    body_row = round((SKY_HEIGHT - 1) * (1 - math.sin(math.pi * progress)))

    cells, taken = [], set()

    def place(row, col, text, color):
        cells.append((row, col, text, color))
        taken.update((row, col + offset) for offset in range(len(text)))

    if daytime:
        for row, col, text, _ in build_clouds(width, load, tick):
            place(row, col, text, 'sunset' if twilight else 'cloud')  # clouds glow at sunrise and sunset
        if not any((body_row, body_col + offset) in taken for offset in range(len(body))):  # else behind a cloud
            place(body_row, body_col, body, 'sunset' if twilight else 'sun')
        star_count = int(width // 8 * TWILIGHT_STAR_SHARE * twilight)
    else:
        if body:
            place(body_row, body_col, body, 'moon')
        row, col = planet_spot(width, day_of_year)
        if not any((row, near) in taken for near in range(col - 1, col + len(PLANET) + 1)):  # else behind the moon
            place(row, col, PLANET, 'planet')
        streak = build_shooting_star(width, tick, seed)
        if streak:
            row, col, text = streak
            if not any((row, col + offset) in taken for offset in range(len(text))):  # else behind the moon or planet
                place(row, col, text, 'shooting_star')
        # A new moon night is darker, so more stars show; the extras come out after the usual ones
        star_count = width // 5 if body is None else width // 8

    for i, (row, col) in enumerate(star_spots(width, star_count)):
        if any((row, near) in taken for near in (col - 1, col, col + 1)):
            continue  # behind (or right beside) the sun, the moon or a cloud
        twinkle = (tick // 4 + i) % 5 == 0
        cells.append((row, col, '*' if twinkle else '.', 'plain'))
    return cells


def build_pals(vitals, tick, mood):
    """The pals visiting right now, nearest the pet first, as (lines, color) for this frame.

    They line up by how permanent they are (guard, doctor, then Claude), so the ones that come and go
    don't make the others move, and a narrow window drops the most fleeting first.
    """
    pals = []
    if vitals.adguard_running and vitals.dns_answering is False:
        pals.append((GUARD_ALARMED[tick // 2 % len(GUARD_ALARMED)], 'alert'))  # the ! flashes twice a second
    elif vitals.adguard_running:
        pals.append((GUARD_ON_WATCH[tick // 4 % len(GUARD_ON_WATCH)], 'guard'))  # a glance every second
    elif vitals.adguard_running is False:
        pals.append((GUARD_ASLEEP[tick // 4 % len(GUARD_ASLEEP)], 'dim'))
    if vitals.pi_vitals_running:
        if mood in URGENT_MOODS:
            pals.append((DOCTOR_WORRIED, 'doctor'))
        else:
            pals.append((DOCTOR_CHECKING[tick // 8 % len(DOCTOR_CHECKING)], 'doctor'))  # looks up or down every 2 s
    if vitals.claude_here:
        pals.append((CLAUDE_PAL[tick // 4 % 2], 'claude'))  # twinkles about once a second
    return pals


def plant_stage(uptime_sec):
    """How far the plant has grown: 0 (sprout) to 3 (in flower), or None without an uptime reading."""
    if uptime_sec is None:
        return None
    days = uptime_sec / 86400
    return max(i for i, (min_days, _) in enumerate(PLANT_STAGES) if days >= min_days)


def build_plant(uptime_sec):
    """The potted plant as (text, color) lines from the top, or [] without an uptime reading."""
    stage = plant_stage(uptime_sec)
    return [] if stage is None else PLANT_STAGES[stage][1]


def build_ground(width):
    """A line of grass across the window."""
    return (GROUND_PATTERN * (width // len(GROUND_PATTERN) + 1))[:width - 1]


def explain_mood(vitals, mood):
    """Why the pet is in this mood, in words, from the reading that decided it."""
    if mood == 'happy':
        return 'you just petted me'
    if vitals.forced_mood:
        return 'forced with --mood'
    if mood == 'dizzy':
        return "the power supply can't keep up (undervoltage alarm)"
    if mood == 'hot':
        return f'CPU at {vitals.temp_c:.1f}°C ({HOT_TEMP_C}°C or more is hot)'
    if mood == 'stuffed':
        return f'SD card {vitals.disk_used_percent:.0f}% full ({STUFFED_DISK_PERCENT}% or more)'
    if mood == 'hungry':
        return f'only {vitals.memory_free_percent:.0f}% of memory free (under {HUNGRY_MEMORY_PERCENT}%)'
    if mood == 'groggy':
        return f'the Pi booted {format_duration(vitals.uptime_sec)} ago (under {GROGGY_UPTIME_SEC // 60} min)'
    if mood == 'lonely':
        return 'no route to the internet'
    if mood == 'busy':
        return f'1-minute load {vitals.load:.2f} ({BUSY_LOAD} or more)'
    if mood == 'surfing':
        return f'{format_rate(vitals.net_rate)} on {vitals.interface} ({SURF_BYTES_PER_SEC // 1024} KB/s or more)'
    if mood == 'sleepy':
        return f'load {vitals.load:.2f} and only {format_rate(vitals.net_rate)} of traffic'
    return 'everything looks normal'


def describe_sky(vitals):
    """What's in the sky right now and why, in words."""
    hour = vitals.hour
    if 6 <= hour < 18:
        clouds = cloud_count(vitals.load)
        weather = f'{clouds} cloud{"s" if clouds != 1 else ""} (load {vitals.load:.2f}: busier means cloudier)'
        if hour >= 17:
            return f'dusk: a red sun, the first stars, {weather}'
        if hour < 7:
            return f'dawn: a red sun, the last stars, {weather}'
        return f'day: the sun, {weather}'
    phase = MOON_PHASE_NAMES[moon_eighth(vitals.moon_age)]
    return f'night: {phase} (day {vitals.moon_age:.1f} of 29.5), a ringed planet, the odd shooting star'


def describe_pals(vitals, mood):
    """One line per visiting pal, saying why it's here."""
    lines = []
    if vitals.adguard_running and vitals.dns_answering is False:
        lines.append(('guard   AdGuard Home runs, but nothing is bound to port 53!', 'alert'))
    elif vitals.adguard_running:
        lines.append(('guard   AdGuard Home is running and serving DNS', 'plain'))
    elif vitals.adguard_running is False:
        lines.append(('guard   asleep: AdGuard Home has stopped', 'alert'))
    if vitals.pi_vitals_running:
        worried = ', and worried about me' if mood in URGENT_MOODS else ''
        lines.append((f'doctor  Pi Vitals is listening on port {PI_VITALS_PORT}{worried}', 'plain'))
    if vitals.claude_here:
        lines.append(('Claude  a Claude Code session is open', 'plain'))
    return lines or [('nobody is visiting right now', 'plain')]


def describe_plant(vitals):
    """The plant's stage, the uptime that grew it, and what comes next."""
    stage = plant_stage(vitals.uptime_sec)
    if stage is None:
        return 'none: there is no uptime reading to grow it'
    grown = f'{PLANT_STAGE_NAMES[stage]}, up {format_duration(vitals.uptime_sec)}'
    if stage + 1 < len(PLANT_STAGES):
        next_days = PLANT_STAGES[stage + 1][0]
        return f'{grown} ({PLANT_STAGE_NAMES[stage + 1]} at {next_days} day{"s" if next_days != 1 else ""})'
    return f'{grown} (a reboot repots it)'


def build_help(vitals, mood):
    """The help screen: what everything on screen means right now, as (label, text, color) lines."""
    lines = [
        ('Mood', f'{mood}: {explain_mood(vitals, mood)}', 'plain'),
        ('Sky', describe_sky(vitals), 'plain'),
    ]
    for i, (text, color) in enumerate(describe_pals(vitals, mood)):
        lines.append(('Pals' if i == 0 else '', text, color))
    lines.append(('Plant', describe_plant(vitals), 'plain'))
    alerts = build_alerts(vitals)
    lines.append(('Alerts', alerts or 'none', 'alert' if alerts else 'plain'))
    lines.append(('Keys', 'p to pet  ·  ? to close this help  ·  q to quit', 'plain'))
    return lines


def layout(height, width, mood, tick, saying, vitals, show_hint=True, show_help=False):
    """Work out everything on screen as (row, col, text, color) cells, clipped to the window.

    Colors: leaf, berry, extra, plain, dim, alert, sun, sunset, moon, planet, shooting_star, grass,
    claude, guard, pot, cloud, doctor. Nothing is placed on the bottom row or in the last column, because
    curses raises an error when writing the window's bottom-right corner.
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

    if show_help:
        add(1, 2, "Pi Pet: what you're looking at", 'leaf')
        row = 3
        for label, text, color in build_help(vitals, mood):
            add(row, 2, label, 'dim')
            for line in textwrap.wrap(text, width - 11):  # long lines wrap under their own text
                add(row, 10, line, color)
                row += 1
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
        sky = build_sky(width, vitals.hour, tick, vitals.load, vitals.moon_age, vitals.sky_seed, vitals.day_of_year)
        for row, col, text, color in sky:
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
    for lines, color in build_pals(vitals, tick, mood):
        pal_left = pal_right - len(lines[0])
        if pal_left < 0:
            break
        for i, line in enumerate(lines):
            add(feet_row - len(lines) + 1 + i, pal_left, line, color)
        pal_right = pal_left - 2

    # The plant stands to the pet's right, past the extras, when there's room
    plant = build_plant(vitals.uptime_sec)
    plant_left = left + PLANT_OFFSET
    if plant and plant_left + len(plant[0][0]) <= width - 1:
        for i, (text, color) in enumerate(plant):
            add(feet_row - len(plant) + 1 + i, plant_left, text, color)

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

    if show_hint and last_row < height - 2:
        hint = 'p to pet  ·  ? for help  ·  q to quit'
        if len(hint) <= width - 2:
            add(height - 2, 1, hint, 'dim')
        else:
            add(height - 2, 0, 'p pet · ? help · q quit', 'dim')
    return cells


def render_lines(cells, height, width, use_color=False):
    """Turn layout cells into printable lines for --once, with ANSI colors if asked.

    Blank rows at the top and bottom and trailing spaces are dropped.
    """
    grid = [[(' ', 'plain')] * width for _ in range(height)]
    for row, col, text, color in cells:
        for offset, char in enumerate(text):
            grid[row][col + offset] = (char, color)

    lines = []
    for row in grid:
        end = max((i + 1 for i, (char, _) in enumerate(row) if char != ' '), default=0)
        parts, current = [], 'plain'
        for char, color in row[:end]:
            if use_color and char != ' ' and color != current:
                code = ANSI_COLORS[color]
                parts.append('\x1b[0m' + (f'\x1b[{code}m' if code else ''))
                current = color
            parts.append(char)
        if use_color and current != 'plain':
            parts.append('\x1b[0m')
        lines.append(''.join(parts))

    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def pick_saying(mood, vitals, rng=random):
    """Something to say for this mood; calm moods sometimes talk to a pal (or brag about the plant) instead."""
    friend_sayings = ((CLAUDE_SAYINGS if vitals.claude_here else [])
                      + (GUARD_SAYINGS if vitals.adguard_running and vitals.dns_answering is not False else [])
                      + (DOCTOR_SAYINGS if vitals.pi_vitals_running else [])
                      + (PLANT_SAYINGS if plant_stage(vitals.uptime_sec) == len(PLANT_STAGES) - 1 else []))
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

    columns, rows = shutil.get_terminal_size()  # 80x24 when the output is piped
    width, height = min(columns, ONCE_MAX_WIDTH), min(rows, ONCE_MAX_HEIGHT)
    cells = layout(height, width, mood, 1, pick_saying(mood, vitals), vitals, show_hint=False)
    use_color = sys.stdout.isatty() and 'NO_COLOR' not in os.environ
    for line in render_lines(cells, height, width, use_color):
        print(line)
    print(f'mood: {mood}')


class LiveState:
    """What the live view keeps between frames: the mood, what the pet is saying, petting and greetings.

    The timing rules live here instead of in the curses loop, so tests can drive them with a fake clock
    (`now`, in seconds) and scripted readings.
    """

    QUIT_KEYS = (ord('q'), ord('Q'), 27)  # 27 is Esc
    PET_KEYS = (ord('p'), ord('P'))
    HELP_KEYS = (ord('?'), ord('h'), ord('H'))

    def __init__(self, vitals, rng=random):
        self.vitals = vitals
        self.rng = rng
        self.mood = vitals.mood()
        self.saying = CLAUDE_GREETING if vitals.claude_here else pick_saying(self.mood, vitals, rng)
        self.petted_until = 0
        self.was_petted = False
        self.claude_was_here = vitals.claude_here
        self.show_help = False
        self.wish_until = 0
        self.streak_on_screen = False

    def press(self, key, now):
        """Handle one key press (curses gives -1 for none). Returns False when it's time to quit."""
        if key in self.QUIT_KEYS:
            return False
        if key in self.PET_KEYS:
            if not self.was_petted:
                self.saying = self.rng.choice(SAYINGS['happy'])
            self.petted_until = now + PET_SECONDS  # petting again keeps it happy longer
        if key in self.HELP_KEYS:
            self.show_help = not self.show_help
        return True

    def step(self, tick, now):
        """Move on one frame: sensors every 2 seconds, and a new thing to say every ~10 seconds or when
        the mood changes. Returns the mood to show."""
        petted = now < self.petted_until
        wishing = now < self.wish_until
        if self.wish_until and not wishing:
            self.wish_until = 0
            if not petted:
                self.saying = pick_saying(self.mood, self.vitals, self.rng)  # the wish is made
        if tick % (FPS * 2) == 0:
            self.vitals.refresh()
            new_mood = self.vitals.mood()
            time_for_new_saying = tick > 0 and tick % (FPS * 10) == 0  # not at tick 0: keep the opening line
            if not petted and not wishing and (new_mood != self.mood or time_for_new_saying):
                self.saying = pick_saying(new_mood, self.vitals, self.rng)
            if self.vitals.claude_here and not self.claude_was_here and not petted and not wishing:
                self.saying = CLAUDE_GREETING
            self.claude_was_here = self.vitals.claude_here
            self.mood = new_mood
        if self.was_petted and not petted:
            self.saying = pick_saying(self.mood, self.vitals, self.rng)  # back to its real mood
        self.was_petted = petted
        return 'happy' if petted else self.mood

    def notice(self, cells, now):
        """Look at the frame that was just drawn: a shooting star appearing makes the pet say WISH_SAYING."""
        streak = any(color == 'shooting_star' for *_, color in cells)
        if streak and not self.streak_on_screen and now >= self.petted_until:
            self.saying = WISH_SAYING
            self.wish_until = now + WISH_SECONDS
        self.streak_on_screen = streak


def curses_colors():
    """The curses attribute for each layout color. Matches ANSI_COLORS for --once."""
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_CYAN, -1)
    curses.init_pair(4, curses.COLOR_YELLOW, -1)
    curses.init_pair(5, curses.COLOR_BLUE, -1)
    curses.init_pair(6, curses.COLOR_MAGENTA, -1)
    return {
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
        'pot': curses.color_pair(4),
        'cloud': 0,  # the terminal's own text color, so clouds show on light and dark themes
        'doctor': curses.A_BOLD,  # a white coat, in the terminal's own text color
        'sunset': curses.color_pair(2) | curses.A_BOLD,  # 8-color terminals have no orange
        'shooting_star': curses.color_pair(4) | curses.A_BOLD,
        'planet': curses.color_pair(6),
    }


def run_live(screen, forced_mood=None, interface=None):
    curses.curs_set(0)
    screen.nodelay(True)
    colors = curses_colors()
    state = LiveState(Vitals(forced_mood, interface))
    tick = 0

    while True:
        now = time.monotonic()
        if not state.press(screen.getch(), now):
            return
        shown_mood = state.step(tick, now)

        screen.erase()
        height, width = screen.getmaxyx()
        cells = layout(height, width, shown_mood, tick, state.saying, state.vitals, show_help=state.show_help)
        for row, col, text, color in cells:
            screen.addstr(row, col, text, colors[color])
        screen.refresh()
        state.notice(cells, now)
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
