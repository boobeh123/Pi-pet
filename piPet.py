#!/usr/bin/env python3
"""Pi Pet: a little raspberry that lives in your terminal and reacts to the Pi's real vitals.

    python3 piPet.py              # live: press p to pet it, q to quit
    python3 piPet.py --once       # print a snapshot of the scene and exit (no curses)
    python3 piPet.py --mood hot   # force a mood (--help lists them all)
    python3 piPet.py --iface eth0 # watch one network interface instead of following the default route
    python3 piPet.py --help       # list the options
"""

import argparse
import collections
import curses
import datetime
import glob
import json
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
    'sunset': '1;31', 'shooting_star': '1;33', 'planet': '35', 'pumpkin': '1;33',
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
CLOUD_TICKS_PER_COLUMN = 8  # half a column a second at 4 FPS
PLANET = '-()-'  # a ringed planet: decorative, not a real planet's position
PLANET_DRIFT = 3  # columns per day; like a real planet it stays put all night and shifts night to night
SHOOTING_STAR_WINDOW = FPS * 60  # each minute of night has one chance of a shooting star...
SHOOTING_STAR_CHANCE = 1 / 3     # ...so about one every three minutes
SHOOTING_STAR_TICKS = 6          # each one crosses the sky in 1.5 seconds
SHOOTING_STAR_SPEED = 3          # columns per tick
WISH_SAYING = 'make a wish!'     # what the pet says when it sees a shooting star...
WISH_SECONDS = 4                 # ...and for how long

# Holidays from the real calendar (see holiday_on)
PI_PET_BIRTHDAY = (10, 8)  # the first commit, Oct 8 2026
PI_PET_BIRTH_YEAR = 2026
HOLIDAY_SAYINGS = {
    'halloween': ['trick or treat!', 'boo!', "I'm dressed as a raspberry"],
    'christmas': ['happy holidays!', 'look at the lights!', 'cozy season'],
    'new_year': ['happy new year!', '3... 2... 1...!', 'ooh, fireworks!'],
    'pi_day': ['happy Pi Day!', '3.14159265...', 'I am the Pi'],
    'birthday': ['happy birthday to me!', 'is there cake?'],  # plus a line with its age
    'valentines': ['be my valentine?', 'sending you hugs', 'I heart you'],
    'st_patricks': ['feeling lucky!', 'where did I put my pot of gold?', 'top of the morning!'],
    'fourth_of_july': ['happy Fourth!', 'red, white and boom!', 'ooh, fireworks!'],
    'thanksgiving': ['thankful for you!', 'pass the pie!', 'gobble gobble'],
}
HOLIDAY_DESCRIPTIONS = {
    'halloween': "Halloween week: a pumpkin hat, a jack-o'-lantern, and bats at night",
    'christmas': 'Christmas week: a Santa hat, and lights strung along the grass',
    'new_year': 'New Year: fireworks at night',
    'pi_day': 'Pi Day (3/14): the clouds know their digits',
    'birthday': "Pi Pet's birthday (born Oct 8, 2026): a party hat",
    'valentines': "Valentine's Day: a heart on its head, and hearts in the sky",
    'st_patricks': "St. Patrick's Day: a green top hat",
    'fourth_of_july': 'Fourth of July: red, white and blue fireworks at night',
    'thanksgiving': 'Thanksgiving: a pilgrim hat',
}
PARTY_HAT = '     /*\\     '  # replaces the leaf on Pi Pet's birthday
SANTA_HAT = '     .--*    '  # ...at Christmas
PUMPKIN_HAT = '    ((|))    '  # ...at Halloween
HEART_HAT = '     <3      '  # ...on Valentine's Day
TOP_HAT = '    _[_]_    '  # ...on St. Patrick's Day
PILGRIM_HAT = '    _|=|_    '  # ...and at Thanksgiving
HOLIDAY_HATS = {
    'birthday': (PARTY_HAT, 'sun'),
    'christmas': (SANTA_HAT, 'berry'),
    'halloween': (PUMPKIN_HAT, 'pumpkin'),
    'valentines': (HEART_HAT, 'berry'),
    'st_patricks': (TOP_HAT, 'leaf'),
    'thanksgiving': (PILGRIM_HAT, 'plain'),
}
PUMPKIN = [' _)_ ', '(^w^)']  # a jack-o'-lantern: stem, then the carved face
PUMPKIN_OFFSET = 31  # columns from the pet's left edge, just past the plant
BAT_FRAMES = ['^v^', '-v-']  # wings up, wings level
BAT_ROWS = [0, 1, 2, 1]  # bats bob up and down as they fly
BAT_TICKS_PER_COLUMN = 2
PI_DAY_CLOUDS = [['.-~~~~~-.', '(3.14159)']]
LIGHT_COLORS = ['berry', 'leaf', 'sun', 'guard']  # Christmas bulbs: red, green, yellow, blue
LIGHT_SPACING = 4
FIREWORK_WINDOW = FPS * 3  # every 3 seconds of New Year's night has a good chance of a firework
FIREWORK_CHANCE = 2 / 3
FIREWORK_COLORS = ['berry', 'sun', 'extra', 'planet', 'leaf']
FIREWORK_HOLIDAY_COLORS = {'new_year': FIREWORK_COLORS, 'fourth_of_july': ['berry', 'plain', 'guard']}  # red, white, blue
FIREWORK_FRAMES = [  # (row, offset from the burst's center column, text), one list per tick
    [(2, 0, "'")],  # the rocket goes up
    [(1, 0, '*')],
    [(0, -1, '\\|/'), (1, -1, '-*-'), (2, -1, '/|\\')],  # bang
    [(0, -1, '.'), (0, 1, '.'), (2, -1, '.'), (2, 1, '.')],  # sparks fading
]

