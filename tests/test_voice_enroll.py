import contextlib
import io
import json
import math
import random
import sys
import tempfile
import threading
import time
import unittest
import wave
from array import array
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import enroll_phrases as ep  # noqa: E402
import memory  # noqa: E402
import voice_enroll as ve  # noqa: E402
import voice_profile as vp  # noqa: E402

MODEL = dict(vp.CAMPPLUS)
DIM = 64


def tone(*parts):
    samples = array("h")
    for seconds, amplitude in parts:
        samples.extend(int(amplitude * math.sin(2 * math.pi * 440 * i / 16000))
                       for i in range(int(seconds * 16000)))
    return samples.tobytes()


class Voice:
    def __init__(self, seed):
        self.random = random.Random(seed)
        self.base = self.direction()
        self.shift = {c: [0.25 * v for v in self.direction()] for c in ep.CONDITIONS}

    def direction(self):
        return vp.normalize([self.random.gauss(0, 1) for _ in range(DIM)])

    def take(self, condition, sigma=0.3):
        noise = [self.random.gauss(0, sigma / math.sqrt(DIM)) for _ in range(DIM)]
        return vp.normalize([b + s + n for b, s, n in zip(self.base, self.shift[condition], noise)])


class Clock:
    """Fake monotonic time: advances on io actions and a little on every read."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        self.now += 0.2
        return self.now


class FakeIO:
    def __init__(self, clock, voice, answers=(), confirm=True):
        self.clock, self.voice = clock, voice
        self.answers = list(answers)
        self.confirm = confirm
        self.said, self.states, self.records = [], [], []
        self.text, self.condition, self.stage = None, None, None
        self.session = None
        self.wrong = set()          # record numbers whose transcript is wrong
        self.speech = None          # seconds of speech per take (None: from the text)
        self.on_record = None
        self.clip_hook = None
        self.model = MODEL

    def say(self, text):
        self.said.append(text)
        self.clock.now += len(text) / 15

    def beep(self):
        self.clock.now += 0.3

    def abort(self):
        pass

    def publish(self, **state):
        self.states.append(state)
        if 'text' in state:
            self.text = state['text']
        if state.get('condition'):
            self.condition = state['condition']
        if 'stage' in state:
            self.stage = state['stage']
            if state['stage'] == 'confirm' and self.confirm and self.session:
                self.session.press('E')

    def record(self, path, seconds):
        self.records.append(Path(path).name)
        if self.on_record:
            self.on_record(len(self.records))
        self.clock.now += seconds
        if 'noise' in Path(path).name:
            pcm = tone((seconds, 30))
        else:
            words = len((self.text or 'eins zwei drei').split())
            spoken = self.speech if self.speech is not None else min(seconds - 1.0,
                                                                     max(2.2, words / 2.4))
            pcm = tone((0.4, 30), (spoken, 8000), (seconds - 0.4 - spoken, 30))
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(16000)
            out.writeframes(pcm)
        return True

    def clip(self, pcm):
        if self.clip_hook:
            self.clip_hook()
        self.clock.now += 0.5
        number = len(self.records)
        heard = 'völlig anderer satz hier' if number in self.wrong else self.text
        return dict(quality={}, transcript=heard, model=self.model, preprocessing=1, dim=DIM,
                    vector=vp.encode(self.voice.take(self.condition or 'near')))

    def transcribe(self, pcm):
        self.clock.now += 0.5
        return self.answers.pop(0) if self.answers else None


class PhraseTests(unittest.TestCase):
    def test_plan_layout_coverage_and_no_duplicates(self):
        for seed in range(25):
            tasks = ep.plan(random.Random(seed))
            self.assertEqual(len(tasks), 24)
            self.assertEqual(len({t["text"] for t in tasks}), 24)
            for condition in ep.CONDITIONS:
                own = [t for t in tasks if t["condition"] == condition]
                self.assertEqual(len(own), 8)
                self.assertEqual(sum(t["role"] == "check" for t in own), 2)
            self.assertEqual(sum(t["role"] == "anchor" for t in tasks), 18)
            self.assertTrue(all(t["pattern"] == "CH" for t in tasks[-2:]))
            self.assertGreaterEqual(ep.coverage(t["text"] for t in tasks), set(ep.INVENTORY))
            anchor_patterns = {t["pattern"] for t in tasks if t["role"] == "anchor"}
            check_patterns = {t["pattern"] for t in tasks if t["role"] == "check"}
            self.assertFalse(anchor_patterns & check_patterns)
            for t in tasks:
                self.assertEqual(ep.unknown_words(t["text"]), [], t["text"])
                words = len(ep.normalize(t["text"]).split())
                low, high = dict(short=(4, 7), mid=(8, 12), long=(13, 20),
                                 challenge=(8, 12))[t["length"]]
                self.assertTrue(low <= words <= high, (t["length"], t["text"]))

    def test_challenges_draw_fresh_words_in_the_middle_and_at_the_end(self):
        tasks = ep.plan(random.Random(4))
        for item in tasks[-2:]:
            self.assertGreaterEqual(len(item["words"]), 3)
            words = ep.normalize(item["text"]).split()
            self.assertEqual(words[-1], ep.normalize(item["words"][-1]))
            self.assertIn(ep.normalize(item["words"][0]), words[2:4])
        drawn = {tuple(ep.challenge(random.Random(seed))[1]) for seed in range(30)}
        self.assertGreater(len(drawn), 25)

    def test_replacement_keeps_the_task_and_changes_the_text(self):
        rng = random.Random(2)
        tasks = ep.plan(rng)
        for item in (tasks[0], tasks[3], tasks[-1]):
            fresh = ep.replacement(item, rng, {t["text"] for t in tasks})
            self.assertNotEqual(fresh["text"], item["text"])
            for key in ("condition", "role", "length", "pattern", "index"):
                self.assertEqual(fresh[key], item[key])

    def test_baseline_pairs_draw_their_variables(self):
        pairs = ep.baseline(random.Random(1))
        self.assertEqual(len(pairs), 12)
        self.assertEqual(pairs[0][0], "Kern bereit. Leitung offen.")
        for number in (2, 8, 10, 11):
            prompt, answer, words = pairs[number - 1]
            self.assertTrue(words and prompt and answer)
            self.assertTrue(ep.in_order(words, answer))
        self.assertNotEqual([p[1] for p in ep.baseline(random.Random(1))],
                            [p[1] for p in ep.baseline(random.Random(2))])

    def test_normalization_and_wer(self):
        self.assertEqual(ep.normalize("Prüfe: 7 Birken!"), "prüfe sieben birken")
        self.assertEqual(ep.wer("a b c d", "a b c d"), 0.0)
        self.assertEqual(ep.wer("a b c d", "a x c d"), 0.25)           # substitution
        self.assertEqual(ep.wer("a b c d", "a c d"), 0.25)             # deletion
        self.assertEqual(ep.wer("a b c d", "a b q c d"), 0.25)         # insertion
        self.assertEqual(ep.wer("", ""), 0.0)

    def test_content_check(self):
        normal = dict(text="Die müde Katze sucht den roten Schlüssel im stillen Schrank.")
        self.assertTrue(ep.check(normal, "die müde katze sucht den roten schlüssel im stillen schrank")["ok"])
        self.assertTrue(ep.check(normal, "die müde katze sucht den roten schlüssel im schrank")["ok"])
        self.assertFalse(ep.check(normal, "die katze sucht schlüssel im schrank")["ok"])
        text, words = "Prüfe bitte Kessel Eisen neun und melde danach Anker.", \
            ["Kessel", "Eisen", "neun", "Anker"]
        item = dict(text=text, words=words)
        self.assertTrue(ep.check(item, "prüfe bitte kessel eisen 9 und melde danach anker")["ok"])
        # One wrong drawn word: WER 0.11 would pass, the word check does not.
        wrong = ep.check(item, "prüfe bitte kessel eisen acht und melde danach anker")
        self.assertEqual((wrong["ok"], wrong["words_ok"]), (False, False))
        self.assertLessEqual(wrong["wer"], ep.CHALLENGE_WER_MAX)
        swapped = ep.check(item, "prüfe bitte eisen kessel neun und melde danach anker")
        self.assertFalse(swapped["ok"])
        short = dict(text="Leitung drei. Lampe blau.", words=["drei", "blau"])
        self.assertTrue(ep.check(short, "leitung drei lampe blau")["ok"])
        self.assertFalse(ep.check(short, "leitung drei lampe blau ja")["ok"])   # exact
        self.assertFalse(ep.check(item, None)["ok"])

    def test_production_randomness_is_secrets(self):
        self.assertIsInstance(ep.system_random(), ep.secrets.SystemRandom)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "proximus"
        root.mkdir()
        (Path(self.tmp.name) / "dev").touch()
        self.core = memory.MemoryCore(root, Path(self.tmp.name) / "dev", clock=lambda: 1000)
        self.clock = Clock()
        self.voice = Voice(5)

    def session(self, io_=None, **kwargs):
        io_ = io_ or FakeIO(self.clock, self.voice, kwargs.pop("answers", ()))
        session = ve.VoiceSession(self.core, io_, clock=self.clock, rng=random.Random(3),
                                  wall=lambda: 1_700_000_000, **kwargs)
        io_.session = session
        return session, io_

    def run_quiet(self, session):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = session.run()
        logged = [json.loads(l) for l in out.getvalue().splitlines() if l.startswith("{")]
        return result, logged

    def store(self):
        return vp.ProfileStore(self.core.voice_dir / "profiles-v2")

    def drafts_empty(self):
        drafts = self.core.voice_dir / "drafts"
        return not drafts.exists() or not any(drafts.rglob("*"))

    def write_reference(self):
        items = []
        for person in range(5):
            other = Voice(100 + person)
            items += [dict(source=f"ref{person}", model_sha256=MODEL["sha256"],
                           vector=vp.encode(other.take(c))) for c in ep.CONDITIONS]
            items.append(dict(source=f"ref{person}", model_sha256=MODEL["sha256"],
                              vector=vp.encode(other.take("near"))))
        self.core.voice_dir.mkdir(parents=True, exist_ok=True)
        (self.core.voice_dir / ve.REFERENCE_FILE).write_text(json.dumps(items))

    def test_full_session_stores_a_provisional_profile_and_facts_after_e(self):
        session, io_ = self.session(answers=["nenn mich ivan", "in würzburg"])
        result, logged = self.run_quiet(session)
        self.assertTrue(result["stored"], result)
        self.assertLessEqual(result["elapsed"], ve.DEADLINE)
        self.assertGreaterEqual(result["speech"], ve.MIN_SPEECH)
        profile = self.store().load(result["profile_id"])
        self.assertEqual((len(profile["anchors"]), len(profile["checks"])), (18, 6))
        self.assertEqual(set(profile["centers"]), set(ep.CONDITIONS))
        self.assertEqual((profile["status"], result["reason"]), ("provisional", "too_few_impostors"))
        self.assertEqual(profile["name"], "Ivan")
        v1 = self.core.profile("Ivan")
        self.assertEqual((v1["profile_id"], v1["count"]), (profile["profile_id"], 18))
        self.assertIn("Name des Bedieners: Ivan", self.core.context()["facts"])
        kept = list((self.core.voice_dir / "v2-audio" / profile["profile_id"]).rglob("*.wav"))
        self.assertEqual(len(kept), 24)
        self.assertTrue(self.drafts_empty())
        self.assertIn(ve.EXTRA_CONFIRMATION, " ".join(io_.said))
        # Logs: numbers and codes, no sentence, no transcript, no name.
        text = json.dumps(logged).lower()
        for secret in ("ivan", "würzburg", "prüfe bitte", "katze"):
            self.assertNotIn(secret, text)
        clips = [e for e in logged if e.get("event") == "enroll_clip"]
        self.assertEqual(sum(e["result"] == "accepted" for e in clips), 24)
        # The display got the progress and every sentence once.
        steps = [s for s in io_.states if s.get("stage") == "voice" and s.get("text")]
        self.assertEqual(max(s["step"] for s in steps), 24)

    def test_reference_voices_allow_a_calibrated_profile(self):
        self.write_reference()
        session, _ = self.session(facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual((result["status"], result["reason"]), ("calibrated", None))
        profile = self.store().load(result["profile_id"])
        self.assertIsNotNone(vp.voice_core([profile]))

    def test_failed_take_is_repeated_with_a_new_sentence(self):
        io_ = FakeIO(self.clock, self.voice)
        io_.wrong = {3}            # first voice take after the noise record
        session, _ = self.session(io_, facts=False)
        result, logged = self.run_quiet(session)
        self.assertTrue(result["stored"])
        clips = [e for e in logged if e.get("event") == "enroll_clip"]
        self.assertEqual(clips[1]["result"], "content")
        texts = {s["text"] for s in io_.states if s.get("stage") == "voice" and s.get("text")}
        self.assertEqual(len(texts), 25)            # 24 sentences plus a new one for the repeat
        self.assertIn(ve.CONTENT_HINT, io_.said)

    def test_too_many_failures_stop_without_touching_the_old_profile(self):
        old = vp.new_profile("Ivan", [dict(condition="near", vector=self.voice.take("near"))],
                             model=MODEL)
        self.store().save(old)
        io_ = FakeIO(self.clock, self.voice)
        io_.wrong = {2, 3, 4, 5}       # the same sentence twice, then two more
        session, _ = self.session(io_, facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="unsuitable"))
        self.assertEqual(io_.said[-1], ve.UNSUITABLE)
        self.assertEqual([p["profile_id"] for p in self.store().all()], [old["profile_id"]])
        self.assertTrue(self.drafts_empty())
        io_ = FakeIO(self.clock, self.voice)
        io_.wrong = {3, 5, 8, 11}      # four sentences once each: three repeats allowed
        session, _ = self.session(io_, facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual(result["reason"], "unsuitable")

    def test_not_confirmed_stores_nothing(self):
        io_ = FakeIO(self.clock, self.voice, ["nenn mich ivan"], confirm=False)
        session, _ = self.session(io_)
        result, _ = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="not_confirmed"))
        self.assertEqual(self.store().all(), [])
        self.assertEqual(self.core.context()["facts"], [])        # facts were a draft
        self.assertEqual(self.core.people(), [])
        self.assertTrue(self.drafts_empty())

    def test_deadline_stops_cleanly(self):
        io_ = FakeIO(self.clock, self.voice)
        io_.on_record = lambda n: setattr(self.clock, "now", self.clock.now + (300 if n == 10 else 0))
        session, _ = self.session(io_, facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="timeout"))
        self.assertEqual(io_.said[-1], ve.TIMEOUT)
        self.assertEqual(self.store().all(), [])
        self.assertTrue(self.drafts_empty())

    def test_cancel_in_every_phase(self):
        for phase in (1, 2, 12, 25, 26):     # noise, first take, middle, after the voice part
            with self.subTest(phase=phase):
                io_ = FakeIO(self.clock, self.voice, ["ivan"] * 7)
                session, _ = self.session(io_)
                io_.on_record = lambda n, s=session, p=phase: s.cancel() if n == p else None
                result, _ = self.run_quiet(session)
                self.assertEqual(result, dict(cancelled=True))
                self.assertEqual(io_.said[-1], ve.CANCELLED)
                self.assertEqual(self.store().all(), [])
                self.assertEqual(self.core.context()["facts"], [])
                self.assertTrue(self.drafts_empty())

    def test_cancel_during_a_blocking_server_call_is_immediate(self):
        io_ = FakeIO(self.clock, self.voice)
        release = threading.Event()
        io_.clip_hook = lambda: release.wait(10)          # a hanging server
        session, _ = self.session(io_, facts=False)
        with contextlib.redirect_stdout(io.StringIO()):
            session.start()
            deadline = time.monotonic() + 5
            while io_.stage != "voice" or len(io_.records) < 2:
                time.sleep(0.01)
                self.assertLess(time.monotonic(), deadline)
            time.sleep(0.1)
            started = time.monotonic()
            session.cancel()
            session.thread.join(2)
            stopped = time.monotonic() - started
        release.set()
        self.assertFalse(session.running)
        self.assertLess(stopped, 0.3)
        self.assertEqual(session.result, dict(cancelled=True))
        self.assertEqual(io_.states[-1], dict(stage=None))

    def test_too_little_speech_replaces_short_takes_or_stops(self):
        io_ = FakeIO(self.clock, self.voice)
        io_.speech = 2.2                  # every take just long enough, too little in all
        session, _ = self.session(io_, facts=False)
        result, logged = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="too_little_speech"))
        self.assertEqual(len([r for r in io_.records if "anchor" in r]), 18 + ve.EXTRA_ATTEMPTS)

    def test_a_model_change_mid_session_stops(self):
        io_ = FakeIO(self.clock, self.voice)
        io_.on_record = lambda n: setattr(io_, "model", dict(name="x", sha256="0" * 64)) \
            if n == 6 else None
        session, _ = self.session(io_, facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="model_changed"))

    def test_add_keeps_id_and_name_of_the_target(self):
        session, _ = self.session(facts=False)
        first, _ = self.run_quiet(session)
        renamed = self.store().rename(first["profile_id"], "Ivan B")
        session, _ = self.session(mode="add", target=dict(name="Ivan B",
                                                          profile_id=renamed["profile_id"]))
        second, _ = self.run_quiet(session)
        self.assertEqual(second["profile_id"], first["profile_id"])
        profile = self.store().load(first["profile_id"])
        self.assertEqual((profile["name"], profile["revision"]), ("Ivan B", 2))
        self.assertEqual(len(self.store().all()), 1)

    def test_voice_only_session_creates_no_facts_and_baseline_stays_out(self):
        session, io_ = self.session(facts=False, baseline=True)
        result, logged = self.run_quiet(session)
        self.assertTrue(result["stored"])
        self.assertEqual(self.core.context()["facts"], [])
        baseline = [e for e in logged if e.get("event") == "enroll_clip"
                    and e.get("kind") == "baseline"]
        self.assertEqual(len(baseline), 12)
        profile = self.store().load(result["profile_id"])
        self.assertEqual(len(profile["anchors"]), 18)
        self.assertLessEqual(result["elapsed"], ve.DEADLINE)

    def test_facts_only_session(self):
        session, io_ = self.session(mode="facts", answers=["nenn mich ivan", "berlin"])
        result, _ = self.run_quiet(session)
        self.assertEqual((result["stored"], result["profile_id"], result["facts"]), (True, None, 2))
        self.assertEqual(self.store().all(), [])
        self.assertFalse(any(s.get("stage") == "voice" for s in io_.states))

    def test_server_gone_stops(self):
        io_ = FakeIO(self.clock, self.voice)
        io_.clip = lambda pcm: None
        session, _ = self.session(io_, facts=False)
        result, _ = self.run_quiet(session)
        self.assertEqual(result, dict(stored=False, reason="server"))


if __name__ == "__main__":
    unittest.main()
