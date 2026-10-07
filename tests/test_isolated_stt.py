"""Exercise real worker exit with a small fake native Vosk module."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
import wave
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from transcribe import transcribe_with_provider, TranscriptionError


class IsolatedSTTTests(unittest.TestCase):
    def test_real_worker_transcribes_without_loading_model_in_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / 'model'
            model.mkdir()
            (root / 'vosk.py').write_text('''
import json
SetLogLevel = lambda level: None
class Model:
    def __init__(self, path): pass
class KaldiRecognizer:
    def __init__(self, model, rate): assert rate == 16000
    def AcceptWaveform(self, pcm): return False
    def FinalResult(self): return json.dumps({'text': 'hallo lokal'})
''')
            audio = root / 'input.wav'
            with wave.open(str(audio), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b'\0\0' * 3200)
            environment = dict(PTT_MEMORY_MODE='isolated', STT_PROVIDER='vosk',
                               VOSK_PYTHON_PATH=tmp, VOSK_MODEL_PATH=str(model))
            with patch.dict(os.environ, environment), patch(
                'transcribe._load_vosk_model', side_effect=AssertionError('parent loaded model')
            ):
                self.assertEqual(transcribe_with_provider(audio), ('hallo lokal', 'vosk'))

    def test_controller_startup_isolated_does_not_load_either_model(self):
        import ptt
        gpiod = ModuleType('gpiod')
        gpiod.request_lines = Mock(side_effect=RuntimeError('GPIO test boundary'))
        gpiod.LineSettings = Mock()
        line = ModuleType('gpiod.line')
        line.Bias = SimpleNamespace(PULL_UP=1, PULL_DOWN=2)
        line.Direction = SimpleNamespace(INPUT=1)
        line.Value = SimpleNamespace(ACTIVE=1)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            sys.modules, {'gpiod': gpiod, 'gpiod.line': line}
        ), patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated',
                                  'PTT_RUNTIME_DIR': tmp, 'PTT_BUTTON_SHIM': '0',
                                  'STT_PROVIDER': 'vosk'}, clear=True), patch.object(
            sys, 'argv', ['ptt.py']
        ), patch('ptt.signal.signal'), patch('ptt.prepare_vosk') as prepare, patch(
            'ptt.ResidentSpeechOutput'
        ) as resident, patch('ptt.SpeechOutput') as cli:
            with self.assertRaisesRegex(RuntimeError, 'GPIO test boundary'):
                ptt.main()
            prepare.assert_not_called()
            resident.assert_not_called()
            self.assertIn(sys.executable, cli.call_args.args[0])
            self.assertIn('/src/speak.py', cli.call_args.args[0])
            cli.return_value.stop.assert_called_once()

    def test_worker_failure_does_not_load_resident_fallback(self):
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated'}), patch(
            'transcribe.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'model missing')
        ), patch('transcribe._load_vosk_model') as load:
            with self.assertRaisesRegex(TranscriptionError, 'model missing'):
                transcribe_with_provider('/tmp/test.wav')
            load.assert_not_called()

    def test_worker_timeout_propagates_as_stt_error(self):
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated'}), patch(
            'transcribe.subprocess.run', side_effect=subprocess.TimeoutExpired([], 120)
        ):
            with self.assertRaisesRegex(TranscriptionError, '120 seconds'):
                transcribe_with_provider('/tmp/test.wav')
