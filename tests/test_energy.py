"""Energy voice/menu integration and optional read-only PiSugar protocol."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import alarms
import device_control
import energy
import intents
import pisugar_client
import power
import system_status
import ptt


class DirectPiSugarTests(unittest.TestCase):
    class FakeBus:
        values = {0x22: 0x0F, 0x23: 0xA0, 0x2A: 80,
                  0x02: 0x90, 0x03: 0x10, 0x04: 70,
                  0x06: 0x80, 0x20: 0x00, 0x26: 0x01, 0x27: 0xF4}

        def __init__(self, failed=()):
            self.failed = failed

        def read_byte_data(self, address, register):
            if register in self.failed:
                raise OSError('optional I2C register unavailable')
            return self.values[register]

        def close(self):
            pass

    def test_direct_config_flags_are_reported_without_vendor_daemon(self):
        reading = power.Battery(bus_factory=self.FakeBus).read()
        self.assertEqual(reading['percent'], 80)
        self.assertEqual(reading['mv'], 4000)
        self.assertTrue(reading['soft_poweroff'])
        self.assertTrue(reading['watchdog'])
        self.assertFalse(reading['charge_protection'])
        self.assertTrue(reading['auto_power_on'])
        self.assertEqual(reading['output_current_ma'], 500)
        snapshot = system_status.collect_snapshot(battery=reading)
        self.assertEqual(snapshot['pisugar_soft_poweroff'], 'on')
        self.assertEqual(snapshot['pisugar_watchdog'], 'on')
        self.assertEqual(snapshot['output_current_ma'], 500)
        self.assertIn('Ausgangsstrom 500 Milliampere', energy.detail(snapshot))
        self.assertEqual(snapshot['pisugar_charge_protection'], 'off')
        self.assertEqual(snapshot['pisugar_auto_power_on'], 'on')
        info = energy.config(snapshot)
        self.assertIn('Akku Eingangsschutz deaktiviert', info)
        self.assertIn('Hardware Watchdog aktiv', info)

    def test_auxiliary_i2c_failure_does_not_hide_core_battery_reading(self):
        reading = power.Battery(bus_factory=lambda: self.FakeBus(
            failed=(0x03, 0x06, 0x20))).read()
        self.assertEqual(reading['percent'], 80)
        self.assertNotIn('soft_poweroff', reading)
        self.assertNotIn('watchdog', reading)
        self.assertNotIn('charge_protection', reading)
        self.assertEqual(reading['output_current_ma'], 500)


class EnergyTextTests(unittest.TestCase):
    def test_direct_offline_questions(self):
        self.assertEqual(intents.match('wie hoch ist die Batteriespannung'), 'power_detail')
        self.assertEqual(intents.match('was meldet PiSugar'), 'power_detail')
        self.assertEqual(intents.match('wie viel strom verbrauchst du'), 'power_current')
        self.assertEqual(intents.answer('power_current', None, dict(output_current_ma=500)),
                         'Ausgangsstrom 500 Milliampere. Kein gemessener Akkustrom.')
        self.assertEqual(intents.match('Ist Soft Poweroff aktiv'), 'power_config')
        self.assertEqual(intents.match('PiSugar Konfiguration'), 'power_config')
        self.assertEqual(intents.match('wie lange hält der Akku'), 'power_runtime')
        self.assertEqual(intents.match('wie hoch ist der akkustand'), 'battery')

    def test_snapshot_never_invents_telemetry(self):
        self.assertIn('nicht verfügbar', energy.detail({}))
        text = energy.detail(dict(battery_pct=82, battery_plugged=True,
                                  battery_charging=False, battery_mv=4110,
                                  pisugar_chip_c=37))
        self.assertIn('82 Prozent', text)
        self.assertIn('4,11 Volt', text)
        self.assertIn('Chiptemperatur 37 Grad', text)
        self.assertIn('nicht aktiv', text)
        self.assertNotIn('Restlaufzeit', text)
        self.assertIn('nicht zuverlässig', energy.runtime(dict(battery_pct=80)))
        self.assertIn('nicht verfügbar', energy.current({}))

    def test_manager_config_missing_or_valid(self):
        self.assertIn('nicht verfügbar', energy.config({}))
        text = energy.config(dict(charge_restart_pct=70, charge_limit_pct=80,
                                  safe_shutdown_pct=3, safe_shutdown_delay_s=30,
                                  pisugar_soft_poweroff='off', pisugar_rtc_alarm='on'))
        self.assertIn('70 bis 80 Prozent', text)
        self.assertIn('Soft Poweroff ist deaktiviert', text)
        self.assertIn('RTC Weckalarm aktiv', text)
        self.assertIn('Power Manager Abschaltschwelle', text)
        self.assertIn('Ladefenster', intents.answer('power_config', None,
                      dict(charge_restart_pct=70, charge_limit_pct=80)))

    def test_voice_commands_must_be_explicit(self):
        self.assertEqual(device_control.command('schalte die energiehinweise aus'),
                         'power_notices_off')
        self.assertEqual(device_control.command('energiehinweise an'),
                         'power_notices_on')
        self.assertEqual(device_control.command('schalte stromhinweise aus'),
                         'power_notices_off')
        self.assertEqual(device_control.command('strom hinweise an'),
                         'power_notices_on')
        self.assertIsNone(device_control.command('wie schalte ich energiehinweise aus'))
        self.assertIsNone(device_control.command('energiehinweise'))
        self.assertIsNone(device_control.command('wie wird eine Batterie geladen'))
        self.assertEqual(device_control.command('fahr dich herunter'), 'shutdown')


class SuppressionTests(unittest.TestCase):
    def test_only_power_source_announcements_are_suppressed(self):
        source = dict(battery_pct=80, battery_plugged=True)
        monitor = alarms.AlarmMonitor()
        self.assertEqual(monitor.update(source, 0, power_source_announcements=False), [])
        self.assertEqual(monitor.update(dict(source, battery_plugged=False), 10,
                                        power_source_announcements=False), [])
        warning = monitor.update(dict(source, battery_pct=6, battery_plugged=False),
                                 20, power_source_announcements=False)
        self.assertEqual(len(warning), 1)
        self.assertIn('Herunterfahren', warning[0])
        self.assertTrue(monitor.shutdown_due(80))
        cancelled = monitor.update(dict(source, battery_pct=6), 30,
                                   power_source_announcements=False)
        self.assertEqual(cancelled, [alarms.SHUTDOWN_CANCELLED])
        self.assertFalse(monitor.shutdown_due(999))


class FakeSocket:
    def __init__(self, answers, requests):
        self.answers, self.requests = answers, requests
        self.command = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        pass

    def settimeout(self, seconds):
        self.timeout = seconds

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.command = data.decode().strip()
        self.requests.append(self.command)

    def recv(self, size):
        key = self.command.partition(' ')[-1]
        value = self.answers.get(key)
        return (f'{key}: {value}\n' if value is not None else '').encode()


class PiSugarClientTests(unittest.TestCase):
    ANSWERS = dict(model='PiSugar 3', firmware_version='1.0.6',
                   battery_charging_range='70,80', safe_shutdown_level='3',
                   safe_shutdown_delay='30', soft_poweroff='true',
                   rtc_alarm_enabled='false', rtc_alarm_time='2026-10-12T16:00:00+02:00')

    def test_validated_readonly_firmware_and_settings(self):
        requests = []
        client = pisugar_client.Client('/run/pisugar-server.sock')
        with patch.object(pisugar_client.socket, 'socket',
                          side_effect=lambda *_: FakeSocket(self.ANSWERS, requests)):
            state = client.read()
        self.assertEqual(state, dict(
            pisugar_firmware='1.0.6', charge_restart_pct=70, charge_limit_pct=80,
            safe_shutdown_pct=3, safe_shutdown_delay_s=30,
            pisugar_soft_poweroff='on', pisugar_rtc_alarm='off'))
        self.assertTrue(all(request.startswith('get ') for request in requests))
        with self.assertRaises(ValueError):
            client.get('set_battery_output')

    def test_other_models_and_bad_values_rejected(self):
        requests = []
        with patch.object(pisugar_client.socket, 'socket',
                          side_effect=lambda *_: FakeSocket({'model': 'PiSugar 2'}, requests)):
            self.assertEqual(pisugar_client.Client('/run/test.sock').read(), {})
        bad = dict(self.ANSWERS, firmware_version='../../etc/shadow',
                   battery_charging_range='[98, 10]',
                   safe_shutdown_level='300', soft_poweroff='maybe')
        with patch.object(pisugar_client.socket, 'socket',
                          side_effect=lambda *_: FakeSocket(bad, requests)):
            state = pisugar_client.Client('/run/test.sock').read()
        self.assertNotIn('pisugar_firmware', state)
        self.assertNotIn('charge_limit_pct', state)
        self.assertNotIn('safe_shutdown_pct', state)
        self.assertNotIn('pisugar_soft_poweroff', state)

    def test_absent_socket_is_noop(self):
        self.assertEqual(pisugar_client.Client(path='').read(), {})
        poll = pisugar_client.Poller(client=pisugar_client.Client(path=''))
        poll.start()
        self.assertEqual(poll.snapshot(), {})
        poll.close()


class ControllerEnergyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(root / 'display.json'),
            'PTT_DISPLAY_EVENT_PATH': str(root / 'event.json'),
            'PTT_SETTINGS_FILE': str(root / 'settings.json'),
        })
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        recorder, speech = Mock(), Mock()
        recorder.process = None
        recorder.take_live_transcript.return_value = None
        speech.active = False
        speech.synthesizing = False
        speech.poll.return_value = None
        self.controller = ptt.VoiceController(recorder, speech, 0.04, 30, remote=True)
        self.settings = root / 'settings.json'

    def test_menu_and_voice_change_only_optional_notices(self):
        c = self.controller
        self.assertFalse(c.power_notices)  # opt-in; not an emergency mute
        c.menu.select('power_notices')
        c._menu_confirm(1.0)
        self.assertTrue(c.power_notices)
        self.assertEqual(json.loads(self.settings.read_text())['power_notices'], 'on')
        self.assertEqual(c.status_snapshot()['power_notices'], 'on')
        c._device_command('power_notices_off')
        self.assertFalse(c.power_notices)
        self.assertEqual(json.loads(self.settings.read_text())['power_notices'], 'off')

    def test_remote_command_matches_local_state(self):
        c = self.controller
        c._device_event(dict(op='power_notices_on'))
        self.assertTrue(c.power_notices)
        c._device_event(dict(op='power_notices_off'))
        self.assertFalse(c.power_notices)

    def test_menu_shutdown_uses_existing_confirmation(self):
        c = self.controller
        c.menu.select('energy_shutdown')
        with patch.object(c, '_say') as say:
            c._menu_confirm(1.0)
        self.assertEqual(c.device_pending, 'shutdown')
        self.assertIn('Bestätigen', say.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
