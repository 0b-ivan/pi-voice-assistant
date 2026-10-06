import json
import os
from pathlib import Path
import signal
import shlex
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from button_shim import ButtonShim
from ptt import VoiceController
from voice_controls import ResidentSpeechOutput, SpeechOutput, TranscriptionJob, change_volume


class ShimTests(unittest.TestCase):
    def test_inputs_and_led_wire_format(self):
        bus = Mock()
        shim = ButtonShim(bus)
        bus.read_byte_data.return_value = 0x16  # A,D pressed
        self.assertEqual(shim.read(), (True, False, False, True, False))
        bus.write_byte_data.reset_mock()
        shim.set_color((255, 0, 0))
        values = [c.args[2] for c in bus.write_byte_data.call_args_list]
        self.assertTrue(all(c.args[:2] == (0x3f, 1) for c in bus.write_byte_data.call_args_list))
        bits = []
        for low, high in zip(values[0:-1:2], values[1:-1:2]):
            self.assertEqual(low & 0x40, 0)
            self.assertEqual(high, low | 0x40)
            bits.append(int(bool(low & 0x80)))
        decoded = [sum(bit << (7-j) for j, bit in enumerate(bits[i:i+8]))
                   for i in range(0, len(bits), 8)]
        self.assertEqual(decoded, [0, 0, 0, 0, 0xe4, 0, 0, 255, 255, 255, 255, 255])
        count = bus.write_byte_data.call_count
        shim.set_color((255, 0, 0))
        self.assertEqual(bus.write_byte_data.call_count, count)
        shim.close()
        bus.close.assert_called_once()

    def test_initialization_failure_closes_bus(self):
        bus = Mock()
        bus.write_byte_data.side_effect = OSError('missing shim')
        with self.assertRaises(OSError):
            ButtonShim(bus)
        bus.close.assert_called_once()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.speech.active = False
        self.speech.poll.return_value = None
        self.c = VoiceController(self.recorder, self.speech, .04, 30)
        self.now = 0
        self.output = StringIO()
        self.context = redirect_stdout(self.output)
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.tick()

    def tick(self, gpio=False, down=''):
        states = tuple(x in down for x in 'ABCDE')
        self.now += .1
        self.c.tick(gpio, states, self.now)
        self.now += .1
        self.c.tick(gpio, states, self.now)

    def events(self):
        return [json.loads(l) for l in self.output.getvalue().splitlines()
                if l.startswith('{')]

    def job(self):
        job = Mock()
        job.done.is_set.return_value = False
        job.cancelled = False
        job.error = None
        job.result = ('Hallo', 'vosk')
        job.cancel.side_effect = lambda: setattr(job, 'cancelled', True)
        self.c.job = job
        return job

    def test_a_and_gpio_hold_one_recording_until_both_released(self):
        self.tick(down='A')
        self.recorder.start.assert_called_once()
        proc = Mock()
        proc.poll.return_value = None
        self.recorder.process = proc
        self.tick(gpio=True, down='A')
        self.tick(gpio=True)
        self.recorder.finish.assert_not_called()
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        with patch('ptt.TranscriptionJob') as worker:
            self.tick()
        self.recorder.finish.assert_called_once_with('release')
        worker.assert_called_once()

    def test_b_cancels_recording_without_restart_when_a_held(self):
        self.tick(down='A')
        proc = Mock()
        proc.poll.return_value = None
        self.recorder.process = proc
        self.tick(down='AB')
        self.recorder.finish.assert_called_once_with('cancel', publish=False)
        self.recorder.process = None
        self.tick(down='A')
        self.recorder.start.assert_called_once()
        self.tick()
        self.tick(down='A')
        self.assertEqual(self.recorder.start.call_count, 2)

    def test_b_discards_stt_and_keeps_slot_busy_until_done(self):
        job = self.job()
        self.tick(down='B')
        job.cancel.assert_called_once()
        self.tick()
        self.tick(down='A')
        self.recorder.start.assert_not_called()
        job.done.is_set.return_value = True
        self.tick(down='A')
        self.assertIsNone(self.c.job)
        self.assertIn('transcript_discarded', [e['event'] for e in self.events()])
        self.assertNotIn('transcript', [e['event'] for e in self.events()])
        self.recorder.start.assert_not_called()
        self.tick()
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_volume_and_status_stay_usable_during_stt(self):
        self.job()
        with patch('ptt.change_volume') as volume:
            self.tick(down='C')
            self.tick(down='C')
            self.tick()
            self.tick(down='D')
        self.assertEqual([c.args for c in volume.call_args_list], [(-1,), (1,)])
        self.tick()
        self.tick(down='E')
        self.assertIn('verarbeite', self.speech.start.call_args.args[0])
        self.tick()
        self.tick(down='BE')
        self.speech.start.assert_called_once()
        self.speech.stop.assert_called_once()

    def test_status_never_plays_into_recording(self):
        self.tick(down='AE')
        self.speech.start.assert_not_called()
        self.recorder.start.assert_called_once()

    def test_errors_are_logged_and_next_job_can_run(self):
        job = self.job()
        job.done.is_set.return_value = True
        job.error = 'recognizer failure'
        self.tick()
        self.assertIsNone(self.c.job)
        self.assertIn('stt_error', [e['event'] for e in self.events()])
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_probe_has_no_audio_or_mixer_actions(self):
        self.c.probe = True
        with patch('ptt.change_volume') as volume:
            self.tick(down='ABCDE')
        volume.assert_not_called()
        self.recorder.start.assert_not_called()
        self.speech.start.assert_not_called()

    def test_completed_job_publishes_and_rearms_released_ptt(self):
        job = self.job()
        job.done.is_set.return_value = True
        self.tick()
        transcript = [e for e in self.events() if e['event'] == 'transcript']
        self.assertEqual(transcript[0]['text'], 'Hallo')
        self.assertEqual(transcript[0]['provider'], 'vosk')
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_held_a_at_service_start_requires_release(self):
        self.c = VoiceController(self.recorder, self.speech, .04, 30)
        self.tick(down='A')
        self.tick(down='A')
        self.recorder.start.assert_not_called()
        self.tick()
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_missing_speech_command_does_not_disable_ptt(self):
        self.speech.start.side_effect = FileNotFoundError('missing TTS')
        self.tick(down='E')
        self.assertIn('speech_error', [e['event'] for e in self.events()])
        self.tick()
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_shutdown_cancels_job_and_stops_audio(self):
        job = self.job()
        self.c.close()
        job.cancel.assert_called_once()
        self.speech.stop.assert_called_once()
        self.recorder.close.assert_called_once()


class ResidentSpeechTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    @staticmethod
    def _write_audio(_text, audio):
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 160)

    @staticmethod
    def _wait_result(speech, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = speech.poll()
            if result is not None:
                return result
            time.sleep(.01)
        raise AssertionError("speech job did not finish")

    def test_resident_piper_loads_once_and_reuses_voice(self):
        voice = Mock()
        voice.synthesize_wav.side_effect = self._write_audio
        loader = Mock(return_value=voice)
        processes = []
        def popen(*_args, **_kwargs):
            proc = Mock()
            proc.wait.return_value = 0
            proc.poll.return_value = 0
            proc.pid = 1234 + len(processes)
            processes.append(proc)
            return proc
        speech = ResidentSpeechOutput(
            "/models/test.onnx", "test-device", self.tmp.name,
            loader=loader, popen=popen)

        speech.start("  Eins  ")
        self.assertEqual(self._wait_result(speech), 0)
        speech.start("Zwei")
        self.assertEqual(self._wait_result(speech), 0)

        loader.assert_called_once_with("/models/test.onnx")
        self.assertEqual(
            [call.args[0] for call in voice.synthesize_wav.call_args_list],
            ["Eins", "Zwei"])
        self.assertEqual(len(processes), 2)
        self.assertFalse(list(Path(self.tmp.name).glob("speech-*.wav")))

    def test_servitor_profile_filters_resident_audio_before_playback(self):
        voice = Mock()
        voice.synthesize_wav.side_effect = self._write_audio
        effect_runner = Mock()
        proc = Mock()
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        proc.pid = 5432
        popen = Mock(return_value=proc)
        speech = ResidentSpeechOutput(
            "/models/test.onnx",
            "test-device",
            self.tmp.name,
            loader=Mock(return_value=voice),
            popen=popen,
            effect_runner=effect_runner,
            profile="servitor",
        )

        speech.start("Systemstatus")
        self.assertEqual(self._wait_result(speech), 0)

        effect_runner.assert_called_once()
        sox = effect_runner.call_args.args[0]
        self.assertEqual(sox[0], "/usr/bin/sox")
        self.assertIn("tremolo", sox)
        playback = popen.call_args.args[0]
        self.assertTrue(Path(playback[-1]).name.startswith("speech-effect-"))
        self.assertFalse(list(Path(self.tmp.name).glob("speech-*.wav")))
        self.assertFalse(list(Path(self.tmp.name).glob("speech-effect-*.wav")))

    def test_cancelled_synthesis_never_starts_playback(self):
        started, release = threading.Event(), threading.Event()
        voice = Mock()
        def synthesize(text, audio):
            started.set()
            self.assertTrue(release.wait(2))
            self._write_audio(text, audio)
        voice.synthesize_wav.side_effect = synthesize
        popen = Mock()
        speech = ResidentSpeechOutput(
            "/models/test.onnx", "test-device", self.tmp.name,
            loader=Mock(return_value=voice), popen=popen)

        speech.start("Abbrechen")
        job = speech._job
        self.assertTrue(started.wait(1))
        speech.stop()
        self.assertFalse(speech.active)
        release.set()
        job.thread.join(2)

        popen.assert_not_called()
        self.assertFalse(list(Path(self.tmp.name).glob("speech-*.wav")))

    def test_stop_terminates_only_resident_playback_group(self):
        voice = Mock()
        voice.synthesize_wav.side_effect = self._write_audio
        playback_started, release = threading.Event(), threading.Event()
        proc = Mock()
        proc.pid = 4321
        proc.poll.return_value = None
        def wait(*_args, **_kwargs):
            playback_started.set()
            self.assertTrue(release.wait(2))
            return -signal.SIGTERM
        proc.wait.side_effect = wait
        speech = ResidentSpeechOutput(
            "/models/test.onnx", "test-device", self.tmp.name,
            loader=Mock(return_value=voice), popen=Mock(return_value=proc))

        speech.start("Status")
        job = speech._job
        self.assertTrue(playback_started.wait(1))
        with patch("voice_controls._terminate_process_group") as terminate:
            speech.stop()
        terminate.assert_called_once_with(proc)
        self.assertFalse(speech.active)
        release.set()
        job.thread.join(2)


class ProcessTests(unittest.TestCase):
    def test_stt_worker_returns_error_and_cancellation_marker(self):
        started, finish = threading.Event(), threading.Event()
        def transcribe(path):
            started.set()
            finish.wait(2)
            raise RuntimeError('native failure')
        job = TranscriptionJob(transcribe, '/tmp/capture.wav')
        self.assertTrue(started.wait(1))
        job.cancel()
        finish.set()
        self.assertTrue(job.done.wait(1))
        self.assertTrue(job.cancelled)
        self.assertEqual(job.error, 'native failure')
        job.thread.join(1)

    def test_speech_stop_terminates_owned_process_group(self):
        speech = SpeechOutput(f'{sys.executable} -c "import time; time.sleep(30)"')
        speech.start('status text; $(no shell)')
        proc = speech.process
        self.assertTrue(speech.active)
        speech.stop()
        self.assertIsNotNone(proc.poll())
        self.assertFalse(speech.active)
        with self.assertRaises(ProcessLookupError):
            os.killpg(proc.pid, 0)

    def test_volume_targets_only_digital_playback(self):
        with patch('voice_controls.subprocess.run') as run:
            change_volume(-1)
        self.assertEqual(run.call_args.args[0][-3:], ['sset', 'Playback', '5%-'])

    def test_speech_stop_also_kills_child_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'child.pid'
            child = (
                'import signal, time\nfrom pathlib import Path\n'
                'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                f'path = Path({str(ready)!r})\n'
                'while True:\n'
                '    path.write_text(str(time.monotonic_ns()))\n'
                '    time.sleep(.01)\n')
            parent = (
                'import subprocess, sys, time; '
                f'subprocess.Popen([sys.executable, "-c", {child!r}]); '
                'time.sleep(30)')
            speech = SpeechOutput(f'{sys.executable} -c {shlex.quote(parent)}')
            speech.start('status')
            try:
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), 'child did not initialize')
                speech.stop()
                # Check the child's activity without assuming /proc shares
                # this executor's PID namespace or how init reaps orphans.
                time.sleep(.05)
                last_write = ready.stat().st_mtime_ns
                time.sleep(.1)
                self.assertEqual(ready.stat().st_mtime_ns, last_write)
            finally:
                speech.stop()


if __name__ == '__main__':
    unittest.main()
