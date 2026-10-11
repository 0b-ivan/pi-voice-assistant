import contextlib
import hashlib
import importlib.util
import io
import json
import math
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import speaker  # noqa: E402
import voice_profile as vp  # noqa: E402

DIM = 64
MODEL = dict(vp.CAMPPLUS)


def unit(values):
    return vp.normalize(values)


class Voices:
    """Synthetic speakers: a base direction, one offset per recording
    condition, Gaussian noise of norm ~sigma per take."""

    def __init__(self, seed=1):
        self.random = random.Random(seed)

    def direction(self):
        return unit([self.random.gauss(0, 1) for _ in range(DIM)])

    def person(self, condition_shift=0.25):
        base = self.direction()
        shifts = {c: [condition_shift * v for v in self.direction()] for c in vp.CONDITIONS}
        return dict(base=base, shifts=shifts)

    def take(self, person, condition, sigma=0.3):
        noise = [self.random.gauss(0, sigma / math.sqrt(DIM)) for _ in range(DIM)]
        shift = person["shifts"][condition]
        return unit([b + s + n for b, s, n in zip(person["base"], shift, noise)])

    def anchors(self, person, per_condition=6, sigma=0.3):
        return [dict(condition=c, vector=self.take(person, c, sigma))
                for c in vp.CONDITIONS for _ in range(per_condition)]

    def impostors(self, people=5, each=4):
        found = []
        for index in range(people):
            other = self.person()
            found += [dict(source=f"p{index}", vector=self.take(other, c))
                      for c in vp.CONDITIONS[:each] for _ in range(1)] + \
                     [dict(source=f"p{index}", vector=self.take(other, "near"))
                      for _ in range(each - min(each, 3))]
        return found


def calibrated_profile(voices, person, name="Ivan"):
    anchors = voices.anchors(person)
    checks = voices.anchors(person, per_condition=2)
    result = vp.calibrate(anchors, checks, voices.impostors())
    profile = vp.new_profile(name, anchors, checks, model=MODEL)
    return vp.apply_calibration(profile, result), result


