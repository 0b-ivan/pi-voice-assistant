import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import speak
import transcribe
from ptt import Recorder


class IsolationTests(unittest.TestCase):
    def test_vosk_worker_exits_before_transcript_is_returned(self):
        result = subprocess.CompletedProcess([], 0, 'hallo\n', '')
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated'}, clear=True), \
             patch('transcribe.subprocess.run', return_value=result) as run, \
             patch('transcribe.transcribe_vosk') as native:
            self.assertEqual(transcribe.transcribe_with_provider('/tmp/test.wav'), ('hallo', 'vosk'))
        native.assert_not_called()
        self.assertEqual(run.call_args.kwargs['env']['PTT_MEMORY_MODE'], 'resident')

    def test_vosk_timeout_is_reported_without_publishing_a_transcript(self):
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'isolated'}, clear=True), \
             patch('transcribe.subprocess.run', side_effect=subprocess.TimeoutExpired('vosk', 120)):
            with self.assertRaisesRegex(transcribe.TranscriptionError, 'exceeded'):
                transcribe.transcribe_with_provider('/tmp/test.wav')

    def test_draining_live_recognizer_retains_capture_and_blocks_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            recorder = Recorder(tmp, 'device', 30)
            recorder.raw.write_bytes(b'capture owned by active recognizer')
            recorder._pump_thread = Mock()
            recorder._pump_thread.is_alive.return_value = True
            recorder.process = Mock()
            recorder.process.poll.return_value = None
            recorder.process.wait.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'drain'):
                recorder.finish('release')
            self.assertTrue(recorder.raw.exists())
            with self.assertRaisesRegex(RuntimeError, 'still draining'):
                recorder.start()
            self.assertTrue(recorder.raw.exists())

    def test_dsp_failure_cleans_both_files_and_never_starts_playback(self):
        with tempfile.TemporaryDirectory() as tmp:
            created = []
            make_temp = tempfile.mkstemp
            def allocate(**kwargs):
                fd, name = make_temp(dir=tmp, suffix='.wav')
                created.append(Path(name))
                return fd, name
            env = {'PTT_MEMORY_MODE': 'isolated', 'TTS_VOICE_PROFILE': 'servitor',
                   'TTS_DSP_MODE': 'buffered'}
            with patch.dict(os.environ, env, clear=True), \
                 patch('speak.tempfile.mkstemp', side_effect=allocate), \
                 patch('speak.subprocess.run', side_effect=[Mock(), subprocess.CalledProcessError(1, ['ffmpeg'])]) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    speak.speak('Hallo')
            self.assertEqual(run.call_count, 2)
            self.assertEqual(len(created), 2)
            self.assertFalse(any(path.exists() for path in created))

class ReferencePlaybackTests(unittest.TestCase):
    def test_reference_aura_is_confined_to_rendering(self):
        from voice_effects import build_render_command, build_stream_playback_command
        with patch.dict(os.environ, {'TTS_SERVITOR_AURA': 'reference'}, clear=True):
            render = build_render_command('in.wav', 'out.wav')
            stream = build_stream_playback_command(22050, 1, 'device')
        self.assertIn('aevalsrc=', render[render.index('-filter_complex') + 1])
        self.assertNotIn('aevalsrc=', stream[stream.index('-filter_complex') + 1])

    def test_invalid_aura_fails_before_ffmpeg_starts(self):
        from voice_effects import build_render_command
        with patch.dict(os.environ, {'TTS_SERVITOR_AURA': 'unknown'}, clear=True):
            with self.assertRaises(ValueError):
                build_render_command('in.wav', 'out.wav')
