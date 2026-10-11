import json
import os
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import memory  # noqa: E402
import people  # noqa: E402
import speaker  # noqa: E402

IVAN = speaker.normalize([1.0, 0.5, 0.0, 0.2] * 128)
OTHER = speaker.normalize([-1.0, 0.2, 0.9, 0.0] * 128)
CLOSE = speaker.normalize([1.0, 0.5, 0.0, 0.2] * 64 + [-0.3, 0.9, 0.4, -0.6] * 64)


class IO:
    def __init__(self, voice, words):
        self.voice, self.words, self.said = voice, list(words), []

    def say(self, text):
        self.said.append(text)

    def beep(self):
        pass

    def record(self, path, seconds):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(bytes(16000 * 2))

    def transcribe(self, pcm):
        return self.words.pop(0) if self.words else None

    def voiceprint(self, pcm):
        return speaker.encode(self.voice)

    def publish(self, **state):
        pass


def pcm(*parts):
    """(seconds, amplitude) pieces -> 16 kHz int16 PCM (a 440 Hz tone or silence)."""
    import math
    from array import array
    samples = array("h")
    for seconds, amplitude in parts:
        samples.extend(int(amplitude * math.sin(2 * math.pi * 440 * i / 16000))
                       for i in range(int(seconds * 16000)))
    return samples.tobytes()