class VectorTests(unittest.TestCase):
    def test_codec_round_trip_and_rejections(self):
        vector = unit([0.3, -0.2, 0.9] * 22)
        back = vp.decode(vp.encode(vector))
        self.assertGreater(vp.cosine(vector, back), 0.999999)
        for bad in ([0.0] * 32, [float("nan")] + [1.0] * 31, [], [float("inf")] * 32):
            with self.assertRaises(vp.ProfileError):
                vp.encode(bad)
        zero = vp.base64.b64encode(bytes(4 * 32)).decode()
        with self.assertRaises(vp.ProfileError):
            vp.decode(zero)                           # zero norm
        with self.assertRaises(vp.ProfileError):
            vp.decode(vp.encode(vector), dim=128)     # wrong dimension
        with self.assertRaises(vp.ProfileError):
            vp.decode("nope!")
        with self.assertRaises(vp.ProfileError):
            vp.decode(vp.base64.b64encode(bytes(4 * 2000)).decode())   # too large


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.voices = Voices()
        self.person = self.voices.person()

    def test_separated_voices_are_calibrated(self):
        profile, result = calibrated_profile(self.voices, self.person)
        self.assertEqual((result["status"], result["reason"]), ("calibrated", None))
        self.assertAlmostEqual(result["tau"], result["impostor_max"] + vp.EPSILON, places=3)
        self.assertTrue(all(g >= result["tau"] for g in result["genuine"]))
        self.assertEqual(len(result["genuine"]), 18)
        self.assertEqual(profile["status"], "calibrated")
        self.assertEqual(set(profile["centers"]), set(vp.CONDITIONS))
        vp.validate(json.loads(json.dumps(profile)))

    def test_loo_never_scores_a_clip_against_itself(self):
        anchors = self.voices.anchors(self.person)
        # One quiet take points elsewhere: with itself inside the quiet center
        # it would score high; left out, it must not.
        odd = self.voices.direction()
        anchors[-1] = dict(condition="quiet", vector=odd)
        loo = vp.loo_scores(anchors)
        with_itself = vp.score(odd, vp.centers_of(anchors))
        self.assertLess(loo[-1]["score"], with_itself - 0.1)
        rest = anchors[:-1]
        self.assertAlmostEqual(loo[-1]["score"], vp.score(odd, vp.centers_of(rest)), places=6)
        # The only clip of a condition leaves that condition out of its fold.
        single = [dict(condition="near", vector=self.voices.take(self.person, "near")),
                  dict(condition="far", vector=self.voices.take(self.person, "far"))]
        folds = vp.loo_scores(single)
        self.assertAlmostEqual(folds[0]["score"],
                               vp.cosine(vp.stored(single[0]["vector"]),
                                         vp.centers_of(single[1:])["far"]), places=6)

    def test_overlapping_and_swapped_distributions_stay_provisional(self):
        anchors = self.voices.anchors(self.person, sigma=1.4)       # scattered own voice
        checks = self.voices.anchors(self.person, per_condition=2)
        near = [dict(source=f"p{i % 5}", vector=self.voices.take(self.person, "near", 0.5))
                for i in range(20)]                                # strangers close to it
        result = vp.calibrate(anchors, checks, near)
        self.assertEqual(result["status"], "provisional")
        self.assertIn(result["reason"], ("not_separable", "impostor_too_close"))
        # Swapped: impostors more like the profile than the own voice.
        swapped = vp.calibrate(self.voices.anchors(self.person, sigma=2.0), checks,
                               [dict(source=f"p{i % 5}", vector=self.voices.take(
                                   self.person, "near", 0.05)) for i in range(20)])
        self.assertEqual(swapped["status"], "provisional")

    def test_missing_impostors_give_no_threshold(self):
        anchors = self.voices.anchors(self.person)
        checks = self.voices.anchors(self.person, per_condition=2)
        for impostors in ([], self.voices.impostors()[:19],
                          [dict(i, source="p0") for i in self.voices.impostors()]):
            result = vp.calibrate(anchors, checks, impostors)
            self.assertEqual((result["status"], result["reason"], result["tau"]),
                             ("provisional", "too_few_impostors", None))

    def test_held_out_checks_must_pass_unchanged(self):
        anchors = self.voices.anchors(self.person)
        checks = [dict(condition=c, vector=self.voices.direction())
                  for c in vp.CONDITIONS for _ in range(2)]
        result = vp.calibrate(anchors, checks, self.voices.impostors())
        self.assertEqual((result["status"], result["reason"]), ("provisional", "checks_failed"))
        self.assertTrue(result["loo_ok"])
        result = vp.calibrate(anchors, checks[:5], self.voices.impostors())
        self.assertEqual(result["reason"], "checks_missing")

    def test_calibration_uses_the_stored_vectors(self):
        anchors = self.voices.anchors(self.person)
        result = vp.calibrate(anchors, self.voices.anchors(self.person, per_condition=2),
                              self.voices.impostors())
        stored = [dict(a, vector=vp.stored(a["vector"])) for a in anchors]
        expected = [round(g["score"], 4) for g in vp.loo_scores(stored)]
        self.assertEqual(result["genuine"], expected)


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.voices = Voices(2)
        self.ivan_voice, self.anna_voice = self.voices.person(), self.voices.person()
        self.ivan, _ = calibrated_profile(self.voices, self.ivan_voice, "Ivan")
        self.anna, _ = calibrated_profile(self.voices, self.anna_voice, "Anna")

    def test_matches_only_a_trusted_profile_above_its_own_tau(self):
        take = self.voices.take(self.ivan_voice, "far")
        result = vp.decide(take, [self.ivan, self.anna], MODEL)
        self.assertEqual((result["status"], result["profile_id"]),
                         (vp.MATCHED, self.ivan["profile_id"]))
        self.assertIsNotNone(result["second"])
        result = vp.decide(self.voices.direction(), [self.ivan, self.anna], MODEL)
        self.assertIn(result["status"], (vp.UNKNOWN, vp.AMBIGUOUS))
        self.assertIsNone(result["profile_id"])
        provisional = dict(self.ivan, status="provisional")
        result = vp.decide(take, [provisional], MODEL)
        self.assertEqual((result["status"], result["profile_id"]), (vp.UNVERIFIED, None))

    def test_score_equal_to_tau_counts(self):
        take = self.voices.take(self.ivan_voice, "near")
        exact = vp.score(vp.normalize(take), vp.comparison_centers(self.ivan))
        at = dict(self.ivan, calibration=dict(self.ivan["calibration"], tau=exact))
        self.assertEqual(vp.decide(take, [at], MODEL)["status"], vp.MATCHED)
        above = dict(self.ivan, calibration=dict(self.ivan["calibration"], tau=exact + 1e-9))
        self.assertEqual(vp.decide(take, [above], MODEL)["status"], vp.UNKNOWN)

    def test_no_fallback_to_a_runner_up_with_a_lower_threshold(self):
        take = self.voices.take(self.ivan_voice, "near")
        strict = dict(self.ivan, calibration=dict(self.ivan["calibration"], tau=0.999))
        lax = dict(self.anna, calibration=dict(self.anna["calibration"], tau=-1.0))
        result = vp.decide(take, [strict, lax], MODEL)
        self.assertEqual((result["status"], result["profile_id"]), (vp.UNKNOWN, None))

    def test_close_profiles_are_ambiguous(self):
        twin = dict(self.ivan, profile_id="f" * 32, name="Zwilling")
        take = self.voices.take(self.ivan_voice, "near")
        self.assertEqual(vp.decide(take, [self.ivan, twin], MODEL)["status"], vp.AMBIGUOUS)

    def test_incompatible_model_preprocessing_or_dimension_is_skipped(self):
        take = self.voices.take(self.ivan_voice, "near")
        other_model = dict(name="ecapa", sha256="0" * 64)
        self.assertEqual(vp.decide(take, [self.ivan], other_model)["status"], vp.UNAVAILABLE)
        old_path = dict(self.ivan, preprocessing=0)
        self.assertEqual(vp.decide(take, [old_path], MODEL)["status"], vp.UNAVAILABLE)
        self.assertEqual(vp.decide(take[:32], [self.ivan], MODEL)["status"], vp.UNAVAILABLE)
        self.assertEqual(vp.decide([0.0] * DIM, [self.ivan], MODEL)["status"], vp.UNAVAILABLE)
        self.assertEqual(vp.decide(None, [self.ivan], MODEL)["status"], vp.UNAVAILABLE)

    def test_cross_check_over_several_people(self):
        samples = [dict(profile_id=self.ivan["profile_id"], vector=self.voices.take(self.ivan_voice, c))
                   for c in vp.CONDITIONS for _ in range(3)]
        samples += [dict(profile_id=self.anna["profile_id"], vector=self.voices.take(self.anna_voice, c))
                    for c in vp.CONDITIONS for _ in range(3)]
        counts = vp.cross_check([self.ivan, self.anna], samples, MODEL)
        self.assertEqual((counts["wrong"], counts["stranger_matched"]), (0, 0))
        self.assertEqual(counts["right"] + counts["refused"], len(samples))
        # A "stranger" that sounds like Ivan (replay, close voice) is counted
        # as a stranger match: 20 impostor samples guarantee nothing for new
        # voices, cross_check only makes such cases visible.
        lookalike = [dict(profile_id=None, vector=self.voices.take(self.ivan_voice, "near"))]
        counts = vp.cross_check([self.ivan, self.anna], lookalike, MODEL)
        self.assertEqual(counts["stranger_matched"], 1)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = vp.ProfileStore(Path(self.tmp.name) / "profiles-v2", clock=lambda: 1000)
        self.voices = Voices(3)
        self.person = self.voices.person()

    def profile(self, name="Ivan"):
        return vp.new_profile(name, self.voices.anchors(self.person),
                              self.voices.anchors(self.person, per_condition=2), model=MODEL)

    def test_stable_ids_duplicate_names_and_renaming(self):
        first, second = self.store.save(self.profile()), self.store.save(self.profile())
        self.assertNotEqual(first["profile_id"], second["profile_id"])
        self.assertEqual(len(self.store.by_name("Ivan")), 2)
        renamed = self.store.rename(first["profile_id"], "Ivan B.")
        self.assertEqual((renamed["profile_id"], renamed["revision"]), (first["profile_id"], 1))
        self.assertEqual(self.store.load(first["profile_id"])["name"], "Ivan B.")
        self.assertEqual(len(self.store.all()), 2)

    def test_interrupted_or_damaged_write_keeps_the_last_valid_revision(self):
        saved = self.store.save(self.profile())
        ident = saved["profile_id"]
        self.store.rename(ident, "Neu")
        main = self.store.folder / f"{ident}.json"
        # Power loss between "current -> previous" and "temporary -> current".
        main.replace(self.store.folder / f"{ident}.prev.json")
        (self.store.folder / f"{ident}.tmp").write_text("{half")
        self.assertEqual(self.store.load(ident)["name"], "Neu")
        main.write_text('{"schema_version": 2, "broken": tru')     # damaged current file
        self.assertEqual(self.store.load(ident)["name"], "Neu")
        self.assertEqual([p["profile_id"] for p in self.store.all()], [ident])
        self.assertTrue(self.store.delete(ident))
        self.assertEqual(list(self.store.folder.iterdir()), [])

    def test_limits_and_bad_data_are_refused(self):
        with self.assertRaises(vp.ProfileError):
            vp.new_profile("Ivan", self.voices.anchors(self.person, per_condition=7))
        profile = self.profile()
        for change in (dict(profile_id="x"), dict(dim=4), dict(status="trusted"),
                       dict(codec="int8"), dict(centers={"loud": profile["centers"]["near"]}),
                       dict(extras=[profile["anchors"][0]] * 25),
                       dict(anchors=[dict(condition="near", vector="nope")])):
            with self.subTest(change=sorted(change)), self.assertRaises(vp.ProfileError):
                vp.validate(dict(profile, **change))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vector = unit([1.0, 0.5, -0.2, 0.1] * 128)
        self.v1 = dict(name="Ivan", print=speaker.encode(self.vector), count=11, at=5,
                       passphrase="omnissiah")

    def test_legacy_print_becomes_one_legacy_center(self):
        profile = vp.migrate_legacy(self.v1)
        vp.validate(profile)
        self.assertEqual((profile["status"], profile["centers"], profile["model"]),
                         ("legacy", {}, None))
        center = vp.comparison_centers(profile)
        self.assertEqual(list(center), ["legacy"])
        self.assertGreater(vp.cosine(center["legacy"], self.vector), 0.998)
        self.assertEqual(profile["legacy"], {k: self.v1[k] for k in ("print", "count", "at",
                                                                     "passphrase")})
        self.assertEqual(profile["anchors"], [])           # count is no anchor count
        # Never compared automatically: no known model.
        self.assertEqual(vp.decide(self.vector, [profile], MODEL)["status"], vp.UNAVAILABLE)
        kept = vp.migrate_legacy(dict(self.v1, profile_id="a" * 32))
        self.assertEqual(kept["profile_id"], "a" * 32)
        with self.assertRaises(vp.ProfileError):
            vp.migrate_legacy(dict(self.v1, print="kaputt"))

    def test_slug_collisions_are_reported(self):
        self.assertEqual(vp.slug_collisions(["Ivan B", "ivan-b", "Anna"]),
                         {"ivan-b": ["Ivan B", "ivan-b"]})

    def script(self):
        spec = importlib.util.spec_from_file_location(
            "voice_profile_migrate", ROOT / "scripts" / "voice-profile-migrate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_script_writes_only_into_the_copy_and_keeps_ids(self):
        source = Path(self.tmp.name) / "stick"
        profiles = source / "voice" / "profiles"
        profiles.mkdir(parents=True)
        (profiles / "ivan.json").write_text(json.dumps(self.v1))
        (profiles / "anna.json").write_text(json.dumps(dict(self.v1, name="Anna")))
        (profiles / "kaputt.json").write_text("{nope")
        (source / "voice" / "voiceprint.json").write_text(json.dumps(dict(self.v1, name="Alt")))

        def fingerprint():
            return {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(source.rglob("*")) if p.is_file()}

        before = fingerprint()
        module = self.script()
        target = Path(self.tmp.name) / "copy"
        first = module.migrate(source, target)
        second = module.migrate(source, target)
        self.assertEqual(fingerprint(), before)
        self.assertEqual(first["unreadable"], ["kaputt.json"])
        self.assertEqual(len(first["migrated"]), 3)
        ids = {m["file"]: m["profile_id"] for m in first["migrated"]}
        self.assertEqual(ids, {m["file"]: m["profile_id"] for m in second["migrated"]})
        self.assertEqual([m["revision"] for m in second["migrated"]], [1, 1, 1])
        store = vp.ProfileStore(target / "voice" / "profiles-v2")
        self.assertEqual({p["status"] for p in store.all()}, {"legacy"})
        self.assertNotIn("Ivan", json.dumps(first))          # report without names
        with self.assertRaises(SystemExit):
            module.migrate(source, source / "voice")
        with self.assertRaises(SystemExit):
            module.migrate(source, source)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["x"]), 2)


if __name__ == "__main__":
    unittest.main()
