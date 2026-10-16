"""Tests for Pi Pet. Standard library only:

    python3 -m unittest discover -s tests -p "*Test.py"
"""

import datetime
import io
import json
import os
import tempfile
import random
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import piPet  # noqa: E402  (imported after the path is set)

DAY = 86400
HOLIDAYS = (None, 'halloween', 'christmas', 'new_year', 'pi_day', 'birthday')
TICKS = range(24)  # long enough to cover every animation cycle (blink is every 12 ticks)


def make_vitals(temp_c=50.0, load=0.5, net_rate=0, forced_mood=None, undervoltage=False,
                disk_used_percent=50.0, memory_free_percent=70.0, uptime_sec=10_000.0, online=True, hour=12.0,
                claude_here=False, adguard_running=None, pi_vitals_running=False,
                moon_age=14.8, sky_seed=0, day_of_year=200, dns_answering=None, holiday=None, year=2026,
                now=datetime.datetime(2026, 10, 15, 20, 42), wifi_dbm=None):
    """A Vitals with chosen readings, without touching the real sensors. Defaults are a healthy Pi."""
    vitals = object.__new__(piPet.Vitals)
    vitals.forced_mood = forced_mood
    vitals.chosen_interface = vitals.interface = 'wlan0'
    vitals.temp_c = temp_c
    vitals.load = load
    vitals.fan_rpm = 2000
    vitals.net_rate = net_rate
    vitals.undervoltage = undervoltage
    vitals.disk_used_percent = disk_used_percent
    vitals.memory_free_percent = memory_free_percent
    vitals.uptime_sec = uptime_sec
    vitals.online = online
    vitals.hour = hour
    vitals.claude_here = claude_here
    vitals.adguard_running = adguard_running
    vitals.pi_vitals_running = pi_vitals_running
    vitals.moon_age = moon_age
    vitals.sky_seed = sky_seed
    vitals.day_of_year = day_of_year
    vitals.dns_answering = dns_answering
    vitals.holiday = holiday
    vitals.year = year
    vitals.now = now
    vitals.wifi_dbm = wifi_dbm
    return vitals


def is_one_column(char):
    # Wide (W/F) and ambiguous (A) characters take two columns in some terminals,
    # and combining marks take zero; any of them knocks the pet's body out of line
    return (
        unicodedata.east_asian_width(char) in ('Na', 'N', 'H')
        and not unicodedata.combining(char)
    )


class DrawingTest(unittest.TestCase):
    def all_frames(self):
        for mood in piPet.FACES:
            for tick in TICKS:
                yield mood, tick, piPet.build_pet(mood, tick)

    def test_every_character_is_one_column_wide(self):
        for mood, tick, lines in self.all_frames():
            for text, _ in lines:
                for char in text:
                    with self.subTest(mood=mood, tick=tick, char=char):
                        self.assertTrue(is_one_column(char), f'{char!r} (U+{ord(char):04X}) is not one column wide')
        for sayings in piPet.SAYINGS.values():
            for saying in sayings:
                for line in piPet.build_bubble(saying):
                    for char in line:
                        self.assertTrue(is_one_column(char), f'{char!r} in the bubble for "{saying}"')

    def test_body_lines_line_up(self):
        for mood, tick, lines in self.all_frames():
            widths = {len(text) for text, _ in lines if text}
            with self.subTest(mood=mood, tick=tick):
                self.assertEqual(len(widths), 1, f'lines have different widths: {sorted(widths)}')

    def test_every_line_has_a_known_color(self):
        for mood, tick, lines in self.all_frames():
            for _, color in lines:
                self.assertIn(color, ('leaf', 'berry', 'extra'))

    def test_content_pet_blinks(self):
        eyes = {piPet.build_pet('content', tick)[2][0] for tick in TICKS}
        self.assertEqual(len(eyes), 2, 'content pet should show open and closed eyes')

    def test_bubble_borders_match(self):
        top, middle, bottom, _ = piPet.build_bubble('hello there')
        self.assertEqual(len(top), len(bottom))
        self.assertIn('hello there', middle)

    def test_extras_never_cover_the_bubble_or_the_pet(self):
        # Same placement as layout: bubble directly above the pet, both starting at the same column
        for mood in piPet.FACES:
            for saying in piPet.SAYINGS[mood]:
                bubble = piPet.build_bubble(saying)
                for tick in TICKS:
                    pet = [text for text, _ in piPet.build_pet(mood, tick)]
                    drawn = {(i - len(bubble), col) for i, line in enumerate(bubble + pet)
                             for col, char in enumerate(line) if char != ' '}
                    for row, col, text in piPet.build_extras(mood, tick):
                        for offset, char in enumerate(text):
                            with self.subTest(mood=mood, saying=saying, tick=tick, char=char):
                                self.assertNotIn((row, col + offset), drawn)

    def test_moods_with_extras(self):
        moods = {mood for mood in piPet.FACES if any(piPet.build_extras(mood, tick) for tick in TICKS)}
        self.assertEqual(moods, {'sleepy', 'lonely', 'dizzy', 'happy'})

    def test_animated_faces_change(self):
        for mood in ('content', 'hungry', 'stuffed', 'groggy'):
            faces = {piPet.pick_face(mood, tick) for tick in TICKS}
            with self.subTest(mood=mood):
                self.assertEqual(faces, set(piPet.FACES[mood]))

    def test_stuffed_pet_has_puffed_cheeks(self):
        cheeks = piPet.build_pet('stuffed', 1)[3][0]
        self.assertIn('(:', cheeks)
        self.assertIn(':)', cheeks)

    def test_sayings_fit_a_small_terminal(self):
        for sayings in piPet.SAYINGS.values():
            for saying in sayings:
                widest = max(len(line) for line in piPet.build_bubble(saying))
                self.assertLessEqual(widest, 60, f'"{saying}" makes a {widest}-column bubble')


class LayoutTest(unittest.TestCase):
    SIZES = [(24, 80), (40, 120), (14, 30), (12, 40), (11, 24)]  # (height, width), down to the minimum

    def all_layouts(self, sizes=SIZES):
        # With both pals and the plant in flower (its tallest), wherever there's room
        vitals = make_vitals(claude_here=True, adguard_running=True, pi_vitals_running=True, uptime_sec=8 * DAY,
                             wifi_dbm=-60)  # the longest status line, with signal bars
        for height, width in sizes:
            for mood, sayings in piPet.SAYINGS.items():
                for saying in sayings:
                    for tick in TICKS:
                        cells = piPet.layout(height, width, mood, tick, saying, vitals)
                        yield (height, width, mood, saying, tick), cells

    def test_everything_stays_inside_the_window(self):
        for (height, width, *rest), cells in self.all_layouts():
            for row, col, text, _ in cells:
                with self.subTest(size=(height, width), case=rest, text=text):
                    self.assertTrue(0 <= row < height - 1, f'row {row}')
                    self.assertTrue(col >= 0 and col + len(text) <= width - 1, f'cols {col}-{col + len(text)}')

    def test_nothing_draws_over_anything_else(self):
        for case, cells in self.all_layouts():
            seen = set()
            for row, col, text, _ in cells:
                for offset, char in enumerate(text):
                    if char != ' ':
                        with self.subTest(case=case, char=char):
                            self.assertNotIn((row, col + offset), seen)
                        seen.add((row, col + offset))

    def test_status_line_always_shows(self):
        for case, cells in self.all_layouts():
            with self.subTest(case=case):
                self.assertTrue(any('load' in text for _, _, text, _ in cells))

    def test_roomy_window_shows_everything(self):
        cells = piPet.layout(24, 80, 'content', 1, 'just vibing', make_vitals())
        texts = [text for _, _, text, _ in cells]
        self.assertIn('(  just vibing  )', texts)
        self.assertIn('p to pet  ·  w to water  ·  ? for help  ·  q to quit', texts)
        self.assertTrue(any(text.startswith('temp ') for text in texts), 'full status line expected')

    def test_tiny_window_shows_a_message(self):
        for height, width in ((10, 80), (24, 23), (5, 10), (1, 1)):
            cells = piPet.layout(height, width, 'content', 1, 'just vibing', make_vitals())
            with self.subTest(size=(height, width)):
                self.assertLessEqual(len(cells), 1)
                self.assertTrue(all('window too small'.startswith(text) for _, _, text, _ in cells))

    def test_busy_bounce_moves_the_pet_not_the_bubble(self):
        # 16 rows: room for the bubble but not the scene, so the pet drops into the gap below it
        def bubble_and_leaf_rows(tick):
            cells = piPet.layout(16, 80, 'busy', tick, 'working hard!', make_vitals())
            bubble_row = next(row for row, _, text, _ in cells if 'working hard!' in text)
            leaf_row = next(row for row, _, text, _ in cells if '\\\\|//' in text)
            return bubble_row, leaf_row

        (bubble_up, leaf_up), (bubble_down, leaf_down) = bubble_and_leaf_rows(1), bubble_and_leaf_rows(2)
        self.assertEqual(bubble_up, bubble_down)
        self.assertEqual(leaf_down, leaf_up + 1)


class PettingTest(unittest.TestCase):
    def test_happy_pet_shows_red_hearts(self):
        cells = piPet.layout(24, 80, 'happy', 1, 'hehe!', make_vitals())
        hearts = [color for _, _, text, color in cells if text == '<3']
        self.assertEqual(hearts, ['berry', 'berry'])

    def test_happy_pet_hops(self):
        def leaf_row(height, tick):
            cells = piPet.layout(height, 80, 'happy', tick, 'hehe!', make_vitals())
            return next(row for row, _, text, _ in cells if '\\\\|//' in text)
        self.assertEqual(leaf_row(24, 2), leaf_row(24, 1) - 1, 'on the ground it hops up')
        self.assertEqual(leaf_row(16, 2), leaf_row(16, 1) + 1, 'without ground it drops into the gap')

    def test_sensors_never_pick_happy(self):
        # Happiness only comes from petting
        for readings in (dict(), dict(load=0.1), dict(load=1.0), dict(net_rate=300 * 1024)):
            with self.subTest(readings=readings):
                self.assertNotEqual(make_vitals(**readings).mood(), 'happy')

    def test_happy_can_be_forced_for_demos(self):
        self.assertEqual(piPet.parse_args(['--mood', 'happy']).mood, 'happy')


class MoodTest(unittest.TestCase):
    def test_moods_by_reading(self):
        everything_wrong = dict(undervoltage=True, temp_c=72, disk_used_percent=95, memory_free_percent=5,
                                uptime_sec=60, online=False, load=3, net_rate=500_000)
        cases = [
            ('dizzy', everything_wrong),  # dizzy beats everything
            ('hot', {**everything_wrong, 'undervoltage': False}),
            ('stuffed', {**everything_wrong, 'undervoltage': False, 'temp_c': 60}),
            ('hungry', dict(memory_free_percent=5, uptime_sec=60, online=False, load=3)),
            ('groggy', dict(uptime_sec=60, online=False, load=3, net_rate=500_000)),  # groggy beats lonely and busy
            ('lonely', dict(online=False, load=3, net_rate=500_000)),
            ('busy', dict(temp_c=60, load=2.5, net_rate=500_000)),  # busy beats surfing
            ('surfing', dict(temp_c=60, load=1.0, net_rate=300 * 1024)),
            ('sleepy', dict(temp_c=50, load=0.1, net_rate=1024)),
            ('content', dict(temp_c=55, load=0.8, net_rate=20 * 1024)),
        ]
        for expected, readings in cases:
            with self.subTest(expected=expected):
                self.assertEqual(make_vitals(**readings).mood(), expected)

    def test_thresholds_are_inclusive(self):
        self.assertEqual(make_vitals(temp_c=piPet.HOT_TEMP_C).mood(), 'hot')
        self.assertEqual(make_vitals(load=piPet.BUSY_LOAD).mood(), 'busy')
        self.assertEqual(make_vitals(net_rate=piPet.SURF_BYTES_PER_SEC).mood(), 'surfing')
        self.assertEqual(make_vitals(disk_used_percent=piPet.STUFFED_DISK_PERCENT).mood(), 'stuffed')

    def test_thresholds_just_outside(self):
        self.assertNotEqual(make_vitals(memory_free_percent=piPet.HUNGRY_MEMORY_PERCENT).mood(), 'hungry')
        self.assertNotEqual(make_vitals(uptime_sec=piPet.GROGGY_UPTIME_SEC).mood(), 'groggy')

    def test_missing_sensors_never_trigger_a_mood(self):
        vitals = make_vitals(load=0.8, net_rate=20 * 1024, undervoltage=None, disk_used_percent=None,
                             memory_free_percent=None, uptime_sec=None)
        self.assertEqual(vitals.mood(), 'content')

    def test_missing_temperature_does_not_crash(self):
        self.assertEqual(make_vitals(temp_c=None, load=0.1).mood(), 'sleepy')

    def test_forced_mood_wins(self):
        self.assertEqual(make_vitals(temp_c=90, forced_mood='sleepy').mood(), 'sleepy')

    def test_every_mood_has_a_face_and_sayings(self):
        self.assertEqual(set(piPet.FACES), set(piPet.SAYINGS))


