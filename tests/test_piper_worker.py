import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import speak


class PiperWorkerTests(unittest.TestCase):
    def test_real_worker_exits_before_playback_and_emits_separate_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = root / 'test.onnx'
            (root / 'piper.py').write_text('''
import os
from pathlib import Path
class PiperVoice:
    @classmethod
    def load(cls, model):
        Path(model + '.pid').write_text(str(os.getpid()))
        return cls()
    def synthesize_wav(self, text, audio):
        assert text == 'Paris.'
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\\0\\0' * 16000)
''')
            real_run = subprocess.run
            events = []
            playback = []
            def run(command, **kwargs):
                if command[0] == sys.executable:
                    self.assertTrue(command[1].endswith('piper_worker.py'))
                    result = real_run(command, **kwargs, capture_output=True)
                    events.extend(json.loads(line) for line in result.stdout.splitlines())
                    return result
                pid = int(Path(str(model) + '.pid').read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
                with wave.open(command[-1], 'rb') as audio:
                    self.assertEqual(audio.getnframes(), 16000)
                playback.append(command)
                return subprocess.CompletedProcess(command, 0)
            env = dict(PTT_MEMORY_MODE='isolated', TTS_VOICE_PROFILE='normal',
                       PIPER_MODEL=str(model), PIPER_PYTHON=sys.executable,
                       PYTHONPATH=tmp)
            with patch.dict(os.environ, env, clear=True), patch('speak.subprocess.run', side_effect=run):
                speak.speak('Paris.')
            self.assertEqual(len(playback), 1)
            self.assertEqual([x['metric'] for x in events if x['event'] == 'latency'],
                             ['import', 'model_load', 'synthesis'])
            self.assertEqual(events[-1]['audio_duration_ms'], 1000)

    def test_failed_isolated_worker_never_starts_playback(self):
        error = subprocess.CalledProcessError(1, ['piper_worker.py'])
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated'}), patch(
            'speak.subprocess.run', side_effect=error
        ) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                speak.speak('Paris.')
        run.assert_called_once()
