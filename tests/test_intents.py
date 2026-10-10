import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import intents  # noqa: E402


class MatchTests(unittest.TestCase):
    def test_personal_measurements_never_go_to_the_model_for_long_questions(self):
        cases = {
            'kannst du bitte einmal nachsehen wie hoch dein akku gerade wirklich ist und ob du lädst': 'battery',
            'wie viel prozent hast du noch': 'battery',
            'wie hoch ist deine cpu temperatur': 'status',
            'sag mir bitte einmal deine messwerte und deinen aktuellen status ohne etwas zu raten': 'status',
            'welches sprachmodell bist du': 'model',
            'was für ein sprach modell nutzt du': 'model',
            'was steht morgen an': 'calendar',
            'kannst du mir bitte sagen was steht morgen an und welche termine habe ich noch': 'calendar',
            'abendbericht': 'briefing',
        }
        for persona in ('servitor', 'mensch'):
            for text, expected in cases.items():
                with self.subTest(persona=persona, text=text):
                    self.assertEqual(intents.match(text, persona), expected)

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
            "wie ist der morgen bericht": "briefing",
            "tages bericht bitte": "briefing",
            "lage bericht": "briefing",
            "starte die morgenlitanei": "briefing",
            "wie ist das wetter": "weather",
            "regnet es heute": "weather",
            "brauche ich einen regenschirm": "weather",
            "welche termine habe ich heute": "calendar",
            "was steht heute an": "calendar",
            "habe ich heute was vor": "calendar",
        }
        for text, intent in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), intent)

    def test_system_test_stt_forms(self):
        # Vosk outputs and phrasings from the 2026-10-10 system test.
        self.assertEqual(intents.match("wie ist der akkus dann"), 'battery')
        self.assertEqual(intents.match("was steht heute noch an"), 'calendar')
        self.assertEqual(intents.match("steht heute noch was an"), 'calendar')
        self.assertIsNone(intents.match("wie lange halten lithium akkus"))

    def test_hardware_questions_are_answered_locally(self):
        # Operator questions of 10.10. 23:37 (the LLM invented 8/16 GB and Teraflops).
        for text in ("wie ist deine speicherplatz arbeitsspeicher aus",
                     "wie viel arbeitsspeicher hast du auf dem kleinen",
                     "wie viel arbeitsspeicher hast",
                     "du willst also behaupten dass du ach gigabyte arbeitsspeicher hast "
                     "kannst du es gerne mal nach",
                     "nie was hast du denn für eine cpu und wie viel liefert sie",
                     "wie viel arbeitsspeicher", "was hast du für hart",     # 23:53
                     "welche hardware hast du", "arbeitsspeicher"):
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), 'hardware')
        for text in ("wie viel ram braucht ein laptop", "was ist ein prozessor",
                     "was ist arbeitsspeicher", "das war hart", "wie hart ist diamant"):
            with self.subTest(text=text):
                self.assertIsNone(intents.match(text))

    def test_hardware_answer_has_the_real_numbers(self):
        for lore in ('off', 'light', 'full', 'billy', 'billy_full'):
            with self.subTest(lore=lore):
                text = intents.answer('hardware', datetime.datetime(2026, 10, 10, 23, 37),
                                      {'mem_free_pct': 46}, lore=lore)
                self.assertIn('Zero 2 W', text)
                self.assertIn('512 Megabyte', text)
                self.assertIn('46 Prozent frei', text)

    def test_missing_functions_get_an_honest_local_answer(self):
        # Vosk forms from the 2026-10-10 system test included ("still" for "stell").
        for text in ("stell einen wecker auf sieben uhr", "still einen wecker auf sieben uhr",
                     "stelle eine wecker auf sieben uhr", "stellt einen timer auf zehn minuten",
                     "kannst du einen wecker stellen", "weck mich um sechs",
                     "ich erinnere mich morgen an den arzttermin",
                     "erinnere mich morgen an den termin", "schick meiner mutter eine nachricht",
                     "spielmusik von queen", "spiel musik von queen",
                     "schalte das licht im wohnzimmer an", "mach das licht aus"):
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), 'unsupported')
        for text in ("wie stelle ich am handy einen wecker", "was ist ein timer",
                     "wann wurde der wecker erfunden", "erzähl mir einen witz",
                     "schalte das wlan aus", "wie spät ist es"):
            with self.subTest(text=text):
                self.assertNotEqual(intents.match(text), 'unsupported')

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
        self.assertIn(intents.answer("time", self.NOW),
                      ("Zeitindex: 14 Uhr 32.", "Es ist 14 Uhr 32."))
        self.assertTrue(intents.answer("time", self.NOW.replace(minute=0)).endswith(" 14 Uhr."))
        self.assertIn(intents.answer("date", self.NOW),
                      ("Datum: Donnerstag, der achte Oktober 2026.",
                       "Heute ist Donnerstag, der achte Oktober 2026."))
        self.assertTrue(intents.answer("date", datetime.datetime(2026, 12, 31)).endswith(
                        "Donnerstag, der einunddreißigste Dezember 2026."))

    def test_variants_keep_facts_and_do_not_repeat_at_once(self):
        import variants
        variants.reset()
        times = [intents.answer("time", self.NOW) for _ in range(8)]
        self.assertTrue(all(t.endswith("14 Uhr 32.") for t in times))
        self.assertTrue(all(a != b for a, b in zip(times, times[1:])))
        names = [intents.answer("identity", self.NOW, {"lore": lore})
                 for lore in ("off", "light", "full") for _ in range(3)]
        self.assertTrue(all("Servitor Proximus" in n for n in names))

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
        self.assertTrue(off.endswith("14 Uhr 32."))
        self.assertIn("14 Uhr 32.", full)
        self.assertIn("Donnerstag, der achte Oktober 2026.",
                      intents.answer("date", self.NOW, {"lore": "full"}))
        self.assertIn("Adeptus Mechanicus", intents.answer("identity", self.NOW, {"lore": "full"}))
        self.assertNotIn("Omnissiah", intents.answer("identity", self.NOW, {"lore": "off"}))
        self.assertIn("50 Prozent", intents.answer("battery", self.NOW,
                                                   {"lore": "full", "battery_pct": 50}))

    WEATHER = dict(now=-2, code=61, high=8, low=-3, rain=70)

    def test_model_question_keeps_both_personas_and_describes_local_mode(self):
        for persona, name in (('servitor', 'Proximus'), ('mensch', 'Billy')):
            text = intents.answer('model', self.NOW, dict(persona=persona, llm_mode='local'))
            self.assertIn(name, text)
            self.assertIn('lokale Sprachkern', text)
            self.assertNotIn('Mistral', text)

    def test_calendar_tomorrow_never_uses_today_or_guesses(self):
        for question in ('was steht morgen an', 'welche termine habe ich morgen'):
            self.assertEqual(intents.calendar_day(question, self.NOW.date()), 1)
        for question in ('was steht übermorgen an', 'welche termine nächste woche',
                         'termine am 15 oktober'):
            day = intents.calendar_day(question, self.NOW.date())
            self.assertNotIn(day, (0, 1))
            self.assertIn('nur für heute und morgen',
                          intents.answer('calendar', self.NOW, dict(agenda_day=day, agenda=[])))
        self.assertEqual(intents.answer('calendar', self.NOW, dict(agenda_day=1)),
                         'Kalenderdaten für morgen nicht verfügbar.')
        self.assertEqual(intents.answer('calendar', self.NOW, dict(agenda_day=1, agenda=[])),
                         'Keine weiteren Termine morgen.')

    def test_evening_report_and_rain_refer_to_tomorrow(self):
        data = dict(self.WEATHER, days=[{}, dict(date='2026-10-09', code=3, high=14, low=6, rain=40)])
        evening = self.NOW.replace(hour=18)
        text = intents.answer('briefing', evening, dict(weather=data))
        self.assertIn('Morgen: 6 bis 14 Grad', text)
        self.assertIn('40 Prozent', text)
        self.assertNotIn('70 Prozent', text)
        self.assertNotIn('Außentemperatur', text)
        morning = intents.answer('briefing', self.NOW.replace(hour=7), dict(weather=data))
        self.assertIn('70 Prozent', morning)
        self.assertNotIn('40 Prozent', morning)
        missing = intents.answer('briefing', evening, dict(weather=self.WEATHER))
        self.assertIn('Wetterdaten für morgen nicht verfügbar.', missing)
        self.assertNotIn('70 Prozent', missing)

    def test_unsupported_names_what_is_missing(self):
        for lore in ('off', 'light', 'full', 'billy', 'billy_full'):
            with self.subTest(lore=lore):
                text = intents.answer('unsupported', self.NOW, {}, lore=lore)
                self.assertIn('Wecker', text)
                self.assertNotIn('gestellt', text)

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
        self.assertTrue(text.startswith("Bediener Ivan identifiziert. Morgenbericht. Datum: "
                                        "Freitag, der neunte Oktober. Zeitindex: 7 Uhr 5."))
        for part in ("Außentemperatur minus 2 Grad", "Energiespeicher 64 Prozent",
                     "Pi: 3 Aktualisierungen", "Bericht Ende."):
            self.assertIn(part, text)
        quiet = intents.answer("briefing", self.NOW, dict(battery_pct=90, battery_plugged=True))
        self.assertEqual(quiet, "Tagesbericht. Datum: Donnerstag, der achte Oktober. "
                                "Zeitindex: 14 Uhr 32. Bericht Ende.")
        self.assertIn("Server nicht erreichbar",
                      intents.answer("briefing", self.NOW, {"server": "down"}))

    def test_weather_day(self):
        saturday = datetime.date(2026, 10, 10)
        cases = {"wie ist das wetter": 0, "wetter heute morgen": 0, "wie wird das wetter morgen": 1,
                 "wird es morgen regnen": 1, "wetter übermorgen": 2,
                 "wie ist das wetter am dienstag": 3, "wetter am freitag": None}
        for text, day in cases.items():
            with self.subTest(text=text):
                self.assertEqual(intents.match(text), "weather")
                self.assertEqual(intents.weather_day(text, saturday), day)
        self.assertEqual(intents.answer("weather", self.NOW, {"weather_day": None}),
                         "Vorhersage reicht nur fünf Tage.")

    def test_calendar_and_briefing(self):
        dentist = dict(summary="Zahnarzt", start=self.NOW.replace(hour=15, minute=30),
                       end=None, all_day=False)
        self.assertEqual(intents.answer("calendar", self.NOW, {}), "Kalenderdaten nicht verfügbar.")
        self.assertEqual(intents.answer("calendar", self.NOW, {"agenda": [dentist]}),
                         "Termine heute. 15 Uhr 30: Zahnarzt.")
        self.assertIn("Termine heute. 15 Uhr 30: Zahnarzt. Bericht Ende.",
                      intents.answer("briefing", self.NOW, {"agenda": [dentist]}))
        self.assertIn("Keine weiteren Termine heute.",
                      intents.answer("briefing", self.NOW, {"agenda": []}))
        # Unrecognized voice: refused on request, silently left out of the briefing.
        self.assertIn("nicht als Bediener erkannt",
                      intents.answer("calendar", self.NOW, {"agenda": "denied"}))
        self.assertNotIn("Bediener", intents.answer("briefing", self.NOW, {"agenda": "denied"}))

    def test_briefing_lore(self):
        text = intents.answer("briefing", datetime.datetime(2026, 10, 9, 21, 0),
                              {"lore": "full", "operator": "Ivan"})
        self.assertTrue(text.startswith("Bediener Ivan identifiziert. Die Abendlitanei "
                                        "beginnt. Ave Omnissiah."))
        self.assertTrue(text.endswith("Das Tagwerk möge beginnen."))
        for hour in (7, 14, 21):
            text = intents.answer("briefing", datetime.datetime(2026, 10, 9, hour, 0), {})
            self.assertNotIn("Guten", text)
        self.assertTrue(text.startswith("Abendbericht."))


if __name__ == "__main__":
    unittest.main()