# Pi Pet remembers a few things between runs, in a small JSON file (see memories_path)
DEFAULT_MEMORIES = {'first_met': None, 'visits': 0, 'times_petted': 0, 'times_watered': 0, 'shooting_stars_seen': 0,
                    'last_seen': None}
MISSED_YOU_SECONDS = 86400  # away this long or more, and the pet says...
MISSED_YOU_SAYING = 'missed you!'

# Thresholds that decide the pet's mood
HOT_TEMP_C = 70
BUSY_LOAD = 2.0
SURF_BYTES_PER_SEC = 200 * 1024
SLEEPY_LOAD = 0.3
SLEEPY_BYTES_PER_SEC = 5 * 1024
WIFI_BAR_DBM = (-55, -65, -75, -85)  # Wi-Fi signal: 4 bars at -55 dBm or stronger, 3 at -65, 2 at -75, 1 at -85
WEAK_WIFI_BARS = 1  # this many bars or fewer, and the pet frets
WIFI_BAR_WORDS = ['very weak', 'weak', 'fair', 'good', 'excellent']
WEAK_WIFI_SAYINGS = ['the Wi-Fi feels far away', 'can you hear me now?']
STUFFED_DISK_PERCENT = 90   # SD card this full or more
HUNGRY_MEMORY_PERCENT = 10  # less memory than this still available
GROGGY_UPTIME_SEC = 5 * 60  # booted less than this long ago

PET_SECONDS = 3  # how long the pet stays happy after you press p
WATER_SECONDS = 3  # how long the watering can pours after you press w
WATERING_CAN = ['  .---.', '-=|___|']  # its spout, on the left, pours onto the plant
WATER_SAYING = 'the plant says thanks!'

# Network speed is only measured over at least this long; one packet over a few microseconds reads as a huge speed
MIN_RATE_SECONDS = 0.5
FAN_FRAMES = '|/-\\'  # the status line's fan icon, a quarter turn apart
FAN_FULL_SPEED_RPM = 6000  # at this speed or faster the icon turns one step every frame (the most 4 FPS can show)
HISTORY_SAMPLES = 300  # readings kept for the trend sparklines: 10 minutes at one every 2 seconds
MIN_TREND_SAMPLES = 4  # fewer than this (a --once snapshot, say) and there's no trend to draw
TEMP_TREND_RANGE = (40, 80)  # °C from the bottom to the top of the temperature sparkline
TREND_WIDTH = 20  # characters per sparkline, two time slices each
# Braille dots from the bottom up, for the left and right halves of a character
LEFT_DOTS = (0x40, 0x04, 0x02, 0x01)
RIGHT_DOTS = (0x80, 0x20, 0x10, 0x08)
CLOCK_TICKS_PER_SECOND = os.sysconf('SC_CLK_TCK')  # the unit of CPU time in /proc/<pid>/stat (100 on Linux)
BUSY_SAYING = '{} is keeping me busy!'
BUSY_SAYING_CHANCE = 1 / 2
FIREFOX_PROCESS_NAMES = ('firefox', 'firefox-esr', 'Web Content', 'Isolated Web Co', 'WebExtensions', 'Privileged Cont',
                         'RDD Process', 'Socket Process', 'Utility Process', 'GPU Process')  # Firefox's many processes
FRIENDLY_PROCESS_NAMES = {'code': 'VS Code', 'chromium': 'Chromium', 'claude': 'Claude', 'node': 'Node',
                          'python3': 'Python', 'AdGuardHome': 'AdGuard', 'mongod': 'MongoDB',
                          **{name: 'Firefox' for name in FIREFOX_PROCESS_NAMES}}

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
GUARD_CHEERING = [' _^_ ', '(^_^)', '\\[+]/']  # arms and shield up, while the pet is being petted
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
DOCTOR_CHEERING = [' _+_ ', '(^-^)', '\\|=|/']  # arms up, while the pet is being petted
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


def read_wifi_dbm(interface):
    """Wi-Fi signal strength in dBm for a wireless interface (from /proc/net/wireless), or None for a
    wired interface, a missing one, or a driver that doesn't report it."""
    try:
        with open('/proc/net/wireless') as f:
            for line in f:
                name, _, fields = line.partition(':')
                if name.strip() == interface:
                    level = float(fields.split()[2].rstrip('.'))
                    return level if level < 0 else None  # 0 or positive means no dBm reading
    except (OSError, ValueError, IndexError):
        pass
    return None


def wifi_bars(dbm):
    """0-4 bars from signal strength in dBm, like a phone."""
    return sum(dbm >= threshold for threshold in WIFI_BAR_DBM)


def format_wifi_bars(dbm):
    """Signal bars for the status line, like '|||.', or '' without a Wi-Fi reading."""
    if dbm is None:
        return ''
    bars = wifi_bars(dbm)
    return '|' * bars + '.' * (len(WIFI_BAR_DBM) - bars)


def is_wifi_weak(vitals):
    return vitals.wifi_dbm is not None and wifi_bars(vitals.wifi_dbm) <= WEAK_WIFI_BARS


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


def thanksgiving_day(year):
    """The day of November that Thanksgiving (the fourth Thursday) falls on."""
    first_thursday = 1 + (3 - datetime.date(year, 11, 1).weekday()) % 7  # Monday is 0, Thursday 3
    return first_thursday + 21


