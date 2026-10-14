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

    def test_busy_controller_cannot_enter_standby(self):
        c = self.controller
        c.recorder.process = Mock()  # recording / active request is never interrupted
        self.assertFalse(c.pisugar_standby(time.monotonic()))
        self.assertFalse(c.manual_standby)

    def test_pending_shutdown_cannot_be_hidden(self):
        c = self.controller
        c.device_pending = 'shutdown'
        self.assertFalse(c.pisugar_standby(time.monotonic()))
        self.assertEqual(c.power, 'awake')


if __name__ == '__main__':
    unittest.main()