def cell_texts(cells, color):
    return [(row, col, text) for row, col, text, cell_color in cells if cell_color == color]


class SceneTest(unittest.TestCase):
    def test_scene_layouts_stay_inside_and_never_overlap(self):
        for height, width in ((24, 80), (40, 120), (20, 40)):
            for hour in (0.0, 7.0, 12.0, 17.9, 18.0, 23.5):
                for mood in piPet.FACES:
                    for tick in TICKS:
                        cells = piPet.layout(height, width, mood, tick, piPet.SAYINGS[mood][-1],
                                             make_vitals(hour=hour, claude_here=True,
                                                         uptime_sec=(8 * DAY, 0.5 * DAY)[tick % 2],  # flower or sprout
                                                         pi_vitals_running=True, moon_age=(0.2, 14.8, 22)[tick % 3],
                                                         day_of_year=tick * 15,
                                                         adguard_running=tick % 2 == 0,  # guard awake or asleep
                                                         dns_answering=tick % 4 != 0,  # and sometimes alarmed
                                                         holiday=HOLIDAYS[tick % len(HOLIDAYS)]),
                                             watering=tick % 3 == 1)
                        seen = set()
                        for row, col, text, _ in cells:
                            with self.subTest(size=(height, width), hour=hour, mood=mood, tick=tick, text=text):
                                self.assertTrue(0 <= row < height - 1 and col >= 0 and col + len(text) <= width - 1)
                                for offset, char in enumerate(text):
                                    if char != ' ':
                                        self.assertNotIn((row, col + offset), seen)
                                        seen.add((row, col + offset))

    def test_scene_only_in_big_windows(self):
        for (height, width), expected in ((24, 80), True), ((20, 40), True), ((16, 80), False), ((24, 39), False):
            cells = piPet.layout(height, width, 'content', 1, 'just vibing', make_vitals())
            with self.subTest(size=(height, width)):
                self.assertEqual(bool(cell_texts(cells, 'grass')), expected)

    def test_sun_by_day_moon_and_stars_by_night(self):
        day = piPet.build_sky(80, 12.0, 0)
        night = piPet.build_sky(80, 0.0, 0, moon_age=22.1)  # last quarter
        self.assertEqual([text for *_, text, color in day if color in ('sun', 'moon')], ['-O-'])
        self.assertEqual([text for *_, text, color in night if color in ('sun', 'moon')], ['C'])
        self.assertFalse([cell for cell in day if cell[3] == 'plain'], 'no stars in the daytime')
        self.assertGreater(len([cell for cell in night if cell[3] == 'plain']), 3)

    def test_sun_crosses_the_sky_and_is_highest_at_noon(self):
        def sun(hour):
            row, col, _, _ = next(cell for cell in piPet.build_sky(80, hour, 0) if cell[3] == 'sun')
            return row, col
        self.assertLess(sun(7)[1], sun(12)[1])
        self.assertLess(sun(12)[1], sun(17)[1])
        self.assertLess(sun(12)[0], sun(7)[0])
        self.assertEqual(sun(12)[0], 0)

    def test_stars_stay_put_and_twinkle(self):
        frames = [piPet.build_sky(80, 22.0, tick) for tick in range(20)]
        spots = {tuple((row, col) for row, col, _, color in frame if color == 'plain') for frame in frames}
        self.assertEqual(len(spots), 1, 'stars should not move between frames')
        looks = {tuple(text for *_, text, color in frame if color == 'plain') for frame in frames}
        self.assertGreater(len(looks), 1, 'stars should twinkle')

    def test_ground_is_right_under_the_feet(self):
        for mood in piPet.FACES:
            cells = piPet.layout(24, 80, mood, 1, 'hi', make_vitals())
            feet_row = max(row for row, _, _, color in cells if color in ('berry', 'extra') and row < 20)
            ground_row = cell_texts(cells, 'grass')[0][0]
            with self.subTest(mood=mood):
                self.assertEqual(ground_row, feet_row + 1)

    def test_busy_pet_hops_up_on_the_ground(self):
        def bubble_and_leaf_rows(tick):
            cells = piPet.layout(24, 80, 'busy', tick, 'working hard!', make_vitals())
            bubble_row = next(row for row, _, text, _ in cells if 'working hard!' in text)
            leaf_row = next(row for row, _, text, _ in cells if '\\\\|//' in text)
            return bubble_row, leaf_row

        (bubble_rest, leaf_rest), (bubble_hop, leaf_hop) = bubble_and_leaf_rows(1), bubble_and_leaf_rows(2)
        self.assertEqual(bubble_rest, bubble_hop)
        self.assertEqual(leaf_hop, leaf_rest - 1)


class ClaudePalTest(unittest.TestCase):
    def fake_processes(self, names):
        paths = [f'/proc/{100 + i}/comm' for i in range(len(names))]

        def fake_open(path, *args, **kwargs):
            name = names[paths.index(path)]
            if name is None:
                raise FileNotFoundError(path)  # the process ended while we looked
            return io.StringIO(name + '\n')

        return mock.patch.object(piPet.glob, 'glob', return_value=paths), mock.patch('builtins.open', side_effect=fake_open)

    def test_reads_process_names(self):
        patch_glob, patch_open = self.fake_processes(['bash', None, 'claude', 'AdGuardHome'])
        with patch_glob, patch_open:
            self.assertEqual(piPet.read_process_names(), {'bash', 'claude', 'AdGuardHome'})

    def test_pal_frames_line_up(self):
        for frame in piPet.CLAUDE_PAL:
            self.assertEqual(len({len(line) for line in frame}), 1)
            for char in ''.join(frame):
                self.assertTrue(is_one_column(char), f'{char!r} is not one column wide')

    def pal_cells(self, height=24, width=80, mood='content', tick=1, here=True):
        cells = piPet.layout(height, width, mood, tick, 'hi', make_vitals(claude_here=here))
        return cells, [(row, col, text) for row, col, text, color in cells if color == 'claude']

    def test_pal_visits_only_while_claude_is_here(self):
        self.assertEqual(len(self.pal_cells()[1]), 3)
        self.assertEqual(self.pal_cells(here=False)[1], [])

    def test_pal_stands_on_the_ground_left_of_the_pet(self):
        for mood in piPet.FACES:
            cells, pal = self.pal_cells(mood=mood)
            pet_cols = [col for _, col, _, color in cells if color in ('leaf', 'berry')]
            ground_row = next(row for row, _, _, color in cells if color == 'grass')
            with self.subTest(mood=mood):
                self.assertEqual(max(row for row, _, _ in pal), ground_row - 1)
                self.assertLess(max(col + len(text) for _, col, text in pal), min(pet_cols))

    def test_no_room_no_pal(self):
        self.assertEqual(self.pal_cells(height=piPet.MIN_HEIGHT, width=piPet.MIN_WIDTH)[1], [])

    def test_pal_twinkles(self):
        looks = {tuple(text for _, _, text in self.pal_cells(tick=tick)[1]) for tick in TICKS}
        self.assertEqual(len(looks), 2)

    def test_chatty_moods_sometimes_talk_to_claude(self):
        rng = random.Random(1)
        said = {piPet.pick_saying('content', make_vitals(claude_here=True), rng) for _ in range(200)}
        self.assertTrue(said & set(piPet.CLAUDE_SAYINGS))
        self.assertTrue(said & set(piPet.SAYINGS['content']))

    def test_no_claude_talk_when_alone_or_urgent(self):
        rng = random.Random(1)
        for mood, here in (('content', False), ('dizzy', True), ('lonely', True), ('sleepy', True)):
            said = {piPet.pick_saying(mood, make_vitals(claude_here=here), rng) for _ in range(100)}
            with self.subTest(mood=mood, here=here):
                self.assertTrue(said <= set(piPet.SAYINGS[mood]))

    def test_friend_sayings_fit(self):
        for saying in piPet.CLAUDE_SAYINGS + [piPet.CLAUDE_GREETING]:
            for char in ''.join(piPet.build_bubble(saying)):
                self.assertTrue(is_one_column(char))


class GuardTest(unittest.TestCase):
    def guard_cells(self, running, claude_here=False, tick=1, height=24, width=80):
        cells = piPet.layout(height, width, 'content', tick, 'hi', make_vitals(adguard_running=running, claude_here=claude_here))
        guard_lines = {line for frame in piPet.GUARD_ON_WATCH + piPet.GUARD_ASLEEP for line in frame}
        return cells, [(row, col, text, color) for row, col, text, color in cells if text in guard_lines]

    def test_guard_frames_line_up(self):
        for frame in piPet.GUARD_ON_WATCH + piPet.GUARD_ASLEEP:
            self.assertEqual(len({len(line) for line in frame}), 1)
            for char in ''.join(frame):
                self.assertTrue(is_one_column(char), f'{char!r} is not one column wide')

    def test_no_guard_when_adguard_is_not_installed(self):
        self.assertEqual(self.guard_cells(running=None)[1], [])

    def test_guard_on_watch_glances_around(self):
        faces = {text for tick in TICKS for _, _, text, _ in self.guard_cells(True, tick=tick)[1] if text.startswith('(')}
        self.assertEqual(faces, {'(o_o)', '(<_<)', '(>_>)'})
        self.assertTrue(all(color == 'guard' for *_, color in self.guard_cells(True)[1]))

    def test_guard_dozes_when_adguard_stops(self):
        _, guard = self.guard_cells(False)
        self.assertIn('(-_-)', [text for _, _, text, _ in guard])
        self.assertTrue(all(color == 'dim' for *_, color in guard))
        self.assertIn('AdGuard stopped', piPet.build_alerts(make_vitals(adguard_running=False)))
        self.assertNotIn('AdGuard', piPet.build_alerts(make_vitals(adguard_running=True)))
        self.assertNotIn('AdGuard', piPet.build_alerts(make_vitals(adguard_running=None)))

    def test_guard_stands_nearest_the_pet(self):
        cells, guard = self.guard_cells(True, claude_here=True)
        claude_cols = [col for _, col, _, color in cells if color == 'claude']
        self.assertTrue(claude_cols, 'both pals should fit at 80 columns')
        self.assertLess(max(claude_cols), min(col for _, col, _, _ in guard))

    def test_guard_gets_the_only_spot(self):
        # 40 columns: room for one pal beside the pet
        cells, guard = self.guard_cells(True, claude_here=True, height=20, width=40)
        self.assertTrue(guard)
        self.assertFalse([cell for cell in cells if cell[3] == 'claude'])

    def test_pet_thanks_the_guard_only_while_on_watch(self):
        rng = random.Random(2)
        said_on_watch = {piPet.pick_saying('content', make_vitals(adguard_running=True), rng) for _ in range(200)}
        said_asleep = {piPet.pick_saying('content', make_vitals(adguard_running=False), rng) for _ in range(200)}
        self.assertTrue(said_on_watch & set(piPet.GUARD_SAYINGS))
        self.assertFalse(said_asleep & set(piPet.GUARD_SAYINGS))


