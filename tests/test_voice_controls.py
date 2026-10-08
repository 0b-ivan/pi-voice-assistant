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
        self.recorder.take_live_transcript.return_value = None
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
        self.c.job_stage = 'stt'
        self.c.job_started_at = time.monotonic()
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

    def test_b_discards_llm_result_without_speaking(self):
        job = self.job()
        self.c.job_stage = 'llm'
        job.result = ('NICHT SPRECHEN.', 'openai/gpt-5.4-mini')

        self.tick(down='B')
        job.cancel.assert_called_once()

        job.done.is_set.return_value = True
        self.tick()

        self.assertIsNone(self.c.job)
        names = [e['event'] for e in self.events()]
        self.assertIn('llm_discarded', names)
        self.assertNotIn('llm_response', names)
        self.speech.start.assert_not_called()

    def test_volume_and_status_stay_usable_during_stt(self):
        self.job()
        level = dict(db=-39.0, percent=35, limit=None)
        with patch('ptt.change_volume', return_value=level) as volume:
            self.tick(down='C')
            self.tick(down='C')
            self.tick()
            self.tick(down='D')
        self.assertEqual([c.args for c in volume.call_args_list], [(-1,), (1,)])
        self.tick()
        self.tick(down='E')
        self.assertIn('Direktive in Bearbeitung.', self.speech.start.call_args.args[0])
        self.tick()
        self.tick(down='BE')
        self.speech.start.assert_called_once()
        self.speech.stop.assert_called_once()

    def test_led_colors_follow_state_route_and_switch(self):
        import ptt
        ptt._display_last_error_at = None
        self.assertEqual(self.c.color, ptt.LED_READY)
        self.c.job = Mock()
        self.c.turn_route = 'server'
        with patch('ptt.time.monotonic', return_value=0.0):
            bright = self.c.color
        with patch('ptt.time.monotonic', return_value=0.7):
            dim = self.c.color
        self.assertEqual(bright, ptt.LED_SERVER)
        self.assertEqual(dim, tuple(round(v * 0.25) for v in ptt.LED_SERVER))
        self.c.turn_route = 'pi'
        with patch('ptt.time.monotonic', return_value=0.0):
            self.assertEqual(self.c.color, ptt.LED_LOCAL)
        self.c.job = None
        self.speech.active = True
        levels = []
        for level in (0.0, 0.5, 1.0):
            self.speech.voice_level = level
            levels.append(self.c.color)
        self.assertEqual(len(set(levels)), 3)  # three steps only
        self.assertEqual(levels[-1], ptt.LED_SPEAKING)
        self.speech.active = False
        self.c.remote = True
        self.c.remote_enabled = False
        self.assertEqual(self.c.color, ptt.LED_READY_LOCAL)
        self.c.led_enabled = False
        self.assertEqual(self.c.color, ptt.LED_OFF)
        proc = Mock()
        self.recorder.process = proc
        self.assertEqual(self.c.color, ptt.LED_RECORDING)  # recording always shows

    def test_led_flashes_after_errors_even_when_switched_off(self):
        import ptt
        self.c.led_enabled = False
        ptt._display_last_error_at = time.time()
        try:
            with patch('ptt.time.monotonic', return_value=0.0):
                on = self.c.color
            with patch('ptt.time.monotonic', return_value=0.3):
                off = self.c.color
        finally:
            ptt._display_last_error_at = None
        self.assertEqual((on, off), (ptt.LED_RECORDING, ptt.LED_OFF))

    def test_status_never_plays_into_recording(self):
        self.tick(down='AE')
        self.speech.start.assert_not_called()
        self.recorder.start.assert_called_once()

    def test_live_transcript_skips_second_file_transcription(self):
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        self.recorder.take_live_transcript.return_value = ('Schon erkannt', 'vosk')
        self.c.submit('release')
        self.assertIsNotNone(self.c.job)
        self.assertTrue(self.c.job.done.wait(1))
        self.assertEqual(self.c.job.result, ('Schon erkannt', 'vosk'))

    def test_errors_are_logged_and_next_job_can_run(self):
        job = self.job()
        job.done.is_set.return_value = True
        job.error = 'recognizer failure'
        self.tick()
        self.assertIsNone(self.c.job)
        self.assertIn('stt_error', [e['event'] for e in self.events()])
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_holding_volume_button_repeats_after_a_pause(self):
        level = dict(db=-39.0, percent=35, limit=None)
        with patch('ptt.change_volume', return_value=level) as volume, \
                patch('ptt.publish_display_status') as status:
            for _ in range(6):  # 12 ticks of 0.1 s with D held
                self.tick(down='D')
            self.tick()
            self.tick()
        # press at ~0.2 s, first repeat after 0.45 s, then every 0.15 s
        self.assertGreaterEqual(volume.call_count, 5)
        self.assertLessEqual(volume.call_count, 8)
        self.assertTrue(all(c.args == (1,) for c in volume.call_args_list))
        self.assertEqual(status.call_args.kwargs['volume'], 35)
        calls = volume.call_count
        self.tick()
        self.assertEqual(volume.call_count, calls)  # released: no more steps

    def test_single_press_is_a_single_step(self):
        level = dict(db=-39.0, percent=35, limit=None)
        with patch('ptt.change_volume', return_value=level) as volume:
            self.tick(down='C')
            self.tick()
            self.tick()
        self.assertEqual(volume.call_count, 1)

    def test_probe_has_no_audio_or_mixer_actions(self):
        self.c.probe = True
        with patch('ptt.change_volume') as volume:
            self.tick(down='ABCDE')
        volume.assert_not_called()
        self.recorder.start.assert_not_called()
        self.speech.start.assert_not_called()

    def test_completed_stt_runs_llm_then_starts_speech(self):
        job = self.job()
        job.done.is_set.return_value = True

        llm_job = Mock()
        llm_job.done.is_set.return_value = True
        llm_job.cancelled = False
        llm_job.error = None
        llm_job.result = ('DIREKTIVE BESTÄTIGT.', 'openai/gpt-5.4-mini')

        with patch('ptt.TranscriptionJob', return_value=llm_job) as worker:
            self.tick()

        transcript = [e for e in self.events() if e['event'] == 'transcript']
        self.assertEqual(transcript[0]['text'], 'Hallo')
        self.assertEqual(transcript[0]['provider'], 'vosk')
        worker.assert_called_once()
        self.assertEqual(worker.call_args.args[1], 'Hallo')
        self.speech.start.assert_called_once_with('DIREKTIVE BESTÄTIGT.')
        names = [e['event'] for e in self.events()]
        self.assertIn('llm_start', names)
        self.assertIn('llm_response', names)
        self.assertIn('speech_started', names)
        self.assertIn('latency', names)

        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_llm_error_is_recoverable(self):
        job = self.job()
        self.c.job_stage = 'llm'
        job.done.is_set.return_value = True
        job.error = 'request timed out'
        self.tick()
        self.assertIsNone(self.c.job)
        errors = [e for e in self.events() if e['event'] == 'llm_error']
        self.assertEqual(errors[-1]['message'], 'request timed out')
        self.speech.start.assert_not_called()
        self.tick(down='A')
        self.recorder.start.assert_called_once()

    def test_completed_speech_logs_playback_total_latency(self):
        self.speech.poll.return_value = 0
        self.c.speech_started_at = time.monotonic() - 0.05

        self.tick()

        latency = [
            e for e in self.events()
            if e['event'] == 'latency'
            and e.get('stage') == 'tts'
            and e.get('metric') == 'playback_total'
        ]
        self.assertTrue(latency)
        self.assertGreaterEqual(latency[-1]['latency_ms'], 0)
        self.assertIsNone(self.c.speech_started_at)

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

    def test_servitor_profile_streams_piper_chunks_to_ffmpeg_stdin(self):
        chunk1 = Mock(
            sample_rate=16000,
            sample_width=2,
            sample_channels=1,
            audio_int16_bytes=b"\\x01\\x00" * 160,
        )
        chunk2 = Mock(
            sample_rate=16000,
            sample_width=2,
            sample_channels=1,
            audio_int16_bytes=b"\\x02\\x00" * 160,
        )
        voice = Mock()
        voice.synthesize.return_value = [chunk1, chunk2]

        proc = Mock()
        proc.wait.return_value = 0
        proc.poll.return_value = 0
        proc.pid = 5432
        proc.stdin = Mock()
        proc.stdin.closed = False
        popen = Mock(return_value=proc)

        with patch(
            "voice_controls._servitor_synthesis_config",
            return_value=(object(), 0.32),
        ):
            speech = ResidentSpeechOutput(
                "/models/test.onnx",
                "test-device",
                self.tmp.name,
                loader=Mock(return_value=voice),
                popen=popen,
                profile="servitor",
            )

            output = StringIO()
            with redirect_stdout(output):
                speech.start("Systemstatus")
                self.assertEqual(self._wait_result(speech), 0)

        speech_events = [
            json.loads(line)
            for line in output.getvalue().splitlines()
            if line.startswith("{")
        ]
        first_chunk_latency = [
            event for event in speech_events
            if event.get("event") == "latency"
            and event.get("stage") == "tts"
            and event.get("metric") == "first_chunk"
        ]
        self.assertTrue(first_chunk_latency)
        self.assertGreaterEqual(first_chunk_latency[-1]["latency_ms"], 0)

        playback = popen.call_args.args[0]
        self.assertEqual(playback[0], "/usr/bin/ffmpeg")
        self.assertIn("pipe:0", playback)
        self.assertIn("-filter_complex", playback)
        self.assertEqual(playback[-3:], ["-f", "alsa", "test-device"])
        self.assertEqual(proc.stdin.write.call_count, 3)
        self.assertEqual(proc.stdin.write.call_args_list[0].args[0], chunk1.audio_int16_bytes)
        self.assertEqual(proc.stdin.write.call_args_list[2].args[0], chunk2.audio_int16_bytes)
        self.assertEqual(
            len(proc.stdin.write.call_args_list[1].args[0]),
            int(16000 * 0.32) * 2,
        )
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
        run = self.mixer(177)
        level = change_volume(-1, step_db=2, run=run)
        self.assertEqual(run.call_args.args[0][-3:], ['sset', 'Playback', '173'])
        self.assertEqual(level, dict(db=-41.0, percent=32, limit=None))

    @staticmethod
    def mixer(raw):
        def run(command, **kwargs):
            if 'sget' in command:
                return Mock(stdout=f"  Limits: 0 - 255\n  Front Left: {raw} [69%] [-39.00dB]\n"
                                   f"  Front Right: {raw} [69%] [-39.00dB]\n")
            return Mock()
        return Mock(side_effect=run)

    def test_volume_steps_are_small_and_clamped(self):
        up = change_volume(1, step_db=2, run=self.mixer(253))
        self.assertEqual((up['db'], up['limit']), (0.0, 'max'))
        top = self.mixer(255)
        self.assertEqual(change_volume(1, step_db=2, run=top)['limit'], 'max')
        self.assertEqual(top.call_count, 1)  # nothing to set at the top
        floor = change_volume(-1, step_db=2, run=self.mixer(137))
        self.assertEqual((floor['db'], floor['percent'], floor['limit']), (-60.0, 0, 'min'))
        below = self.mixer(100)  # set lower elsewhere: down must not raise it
        self.assertEqual(change_volume(-1, step_db=2, run=below)['db'], -77.5)
        self.assertEqual(below.call_count, 1)

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
