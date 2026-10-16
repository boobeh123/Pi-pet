#!/usr/bin/env python3
"""Render Pi Pet's demo GIF from its own layout() code with scripted readings.

    python3 docs/renderDemo.py                 # writes docs/piPetDemo.gif
    python3 docs/renderDemo.py --out demo.gif  # somewhere else

Every frame is drawn by piPet.layout(), the same function the live view paints; only the readings are
scripted, so one clip can show day and night, holidays, the pals and every mood.

It needs Pillow (`sudo apt install python3-pil`, or `pip install pillow` in a venv), ffmpeg, and fonts that
fontconfig can find: DejaVu Sans Mono, plus a monospace font with Braille for the sparklines (FreeMono on
Raspberry Pi OS). It sends nothing anywhere: frames go to a temporary folder that's deleted afterwards,
and the only file it writes is the GIF.
"""

import argparse
import datetime
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tests'))
import piPet  # noqa: E402  (imported after the path is set)
from piPetTest import make_vitals, DAY  # noqa: E402

WIDTH, HEIGHT = 80, 24  # terminal cells
FONT_SIZE = 15
FPS = piPet.FPS
BACKGROUND = (24, 24, 27)
FOREGROUND = (212, 212, 212)
# (RGB, bold) for each layout color, like a dark terminal theme
COLORS = {
    'leaf': ((110, 224, 110), True), 'berry': ((240, 90, 90), True), 'extra': ((78, 201, 214), False),
    'plain': (FOREGROUND, False), 'dim': ((128, 128, 128), False), 'alert': ((224, 85, 85), False),
    'sun': ((245, 213, 71), True), 'sunset': ((255, 96, 64), True), 'moon': ((255, 255, 255), True),
    'planet': ((214, 112, 214), False), 'shooting_star': ((245, 213, 71), True), 'grass': ((76, 175, 80), False),
    'claude': ((215, 186, 90), False), 'guard': ((92, 156, 255), True), 'pot': ((215, 186, 90), False),
    'cloud': (FOREGROUND, False), 'doctor': ((255, 255, 255), True), 'pumpkin': ((255, 165, 40), True),
}

# Readings shared by every scene: a healthy Pi with its trend history, all the regular pals visiting,
# and a strong Wi-Fi signal
BASE = dict(
    temp_c=52.3, load=0.6, net_rate=3 * 1024, wifi_dbm=-38, claude_here=True, adguard_running=True,
    dns_answering=True, pi_vitals_running=True, vscode_open=True, firefox_open=True, uptime_sec=8 * DAY,
    moon_age=7.4, sky_seed=1, day_of_year=290, busiest=('VS Code', 12.0),
    # 10 minutes of history with some ups and downs, so the trend row's sparklines show their shape
    temp_history=[56 + 9 * math.sin(i / 35) for i in range(300)], load_history=[1.4 + 1.1 * math.sin(i / 22) for i in range(300)],
)
# Memories of a pet that met you 40 days before the scenes' Oct 15, so it has grown plump
MEMORIES = {**piPet.DEFAULT_MEMORIES, 'first_met': '2026-09-05', 'visits': 31, 'times_petted': 64, 'times_watered': 12,
            'shooting_stars_seen': 252, 'badges': {'first_pet': '2026-10-13', 'stargazer': '2026-10-13',
                                                    'star_collector': '2026-10-15', 'whole_gang': '2026-10-15'}}
SCENE_DATES = {'halloween': (10, 31), 'christmas': (12, 24)}  # holiday scenes happen on the day; the rest on Oct 15


def first_streak_tick(seed):
    tick = 0
    while not piPet.build_shooting_star(WIDTH, tick, seed):
        tick += 1
    return tick


# (mood, saying, frames, readings that differ from BASE, layout options)
OPENING = ('content', 'oh hi Claude!', 12, dict(hour=21.5), {})
SCENES = [
    ('content', piPet.WISH_SAYING, 8, dict(hour=21.5), {}),  # starts as the shooting star appears
    ('happy', 'hehe!', 10, dict(hour=21.5), {}),
    ('content', 'just vibing', 8, dict(hour=21.5), dict(pal_saying=('fox', 'so many tabs!'))),
    ('content', 'just vibing', 8, dict(hour=21.5), dict(pal_saying=('vscode', 'same, honestly'))),
    ('content', piPet.WATER_SAYING, 10, dict(hour=21.5), dict(watering=True)),
    ('content', 'just vibing', 16, dict(hour=21.5), dict(show_help=True)),
    ('sleepy', 'zzz...', 8, dict(hour=23.0, load=0.1, net_rate=512, sound_playing=True), {}),
    ('groggy', 'five more minutes...', 8, dict(hour=6.4, uptime_sec=150), {}),
    ('busy', 'VS Code is keeping me busy!', 8, dict(hour=11.0, load=2.6), {}),
    ('surfing', 'whee, packets!', 6, dict(hour=12.5, load=1.0, net_rate=420 * 1024), {}),
    ('hot', 'need... ice...', 6, dict(hour=14.0, temp_c=73.8, load=1.2), {}),
    ('content', 'nice and cool', 10, dict(hour=14.5, rainbow=True), {}),
    ('stuffed', 'no room for dessert', 6, dict(hour=16.0, disk_used_percent=93.2), {}),
    ('dizzy', 'power feels low...', 6, dict(hour=16.5, undervoltage=True), {}),
    ('content', 'trick or treat!', 14, dict(hour=20.5, holiday='halloween'), {}),
    ('content', 'look at the lights!', 10, dict(hour=12.0, holiday='christmas'), {}),
    ('lonely', 'hello? anyone?', 8, dict(hour=22.0, online=False), {}),
]


