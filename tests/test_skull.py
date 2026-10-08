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
