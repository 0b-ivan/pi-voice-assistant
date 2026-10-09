from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import face  # noqa: E402


def make_sheet(path, extras=True, scale=4):
    """A stand-in for the real sheet: coloured blocks in the same layout."""
    from PIL import Image, ImageDraw
    image = Image.new('RGBA', (11 * 34 * scale, 5 * 36 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for row in range(5):
        count = 9 if extras and row in (0, 4) else 8
        for column in range(count):
            width = 30 if column in (3, 4) else 24
            x, y = column * 34 * scale, row * 36 * scale
            draw.rectangle((x, y, x + width * scale - 1, y + 31 * scale - 1),
                           fill=(40 * row + 10, 20 * column + 5, 200, 255))
    image.save(path)


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'faces.png'

    def test_slices_all_faces_and_extras_without_rescaling(self):
        make_sheet(self.path)
        sheet = face.Face(self.path)
        originals = [k for k in sheet.frames if isinstance(k, str) or k[1] in face.COLUMNS]
        self.assertEqual(len(originals), 42)
        self.assertEqual(sheet.size, (120, 128))   # widest head, 4x, plus a pixel for the jaw
        self.assertEqual(sheet.frame((4, 'teeth')).getpixel((60, 62)), (170, 145, 200))
        self.assertEqual(sheet.frame('god').getpixel((60, 62)), (10, 165, 200))
        self.assertEqual(sheet.frame((2, 'unknown')), sheet.frame((2, 'look')))

    def test_small_sheet_is_scaled_up_by_whole_pixels(self):
        make_sheet(self.path, extras=False, scale=1)
        sheet = face.Face(self.path)
        self.assertEqual(sheet.size, (120, 128))   # 30 x 31 times 4, plus the jaw
        self.assertNotIn('dead', sheet.frames)

    def test_bundled_sheet_has_every_face(self):
        sheet = face.Face(Path(__file__).resolve().parents[1] / 'assets' / 'display'
                          / 'doom-faces.png')
        self.assertEqual(len([k for k in sheet.frames
                              if isinstance(k, str) or k[1] in face.COLUMNS]), 42)
        self.assertEqual(sheet.size, (120, 128))
        for name in face.DERIVED:                          # made from the sheet itself
            self.assertNotEqual(sheet.frame((0, name)), sheet.frame((0, 'look')), name)
        for key in ('god', 'dead', (0, 'look'), (4, 'teeth')):
            self.assertIn(key, sheet.frames)

    def test_wrong_layout_is_rejected(self):
        from PIL import Image
        Image.new('RGBA', (50, 50), (255, 0, 0, 255)).save(self.path)
        with self.assertRaises(ValueError):
            face.Face(self.path)


class ChooseTests(unittest.TestCase):
    def test_battery_is_health(self):
        self.assertEqual([face.health_row(p) for p in (100, 80, 79, 60, 59, 20, 19, 0, None)],
                         [0, 0, 1, 1, 2, 3, 4, 4, 0])
        self.assertEqual(face.choose('BEREIT', 0, battery=dict(percent=2)), 'dead')
        self.assertNotEqual(face.choose('BEREIT', 0, battery=dict(percent=2, plugged=True)),
                            'dead')

    def test_speaking_follows_loudness(self):
        battery = dict(percent=90)
        now = 2.0                                          # not blinking
        self.assertFalse(face.blinking(now, every=5.0))
        self.assertEqual(face.choose('SPRECHEN', now, 0.1, battery), (0, 'look'))
        self.assertEqual(face.choose('SPRECHEN', now, 0.4, battery), (0, 'talk_half'))
        self.assertEqual(face.choose('AUSGABE', now, 0.9, battery), (0, 'talk_open'))
        angry = ('gereizt', 0.8)
        self.assertEqual(face.choose('AUSGABE', now, 0.9, battery, mood=angry), (0, 'ouch'))
        self.assertEqual(face.choose('AUSGABE', now, 0.1, battery, mood=angry), (0, 'teeth'))

    def test_blinks_now_and_then(self):
        blinks = [face.blinking(t / 20) for t in range(20 * 60)]       # one minute
        self.assertTrue(12 <= blinks.count(True) / 3 <= 20)           # ~15 blinks of 3 ticks
        self.assertEqual(face.choose('BEREIT', 0.05), (0, 'blink'))
        self.assertEqual(face.resting(dict(percent=50)), (2, 'blink'))

    def test_thinking_turns_the_head_and_idle_glances(self):
        turns = {face.choose('DENKEN', t / 10)[1] for t in range(30)}
        self.assertEqual(turns, {'turn_a', 'turn_b', 'look'})
        glances = [face.choose('BEREIT', t * 0.7)[1] for t in range(200)]
        self.assertGreater(glances.count('look'), 150)
        self.assertIn('look_a', glances)
        self.assertEqual(face.choose('ZUHÖREN', 1.0), (0, 'look'))

    def test_hush_alarm_and_charging(self):
        self.assertEqual(face.choose('BEREIT', 100.2, hushed_at=100.0), (0, 'ouch'))
        self.assertEqual(face.choose('BEREIT', 101.0, hushed_at=100.0), (0, 'turn_a'))
        self.assertNotIn(face.choose('BEREIT', 103.0, hushed_at=100.0)[1], ('ouch', 'turn_a'))
        self.assertEqual(face.choose('BEREIT', 10.0, alarm=True), (0, 'ouch'))
        charging = dict(percent=50, charging=True, plugged=True)
        self.assertNotEqual(face.choose('BEREIT', 16.5, battery=charging), 'god')  # not while charging
        self.assertEqual(face.choose('BEREIT', 16.5, battery=charging, plugged_at=15.0), 'god')
        self.assertNotEqual(face.choose('BEREIT', 20.0, battery=charging, plugged_at=15.0), 'god')


class RenderTests(unittest.TestCase):
    def test_face_screen_and_glitch_render(self):
        from PIL import Image

        class Screen:
            def image(self, image, rotation):
                self.shown = image

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'faces.png'
            make_sheet(path)
            sheet = face.Face(path)
        screen = Screen()
        display.render_face(screen, sheet.frame((0, 'look')), 'DENKEN', 'ok',
                            dict(clock='12:00', route='server'))
        self.assertEqual(screen.shown.size, (240, 240))
        self.assertEqual(screen.shown.getpixel((120, 105)), sheet.frame((0, 'look')).getpixel(
            (120 - (240 - 120) // 2, 105 - display.FACE_TOP - 4)))
        glitch = display.glitch_picture(Image.new('RGB', (136, 136), 'white'),
                                        sheet.frame((0, 'look')), 3)
        self.assertEqual(glitch.size, sheet.size)
        display.render_rest(screen, None, 'ok', dict(clock='12:00'),
                            picture=sheet.frame((0, 'look')))
        self.assertEqual(screen.shown.size, (240, 240))


if __name__ == '__main__':
    unittest.main()