class PlantTest(unittest.TestCase):
    def plant_cells(self, uptime_sec, mood='content', tick=1, height=24, width=80):
        cells = piPet.layout(height, width, mood, tick, 'hi', make_vitals(uptime_sec=uptime_sec))
        return cells, [(row, col, text, color) for row, col, text, color in cells if color == 'pot' or text == ' (@) '
                       or (color == 'leaf' and len(text) == 5)]

    def test_grows_with_uptime(self):
        cases = [(0, 0), (0.99 * DAY, 0), (1 * DAY, 1), (2.9 * DAY, 1), (3 * DAY, 2), (7 * DAY, 3), (365 * DAY, 3)]
        for uptime_sec, stage in cases:
            with self.subTest(days=uptime_sec / DAY):
                self.assertEqual(piPet.plant_stage(uptime_sec), stage)

    def test_taller_as_it_grows_and_flowers_last(self):
        heights = [len(lines) for _, lines in piPet.PLANT_STAGES]
        self.assertEqual(heights, sorted(heights))
        self.assertIn('@', piPet.PLANT_STAGES[-1][1][0][0])
        self.assertFalse(any('@' in text for _, lines in piPet.PLANT_STAGES[:-1] for text, _ in lines))

    def test_plant_lines_line_up(self):
        for _, lines in piPet.PLANT_STAGES:
            self.assertEqual(len({len(text) for text, _ in lines}), 1)
            for text, color in lines:
                self.assertIn(color, ('leaf', 'berry', 'pot'))
                for char in text:
                    self.assertTrue(is_one_column(char), f'{char!r} is not one column wide')

    def test_no_uptime_no_plant(self):
        self.assertEqual(piPet.build_plant(None), [])
        self.assertEqual(self.plant_cells(None)[1], [])

    def test_plant_stands_on_the_ground_right_of_the_pet(self):
        for mood in piPet.FACES:
            for tick in TICKS:
                cells, plant = self.plant_cells(8 * DAY, mood=mood, tick=tick)
                ground_row = next(row for row, _, _, color in cells if color == 'grass')
                pot_row, pot_col = next((row, col) for row, col, _, color in plant if color == 'pot')
                pet_cols = [col + len(text.rstrip()) for _, col, text, color in cells if color in ('leaf', 'berry') and len(text) >= 13]
                with self.subTest(mood=mood, tick=tick):
                    self.assertEqual(pot_row, ground_row - 1)
                    self.assertGreater(pot_col, max(pet_cols))

    def test_no_room_no_plant(self):
        self.assertEqual(self.plant_cells(8 * DAY, height=20, width=40)[1], [])

    def test_pet_brags_only_when_it_flowers(self):
        rng = random.Random(3)
        said_in_flower = {piPet.pick_saying('content', make_vitals(uptime_sec=8 * DAY), rng) for _ in range(200)}
        said_leafy = {piPet.pick_saying('content', make_vitals(uptime_sec=4 * DAY), rng) for _ in range(200)}
        self.assertTrue(said_in_flower & set(piPet.PLANT_SAYINGS))
        self.assertFalse(said_leafy & set(piPet.PLANT_SAYINGS))


class MoonTest(unittest.TestCase):
    def test_moon_age_matches_known_new_and_full_moons(self):
        # The total solar eclipse of 2024-04-08 (~18:20 UTC) happened at new moon,
        # and the full moon of 2024-04-23 was at ~23:49 UTC. The formula is good to about a day.
        eclipse = 1712600400
        full_moon = 1713916140
        age_at_eclipse = piPet.moon_age_days(eclipse)
        self.assertTrue(age_at_eclipse < 1 or age_at_eclipse > piPet.SYNODIC_MONTH_DAYS - 1, age_at_eclipse)
        self.assertAlmostEqual(piPet.moon_age_days(full_moon), piPet.SYNODIC_MONTH_DAYS / 2, delta=1)

    def test_shapes_through_the_month(self):
        cases = [(0, None), (2.5, ')'), (7.4, 'D'), (14.8, 'O'), (22.1, 'C'), (26.5, '('), (29.3, None)]
        for age, shape in cases:
            with self.subTest(age=age):
                self.assertEqual(piPet.moon_glyph(age), shape)

    def test_new_moon_night_has_no_moon_and_more_stars(self):
        def stars(cells):
            return {(row, col) for row, col, _, color in cells if color == 'plain'}
        new_moon = piPet.build_sky(80, 22.0, 0, moon_age=0.2)
        full_moon = piPet.build_sky(80, 22.0, 0, moon_age=14.8)
        self.assertFalse([cell for cell in new_moon if cell[3] == 'moon'])
        self.assertEqual([text for *_, text, color in full_moon if color == 'moon'], ['O'])
        self.assertGreater(len(stars(new_moon)), len(stars(full_moon)))

    def test_layout_draws_the_real_phase(self):
        cells = piPet.layout(24, 80, 'sleepy', 1, 'zzz...', make_vitals(hour=21.0, moon_age=7.4))
        self.assertEqual([text for *_, text, color in cells if color == 'moon'], ['D'])