class MatchingTests(unittest.TestCase):
    def test_phrase_tolerates_split_words_and_fillers(self):
        stored = "omnissiah segne diese maschine"
        self.assertTrue(people.phrase_matches("omni sia segne diese maschine", stored))
        self.assertTrue(people.phrase_matches("äh omnissiah segne diese maschine ok", stored))
        self.assertTrue(people.phrase_matches("omnissiah segnet diese maschine", stored))
        self.assertFalse(people.phrase_matches("hallo welt", stored))
        self.assertFalse(people.phrase_matches("diese maschine", stored))
        self.assertFalse(people.phrase_matches("", stored))

    def test_trim_silence_keeps_speech_only(self):
        take = pcm((2.0, 0), (1.5, 8000), (2.5, 30))
        trimmed = speaker.trim_silence(take)
        self.assertAlmostEqual(len(trimmed) / 32000, 1.5 + 2 * 0.2, delta=0.1)

    def test_trim_silence_never_goes_below_a_second(self):
        take = pcm((2.0, 0), (0.3, 8000), (2.0, 0))
        self.assertEqual(speaker.trim_silence(take), take)
        silent = pcm((4.0, 0))
        self.assertEqual(speaker.trim_silence(silent), silent)

    def test_trim_silence_short_takes_with_lower_minimum(self):
        take = pcm((0.6, 0), (0.7, 8000), (0.7, 0))      # one 2 s "Proximus" take
        trimmed = speaker.trim_silence(take, min_seconds=0.3)
        self.assertAlmostEqual(len(trimmed) / 32000, 0.7 + 2 * 0.2, delta=0.1)


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "proximus"
        root.mkdir()
        (Path(self.tmp.name) / "dev").touch()
        self.core = memory.MemoryCore(root, Path(self.tmp.name) / "dev")
        self.core.save_voiceprint("Ivan", speaker.encode(IVAN), 7)

    def flow(self, voice, words, action, io=None):
        io = io or IO(voice, words)
        self.log = StringIO()
        with redirect_stdout(self.log):
            return people.Flow(self.core, io, "Ivan", action).run(), io

    def events(self):
        return [json.loads(line) for line in self.log.getvalue().splitlines()]

    def test_second_attempt_after_a_miss(self):
        self.core.update_profile("Ivan", passphrase="omnissiah segne diese maschine")
        io = IO(IVAN, ["hallo welt", "omnissiah segne diese maschine"])
        result, io = self.flow(None, None, "details", io)
        self.assertTrue(result["auth"])
        self.assertIn(people.AUTH_RETRY, io.said)
        self.assertEqual([e["result"] for e in self.events()], ["failed", "ok"])

    def test_auth_log_has_scores_but_never_the_phrase(self):
        self.core.update_profile("Ivan", passphrase="omnissiah segne diese maschine")
        result, io = self.flow(OTHER, ["omnissiah segne diese maschine"] * 2, "details")
        self.assertFalse(result["auth"])
        self.assertEqual(io.said[-1], people.AUTH_FAILED)
        events = self.events()
        self.assertEqual(len(events), people.ATTEMPTS)
        self.assertEqual(events[0]["phrase"], 1.0)
        self.assertLess(events[0]["voice"], speaker.THRESHOLD)
        self.assertNotIn("omnissiah", self.log.getvalue())
        self.assertLess(self.core.profile("Ivan")["last_score"], speaker.THRESHOLD)

    def test_first_time_voice_only_then_passphrase_is_required(self):
        result, io = self.flow(IVAN, ["omnissiah segne diese maschine"] * 2, "details")
        self.assertEqual((result["auth"], result["action"], result["passphrase"]),
                         (True, "passphrase", True))
        self.assertEqual(self.core.profile("Ivan")["passphrase"], "omnissiah segne diese maschine")
        self.assertFalse((self.core.voice_dir / "auth.wav").exists())

    def test_phrase_and_voice_needed(self):
        self.core.update_profile("Ivan", passphrase="omnissiah segne diese maschine")
        result, io = self.flow(IVAN, ["omnissiah segnet diese maschine"], "details")
        self.assertTrue(result["auth"])
        self.assertIn("Ivan: 7 Aufnahmen", io.said[-1])
        result, io = self.flow(IVAN, ["hallo welt"], "details")
        self.assertFalse(result["auth"])
        result, io = self.flow(OTHER, ["omnissiah segne diese maschine"], "details")
        self.assertFalse(result["auth"])

    def test_weak_voice_with_right_phrase_suggests_retraining(self):
        self.core.update_profile("Ivan", passphrase="omnissiah segne diese maschine")
        score = speaker.cosine(CLOSE, IVAN)
        self.assertTrue(people.WEAK_VOICE <= score < speaker.THRESHOLD, score)
        result, io = self.flow(CLOSE, ["omnissiah segne diese maschine"], "refine")
        self.assertEqual((result["auth"], result["weak"], result.get("refine")), (True, True, True))
        self.assertIn(people.AUTH_WEAK, io.said)

    def test_browser_navigation(self):
        browser = people.Browser()
        browser.open(["Ivan", "Anna"])
        self.assertEqual(browser.view()["items"], ["Ivan", "Anna", "Zurück"])
        browser.move(1)
        self.assertEqual(browser.select(), ("person", "Anna"))
        browser.move(2)
        self.assertEqual(browser.select(), ("action", "passphrase"))
        self.assertFalse(browser.back())
        self.assertEqual(browser.page, "list")
        self.assertTrue(browser.back())
        self.assertFalse(browser.active)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        import ptt
        self.ptt = ptt
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "PTT_DISPLAY_STATUS_PATH": str(Path(self.tmp.name) / "status.json"),
            "PTT_DISPLAY_EVENT_PATH": str(Path(self.tmp.name) / "event.json"),
            "PTT_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        context = redirect_stdout(StringIO())
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        recorder, speech = Mock(), Mock()
        recorder.process = None
        speech.active = False
        speech.synthesizing = False
        self.c = ptt.VoiceController(recorder, speech, .04, 30)
        root = Path(self.tmp.name) / "proximus"
        root.mkdir()
        (Path(self.tmp.name) / "dev").touch()
        self.c.memory = memory.MemoryCore(root, Path(self.tmp.name) / "dev")
        self.c.memory.save_voiceprint("Ivan", speaker.encode(IVAN), 7)
        self.said = []
        self.c._say = self.said.append

    def view(self):
        return json.loads((Path(self.tmp.name) / "display-people.json").read_text())

    def test_delete_needs_auth_and_button_confirmation(self):
        self.c._open_people()
        self.assertEqual(self.view()["items"], ["Ivan", "Zurück"])
        self.c._people_buttons(True, False, 0)                 # E on Ivan
        self.assertEqual(self.view()["page"], "actions")
        flow = people.Flow(self.c.memory, Mock(), "Ivan", "delete")
        self.c._people_flow_done(flow, dict(auth=True, delete_pending=True), 10)
        self.assertTrue(self.view()["confirm_delete"])
        self.c._people_buttons(True, False, 11)                # E: delete
        self.assertEqual(self.c.memory.people(), [])
        self.assertIn(people.DELETED, self.said)

    def test_failed_auth_changes_nothing(self):
        self.c._open_people()
        self.c._people_buttons(True, False, 0)
        flow = people.Flow(self.c.memory, Mock(), "Ivan", "delete")
        self.c._people_flow_done(flow, dict(auth=False), 10)
        self.assertFalse(self.view()["confirm_delete"])
        self.c._people_buttons(True, False, 11)                # E now: just an action choice
        self.assertEqual(len(self.c.memory.people()), 1)

    def test_authenticated_refine_starts_a_targeted_session(self):
        flow = people.Flow(self.c.memory, Mock(), "Ivan", "refine")
        with patch.object(self.c, "_start_enroll") as start:
            self.c._people_flow_done(flow, dict(auth=True, weak=True, refine=True), 0)
        start.assert_called_once_with("refine", target="Ivan")
        self.assertTrue(self.c.people.weak)


if __name__ == "__main__":
    unittest.main()


class MicrophoneTests(ControllerTests):
    def test_wake_listener_stays_off_while_a_flow_runs(self):
        wake = Mock(running=False, error=None, detector=None)
        wake.take_detection.return_value = False
        self.c.wake = wake
        self.c.wake_enabled = True
        self.c.wake_resume_at = 0
        self.c.enroll = Mock(running=True)
        self.c._wake_tick(100.0)
        wake.start.assert_not_called()
        self.c.enroll = None
        self.c._wake_tick(101.0)
        wake.start.assert_called_once()