def holiday_on(month, day, year):
    """Which holiday a date falls on, or None: Valentine's Day, Pi Day (Mar 14), St. Patrick's Day,
    the Fourth of July, Pi Pet's own birthday (Oct 8), Halloween week, Thanksgiving, Christmas week,
    and New Year (Dec 31 and Jan 1)."""
    if (month, day) == (2, 14):
        return 'valentines'
    if (month, day) == (3, 14):
        return 'pi_day'
    if (month, day) == (3, 17):
        return 'st_patricks'
    if (month, day) == (7, 4):
        return 'fourth_of_july'
    if (month, day) == (11, thanksgiving_day(year)):
        return 'thanksgiving'
    if (month, day) == PI_PET_BIRTHDAY:
        return 'birthday'
    if month == 10 and 25 <= day <= 31:
        return 'halloween'
    if month == 12 and 20 <= day <= 26:
        return 'christmas'
    if (month, day) in ((12, 31), (1, 1)):
        return 'new_year'
    return None


def read_process_ticks():
    """Every running program's processes except Pi Pet itself, as {pid: (name, CPU clock ticks used so far,
    start time)}, from /proc/<pid>/stat. Names are at most 15 characters, the same as /proc/<pid>/comm.

    Kernel threads (kthreadd, pid 2, and its children) are left out: they aren't programs anyone opened,
    and kworker threads rename themselves as they switch jobs."""
    processes = {}
    own_pid = os.getpid()
    for stat_path in glob.glob('/proc/[0-9]*/stat'):
        try:
            with open(stat_path) as f:
                stat = f.read()
            pid_text, _, rest = stat.partition(' (')
            name, _, fields = rest.rpartition(') ')  # the name itself can contain spaces and parentheses
            fields = fields.split()
            pid, parent = int(pid_text), int(fields[1])
            if pid != own_pid and pid != 2 and parent != 2:
                # user time + system time, and the start time, which tells a process apart from a later one with its pid
                processes[pid] = (name, int(fields[11]) + int(fields[12]), int(fields[19]))
        except (OSError, ValueError, IndexError):
            continue  # the process ended while we looked
    return processes


def busiest_process(before, after, seconds):
    """The program that used the most CPU between two readings of read_process_ticks, as (friendly name,
    percent of one core), adding up all of a program's processes (all of Firefox's, say). None if nothing
    used 1%."""
    used = {}
    for pid, (name, ticks, started) in after.items():
        earlier = before.get(pid)
        # A process that's new since the last reading used all of its time in between (a reused pid is new too)
        start = earlier[1] if earlier and earlier[2] == started else 0
        program = friendly_name(name)
        used[program] = used.get(program, 0) + max(0, ticks - start)
    if not used or seconds <= 0:
        return None
    name, ticks = max(used.items(), key=lambda item: item[1])
    percent = ticks / CLOCK_TICKS_PER_SECOND / seconds * 100
    return (name, percent) if percent >= 1 else None


def friendly_name(process_name):
    return FRIENDLY_PROCESS_NAMES.get(process_name, process_name)


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
        self.process_ticks = None  # the last read_process_ticks, for working out the busiest program
        self.process_time = None
        self.busiest = None  # (name, percent of one core), once there are two readings to compare
        self.temp_history = collections.deque(maxlen=HISTORY_SAMPLES)  # for the trend sparklines
        self.load_history = collections.deque(maxlen=HISTORY_SAMPLES)
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
        processes = self.update_busiest()
        names = {name for name, _, _ in processes.values()}
        self.claude_here = 'claude' in names  # a Claude Code session, in VS Code or the terminal
        # None when AdGuard Home isn't installed, so other Pis get no guard at all
        self.adguard_running = 'AdGuardHome' in names if is_adguard_installed() else None
        self.pi_vitals_running = is_port_listening(PI_VITALS_PORT)
        # Only asked while AdGuard runs: is it actually serving DNS? None means "not asked"
        self.dns_answering = is_udp_port_bound(DNS_PORT) if self.adguard_running else None
        now = datetime.datetime.now()  # local time, for the clock, the sun and moon, and holidays
        self.now = now
        self.hour = now.hour + now.minute / 60
        self.moon_age = moon_age_days(time.time())
        self.day_of_year = now.timetuple().tm_yday  # moves the planet a little each night
        self.year = now.year
        self.holiday = holiday_on(now.month, now.day, now.year)
        self.update_net_rate()
        self.wifi_dbm = read_wifi_dbm(self.interface)  # after update_net_rate, which may switch interfaces
        if self.temp_c is not None:
            self.temp_history.append(self.temp_c)
        self.load_history.append(self.load)

    def update_busiest(self):
        """Read every process's CPU time, and work out the busiest program since the last reading at least
        MIN_RATE_SECONDS ago (closer readings would turn one tick into a huge percentage). Returns the reading."""
        now = time.monotonic()
        processes = read_process_ticks()
        if self.process_ticks is None:
            self.process_ticks, self.process_time = processes, now  # nothing to compare with yet
        elif now - self.process_time >= MIN_RATE_SECONDS:
            self.busiest = busiest_process(self.process_ticks, processes, now - self.process_time)
            self.process_ticks, self.process_time = processes, now
        return processes

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


def format_clock(now):
    """The live view's clock: 'Wed Oct 15  3:32 PM'."""
    return f'{now:%a %b} {now.day}  {now.hour % 12 or 12}:{now:%M} {"AM" if now.hour < 12 else "PM"}'


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


