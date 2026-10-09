import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import intents  # noqa: E402


class MatchTests(unittest.TestCase):
    def test_recognized_questions(self):
        cases = {
            "wie spät ist es": "time",
            "wie viel uhr ist es": "time",
            "wieviel uhr haben wir": "time",
            "sag mir die uhrzeit": "time",
            "welcher tag ist heute": "date",
            "welches datum haben wir": "date",
            "der wievielte ist heute": "date",
            "wie ist dein status": "status",
            "statusbericht": "status",
            "status": "status",
            "wie geht es dir": "status",
            "wie voll ist dein akku": "battery",
            "wie ist der ladestand": "battery",
            "wer bist du": "identity",
            "Wie spät ist es?": "time",
        }
        for text, intent in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), intent)

    def test_everything_else_goes_to_the_llm(self):
        for text in ("wie spät ist es in tokio", "welcher tag ist heute in new york",
                     "wie hoch ist der eiffelturm", "erkläre kurz was ein raspberry pi ist",
                     "wie viele minuten hat ein tag", "", "was ist der status quo in der "
                     "deutschen politik und warum ist das wichtig für die zukunft unseres landes"):
            with self.subTest(text=text):
                self.assertIsNone(intents.match(text))


class AnswerTests(unittest.TestCase):
    NOW = datetime.datetime(2026, 10, 8, 14, 32)

    def test_time_and_date(self):
        self.assertEqual(intents.answer("time", self.NOW), "Zeitindex: 14 Uhr 32.")
        self.assertEqual(intents.answer("time", self.NOW.replace(minute=0)), "Zeitindex: 14 Uhr.")
        self.assertEqual(intents.answer("date", self.NOW),
                         "Datum: Donnerstag, der achte Oktober 2026.")
        self.assertEqual(intents.answer("date", datetime.datetime(2026, 12, 31)),
                         "Datum: Donnerstag, der einunddreißigste Dezember 2026.")

    def test_status_battery_identity(self):
        snapshot = dict(battery_pct=83, battery_charging=True, temp_c=45, server="ok")
        self.assertTrue(intents.answer("status", self.NOW, snapshot).startswith("Status nominal."))
        self.assertEqual(intents.answer("battery", self.NOW, snapshot),
                         "Energiespeicher 83 Prozent. Ladung aktiv.")
        self.assertEqual(intents.answer("battery", self.NOW, {}), "Energiedaten nicht verfügbar.")
        self.assertIn("Servitor Proximus", intents.answer("identity", self.NOW))

    def test_stop_phrases_only_as_whole_utterance(self):
        for text in ('Stop', 'stopp', 'Abbruch!', 'abbrechen', 'Sei still', 'sei ruhig',
                     'Klappe halten', 'halt die Klappe Proximus', 'Halt den Mund',
                     'Hör auf bitte', 'ok danke das reicht', 'Ruhe jetzt', 'Schnauze Billy'):
            self.assertTrue(intents.is_stop(text), text)
        for text in ('wie stoppt man eine blutung', 'stop die musik und erzähl mir was',
                     'schalte das licht aus', 'genug für heute danke', '', 'bitte',
                     'was bedeutet abbruch'):
            self.assertFalse(intents.is_stop(text), text)

    def test_billy_answers_identity_and_wellbeing_himself(self):
        self.assertEqual(intents.match('wer bist du'), 'identity')
        self.assertEqual(intents.match('wie geht es dir'), 'status')
        self.assertIsNone(intents.match('wer bist du', 'mensch'))
        self.assertIsNone(intents.match('wie geht es dir', 'mensch'))
        self.assertEqual(intents.match('systemstatus', 'mensch'), 'status')
        self.assertEqual(intents.match('wie spät ist es', 'mensch'), 'time')

    def test_lore_levels_change_wording_not_facts(self):
        off = intents.answer("time", self.NOW, {"lore": "off"})
        full = intents.answer("time", self.NOW, {"lore": "full"})
        self.assertEqual(off, "Zeitindex: 14 Uhr 32.")
        self.assertIn("14 Uhr 32.", full)
        self.assertIn("Omnissiah", full)
        self.assertIn("Donnerstag, der achte Oktober 2026.",
                      intents.answer("date", self.NOW, {"lore": "full"}))
        self.assertIn("Adeptus Mechanicus", intents.answer("identity", self.NOW, {"lore": "full"}))
        self.assertNotIn("Omnissiah", intents.answer("identity", self.NOW, {"lore": "off"}))
        self.assertIn("Heilige Ölung", intents.answer("battery", self.NOW,
                                                      {"lore": "full", "battery_pct": 50}))


if __name__ == "__main__":
    unittest.main()
