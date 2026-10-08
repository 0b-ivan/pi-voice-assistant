import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boardled  # noqa: E402


class BoardLedTests(unittest.TestCase):
    def test_off_and_restore_trigger(self):
        with tempfile.TemporaryDirectory() as tmp:
            act = Path(tmp) / "ACT"
            act.mkdir()
            (act / "trigger").write_text("none timer [actpwr] mmc0\n")
            (act / "brightness").write_text("255\n")
            leds = boardled.BoardLeds(tmp)
            self.assertIsNone(leds.off())
            self.assertEqual(((act / "trigger").read_text(), (act / "brightness").read_text()),
                             ("none", "0"))
            leds.off()                       # sleeping twice keeps the original trigger
            (act / "trigger").write_text("[none] timer actpwr mmc0\n")
            leds.restore()
            self.assertEqual((act / "trigger").read_text(), "actpwr")

    def test_missing_permission_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            act = Path(tmp) / "ACT"
            act.mkdir()
            (act / "trigger").write_text("[actpwr]\n")
            (act / "brightness").mkdir()     # writing fails like EACCES
            self.assertIsNotNone(boardled.BoardLeds(tmp).off())


if __name__ == "__main__":
    unittest.main()
