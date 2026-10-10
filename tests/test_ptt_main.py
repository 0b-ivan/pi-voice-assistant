"""main() up to the GPIO request, with fake gpiod; checks wiring that only
runs later in other threads (wake word detector factory)."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import ptt  # noqa: E402


def fake_gpiod():
    gpiod = ModuleType('gpiod')
    gpiod.request_lines = Mock(side_effect=RuntimeError('GPIO test boundary'))
    gpiod.LineSettings = Mock()
    line = ModuleType('gpiod.line')
    line.Bias = SimpleNamespace(PULL_UP=1, PULL_DOWN=2)
    line.Direction = SimpleNamespace(INPUT=1)
    line.Value = SimpleNamespace(ACTIVE=1)
    return {'gpiod': gpiod, 'gpiod.line': line}


class EventTests(unittest.TestCase):
    def test_field_called_name_does_not_clash_with_event_name(self):
        # Enrollment results carry the speaker's name (crashed pi-ptt on 2026-10-10).
        with patch('builtins.print') as printed, patch.object(ptt, 'publish_display_event') as display:
            ptt.event('enroll', name='ivan', saved=True)
        self.assertIn('"event": "enroll"', printed.call_args.args[0])
        self.assertIn('"name": "ivan"', printed.call_args.args[0])
        display.assert_called_once_with('enroll')


class MainWiringTests(unittest.TestCase):
    def test_wake_detector_factory_still_works_after_main_set_up_gpio(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp) / 'wake'
            models.mkdir()
            (models / 'hey_jarvis_v0.1.onnx').write_bytes(b'')
            wakeword = ModuleType('wakeword')
            wakeword.Detector = Mock(name='Detector')
            wakeword.WakeWord = Mock(name='WakeWord')
            env = {'PTT_MEMORY_MODE': 'isolated', 'PTT_RUNTIME_DIR': tmp,
                   'PTT_WAKE_WORD': 'hey_jarvis_v0.1', 'PTT_WAKE_MODEL_DIR': str(models),
                   'PIPER_VENV': str(Path(tmp) / 'venv'), 'PTT_BLUETOOTH': '0',
                   'PTT_DISPLAY_EVENT_PATH': str(Path(tmp) / 'event.json'),
                   'PTT_DISPLAY_STATUS_PATH': str(Path(tmp) / 'status.json'),
                   'PTT_SETTINGS_FILE': str(Path(tmp) / 'settings.json')}
            with patch.dict(sys.modules, {**fake_gpiod(), 'wakeword': wakeword}), \
                    patch.dict(os.environ, env, clear=True), \
                    patch.object(sys, 'argv', ['ptt.py']), patch('ptt.signal.signal'), \
                    patch('ptt.SpeechOutput'), patch('ptt.load_remote_config', return_value=None), \
                    patch('ptt.InternetProbe'), patch('ptt.sysmon'), patch('ptt.wlan_radio'), \
                    patch('ptt.cue_sound'), patch('ptt.agenda_feed'), \
                    patch('wake_listener.WakeListener') as listener, \
                    patch('ptt.print'):
                with self.assertRaisesRegex(RuntimeError, 'GPIO test boundary'):
                    ptt.main()
                factory = listener.call_args.args[1]
                factory()  # runs in the wake thread, after main() reached the GPIO loop
            wakeword.Detector.assert_called_once()


if __name__ == '__main__':
    unittest.main()
