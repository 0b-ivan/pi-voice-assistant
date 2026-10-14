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
        self.assertEqual(menu.label('cpu_mode'), 'CPU-Leistung')
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


class ControllerCpuModeTests(unittest.TestCase):
    """The governor switch runs in a worker; the main loop applies its result."""

    def setUp(self):
        import os
        from contextlib import redirect_stdout
        from io import StringIO
        import ptt
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(Path(tmp.name) / 'display.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(tmp.name) / 'event.json'),
            'PTT_SETTINGS_FILE': str(Path(tmp.name) / 'settings.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        recorder, speech = Mock(), Mock()
        recorder.process = None
        recorder.take_live_transcript.return_value = None
        speech.active = speech.synthesizing = False
        speech.poll.return_value = None
        self.ptt = ptt
        self.c = ptt.VoiceController(recorder, speech, .04, 30, remote=True)
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def switch(self, mode, result, announce=True):
        with patch.object(cpu_modes, 'set_mode', side_effect=result), \
                patch('threading.Thread') as thread:
            self.assertTrue(self.c._set_cpu_mode(mode, announce))
            thread.call_args.kwargs['target']()          # the worker, synchronously
        self.assertTrue(self.c.cpu_mode_busy)               # until the main loop takes it
        self.assertFalse(self.c._set_cpu_mode('eco'))       # one switch at a time
        self.c.tick(False, (False,) * 5, 1.0)
        self.assertFalse(self.c.cpu_mode_busy)

    def test_success_is_applied_and_saved_on_the_main_loop(self):
        self.switch('power', [True])
        self.assertEqual(self.c.cpu_mode, 'power')
        self.assertEqual(self.c.settings.load().get('cpu_mode'), 'power')
        self.assertEqual(self.c.alarm_queue, [])
        self.c.speech.start.assert_not_called()

    def test_failure_is_said_once_but_not_at_start_up(self):
        self.switch('power', OSError('denied'))
        self.assertEqual(self.c.cpu_mode, 'balanced')
        said = self.c.alarm_queue + [c.args[0] for c in self.c.speech.start.call_args_list]
        self.assertEqual(said, [device_control.failed_text(self.c.style)])
        failed = [line for line in self.output.getvalue().splitlines()
                  if '"cpu_mode"' in line and '"failed"' in line]
        self.assertEqual(len(failed), 1)                    # logged once, with the reason
        self.assertIn('denied', failed[0])
        self.c.alarm_queue = []
        self.c.speech.start.reset_mock()
        self.switch('eco', [False], announce=False)
        self.assertEqual(self.c.alarm_queue, [])
        self.c.speech.start.assert_not_called()

    def test_voice_command_for_the_current_mode_switches_nothing(self):
        self.c.cpu_mode = 'eco'
        with patch.object(self.c, '_set_cpu_mode') as switch:
            self.assertEqual(self.c._device_command('cpu_eco'),
                             'Prozessor ist bereits im Sparmodus.')
        switch.assert_not_called()
