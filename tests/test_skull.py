import json
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import skull  # noqa: E402
import display  # noqa: E402
from remote_turn import speech_envelope  # noqa: E402


class SkullTests(unittest.TestCase):
    def test_drawn_fallback_has_a_pulsing_eye(self):
        s = skull.Skull("/does/not/exist")
        self.assertEqual(s.source, "drawn")
        self.assertIsNotNone(s.box)
        dim, hot = s.frame(0.0), s.frame(1.0)
        x0, y0, x1, y1 = s.box
        center = ((x0 + x1) // 2, (y0 + y1) // 2)
        self.assertGreater(hot.getpixel(center)[0], dim.getpixel(center)[0])
        self.assertEqual(dim.getpixel((2, 2)), hot.getpixel((2, 2)))  # outside the eye

    def test_image_file_is_squared_and_eye_found(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skull.png"
            image = Image.new("RGB", (320, 180), (90, 80, 60))
            ImageDraw.Draw(image).ellipse((190, 70, 230, 110), fill=(250, 20, 20))
            image.save(path)
            s = skull.Skull(path)
        self.assertEqual((s.source, s.base.size), ("file", (skull.SIZE, skull.SIZE)))
        self.assertIsNotNone(s.box)

    def test_levels(self):
        self.assertTrue(0.1 <= skull.idle_level(1.0) <= 0.8)
        envelope = dict(start=100.0, step=0.05, levels=[0, 100])
        self.assertAlmostEqual(skull.speaking_level(100.01, envelope), 0.55)
        self.assertAlmostEqual(skull.speaking_level(100.06, envelope), 1.0)
        self.assertTrue(0.4 <= skull.speaking_level(200.0, envelope) <= 1.0)  # past: pulse


class EnvelopeTests(unittest.TestCase):
    def test_envelope_follows_loudness(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reply.wav"
            samples = array("h", [0] * 2400 + [8000, -8000] * 1200 + [400, -400] * 1200)
            with wave.open(str(path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(48000)
                out.writeframes(samples.tobytes())
            levels = speech_envelope(path)
        self.assertEqual(len(levels), 3)
        self.assertEqual(levels[0], 0)
        self.assertEqual(levels[1], 100)
        self.assertLess(levels[2], 20)

    def test_read_envelope_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env.json"
            path.write_text(json.dumps(dict(start=1.0, step=0.05, levels=[0, 50, 100])))
            self.assertEqual(display.read_envelope(path)["levels"], [0, 50, 100])
            path.write_text(json.dumps(dict(start=1.0, step=0.05, levels=[0, 500])))
            self.assertIsNone(display.read_envelope(path))

    def test_render_skull_screens(self):
        class Capture:
            def image(self, image, rotation=0):
                self.frame = image
        s = skull.Skull("/does/not/exist")
        for state, info in (("BEREIT", dict(wake="Hey Jarvis")), ("BEREIT", dict(alarm="battery")),
                            ("AUSGABE", {})):
            capture = Capture()
            display.render_skull(capture, s, state, True, 0.5, info)
            self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))


if __name__ == "__main__":
    unittest.main()


class RainTests(unittest.TestCase):
    def test_rain_is_deterministic_moves_and_spells_litanies(self):
        from PIL import Image
        r = skull.Rain((2, 14), top=0, rows=12)
        font = display.font(10)
        frames = []
        for frame in (5, 5, 6):
            image = Image.new("RGB", (30, 140), "black")
            r.draw(image, frame, "think", (0, 170, 255), font)
            frames.append(image.tobytes())
        self.assertEqual(frames[0], frames[1])      # same frame index, same picture
        self.assertNotEqual(frames[0], frames[2])   # next frame: the streams moved
        self.assertTrue(all(t.isupper() or not t.isalpha() for texts in skull.LITANIES.values()
                            for t in texts))

    def test_status_light_follows_led_meaning(self):
        self.assertEqual(display.status_light("BEREIT", dict(server="ok")), display.LED_LIKE["ready"])
        self.assertEqual(display.status_light("BEREIT", dict(server="down")), display.LED_LIKE["local"])
        self.assertEqual(display.status_light("DENKEN", dict(route="server")), display.LED_LIKE["server"])
        self.assertEqual(display.status_light("ZUHÖREN", {}), display.LED_LIKE["listen"])
        self.assertEqual(display.status_light("AUSGABE", {}), display.LED_LIKE["speak"])

    def test_other_eye_takes_the_status_colour(self):
        s = skull.Skull("/does/not/exist")
        self.assertTrue(s.lens)
        x, y = s.lens[len(s.lens) // 2]
        plain, tinted = s.frame(0.5), s.frame(0.5, (0, 170, 255))
        self.assertGreater(tinted.getpixel((x, y))[2], plain.getpixel((x, y))[2])

    def test_partial_display_sends_side_bands_separately(self):
        from PIL import Image

        class Hardware:
            def __init__(self):
                self.calls = []

            def image(self, image, rotation=0, x=0, y=0):
                self.calls.append((x, y, image.size))

        hw = Hardware()
        partial = display.PartialDisplay(hw)
        first = Image.new("RGB", (240, 240), "black")
        partial.image(first)
        second = first.copy()
        second.putpixel((5, 100), (255, 0, 0))
        second.putpixel((230, 100), (255, 0, 0))
        partial.image(second)
        self.assertEqual(hw.calls[1:], [(5, 100, (1, 1)), (230, 100, (1, 1))])

    def test_rgb565_matches_the_driver_formula(self):
        from PIL import Image
        colours = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (12, 200, 99), (255, 255, 255), (7, 3, 250)]
        image = Image.new("RGB", (len(colours), 1))
        image.putdata(colours)
        expected = b"".join((((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)).to_bytes(2, "big")
                            for r, g, b in colours)
        self.assertEqual(display.rgb565(image), expected)


class RestTests(unittest.TestCase):
    def test_rest_screen_is_dim_without_rain_or_lens(self):
        from PIL import Image

        class Capture:
            def image(self, image, rotation=0):
                self.picture = image.rotate(rotation)

        s = skull.Skull("/does/not/exist")
        awake, rest = Capture(), Capture()
        display.render_skull(awake, s, "BEREIT", "ok", 0.9, dict(server="ok"), frame=7)
        display.render_rest(rest, s, "ok", dict(server="ok"))
        # no litanies in the side strips
        self.assertIsNone(rest.picture.crop((0, 43, 50, 175)).convert("L").point(
            lambda v: 255 if v > 3 else 0).getbbox())
        self.assertIsNotNone(awake.picture.crop((0, 43, 50, 175)).getbbox())
        bright = max(max(px) for px in rest.picture.getdata())
        self.assertLessEqual(bright, int(255 * display.REST_BRIGHTNESS))

    def test_status_whitelist_accepts_power(self):
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.json"
            path.write_text(json.dumps({"power": "sleep"}))
            self.assertEqual(display.read_status(path)["power"], "sleep")
            path.write_text(json.dumps({"power": "hack"}))
            self.assertNotIn("power", display.read_status(path))
