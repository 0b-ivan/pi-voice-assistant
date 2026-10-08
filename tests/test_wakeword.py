import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from endpoint import Endpointer, rms  # noqa: E402
from wake_listener import WakeListener  # noqa: E402
import wakeword  # noqa: E402
from wakeword import Detector, WakeWord  # noqa: E402
from menu import ITEMS  # noqa: E402
import ptt  # noqa: E402
from ptt import VoiceController  # noqa: E402


def tone(seconds, amplitude, rate=16000):
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * 220 * t) * amplitude).astype(np.int16).tobytes()


class EndpointTests(unittest.TestCase):
    def feed(self, endpointer, pcm, chunk=3200):
        result = None
        for i in range(0, len(pcm), chunk):
            result = endpointer.feed(pcm[i:i + chunk])
            if result:
                return result, i / 32000
        return result, None

    def test_speech_then_pause_ends(self):
        audio = tone(0.5, 40) + tone(1.5, 3000) + tone(2.0, 40)
        result, at = self.feed(Endpointer(silence=0.9), audio)
        self.assertEqual(result, 'end')
        self.assertAlmostEqual(at, 2.8, delta=0.15)  # 0.5 + 1.5 + 0.9

    def test_nobody_speaks_times_out(self):
        result, at = self.feed(Endpointer(onset_timeout=2.0), tone(3.0, 40))
        self.assertEqual(result, 'timeout')
        self.assertAlmostEqual(at, 1.9, delta=0.15)

    def test_short_pauses_inside_speech_do_not_end(self):
        audio = tone(1.0, 3000) + tone(0.5, 40) + tone(1.0, 3000) + tone(0.3, 40)
        self.assertIsNone(self.feed(Endpointer(silence=0.9), audio)[0])

    def test_rms(self):
        self.assertEqual(rms(b''), 0.0)
        self.assertAlmostEqual(rms(tone(0.1, 1000)), 707, delta=10)


class FakeSession:
    """Stands in for the three ONNX models with the real shapes."""
    def __init__(self, kind, calls):
        self.kind, self.calls = kind, calls

    def get_inputs(self):
        return [Mock(name='input')]

    def run(self, _outputs, feeds):
        self.calls[self.kind] += 1
        x = next(iter(feeds.values()))
        if self.kind == 'mel':
            frames = (x.shape[1] - 400) // 160 + 1
            return [np.zeros((1, 1, frames, 32), dtype=np.float32)]
        if self.kind == 'embedding':
            return [np.zeros((1, 1, 1, 96), dtype=np.float32)]
        return [np.array([[0.9]], dtype=np.float32)]


def fake_wakeword(gate):
    calls = {'mel': 0, 'embedding': 0, 'word': 0}

    def session(path):
        name = Path(path).name
        kind = 'mel' if name.startswith('mel') else 'embedding' if name.startswith('emb') else 'word'
        return FakeSession(kind, calls)
    word = WakeWord('/models', 'hey_jarvis_v0.1', session=session, gate=gate)
    for key in calls:
        calls[key] = 0
    return word, calls


class WakeWordGateTests(unittest.TestCase):
    def test_quiet_blocks_skip_embedding_and_classifier(self):
        word, calls = fake_wakeword(gate=True)
        quiet = (np.random.default_rng(0).normal(0, 30, 1280 * 50)).astype(np.int16)
        scores = [word.process(quiet[i:i + 1280]) for i in range(0, len(quiet), 1280)]
        self.assertEqual(scores[-1], 0.0)
        self.assertEqual(calls['mel'], 50)          # cheap path keeps running
        self.assertLessEqual(calls['embedding'], 3)  # only silence refreshes
        self.assertEqual(calls['word'], 0)

    def test_loud_blocks_are_processed_exactly_with_hangover(self):
        word, calls = fake_wakeword(gate=True)
        quiet = np.zeros(1280 * 10, dtype=np.int16) + 20
        loud = np.frombuffer(tone(1280 * 5 / 16000, 4000), dtype=np.int16)
        for audio in (quiet, loud, np.zeros(1280 * 20, dtype=np.int16) + 20):
            for i in range(0, len(audio), 1280):
                score = word.process(audio[i:i + 1280])
        self.assertEqual(word.exact_blocks, 5 + wakeword.GATE_HANGOVER)
        self.assertEqual(calls['word'], 5 + wakeword.GATE_HANGOVER)

    def test_without_gate_every_block_is_exact(self):
        word, calls = fake_wakeword(gate=False)
        word.process(np.zeros(1280 * 7, dtype=np.int16))
        self.assertEqual((calls['embedding'], calls['word']), (7, 7))

    def test_detector_patience_and_cooldown(self):
        scores = iter([0.9, 0.2, 0.9, 0.9, 0.9, 0.9])
        detector = Detector(Mock(process=lambda _pcm: next(scores)), threshold=0.5,
                            patience=2, cooldown=2.0)
        results = [detector.feed(b'', t) for t in (0, 0.08, 0.16, 0.24, 0.32, 2.5)]
        self.assertEqual(results, [False, False, False, True, False, False])


