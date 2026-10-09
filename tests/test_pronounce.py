import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import alarm_audio  # noqa: E402
import pronounce  # noqa: E402
import voice_controls  # noqa: E402


class PronounceTests(unittest.TestCase):
    def test_omnissiah_stressed_on_the_second_syllable(self):
        self.assertEqual(pronounce.spoken("Lob dem Omnissiah. AVE OMNISSIAH"),
                         "Lob dem Omnissi-ah. AVE Omnissi-ah")
        self.assertEqual(pronounce.spoken("Des Omnissiahs Wille."), "Des Omnissi-ah Wille.")
        self.assertEqual(pronounce.spoken("Omnissiahtempel"), "Omnissiahtempel")  # whole words

    def test_piper_gets_the_respelling(self):
        class Voice:
            def synthesize_wav(self, text, audio):
                self.text = text
        voice = Voice()
        voice_controls._synthesize_voice(voice, "Lob dem Omnissiah.", None, 'normal')
        self.assertEqual(voice.text, "Lob dem Omnissi-ah.")

    def test_alarm_clips_follow_the_spoken_form(self):
        # A new respelling renames the clip, so "build" renders it again.
        self.assertEqual(alarm_audio.clip_path("Lob dem Omnissiah."),
                         alarm_audio.clip_path("Lob dem Omnissi-ah."))
        self.assertNotEqual(alarm_audio.clip_path("Lob dem Omnissiah."),
                            alarm_audio.clip_path("Lob dem Maschinengeist."))


if __name__ == '__main__':
    unittest.main()