class TwilightTest(unittest.TestCase):
    def stars(self, hour, width=80):
        return [(row, col) for row, col, _, color in piPet.build_sky(width, hour, 0) if color == 'plain']

    def colors(self, hour, load=3.0, tick=0):
        return {color for *_, color in piPet.build_sky(80, hour, tick, load)}

    def test_twilight_strength(self):
        cases = [(12.0, 0), (17.0, 0), (17.5, 0.5), (6.0, 1), (6.5, 0.5), (7.0, 0), (22.0, 0), (3.0, 0)]
        for hour, strength in cases:
            with self.subTest(hour=hour):
                self.assertAlmostEqual(piPet.twilight_strength(hour), strength)

    def test_red_sun_and_glowing_clouds_at_sunrise_and_sunset(self):
        for hour in (6.5, 17.5):
            with self.subTest(hour=hour):
                colors = set().union(*(self.colors(hour, tick=tick) for tick in range(0, 400, 40)))
                self.assertIn('sunset', colors)
                self.assertFalse(colors & {'sun', 'cloud'})
        noon = set().union(*(self.colors(12.0, tick=tick) for tick in range(0, 400, 40)))
        self.assertNotIn('sunset', noon)

    def test_stars_come_out_at_dusk_and_fade_at_dawn(self):
        self.assertEqual(self.stars(12.0), [])
        self.assertEqual(self.stars(17.0), [])
        self.assertGreater(len(self.stars(17.95)), 0)
        self.assertGreaterEqual(len(self.stars(17.95)), len(self.stars(17.4)))
        self.assertGreaterEqual(len(self.stars(6.05)), len(self.stars(6.6)))
        self.assertEqual(self.stars(7.0), [])

    def test_dusk_stars_are_the_first_stars_of_the_night(self):
        for width in (40, 80, 120):
            night = piPet.star_spots(width, width // 8)
            with self.subTest(width=width):
                self.assertEqual(piPet.star_spots(width, 2), night[:2])
                self.assertEqual(piPet.star_spots(width, width // 8), night, 'the same width gives the same stars')


class PlanetTest(unittest.TestCase):
    def planets(self, hour=22.0, day=100, width=80, moon_age=0.2, tick=0):
        return [(row, col, text) for row, col, text, color in piPet.build_sky(width, hour, tick, moon_age=moon_age,
                                                                          day_of_year=day) if color == 'planet']

    def test_one_planet_at_night_none_by_day(self):
        self.assertEqual([text for *_, text in self.planets()], ['-()-'])
        self.assertEqual(self.planets(hour=12.0), [])
        self.assertEqual(self.planets(hour=17.9), [], 'not at dusk either; it comes out with the night')

    def test_stays_put_all_night(self):
        spots = {tuple(self.planets(hour=hour, tick=tick)) for hour in (18.5, 22.0, 2.0, 5.5) for tick in (0, 50, 999)}
        self.assertEqual(len(spots), 1)

    def test_drifts_a_little_each_day_and_wraps(self):
        cols = [piPet.planet_spot(80, day)[1] for day in range(1, 60)]
        steps = [later - earlier for earlier, later in zip(cols, cols[1:])]
        self.assertIn(piPet.PLANET_DRIFT, steps)
        self.assertTrue(any(step < 0 for step in steps), 'should wrap back to the left')
        self.assertTrue(all(0 <= col <= 80 - 1 - len(piPet.PLANET) for col in cols))

    def test_hides_behind_the_moon(self):
        # Find a night where the full moon sits on the planet's spot
        for day in range(366):
            row, col = piPet.planet_spot(80, day)
            moon = [(r, c) for r, c, _, color in piPet.build_sky(80, 22.0, 0, moon_age=14.8, day_of_year=day)
                    if color == 'moon']
            if moon and moon[0][0] == row and col - 1 <= moon[0][1] <= col + len(piPet.PLANET):
                self.assertEqual(self.planets(day=day, moon_age=14.8), [])
                return
        self.fail('expected the moon to pass the planet at least once in a year')


class ShootingStarTest(unittest.TestCase):
    WINDOWS = 300

    def streak_ticks(self, seed, windows=WINDOWS, width=80):
        """Every tick with a shooting star, found without scanning all the empty ones."""
        ticks = []
        for window in range(windows):
            base = window * piPet.SHOOTING_STAR_WINDOW
            rng = random.Random(f'{seed}:{window}')
            if rng.random() < piPet.SHOOTING_STAR_CHANCE:
                start = rng.randrange(piPet.SHOOTING_STAR_WINDOW - piPet.SHOOTING_STAR_TICKS)
                ticks.extend(range(base + start, base + start + piPet.SHOOTING_STAR_TICKS))
        return ticks

    def test_about_one_every_three_minutes(self):
        windows_with_a_star = {tick // piPet.SHOOTING_STAR_WINDOW for tick in self.streak_ticks(seed=1)}
        self.assertTrue(0.2 < len(windows_with_a_star) / self.WINDOWS < 0.45, len(windows_with_a_star))

    def test_streak_crosses_and_falls(self):
        ticks = self.streak_ticks(seed=1, windows=30)
        first = ticks[:piPet.SHOOTING_STAR_TICKS]
        frames = [piPet.build_shooting_star(80, tick, seed=1) for tick in first]
        self.assertTrue(all(frames))
        self.assertIsNone(piPet.build_shooting_star(80, first[0] - 1, seed=1))
        self.assertIsNone(piPet.build_shooting_star(80, first[-1] + 1, seed=1))
        rows = [row for row, _, _ in frames]
        self.assertEqual((rows[0], rows[-1]), (0, piPet.SKY_HEIGHT - 1))
        self.assertEqual(rows, sorted(rows), 'it should only fall')
        steps = {later[1] - earlier[1] for earlier, later in zip(frames, frames[1:])}
        self.assertIn(steps, ({piPet.SHOOTING_STAR_SPEED}, {-piPet.SHOOTING_STAR_SPEED}))
        self.assertEqual({text for _, _, text in frames}, {'--*'} if steps == {piPet.SHOOTING_STAR_SPEED} else {'*--'})

    def test_runs_get_different_skies(self):
        self.assertNotEqual(self.streak_ticks(seed=1, windows=50), self.streak_ticks(seed=2, windows=50))

    def test_only_at_night(self):
        for tick in self.streak_ticks(seed=0, windows=60):
            colors = {color for *_, color in piPet.build_sky(80, 12.0, tick, seed=0)}
            self.assertNotIn('shooting_star', colors)

    def test_night_sky_with_streaks_never_overlaps_or_spills(self):
        drawn = 0
        for width in (40, 80, 120):
            for moon_age in (0.2, 7.4, 14.8):
                for hour in (19.0, 0.0, 4.5):
                    for tick in self.streak_ticks(seed=width, windows=40, width=width):
                        cells = piPet.build_sky(width, hour, tick, moon_age=moon_age, seed=width, day_of_year=tick % 366,
                                                holiday=(None, 'halloween', 'new_year')[tick % 3])
                        drawn += any(color == 'shooting_star' for *_, color in cells)
                        seen = set()
                        for row, col, text, _ in cells:
                            with self.subTest(width=width, moon_age=moon_age, hour=hour, tick=tick, text=text):
                                self.assertTrue(0 <= row < piPet.SKY_HEIGHT and col >= 0 and col + len(text) <= width - 1)
                                for offset in range(len(text)):
                                    self.assertNotIn((row, col + offset), seen)
                                    seen.add((row, col + offset))
        self.assertGreater(drawn, 100)


class ClipTest(unittest.TestCase):
    def test_clip_to_width(self):
        cases = [((5, 'abc'), (5, 'abc')), ((-1, 'abc'), (0, 'bc')), ((-3, 'abc'), (0, '')), ((-9, 'abc'), (0, '')),
                 ((8, 'abc'), (8, 'a')), ((9, 'abc'), (9, '')), ((7, 'abc'), (7, 'ab'))]
        for (col, text), expected in cases:
            with self.subTest(col=col, text=text):
                self.assertEqual(piPet.clip_to_width(col, text, width=10), expected)


class CloudTest(unittest.TestCase):
    def clouds(self, cells):
        return [cell for cell in cells if cell[3] == 'cloud']

    def test_busier_cpu_means_more_clouds(self):
        for load, count in ((0.0, 1), (0.49, 1), (0.5, 2), (1.49, 2), (1.5, 3), (4.0, 3)):
            with self.subTest(load=load):
                self.assertEqual(piPet.cloud_count(load), count)

    def test_clouds_only_by_day(self):
        self.assertTrue(self.clouds(piPet.build_sky(80, 12.0, 100, load=3)))
        self.assertFalse(self.clouds(piPet.build_sky(80, 22.0, 100, load=3)))

    def test_clouds_drift_right_and_wrap_around(self):
        def right_edge(tick):
            # One cloud when idle; its bottom line's right edge stays correct even when the left edge is cut off
            edges = [col + len(text) for row, col, text, _ in piPet.build_clouds(80, 0.0, tick) if row == 1]
            return edges[0] if edges else None
        start = right_edge(0)
        self.assertEqual(right_edge(piPet.CLOUD_TICKS_PER_COLUMN), start + 1)
        self.assertEqual(right_edge(piPet.CLOUD_TICKS_PER_COLUMN - 1), start)
        edges = [edge for edge in (right_edge(t) for t in range(0, 2000, piPet.CLOUD_TICKS_PER_COLUMN)) if edge is not None]
        self.assertTrue(any(later < earlier for earlier, later in zip(edges, edges[1:])), 'should wrap back to the left')

    def test_sky_never_overlaps_or_spills_over_a_long_day(self):
        sun_hidden = False
        for width in (40, 80, 120):
            for load in (0.0, 1.0, 3.0):
                for hour in (6.1, 6.5, 9.0, 12.0, 15.0, 17.5, 17.9):  # includes dawn and dusk, with stars
                    for tick in range(0, 1200, 25):
                        cells = piPet.build_sky(width, hour, tick, load, holiday='pi_day' if tick % 2 else None)
                        seen = set()
                        for row, col, text, _ in cells:
                            with self.subTest(width=width, load=load, hour=hour, tick=tick, text=text):
                                self.assertTrue(0 <= row < piPet.SKY_HEIGHT and col >= 0)
                                for offset, char in enumerate(text):
                                    self.assertNotIn((row, col + offset), seen)
                                    seen.add((row, col + offset))
                        sun_hidden |= not any(color == 'sun' for *_, color in cells)
        self.assertTrue(sun_hidden, 'clouds should pass in front of the sun sometimes')

    def test_cloud_shapes_are_one_column_characters(self):
        for shape in piPet.CLOUD_SHAPES:
            for char in ''.join(shape):
                self.assertTrue(is_one_column(char))


class HolidayTest(unittest.TestCase):
    def layout(self, holiday, hour=21.0, tick=1, width=80, **readings):
        vitals = make_vitals(holiday=holiday, hour=hour, uptime_sec=4 * DAY, **readings)
        return piPet.layout(24, width, 'content', tick, 'hi', vitals)

    def test_dates(self):
        cases = [((10, 24), None), ((10, 25), 'halloween'), ((10, 31), 'halloween'), ((11, 1), None),
                 ((12, 19), None), ((12, 20), 'christmas'), ((12, 26), 'christmas'), ((12, 27), None),
                 ((12, 31), 'new_year'), ((1, 1), 'new_year'), ((1, 2), None), ((3, 14), 'pi_day'),
                 ((10, 8), 'birthday'), ((7, 15), None)]
        for (month, day), holiday in cases:
            with self.subTest(date=(month, day)):
                self.assertEqual(piPet.holiday_on(month, day), holiday)

    def test_new_art_is_one_column_and_lines_up(self):
        art = ([piPet.PARTY_HAT, piPet.SANTA_HAT, piPet.PUMPKIN_HAT, 'o'] + piPet.PUMPKIN + piPet.BAT_FRAMES
               + piPet.PI_DAY_CLOUDS[0]
               + [text for frame in piPet.FIREWORK_FRAMES for _, _, text in frame])
        for char in ''.join(art):
            self.assertTrue(is_one_column(char), f'{char!r} is not one column wide')
        for hat, _ in piPet.HOLIDAY_HATS.values():
            self.assertEqual(len(hat), piPet.PET_WIDTH)
        self.assertEqual(len({len(line) for line in piPet.PUMPKIN}), 1)
        self.assertEqual(len({len(line) for line in piPet.PI_DAY_CLOUDS[0]}), 1)

    def test_holiday_hats_replace_the_leaf(self):
        cases = [('birthday', piPet.PARTY_HAT, 'sun'), ('christmas', piPet.SANTA_HAT, 'berry'),
                 ('halloween', piPet.PUMPKIN_HAT, 'pumpkin')]
        for holiday, hat, color in cases:
            with self.subTest(holiday=holiday):
                self.assertEqual(piPet.build_pet('content', 1, holiday)[0], (hat, color))
        self.assertEqual(piPet.PARTY_HAT, '     /*\\     ', 'the birthday party hat stays as it is')
        for holiday in (None, 'new_year', 'pi_day'):
            with self.subTest(holiday=holiday):
                self.assertEqual(piPet.build_pet('content', 1, holiday)[0], ('    \\\\|//    ', 'leaf'))

    def test_birthday_lines_know_the_age(self):
        self.assertIn('I was born today!', piPet.holiday_sayings(make_vitals(holiday='birthday', year=2026)))
        self.assertIn("I'm 1 today!", piPet.holiday_sayings(make_vitals(holiday='birthday', year=2027)))
        self.assertEqual(piPet.holiday_sayings(make_vitals()), [])

    def test_jack_o_lantern_sits_on_the_grass_past_the_plant_and_flickers(self):
        cells = self.layout('halloween')
        ground_row = next(row for row, _, _, color in cells if color == 'grass')
        face = [(row, col, color) for row, col, text, color in cells if text == piPet.PUMPKIN[1]]
        pot_col = next(col for row, col, text, color in cells if color == 'pot')
        self.assertEqual(face[0][0], ground_row - 1)
        self.assertGreater(face[0][1], pot_col + 4)
        flames = {color for tick in TICKS for *_, text, color in self.layout('halloween', tick=tick) if text == piPet.PUMPKIN[1]}
        self.assertEqual(flames, {'pumpkin', 'sunset'})
        self.assertFalse([cell for cell in self.layout('halloween', width=40) if cell[2] == piPet.PUMPKIN[1]],
                         'no room at 40 columns')
        self.assertFalse([cell for cell in self.layout(None) if cell[2] == piPet.PUMPKIN[1]])

    def test_bats_fly_on_halloween_nights_only(self):
        def bats(holiday, hour):
            return [text for tick in TICKS for *_, text, color in piPet.build_sky(80, hour, tick, holiday=holiday)
                    if text in piPet.BAT_FRAMES]
        self.assertEqual(set(bats('halloween', 21.0)), set(piPet.BAT_FRAMES), 'they flap')
        self.assertEqual(bats('halloween', 12.0), [])
        self.assertEqual(bats(None, 21.0), [])

    def test_christmas_lights_string_along_the_grass(self):
        for width in (40, 80, 120):
            pieces = piPet.build_ground(width, 'christmas', tick=0)
            covered = ''.join(text for _, text, _ in sorted(pieces))
            with self.subTest(width=width):
                self.assertEqual(len(covered), width - 1, 'the pieces cover the line exactly once')
                bulbs = [col for col, text, _ in pieces if text == 'o']
                self.assertEqual(bulbs, list(range(piPet.LIGHT_SPACING // 2, width - 1, piPet.LIGHT_SPACING)))
        colors_now = [color for _, text, color in piPet.build_ground(80, 'christmas', tick=0) if text == 'o']
        colors_later = [color for _, text, color in piPet.build_ground(80, 'christmas', tick=4) if text == 'o']
        self.assertNotEqual(colors_now, colors_later, 'the colors chase along the string')
        self.assertEqual(piPet.build_ground(80), [(0, piPet.build_ground(80)[0][1], 'grass')])

    def test_fireworks_light_up_new_years_night(self):
        windows = 200
        with_a_firework = sum(
            any(piPet.build_fireworks(80, window * piPet.FIREWORK_WINDOW + t, seed=3)
                for t in range(piPet.FIREWORK_WINDOW))
            for window in range(windows))
        self.assertTrue(0.5 < with_a_firework / windows < 0.8, with_a_firework)
        burst_tick = next(t for t in range(5000) if len(piPet.build_fireworks(80, t, seed=3)) == 3)
        steps = [piPet.build_fireworks(80, t, seed=3) for t in range(burst_tick - 2, burst_tick + 2)]
        self.assertEqual([len(cells) for cells in steps], [1, 1, 3, 4], 'rocket, rocket, bang, sparks')
        self.assertEqual(len({color for cells in steps for *_, color in cells}), 1, 'one color per firework')
        for width in (40, 80, 120):
            for tick in range(0, 3000, 3):
                for row, col, text, _ in piPet.build_fireworks(width, tick, seed=width):
                    self.assertTrue(0 <= row < piPet.SKY_HEIGHT and col >= 0 and col + len(text) <= width - 1)
        night = {color for t in range(burst_tick - 2, burst_tick + 2)
                 for *_, color in piPet.build_sky(80, 23.5, t, seed=3, holiday='new_year')}
        self.assertTrue(night & set(piPet.FIREWORK_COLORS))
        day = [cell for t in range(burst_tick - 2, burst_tick + 2)
               for cell in piPet.build_sky(80, 12.0, t, seed=3, holiday='new_year') if cell[2] in ('-*-', "'")]
        self.assertEqual(day, [], 'fireworks are for the night')

    def test_pi_day_clouds_know_their_digits(self):
        sky = piPet.build_sky(80, 11.0, 100, load=1.0, holiday='pi_day')
        self.assertTrue(any('3.14159' in text for *_, text, _ in sky))

    def test_holiday_lines_in_calm_moods_only_on_the_day(self):
        rng = random.Random(6)
        halloween = {piPet.pick_saying('content', make_vitals(holiday='halloween'), rng) for _ in range(200)}
        self.assertTrue(halloween & set(piPet.HOLIDAY_SAYINGS['halloween']))
        dizzy = {piPet.pick_saying('dizzy', make_vitals(holiday='halloween'), rng) for _ in range(100)}
        self.assertTrue(dizzy <= set(piPet.SAYINGS['dizzy']))

    def test_help_mentions_the_holiday(self):
        for holiday, description in piPet.HOLIDAY_DESCRIPTIONS.items():
            lines = piPet.build_help(make_vitals(holiday=holiday), 'content')
            with self.subTest(holiday=holiday):
                self.assertIn(('Today', description, 'plain'), lines)
        self.assertFalse([line for line in piPet.build_help(make_vitals(), 'content') if line[0] == 'Today'])

    def test_every_holiday_has_lines_and_a_description(self):
        holidays = {piPet.holiday_on(month, day) for month in range(1, 13) for day in range(1, 32)} - {None}
        self.assertEqual(holidays, set(piPet.HOLIDAY_SAYINGS))
        self.assertEqual(holidays, set(piPet.HOLIDAY_DESCRIPTIONS))


SAMPLE_WIRELESS = """\
Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   70.  -22.  -256        0      0      0      5      0        0
"""


class WifiTest(unittest.TestCase):
    def test_reads_the_signal_for_one_interface(self):
        with fake_file(SAMPLE_WIRELESS):
            self.assertEqual(piPet.read_wifi_dbm('wlan0'), -22.0)
        with fake_file(SAMPLE_WIRELESS):
            self.assertIsNone(piPet.read_wifi_dbm('eth0'), 'wired interfaces have no signal')
        with fake_file(SAMPLE_WIRELESS.replace('-22.', '0.')):
            self.assertIsNone(piPet.read_wifi_dbm('wlan0'), 'a driver without a dBm reading')
        with mock.patch('builtins.open', side_effect=OSError):
            self.assertIsNone(piPet.read_wifi_dbm('wlan0'))

    def test_bars_like_a_phone(self):
        cases = [(-22, 4, '||||'), (-55, 4, '||||'), (-56, 3, '|||.'), (-70, 2, '||..'), (-80, 1, '|...'), (-90, 0, '....')]
        for dbm, bars, text in cases:
            with self.subTest(dbm=dbm):
                self.assertEqual(piPet.wifi_bars(dbm), bars)
                self.assertEqual(piPet.format_wifi_bars(dbm), text)
        self.assertEqual(piPet.format_wifi_bars(None), '')

    def test_status_line_shows_the_bars(self):
        self.assertIn('wlan0 |||. 0.0 KB/s', piPet.build_status(make_vitals(wifi_dbm=-60)))
        self.assertIn('wlan0 0.0 KB/s', piPet.build_status(make_vitals()), 'no bars without a Wi-Fi reading')

    def test_weak_signal_brings_an_alert_and_some_fretting(self):
        weak = make_vitals(wifi_dbm=-80)
        self.assertIn('weak Wi-Fi (-80 dBm)', piPet.build_alerts(weak))
        self.assertNotIn('Wi-Fi', piPet.build_alerts(make_vitals(wifi_dbm=-70)))
        rng = random.Random(7)
        said = {piPet.pick_saying('content', weak, rng) for _ in range(200)}
        self.assertTrue(said & set(piPet.WEAK_WIFI_SAYINGS))

    def test_help_describes_the_network(self):
        self.assertEqual(piPet.describe_network(make_vitals(wifi_dbm=-22, net_rate=1024)),
                         'wlan0: 1.0 KB/s, signal -22 dBm (excellent)')
        self.assertIn('no route', piPet.describe_network(make_vitals(online=False)))
        labels = [label for label, _, _ in piPet.build_help(make_vitals(), 'content')]
        self.assertIn('Network', labels)


class ClockTest(unittest.TestCase):
    def test_format_clock(self):
        cases = [((2026, 10, 15, 0, 5), 'Thu Oct 15  12:05 AM'), ((2026, 10, 15, 9, 7), 'Thu Oct 15  9:07 AM'),
                 ((2026, 10, 15, 12, 0), 'Thu Oct 15  12:00 PM'), ((2026, 12, 31, 23, 59), 'Thu Dec 31  11:59 PM')]
        for parts, text in cases:
            with self.subTest(text=text):
                self.assertEqual(piPet.format_clock(datetime.datetime(*parts)), text)

    def clock_cells(self, width, show_hint=True):
        cells = piPet.layout(24, width, 'content', 1, 'hi', make_vitals(), show_hint=show_hint)
        return [(row, col, text) for row, col, text, _ in cells if text == 'Thu Oct 15  8:42 PM']

    def test_clock_in_the_bottom_right_corner(self):
        [(row, col, text)] = self.clock_cells(80)
        self.assertEqual(row, 24 - 2)
        self.assertEqual(col + len(text), 80 - 2)

    def test_no_clock_without_room_or_in_a_snapshot(self):
        self.assertEqual(self.clock_cells(40), [], 'no room beside the key hint')
        self.assertEqual(self.clock_cells(80, show_hint=False), [], '--once has no bottom bar')


class WaterTest(unittest.TestCase):
    def make_state(self, **readings):
        vitals = make_vitals(**readings)
        vitals.refresh = lambda: None
        return piPet.LiveState(vitals, rng=random.Random(0), memories=dict(piPet.DEFAULT_MEMORIES))

    def watering_cells(self, uptime_sec=8 * DAY, width=80, tick=1):
        cells = piPet.layout(24, width, 'content', tick, 'hi', make_vitals(uptime_sec=uptime_sec), watering=True)
        return cells, [cell for cell in cells if cell[2] in piPet.WATERING_CAN or (cell[2] == "'" and cell[3] == 'extra')]

    def test_watering_art_lines_up(self):
        self.assertEqual(len({len(line) for line in piPet.WATERING_CAN}), 1)
        for char in ''.join(piPet.WATERING_CAN):
            self.assertTrue(is_one_column(char))

    def test_w_waters_for_a_few_seconds_and_the_plant_says_thanks(self):
        state = self.make_state()
        state.press(ord('w'), now=10.0)
        self.assertTrue(state.watering(10.0))
        self.assertEqual(state.saying, piPet.WATER_SAYING)
        state.step(1, now=10.0 + piPet.WATER_SECONDS - 0.1)
        self.assertEqual(state.saying, piPet.WATER_SAYING)
        self.assertFalse(state.watering(10.0 + piPet.WATER_SECONDS))
        state.step(2, now=10.0 + piPet.WATER_SECONDS)
        self.assertIn(state.saying, piPet.SAYINGS['content'], 'back to its own lines when the can is put away')

    def test_each_watering_counts_once(self):
        state = self.make_state()
        state.press(ord('w'), now=10.0)
        state.press(ord('w'), now=11.0)  # still pouring
        state.press(ord('w'), now=20.0)  # a new watering
        self.assertEqual(state.unsaved, {'times_watered': 2})

    def test_petting_keeps_its_line_while_watering(self):
        state = self.make_state()
        state.press(ord('p'), now=10.0)
        state.step(1, now=10.0)
        state.press(ord('w'), now=10.5)
        self.assertIn(state.saying, piPet.SAYINGS['happy'])

    def test_the_can_pours_onto_the_plant(self):
        for uptime_sec in (0.5 * DAY, 2 * DAY, 4 * DAY, 8 * DAY):
            cells, watering = self.watering_cells(uptime_sec)
            pot = next((row, col) for row, col, _, color in cells if color == 'pot')
            plant_top = min(row for row, col, text, color in cells
                            if color in ('leaf', 'berry', 'pot') and col >= pot[1] and len(text) == 5)
            spout = next((row, col) for row, col, text, _ in watering if text == piPet.WATERING_CAN[1])
            # A tall plant leaves room for one drop row, which drips every other frame, so look at a few frames
            drops = [(row, col) for tick in range(1, 5) for row, col, text, _ in self.watering_cells(uptime_sec, tick=tick)[1]
                     if text == "'"]
            with self.subTest(days=uptime_sec / DAY):
                self.assertEqual(spout[1], pot[1] + 2, 'the spout is over the middle of the plant')
                self.assertTrue(drops)
                self.assertTrue(all(col == spout[1] and spout[0] < row < plant_top for row, col in drops))

    def test_no_can_without_a_plant_or_room(self):
        self.assertEqual(self.watering_cells(uptime_sec=None)[1], [])
        self.assertEqual(self.watering_cells(width=40)[1], [])
        cells = piPet.layout(24, 80, 'content', 1, 'hi', make_vitals(uptime_sec=8 * DAY))
        self.assertFalse([cell for cell in cells if cell[2] in piPet.WATERING_CAN])

    def test_key_hint_fits_the_window(self):
        for width, hint in ((80, 'p to pet  ·  w to water  ·  ? for help  ·  q to quit'),
                            (40, 'p pet · w water · ? help · q quit'), (piPet.MIN_WIDTH, 'p pet · ? help · q quit')):
            cells = piPet.layout(24, width, 'content', 1, 'hi', make_vitals())
            with self.subTest(width=width):
                self.assertIn(hint, [text for *_, text, _ in cells])


class OnceTest(unittest.TestCase):
    def test_render_lines_places_text_and_trims_blank_space(self):
        cells = [(1, 2, 'hi', 'plain'), (3, 0, 'there', 'leaf')]
        self.assertEqual(piPet.render_lines(cells, height=6, width=10), ['  hi', '', 'there'])

    def test_render_lines_colors_only_when_asked(self):
        cells = [(0, 0, 'ab', 'leaf'), (0, 3, 'c', 'plain')]
        self.assertEqual(piPet.render_lines(cells, 1, 10), ['ab c'])
        self.assertEqual(piPet.render_lines(cells, 1, 10, use_color=True), ['\x1b[0m\x1b[1;32mab \x1b[0mc'])

    def test_every_layout_color_has_an_ansi_code(self):
        colors = set()
        for mood in piPet.FACES:
            for hour, running in ((12.0, True), (22.0, False)):
                vitals = make_vitals(hour=hour, claude_here=True, adguard_running=running, uptime_sec=8 * DAY, online=False,
                                     holiday=HOLIDAYS[hash(mood) % len(HOLIDAYS)])
                colors |= {color for *_, color in piPet.layout(24, 80, mood, 1, 'hi', vitals)}
        self.assertLessEqual(colors, set(piPet.ANSI_COLORS))

    def test_no_key_hint_when_asked(self):
        texts = [text for _, _, text, _ in piPet.layout(24, 80, 'content', 1, 'hi', make_vitals(), show_hint=False)]
        self.assertFalse(any('q to quit' in text for text in texts))

    def test_print_once_shows_the_whole_scene(self):
        vitals = make_vitals(hour=12.0, claude_here=True, adguard_running=True, uptime_sec=8 * DAY)
        vitals.refresh = lambda: None
        with mock.patch.object(piPet, 'Vitals', return_value=vitals), \
                mock.patch.object(piPet.time, 'sleep'), \
                mock.patch.object(piPet.shutil, 'get_terminal_size', return_value=os.terminal_size((80, 24))), \
                mock.patch.object(sys, 'stdout', new_callable=io.StringIO) as stdout:
            piPet.print_once()
        output = stdout.getvalue()
        for piece in ('-O-', '\\\\|//', '-^‿^-', '(o_o)', '(@)', "_.,_'", 'temp ', 'mood: content'):
            self.assertIn(piece, output)
        self.assertNotIn('q to quit', output)
        self.assertNotIn('\x1b[', output, 'no colors when the output is not a terminal')


SAMPLE_TCP = """\
  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
   0: 0100007F:0C45 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 1 1
   1: 3C44A8C0:0016 3F44A8C0:D431 01 00000000:00000000 00:00000000 00000000     0        0 2 1
"""


class DoctorTest(unittest.TestCase):
    def pal_colors(self, cells):
        return [color for _, _, text, color in cells if len(text) == 5 and color in ('guard', 'doctor', 'claude')]

    def doctor_faces(self, mood='content', ticks=TICKS):
        faces = set()
        for tick in ticks:
            cells = piPet.layout(24, 80, mood, tick, 'hi', make_vitals(pi_vitals_running=True))
            faces |= {text for _, _, text, color in cells if color == 'doctor' and text.startswith('(')}
        return faces

    def test_finds_a_listening_port_without_connecting(self):
        with fake_file(SAMPLE_TCP):
            self.assertTrue(piPet.is_port_listening(3141))
            self.assertFalse(piPet.is_port_listening(22), 'an open connection on 22 is not a listening socket')
        with mock.patch('builtins.open', side_effect=OSError):
            self.assertFalse(piPet.is_port_listening(3141))

    def test_doctor_frames_line_up(self):
        for frame in piPet.DOCTOR_CHECKING + [piPet.DOCTOR_WORRIED]:
            self.assertEqual(len({len(line) for line in frame}), 1)
            for char in ''.join(frame):
                self.assertTrue(is_one_column(char), f'{char!r} is not one column wide')

    def test_doctor_visits_only_while_pi_vitals_runs(self):
        with_doctor = piPet.layout(24, 80, 'content', 1, 'hi', make_vitals(pi_vitals_running=True))
        without = piPet.layout(24, 80, 'content', 1, 'hi', make_vitals())
        self.assertIn('doctor', self.pal_colors(with_doctor))
        self.assertNotIn('doctor', self.pal_colors(without))

    def test_doctor_checks_and_takes_notes(self):
        self.assertEqual(self.doctor_faces(), {'(o-o)', '(.-.)'})

    def test_doctor_worries_about_urgent_moods(self):
        for mood in piPet.URGENT_MOODS:
            with self.subTest(mood=mood):
                self.assertEqual(self.doctor_faces(mood), {'(O-O)'})
        self.assertNotIn('(O-O)', self.doctor_faces('sleepy'))

    def test_pals_line_up_by_how_permanent_they_are(self):
        vitals = make_vitals(adguard_running=True, pi_vitals_running=True, claude_here=True)
        cells = piPet.layout(24, 80, 'content', 1, 'hi', vitals)
        order = sorted((col, color) for _, col, text, color in cells if color in ('guard', 'doctor', 'claude') and len(text) == 5)
        nearest_first = list(dict.fromkeys(color for _, color in reversed(order)))
        self.assertEqual(nearest_first, ['guard', 'doctor', 'claude'])

    def test_pet_mentions_the_doctor_only_during_a_visit(self):
        rng = random.Random(4)
        visiting = {piPet.pick_saying('content', make_vitals(pi_vitals_running=True), rng) for _ in range(200)}
        away = {piPet.pick_saying('content', make_vitals(), rng) for _ in range(200)}
        self.assertTrue(visiting & set(piPet.DOCTOR_SAYINGS))
        self.assertFalse(away & set(piPet.DOCTOR_SAYINGS))


SAMPLE_UDP6 = """\
  sl  local_address                         remote_address                        st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode ref pointer drops
 2104: 00000000000000000000000000000000:0035 00000000000000000000000000000000:0000 07 00000000:00000000 00:00000000 00000000     0        0 1 2 0
 2105: 0000000000000000FFFF00003C44A8C0:D2F1 0000000000000000FFFF00000144A8C0:0035 01 00000000:00000000 00:00000000 00000000  1000        0 2 2 0
"""


class DnsGuardTest(unittest.TestCase):
    def test_finds_a_bound_dns_socket_without_sending_anything(self):
        with fake_file(SAMPLE_UDP6):
            self.assertTrue(piPet.is_udp_port_bound(53))
        connected_only = '\n'.join(line for line in SAMPLE_UDP6.splitlines() if ' 2104:' not in line)
        with fake_file(connected_only):
            self.assertFalse(piPet.is_udp_port_bound(53), 'a query sent to port 53 is not a server on port 53')
        with mock.patch('builtins.open', side_effect=OSError):
            self.assertFalse(piPet.is_udp_port_bound(53))

    def test_alarmed_frames_line_up(self):
        for frame in piPet.GUARD_ALARMED:
            self.assertEqual(len({len(line) for line in frame}), 1)
            for char in ''.join(frame):
                self.assertTrue(is_one_column(char))

    def guard_faces(self, **readings):
        faces = set()
        for tick in TICKS:
            cells = piPet.layout(24, 80, 'content', tick, 'hi', make_vitals(**readings))
            faces |= {(text, color) for _, _, text, color in cells if text.startswith('(') and text.endswith(')') and len(text) == 5}
        return faces

    def test_guard_raises_the_alarm_when_dns_stops_being_served(self):
        faces = self.guard_faces(adguard_running=True, dns_answering=False)
        self.assertEqual(faces, {('(O_O)', 'alert')})
        self.assertEqual(piPet.build_alerts(make_vitals(adguard_running=True, dns_answering=False)), 'DNS port 53 closed')

    def test_no_dns_alarm_unless_adguard_runs_and_the_port_is_closed(self):
        for readings in (dict(adguard_running=True, dns_answering=True), dict(adguard_running=True, dns_answering=None),
                         dict(adguard_running=None), dict(adguard_running=False, dns_answering=False)):
            with self.subTest(readings=readings):
                self.assertNotIn('DNS', piPet.build_alerts(make_vitals(**readings)))
                self.assertNotIn(('(O_O)', 'alert'), self.guard_faces(**readings))

    def test_no_bragging_about_the_guard_while_dns_is_down(self):
        rng = random.Random(5)
        said = {piPet.pick_saying('content', make_vitals(adguard_running=True, dns_answering=False), rng) for _ in range(200)}
        self.assertFalse(said & set(piPet.GUARD_SAYINGS))


class LiveStateTest(unittest.TestCase):
    """The live view's timing rules, driven with a fake clock and scripted readings."""

    def make_state(self, **readings):
        vitals = make_vitals(**readings)
        vitals.refresh = lambda: None  # readings change only when a test changes them
        return piPet.LiveState(vitals, rng=random.Random(0))

    def counting_sayings(self):
        """Patch pick_saying so every new pick is visibly different: 'saying 1', 'saying 2', ..."""
        count = iter(range(1, 1000))
        return mock.patch.object(piPet, 'pick_saying', side_effect=lambda *args: f'saying {next(count)}')

    def test_opening_line(self):
        self.assertEqual(self.make_state(claude_here=True).saying, piPet.CLAUDE_GREETING)
        self.assertIn(self.make_state().saying, piPet.SAYINGS['content'])

    def test_quit_keys(self):
        state = self.make_state()
        for key in (ord('q'), ord('Q'), 27):
            self.assertFalse(state.press(key, now=0))
        for key in (-1, ord('x'), ord('p')):
            self.assertTrue(state.press(key, now=0))

    def test_new_saying_every_ten_seconds_but_not_at_the_start(self):
        with self.counting_sayings():
            state = self.make_state()
            opening = state.saying
            for tick in range(piPet.FPS * 10):
                state.step(tick, now=tick / piPet.FPS)
                self.assertEqual(state.saying, opening, f'tick {tick}')
            state.step(piPet.FPS * 10, now=10.0)
            self.assertNotEqual(state.saying, opening)

    def test_mood_change_brings_a_new_saying(self):
        with self.counting_sayings():
            state = self.make_state()
            state.step(0, now=0)
            state.vitals.load = 3.0  # busy now
            state.step(1, now=0.25)
            self.assertEqual(state.mood, 'content', 'sensors are only read every 2 seconds')
            before = state.saying
            state.step(piPet.FPS * 2, now=2.0)
            self.assertEqual(state.mood, 'busy')
            self.assertNotEqual(state.saying, before)

    def test_petting(self):
        state = self.make_state()
        state.step(0, now=99.0)
        state.press(ord('p'), now=100.0)
        self.assertIn(state.saying, piPet.SAYINGS['happy'])
        self.assertEqual(state.step(1, now=100.0), 'happy')
        self.assertEqual(state.step(2, now=100.0 + piPet.PET_SECONDS - 0.01), 'happy')
        self.assertEqual(state.step(3, now=100.0 + piPet.PET_SECONDS), 'content')
        self.assertIn(state.saying, piPet.SAYINGS['content'], 'back to its real mood')

    def test_petting_again_keeps_it_happy_longer_without_changing_the_line(self):
        state = self.make_state()
        state.press(ord('p'), now=100.0)
        state.step(1, now=100.0)
        line = state.saying
        state.press(ord('p'), now=102.0)
        self.assertEqual(state.saying, line)
        self.assertEqual(state.step(2, now=104.0), 'happy')
        self.assertEqual(state.step(3, now=102.0 + piPet.PET_SECONDS), 'content')

    def test_claude_arriving_gets_a_greeting_unless_the_pet_is_busy_being_petted(self):
        state = self.make_state()
        state.step(0, now=0)
        state.vitals.claude_here = True
        state.step(piPet.FPS * 2, now=2.0)
        self.assertEqual(state.saying, piPet.CLAUDE_GREETING)

        petted = self.make_state()
        petted.step(0, now=0)
        petted.press(ord('p'), now=1.0)
        petted.vitals.claude_here = True
        petted.step(piPet.FPS * 2, now=2.0)
        self.assertIn(petted.saying, piPet.SAYINGS['happy'])


class WishTest(unittest.TestCase):
    STREAK = [(0, 40, '--*', 'shooting_star')]
    NIGHT = [(1, 10, '.', 'plain')]

    def make_state(self):
        vitals = make_vitals()
        vitals.refresh = lambda: None
        return piPet.LiveState(vitals, rng=random.Random(0))

    def test_a_shooting_star_brings_a_wish_for_a_few_seconds(self):
        state = self.make_state()
        state.step(1, now=10.0)
        state.notice(self.STREAK, now=10.0)
        self.assertEqual(state.saying, piPet.WISH_SAYING)
        state.step(2, now=10.0 + piPet.WISH_SECONDS - 0.1)
        self.assertEqual(state.saying, piPet.WISH_SAYING)
        state.step(3, now=10.0 + piPet.WISH_SECONDS)
        self.assertIn(state.saying, piPet.SAYINGS['content'])

    def test_one_wish_per_streak(self):
        state = self.make_state()
        state.notice(self.STREAK, now=10.0)
        state.step(1, now=10.0 + piPet.WISH_SECONDS)  # the wish is over...
        state.notice(self.STREAK, now=10.0 + piPet.WISH_SECONDS)  # ...but it's the same streak
        self.assertNotEqual(state.saying, piPet.WISH_SAYING)
        state.notice(self.NIGHT, now=20.0)
        state.notice(self.STREAK, now=30.0)  # a new streak
        self.assertEqual(state.saying, piPet.WISH_SAYING)

    def test_the_wish_survives_the_saying_rotation(self):
        state = self.make_state()
        state.notice(self.STREAK, now=9.0)
        state.step(piPet.FPS * 10, now=10.0)  # time for a new saying, but the pet is wishing
        self.assertEqual(state.saying, piPet.WISH_SAYING)

    def test_a_real_night_sky_brings_the_wish(self):
        vitals = make_vitals(hour=22.0, sky_seed=1)
        vitals.refresh = lambda: None
        state = piPet.LiveState(vitals, rng=random.Random(0))
        streak_tick = next(tick for tick in range(10_000) if piPet.build_shooting_star(80, tick, seed=1))
        for tick in range(streak_tick - 2, streak_tick + 1):
            mood = state.step(tick, now=tick / piPet.FPS)
            state.notice(piPet.layout(24, 80, mood, tick, state.saying, vitals), now=tick / piPet.FPS)
        self.assertEqual(state.saying, piPet.WISH_SAYING)

    def test_no_wish_while_being_petted(self):
        state = self.make_state()
        state.press(ord('p'), now=10.0)
        state.step(1, now=10.0)
        state.notice(self.STREAK, now=10.5)
        self.assertIn(state.saying, piPet.SAYINGS['happy'])


class MemoriesTest(unittest.TestCase):
    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = temp_dir.name
        self.path = os.path.join(self.dir, 'pi-pet', 'memories.json')

    def write(self, content):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(content if isinstance(content, str) else json.dumps(content))

    def make_state(self, memories, wall_time=1_000_000.0, **readings):
        vitals = make_vitals(**readings)
        vitals.refresh = lambda: None
        return piPet.LiveState(vitals, rng=random.Random(0), memories=memories, wall_time=wall_time)

    def test_lives_in_the_xdg_state_folder(self):
        with mock.patch.dict(os.environ, {'XDG_STATE_HOME': '/somewhere'}):
            self.assertEqual(piPet.memories_path(), '/somewhere/pi-pet/memories.json')
        with mock.patch.dict(os.environ, {'HOME': '/home/someone'}, clear=True):
            self.assertEqual(piPet.memories_path(), '/home/someone/.local/state/pi-pet/memories.json')

    def test_missing_or_damaged_file_means_starting_fresh(self):
        self.assertEqual(piPet.load_memories(self.path), piPet.DEFAULT_MEMORIES)
        for content in ('{not json', '[1, 2, 3]', '"hello"', ''):
            with self.subTest(content=content):
                self.write(content)
                self.assertEqual(piPet.load_memories(self.path), piPet.DEFAULT_MEMORIES)

    def test_damaged_entries_are_reset_one_by_one(self):
        self.write({'first_met': 'last tuesday', 'visits': -3, 'times_petted': True, 'shooting_stars_seen': 4,
                    'last_seen': 'yesterday'})
        memories = piPet.load_memories(self.path)
        self.assertEqual(memories, {**piPet.DEFAULT_MEMORIES, 'shooting_stars_seen': 4})

    def test_save_and_load(self):
        memories = {'first_met': '2026-10-12', 'visits': 3, 'times_petted': 7, 'times_watered': 2, 'shooting_stars_seen': 1,
                    'last_seen': 123.5}
        self.assertTrue(piPet.save_memories(self.path, memories))
        self.assertEqual(piPet.load_memories(self.path), memories)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ['memories.json'], 'no temporary file left behind')

    def test_an_unwritable_place_is_not_a_crash(self):
        blocker = os.path.join(self.dir, 'a-file')
        with open(blocker, 'w'):
            pass
        self.assertFalse(piPet.save_memories(os.path.join(blocker, 'memories.json'), dict(piPet.DEFAULT_MEMORIES)))

    def test_two_pets_at_once_add_up_instead_of_overwriting(self):
        piPet.update_memories(self.path, add={'visits': 1}, first_met='2026-10-12')
        piPet.update_memories(self.path, add={'visits': 1}, first_met='2026-10-13')  # a second window
        piPet.update_memories(self.path, add={'times_petted': 2})
        saved = piPet.update_memories(self.path, add={'times_petted': 3}, last_seen=99.0)
        self.assertEqual((saved['visits'], saved['times_petted'], saved['last_seen']), (2, 5, 99.0))
        self.assertEqual(saved['first_met'], '2026-10-12', 'the first meeting is never overwritten')

    def test_missed_you_after_a_day_away(self):
        away = lambda seconds: {**piPet.DEFAULT_MEMORIES, 'last_seen': 1_000_000.0 - seconds}
        self.assertEqual(self.make_state(away(piPet.MISSED_YOU_SECONDS)).saying, piPet.MISSED_YOU_SAYING)
        self.assertEqual(self.make_state(away(piPet.MISSED_YOU_SECONDS), claude_here=True).saying,
                         piPet.MISSED_YOU_SAYING, 'missing you comes before greeting Claude')
        self.assertNotEqual(self.make_state(away(3600)).saying, piPet.MISSED_YOU_SAYING)
        self.assertNotEqual(self.make_state(dict(piPet.DEFAULT_MEMORIES)).saying, piPet.MISSED_YOU_SAYING)

    def test_counts_each_petting_once_and_each_shooting_star(self):
        state = self.make_state(dict(piPet.DEFAULT_MEMORIES))
        state.press(ord('p'), now=10.0)
        state.press(ord('p'), now=10.0)  # two presses in one frame
        state.step(1, now=10.0)
        state.press(ord('p'), now=11.0)  # still happy: the same petting
        state.step(2, now=11.0 + piPet.PET_SECONDS)  # happy is over
        state.press(ord('p'), now=20.0)  # a new petting
        state.notice([(0, 40, '--*', 'shooting_star')], now=30.0)
        self.assertEqual(state.unsaved, {'times_petted': 2, 'shooting_stars_seen': 1})
        self.assertEqual(state.memories['times_petted'], 2)

    def test_not_remembering_is_fine(self):
        state = self.make_state(None)
        state.press(ord('p'), now=10.0)
        state.notice([(0, 40, '--*', 'shooting_star')], now=30.0)
        self.assertEqual(state.unsaved, {})

    def test_the_help_screen_tells_your_story(self):
        memories = {'first_met': '2026-10-08', 'visits': 1, 'times_petted': 12, 'times_watered': 0, 'shooting_stars_seen': 3,
                    'last_seen': None}
        line = piPet.describe_memories(memories, today=datetime.date(2026, 10, 12))
        self.assertEqual(line, 'met Oct 8, 2026 (4 days ago)  ·  1 visit  ·  petted 12 times  ·  3 shooting stars seen')
        watered = piPet.describe_memories({**memories, 'times_watered': 4}, today=datetime.date(2026, 10, 12))
        self.assertIn('plant watered 4 times', watered)
        self.assertIn('(today)', piPet.describe_memories({**memories, 'first_met': '2026-10-12'}, datetime.date(2026, 10, 12)))
        self.assertIn('(yesterday)', piPet.describe_memories({**memories, 'first_met': '2026-10-11'}, datetime.date(2026, 10, 12)))
        cells = piPet.layout(24, 80, 'content', 1, 'hi', make_vitals(), show_help=True, memories=memories)
        self.assertTrue(any(text == 'Together' for *_, text, _ in cells))
        no_memories = piPet.layout(24, 80, 'content', 1, 'hi', make_vitals(), show_help=True)
        self.assertFalse(any(text == 'Together' for *_, text, _ in no_memories))


class HelpTest(unittest.TestCase):
    def help_rows(self, height=24, width=80, mood='content', **readings):
        cells = piPet.layout(height, width, mood, 1, 'hi', make_vitals(**readings), show_help=True)
        rows = {}
        for row, col, text, color in sorted(cells):
            rows.setdefault(row, []).append((col, text, color))
        return cells, {row: ' '.join(text for _, text, _ in parts) for row, parts in rows.items()}

    def test_format_duration(self):
        cases = [(45, '45 s'), (12 * 60, '12 min'), (3 * 3600 + 5 * 60, '3 h 5 min'), (86400 + 4 * 3600, '1 day 4 h'),
                 (2 * 86400 + 3600, '2 days 1 h')]
        for seconds, text in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(piPet.format_duration(seconds), text)

    def test_moon_phase_names_follow_the_shapes(self):
        cases = [(0.2, 'new moon'), (2.5, 'waxing crescent'), (7.4, 'first quarter'), (14.8, 'full moon'),
                 (22.1, 'last quarter'), (26.5, 'waning crescent')]
        for age, name in cases:
            with self.subTest(age=age):
                self.assertEqual(piPet.MOON_PHASE_NAMES[piPet.moon_eighth(age)], name)

    def test_every_mood_is_explained_with_its_reading(self):
        cases = [
            ('dizzy', dict(undervoltage=True), 'undervoltage'),
            ('hot', dict(temp_c=73.8), '73.8°C'),
            ('stuffed', dict(disk_used_percent=93.2), '93% full'),
            ('hungry', dict(memory_free_percent=6.8), '7% of memory'),
            ('groggy', dict(uptime_sec=150), '2 min ago'),
            ('lonely', dict(online=False), 'no route'),
            ('busy', dict(load=2.6), 'load 2.60'),
            ('surfing', dict(net_rate=420 * 1024), '420.0 KB/s on wlan0'),
            ('sleepy', dict(load=0.1, net_rate=512), 'load 0.10'),
            ('content', dict(), 'normal'),
            ('happy', dict(), 'petted'),
        ]
        for mood, readings, expected in cases:
            with self.subTest(mood=mood):
                self.assertIn(expected, piPet.explain_mood(make_vitals(**readings), mood))
        self.assertEqual(piPet.explain_mood(make_vitals(forced_mood='hot'), 'hot'), 'forced with --mood')
        self.assertEqual(piPet.explain_mood(make_vitals(forced_mood='happy'), 'happy'), 'forced with --mood')
        self.assertEqual(piPet.explain_mood(make_vitals(forced_mood='hot'), 'happy'), 'you just petted me')

    def test_sky_is_described_for_the_time_of_day(self):
        cases = [(10.0, 'day: the sun'), (6.5, 'dawn'), (17.5, 'dusk'), (22.0, 'night: full moon')]
        for hour, expected in cases:
            with self.subTest(hour=hour):
                self.assertIn(expected, piPet.describe_sky(make_vitals(hour=hour, load=0.6)))
        self.assertIn('2 clouds (load 0.60', piPet.describe_sky(make_vitals(hour=10.0, load=0.6)))

    def test_pals_are_explained(self):
        everyone = piPet.describe_pals(make_vitals(adguard_running=True, pi_vitals_running=True, claude_here=True), 'hot')
        self.assertEqual([text.split()[0] for text, _ in everyone], ['guard', 'doctor', 'Claude'])
        self.assertIn('worried', everyone[1][0])
        alarmed = piPet.describe_pals(make_vitals(adguard_running=True, dns_answering=False), 'content')
        self.assertEqual(alarmed[0][1], 'alert')
        self.assertIn('port 53', alarmed[0][0])
        self.assertIn('stopped', piPet.describe_pals(make_vitals(adguard_running=False), 'content')[0][0])
        self.assertEqual(piPet.describe_pals(make_vitals(), 'content'), [('nobody is visiting right now', 'plain')])

    def test_plant_is_explained(self):
        self.assertIn('seedling, up 2 days 0 h (leafy at 3 days)', piPet.describe_plant(make_vitals(uptime_sec=2 * DAY)))
        self.assertIn('in flower', piPet.describe_plant(make_vitals(uptime_sec=8 * DAY)))
        self.assertIn('repots', piPet.describe_plant(make_vitals(uptime_sec=8 * DAY)))
        self.assertIn('no uptime', piPet.describe_plant(make_vitals(uptime_sec=None)))

    def test_help_screen_has_every_section_and_stays_inside(self):
        for height, width in ((24, 80), (14, 40), (11, 24)):
            cells, rows = self.help_rows(height, width, adguard_running=True, pi_vitals_running=True, claude_here=True)
            for row, col, text, _ in cells:
                with self.subTest(size=(height, width), text=text):
                    self.assertTrue(0 <= row < height - 1 and col >= 0 and col + len(text) <= width - 1)
        _, rows = self.help_rows(adguard_running=True, pi_vitals_running=True, claude_here=True)
        labels = [text.split()[0] for row, text in sorted(rows.items())]
        for label in ('Mood', 'Sky', 'Pals', 'Plant', 'Alerts', 'Keys'):
            self.assertIn(label, labels)

    def test_keys_fit_on_one_row_at_80_columns(self):
        keys = next(text for label, text, _ in piPet.build_help(make_vitals(), 'content') if label == 'Keys')
        self.assertLessEqual(len(keys), 80 - 13, 'the help text column starts at 12 and stops before the last column')

    def test_alerts_show_in_red(self):
        cells, _ = self.help_rows(online=False)
        self.assertIn(('offline', 'alert'), [(text, color) for *_, text, color in cells])

    def test_help_keys_toggle_the_screen(self):
        vitals = make_vitals()
        vitals.refresh = lambda: None
        state = piPet.LiveState(vitals, rng=random.Random(0))
        self.assertFalse(state.show_help)
        state.press(ord('?'), now=0)
        self.assertTrue(state.show_help)
        state.press(ord('h'), now=0)
        self.assertFalse(state.show_help)

    def test_short_key_hint_in_a_narrow_window(self):
        cells = piPet.layout(piPet.MIN_HEIGHT + 4, piPet.MIN_WIDTH, 'content', 1, 'hi', make_vitals())
        self.assertIn('p pet · ? help · q quit', [text for *_, text, _ in cells])


class CheerTest(unittest.TestCase):
    def pal_faces(self, mood, tick=1, **readings):
        cells = piPet.layout(24, 80, mood, tick, 'hi', make_vitals(**readings))
        return {color: text for *_, text, color in cells
                if color in ('guard', 'doctor', 'alert', 'dim') and text.startswith('(') and len(text) == 5}

    def test_cheering_art_lines_up(self):
        for frame in (piPet.GUARD_CHEERING, piPet.DOCTOR_CHEERING):
            self.assertEqual(len({len(line) for line in frame}), 1)
            for char in ''.join(frame):
                self.assertTrue(is_one_column(char))

    def test_pals_cheer_while_the_pet_is_petted(self):
        everyone = dict(adguard_running=True, pi_vitals_running=True)
        self.assertEqual(self.pal_faces('happy', **everyone), {'guard': '(^_^)', 'doctor': '(^-^)'})
        self.assertNotIn('(^_^)', self.pal_faces('content', **everyone).values())
        self.assertNotIn('(^-^)', self.pal_faces('content', **everyone).values())

    def test_a_dns_problem_or_a_nap_is_not_interrupted(self):
        self.assertEqual(self.pal_faces('happy', adguard_running=True, dns_answering=False), {'alert': '(O_O)'})
        self.assertEqual(self.pal_faces('happy', adguard_running=False), {'dim': '(-_-)'})

    def test_claude_twinkles_faster_when_cheering(self):
        def twinkles(mood):
            frames = [tuple(text for *_, text, color in piPet.layout(24, 80, mood, tick, 'hi', make_vitals(claude_here=True))
                            if color == 'claude') for tick in range(4)]
            return len({frames[0], frames[2]})
        self.assertEqual(twinkles('happy'), 2)
        self.assertEqual(twinkles('content'), 1)


class AlertTest(unittest.TestCase):
    def test_healthy_pi_has_no_alerts(self):
        self.assertEqual(piPet.build_alerts(make_vitals()), '')

    def test_every_alert(self):
        vitals = make_vitals(undervoltage=True, disk_used_percent=93.4, memory_free_percent=7.2, online=False)
        self.assertEqual(piPet.build_alerts(vitals), 'power low  ·  disk 93% full  ·  memory 7% free  ·  offline')

    def test_alert_line_shows_under_the_status_line(self):
        cells = piPet.layout(24, 80, 'lonely', 1, 'hello? anyone?', make_vitals(online=False))
        rows = {text: row for row, _, text, _ in cells}
        status_row = next(row for text, row in rows.items() if text.startswith('temp '))
        self.assertEqual(rows['offline'], status_row + 1)
        self.assertIn('p to pet  ·  w to water  ·  ? for help  ·  q to quit', rows)

    def test_alert_line_gives_way_in_a_short_window(self):
        # The surfing pet is the tallest (waves); --mood surfing on an offline Pi still has an alert
        cells = piPet.layout(piPet.MIN_HEIGHT, 80, 'surfing', 1, 'whee, packets!', make_vitals(online=False))
        texts = [text for _, _, text, _ in cells]
        self.assertNotIn('offline', texts)
        self.assertTrue(any('load' in text for text in texts))


class SensorTest(unittest.TestCase):
    def test_memory_free_percent(self):
        meminfo = 'MemTotal:        8000000 kB\nMemFree:          100000 kB\nMemAvailable:     600000 kB\n'
        with fake_file(meminfo):
            self.assertEqual(piPet.read_memory_free_percent(), 7.5)
        with fake_file('MemTotal:        8000000 kB\n'):
            self.assertIsNone(piPet.read_memory_free_percent())

    def test_uptime(self):
        with fake_file('138736.54 532381.80\n'):
            self.assertEqual(piPet.read_uptime_sec(), 138736.54)
        with mock.patch('builtins.open', side_effect=OSError):
            self.assertIsNone(piPet.read_uptime_sec())

    def test_disk_used_percent_matches_df(self):
        # df: Use% = used / (used + available to ordinary users); reserved blocks don't count
        stats = mock.Mock(f_blocks=1000, f_bfree=150, f_bavail=100)
        with mock.patch.object(piPet.os, 'statvfs', return_value=stats):
            self.assertAlmostEqual(piPet.read_disk_used_percent(), 850 / 950 * 100)
        with mock.patch.object(piPet.os, 'statvfs', side_effect=OSError):
            self.assertIsNone(piPet.read_disk_used_percent())

    def test_undervoltage_found_by_hwmon_name(self):
        files = {
            '/sys/class/hwmon/hwmon0/name': 'cpu_thermal\n',
            '/sys/class/hwmon/hwmon3/name': 'rpi_volt\n',
            '/sys/class/hwmon/hwmon3/in0_lcrit_alarm': '1\n',
        }

        def fake_open(path, *args, **kwargs):
            if path not in files:
                raise FileNotFoundError(path)
            return io.StringIO(files[path])

        with mock.patch.object(piPet.glob, 'glob', return_value=sorted(n for n in files if n.endswith('name'))), \
                mock.patch('builtins.open', side_effect=fake_open):
            self.assertIs(piPet.read_undervoltage(), True)
            files['/sys/class/hwmon/hwmon3/in0_lcrit_alarm'] = '0\n'
            self.assertIs(piPet.read_undervoltage(), False)
            files['/sys/class/hwmon/hwmon3/name'] = 'something_else\n'
            self.assertIsNone(piPet.read_undervoltage())


class NetRateTest(unittest.TestCase):
    def measure(self, vitals, seconds, total_bytes):
        """Take a network reading at a chosen time with a chosen byte count."""
        with mock.patch.object(piPet.time, 'monotonic', return_value=seconds), \
                mock.patch.object(piPet, 'read_net_bytes', return_value=total_bytes):
            vitals.update_net_rate()

    def make_measured_vitals(self, seconds=100.0, total_bytes=1_000_000):
        vitals = make_vitals()
        vitals.last_net, vitals.last_time = total_bytes, seconds
        return vitals

    def test_startup_does_not_spike(self):
        # One 1500-byte packet between the two readings at startup used to read as ~30 MB/s
        with mock.patch.object(piPet.time, 'monotonic', side_effect=[100.0, 100.00005]), \
                mock.patch.object(piPet, 'read_net_bytes', side_effect=[1_000_000, 1_001_500]):
            vitals = piPet.Vitals(interface='wlan0')
        self.assertEqual(vitals.net_rate, 0)
        self.assertNotEqual(vitals.mood(), 'surfing')

    def test_readings_too_close_together_are_skipped(self):
        vitals = self.make_measured_vitals()
        self.measure(vitals, 100.1, 1_050_000)
        self.assertEqual(vitals.net_rate, 0)
        self.assertEqual(vitals.last_net, 1_000_000, 'the earlier reading should be kept')

    def test_rate_over_two_seconds(self):
        vitals = self.make_measured_vitals()
        self.measure(vitals, 102.0, 1_000_000 + 400 * 1024)
        self.assertEqual(vitals.net_rate, 200 * 1024)

    def test_counter_reset_reads_as_zero(self):
        vitals = self.make_measured_vitals()
        self.measure(vitals, 102.0, 500)
        self.assertEqual(vitals.net_rate, 0)

    def test_missing_interface_reads_as_zero_then_starts_over(self):
        vitals = self.make_measured_vitals()
        vitals.net_rate = 300 * 1024
        self.measure(vitals, 102.0, None)
        self.assertEqual(vitals.net_rate, 0)
        self.measure(vitals, 104.0, 5_000)  # interface is back with a fresh counter
        self.assertEqual(vitals.net_rate, 0)
        self.measure(vitals, 106.0, 5_000 + 2048)
        self.assertEqual(vitals.net_rate, 1024)


SAMPLE_ROUTE = """\
Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0144A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
eth0\t00000000\t0144A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0
eth0\t0044A8C0\t00000000\t0001\t0\t0\t100\t00FCFFFF\t0\t0\t0
"""

SAMPLE_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:  673446  2920    0    0    0     0          0         0   673446  2920    0    0    0     0       0          0
  eth0:    1000    10    0    0    0     0          0         0      200     2    0    0    0     0       0          0
 wlan0:   50000   500    0    0    0     0          0         0     7000    70    0    0    0     0       0          0
"""


def fake_file(text):
    return mock.patch('builtins.open', mock.mock_open(read_data=text))


class InterfaceTest(unittest.TestCase):
    def test_default_route_with_the_lowest_metric_wins(self):
        with fake_file(SAMPLE_ROUTE):
            self.assertEqual(piPet.get_default_interface(), 'eth0')

    def test_single_default_route(self):
        wifi_only = '\n'.join(line for line in SAMPLE_ROUTE.splitlines() if not line.startswith('eth0'))
        with fake_file(wifi_only):
            self.assertEqual(piPet.get_default_interface(), 'wlan0')

    def test_no_default_route_or_no_file(self):
        local_only = '\n'.join(SAMPLE_ROUTE.splitlines()[::3])  # header and the non-default eth0 route
        with fake_file(local_only):
            self.assertIsNone(piPet.get_default_interface())
        with mock.patch('builtins.open', side_effect=OSError):
            self.assertIsNone(piPet.get_default_interface())

    def test_read_net_bytes_adds_in_and_out_for_one_interface(self):
        for interface, expected in (('wlan0', 57000), ('eth0', 1200), ('wlan1', None), ('wlan', None)):
            with self.subTest(interface=interface), fake_file(SAMPLE_NET_DEV):
                self.assertEqual(piPet.read_net_bytes(interface), expected)

    def test_switching_networks_starts_a_fresh_measurement(self):
        vitals = make_vitals()
        vitals.chosen_interface = None  # following the default route
        vitals.last_net, vitals.last_time = 1_000_000, 100.0
        with mock.patch.object(piPet, 'get_default_interface', return_value='eth0'), \
                mock.patch.object(piPet.time, 'monotonic', return_value=102.0), \
                mock.patch.object(piPet, 'read_net_bytes', return_value=5_000) as read:
            vitals.update_net_rate()
        read.assert_called_with('eth0')
        self.assertEqual(vitals.interface, 'eth0')
        self.assertEqual(vitals.net_rate, 0, 'wlan0 and eth0 byte counts must not be compared')

    def test_chosen_interface_is_not_switched(self):
        vitals = make_vitals()  # chosen_interface is wlan0
        with mock.patch.object(piPet, 'get_default_interface', return_value='eth0') as detect, \
                mock.patch.object(piPet, 'read_net_bytes', return_value=None):
            vitals.update_net_rate()
        detect.assert_not_called()
        self.assertEqual(vitals.interface, 'wlan0')

    def test_status_line_names_the_interface(self):
        vitals = make_vitals(net_rate=1536)
        vitals.interface = 'eth0'
        self.assertIn('eth0 1.5 KB/s', piPet.build_status(vitals))


class CommandLineTest(unittest.TestCase):
    def parse_with_error(self, argv):
        """Parse arguments that should be rejected; return argparse's error message."""
        with mock.patch.object(sys, 'stderr', new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit) as exit_info:
                piPet.parse_args(argv)
        self.assertEqual(exit_info.exception.code, 2)
        return stderr.getvalue()

    def test_defaults(self):
        args = piPet.parse_args([])
        self.assertFalse(args.once)
        self.assertIsNone(args.mood)
        self.assertIsNone(args.iface)

    def test_both_mood_spellings(self):
        for argv in (['--mood', 'hot'], ['--mood=hot']):
            with self.subTest(argv=argv):
                self.assertEqual(piPet.parse_args(argv).mood, 'hot')

    def test_once_with_a_mood(self):
        args = piPet.parse_args(['--once', '--mood', 'sleepy'])
        self.assertTrue(args.once)
        self.assertEqual(args.mood, 'sleepy')

    def test_unknown_mood_lists_the_choices(self):
        message = self.parse_with_error(['--mood', 'angry'])
        self.assertIn("invalid choice: 'angry'", message)
        for mood in piPet.FACES:
            self.assertIn(mood, message)

    def test_iface_must_exist(self):
        self.assertEqual(piPet.parse_args(['--iface', 'lo']).iface, 'lo')  # every Linux machine has lo
        self.assertIn("no network interface called 'wlna0'", self.parse_with_error(['--iface', 'wlna0']))

    def test_missing_mood_or_typo_is_an_error(self):
        self.assertIn('expected one argument', self.parse_with_error(['--mood']))
        self.assertIn('unrecognized arguments: --onec', self.parse_with_error(['--onec']))


class FormatTest(unittest.TestCase):
    def test_format_rate(self):
        self.assertEqual(piPet.format_rate(0), '0.0 KB/s')
        self.assertEqual(piPet.format_rate(1536), '1.5 KB/s')
        self.assertEqual(piPet.format_rate(3 * 1024 * 1024), '3.0 MB/s')

    def test_status_line_handles_missing_sensors(self):
        vitals = make_vitals(temp_c=None)
        vitals.fan_rpm = None
        status = piPet.build_status(vitals)
        self.assertIn('temp --', status)
        self.assertIn('fan --', status)


if __name__ == '__main__':
    unittest.main()
