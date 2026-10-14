"""Runtime CPU profiles and guarded Zero 2 W overclock boot presets."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import cpu_modes
import device_control
import menu


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gov = load_script('test_governor_helper', 'proximus-cpu-mode.py')
oc = load_script('test_overclock_helper', 'configure-overclock.py')


class GovernorModesTests(unittest.TestCase):
    def test_user_profiles_map_to_kernel_governors(self):
        self.assertEqual(gov.MODES, {
            'eco': 'powersave', 'balanced': 'ondemand', 'power': 'performance'})
        self.assertEqual(cpu_modes.MODES, ('eco', 'balanced', 'power'))

    def test_set_mode_exact_allowlist(self):
        calls = []
        def run(argv, **kw):
            calls.append(argv)
            return Mock(returncode=0)
        self.assertTrue(cpu_modes.set_mode('power', run=run))
        self.assertEqual(calls, [['/usr/bin/systemctl', 'start',
                                  'proximus-cpu-mode@power.service']])
        with self.assertRaises(ValueError):
            cpu_modes.set_mode('../shutdown', run=run)
        self.assertEqual(len(calls), 1)

    def test_modes_change_governor_without_overclock(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / 'policy0'
            policy.mkdir()
            (policy / 'scaling_governor').write_text('ondemand')
            (policy / 'scaling_available_governors').write_text(
                'conservative ondemand userspace powersave performance schedutil')
            result = gov.apply('power', Path(tmp))
            self.assertEqual(result, {'policy0': 'ondemand'})
            self.assertEqual((policy / 'scaling_governor').read_text().strip(),
                             'performance')
            gov.apply('eco', Path(tmp))
            self.assertEqual((policy / 'scaling_governor').read_text().strip(),
                             'powersave')
            gov.apply('balanced', Path(tmp))
            self.assertEqual((policy / 'scaling_governor').read_text().strip(),
                             'ondemand')

    def test_unsupported_or_invalid_governor_never_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = Path(tmp) / 'policy0'
            policy.mkdir()
            path = policy / 'scaling_governor'
            path.write_text('ondemand')
            (policy / 'scaling_available_governors').write_text('ondemand powersave')
            with self.assertRaises(RuntimeError):
                gov.apply('power', Path(tmp))
            with self.assertRaises(ValueError):
                gov.apply('over_voltage=8', Path(tmp))
            self.assertEqual(path.read_text(), 'ondemand')

    def test_menu_exposes_profile(self):
        self.assertEqual(menu.label('cpu_mode'), 'CPU-Profil')
        self.assertIn('cpu_mode', menu.entries('energy'))

    def test_voice_profiles_but_no_unsafe_voltage_changes(self):
        commands = {
            'stell den cpu modus auf eco': 'cpu_eco',
            'schalte cpu profil auf balanced': 'cpu_balanced',
            'stell den prozessor modus auf power': 'cpu_power',
            'stell cpu modus auf overclock': None,
            'schalte cpu modus mit übertaktung und voltage ein': None,
            'wie ist der cpu modus': None,
            'schalte die stromhinweise an': 'power_notices_on',
        }
        for phrase, expected in commands.items():
            with self.subTest(phrase=phrase):
                self.assertEqual(device_control.command(phrase), expected)


class OverclockStagingTests(unittest.TestCase):
    def test_mode_specific_section_and_no_force_turbo(self):
        self.assertIn('[pi02]', oc.MANAGED)
        self.assertIn('arm_freq=1200', oc.MANAGED)
        self.assertIn('over_voltage=2', oc.MANAGED)
        self.assertIn('force_turbo=0', oc.MANAGED)
        self.assertIn('[all]', oc.MANAGED)

    def test_enable_disable_roundtrip_preserves_other_sections(self):
        original = '[all]\ndtparam=i2s=on\ndtoverlay=wm8960-soundcard\n'
        after = oc.prepare_enable(original)
        self.assertIn('arm_freq=1200', after)
        self.assertEqual(oc.prepare_disable(after), original)

    def test_duplicate_or_external_clock_entries_rejected(self):
        for config in ('[all]\narm_freq=1350\n',
                       '[all]\nover_voltage=4\n',
                       '[all]\nforce_turbo=1\n',
                       '[all]\ninclude extra.txt\n'):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    oc.prepare_enable(config)
        with self.assertRaises(ValueError):
            oc.prepare_enable(oc.MANAGED)

    def test_overclock_block_refuses_to_delete_modified_config(self):
        changed = oc.MANAGED.replace('over_voltage=2', 'over_voltage=6')
        with self.assertRaises(ValueError):
            oc.prepare_disable(changed)

    def test_board_identity_must_be_zero2w(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / 'model'
            model.write_bytes(b'Raspberry Pi 5 Model B Rev 1.0\x00')
            with self.assertRaises(RuntimeError):
                oc.verify_model(model)
            model.write_bytes(b'Raspberry Pi Zero 2 W Rev 1.0\x00')
            oc.verify_model(model)


if __name__ == '__main__':
    unittest.main()
