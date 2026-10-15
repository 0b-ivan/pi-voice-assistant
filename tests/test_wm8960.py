import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    'configure_wm8960', ROOT / 'scripts' / 'configure-wm8960.py')
wm8960 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wm8960)


class WM8960Tests(unittest.TestCase):
    def mixer(self, left, right):
        return Mock(return_value=subprocess.CompletedProcess(
            [], 0, stdout=("Simple mixer control 'Headphone',0\n"
                           "  Capabilities: pvolume\n"
                           "  Limits: Playback 0 - 127\n"
                           f"  Front Left: Playback {left} [0%] [-999dB]\n"
                           f"  Front Right: Playback {right} [0%] [-999dB]\n")))

    def test_silent_jack_is_enabled_and_saved_without_changing_other_levels(self):
        run = self.mixer(0, 0)
        self.assertEqual(wm8960.configure(run), [89, 89])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn(['amixer', '-c', 'wm8960soundcard',
                       'sset', 'Headphone', '89,89'], commands)
        for channel in ('Left', 'Right'):
            self.assertIn(['amixer', '-c', 'wm8960soundcard', 'sset',
                           f'{channel} Output Mixer PCM', 'on'], commands)
        self.assertEqual(commands[-1], ['alsactl', 'store', 'wm8960soundcard'])
        changed = {command[4] for command in commands if 'sset' in command}
        self.assertEqual(changed, {'Headphone', 'Left Output Mixer PCM',
                                   'Right Output Mixer PCM'})

    def test_existing_per_channel_levels_are_preserved(self):
        run = self.mixer(37, 115)
        self.assertEqual(wm8960.configure(run), [37, 115])
        self.assertFalse(any(call.args[0][3:5] == ['sset', 'Headphone']
                             for call in run.call_args_list))

    def test_only_silent_channel_is_initialized(self):
        run = self.mixer(0, 102)
        self.assertEqual(wm8960.configure(run), [89, 102])
        self.assertEqual(run.call_args_list[1].args[0][-1], '89,102')

    def test_invalid_mixer_output_does_not_change_or_save_state(self):
        for output in ('', '  Front Left: Playback 0 [0%]\n',
                       '  Front Left: Playback 128\n  Front Right: Playback 0\n'):
            with self.subTest(output=output):
                run = Mock(return_value=subprocess.CompletedProcess([], 0, output))
                with self.assertRaises(ValueError):
                    wm8960.configure(run)
                self.assertEqual(run.call_count, 1)

    def test_routing_failure_does_not_save_partial_profile(self):
        run = self.mixer(89, 89)
        run.side_effect = [run.return_value,
                           subprocess.CalledProcessError(1, ['amixer'])]
        with self.assertRaises(subprocess.CalledProcessError):
            wm8960.configure(run)
        self.assertFalse(any(call.args[0][0] == 'alsactl'
                             for call in run.call_args_list))

    def test_optional_install_skips_absent_card(self):
        with patch('sys.argv', ['configure-wm8960.py', '--if-present']), \
             patch.object(wm8960.os, 'geteuid', return_value=0), \
             patch.object(wm8960.Path, 'exists', return_value=False), \
             patch.object(wm8960, 'configure') as configure, \
             patch('builtins.print'):
            wm8960.main()
        configure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
