import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import device_control as dc  # noqa: E402


class CommandTests(unittest.TestCase):
    def test_commands(self):
        cases = {
            'schalte das wlan aus': 'wlan_off', 'wlan ausschalten': 'wlan_off',
            'deaktiviere das wlan': 'wlan_off', 'wlan an': 'wlan_on',
            'mach das wlan wieder an': 'wlan_on', 'wlan einschalten': 'wlan_on',
            'fahr dich herunter': 'shutdown', 'herunterfahren': 'shutdown',
            'runter fahren': 'shutdown', 'schalt dich aus': 'shutdown',
            'starte dich neu': 'reboot', 'neustart': 'reboot',
            'terminiere dich selbst': 'shutdown', 'geh sterben': 'shutdown',
            'zerstöre dich': 'shutdown', 'mach dich aus': 'shutdown',
            'schalte dich aus': 'shutdown',
            'geh schlafen': 'sleep', 'versetze dich in den ruhemodus': 'sleep',
            'energiesparmodus': 'sleep', 'schlafmodus': 'sleep',
        }
        for text, op in cases.items():
            with self.subTest(text=text):
                self.assertEqual(dc.command(text), op)

    def test_not_commands(self):
        for text in ('wie ist das wlan', 'wie ist das wetter', 'starte den server neu',
                     'schalte das licht aus', 'fahr nach hause', 'starte die musik', '',
                     'zerstöre den server', 'wie lange soll ich schlafen',
                     'erzähl mir wie man einen computer herunterfährt und wieder startet bitte'):
            with self.subTest(text=text):
                self.assertIsNone(dc.command(text))

    def test_answers_must_be_the_whole_utterance(self):
        for text in ('bestätigt', 'ja', 'ja bitte', 'bestätigt proximus', 'ich bestätige',
                     'mach das'):
            with self.subTest(text=text):
                self.assertEqual(dc.answer(text), 'confirm')
        for text in ('nein', 'abbrechen', 'doch nicht'):
            with self.subTest(text=text):
                self.assertEqual(dc.answer(text), 'cancel')
        for text in ('mach das licht an', 'wie spät ist es', 'ja wie spät ist es', ''):
            with self.subTest(text=text):
                self.assertIsNone(dc.answer(text))


class TextTests(unittest.TestCase):
    def test_reply(self):
        self.assertIn('Bestätigen', dc.reply('shutdown'))
        self.assertIn('Schalter', dc.reply('shutdown'))
        self.assertEqual(dc.reply('wlan_off', 'on'), "WLAN deaktiviert. Lokaler Betrieb.")
        self.assertEqual(dc.reply('wlan_off', 'off'), "WLAN ist bereits deaktiviert.")
        self.assertEqual(dc.reply('wlan_on', 'on'), "WLAN ist bereits aktiv.")

    def test_styles(self):
        self.assertIn('Omnissiah', dc.start_text('shutdown', 'full'))
        self.assertEqual(dc.start_text('wlan_off', 'full'), dc.START['wlan_off'])
        for op in dc.OPS:
            self.assertNotEqual(dc.start_text(op, 'billy'), dc.start_text(op, 'off'))
        self.assertEqual(dc.button_only_text('shutdown'),
                         "Herunterfahren angefordert. Stimme nicht erkannt. "
                         "Bestätigen nur mit Taste E.")
        self.assertIn('drück E', dc.button_only_text('reboot', 'billy'))
        self.assertTrue(dc.auto_wlan_text('off', retry=True).endswith('wiederholen.'))


if __name__ == '__main__':
    unittest.main()
