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