def find_font(pattern):
    """The font file fontconfig picks for a pattern like 'DejaVu Sans Mono:bold'."""
    try:
        path = subprocess.run(['fc-match', '-f', '%{file}', pattern], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Couldn't run fc-match to find fonts (it comes with fontconfig).")
    if not path or not Path(path).exists():
        sys.exit(f'No font found for {pattern!r}.')
    return path


def render_frames(out_dir):
    """Draw every frame as a PNG in out_dir. Returns how many there are."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit('Rendering needs Pillow: sudo apt install python3-pil (or pip install pillow in a venv).')

    missing = set(piPet.ANSI_COLORS) - set(COLORS)
    if missing:
        sys.exit(f'Give these layout colors an RGB in COLORS: {sorted(missing)}')
    regular = ImageFont.truetype(find_font('DejaVu Sans Mono'), FONT_SIZE)
    bold = ImageFont.truetype(find_font('DejaVu Sans Mono:bold'), FONT_SIZE)
    braille = ImageFont.truetype(find_font('monospace:charset=2800'), FONT_SIZE)  # DejaVu Sans Mono has none
    cell_w, cell_h = round(regular.getlength('M')), round(FONT_SIZE * 1.2)

    def render(cells):
        image = Image.new('RGB', (WIDTH * cell_w, HEIGHT * cell_h), BACKGROUND)
        draw = ImageDraw.Draw(image)
        for row, col, text, color in cells:
            rgb, is_bold = COLORS[color]
            for offset, char in enumerate(text):
                if char == ' ':
                    continue
                font = braille if 0x2800 <= ord(char) <= 0x28FF else bold if is_bold else regular
                draw.text(((col + offset) * cell_w, row * cell_h), char, font=font, fill=rgb)
        return image

    # The opening scene ends just as this seed's first shooting star appears, so the next scene is the wish
    tick = first_streak_tick(BASE['sky_seed']) - OPENING[2]
    frame = 0
    for mood, saying, frames, readings, options in [OPENING] + SCENES:
        month, day = SCENE_DATES.get(readings.get('holiday'), (10, 15))
        now = datetime.datetime(2026, month, day) + datetime.timedelta(hours=readings['hour'])  # for the clock
        vitals = make_vitals(**{**BASE, 'now': now, **readings})
        for _ in range(frames):
            cells = piPet.layout(HEIGHT, WIDTH, mood, tick, saying, vitals, memories=MEMORIES, **options)
            render(cells).save(Path(out_dir) / f'frame{frame:03d}.png')
            tick += 1
            frame += 1
    return frame


def build_gif(frames_dir, out_path):
    """Turn the frames into a GIF with ffmpeg: one shared 64-color palette, no dithering."""
    if not shutil.which('ffmpeg'):
        sys.exit('Building the GIF needs ffmpeg: sudo apt install ffmpeg')
    frames = str(Path(frames_dir) / 'frame%03d.png')
    palette = str(Path(frames_dir) / 'palette.png')
    quiet = ['ffmpeg', '-loglevel', 'error', '-y', '-framerate', str(FPS)]
    subprocess.run(quiet + ['-i', frames, '-vf', 'palettegen=stats_mode=full:max_colors=64', palette], check=True)
    subprocess.run(quiet + ['-i', frames, '-i', palette, '-lavfi', 'paletteuse=dither=none', '-loop', '0',
                            str(out_path)], check=True)


def main():
    parser = argparse.ArgumentParser(description="Render Pi Pet's demo GIF from its own layout code.")
    parser.add_argument('--out', default=str(REPO / 'docs' / 'piPetDemo.gif'), help='where to write the GIF')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as frames_dir:
        count = render_frames(frames_dir)
        build_gif(frames_dir, args.out)
    size = Path(args.out).stat().st_size
    print(f'{args.out}: {count} frames, {count / FPS:.1f} seconds, {size / 1024:.0f} KB')


if __name__ == '__main__':
    main()
