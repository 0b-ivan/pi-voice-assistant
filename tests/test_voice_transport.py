import http.client
import json
import math
import random
import sys
import tempfile
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_remote_turn import LiveServerCase, ss, TOKEN  # noqa: E402

import memory  # noqa: E402
import protocol  # noqa: E402
import voice_profile as vp  # noqa: E402

MODEL = dict(vp.CAMPPLUS)
DIM = 64


def tone(*parts):
    samples = array("h")
    for seconds, amplitude in parts:
        samples.extend(int(amplitude * math.sin(2 * math.pi * 440 * i / 16000))
                       for i in range(int(seconds * 16000)))
    return samples.tobytes()


SPEECH = tone((0.2, 0), (1.7, 8000))


def unit(seed):
    generator = random.Random(seed)
    return vp.normalize([generator.gauss(0, 1) for _ in range(DIM)])


IVAN = unit(1)


def calibrated(name="Ivan", vector=IVAN, tau=0.8):
    anchors = [dict(condition=c, vector=vector) for c in ("near", "far", "quiet")]
    profile = vp.new_profile(name, anchors, model=MODEL)
    return dict(profile, status="calibrated", calibration=dict(tau=tau))


class Embedder:
    available = True

    def __init__(self, vector):
        self.vector = vector

    def embed(self, pcm):
        return self.vector if len(pcm) >= 32000 else None

    def model_info(self):
        return MODEL


