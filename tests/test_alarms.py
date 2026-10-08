import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from alarms import AlarmMonitor  # noqa: E402
import ptt  # noqa: E402
from ptt import VoiceController  # noqa: E402
import system_status as ss  # noqa: E402
import wlan  # noqa: E402

OK = dict(battery_pct=80, battery_plugged=False, mem_free_pct=40, swap_used_pct=10,
          temp_c=45, load_pct=20, throttled=0)


class AlarmMonitorTests(unittest.TestCase):
    def test_battery_warns_once_and_repeats_when_critical(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(OK, 0), [])
        low = m.update(dict(OK, battery_pct=14), 10)
        self.assertEqual(low, ["Warnung. Energiespeicher bei 14 Prozent. Netzteil anschließen."])
        self.assertEqual(m.update(dict(OK, battery_pct=13), 20), [])
        self.assertEqual(m.update(dict(OK, battery_pct=6), 200), [])       # within 5 min
        self.assertEqual(len(m.update(dict(OK, battery_pct=5), 320)), 1)    # repeated
        self.assertEqual(m.update(dict(OK, battery_pct=17), 330), [])      # hysteresis
        self.assertEqual(m.active, ['battery'])
        self.assertEqual(m.update(dict(OK, battery_pct=17, battery_plugged=True), 340), [])
        self.assertEqual(m.active, [])

    def test_cpu_needs_a_minute(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(dict(OK, load_pct=95), 0), [])
        self.assertEqual(m.update(dict(OK, load_pct=95), 30), [])
        self.assertEqual(len(m.update(dict(OK, load_pct=95), 61)), 1)
        self.assertEqual(m.update(dict(OK, load_pct=75), 70), [])           # still above off
        self.assertIn('cpu', m.active)
        m.update(dict(OK, load_pct=60), 80)
        self.assertEqual(m.active, [])

    def test_memory_temperature_undervoltage(self):
        m = AlarmMonitor()
        texts = m.update(dict(OK, mem_free_pct=6, temp_c=78, throttled=0x50005), 0)
        self.assertEqual(len(texts), 3)
        self.assertEqual(m.active, ['undervoltage', 'memory', 'temperature'])
        self.assertEqual(m.label(), 'ALARM: Unterspannung')
        self.assertEqual(len(AlarmMonitor().update(dict(OK, swap_used_pct=90), 0)), 1)

    def test_links_need_two_failures_and_announce_recovery(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(OK, 0, network=True, server='down'), [])
        self.assertEqual(m.update(OK, 10, network=True, server='down'),
                         ["Warnung. Verbindung zum Server verloren. Lokaler Betrieb."])
        self.assertEqual(m.update(OK, 20, network=True, server='ok'), ["Server wieder erreichbar."])

    def test_network_loss_does_not_fake_server_recovery(self):
        m = AlarmMonitor()
        m.update(OK, 0, network=True, server='down')
        m.update(OK, 10, network=True, server='down')
        m.update(OK, 20, network=False, server='down')
        texts = m.update(OK, 30, network=False, server='down')
        self.assertEqual(texts, ["Warnung. Netzwerkverbindung verloren. Lokaler Betrieb."])
        self.assertIn('server', m.active)

    def test_wlan_switched_off_is_silent(self):
        m = AlarmMonitor()
        for t in (0, 10, 20):
            self.assertEqual(m.update(OK, t, network=None, server='off'), [])

    def test_lore_full_wording(self):
        text = AlarmMonitor().update(dict(OK, battery_pct=10), 0, lore='full')[0]
        self.assertIn('Die Einheit verlangt Nahrung.', text)


class StatusWlanTests(unittest.TestCase):
    def test_status_mentions_wlan_off(self):
        text = ss.status_text(dict(OK, wlan='off', server='off'))
        self.assertIn('WLAN deaktiviert. Nur lokaler Betrieb.', text)
        self.assertEqual(text.count('lokaler Betrieb'), 1)

    def test_swap_used_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'meminfo'
            path.write_text('SwapTotal: 400000 kB\nSwapFree: 100000 kB\n')
            self.assertEqual(ss._swap_used_percent(path), 75)


class WlanTests(unittest.TestCase):
    def test_blocked_state_from_sysfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, kind, soft in (('rfkill0', 'bluetooth', '1'), ('rfkill1', 'wlan', '0')):
                device = Path(tmp) / name
                device.mkdir()
                (device / 'type').write_text(kind + '\n')
                (device / 'soft').write_text(soft + '\n')
            self.assertFalse(wlan.wlan_blocked(Path(tmp)))
            (Path(tmp) / 'rfkill1' / 'soft').write_text('1\n')
            self.assertTrue(wlan.wlan_blocked(Path(tmp)))
            self.assertIsNone(wlan.wlan_blocked(Path(tmp) / 'missing'))

    def test_set_wlan_command(self):
        run = Mock()
        wlan.set_wlan(False, run=run)
        self.assertEqual(run.call_args.args[0], ['/usr/sbin/rfkill', 'block', 'wlan'])


class ControllerAlarmTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.speech.active = False
        self.speech.synthesizing = False
        self.speech.poll.return_value = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(Path(self.tmp.name) / 'status.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        context = redirect_stdout(StringIO())
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.c = VoiceController(self.recorder, self.speech, .04, 30)
        self.c.battery = dict(percent=10, plugged=False, charging=False)

    def test_alarm_is_spoken_when_idle_and_shown(self):
        self.speech.active = True  # busy: wait
        self.c.check_alarms(0, network=True)
        self.c.tick(False, (False,) * 5, 0.1)
        self.speech.start.assert_not_called()
        self.speech.active = False
        self.c.tick(False, (False,) * 5, 0.2)
        self.assertIn('Energiespeicher bei 10 Prozent', self.speech.start.call_args.args[0])
        status = json.loads((Path(self.tmp.name) / 'status.json').read_text())
        self.assertEqual(status['alarm'], 'battery')

    def test_alarms_switched_off_are_silent_but_tracked(self):
        self.c.alarms_enabled = False
        self.c.check_alarms(0, network=True)
        self.c.tick(False, (False,) * 5, 0.1)
        self.speech.start.assert_not_called()
        self.assertEqual(self.c.alarms.active, ['battery'])

    def test_wlan_switch_disables_uplink(self):
        self.c.remote = True
        with patch('wlan.set_wlan') as radio:
            self.assertTrue(self.c.set_wlan(False))
        radio.assert_called_once_with(False)
        self.assertEqual((self.c.wlan_on, self.recorder.uplink_enabled), (False, False))
        self.assertEqual(self.c.server_state(), 'off')
        with patch('wlan.set_wlan', side_effect=OSError('Operation not permitted')):
            self.assertFalse(self.c.set_wlan(True))
        self.assertFalse(self.c.wlan_on)


if __name__ == '__main__':
    unittest.main()