def build_pet(mood, tick, holiday=None):
    """Return the pet as a list of (text, color) lines. Colors: leaf, berry, extra (or a holiday hat's color)."""
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
    if holiday in HOLIDAY_HATS:
        lines[0] = HOLIDAY_HATS[holiday]  # the hat sits where the leaf was

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


def fan_icon(rpm, tick):
    """The status line's little fan, turning faster the faster the real fan spins: '' without a fan
    reading, a still '-' when it's stopped."""
    if rpm is None:
        return ''
    if rpm <= 0:
        return '-'
    steps_per_tick = min(1.0, rpm / FAN_FULL_SPEED_RPM)  # more than one step a frame would look like it's slowing
    return FAN_FRAMES[int(tick * steps_per_tick) % len(FAN_FRAMES)]


def build_status(vitals, tick=0):
    temp = '--' if vitals.temp_c is None else f'{vitals.temp_c:.1f}°C'
    fan = '--' if vitals.fan_rpm is None else f'{fan_icon(vitals.fan_rpm, tick)} {int(vitals.fan_rpm)} rpm'
    signal = format_wifi_bars(vitals.wifi_dbm)
    network = ' '.join(part for part in (vitals.interface, signal, format_rate(vitals.net_rate)) if part)
    return f'temp {temp}  ·  load {vitals.load:.2f}  ·  fan {fan}  ·  {network}'


def sparkline(values, width, low, high, slots=None):
    """A Braille sparkline `width` characters wide, newest on the right. Each character holds two time
    slots; the full width covers `slots` readings (all of them by default), so a short history leaves
    the left side blank. Every slot with data shows at least one dot; low and high set the scale."""
    values = list(values)
    dots = width * 2
    per_dot = max(slots or len(values), dots) / dots  # readings per dot (at least one)
    heights = []
    for dot in range(dots):
        start = len(values) - round((dots - dot) * per_dot)
        end = len(values) - round((dots - 1 - dot) * per_dot)
        bucket = values[max(0, start):max(0, end)]
        if bucket:
            level = (sum(bucket) / len(bucket) - low) / (high - low)
            heights.append(1 + round(min(1, max(0, level)) * 3))
        else:
            heights.append(0)
    return ''.join(chr(0x2800 + sum(LEFT_DOTS[:left]) + sum(RIGHT_DOTS[:right]))
                   for left, right in zip(heights[::2], heights[1::2]))