class TransportTests(LiveServerCase):
    def setUp(self):
        super().setUp()
        self.service.embedder = Embedder(IVAN)
        self.context = dict(facts=["Bediener heißt Ivan"], directives=[], total_facts=1,
                            history=[], enrolled=1)
        self.session = protocol.ClientSession()

    def voice_turn(self, core, agenda=None):
        header = memory.encode_header(self.session.memory_payload(self.context))
        guest = memory.encode_header(dict(memory.guest_view(self.context, "unverified"),
                                          enrolled=1))
        job = self.turn(pcm=SPEECH, status=dict(memory="on"), memory=header, session=self.session,
                        voice=core, memory_guest=guest, agenda=agenda)
        self.assertIsNone(job.error)
        return job, job.drain()

    def test_core_travels_once_and_a_calibrated_voice_is_recognized(self):
        core = vp.voice_core([calibrated()])
        puts = []
        original = ss.Handler.do_PUT

        def counting(handler):
            puts.append(handler.path)
            return original(handler)

        with patch.object(ss.Handler, "do_PUT", counting):
            for _ in range(2):
                job, events = self.voice_turn(core)
                heard = next(e for e in events if e["event"] == "speaker")
                self.assertEqual((heard["status"], heard["known"], heard["profile"]),
                                 ("matched", True, 2))
                self.assertEqual(self.pipeline.memory["speaker"], "Ivan")
                self.assertEqual(self.pipeline.memory["facts"], ["Bediener heißt Ivan"])
        self.assertEqual(len(puts), 1)
        self.assertEqual(self.session.voice, protocol.digest(core))

    def test_other_voice_short_speech_and_provisional_profiles_are_guests(self):
        core = vp.voice_core([calibrated()])
        self.service.embedder.vector = unit(9)
        _, events = self.voice_turn(core)
        heard = next(e for e in events if e["event"] == "speaker")
        self.assertEqual((heard["status"], heard["known"]), ("unknown", False))
        self.assertEqual(self.pipeline.memory["facts"], [])
        self.service.embedder.vector = IVAN
        job = self.turn(pcm=tone((0.2, 0), (1.0, 8000), (0.7, 0)), status=dict(memory="on"),
                        memory=memory.encode_header(self.session.memory_payload(self.context)),
                        session=self.session, voice=core)
        heard = next(e for e in job.drain() if e["event"] == "speaker")
        self.assertEqual(heard["status"], "insufficient")              # < 1.5 s of speech
        self.assertIsNone(vp.voice_core([dict(calibrated(), status="provisional")]))

    def test_server_that_lost_the_core_never_falls_back(self):
        core = vp.voice_core([calibrated()])
        self.voice_turn(core)
        self.service.sessions = protocol.Sessions()                  # server restarted
        agenda = "W10="                                              # base64 "[]"
        seen = {}
        original = ss.Service.run_turn

        def spy(service, *args, **kwargs):
            seen.update(kwargs)
            return original(service, *args, **kwargs)

        with patch.object(ss.Service, "run_turn", spy):
            job, events = self.voice_turn(core, agenda=agenda)
        # The stale session reached a restarted server: no memory, no calendar.
        self.assertFalse(isinstance(self.pipeline.memory, dict)
                         and self.pipeline.memory.get("facts"))
        self.assertIsNone(seen.get("appointments"))
        self.assertIsNone(self.session.id)
        job, events = self.voice_turn(core)                           # hello, PUT, recognized
        self.assertEqual(self.pipeline.memory["speaker"], "Ivan")

    def test_unknown_digest_gives_the_guest_view_and_a_resend(self):
        core = vp.voice_core([calibrated()])
        self.voice_turn(core)
        self.session.voice = protocol.digest(core)
        self.service.sessions.set_voice(self.session.id, "other", [])   # server holds another
        _, events = self.voice_turn(core)
        self.assertEqual(self.pipeline.memory["facts"], [])
        self.assertIsNone(self.session.voice)                          # resent next turn
        self.voice_turn(core)
        self.assertEqual(self.pipeline.memory["speaker"], "Ivan")

    def test_old_server_gets_nothing_personal(self):
        core = vp.voice_core([calibrated()])
        with patch.object(ss, "FEATURES", ()):
            self.voice_turn(core, agenda="W10=")
        self.assertFalse(self.session.voice_supported)
        self.assertEqual(self.pipeline.memory["facts"], [])
        heard_voice = [p for p in [self.session.voice] if p]
        self.assertEqual(heard_voice, [])

    def test_v1_stale_session_after_a_restart_gets_no_calendar(self):
        self.voice_turn(None)                                         # session with core
        self.voice_turn(None)
        self.service.sessions = protocol.Sessions()                  # server restarted
        seen = {}
        original = ss.Service.run_turn

        def spy(service, *args, **kwargs):
            seen.update(kwargs)
            return original(service, *args, **kwargs)

        with patch.object(ss.Service, "run_turn", spy):
            self.voice_turn(None, agenda="W10=")
        self.assertIs(seen["memory_copy"], ss.NO_MEMORY)
        self.assertIsNone(seen["appointments"])

    def test_without_v2_profiles_nothing_changes(self):
        job, events = self.voice_turn(None)
        self.assertEqual(self.session.voice, None)
        self.assertFalse(any(e["event"] == "speaker" and e.get("profile") == 2 for e in events))

    def request(self, method, path, body=b"", token=TOKEN):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        headers = {"Content-Length": str(len(body))}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, json.loads(data) if data else None

    def test_core_endpoint_checks_everything(self):
        ident = self.service.sessions.open("pi")
        core = json.dumps(vp.voice_core([calibrated()])).encode()
        self.assertEqual(self.request("PUT", f"/v2/session/{ident}/core", core, token="")[0], 401)
        self.assertEqual(self.request("PUT", "/v2/session/nope/core", core)[0], 404)
        status, reply = self.request("PUT", f"/v2/session/{ident}/core", core)
        self.assertEqual((status, reply["profiles"]), (200, 1))
        bad = dict(profiles=[dict(calibrated(), status="provisional")])
        bad["profiles"][0].pop("anchors"), bad["profiles"][0].pop("checks"), \
            bad["profiles"][0].pop("extras"), bad["profiles"][0].pop("at")
        self.assertEqual(self.request("PUT", f"/v2/session/{ident}/core",
                                      json.dumps(bad).encode())[0], 400)
        many = dict(profiles=vp.voice_core([calibrated()])["profiles"] * 6)
        self.assertEqual(self.request("PUT", f"/v2/session/{ident}/core",
                                      json.dumps(many).encode())[0], 400)
        nan = vp.voice_core([calibrated()])
        nan["profiles"][0]["centers"]["near"] = vp.base64.b64encode(
            vp.struct.pack(f"<{DIM}f", *([float("nan")] * DIM))).decode()
        self.assertEqual(self.request("PUT", f"/v2/session/{ident}/core",
                                      json.dumps(nan).encode())[0], 400)
        with patch.object(vp, "CORE_BYTES_MAX", 100):
            self.assertEqual(self.request("PUT", f"/v2/session/{ident}/core", core)[0], 413)

    def test_enroll_clip_endpoint(self):
        self.pipeline.transcript = "die müde katze schläft heute"
        status, reply = self.request("POST", "/v2/enroll/clip", SPEECH)
        self.assertEqual(status, 200)
        self.assertEqual(reply["transcript"], "die müde katze schläft heute")
        self.assertGreater(vp.cosine(vp.decode(reply["vector"]), IVAN), 0.9999)
        self.assertEqual((reply["model"], reply["dim"]), (MODEL, DIM))
        self.assertAlmostEqual(reply["quality"]["speech_seconds"], 1.7, delta=0.05)
        status, reply = self.request("POST", "/v2/enroll/clip", tone((2.0, 0)))
        self.assertEqual((status, reply["vector"]), (200, None))
        self.assertEqual(self.request("POST", "/v2/enroll/clip", bytes(16000 * 2 * 13))[0], 413)
        self.assertEqual(self.request("POST", "/v2/enroll/clip", SPEECH, token="")[0], 401)
        self.assertEqual(list(p for p in Path(self.tmp.name).iterdir() if p.is_file()), [])


class ControllerCoreTests(unittest.TestCase):
    def test_voice_core_comes_from_the_stick(self):
        from unittest.mock import Mock
        import ptt
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proximus"
            root.mkdir()
            device = Path(tmp) / "dev"
            controller = Mock()
            controller.memory = memory.MemoryCore(root, device)
            core = ptt.VoiceController.voice_core
            profiles = ptt.VoiceController.voice_profiles
            controller.voice_profiles = lambda: profiles(controller)
            self.assertIsNone(core(controller))                       # stick absent
            device.touch()
            store = vp.ProfileStore(root / "voice" / "profiles-v2")
            store.save(dict(calibrated(), anchors=[], checks=[]))
            store.save(vp.new_profile("Anna", [dict(condition="near", vector=unit(3))],
                                      model=MODEL))                  # provisional
            result = core(controller)
            self.assertEqual([p["name"] for p in result["profiles"]], ["Ivan"])
            self.assertNotIn("anchors", result["profiles"][0])


if __name__ == "__main__":
    unittest.main()
