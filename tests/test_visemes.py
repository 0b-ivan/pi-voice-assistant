import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import face  # noqa: E402
import remote_turn  # noqa: E402
import visemes  # noqa: E402


def syllables(*peaks, gap=2, width=4):
    """A loudness curve: one bump per syllable, silence between."""
    levels = [0] * gap
    for peak in peaks:
        levels += [peak // 3, peak, peak // 2] + [peak // 4] * (width - 3) + [0] * gap
    return levels


class VisemeTests(unittest.TestCase):
    def test_vowels_of_german_text(self):
        self.assertEqual(visemes.vowels('Hallo Boss'), ['a', 'o', 'o'])
        self.assertEqual(visemes.vowels('Kein Ding, Boss.'), ['e', 'e', 'o'])
        self.assertEqual(visemes.vowels('Häuser bauen'), ['o', 'e', 'a', 'e'])
        self.assertEqual(visemes.vowels('8'), ['a'])
        self.assertEqual(visemes.vowels(''), [])

    def test_each_syllable_gets_its_vowel_and_pauses_close(self):
        levels = syllables(90, 40, 90)
        mouth = visemes.track(levels, 'Ja so gut')
        self.assertEqual(len(mouth), len(levels))
        self.assertTrue(set(mouth) <= set(visemes.CODES))
        self.assertEqual(mouth[0], '.')
        shapes = [c.lower() for c in mouth if c != '.']
        self.assertEqual(shapes[0], 'a')
        self.assertIn('o', shapes)
        self.assertIn('A', mouth)                    # the loud first syllable opens wide

    def test_more_syllables_than_vowels_and_no_flicker(self):
        mouth = visemes.track(syllables(80, 80, 80, 80, 80), 'Ja')
        self.assertEqual({c.lower() for c in mouth} - {'.'}, {'a'})
        runs = [len(run) for run in ''.join(c if c != '.' else ' ' for c in mouth).split()]
        self.assertTrue(all(r >= 2 for r in runs))
        self.assertEqual(visemes.track([0, 5, 3], 'Hallo'), '...')


class FaceTests(unittest.TestCase):
    def test_mouth_follows_the_code(self):
        now = 2.0
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme='O'), (0, 'talk_round'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme='A'), (0, 'talk_open'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme='e'), (0, 'talk_e'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme='a'), (0, 'talk_half'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme='.'), (0, 'look'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, viseme=None), (0, 'talk_open'))
        angry = ('gereizt', 0.9)
        self.assertEqual(face.choose('SPRECHEN', now, 0.9, mood=angry, viseme='O'), (0, 'ouch'))

    def test_mouths_are_drawn_at_the_calm_lips(self):
        from PIL import Image
        path = Path(__file__).resolve().parents[1] / 'assets' / 'display' / 'doom-faces.png'
        sheet = face.Face(path)
        shapes = ('talk_e', 'talk_half', 'talk_open', 'talk_round')
        self.assertEqual(len({sheet.frame((0, s)).tobytes() for s in shapes}), 4)
        clean = face._shrink(face.slice_sheet(Image.open(path))[(0, 'look')], 4)
        row, left, right = face._lipline(clean)
        self.assertEqual((row, left, right), (23, 9, 14))
        look = sheet.frame((0, 'look'))
        top = sheet.size[1] - (clean.height + face.JAW) * 4
        for shape in shapes:          # eyes and upper lip stay; the jaw moves below the lips
            frame = sheet.frame((0, shape))
            changed = {(y - top) // 4 for y in range(sheet.size[1]) for x in range(sheet.size[0])
                       if frame.getpixel((x, y)) != look.getpixel((x, y))}
            self.assertTrue(changed and min(changed) >= row, shape)


class EnvelopeTests(unittest.TestCase):
    def test_mouth_codes_written_never_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'reply.wav'
            with wave.open(str(audio), 'wb') as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                frames = b''.join((b'\x00\x40' if (i // 1600) % 3 else b'\x00\x00') * 1
                                  for i in range(16000))
                out.writeframes(frames)
            target = Path(tmp) / 'speech-envelope.json'
            with patch.dict('os.environ', {'PI_DISPLAY_ENVELOPE_FILE': str(target)}):
                remote_turn.publish_envelope(audio, 100.0, 'Geheimer Text über Ivan')
            raw = target.read_text()
            self.assertNotIn('Ivan', raw)
            data = json.loads(raw)
            self.assertEqual(len(data['mouth']), len(data['levels']))
            envelope = display.read_envelope(target)
            self.assertEqual(envelope['mouth'], data['mouth'])
            index = next(i for i, c in enumerate(data['mouth']) if c != '.')
            self.assertEqual(display.mouth_now(envelope, 100.0 + index * 0.05 + 0.01),
                             data['mouth'][index])
            self.assertIsNone(display.mouth_now(envelope, 99.0))
            target.write_text(json.dumps(dict(data, mouth='<script>')))
            self.assertNotIn('mouth', display.read_envelope(target))


if __name__ == '__main__':
    unittest.main()
