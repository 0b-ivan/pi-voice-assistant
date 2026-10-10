"""Fixed sentences in Billy's words (persona "mensch")."""
import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import alarms  # noqa: E402
import intents  # noqa: E402
import maintenance  # noqa: E402
import memory  # noqa: E402
from system_status import phrase_style, status_text  # noqa: E402

NOW = datetime.datetime(2026, 10, 9, 7, 15)
MACHINE = ('Diese Einheit', 'Omnissiah', 'Maschinengeist', 'Zeitindex', 'Energiespeicher',
           'Kogitator', 'Noosphäre', 'Bediener')


class StyleTests(unittest.TestCase):
    def test_style_from_persona_and_lore(self):
        self.assertEqual([phrase_style(lore) for lore in ('off', 'light', 'full', None, 'x')],
                         ['off', 'light', 'full', 'off', 'off'])
        self.assertEqual([phrase_style(lore, 'mensch') for lore in ('off', 'light', 'full')],
                         ['billy', 'billy', 'billy_full'])
        self.assertEqual(phrase_style('full', 'servitor'), 'full')


class BillyWordingTests(unittest.TestCase):
    def assertHuman(self, text):
        for word in MACHINE:
            self.assertNotIn(word, text)

    def test_intents_follow_the_snapshot_persona(self):
        snapshot = dict(persona='mensch', lore='light', battery_pct=80, battery_plugged=True,
                        server='ok', temp_c=50)
        for intent in ('time', 'date', 'status', 'battery', 'network'):
            self.assertHuman(intents.answer(intent, NOW, snapshot))
        # Wording varies (variants.py); the time itself never does.
        for _ in range(6):
            self.assertIn('7 Uhr 15.', intents.answer('time', NOW, snapshot))
            self.assertHuman(intents.answer('time', NOW, dict(snapshot, lore='full')))
        self.assertIn(intents.answer('time', NOW, dict(snapshot, persona='servitor')),
                      ('Zeitindex: 7 Uhr 15.', 'Es ist 7 Uhr 15.'))
        briefing = intents.answer('briefing', NOW, dict(snapshot, operator='Ivan'))
        self.assertTrue(briefing.startswith('Morgen, Ivan.'))
        self.assertHuman(briefing)

    def test_status_keeps_the_facts(self):
        text = status_text(dict(persona='mensch', battery_pct=12, temp_c=80, server='down'))
        self.assertTrue(text.startswith('Lagebericht. Nicht alles rund.'))
        self.assertIn('12 Prozent', text)
        self.assertIn('80 Grad', text)
        self.assertIn('Server nicht erreichbar', text)

    def test_alarms_in_billys_words(self):
        snapshot = dict(battery_pct=9, temp_c=78, load_pct=95)
        for key in alarms.ALARMS:
            self.assertHuman(alarms._phrase(key, snapshot, 'billy'))
        self.assertIn('78 Grad', alarms._phrase('temperature', snapshot, 'billy'))
        self.assertIn('9 Prozent', alarms._battery_phrase(2, 9, 'billy'))
        for phrase in alarms.WAKE_PHRASES['billy']:
            self.assertHuman(phrase)
        self.assertIn(alarms.wake_phrase('billy'), alarms.WAKE_PHRASES['billy'])
        self.assertEqual(alarms.shutdown_text(alarms.SHUTDOWN_NOW, 'off'), alarms.SHUTDOWN_NOW)
        self.assertNotEqual(alarms.shutdown_text(alarms.SHUTDOWN_NOW, 'billy'),
                            alarms.SHUTDOWN_NOW)
        monitor = alarms.AlarmMonitor()
        out = monitor.update(dict(battery_pct=50, battery_plugged=True), 0, lore='billy')
        out += monitor.update(dict(battery_pct=50, battery_plugged=False), 10, lore='billy')
        self.assertEqual(out, ['Kein Netzteil mehr, ich lauf auf Akku. Akku bei 50 Prozent.'])

    def test_memory_and_maintenance(self):
        context = dict(facts=['Ivan mag Kaffee'], directives=[])
        self.assertIn(memory.reply('add_fact', 'x', context, 'billy'),
                      memory.BILLY_REPLIES['add_fact'])
        for _ in range(4):   # every variant keeps the count
            self.assertIn('1 Eintrag', memory.reply('forget', 'kaffee', context, 'billy'))
        self.assertIn('Stick', memory.reply('add_fact', 'x', None, 'billy'))
        self.assertEqual(maintenance.result_text('pi', dict(state='done', upgraded=3), 'billy'),
                         'Pi: Update fertig, 3 Pakete.')
        self.assertEqual(alarms.memory_phrase(True, 4, 'billy'), 'Gedächtnis ist wieder da. 4 Einträge.')


class ControllerTests(unittest.TestCase):
    def test_controller_style_and_wake_phrase(self):
        import ptt
        controller = ptt.VoiceController(Mock(), Mock(), .04, 30)
        controller.persona, controller.lore = 'mensch', 'full'
        self.assertEqual(controller.style, 'billy_full')
        controller.persona = 'servitor'
        self.assertEqual(controller.style, 'full')


if __name__ == '__main__':
    unittest.main()
