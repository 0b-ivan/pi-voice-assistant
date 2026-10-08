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


class FakeIO:
    def __init__(self, answers, cancel_after=None):
        self.answers, self.said, self.recorded = list(answers), [], []
        self.cancel_after, self.session = cancel_after, None
        self.states = []

    def say(self, text):
        self.said.append(text)

    def beep(self):
        pass

    def record(self, path, seconds):
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(bytes(16000 * 2 * seconds))
        self.recorded.append(Path(path))
        if self.cancel_after is not None and len(self.recorded) >= self.cancel_after:
            self.session.cancel()

    def transcribe(self, pcm):
        return self.answers.pop(0) if self.answers else None

    def voiceprint(self, pcm):
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
        self.assertEqual(enroll.command("starte das stimmtraining"), "enroll")
        self.assertIsNone(enroll.command("wie spät ist es"))
        self.assertEqual(enroll.name_from("du kannst mich ivan nennen"), "Ivan Nennen")
        self.assertEqual(enroll.name_from("ich heiße ivan"), "Ivan")


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

    def test_guest_gets_no_personal_memory(self):
        copy = dict(facts=["Name des Bedieners: Ivan"], directives=["Städte heißen Makropolen"],
                    history=[dict(q="q", a="a")], total_facts=1)
        guest = memory.guest_view(copy)
        prompt = memory.prompt_section(guest)
        self.assertNotIn("Ivan", prompt)
        self.assertIn("Makropolen", prompt)
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

            def embed(inner, pcm):
                return inner.vector if len(pcm) >= 32000 else None

        self.service.embedder = Embedder()
        self.server = ss.Server(("127.0.0.1", 0), ss.Handler)
        self.server.service = self.service
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def turn(self):
        copy = dict(facts=["Name des Bedieners: Ivan"], directives=[], history=[],
                    total_facts=1, voiceprints=[dict(name="Ivan", print=speaker.encode(self.voice))])
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/turn", body=b"\1" * 64000, headers={
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

    def test_voiceprint_endpoint_and_transcribe_mode(self):
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/voiceprint", body=b"\0" * 64000,
                     headers={"Authorization": f"Bearer {self.token}"})
        reply = json.loads(conn.getresponse().read())
        conn.close()
        self.assertGreater(speaker.cosine(speaker.decode(reply["print"]), self.voice), 0.999)
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        conn.request("POST", "/v1/turn?mode=transcribe", body=b"\1" * 64000,
                     headers={"Authorization": f"Bearer {self.token}"})
        events = [json.loads(l) for l in conn.getresponse().read().decode().splitlines() if l]
        conn.close()
        self.assertEqual([e["event"] for e in events], ["stage", "transcript", "done"])


if __name__ == "__main__":
    unittest.main()