class FakeProcess:
    def __init__(self, chunks):
        self.stdout = Mock()
        self.stdout.read.side_effect = chunks + [b''] * 100
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


class WakeListenerTests(unittest.TestCase):
    def test_detection_frees_microphone_and_reports_once(self):
        process = FakeProcess([b'a' * 2560, b'b' * 2560, b'c' * 2560])
        detector = Mock()
        detector.feed.side_effect = [False, True]
        listener = WakeListener('dev', lambda: detector, popen=Mock(return_value=process))
        listener.start()
        listener.thread.join(2)
        self.assertTrue(process.terminated)
        self.assertTrue(listener.take_detection())
        self.assertFalse(listener.take_detection())
        self.assertFalse(listener.running)

    def test_stop_during_a_blocked_read_is_no_error(self):
        import threading
        reading = threading.Event()

        class Stream:
            def __init__(self):
                self.closed = threading.Event()

            def read(self, size):
                reading.set()
                self.closed.wait(2)
                raise ValueError('read of closed file')

            def close(self):
                self.closed.set()

        process = Mock(stdout=Stream())
        process.poll.return_value = 0
        listener = WakeListener('dev', lambda: Mock(), popen=Mock(return_value=process))
        listener.start()
        self.assertTrue(reading.wait(2))
        listener.stop()
        self.assertIsNone(listener.error)
        self.assertFalse(listener.running)

    def test_errors_are_reported(self):
        listener = WakeListener('dev', Mock(side_effect=RuntimeError('model missing')),
                                popen=Mock())
        listener.start()
        listener.thread.join(2)
        self.assertEqual(listener.error, 'model missing')


class ControllerWakeTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.recorder.endpoint_result = None
        self.recorder.take_live_transcript.return_value = None
        self.speech.active = False
        self.speech.synthesizing = False
        self.speech.poll.return_value = None
        self.wake = Mock(running=False, error=None, detector=None)
        self.wake.take_detection.return_value = False
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(Path(self.tmp.name) / 'status.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.wake.start.side_effect = lambda: setattr(self.wake, 'running', True)
        self.wake.stop.side_effect = lambda: setattr(self.wake, 'running', False)
        self.c = VoiceController(self.recorder, self.speech, .04, 30, wake=self.wake,
                                 wake_word='hey_jarvis')
        self.now = 10.0
        # Buttons must be seen released once before they can trigger.
        self.c.tick(False, (False,) * 5, self.now)
        self.c.tick(False, (False,) * 5, self.now + 0.05)
        self.now += 0.05
        self.wake.start.reset_mock()
        self.wake.stop.reset_mock()

    def tick(self, down='', gpio=False):
        self.now += 0.1
        self.c.tick(gpio, tuple(x in down for x in 'ABCDE'), self.now)

    def events(self):
        return [json.loads(l)['event'] for l in self.output.getvalue().splitlines()
                if l.startswith('{')]

    def test_listens_only_when_idle(self):
        self.wake.running = False
        self.tick()
        self.wake.start.assert_called_once()
        self.speech.active = True
        self.tick()
        self.wake.stop.assert_called_once()

    def test_wake_word_records_until_pause_then_submits(self):
        self.wake.take_detection.return_value = True
        self.tick()
        self.recorder.start.assert_called_once_with(auto_stop=True)
        self.assertIn('wake', self.events())
        self.wake.take_detection.return_value = False
        self.recorder.process = Mock(poll=Mock(return_value=None))
        self.tick()
        self.recorder.finish.assert_not_called()
        self.recorder.endpoint_result = 'end'
        self.recorder.finish.return_value = None
        self.tick()
        self.recorder.finish.assert_called_once_with('silence')

    def test_nobody_speaks_cancels_quietly(self):
        self.wake.take_detection.return_value = True
        self.tick()
        self.wake.take_detection.return_value = False
        self.recorder.process = Mock(poll=Mock(return_value=None))
        self.recorder.endpoint_result = 'timeout'
        self.tick()
        self.recorder.finish.assert_called_once_with('wake_timeout', publish=False)
        self.assertIn('wake_timeout', self.events())
        self.assertFalse(self.c.wake_recording)

    def test_button_during_wake_recording_takes_over(self):
        self.wake.take_detection.return_value = True
        self.tick()
        self.wake.take_detection.return_value = False
        self.recorder.process = Mock(poll=Mock(return_value=None))
        self.tick(gpio=False)
        for _ in range(3):
            self.tick(gpio=True)
        self.assertFalse(self.c.wake_recording)
        self.assertEqual(self.recorder.start.call_count, 1)  # no second recording

    def test_button_frees_microphone_before_recording(self):
        self.tick()
        self.assertTrue(self.wake.running)
        for _ in range(3):
            self.tick(gpio=True)
        self.wake.stop.assert_called()
        self.recorder.start.assert_called_once_with()

    def test_echo_pause_after_own_speech(self):
        self.wake.running = False
        self.speech.poll.return_value = 0
        self.tick()
        self.speech.poll.return_value = None
        self.wake.start.reset_mock()
        self.tick()
        self.wake.start.assert_not_called()  # still inside WAKE_ECHO_PAUSE
        for _ in range(7):
            self.tick()
        self.wake.start.assert_called_once()

    def test_menu_switch(self):
        self.c.menu.show(self.now)
        self.c.menu.index = ITEMS.index('wake')
        self.assertTrue(self.wake.running)
        self.tick(down='E')
        self.tick(down='E')
        self.tick()
        self.assertFalse(self.c.wake_enabled)
        self.wake.stop.assert_called()
        status = json.loads((Path(self.tmp.name) / 'status.json').read_text())
        self.assertEqual((status['opt_wake'], status['wake_word']), ('off', 'hey_jarvis'))



class StandbyVoskTests(unittest.TestCase):
    def controller(self, state):
        recorder, speech = Mock(), Mock()
        recorder.process = None
        speech.active = False
        speech.synthesizing = False
        speech.poll.return_value = None
        c = VoiceController(recorder, speech, .04, 30, remote=True)
        c.server_probe = Mock(state=state)
        return c

    def test_standby_vosk_only_while_server_is_down(self):
        with patch.dict(os.environ, {'PTT_MEMORY_MODE': 'hybrid'}), \
                patch('ptt.prepare_vosk_worker') as prepare, \
                patch('ptt.stop_prepared_vosk') as stop, redirect_stdout(StringIO()):
            self.controller('ok').tick(False, (False,) * 5, 1.0)
            stop.assert_called()
            prepare.assert_not_called()
            stop.reset_mock()
            self.controller('down').tick(False, (False,) * 5, 1.0)
            prepare.assert_called_once()
            stop.assert_not_called()

    def test_server_state_prefers_probe(self):
        c = self.controller('down')
        self.assertEqual(c.server_state(), 'down')
        c.remote_enabled = False
        self.assertEqual(c.server_state(), 'off')


if __name__ == '__main__':
    unittest.main()


class ShadowWordTests(unittest.TestCase):
    def test_shadow_word_is_logged_but_never_triggers(self):
        import wakeword
        ww = Mock(shadow_scores={"proximus": 0.0}, segment_peaks=[])
        scores = iter([(0.1, 0.9), (0.1, 0.95), (0.1, 0.2)])

        def process(pcm):
            main, shadow = next(scores)
            ww.shadow_scores = {"proximus": shadow}
            return main

        ww.process.side_effect = process
        detector = wakeword.Detector(ww, threshold=0.5, shadow_thresholds={"proximus": 0.7})
        fired = [detector.feed(b"", t) for t in (1.0, 1.1, 1.2)]
        self.assertEqual(fired, [False, False, False])
        self.assertEqual(detector.shadow_hits, [("proximus", 0.95)])


class ActiveExtraWordTests(unittest.TestCase):
    def test_active_extra_word_triggers_with_its_own_patience(self):
        import wakeword
        ww = Mock(shadow_scores={"proximus": 0.0}, segment_peaks=[], word="hey_jarvis_v0.1")
        values = iter([0.95, 0.3, 0.95])

        def process(pcm):
            ww.shadow_scores = {"proximus": next(values)}
            return 0.1

        ww.process.side_effect = process
        detector = wakeword.Detector(ww, threshold=0.5, shadow_thresholds={"proximus": 0.9},
                                     active={"proximus": 1})
        self.assertTrue(detector.feed(b"", 1.0))
        self.assertEqual(detector.last_word, "proximus")
        self.assertFalse(detector.feed(b"", 1.1))   # cooldown
        self.assertFalse(detector.feed(b"", 1.2))
