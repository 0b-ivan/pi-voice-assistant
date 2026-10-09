"""Spoken device commands in the controller: WLAN, reboot, shutdown."""
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import device_control  # noqa: E402
import ptt  # noqa: E402
from ptt import VoiceController  # noqa: E402


class DeviceCommandTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.recorder.take_live_transcript.return_value = None
        self.speech.active = False
        self.speech.poll.return_value = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(Path(self.tmp.name) / 'status.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json'),
            'PTT_SETTINGS_FILE': str(Path(self.tmp.name) / 'settings.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        self.c = VoiceController(self.recorder, self.speech, .04, 30, remote=True)
        self.spoken = []
        self.c._start_speech = lambda text, **fields: self.spoken.append(text)
        self.c._say = lambda text: self.spoken.append(text)
        self.wlan = patch('ptt.wlan_radio.set_wlan').start()
        self.addCleanup(patch.stopall)
        out = redirect_stdout(StringIO())
        out.__enter__()
        self.addCleanup(out.__exit__, None, None, None)

    now = 0.0

    def press(self, name):
        """Release, press and release a SHIM button (as in test_menu)."""
        for down in ('', name, name, ''):
            self.now += .1
            self.c.tick(False, tuple(x in down for x in 'ABCDE'), self.now)
            if not down:
                self.now += .1
                self.c.tick(False, (False,) * 5, self.now)

    def turn(self, text):
        """A spoken turn on the local path: submit takes over the pending question."""
        self.c.turn_device_pending = self.c._device_take_pending()
        self.c._start_llm(text)

    def test_wlan_off_and_on(self):
        self.turn('schalte das wlan aus')
        self.wlan.assert_called_once_with(False)
        self.assertFalse(self.c.wlan_on)
        self.assertEqual(self.spoken[-1], "WLAN deaktiviert. Lokaler Betrieb.")
        self.turn('wlan aus')
        self.assertEqual(self.wlan.call_count, 1)            # already off: nothing to do
        self.assertEqual(self.spoken[-1], "WLAN ist bereits deaktiviert.")
        self.turn('wlan einschalten')
        self.wlan.assert_called_with(True)
        self.assertTrue(self.c.wlan_on)

    def test_wlan_failure_is_reported(self):
        self.wlan.side_effect = OSError('no rfkill permission')
        self.turn('wlan aus')
        self.assertTrue(self.c.wlan_on)
        self.assertEqual(self.spoken[-1], device_control.FAILED)

    def test_shutdown_needs_spoken_confirmation(self):
        self.turn('fahr dich herunter')
        self.assertEqual(self.c.device_pending, 'shutdown')
        self.assertIn('Bestätigen', self.spoken[-1])
        self.assertEqual(self.c.status_snapshot()['pending'], 'shutdown')
        self.turn('bestätigt')
        self.assertEqual(self.spoken[-1], "Einheit fährt herunter.")
        self.assertEqual(self.c.device_after_speech, 'shutdown')
        with patch('ptt.subprocess.run') as run:
            self.speech.poll.return_value = 0      # announcement finished
            self.c.tick(False, (False,) * 5, 100.0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'poweroff'])

    def test_b_during_the_announcement_stops_it(self):
        self.turn('herunterfahren')
        self.turn('bestätigt')
        self.assertEqual(self.c.device_after_speech, 'shutdown')
        self.press('B')                       # cuts off "Einheit fährt herunter."
        with patch('ptt.subprocess.run') as run:
            self.speech.poll.return_value = -15
            self.press('')
        run.assert_not_called()

    def test_a_stale_confirmation_never_fires_later(self):
        self.turn('herunterfahren')
        self.turn('bestätigt')
        self.c.device_after_until = 0.0       # the announcement never played
        with patch('ptt.subprocess.run') as run:
            self.speech.poll.return_value = 0  # some later speech ends
            self.press('')
        run.assert_not_called()
        self.assertIsNone(self.c.device_after_speech)

    def test_anything_else_drops_the_question(self):
        self.turn('starte dich neu')
        with patch.object(self.c, '_memory_command', return_value=None), \
                patch('ptt.intents.match', return_value='time'), \
                patch('ptt.intents.answer', return_value='Zeitindex.'):
            self.turn('wie spät ist es')
        self.assertEqual(self.spoken[-1], 'Zeitindex.')
        self.assertIsNone(self.c.device_pending)
        self.turn('bestätigt')                    # too late: nothing pending any more
        self.assertIsNone(self.c.device_after_speech)

    def test_cancel_and_expiry(self):
        self.turn('neustart')
        self.turn('abbrechen')
        self.assertEqual(self.spoken[-1], "Abgebrochen.")
        self.assertIsNone(self.c.device_after_speech)
        self.turn('neustart')
        self.c.device_pending_until = 0.0         # 20 s passed
        self.assertIsNone(self.c.status_snapshot().get('pending'))
        self.turn('bestätigt')
        self.assertIsNone(self.c.device_after_speech)

    def test_button_e_confirms_and_b_cancels(self):
        self.turn('neustart')
        with patch('ptt.maintenance.installed', return_value=True), \
                patch('ptt.maintenance.request') as request:
            self.press('E')
        request.assert_called_once_with('reboot')       # via the root maintenance worker
        self.assertEqual(self.spoken[-1], "Neustart eingeleitet.")
        self.turn('herunterfahren')
        self.press('B')
        self.assertEqual(self.spoken[-1], "Abgebrochen.")
        self.assertIsNone(self.c.device_pending)

    def test_maintenance_mode_keeps_its_own_reboot(self):
        self.c.maint.enter()
        self.turn('starte neu')
        self.assertIsNone(self.c.device_pending)
        self.assertEqual(self.c.maint.pending, 'reboot_pi')

    def test_server_events(self):
        self.c._remote_progress(dict(event='device', op='reboot'))
        self.assertEqual(self.c.device_pending, 'reboot')
        self.c.turn_device_pending = self.c._device_take_pending()
        self.c._remote_progress(dict(event='device', op='shutdown', confirm=True))
        self.assertIsNone(self.c.device_after_speech)   # not what this Pi asked
        self.c.turn_device_pending = 'reboot'
        self.c._remote_progress(dict(event='device', op='reboot', confirm=True))
        self.assertEqual(self.c.device_after_speech, 'reboot')
        self.c._remote_progress(dict(event='device', op='wlan_off'))
        self.wlan.assert_called_once_with(False)
        self.c._remote_progress(dict(event='device', op='format_disk'))   # unknown: ignored

    def test_wlan_comes_back_for_a_question_that_needs_the_network(self):
        self.c.wlan_on = False
        with patch.object(self.c, '_memory_command', return_value=None), \
                patch('ptt.intents.match', return_value=None), \
                patch('ptt.TranscriptionJob') as job:
            self.c._start_llm('wie hoch ist der eiffelturm')
        self.wlan.assert_called_once_with(True)
        self.assertEqual(self.spoken[-1], device_control.AUTO_WLAN)
        function = job.call_args.args[0]
        self.assertIs(function.func, ptt._after_network)

    def test_local_language_core_asks_to_repeat(self):
        self.c.wlan_on = False
        self.c.llm_mode = 'local'
        with patch.object(self.c, '_memory_command', return_value=None), \
                patch('ptt.intents.match', return_value=None):
            self.c._start_llm('wie hoch ist der eiffelturm')
        self.assertEqual(self.spoken[-1], device_control.AUTO_WLAN_RETRY)

    def test_after_network_waits_for_an_address(self):
        states = iter([False, False, True])
        with patch('ptt.network_up', side_effect=lambda: next(states)), \
                patch('ptt.time.sleep') as sleep:
            self.assertEqual(ptt._after_network(str.upper, 'frage'), 'FRAGE')
        self.assertEqual(sleep.call_count, 2)


if __name__ == '__main__':
    unittest.main()
