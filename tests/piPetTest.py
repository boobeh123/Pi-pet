"""Tests for Pi Pet. Standard library only:

    python3 -m unittest discover -s tests -p "*Test.py"
"""

import io
import random
import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import piPet  # noqa: E402  (imported after the path is set)

TICKS = range(24)  # long enough to cover every animation cycle (blink is every 12 ticks)


def make_vitals(temp_c=50.0, load=0.5, net_rate=0, forced_mood=None, undervoltage=False,
                disk_used_percent=50.0, memory_free_percent=70.0, uptime_sec=10_000.0, online=True, hour=12.0,
                claude_here=False, adguard_running=None):
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
        vitals = make_vitals(claude_here=True, adguard_running=True)  # with both pals, wherever there's room
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
        self.assertIn('p to pet  ·  q to quit', texts)
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
                                                         adguard_running=tick % 2 == 0))  # guard awake or asleep
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
        night = piPet.build_sky(80, 0.0, 0)
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
        self.assertIn('p to pet  ·  q to quit', rows)

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
