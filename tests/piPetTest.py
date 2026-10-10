"""Tests for Pi Pet. Standard library only:

    python3 -m unittest discover -s tests -p "*Test.py"
"""

import sys
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import piPet  # noqa: E402  (imported after the path is set)

TICKS = range(24)  # long enough to cover every animation cycle (blink is every 12 ticks)


def make_vitals(temp_c=50.0, load=0.5, net_rate=0, forced_mood=None):
    """A Vitals with chosen readings, without touching the real sensors."""
    vitals = object.__new__(piPet.Vitals)
    vitals.forced_mood = forced_mood
    vitals.temp_c = temp_c
    vitals.load = load
    vitals.fan_rpm = 2000
    vitals.net_rate = net_rate
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

    def test_zzz_never_cover_the_bubble_or_the_pet(self):
        # Same placement as run_live: bubble directly above the pet, both starting at the same column
        for saying in piPet.SAYINGS['sleepy']:
            bubble = piPet.build_bubble(saying)
            for tick in TICKS:
                pet = [text for text, _ in piPet.build_pet('sleepy', tick)]
                drawn = {(i - len(bubble), col) for i, line in enumerate(bubble + pet)
                         for col, char in enumerate(line) if char != ' '}
                for row, col, letter in piPet.build_zzz(tick):
                    with self.subTest(saying=saying, tick=tick, letter=letter):
                        self.assertNotIn((row, col), drawn)

    def test_sayings_fit_a_small_terminal(self):
        for sayings in piPet.SAYINGS.values():
            for saying in sayings:
                widest = max(len(line) for line in piPet.build_bubble(saying))
                self.assertLessEqual(widest, 60, f'"{saying}" makes a {widest}-column bubble')


class LayoutTest(unittest.TestCase):
    SIZES = [(24, 80), (40, 120), (14, 30), (12, 40), (11, 24)]  # (height, width), down to the minimum

    def all_layouts(self, sizes=SIZES):
        vitals = make_vitals()
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
        self.assertIn('q to quit', texts)
        self.assertTrue(any(text.startswith('temp ') for text in texts), 'full status line expected')

    def test_tiny_window_shows_a_message(self):
        for height, width in ((10, 80), (24, 23), (5, 10), (1, 1)):
            cells = piPet.layout(height, width, 'content', 1, 'just vibing', make_vitals())
            with self.subTest(size=(height, width)):
                self.assertLessEqual(len(cells), 1)
                self.assertTrue(all('window too small'.startswith(text) for _, _, text, _ in cells))

    def test_busy_bounce_moves_the_pet_not_the_bubble(self):
        def bubble_and_leaf_rows(tick):
            cells = piPet.layout(24, 80, 'busy', tick, 'working hard!', make_vitals())
            bubble_row = next(row for row, _, text, _ in cells if 'working hard!' in text)
            leaf_row = next(row for row, _, text, _ in cells if '\\\\|//' in text)
            return bubble_row, leaf_row

        (bubble_up, leaf_up), (bubble_down, leaf_down) = bubble_and_leaf_rows(1), bubble_and_leaf_rows(2)
        self.assertEqual(bubble_up, bubble_down)
        self.assertEqual(leaf_down, leaf_up + 1)


class MoodTest(unittest.TestCase):
    def test_moods_by_reading(self):
        cases = [
            ('hot', dict(temp_c=72, load=3, net_rate=500_000)),  # hot beats everything
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

    def test_missing_temperature_does_not_crash(self):
        self.assertEqual(make_vitals(temp_c=None, load=0.1).mood(), 'sleepy')

    def test_forced_mood_wins(self):
        self.assertEqual(make_vitals(temp_c=90, forced_mood='sleepy').mood(), 'sleepy')

    def test_every_mood_has_a_face_and_sayings(self):
        self.assertEqual(set(piPet.FACES), set(piPet.SAYINGS))


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
            vitals = piPet.Vitals()
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


class CommandLineTest(unittest.TestCase):
    def test_valid_mood(self):
        with mock.patch.object(sys, 'argv', ['piPet.py', '--mood', 'hot']):
            self.assertEqual(piPet.get_forced_mood(), 'hot')

    def test_no_mood(self):
        with mock.patch.object(sys, 'argv', ['piPet.py']):
            self.assertIsNone(piPet.get_forced_mood())

    def test_unknown_or_missing_mood_exits_with_a_message(self):
        for argv in (['piPet.py', '--mood', 'angry'], ['piPet.py', '--mood']):
            with self.subTest(argv=argv), mock.patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit) as exit_info:
                    piPet.get_forced_mood()
                self.assertIn('Pick one of', str(exit_info.exception.code))


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
