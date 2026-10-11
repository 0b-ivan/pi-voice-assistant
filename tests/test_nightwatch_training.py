import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from nightwatch_training import dataset_plan, assess_candidate
from nightwatch_client import samples
from memory import MemoryCore


def row(number, session='20261011-010000'):
    return dict(file=f'{session}-{number:02d}.wav', frames=16000,
                sha256=hashlib.sha256(str(number).encode()).hexdigest())


class PlanTests(unittest.TestCase):
    def test_duplicates_never_cross_splits_or_make_a_dataset_ready(self):
        records = [row(i) for i in range(20)]
        duplicate = dict(records[0], file='20261012-010000-01.wav')
        plan = dataset_plan(records + [duplicate] * 10)
        self.assertEqual(plan['valid'], 20)
        self.assertEqual(plan['duplicates'], 10)
        self.assertEqual(plan['sessions'], 1)
        self.assertFalse(plan['positive_split_ready'])
        self.assertEqual(len({r['split'] for r in plan['manifest']}), 1)

    def test_new_session_never_moves_old_recordings_between_splits(self):
        records = [row(i) for i in range(20)]
        old = dataset_plan(records)
        new = dataset_plan(records + [row(30, '20261012-010000')])
        by_id = {r['sha256']: r['split'] for r in new['manifest']}
        self.assertTrue(all(by_id[r['sha256']] == r['split'] for r in old['manifest']))
        unknown = dataset_plan([dict(row(99), file='recording.wav')])
        self.assertEqual(unknown['manifest'][0]['split'], 'unassigned')
        with self.assertRaises(ValueError):
            dataset_plan([dict(row(1), file='../private.wav')])

    def test_real_wav_duplicates_are_counted_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            device = root / 'device'
            device.touch()
            folder = root / 'voice' / 'wake'
            folder.mkdir(parents=True)
            for index in (1, 2):
                with wave.open(str(folder / f'20261011-010000-{index:02d}.wav'), 'wb') as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(16000)
                    out.writeframes(b'\xe8\x03' * 16000)
            data, probe = samples(MemoryCore(root, device))
            self.assertEqual((data['valid'], data['duplicates']), (1, 1))
            self.assertTrue(probe)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        test = [row(i) | dict(split='test') for i in range(30)]
        self.plan = dict(dataset_id='d' * 64, manifest=test, positive_split_ready=True)
        self.baseline = dict(dataset_id='d' * 64, test_sha256=sorted(r['sha256'] for r in test),
                             measured_on='pi-zero-2-w', selection_used_test=False,
                             positive_count=30, false_activations=5, negative_hours=10,
                             recall=0.9, true_positives=27, latency_p95_ms=10, threshold=0.8,
                             model_sha256='a' * 64, negative_dataset_sha256='e' * 64)
        self.candidate = self.baseline | dict(model_sha256='b' * 64, recall=29 / 30,
                                             true_positives=29, false_activations=4)

    def test_improved_candidate_can_only_be_ready_for_review(self):
        result = assess_candidate(self.baseline, self.candidate, self.plan)
        self.assertTrue(result['review_ready'])
        self.assertFalse(result['activated'])

    def test_test_leakage_different_corpus_and_regressions_block_review(self):
        for change in (dict(selection_used_test=True), dict(test_sha256=[]),
                       dict(negative_dataset_sha256='f' * 64), dict(recall=0.8),
                       dict(false_activations=6), dict(latency_p95_ms=12),
                       dict(negative_hours=1), dict(recall=0.9, false_activations=5),
                       dict(latency_p95_ms=float('nan')), dict(positive_count=True)):
            with self.subTest(change=change):
                result = assess_candidate(self.baseline, self.candidate | change, self.plan)
                self.assertFalse(result['review_ready'])
                self.assertFalse(result['activated'])


if __name__ == '__main__':
    unittest.main()
