#!/usr/bin/env python3
"""Pi Pet: a little raspberry that lives in your terminal and reacts to the Pi's real vitals.

    python3 piPet.py          # live, press q to quit
    python3 piPet.py --once   # print one frame and exit (no curses)
    python3 piPet.py --mood hot   # force a mood: content, sleepy, hot, busy, surfing
"""

import curses
import glob
import os
import random
import sys
import time

FPS = 4
WIFI_INTERFACE = 'wlan0'

# Thresholds that decide the pet's mood
HOT_TEMP_C = 70
BUSY_LOAD = 2.0
SURF_BYTES_PER_SEC = 200 * 1024
SLEEPY_LOAD = 0.3
SLEEPY_BYTES_PER_SEC = 5 * 1024

# Faces: (left eye, right eye, mouth). Every character must be exactly one terminal column wide,
# so no emoji or East Asian wide characters, or the body falls out of alignment.
FACES = {
    'content': [('o', 'o', '‿'), ('-', '-', '‿')],  # second frame is a blink
    'sleepy': [('-', '-', '_')],
    'hot': [('>', '<', '~')],
    'busy': [('o', 'o', 'o')],
    'surfing': [('^', '^', 'w')],
}

SAYINGS = {
    'content': ['all good in here', 'just vibing', 'nice and cool', 'hi Bobby!'],
    'sleepy': ['zzz...', 'so quiet...', 'wake me if something happens'],
    'hot': ['it is getting toasty', 'fan, do your thing!', 'need... ice...'],
    'busy': ['working hard!', 'so many processes', 'cpu go brrr'],
    'surfing': ['whee, packets!', "who's downloading?", 'riding the wifi waves'],
}


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


def read_net_bytes():
    """Total bytes received + sent on Wi-Fi so far."""
    try:
        with open('/proc/net/dev') as f:
            for line in f:
                if line.strip().startswith(f'{WIFI_INTERFACE}:'):
                    fields = line.split(':')[1].split()
                    return int(fields[0]) + int(fields[8])
    except OSError:
        pass
    return None


class Vitals:
    """Polls the sensors and works out network speed between polls."""

    def __init__(self, forced_mood=None):
        self.forced_mood = forced_mood
        self.last_net = read_net_bytes()
        self.last_time = time.monotonic()
        self.net_rate = 0
        self.refresh()

    def refresh(self):
        self.temp_c = read_temp_c()
        self.load = os.getloadavg()[0]
        self.fan_rpm = read_fan_rpm()

        now = time.monotonic()
        net = read_net_bytes()
        if net is not None and self.last_net is not None and now > self.last_time:
            self.net_rate = (net - self.last_net) / (now - self.last_time)
        self.last_net, self.last_time = net, now

    def mood(self):
        if self.forced_mood:
            return self.forced_mood
        if self.temp_c is not None and self.temp_c >= HOT_TEMP_C:
            return 'hot'
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


def build_pet(mood, tick):
    """Return the pet as a list of (text, color) lines. Colors: leaf, berry, extra."""
    frames = FACES[mood]
    # Blink for one frame every ~3 seconds when content
    face = frames[1] if mood == 'content' and tick % 12 == 0 else frames[0]
    left_eye, right_eye, mouth = face

    # Busy pet bounces up and down; surfing pet sways left and right
    bounce = mood == 'busy' and tick % 2 == 0
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
    return f'temp {temp}  ·  load {vitals.load:.2f}  ·  fan {fan}  ·  wifi {format_rate(vitals.net_rate)}'


def build_zzz(tick):
    """Floating Zs for the sleepy pet, drifting upward.

    Returns (row, col, letter) measured from the pet's top-left corner. The Zs stay beside the
    head and body (rows 0-5, right of the 13-column body), so they never land on the speech bubble.
    """
    phase = tick % 6
    return [(5 - phase, 14 + phase, 'z'), ((8 - phase) % 6, 16 + (phase + 3) % 6, 'Z')]


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

def get_forced_mood():
    """Read --mood NAME from the command line, or None."""
    if '--mood' not in sys.argv:
        return None
    index = sys.argv.index('--mood')
    mood = sys.argv[index + 1] if index + 1 < len(sys.argv) else ''
    if mood not in FACES:
        sys.exit(f"Unknown mood '{mood}'. Pick one of: {', '.join(FACES)}")
    return mood


def print_once():
    vitals = Vitals(get_forced_mood())
    time.sleep(1)  # one second of network data so speed isn't 0
    vitals.refresh()
    mood = vitals.mood()

    for line in build_bubble(random.choice(SAYINGS[mood])):
        print(line)
    for text, _ in build_pet(mood, tick=1):
        print(text)
    print()
    print(f'mood: {mood}')
    print(build_status(vitals))


def run_live(screen):
    curses.curs_set(0)
    screen.nodelay(True)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_CYAN, -1)
    colors = {
        'leaf': curses.color_pair(1) | curses.A_BOLD,
        'berry': curses.color_pair(2) | curses.A_BOLD,
        'extra': curses.color_pair(3),
    }

    vitals = Vitals(get_forced_mood())
    tick = 0
    mood = vitals.mood()
    saying = random.choice(SAYINGS[mood])

    while True:
        key = screen.getch()
        if key in (ord('q'), ord('Q'), 27):
            return

        # Sensors every 2 seconds; a new thing to say every ~10 seconds or on a mood change
        if tick % (FPS * 2) == 0:
            vitals.refresh()
            new_mood = vitals.mood()
            if new_mood != mood or tick % (FPS * 10) == 0:
                saying = random.choice(SAYINGS[new_mood])
            mood = new_mood

        screen.erase()
        height, width = screen.getmaxyx()
        pet = build_pet(mood, tick)
        bubble = build_bubble(saying)
        status = build_status(vitals)

        block_height = len(bubble) + len(pet) + 3
        top = max(0, (height - block_height) // 2)
        left = max(0, (width - 13) // 2)

        def put(row, col, text, attr=0):
            # Skip anything that would fall off a small terminal
            if 0 <= row < height - 1 and col < width:
                screen.addnstr(row, max(0, col), text, max(0, width - col - 1), attr)

        for i, line in enumerate(bubble):
            put(top + i, left, line)
        pet_top = top + len(bubble)
        for i, (text, color) in enumerate(pet):
            put(pet_top + i, left, text, colors[color])
        if mood == 'sleepy':
            for row, col, letter in build_zzz(tick // 2):
                put(pet_top + row, left + col, letter, colors['extra'])

        put(pet_top + len(pet) + 1, (width - len(status)) // 2, status, curses.A_DIM)
        put(height - 2, 1, 'q to quit', curses.A_DIM)

        screen.refresh()
        tick += 1
        time.sleep(1 / FPS)


def main():
    if '--once' in sys.argv:
        print_once()
        return
    try:
        curses.wrapper(run_live)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
