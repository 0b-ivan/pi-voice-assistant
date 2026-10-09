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
            "guten morgen": "briefing",
            "guten morgen proximus wie spät ist es": "briefing",
            "morgenbericht": "briefing",
            "starte die morgenlitanei": "briefing",
            "wie ist das wetter": "weather",
            "regnet es heute": "weather",
            "brauche ich einen regenschirm": "weather",
        }
        for text, intent in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), intent)

    def test_everything_else_goes_to_the_llm(self):
        for text in ("wie spät ist es in tokio", "wie ist das wetter in rom",
                     "bis morgen", "welcher tag ist heute in new york",
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

    WEATHER = dict(now=-2, code=61, high=8, low=-3, rain=70)

    def test_weather(self):
        self.assertEqual(intents.answer("weather", self.NOW, {}), "Wetterdaten nicht verfügbar.")
        text = intents.answer("weather", self.NOW, {"weather": self.WEATHER})
        self.assertTrue(text.startswith("Außentemperatur minus 2 Grad, Regen."))
        self.assertTrue(intents.answer("weather", self.NOW, {"weather": self.WEATHER,
                                                             "lore": "full"})
                        .startswith("Auspex meldet:"))

    def test_briefing_names_only_what_matters(self):
        morning = datetime.datetime(2026, 10, 9, 7, 5)
        text = intents.answer("briefing", morning, dict(
            weather=self.WEATHER, battery_pct=64, updates=3, operator="Ivan"))
        self.assertTrue(text.startswith("Guten Morgen, Ivan. Heute ist Freitag, der neunte "
                                        "Oktober. Es ist 7 Uhr 5."))
        for part in ("Außentemperatur minus 2 Grad", "Energiespeicher 64 Prozent",
                     "Pi: 3 Aktualisierungen", "Bericht Ende."):
            self.assertIn(part, text)
        quiet = intents.answer("briefing", self.NOW, dict(battery_pct=90, battery_plugged=True))
        self.assertEqual(quiet, "Guten Tag. Heute ist Donnerstag, der achte Oktober. "
                                "Es ist 14 Uhr 32. Bericht Ende.")
        self.assertIn("Server nicht erreichbar",
                      intents.answer("briefing", self.NOW, {"server": "down"}))

    def test_briefing_lore(self):
        text = intents.answer("briefing", datetime.datetime(2026, 10, 9, 21, 0),
                              {"lore": "full", "operator": "Ivan"})
        self.assertTrue(text.startswith("Die Tageslitanei beginnt. Ave, Ivan."))
        self.assertTrue(text.endswith("Das Tagwerk möge beginnen."))
        self.assertTrue(intents.answer("briefing", datetime.datetime(2026, 10, 9, 21, 0), {})
                        .startswith("Guten Abend."))


if __name__ == "__main__":
    unittest.main()
