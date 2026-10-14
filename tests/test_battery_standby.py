"""Reversible CPU/radio battery-only power policy; no hardware access in CI."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from battery_standby import Supervisor, UNIT

spec = importlib.util.spec_from_file_location(
    'root_standby', ROOT / 'scripts' / 'proximus-battery-standby.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RootHelperTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.cpu = self.root / 'cpufreq'
        self.rfkill = self.root / 'rfkill'
        self.cpu.mkdir()
        self.rfkill.mkdir()
        self.state = self.root / 'state.json'
        self.patches = [
            patch.object(helper, 'CPU_ROOT', self.cpu),
            patch.object(helper, 'RFKILL_ROOT', self.rfkill),
            patch.object(helper, 'STATE', self.state),
            patch.object(helper, 'battery_plugged', return_value=False),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.commands = []

        def command(*args):
            self.commands.append(args)
            if args[0] == helper.RFKILL:
                kind = args[2]
                for folder in self.rfkill.glob('rfkill*'):
                    if (folder / 'type').read_text().strip() == kind:
                        (folder / 'soft').write_text('1' if args[1] == 'block' else '0')
        self.command_patch = patch.object(helper, 'command', side_effect=command)
        self.command_patch.start()
        self.addCleanup(self.command_patch.stop)
        self.env_patch = patch.dict(os.environ, {
            'PROXIMUS_SAVE_CPU': 'powersave',
            'PROXIMUS_SAVE_WLAN': 'keep',
            'PROXIMUS_SAVE_BLUETOOTH': 'keep',
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def cpu_policy(self, governor='ondemand', available='ondemand powersave'):
        policy = self.cpu / 'policy0'
        policy.mkdir()
        (policy / 'scaling_governor').write_text(governor)
        (policy / 'scaling_available_governors').write_text(available)
        return policy

    def radio(self, name, kind, soft='0'):
        dev = self.rfkill / name
        dev.mkdir()
        (dev / 'type').write_text(kind)
        (dev / 'soft').write_text(soft)
        return dev

    def test_cpu_powersave_uses_available_governor_and_restores(self):
        policy = self.cpu_policy()
        helper.enter()
        self.assertEqual((policy / 'scaling_governor').read_text().strip(), 'powersave')
        self.assertTrue(self.state.exists())
        helper.restore()
        self.assertEqual((policy / 'scaling_governor').read_text().strip(), 'ondemand')
        self.assertFalse(self.state.exists())

    def test_no_hardcoded_cpu_frequency_or_unsupported_governor(self):
        policy = self.cpu_policy(available='ondemand performance')
        helper.enter()
        self.assertEqual((policy / 'scaling_governor').read_text(), 'ondemand')
        self.assertFalse(self.state.exists())

    def test_mains_and_missing_telemetry_do_not_change_power(self):
        policy = self.cpu_policy()
        with patch.object(helper, 'battery_plugged', return_value=True):
            helper.enter()
        with patch.object(helper, 'battery_plugged', return_value=None):
            helper.enter()
        self.assertEqual((policy / 'scaling_governor').read_text(), 'ondemand')
        self.assertFalse(self.state.exists())

    def test_radios_default_keep_even_when_available(self):
        wifi = self.radio('rfkill0', 'wlan')
        bt = self.radio('rfkill1', 'bluetooth')
        helper.enter()
        self.assertEqual((wifi / 'soft').read_text(), '0')
        self.assertEqual((bt / 'soft').read_text(), '0')
        self.assertEqual(self.commands, [])

    def test_explicit_wifi_bt_off_then_restore_original_states(self):
        wifi = self.radio('rfkill0', 'wlan')
        bt = self.radio('rfkill1', 'bluetooth')
        with patch.dict(os.environ, {'PROXIMUS_SAVE_WLAN': 'off',
                                     'PROXIMUS_SAVE_BLUETOOTH': 'off'}):
            with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=3)):
                helper.enter()
        self.assertEqual((wifi / 'soft').read_text(), '1')
        self.assertEqual((bt / 'soft').read_text(), '1')
        self.assertEqual(self.commands[0][1:], ('block', 'bluetooth'))
        self.assertEqual(self.commands[1][1:], ('block', 'wlan'))
        helper.restore()
        self.assertEqual((wifi / 'soft').read_text(), '0')
        self.assertEqual((bt / 'soft').read_text(), '0')
        self.assertFalse(self.state.exists())

    def test_honor_preblocked_radio(self):
        wifi = self.radio('rfkill0', 'wlan', soft='1')
        with patch.dict(os.environ, {'PROXIMUS_SAVE_WLAN': 'off'}):
            with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=3)):
                helper.enter()
        self.assertFalse(self.state.exists())
        self.assertEqual((wifi / 'soft').read_text(), '1')
        self.assertEqual(self.commands, [])

    def test_provisioning_hotspot_is_not_turned_off(self):
        wifi = self.radio('rfkill0', 'wlan')
        with patch.dict(os.environ, {'PROXIMUS_SAVE_WLAN': 'off'}):
            with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)):
                helper.enter()
        self.assertEqual((wifi / 'soft').read_text(), '0')

    def test_restore_after_partial_apply_failure(self):
        policy = self.cpu_policy()
        wifi = self.radio('rfkill0', 'wlan')
        with patch.dict(os.environ, {'PROXIMUS_SAVE_WLAN': 'off'}):
            with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=3)):
                with patch.object(helper, 'command', side_effect=OSError('rfkill failed')):
                    with self.assertRaises(OSError):
                        helper.enter()
        self.assertEqual((policy / 'scaling_governor').read_text().strip(), 'ondemand')
        self.assertFalse(self.state.exists())
        self.assertEqual((wifi / 'soft').read_text(), '0')

    def test_restore_is_idempotent(self):
        helper.restore()
        helper.restore()
        self.assertFalse(self.state.exists())

    def test_invalid_mode_rejected_before_changes(self):
        policy = self.cpu_policy()
        with patch.dict(os.environ, {'PROXIMUS_SAVE_CPU': 'unsafe'}):
            with self.assertRaises(ValueError):
                helper.enter()
        self.assertFalse(self.state.exists())
        self.assertEqual((policy / 'scaling_governor').read_text(), 'ondemand')


class SupervisorTests(unittest.TestCase):
    def test_controls_only_whitelisted_systemd_unit(self):
        commands = []
        def run(argv, **kwargs):
            commands.append(argv)
            return Mock(returncode=0)
        s = Supervisor(run=run)
        s._call('start')
        s._call('stop')
        self.assertEqual(commands, [
            ['/usr/bin/systemctl', 'start', UNIT],
            ['/usr/bin/systemctl', 'stop', UNIT],
        ])

    def test_failures_do_not_claim_success(self):
        reports = []
        s = Supervisor(run=lambda *a, **kw: Mock(returncode=1),
                       report=lambda *a, **kw: reports.append((a, kw)))
        self.assertFalse(s._call('start'))
        self.assertEqual(reports[0][0], ('battery_standby_error',))


if __name__ == '__main__':
    unittest.main()
