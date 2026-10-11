import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import enroll  # noqa: E402
import memory  # noqa: E402
import speaker  # noqa: E402
import voice_quality  # noqa: E402


def tone(*parts):
    """(seconds, amplitude) pieces -> 16 kHz int16 PCM (a 440 Hz tone or silence)."""
    import math
    from array import array
    samples = array("h")
    for seconds, amplitude in parts:
        samples.extend(int(amplitude * math.sin(2 * math.pi * 440 * i / 16000))
                       for i in range(int(seconds * 16000)))
    return samples.tobytes()


def speech_take(seconds):
    """A clip like a real take: quiet after the beep, speech, quiet."""
    spoken = min(3.0, seconds / 2)
    return tone((0.5, 0), (spoken, 8000), (seconds - 0.5 - spoken, 0))


class FakeIO:
    def __init__(self, answers, cancel_after=None, take=None):
        self.answers, self.said, self.recorded = list(answers), [], []
        self.cancel_after, self.session = cancel_after, None
        self.states = []
        self.take = take        # PCM for every recording (default: speech_take)
        self.printed = []

    def say(self, text):
        self.said.append(text)

    def beep(self):
        pass

    def record(self, path, seconds):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(self.take if self.take is not None else speech_take(seconds))
        self.recorded.append(Path(path))
        if self.cancel_after is not None and len(self.recorded) >= self.cancel_after:
            self.session.cancel()

    def transcribe(self, pcm):
        return self.answers.pop(0) if self.answers else None

    def voiceprint(self, pcm):
        self.printed.append(len(pcm))
        return speaker.encode([1.0, 0.5, 0.0, 0.2] * 4)

    def publish(self, **state):
        self.states.append(state)


