import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import transcribe
from voice_controls import SpeechOutput

class PreparedChild:
    args = ['transcribe.py', '--prepared-worker']
    def __init__(self, timeout=False):
        self.returncode = None
        self.stdin, self.stdout = Mock(), Mock()
        self.timeout = timeout
        self.terminated = False
    def poll(self):
        return self.returncode
    def communicate(self, data, timeout):
        if self.timeout:
            raise subprocess.TimeoutExpired(self.args, timeout)
        self.returncode = 0
        return 'Hallo\n', None
    def terminate(self):
        self.terminated = True
        self.returncode = -15
    def wait(self, timeout):
        return self.returncode

class PreparedWorkerTests(unittest.TestCase):
    def setUp(self):
        transcribe._PREPARED_VOSK = None
        transcribe._PREPARED_LAST_START = 0
        self.addCleanup(transcribe.stop_prepared_vosk)

    def test_claimed_worker_is_reaped_before_transcript_returns(self):
        child = PreparedChild()
        transcribe._PREPARED_VOSK = child
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True), \
             patch('transcribe.transcribe_vosk') as native:
            self.assertEqual(transcribe.transcribe_with_provider('/tmp/input.wav'), ('Hallo', 'vosk'))
        self.assertEqual(child.returncode, 0)
        self.assertIsNone(transcribe._PREPARED_VOSK)
        native.assert_not_called()
        child.stdout.close.assert_called()

    def test_timeout_terminates_owned_native_worker(self):
        child = PreparedChild(timeout=True)
        transcribe._PREPARED_VOSK = child
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True):
            with self.assertRaisesRegex(transcribe.TranscriptionError, 'exceeded'):
                transcribe.transcribe_with_provider('/tmp/input.wav')
        self.assertTrue(child.terminated)
        self.assertIsNone(transcribe._PREPARED_VOSK)

    def test_idle_preparation_is_reused_and_child_does_not_recurse(self):
        child = PreparedChild()
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True), \
             patch('transcribe.subprocess.Popen', return_value=child) as start:
            transcribe.prepare_vosk_worker()
            transcribe.prepare_vosk_worker()
        self.assertEqual(start.call_count, 1)
        self.assertEqual(start.call_args.kwargs['env']['PTT_MEMORY_MODE'], 'resident')

    def test_speech_releases_standby_model_before_launch(self):
        child = PreparedChild()
        transcribe._PREPARED_VOSK = child
        def launch(*args, **kwargs):
            self.assertTrue(child.terminated)
            return Mock()
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True), \
             patch('voice_controls.subprocess.Popen', side_effect=launch):
            output = SpeechOutput('/usr/bin/true')
            output.start('Hallo')
        self.assertIsNone(transcribe._PREPARED_VOSK)

class RemoteLiveWorkerTests(unittest.TestCase):
    def child(self, code):
        proc = subprocess.Popen([sys.executable, '-u', '-c', code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        transcribe._PREPARED_VOSK = proc
        self.addCleanup(lambda: transcribe._reap_prepared(proc))
        return proc

    def test_pcm_is_ordered_and_worker_exits_before_result(self):
        proc = self.child("import sys; assert sys.stdin.buffer.readline() == b'@LIVE16000\\n'; print(len(sys.stdin.buffer.read()))")
        recognizer = transcribe.RemoteLiveVoskRecognizer()
        for _ in range(4):
            recognizer.accept_pcm(b'\x01\x00' * 1600)
        self.assertEqual(recognizer.finish(), '12800')
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(recognizer.writer.is_alive())

    def test_cancel_interrupts_a_blocked_writer_and_reaps_child(self):
        proc = self.child('import time; time.sleep(60)')
        recognizer = transcribe.RemoteLiveVoskRecognizer()
        for _ in range(8):
            recognizer.accept_pcm(b'\0' * 32000)
        recognizer.cancel()
        self.assertIsNotNone(proc.poll())
        self.assertFalse(recognizer.writer.is_alive())
        with self.assertRaisesRegex(transcribe.TranscriptionError, 'cancelled'):
            recognizer.finish()

    def test_native_finalize_timeout_terminates_worker(self):
        proc = self.child('import sys,time; sys.stdin.buffer.readline(); sys.stdin.buffer.read(); time.sleep(60)')
        recognizer = transcribe.RemoteLiveVoskRecognizer()
        recognizer.accept_pcm(b'\0' * 3200)
        with patch.dict(os.environ, {'VOSK_LIVE_FINALIZE_TIMEOUT_SECONDS': '0.1'}):
            with self.assertRaises(transcribe.TranscriptionError):
                recognizer.finish()
        self.assertIsNotNone(proc.poll())
        self.assertFalse(recognizer.writer.is_alive())

class HybridControllerTests(unittest.TestCase):
    def controller(self, synthesizing=False):
        from types import SimpleNamespace
        from ptt import VoiceController
        recorder = SimpleNamespace(process=None, start=Mock(), finish=Mock(), close=Mock())
        recorder.start.side_effect = lambda: setattr(recorder, 'process', Mock(poll=lambda: None))
        speech = SimpleNamespace(active=False, synthesizing=synthesizing,
                                 start=Mock(), stop=Mock(), poll=lambda: None)
        return VoiceController(recorder, speech, 0.04, 30), recorder

    def test_capture_does_not_spawn_a_second_prepared_model(self):
        controller, recorder = self.controller()
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True), \
             patch('ptt.prepare_vosk_worker') as prepare:
            controller.tick(False, (False,)*5, 0)
            controller.tick(False, (False,)*5, 0.1)
            controller.tick(True, (False,)*5, 0.2)
            prepare.reset_mock()
            controller.tick(True, (False,)*5, 0.3)
        recorder.start.assert_called_once()
        prepare.assert_not_called()

    def test_cancelled_native_synthesis_blocks_recording_and_preload(self):
        controller, recorder = self.controller(synthesizing=True)
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}, clear=True), \
             patch('ptt.prepare_vosk_worker') as prepare:
            for held, stamp in [(False,0),(False,.1),(True,.2),(True,.3)]:
                controller.tick(held, (False,)*5, stamp)
        recorder.start.assert_not_called()
        prepare.assert_not_called()


class SynthesisScratchTests(unittest.TestCase):
    def test_scratch_is_released_even_when_piper_fails(self):
        import tempfile
        import threading
        from voice_controls import ResidentSpeechOutput

        class FailingVoice:
            def synthesize(self, *args, **kwargs):
                raise RuntimeError('piper failed')

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {'PTT_MEMORY_MODE': 'resident'}, clear=True):
            output = ResidentSpeechOutput('model.onnx', 'dev', tmp,
                                          loader=lambda _model: FailingVoice(), profile='normal')
            job = Mock()
            output._job = job
            output._synthesis_lock = threading.Lock()
            with patch('voice_controls._release_synthesis_scratch') as release, \
                    patch('voice_controls._synthesize_voice', side_effect=RuntimeError('piper')):
                import wave
                # wave may raise its own error while closing the empty file.
                with self.assertRaises((RuntimeError, wave.Error)):
                    output._run_file(job, 'Hallo')
            release.assert_called_once()
            self.assertEqual(list(Path(tmp).iterdir()), [])