def build_trend(vitals, width):
    """The trend row under the status line: 10-minute sparklines of temperature and load, as
    (col, text, color) pieces for a window `width` wide, or [] without enough history or room."""
    if len(vitals.load_history) < MIN_TREND_SAMPLES:
        return []
    spark_width = min(TREND_WIDTH, (width - 22) // 2)
    if spark_width < 6:
        return []
    cores = os.cpu_count() or 4
    pieces = []
    if len(vitals.temp_history) >= MIN_TREND_SAMPLES:
        temp = sparkline(vitals.temp_history, spark_width, *TEMP_TREND_RANGE, slots=HISTORY_SAMPLES)
        pieces += [('temp ', 'dim'), (temp, 'sun'), ('  ', 'dim')]
    load = sparkline(vitals.load_history, spark_width, 0, cores, slots=HISTORY_SAMPLES)
    pieces += [('load ', 'dim'), (load, 'extra'), ('  10 min', 'dim')]
    col = (width - 1 - sum(len(text) for text, _ in pieces)) // 2
    placed = []
    for text, color in pieces:
        placed.append((col, text, color))
        col += len(text)
    return placed


def build_short_status(vitals, tick=0):
    """A narrower status line for small windows."""
    temp = '--' if vitals.temp_c is None else f'{vitals.temp_c:.1f}°C'
    fan = '--' if vitals.fan_rpm is None else f'{fan_icon(vitals.fan_rpm, tick)} {int(vitals.fan_rpm)}'
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
    if is_wifi_weak(vitals):
        alerts.append(f'weak Wi-Fi ({vitals.wifi_dbm:.0f} dBm)')
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


def clip_to_width(col, text, width):
    """The part of text that's inside a window `width` columns wide (never using the last column),
    as (col, text). For things drifting in from the left edge or off the right one; text is '' when
    none of it is inside."""
    if col < 0:
        text, col = text[-col:], 0
    return col, text[:max(0, width - 1 - col)]


def build_clouds(width, load, tick, shapes=CLOUD_SHAPES):
    """Clouds drifting left to right across the sky as (row, col, text, color), wrapping around.

    They all move at the same speed from evenly spaced starts, so they never run into each other.
    """
    count = cloud_count(load)
    period = width + max(len(shape[-1]) for shape in shapes)
    cells = []
    for i in range(count):
        shape = shapes[i % len(shapes)]
        cloud_width = len(shape[-1])
        start = period // 4 + i * period // count
        cloud_left = (start + tick // CLOUD_TICKS_PER_COLUMN) % period - cloud_width
        for line_no, text in enumerate(shape):
            col, text = clip_to_width(cloud_left + (cloud_width - len(text)) // 2, text, width)
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


def build_bats(width, tick):
    """Two bats flitting across the Halloween night sky as (row, col, text), flapping and bobbing.

    They fly at the same speed half a lap apart, so they never run into each other.
    """
    bat_width = len(BAT_FRAMES[0])
    period = width + bat_width
    cells = []
    for i in range(2):
        col = (tick // BAT_TICKS_PER_COLUMN + i * period // 2) % period - bat_width
        row = BAT_ROWS[(tick // 4 + 2 * i) % len(BAT_ROWS)]
        col, text = clip_to_width(col, BAT_FRAMES[(tick // 2 + i) % len(BAT_FRAMES)], width)
        if text:
            cells.append((row, col, text))
    return cells


def build_fireworks(width, tick, seed, colors=FIREWORK_COLORS):
    """A holiday firework as (row, col, text, color) cells: a rocket, a burst, fading sparks.

    Like shooting stars, each FIREWORK_WINDOW has a FIREWORK_CHANCE of one, decided from the seed.
    """
    window, tick_in_window = divmod(tick, FIREWORK_WINDOW)
    rng = random.Random(f'fireworks:{seed}:{window}')
    if rng.random() >= FIREWORK_CHANCE:
        return []
    start = rng.randrange(FIREWORK_WINDOW - len(FIREWORK_FRAMES))
    step = tick_in_window - start
    if not 0 <= step < len(FIREWORK_FRAMES):
        return []
    center = rng.randrange(1, width - 2)  # room for the burst's arms on both sides
    color = rng.choice(colors)
    return [(row, center + offset, text, color) for row, offset, text in FIREWORK_FRAMES[step]]


def heart_spots(width):
    """Where Valentine's Day hearts float in the sky: the same spots for the same window width."""
    rng = random.Random(f'hearts:{width}')
    return [(rng.randrange(SKY_HEIGHT), rng.randrange(width - 3)) for _ in range(max(2, width // 16))]


def build_sky(width, hour, tick, load=0.0, moon_age=SYNODIC_MONTH_DAYS / 2, seed=0, day_of_year=0, holiday=None):
    """The sky across the top of the window as (row, col, text, color): a sun and clouds by day,
    the moon in its real phase, a ringed planet, stars and the odd shooting star by night, and a red
    sun, glowing clouds and the first (or last) stars at dusk and dawn. Holidays add bats (Halloween),
    fireworks (New Year, the Fourth of July), hearts (Valentine's Day) and clouds made of digits (Pi Day).

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

    def is_free(row, col, text, margin=0):
        """True if nothing in the sky so far is on these spots (or within `margin` columns of them)."""
        return not any((row, near) in taken for near in range(col - margin, col + len(text) + margin))

    if daytime:
        shapes = PI_DAY_CLOUDS if holiday == 'pi_day' else CLOUD_SHAPES
        for row, col, text, _ in build_clouds(width, load, tick, shapes):
            place(row, col, text, 'sunset' if twilight else 'cloud')  # clouds glow at sunrise and sunset
        if is_free(body_row, body_col, body):  # else behind a cloud
            place(body_row, body_col, body, 'sunset' if twilight else 'sun')
        star_count = int(width // 8 * TWILIGHT_STAR_SHARE * twilight)
    else:
        if body:
            place(body_row, body_col, body, 'moon')
        row, col = planet_spot(width, day_of_year)
        if is_free(row, col, PLANET, margin=1):  # else behind the moon
            place(row, col, PLANET, 'planet')
        streak = build_shooting_star(width, tick, seed)
        if streak:
            row, col, text = streak
            if is_free(row, col, text):  # else behind the moon or planet
                place(row, col, text, 'shooting_star')
        if holiday == 'halloween':
            for row, col, text in build_bats(width, tick):
                if is_free(row, col, text):  # else behind the moon
                    place(row, col, text, 'dim')
        if holiday in FIREWORK_HOLIDAY_COLORS:
            burst = build_fireworks(width, tick, seed, FIREWORK_HOLIDAY_COLORS[holiday])
            if all(is_free(row, col, text) for row, col, text, _ in burst):
                for row, col, text, color in burst:
                    place(row, col, text, color)
        # A new moon night is darker, so more stars show; the extras come out after the usual ones
        star_count = width // 5 if body is None else width // 8

    if holiday == 'valentines':
        for row, col in heart_spots(width):
            if is_free(row, col, '<3', margin=1):  # else behind a cloud, the sun, the moon or another heart
                place(row, col, '<3', 'berry')

    for i, (row, col) in enumerate(star_spots(width, star_count)):
        if not is_free(row, col, '.', margin=1):
            continue  # behind (or right beside) the sun, the moon, a cloud or the planet
        twinkle = (tick // 4 + i) % 5 == 0
        cells.append((row, col, '*' if twinkle else '.', 'plain'))
    return cells


def build_pals(vitals, tick, mood):
    """The pals visiting right now, nearest the pet first, as (lines, color) for this frame.

    They line up by how permanent they are (guard, doctor, then Claude), so the ones that come and go
    don't make the others move, and a narrow window drops the most fleeting first. While the pet is
    happy (being petted) they cheer, unless the guard is busy with a DNS problem or asleep.
    """
    cheering = mood == 'happy'
    pals = []
    if vitals.adguard_running and vitals.dns_answering is False:
        pals.append((GUARD_ALARMED[tick // 2 % len(GUARD_ALARMED)], 'alert'))  # the ! flashes twice a second
    elif vitals.adguard_running:
        frame = GUARD_CHEERING if cheering else GUARD_ON_WATCH[tick // 4 % len(GUARD_ON_WATCH)]  # a glance every second
        pals.append((frame, 'guard'))
    elif vitals.adguard_running is False:
        pals.append((GUARD_ASLEEP[tick // 4 % len(GUARD_ASLEEP)], 'dim'))
    if vitals.pi_vitals_running:
        if cheering:
            pals.append((DOCTOR_CHEERING, 'doctor'))
        elif mood in URGENT_MOODS:
            pals.append((DOCTOR_WORRIED, 'doctor'))
        else:
            pals.append((DOCTOR_CHECKING[tick // 8 % len(DOCTOR_CHECKING)], 'doctor'))  # looks up or down every 2 s
    if vitals.claude_here:
        twinkle_ticks = 2 if cheering else 4  # twinkles about once a second, twice as fast when cheering
        pals.append((CLAUDE_PAL[tick // twinkle_ticks % 2], 'claude'))
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


def build_ground(width, holiday=None, tick=0):
    """The ground across the window as (col, text, color) pieces: a line of grass, strung with
    twinkling lights at Christmas."""
    grass = (GROUND_PATTERN * (width // len(GROUND_PATTERN) + 1))[:width - 1]
    if holiday != 'christmas':
        return [(0, grass, 'grass')]
    pieces, start = [], 0
    for col in range(LIGHT_SPACING // 2, width - 1, LIGHT_SPACING):
        if col > start:
            pieces.append((start, grass[start:col], 'grass'))
        color = LIGHT_COLORS[(col // LIGHT_SPACING + tick // 4) % len(LIGHT_COLORS)]  # the colors chase each second
        pieces.append((col, 'o', color))
        start = col + 1
    if start < width - 1:
        pieces.append((start, grass[start:], 'grass'))
    return pieces


def build_watering(drop_rows, tick):
    """The watering can and its falling drops as (row, col, text, color) from the can's top-left corner,
    which is also where its spout pours. drop_rows is how many rows the water falls before the plant."""
    cells = [(i, 0, line, 'plain') for i, line in enumerate(WATERING_CAN)]
    for row in range(len(WATERING_CAN), len(WATERING_CAN) + drop_rows):
        if (row - tick) % 2 == 0:  # every other row has a drop, and they move down a row each tick
            cells.append((row, 0, "'", 'extra'))
    return cells


def build_pumpkin(tick):
    """The Halloween jack-o'-lantern as (text, color) lines; its candle flickers."""
    flame = 'sunset' if tick // 2 % 3 == 0 else 'pumpkin'
    return [(PUMPKIN[0], 'leaf'), (PUMPKIN[1], flame)]


def explain_mood(vitals, mood):
    """Why the pet is in this mood, in words, from the reading that decided it."""
    if mood == 'happy' and vitals.forced_mood != 'happy':
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
        busiest = f', mostly {vitals.busiest[0]}' if vitals.busiest else ''
        return f'1-minute load {vitals.load:.2f} ({BUSY_LOAD} or more){busiest}'
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


def describe_network(vitals):
    """The network in words: interface, speed, and Wi-Fi signal when there is one."""
    text = f'{vitals.interface}: {format_rate(vitals.net_rate)}'
    if vitals.wifi_dbm is not None:
        text += f', signal {vitals.wifi_dbm:.0f} dBm ({WIFI_BAR_WORDS[wifi_bars(vitals.wifi_dbm)]})'
    return text if vitals.online else f'{text}, but no route to the internet'


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


def describe_memories(memories, today):
    """What Pi Pet remembers about the two of you, in one line."""
    if memories['first_met']:
        met = datetime.date.fromisoformat(memories['first_met'])
        days = (today - met).days
        ago = 'today' if days <= 0 else 'yesterday' if days == 1 else f'{days} days ago'
        parts = [f'met {met:%b} {met.day}, {met.year} ({ago})']
    else:
        parts = ['just met']

    def count(number, thing):
        return f'{number} {thing}{"" if number == 1 else "s"}'
    parts.append(count(memories['visits'], 'visit'))
    parts.append(f'petted {count(memories["times_petted"], "time")}')
    if memories['times_watered']:
        parts.append(f'plant watered {count(memories["times_watered"], "time")}')
    parts.append(f'{count(memories["shooting_stars_seen"], "shooting star")} seen')
    return '  ·  '.join(parts)


def build_help(vitals, mood, memories=None, today=None):
    """The help screen: what everything on screen means right now, as (label, text, color) lines."""
    lines = [
        ('Mood', f'{mood}: {explain_mood(vitals, mood)}', 'plain'),
        ('Sky', describe_sky(vitals), 'plain'),
        ('Network', describe_network(vitals), 'plain'),
    ]
    if vitals.busiest:
        name, percent = vitals.busiest
        lines.append(('Busiest', f'{name}, {percent:.0f}% of one core (of {os.cpu_count()})', 'plain'))
    for i, (text, color) in enumerate(describe_pals(vitals, mood)):
        lines.append(('Pals' if i == 0 else '', text, color))
    lines.append(('Plant', describe_plant(vitals), 'plain'))
    alerts = build_alerts(vitals)
    lines.append(('Alerts', alerts or 'none', 'alert' if alerts else 'plain'))
    if vitals.holiday:
        lines.append(('Today', HOLIDAY_DESCRIPTIONS[vitals.holiday], 'plain'))
    if memories is not None:
        lines.append(('Together', describe_memories(memories, today or datetime.date.today()), 'plain'))
    lines.append(('Keys', 'p to pet  ·  w to water  ·  ? closes this page  ·  q to quit', 'plain'))
    return lines


def layout(height, width, mood, tick, saying, vitals, show_hint=True, show_help=False, memories=None,
           watering=False):
    """Work out everything on screen as (row, col, text, color) cells, clipped to the window.

    Colors: leaf, berry, extra, plain, dim, alert, sun, sunset, moon, planet, shooting_star, grass,
    claude, guard, pot, cloud, doctor, pumpkin. Nothing is placed on the bottom row or in the last column, because
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
        for label, text, color in build_help(vitals, mood, memories):
            add(row, 2, label, 'dim')
            for line in textwrap.wrap(text, width - 13):  # long lines wrap under their own text
                add(row, 12, line, color)
                row += 1
        return cells

    pet = build_pet(mood, tick, vitals.holiday)
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
    # The trend row (sparklines under the status line) comes last: it shows only if it fits with all of that
    trend = build_trend(vitals, width)
    if trend and block_height + 1 + (SKY_HEIGHT + 2 if show_scene else 0) <= usable:
        block_height += 1
    else:
        trend = []
    if show_scene:
        top = SKY_HEIGHT + (usable - SKY_HEIGHT - block_height - 2) // 2
        sky = build_sky(width, vitals.hour, tick, vitals.load, vitals.moon_age, vitals.sky_seed, vitals.day_of_year,
                        vitals.holiday)
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
        plant_top = feet_row - len(plant) + 1
        for i, (text, color) in enumerate(plant):
            add(plant_top + i, plant_left, text, color)
        # The watering can hangs in the pet's top rows, its spout over the plant's middle
        can_left = plant_left + 2
        if watering and can_left + len(WATERING_CAN[0]) <= width - 1:
            for row, col, text, color in build_watering(plant_top - pet_top - len(WATERING_CAN), tick):
                add(pet_top + row, can_left + col, text, color)

    # At Halloween a jack-o'-lantern sits just past the plant, when there's room
    pumpkin_left = left + PUMPKIN_OFFSET
    if vitals.holiday == 'halloween' and pumpkin_left + len(PUMPKIN[0]) <= width - 1:
        pumpkin = build_pumpkin(tick)
        for i, (text, color) in enumerate(pumpkin):
            add(feet_row - len(pumpkin) + 1 + i, pumpkin_left, text, color)

    extras_color = 'berry' if mood == 'happy' else 'extra'  # red hearts
    for row, col, text in extras:
        add(pet_top + row, left + col, text, extras_color)

    status = build_status(vitals, tick)
    if len(status) > width - 1:
        status = build_short_status(vitals, tick)
    status_row = pet_top + pet_height + 1
    if show_scene:
        for col, text, color in build_ground(width, vitals.holiday, tick):
            add(pet_top + pet_height, col, text, color)
        status_row += 1
    add(status_row, (width - 1 - len(status)) // 2, status, 'dim')
    last_row = status_row
    if trend:
        last_row += 1
        for col, text, color in trend:
            add(last_row, col, text, color)
    if alerts:
        last_row += 1
        add(last_row, (width - 1 - len(alerts)) // 2, alerts, 'alert')

    if show_hint and last_row < height - 2:
        hints = ['p to pet  ·  w to water  ·  ? for help  ·  q to quit', 'p pet · w water · ? help · q quit']
        hint = next((hint for hint in hints if len(hint) <= width - 2), None)
        if hint:
            add(height - 2, 1, hint, 'dim')
        else:
            hint = 'p pet · ? help · q quit'
            add(height - 2, 0, hint, 'dim')
        # The clock sits in the bottom-right corner, when it fits beside the hint
        clock = format_clock(vitals.now)
        clock_col = width - 2 - len(clock)
        if clock_col >= len(hint) + 4:
            add(height - 2, clock_col, clock, 'dim')
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


def holiday_sayings(vitals):
    """Today's holiday lines, if it's a holiday."""
    sayings = list(HOLIDAY_SAYINGS.get(vitals.holiday, []))
    if vitals.holiday == 'birthday':
        age = vitals.year - PI_PET_BIRTH_YEAR
        sayings.append(f"I'm {age} today!" if age > 0 else 'I was born today!')
    return sayings


def pick_saying(mood, vitals, rng=random):
    """Something to say for this mood; calm moods sometimes talk to a pal (or brag about the plant) instead,
    and a busy pet sometimes says which program is keeping it busy."""
    if mood == 'busy' and vitals.busiest and rng.random() < BUSY_SAYING_CHANCE:
        return BUSY_SAYING.format(vitals.busiest[0])
    friend_sayings = ((CLAUDE_SAYINGS if vitals.claude_here else [])
                      + (GUARD_SAYINGS if vitals.adguard_running and vitals.dns_answering is not False else [])
                      + (DOCTOR_SAYINGS if vitals.pi_vitals_running else [])
                      + (PLANT_SAYINGS if plant_stage(vitals.uptime_sec) == len(PLANT_STAGES) - 1 else [])
                      + holiday_sayings(vitals)
                      + (WEAK_WIFI_SAYINGS if is_wifi_weak(vitals) else []))
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


def memories_path():
    """Where Pi Pet keeps its memories: $XDG_STATE_HOME/pi-pet/memories.json, which is
    ~/.local/state/pi-pet/memories.json unless XDG_STATE_HOME says otherwise."""
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(os.path.expanduser('~'), '.local', 'state')
    return os.path.join(base, 'pi-pet', 'memories.json')


def load_memories(path):
    """Pi Pet's memories from disk. A missing, unreadable or damaged file (or a damaged entry) means
    starting fresh for it, never a crash."""
    memories = dict(DEFAULT_MEMORIES)
    try:
        with open(path) as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return memories
    if not isinstance(saved, dict):
        return memories
    for key in ('visits', 'times_petted', 'times_watered', 'shooting_stars_seen'):
        value = saved.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            memories[key] = value
    try:
        memories['first_met'] = datetime.date.fromisoformat(saved.get('first_met')).isoformat()
    except (TypeError, ValueError):
        pass
    last_seen = saved.get('last_seen')
    if isinstance(last_seen, (int, float)) and not isinstance(last_seen, bool):
        memories['last_seen'] = last_seen
    return memories


def save_memories(path, memories):
    """Write the memories to a temporary file and swap it in, so a crash can't leave half a file.
    Returns False (instead of raising) if it can't be written."""
    temp_path = path + '.tmp'
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(temp_path, 'w') as f:
            json.dump(memories, f, indent=2)
        os.replace(temp_path, path)
        return True
    except OSError:
        return False


def update_memories(path, add=None, first_met=None, last_seen=None):
    """Add counts to the memories on disk and return what's saved. It re-reads the file first, so two
    Pi Pets running at once each add their own counts instead of overwriting each other's."""
    memories = load_memories(path)
    for key, amount in (add or {}).items():
        memories[key] += amount
    if first_met and not memories['first_met']:
        memories['first_met'] = first_met
    if last_seen is not None:
        memories['last_seen'] = last_seen
    save_memories(path, memories)
    return memories


class LiveState:
    """What the live view keeps between frames: the mood, what the pet is saying, petting and greetings.

    The timing rules live here instead of in the curses loop, so tests can drive them with a fake clock
    (`now`, in seconds) and scripted readings.
    """

    QUIT_KEYS = (ord('q'), ord('Q'), 27)  # 27 is Esc
    PET_KEYS = (ord('p'), ord('P'))
    HELP_KEYS = (ord('?'), ord('h'), ord('H'))
    WATER_KEYS = (ord('w'), ord('W'))

    def __init__(self, vitals, rng=random, memories=None, wall_time=None):
        """memories: what load_memories found, or None to remember nothing. wall_time: time.time() now."""
        self.vitals = vitals
        self.rng = rng
        self.mood = vitals.mood()
        self.memories = memories
        self.unsaved = {}  # counts added since the last save, for run_live to write with update_memories
        last_seen = memories['last_seen'] if memories else None
        if last_seen is not None and wall_time is not None and wall_time - last_seen >= MISSED_YOU_SECONDS:
            self.saying = MISSED_YOU_SAYING
        elif vitals.claude_here:
            self.saying = CLAUDE_GREETING
        else:
            self.saying = pick_saying(self.mood, vitals, rng)
        self.petted_until = 0
        self.was_petted = False
        self.claude_was_here = vitals.claude_here
        self.show_help = False
        self.wish_until = 0
        self.streak_on_screen = False
        self.watering_until = 0

    def press(self, key, now):
        """Handle one key press (curses gives -1 for none). Returns False when it's time to quit."""
        if key in self.QUIT_KEYS:
            return False
        if key in self.PET_KEYS:
            if not self.was_petted:
                self.saying = self.rng.choice(SAYINGS['happy'])
                if now >= self.petted_until:
                    self.remember('times_petted')  # once per petting, not per key press
            self.petted_until = now + PET_SECONDS  # petting again keeps it happy longer
        if key in self.HELP_KEYS:
            self.show_help = not self.show_help
        if key in self.WATER_KEYS:
            if now >= self.watering_until:
                self.remember('times_watered')  # once per watering, not per key press
                if now >= self.petted_until:
                    self.saying = WATER_SAYING
            self.watering_until = now + WATER_SECONDS
        return True

    def watering(self, now):
        return now < self.watering_until

    def step(self, tick, now):
        """Move on one frame: sensors every 2 seconds, and a new thing to say every ~10 seconds or when
        the mood changes. Returns the mood to show."""
        petted = now < self.petted_until
        wishing = now < self.wish_until
        if self.saying == WATER_SAYING and not self.watering(now) and not petted:
            self.saying = pick_saying(self.mood, self.vitals, self.rng)  # done watering
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
        if streak and not self.streak_on_screen:
            self.remember('shooting_stars_seen')
            if now >= self.petted_until:
                self.saying = WISH_SAYING
                self.wish_until = now + WISH_SECONDS
        self.streak_on_screen = streak

    def remember(self, key):
        """Count something for the memories file (if the pet is remembering at all)."""
        if self.memories is None:
            return
        self.memories[key] += 1
        self.unsaved[key] = self.unsaved.get(key, 0) + 1


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
        'pumpkin': curses.color_pair(4) | curses.A_BOLD,
    }


def run_live(screen, forced_mood=None, interface=None):
    curses.curs_set(0)
    screen.nodelay(True)
    colors = curses_colors()
    path = memories_path()
    state = LiveState(Vitals(forced_mood, interface), memories=load_memories(path), wall_time=time.time())
    state.memories = update_memories(path, add={'visits': 1}, first_met=datetime.date.today().isoformat())
    tick = 0

    try:
        while True:
            now = time.monotonic()
            if not state.press(screen.getch(), now):
                return
            shown_mood = state.step(tick, now)

            screen.erase()
            height, width = screen.getmaxyx()
            cells = layout(height, width, shown_mood, tick, state.saying, state.vitals,
                           show_help=state.show_help, memories=state.memories, watering=state.watering(now))
            for row, col, text, color in cells:
                screen.addstr(row, col, text, colors[color])
            screen.refresh()
            state.notice(cells, now)
            if state.unsaved:
                state.memories = update_memories(path, add=state.unsaved)
                state.unsaved = {}
            tick += 1
            time.sleep(1 / FPS)
    finally:
        update_memories(path, add=state.unsaved, last_seen=time.time())  # also on Ctrl+C


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
