import sys
import unicodedata
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

    def test_dates_are_read_as_ordinals_without_a_sentence_break(self):
        spoken = pronounce.spoken
        self.assertEqual(spoken("Heute ist Samstag, der 10. Oktober 2026."),
                         "Heute ist Samstag, der zehnte Oktober 2026.")
        self.assertEqual(spoken("Termin am 1.3. um 8.30 Uhr."),
                         "Termin am ersten März um 8 Uhr 30.")
        self.assertEqual(spoken("Samstag, 21. Juni"), "Samstag, einundzwanzigster Juni")
        self.assertEqual(spoken("seit dem 03.07.2025"), "seit dem dritten Juli 2025")
        self.assertEqual(spoken("Es sind 3.5."), "Es sind 3.5.")   # no date context
        self.assertEqual(spoken("am 32.13."), "am 32.13.")

    def test_abbreviations_units_and_decimals(self):
        spoken = pronounce.spoken
        self.assertEqual(spoken("Das sind z. B. Mars, Terra usw. Ende."),
                         "Das sind zum Beispiel Mars, Terra und so weiter. Ende.")
        self.assertEqual(spoken("ca. 3.5 Kilo bei 21,5 °C und 10 km/h Wind, d.h. mild"),
                         "circa 3,5 Kilo bei 21,5 Grad und 10 Kilometer pro Stunde Wind, "
                         "das heißt mild")
        self.assertEqual(spoken("1.000 Meter, Nr. 4, Version 3.12.1"),
                         "1.000 Meter, Nummer 4, Version 3.12.1")
        self.assertEqual(spoken("Africa. Kalkulator"), "Africa. Kalkulator")
        self.assertEqual(spoken("Wie auf Phobos IX."), "Wie auf Phobos Neun.")

    def test_wrongly_stressed_names_go_to_piper_as_phonemes(self):
        text = pronounce.spoken("Lob dem Adeptus Mechanicus. Proximus dient, Boss.",
                                raw_phonemes=True)
        self.assertEqual(text, unicodedata.normalize(
            "NFD", "Lob dem [[ adˈɛptʊs meçˈaːnikʊs. ]] [[ prˈɔksimʊs ]] dient, Boss."))
        self.assertEqual(pronounce.spoken("Proximus dient.", raw_phonemes=False),
                         "Proximus dient.")
        self.assertEqual(pronounce.spoken("Proximusse", raw_phonemes=True), "Proximusse")

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
