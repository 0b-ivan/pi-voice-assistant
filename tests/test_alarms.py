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
    def test_three_battery_warnings_then_shutdown(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(OK, 0), [])
        self.assertEqual(m.update(dict(OK, battery_pct=15), 10),
                         ["Warnung 1 von 3. Energiespeicher bei 15 Prozent. Netzteil anschließen."])
        self.assertEqual(m.update(dict(OK, battery_pct=13), 20), [])
        self.assertTrue(m.update(dict(OK, battery_pct=10), 30)[0].startswith("Warnung 2 von 3."))
        last = m.update(dict(OK, battery_pct=6), 40)
        self.assertTrue(last[0].startswith("Letzte Warnung."))
        self.assertIn("Herunterfahren in 60 Sekunden", last[0])
        self.assertFalse(m.shutdown_due(99))
        self.assertTrue(m.shutdown_due(100))

    def test_charger_cancels_shutdown_and_announces_source(self):
        m = AlarmMonitor()
        m.update(dict(OK, battery_pct=16), 0)
        for t, pct in ((10, 15), (20, 10), (30, 6)):
            m.update(dict(OK, battery_pct=pct), t)
        texts = m.update(dict(OK, battery_pct=6, battery_plugged=True), 50)
        self.assertEqual(texts, ["Netzbetrieb. Energiespeicher 6 Prozent.",
                                 "Herunterfahren abgebrochen."])
        self.assertFalse(m.shutdown_due(1000))
        self.assertEqual(m.update(dict(OK, battery_pct=7, battery_plugged=False), 60),
                         ["Akkubetrieb. Energiespeicher 7 Prozent.",
                          "Warnung 2 von 3. Energiespeicher bei 7 Prozent. Netzteil anschließen."])

    def test_big_jump_gives_one_warning_per_check(self):
        m = AlarmMonitor()
        texts = m.update(dict(OK, battery_pct=5), 0)
        self.assertEqual(len(texts), 1)  # straight to the last stage, one sentence
        self.assertTrue(texts[0].startswith("Letzte Warnung."))
        self.assertTrue(m.shutdown_due(60))

    def test_first_reading_sets_source_silently(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(dict(OK, battery_plugged=True), 0), [])
        self.assertEqual(m.update(dict(OK, battery_plugged=False), 10),
                         ["Akkubetrieb. Energiespeicher 80 Prozent."])

    def test_internet_loss_and_recovery(self):
        m = AlarmMonitor()
        self.assertEqual(m.update(OK, 0, network=True, internet=False), [])
        self.assertEqual(m.update(OK, 10, network=True, internet=False),
                         ["Warnung. Internetverbindung verloren. Antworten nur noch lokal."])
        self.assertEqual(m.update(OK, 20, network=True, internet=True),
                         ["Internetverbindung wiederhergestellt."])

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
        self.c.speech.active = False

    def test_alarm_is_spoken_when_idle_and_shown(self):
        self.speech.active = True  # busy: wait
        self.c.check_alarms(0, network=True)
        self.c.tick(False, (False,) * 5, 0.1)
        self.speech.start.assert_not_called()
        self.speech.active = False
        self.c.tick(False, (False,) * 5, 0.2)
        self.assertIn('Energiespeicher bei 10 Prozent', self.speech.start.call_args.args[0])
        self.assertIn('Warnung 2 von 3', self.speech.start.call_args.args[0])
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

    def test_no_link_alarms_while_wlan_reconnects(self):
        self.c.battery = None
        self.c.server_state = lambda: 'down'
        self.c.internet_probe = Mock(state=False)
        with patch('wlan.set_wlan'), patch('ptt.time.monotonic', return_value=1000.0):
            self.assertTrue(self.c.set_wlan(True))
        for now in (1010.0, 1020.0, 1030.0, 1050.0):
            self.c.check_alarms(now, network=False)
        self.assertEqual(self.c.alarms.active, [])
        for now in (1070.0, 1080.0):
            self.c.check_alarms(now, network=True)
        self.assertEqual(self.c.alarms.active, ['internet', 'server'])


class PowerStageTests(ControllerAlarmTests):
    def setUp(self):
        super().setUp()
        self.c.rest_after, self.c.sleep_after = 30, 600
        self.c.last_activity = 0.0

    def status(self):
        return json.loads((Path(self.tmp.name) / 'status.json').read_text())

    def test_idle_rests_then_sleeps_and_activity_wakes(self):
        self.c._update_power(10)
        self.assertEqual(self.c.power, 'awake')
        self.c._update_power(31)
        self.assertEqual((self.c.power, self.status()['power']), ('rest', 'rest'))
        self.assertEqual(self.c.color, self.c._scale(ptt.LED_READY, 0.3))
        self.c._update_power(601)
        self.assertEqual((self.c.power, self.status()['power']), ('sleep', 'sleep'))
        self.assertEqual(self.c.color, ptt.LED_OFF)
        self.speech.active = True          # e.g. an alarm or the wake word's reply
        self.c._update_power(700)
        self.assertEqual(self.c.power, 'awake')

    def test_first_display_button_only_wakes(self):
        self.c._update_power(601)
        for now, pressed in ((601.8, False), (601.9, False), (602.0, True), (602.1, True)):
            self.c._pitft_input((pressed, False), now)
        self.assertEqual(self.c.power, 'awake')
        self.assertFalse(self.c.menu.open)

    def test_sleep_can_switch_wlan_off_and_back_on(self):
        self.c.sleep_wlan_off = True
        with patch('wlan.set_wlan') as radio:
            self.c._update_power(601)
            self.assertFalse(self.c.wlan_on)
            self.c._wake_up(650)
            self.assertTrue(self.c.wlan_on)
        self.assertEqual([call.args for call in radio.call_args_list], [(False,), (True,)])

    def test_wlan_switched_off_by_user_stays_off_after_sleep(self):
        self.c.sleep_wlan_off = True
        self.c.wlan_on = False
        with patch('wlan.set_wlan') as radio:
            self.c._update_power(601)
            self.c._wake_up(650)
        radio.assert_not_called()
        self.assertFalse(self.c.wlan_on)


class ShutdownAndModeTests(ControllerAlarmTests):
    def test_battery_shutdown_powers_off(self):
        for t, pct in ((0, 15), (10, 10), (20, 6)):
            self.c.battery = dict(percent=pct, plugged=False, charging=False)
            self.c.check_alarms(t, network=True)
        with patch('ptt.subprocess.run') as run, patch('ptt.time.sleep'):
            self.c.check_alarms(81, network=True)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'poweroff'])
        self.assertTrue(self.c.shutting_down)

    def test_failed_poweroff_is_reported(self):
        import subprocess
        self.c.alarms.shutdown_at = 0
        with patch('ptt.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'x')), \
                patch('ptt.time.sleep'):
            self.c.check_alarms(1, network=True)
        self.assertFalse(self.c.shutting_down)
        self.assertIn('Herunterfahren nicht möglich. Bitte manuell ausschalten.', self.c.alarm_queue)

    def test_local_llm_mode_never_calls_openrouter_on_the_pi(self):
        self.c.llm_mode = 'local'
        with patch('ptt.TranscriptionJob') as job:
            self.c._start_llm('wie hoch ist der eiffelturm')
        job.assert_not_called()
        self.assertIn('Lokaler Sprachkern nicht erreichbar', self.speech.start.call_args.args[0])
        self.assertEqual(self.c.status_snapshot()['llm_mode'], 'local')


if __name__ == '__main__':
    unittest.main()