class RecordRetryTests(unittest.TestCase):
    def test_busy_microphone_is_retried(self):
        import os
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        import ptt

        calls = []

        class Proc:
            def __init__(self, args, **kwargs):
                calls.append(args)
                self.returncode = 1 if len(calls) < 3 else 0
                if self.returncode == 0:
                    Path(args[-1]).write_bytes(b"RIFF")

            def communicate(self, timeout=None):
                return b"", b"audio open error: Device or resource busy"

        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"PTT_RUNTIME_DIR": tmp}), \
                patch.object(ptt.subprocess, "Popen", Proc), patch.object(ptt, "event") as log, \
                patch.object(ptt.time, "sleep"):
            io = ptt.EnrollIO()
            self.assertTrue(io.record(Path(tmp) / "x.wav", 2))
        self.assertEqual(len(calls), 3)
        self.assertEqual(log.call_count, 2)
        self.assertIn("busy", log.call_args.kwargs["message"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "proximus"
        root.mkdir()
        device = Path(self.tmp.name) / "dev"
        device.touch()
        self.core = memory.MemoryCore(root, device)

    def test_full_session_stores_clips_facts_directive_and_voiceprint(self):
        answers = ["nenn mich ivan", "in berlin", "software", "raumfahrt", "meine familie",
                   "knapp", "ich mag warhammer"]
        io = FakeIO(answers)
        session = enroll.Session(self.core, io)
        result = session.run()
        self.assertEqual(result["name"], "Ivan")
        self.assertEqual(len(list((self.core.voice_dir / "wake").glob("*.wav"))), enroll.WAKE_COUNT)
        self.assertEqual(len(list((self.core.voice_dir / "answers").glob("*.wav"))),
                         len(enroll.QUESTIONS))
        context = self.core.context()
        self.assertIn("Name des Bedieners: Ivan", context["facts"])
        self.assertIn("Antwortstil nach Wunsch des Bedieners: knapp", context["directives"])
        self.assertEqual(context["voiceprints"][0]["name"], "Ivan")
        self.assertIn("Stimmprofil gespeichert", io.said[-1])
        self.assertEqual(io.states[-1], dict(stage=None))

    def run_quiet(self, session):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = session.run()
        return result, [json.loads(l) for l in out.getvalue().splitlines() if l.startswith("{")]

    def test_bad_clips_give_no_voiceprint_but_numbers_in_the_log(self):
        clipped = tone((0.5, 0), (3.0, 32767), (2.5, 0))
        io_ = FakeIO(["nenn mich ivan"] + ["x"] * 6, take=clipped)
        result, logged = self.run_quiet(enroll.Session(self.core, io_))
        self.assertFalse(result["voiceprint"])
        self.assertEqual(io_.printed, [])
        self.assertIn("Name des Bedieners: Ivan", self.core.context()["facts"])
        answers = [e for e in logged if e.get("event") == "enroll_clip" and e["kind"] == "answer"]
        self.assertEqual(len(answers), len(enroll.QUESTIONS))
        self.assertEqual(answers[0]["problems"][0], "clipping")
        self.assertNotIn("ivan", json.dumps(logged).lower())       # numbers, no content

    def test_silent_and_broken_clips_are_deleted(self):
        io_ = FakeIO(["nenn mich ivan"] * 7, take=bytes(16000 * 2 * 6))
        result, _ = self.run_quiet(enroll.Session(self.core, io_))
        self.assertEqual(result["facts"], 0)                      # nothing transcribed
        self.assertEqual(list((self.core.voice_dir / "answers").glob("*.wav")), [])
        path = Path(self.tmp.name) / "eight-bit.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(1)
            out.setframerate(16000)
            out.writeframes(bytes(16000))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(enroll._read(path, "answer", 0))
        self.assertFalse(path.exists())

    def test_cancel_stops_the_session(self):
        io = FakeIO([], cancel_after=3)
        session = enroll.Session(self.core, io)
        io.session = session
        session.start()
        session.thread.join(5)
        self.assertEqual(session.result, dict(cancelled=True))
        self.assertEqual(io.said[-1], enroll.CANCELLED)
        self.assertEqual(len(io.recorded), 3)
        self.assertEqual(self.core.voiceprints(), [])

    def test_commands_and_names(self):
        self.assertEqual(enroll.command("lerne mich kennen"), "enroll")
        self.assertEqual(enroll.command("lerne meine stimme"), "enroll")
        self.assertEqual(enroll.command("starte das stimmtraining"), "refine")
        self.assertEqual(enroll.command("stimmprofil nachtrainieren"), "refine")
        self.assertIsNone(enroll.command("wie spät ist es"))
        self.assertEqual(enroll.name_from("du kannst mich ivan nennen"), "Ivan Nennen")
        self.assertEqual(enroll.name_from("ich heiße ivan"), "Ivan")


class ProfileTests(SessionTests):
    def test_refine_merges_into_the_matching_profile_and_people_are_listed(self):
        ivan = speaker.normalize([1.0, 0.5, 0.0, 0.2] * 4)
        anna = speaker.normalize([-1.0, 0.2, 0.9, 0.0] * 4)
        self.core.save_voiceprint("Ivan", speaker.encode(ivan), 7)
        self.core.save_voiceprint("Anna", speaker.encode(anna), 7)
        io = FakeIO([])                         # its voiceprint() returns Ivan's direction
        result = enroll.Session(self.core, io, mode="refine").run()
        self.assertEqual(result["refined"], "Ivan")
        self.assertEqual(dict(self.core.people()), {"Ivan": 11, "Anna": 7})
        self.assertIn("Stimmprofil Ivan verfeinert", io.said[-1])
        reply = memory.reply("list_people", "", self.core.context())
        self.assertEqual(reply, "2 Personen an der Stimme bekannt: Anna, Ivan.")
        self.assertEqual(memory.command("wen kennst du"), ("list_people", ""))

    def test_refine_without_matching_profile_changes_nothing(self):
        other = speaker.normalize([-1.0, 0.2, 0.9, 0.0] * 4)
        self.core.save_voiceprint("Anna", speaker.encode(other), 7)
        io = FakeIO([])
        result = enroll.Session(self.core, io, mode="refine").run()
        self.assertIsNone(result["refined"])
        self.assertEqual(io.said[-1], enroll.NO_MATCH)
        self.assertEqual(dict(self.core.people()), {"Anna": 7})

    def test_first_version_profile_is_moved(self):
        voice = self.core.voice_dir
        voice.mkdir(parents=True)
        (voice / "voiceprint.json").write_text(json.dumps(
            dict(name="Ivan", print=speaker.encode([1.0, 0.5, 0.0, 0.2] * 4), count=7)))
        self.assertEqual(self.core.people(), [("Ivan", 7)])
        self.core.save_voiceprint("Ivan", speaker.encode([1.0, 0.5, 0.0, 0.2] * 4), 4)
        self.assertFalse((voice / "voiceprint.json").exists())
        self.assertEqual(self.core.people(), [("Ivan", 11)])


class SpeakerTests(unittest.TestCase):
    def test_encoding_and_identification(self):
        a = speaker.normalize([1.0, 0.2, -0.3, 0.0] * 128)
        b = speaker.normalize([-0.5, 1.0, 0.1, 0.4] * 128)
        decoded = speaker.decode(speaker.encode(a))
        self.assertGreater(speaker.cosine(a, decoded), 0.999)
        prints = [dict(name="Ivan", vector=decoded)]
        self.assertEqual(speaker.identify(a, prints)[0], "Ivan")
        self.assertIsNone(speaker.identify(b, prints)[0])
        self.assertIsNone(speaker.decode("not base64!"))
        self.assertLess(len(speaker.encode(a)), 1500)

    def test_decide_names_a_person_only_for_a_clear_match(self):
        a = speaker.normalize([1.0, 0.2, -0.3, 0.0] * 128)
        b = speaker.normalize([-0.5, 1.0, 0.1, 0.4] * 128)
        result = speaker.decide(a, [dict(name="Ivan", vector=a), dict(name="Anna", vector=b)])
        self.assertEqual((result["status"], result["name"]), (speaker.MATCHED, "Ivan"))
        self.assertIsNotNone(result["second"])
        result = speaker.decide(b, [dict(name="Ivan", vector=a)])
        self.assertEqual((result["status"], result["name"]), (speaker.UNKNOWN, None))
        # Two profiles above the threshold and too close to each other: nobody.
        near = speaker.normalize([1.0, 0.21, -0.3, 0.0] * 128)
        result = speaker.decide(a, [dict(name="Ivan", vector=a), dict(name="Anna", vector=near)])
        self.assertEqual((result["status"], result["name"]), (speaker.AMBIGUOUS, None))
        self.assertEqual(speaker.decide(None, [dict(name="Ivan", vector=a)])["status"],
                         speaker.UNAVAILABLE)
        self.assertEqual(speaker.decide(a, [])["status"], speaker.UNAVAILABLE)
        # Exactly at the threshold counts (score >= threshold).
        exact = speaker.cosine(a, a)
        self.assertEqual(speaker.decide(a, [dict(name="Ivan", vector=a)], threshold=exact)["status"],
                         speaker.MATCHED)

    def test_speech_seconds_counts_speech_not_file_length(self):
        quiet, loud = b"\0\0" * 16000, b"\x40\x1f\xc0\xe0" * 8000   # 1 s silence, 1 s tone
        self.assertEqual(speaker.speech_seconds(b""), 0.0)
        self.assertEqual(speaker.speech_seconds(quiet * 10), 0.0)
        self.assertEqual(speaker.speech_seconds(b"\1\1" * 160000), 0.0)    # constant hum, 10 s
        # 0.6 s of speech inside 10 s of PCM stays far below one second.
        short = quiet * 5 + loud[:int(len(loud) * 0.6) // 4 * 4] + quiet * 5
        self.assertLess(speaker.speech_seconds(short), speaker.MIN_SECONDS)
        self.assertAlmostEqual(speaker.speech_seconds(quiet + loud * 2 + quiet), 2.0, delta=0.1)
        # Spoken without pauses: no quiet tenth, still speech.
        self.assertAlmostEqual(speaker.speech_seconds(loud * 3), 3.0, delta=0.1)

    def test_guest_gets_no_personal_memory(self):
        copy = dict(facts=["Name des Bedieners: Ivan"],
                    directives=["Städte heißen Makropolen", "Humor-Erweiterung installiert",
                                "Nenne mich Meister Ivan"],
                    history=[dict(q="q", a="a")], total_facts=1)
        guest = memory.guest_view(copy)
        prompt = memory.prompt_section(guest)
        self.assertNotIn("Ivan", prompt)
        # Only installed persona modules are public; other directives may be personal.
        self.assertEqual(guest["directives"], ["Humor-Erweiterung installiert"])
        self.assertNotIn("Makropolen", prompt)
        self.assertEqual(memory.guest_view(copy, "insufficient")["identity"], "insufficient")
        self.assertEqual(memory.history_messages(guest), [])
        self.assertEqual(memory.reply("recall", "", guest), memory.GUEST_TEXT)
        self.assertIn("erkannt als Ivan", memory.prompt_section(dict(copy, speaker="Ivan")))


class ServerSpeakerTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import http.client
        import servitor_server as ss
        from test_servitor_server import TOKEN, FakePipeline
        self.http, self.token = http.client, TOKEN
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config({"SERVITOR_API_TOKEN": TOKEN, "SERVITOR_WORKDIR": self.tmp.name})
        self.pipeline = FakePipeline(self.tmp.name)
        self.pipeline.transcript = "wie heiße ich"
        self.service = ss.Service(config, self.pipeline)
        self.service.ready = True
        self.voice = speaker.normalize([1.0, 0.0, 0.5, 0.25] * 128)

        class Embedder:
            available = True
            vector = self.voice
            error = None
            inputs = []

            def embed(inner, pcm):
                inner.seen = len(pcm)
                inner.inputs.append(bytes(pcm))
                if inner.error is not None:
                    raise inner.error
                return inner.vector if len(pcm) >= 32000 else None

        self.service.embedder = Embedder()
        self.server = ss.Server(("127.0.0.1", 0), ss.Handler)
        self.server.service = self.service
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    SPEECH = b"\x40\x1f\xc0\xe0" * 16000          # 2 s at amplitude 8000

    def turn(self, body=SPEECH, copy=None):
        if copy is None:
            copy = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[],
                        total_facts=1,
                        voiceprints=[dict(name="Ivan", print=speaker.encode(self.voice))])
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/turn", body=body, headers={
            "Authorization": f"Bearer {self.token}", "X-Servitor-Status": json.dumps({"memory": "on"}),
            "X-Servitor-Memory": memory.encode_header(copy)})
        data = conn.getresponse().read()
        conn.close()
        return [json.loads(line) for line in data.decode().splitlines() if line]

    def test_operator_is_recognized_and_strangers_get_the_guest_view(self):
        events = self.turn()
        heard = next(e for e in events if e["event"] == "speaker")
        self.assertTrue(heard["known"])
        self.assertGreater(heard["score"], 0.99)
        self.assertEqual(self.pipeline.memory["speaker"], "Ivan")
        self.service.embedder.vector = speaker.normalize([-1.0, 0.3, 0.0, -0.2] * 128)
        events = self.turn()
        self.assertTrue(any(e["event"] == "speaker" and not e["known"] for e in events))
        self.assertEqual(self.pipeline.memory["facts"], [])

    def assert_guest(self, events, status):
        heard = next(e for e in events if e["event"] == "speaker")
        self.assertEqual((heard["status"], heard["known"]), (status, False))
        self.assertEqual(self.pipeline.memory["speaker"], "unknown")
        self.assertEqual(self.pipeline.memory["facts"], [])
        self.assertEqual(self.pipeline.memory["identity"], status)

    def test_too_little_speech_is_no_operator(self):
        # 0.6 s of speech in 4 s of PCM: formally long enough, still a guest.
        quiet = b"\0\0" * 16000
        self.assert_guest(self.turn(quiet * 2 + self.SPEECH[:19200] + quiet * 2), "insufficient")
        self.assert_guest(self.turn(b"\0\0" * 64000), "insufficient")      # 4 s of silence
        self.assert_guest(self.turn(b"\1" * 64000), "insufficient")        # constant hum

    def test_missing_model_error_and_broken_profile_are_no_operator(self):
        self.service.embedder.available = False
        self.assert_guest(self.turn(), "unavailable")
        self.service.embedder.available = True
        self.service.embedder.error = RuntimeError("onnx /secret/path broke")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assert_guest(self.turn(), "unavailable")
        self.assertNotIn("/secret/path", out.getvalue())     # no free error text in the log
        self.service.embedder.error = None
        broken = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[],
                      total_facts=1, voiceprints=[dict(name="Ivan", print="kaputt!")])
        self.assert_guest(self.turn(copy=broken), "unavailable")

    def test_enrolled_without_a_usable_print_is_no_operator(self):
        copy = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[],
                    total_facts=1, enrolled=1)
        self.assert_guest(self.turn(copy=copy), "unavailable")

    def test_two_close_profiles_are_ambiguous(self):
        near = speaker.normalize([1.0, 0.02, 0.5, 0.25] * 128)
        copy = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[],
                    total_facts=1, voiceprints=[dict(name="Ivan", print=speaker.encode(self.voice)),
                                                dict(name="Anna", print=speaker.encode(near))])
        self.assert_guest(self.turn(copy=copy), "ambiguous")

    def test_without_enrollment_nothing_changes(self):
        copy = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[], total_facts=1)
        events = self.turn(body=b"\0\0" * 64000, copy=copy)
        self.assertFalse(any(e["event"] == "speaker" for e in events))
        self.assertEqual(self.pipeline.memory["facts"], ["Name des Bedieners: Ivan"])

    def test_turn_voice_is_compared_without_the_silence_around_it(self):
        quiet, loud = b"\0\0" * 32000, (b"\x40\x1f" * 32000)   # 2 s silence, 2 s at 8000
        copy = dict(facts=[], directives=[], history=[], total_facts=0,
                    voiceprints=[dict(name="Ivan", print=speaker.encode(self.voice))])
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/turn", body=quiet + loud + quiet, headers={
            "Authorization": f"Bearer {self.token}", "X-Servitor-Status": json.dumps({"memory": "on"}),
            "X-Servitor-Memory": memory.encode_header(copy)})
        conn.getresponse().read()
        conn.close()
        self.assertAlmostEqual(self.service.embedder.seen / 32000, 2.0 + 2 * 0.15, delta=0.05)

    def voiceprint(self, body):
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/voiceprint", body=body,
                     headers={"Authorization": f"Bearer {self.token}"})
        response = conn.getresponse()
        reply = json.loads(response.read())
        conn.close()
        return response.status, reply

    def test_turn_and_enrollment_take_the_same_path(self):
        # The same PCM through a turn's voice check and through /v1/voiceprint:
        # the same speech measure, the same cut, the same bytes for the model.
        take = b"\0\0" * 12000 + self.SPEECH + b"\0\0" * 20000
        self.turn(take)
        status, reply = self.voiceprint(take)
        self.assertEqual(status, 200)
        turn_input, enroll_input = self.service.embedder.inputs[-2:]
        self.assertEqual(turn_input, enroll_input)
        self.assertEqual(reply["quality"]["speech_seconds"], speaker.speech_seconds(take))
        self.assertEqual(reply["quality"], voice_quality.analyze(take))

    def test_rejected_voice_leaves_no_files_on_the_server(self):
        status, reply = self.voiceprint(b"\0\0" * 64000)
        self.assertEqual(status, 400)
        self.assertEqual(reply["quality"]["speech_seconds"], 0.0)
        self.turn(b"\0\0" * 64000)
        self.turn()
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_voiceprint_endpoint_and_transcribe_mode(self):
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/voiceprint", body=self.SPEECH,
                     headers={"Authorization": f"Bearer {self.token}"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        self.assertGreater(speaker.cosine(speaker.decode(reply["print"]), self.voice), 0.999)
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/voiceprint", body=b"\0" * 64000,
                     headers={"Authorization": f"Bearer {self.token}"})
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 400)     # silence gives no voiceprint
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/turn?mode=transcribe", body=b"\1" * 64000,
                     headers={"Authorization": f"Bearer {self.token}"})
        events = [json.loads(l) for l in conn.getresponse().read().decode().splitlines() if l]
        conn.close()
        self.assertEqual([e["event"] for e in events], ["stage", "transcript", "done"])


if __name__ == "__main__":
    unittest.main()
