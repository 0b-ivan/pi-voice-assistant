import json
import signal
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ptt import Button, Recorder, process_capture
from transcribe import TranscriptionError


class ButtonTests(unittest.TestCase):
    def test_held_at_boot_requires_release(self):
        b = Button()
        self.assertIsNone(b.update(True, 0))
        self.assertIsNone(b.update(True, 1))
        b.update(False, 2)
        b.update(False, 2.1)
        b.update(True, 3)
        self.assertEqual(b.update(True, 3.1), 'start')

    def test_bounce_and_release(self):
        b = Button()
        b.update(False, 0)
        b.update(False, .1)
        b.update(True, .2)
        b.update(False, .21)
        b.update(True, .22)
        self.assertIsNone(b.update(True, .24))
        self.assertEqual(b.update(True, .27), 'start')
        b.update(False, .3)
        b.update(True, .31)
        b.update(False, .32)
        self.assertEqual(b.update(False, .37), 'release')
        self.assertIsNone(b.update(False, .5))

    def test_limit_does_not_restart_held_button(self):
        b = Button(limit=1)
        b.update(False, 0)
        b.update(False, .1)
        b.update(True, .2)
        self.assertEqual(b.update(True, .3), 'start')
        self.assertEqual(b.update(True, 1.4), 'limit')
        self.assertIsNone(b.update(True, 2))
        b.update(False, 3)
        b.update(False, 3.1)
        b.update(True, 4)
        self.assertEqual(b.update(True, 4.1), 'start')

    def test_failure_requires_release(self):
        b = Button()
        b.update(False, 0)
        b.update(False, .1)
        b.update(True, .2)
        b.update(True, .3)
        b.failed()
        self.assertIsNone(b.update(True, 1))
        b.update(False, 2)
        b.update(False, 2.1)
        b.update(True, 3)
        self.assertEqual(b.update(True, 3.1), 'start')

    def test_resync_after_processing_requires_release_if_held(self):
        b = Button()
        b.resync(True, 10)
        self.assertIsNone(b.update(True, 11))
        b.update(False, 12)
        b.update(False, 12.1)
        b.update(True, 13)
        self.assertEqual(b.update(True, 13.1), 'start')

    def test_resync_after_processing_arms_when_released(self):
        b = Button()
        b.resync(False, 10)
        b.update(True, 11)
        self.assertEqual(b.update(True, 11.1), 'start')


class FakeProcess:
    def __init__(self, code=0, stuck=False):
        self.code, self.stuck = code, stuck
        self.running = True
        self.signals = []
        self.killed = False

    def poll(self):
        return None if self.running else self.code

    def send_signal(self, sig):
        self.signals.append(sig)

    def wait(self, timeout):
        import subprocess
        if self.stuck and not self.killed:
            raise subprocess.TimeoutExpired('arecord', timeout)
        self.running = False
        return self.code

    def kill(self):
        self.killed = True


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Recorder(self.tmp.name, 'test-device', 1)
        self.output = StringIO()

    def wav(self, frames=9600):
        self.r.raw.write_bytes(b'\0' * frames * 4)

    def test_start_command_and_replaces_previous_capture(self):
        self.r.ready.write_bytes(b'old')
        with patch('ptt.subprocess.Popen', return_value=FakeProcess()) as popen, redirect_stdout(self.output):
            self.r.start()
        self.assertFalse(self.r.ready.exists())
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], '/usr/bin/arecord')
        self.assertIn('test-device', argv)
        self.assertIn('48000', argv)
        self.assertEqual(argv[-2:], ['1', str(self.r.raw)])

    def test_release_publishes_valid_wav_and_contract(self):
        proc = self.r.process = FakeProcess()
        self.wav()
        with redirect_stdout(self.output):
            capture = self.r.finish('release')
        result = json.loads(self.output.getvalue())
        self.assertEqual(result['event'], 'capture_ready')
        self.assertEqual(result['frames'], 9600)
        self.assertEqual(result['channels'], 2)
        self.assertEqual(proc.signals, [signal.SIGINT])
        self.assertEqual(capture, self.r.ready)
        self.assertTrue(self.r.ready.exists())
        self.assertFalse(self.r.partial.exists())

    def test_failure_and_empty_audio_are_not_published(self):
        for code, frames in [(1, 9600), (0, 0)]:
            self.r.process = FakeProcess(code)
            self.r.process.running = False
            self.wav(frames)
            with self.assertRaises(RuntimeError):
                self.r.finish('release')
            self.assertFalse(self.r.ready.exists())
            self.assertFalse(self.r.partial.exists())

    def test_shutdown_discards_recording(self):
        self.r.process = FakeProcess()
        self.wav()
        self.r.ready.write_bytes(b'old')
        self.r.close()
        self.assertFalse(self.r.ready.exists())
        self.assertFalse(self.r.partial.exists())

    def test_hung_recorder_is_killed(self):
        proc = self.r.process = FakeProcess(stuck=True)
        self.wav()
        with self.assertRaises(RuntimeError):
            self.r.finish('release')
        self.assertTrue(proc.killed)
        self.assertFalse(self.r.partial.exists())

    def test_unaligned_pcm_is_rejected(self):
        self.r.process = FakeProcess()
        self.wav()
        self.r.raw.write_bytes(self.r.raw.read_bytes()[:-1])
        with self.assertRaises(RuntimeError):
            self.r.finish('release')
        self.assertFalse(self.r.ready.exists())

    def test_natural_limit_publishes_without_sending_signal(self):
        proc = self.r.process = FakeProcess()
        proc.running = False
        self.wav()
        with redirect_stdout(self.output):
            capture = self.r.finish('process_exit')
        self.assertEqual(proc.signals, [])
        self.assertEqual(json.loads(self.output.getvalue())['reason'], 'process_exit')
        self.assertEqual(capture, self.r.ready)

    def test_requested_stop_with_alsa_interrupted_read_builds_wav(self):
        self.r.process = FakeProcess(code=1)
        self.wav()
        with redirect_stdout(self.output):
            self.r.finish('release')
        with wave.open(str(self.r.ready), 'rb') as audio:
            self.assertEqual(audio.getnframes(), 9600)
        self.assertFalse(self.r.raw.exists())


class ProcessingTests(unittest.TestCase):
    def test_success_emits_transcript(self):
        output = StringIO()
        with patch('ptt.transcribe', return_value='Hallo Welt'), redirect_stdout(output):
            self.assertEqual(process_capture('/tmp/capture.wav'), 'Hallo Welt')
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])['event'], 'processing')
        self.assertEqual(json.loads(lines[1]), {
            'version': 1,
            'event': 'transcript',
            'text': 'Hallo Welt',
        })
        self.assertEqual(lines[2], 'ERKANNT: Hallo Welt')

    def test_stt_error_is_recoverable(self):
        output = StringIO()
        with patch('ptt.transcribe', side_effect=TranscriptionError('offline')), redirect_stdout(output):
            self.assertIsNone(process_capture('/tmp/capture.wav'))
        lines = output.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])['event'], 'processing')
        error = json.loads(lines[1])
        self.assertEqual(error['event'], 'stt_error')
        self.assertIn('offline', error['message'])


if __name__ == '__main__':
    unittest.main()
