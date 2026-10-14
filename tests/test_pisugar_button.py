"""PiSugar 3 function-button event ACK and opt-in software standby."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pisugar_button
import ptt


class FakePiSugarBus:
    def __init__(self, tap=1, fail_at=None, ignore_ack=False):
        self.tap = tap
        self.fail_at = fail_at
        self.ignore_ack = ignore_ack
        self.writes = []
        self.unlocked = False
        self.closed = False

    def read_byte_data(self, address, register):
        assert address == pisugar_button.ADDRESS
        assert register == pisugar_button.TAP_REGISTER
        return self.tap

    def write_byte_data(self, address, register, value):
        assert address == pisugar_button.ADDRESS
        self.writes.append((register, value))
        if self.fail_at == register:
            raise OSError('I2C write failed')
        if register == pisugar_button.WRITE_ENABLE:
            self.unlocked = value == pisugar_button.UNLOCK
        elif register == pisugar_button.TAP_REGISTER:
            if not self.unlocked:
                raise OSError('write protection')
            if not self.ignore_ack:
                self.tap = value
        else:
            raise AssertionError('unexpected PiSugar register write')

    def close(self):
        self.closed = True


class TapReaderTests(unittest.TestCase):
    def test_reads_and_acknowledges_single_tap(self):
        bus = FakePiSugarBus(tap=0xA1)
        reader = pisugar_button.TapReader(bus_factory=lambda: bus)
        self.assertEqual(reader.poll(1), 1)
        self.assertEqual(bus.writes, [(0x0B, 0x29), (0x08, 0xA0), (0x0B, 0)])
        self.assertEqual(bus.tap, 0xA0)
        self.assertTrue(bus.closed)
        self.assertFalse(bus.unlocked)

    def test_no_event_does_not_write(self):
        bus = FakePiSugarBus(0)
        self.assertEqual(pisugar_button.TapReader(bus_factory=lambda: bus).poll(), 0)
        self.assertEqual(bus.writes, [])
        self.assertTrue(bus.closed)

    def test_double_and_long_taps_are_acknowledged_without_standby(self):
        for gesture in (2, 3):
            with self.subTest(gesture=gesture):
                bus = FakePiSugarBus(gesture)
                self.assertEqual(pisugar_button.TapReader(bus_factory=lambda: bus).poll(), gesture)
                self.assertEqual(bus.tap, 0)

    def test_stale_or_bounced_event_is_ignored_after_ack(self):
        buses = [FakePiSugarBus(1), FakePiSugarBus(1)]
        reader = pisugar_button.TapReader(bus_factory=lambda: buses.pop(0))
        self.assertEqual(reader.poll(10), 1)
        self.assertEqual(reader.poll(10.2), 0)
        self.assertFalse(buses)

    def test_failed_ack_relocks_and_rejects_tap(self):
        bus = FakePiSugarBus(1, fail_at=0x08)
        reader = pisugar_button.TapReader(bus_factory=lambda: bus)
        with self.assertRaises(OSError):
            reader.poll(10)
        self.assertEqual(bus.writes[-1], (0x0B, 0))
        self.assertFalse(bus.unlocked)
        self.assertTrue(bus.closed)

    def test_latched_event_not_cleared_does_not_toggle(self):
        bus = FakePiSugarBus(1, ignore_ack=True)
        with self.assertRaises(OSError):
            pisugar_button.TapReader(bus_factory=lambda: bus).poll()
        self.assertFalse(bus.unlocked)


class TapMonitorRecoveryTests(unittest.TestCase):
    def test_stale_startup_tap_is_consumed_without_action(self):
        reader = Mock()
        reader.poll.side_effect = [1, 0, 1]
        monitor = pisugar_button.TapMonitor(reader=reader)
        self.assertEqual(monitor.poll(0), dict(
            state='ready', ignored_startup_gesture=1, recovered_after=0))
        self.assertIsNone(monitor.poll(0.1))
        self.assertEqual(monitor.poll(0.25)['gesture'], 0)
        self.assertEqual(monitor.poll(0.50)['gesture'], 1)

    def test_one_i2c_error_recovers_automatically_without_replay(self):
        reader = Mock()
        reader.poll.side_effect = [0, OSError(121, 'Remote I/O error'), 1, 0]
        monitor = pisugar_button.TapMonitor(reader=reader)
        self.assertEqual(monitor.poll(0)['state'], 'ready')
        error = monitor.poll(0.25)
        self.assertEqual(error['state'], 'error')
        self.assertEqual(error['failures'], 1)
        self.assertEqual(error['retry_seconds'], 1)
        self.assertFalse(error['should_wake'])
        self.assertIsNone(monitor.poll(1.0))
        recovered = monitor.poll(1.25)
        self.assertEqual(recovered, dict(
            state='ok', gesture=1, recovered_after=1))
        self.assertEqual(monitor.poll(1.50)['gesture'], 0)
        self.assertEqual(monitor.failures, 0)

    def test_repeated_errors_trigger_safe_wake_and_continue_retry(self):
        reader = Mock()
        reader.poll.side_effect = [0, OSError(121, 'Remote I/O error'),
                                   OSError(121, 'Remote I/O error'),
                                   OSError(121, 'Remote I/O error'), 1]
        monitor = pisugar_button.TapMonitor(reader=reader)
        self.assertEqual(monitor.poll(0)['state'], 'ready')
        first = monitor.poll(0.25)
        second = monitor.poll(1.25)
        third = monitor.poll(3.25)
        self.assertEqual([r['retry_seconds'] for r in (first, second, third)],
                         [1, 2, 4])
        self.assertFalse(first['should_wake'])
        self.assertFalse(second['should_wake'])
        self.assertTrue(third['should_wake'])
        self.assertIsNone(monitor.poll(4))
        self.assertEqual(monitor.poll(7.25), dict(
            state='ok', gesture=1, recovered_after=3))

    def test_initial_read_failure_does_not_replay_startup_tap(self):
        reader = Mock()
        reader.poll.side_effect = [OSError(121, 'Remote I/O error'), 1, 0, 1]
        monitor = pisugar_button.TapMonitor(reader=reader)
        self.assertEqual(monitor.poll(0)['state'], 'error')
        resumed = monitor.poll(1)
        self.assertEqual(resumed['state'], 'ready')
        self.assertEqual(resumed['ignored_startup_gesture'], 1)
        self.assertEqual(resumed['recovered_after'], 1)
        self.assertEqual(monitor.poll(1.25)['gesture'], 0)
        self.assertEqual(monitor.poll(1.50)['gesture'], 1)

    def test_max_backoff_is_bounded(self):
        reader = Mock()
        reader.poll.side_effect = [OSError(121, 'Remote I/O error')] * 12
        monitor = pisugar_button.TapMonitor(reader=reader)
        now = 0
        for _ in range(12):
            result = monitor.poll(now)
            self.assertLessEqual(result['retry_seconds'], 30)
            now += result['retry_seconds']
        self.assertTrue(result['should_wake'])
        self.assertEqual(result['retry_seconds'], 30)


class ManualStandbyTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(base / 'display.json'),
            'PTT_DISPLAY_EVENT_PATH': str(base / 'display-event.json'),
            'PTT_SETTINGS_FILE': str(base / 'settings.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        recorder = Mock()
        recorder.process = None
        speech = Mock()
        speech.active = False
        speech.synthesizing = False
        speech.poll.return_value = None
        self.controller = ptt.VoiceController(recorder, speech, 0.04, 30, remote=True)

    def test_toggle_keeps_wifi_and_does_not_greet(self):
        c = self.controller
        c.sleep_wlan_off = True   # explicit manual standby overrides auto-sleep WLAN policy
        c.sleep_after = 999999
        c.rest_after = 999999
        c.last_activity = now = time.monotonic()
        with (patch.object(c, '_release_microphone') as release,
              patch.object(c.board_leds, 'off', return_value=None) as off,
              patch.object(c.board_leds, 'restore') as restore,
              patch.object(c, 'set_wlan') as wifi,
              patch.object(c, '_greet') as greet):
            self.assertTrue(c.pisugar_standby(now))
            self.assertTrue(c.manual_standby)
            self.assertEqual(c.power, 'sleep')
            release.assert_called_once()
            off.assert_called_once()
            wifi.assert_not_called()
            c.tick(False, (False,) * 5, now + 1)
            self.assertTrue(c.manual_standby)
            self.assertTrue(c.pisugar_standby(now + 2))
            self.assertFalse(c.manual_standby)
            self.assertEqual(c.power, 'awake')
            restore.assert_called_once()
            wifi.assert_not_called()
            greet.assert_not_called()

    def journal(self):
        import json
        return [json.loads(line) for line in self.output.getvalue().splitlines()
                if line.startswith('{')]

    def tap(self, c):
        from contextlib import redirect_stdout
        from io import StringIO
        self.output = StringIO()
        with redirect_stdout(self.output), patch.object(c, '_release_microphone'):
            return c.pisugar_standby(time.monotonic())

    def test_ordinary_speech_is_stopped_and_standby_follows(self):
        # Hardware test 14.10.2026: taps during long status or maintenance
        # announcements were refused ("busy_or_critical").
        c = self.controller
        c.speech.active = True
        c.alarm_queue = ['Netzbetrieb. Energiespeicher 80 Prozent.']
        self.assertTrue(self.tap(c))
        self.assertTrue(c.manual_standby)
        c.speech.stop.assert_called()
        self.assertEqual(c.alarm_queue, [])
        entry = [e for e in self.journal() if e.get('event') == 'pisugar_standby'][-1]
        self.assertEqual((entry['state'], entry['interrupted']), ('sleep', True))

    def test_recording_and_open_menu_are_stopped_too(self):
        c = self.controller
        c.recorder.process = Mock()
        c.menu.show(0)
        self.assertTrue(self.tap(c))
        self.assertTrue(c.manual_standby)
        c.recorder.finish.assert_called()
        self.assertFalse(c.menu.open)

    def test_pending_shutdown_cannot_be_hidden(self):
        c = self.controller
        c.device_pending = 'shutdown'
        self.assertFalse(self.tap(c))
        self.assertEqual(c.power, 'awake')
        self.assertEqual(self.journal()[-1]['reason'], 'power_action')

    def test_critical_alarm_maintenance_and_enrollment_keep_it_awake(self):
        c = self.controller
        battery = c.alarms.states['battery']
        for setup, reason in (
                (lambda: setattr(battery, 'active', True), 'critical_alarm'),
                (lambda: setattr(c.maint, 'active', True), 'maintenance'),
                (lambda: setattr(c, 'enroll', Mock(running=True)), 'enrollment')):
            with self.subTest(reason=reason):
                c.speech.active = True
                setup()
                self.assertFalse(self.tap(c))
                self.assertFalse(c.manual_standby)
                self.assertEqual(self.journal()[-1]['reason'], reason)
                c.speech.stop.assert_not_called()              # nothing was cut off
                battery.active = False
                c.maint.active = False
                c.enroll = None


if __name__ == '__main__':
    unittest.main()
