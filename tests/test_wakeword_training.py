import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server' / 'wakeword'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import train_wakeword as trainer
from nightwatch_training import dataset_plan, read_plan
from test_nightwatch_training import session_for


class TrainingSplitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.audio = self.root / 'recordings'
        self.audio.mkdir()
        self.manifest = self.root / 'manifest.json'
        records = []
        index = 0
        for split, count in (('train', 10), ('validation', 5), ('test', 30)):
            for take in range(count):
                name = f'{session_for(split)}-{take:02d}.wav'
                pcm = np.full(16000, 1000 + index, dtype='<i2').tobytes()
                index += 1
                with wave.open(str(self.audio / name), 'wb') as out:
                    out.setnchannels(1)
                    out.setsampwidth(2)
                    out.setframerate(16000)
                    out.writeframes(pcm)
                records.append(dict(file=name, frames=16000,
                                    sha256=hashlib.sha256(pcm).hexdigest()))
        self.plan = dataset_plan(records)
        self.manifest.write_text(json.dumps(self.plan))
        (self.root / 'models').mkdir()
        for name in ('melspectrogram.onnx', 'embedding_model.onnx'):
            (self.root / 'models' / name).write_bytes(name.encode())
        self.args = SimpleNamespace(real=self.audio, manifest=self.manifest, copies=2, seed=7)
        for patcher in (patch.object(trainer, 'ROOT', self.root),
                        patch.object(trainer, 'load_rirs', return_value=[]),
                        patch.object(trainer, 'Features', return_value=lambda pcm:
                                     np.full((16, 96), float(pcm.mean()) / 4096, dtype=np.float16))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def build(self):
        trainer.build_real(self.args)
        return self.root / 'features'

    def test_sessions_are_separate_before_augmentation_and_final_test_is_unaugmented(self):
        original = trainer.augment
        with patch.object(trainer, 'augment', wraps=original) as augment:
            folder = self.build()
        # Only 10 training clips x2 and 5 validation clips x4 are augmented.
        self.assertEqual(augment.call_count, 40)
        meta = json.loads((folder / 'real_manifest.json').read_text())
        self.assertEqual([meta['files'][split]['rows'] for split in ('train', 'validation', 'test')],
                         [20, 20, 30])
        ids = [set(meta['files'][split]['clips']) for split in ('train', 'validation', 'test')]
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
        self.assertFalse(meta['test_augmented'])
        self.assertEqual(meta['plan']['dataset_id'], self.plan['dataset_id'])

    def test_training_loads_no_final_test_data_even_if_test_file_is_absent(self):
        folder = self.build()
        (folder / 'positive_real_test.npy').unlink()
        with patch.object(trainer.np, 'load', wraps=np.load) as load:
            train, validation, plan = trainer.load_real_training(folder)
        self.assertEqual((train.shape, validation.shape), ((20, 16, 96), (20, 16, 96)))
        self.assertEqual(plan['dataset_id'], self.plan['dataset_id'])
        self.assertEqual([Path(call.args[0]).name for call in load.call_args_list],
                         ['positive_real_train.npy', 'positive_real_validation.npy'])

    def test_changed_audio_is_rejected_before_loading_any_model(self):
        name = self.plan['manifest'][0]['file']
        path = self.audio / name
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        with patch.object(trainer, 'Features') as features:
            with self.assertRaisesRegex(ValueError, 'content changed'):
                trainer.build_real(self.args)
        features.assert_not_called()
        self.assertFalse((self.root / 'features' / 'real_manifest.json').exists())

    def test_edited_split_and_duplicate_identity_cannot_make_training_ready(self):
        data = json.loads(self.manifest.read_text())
        data['manifest'][0]['split'] = 'unassigned'
        self.manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            read_plan(self.manifest)
        data = json.loads(json.dumps(self.plan))
        data['manifest'].append(data['manifest'][0])
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            read_plan(self.manifest)

    def test_interrupted_or_mismatched_feature_build_cannot_use_an_old_index(self):
        folder = self.build()
        path = folder / 'positive_real_train.npy'
        values = np.load(path)
        values[0, 0, 0] += 1
        np.save(path, values)
        with self.assertRaisesRegex(ValueError, 'content or clip identity'):
            trainer.load_real_training(folder)

    def test_different_frontend_and_nonfinite_features_are_rejected(self):
        folder = self.build()
        model = self.root / 'models' / 'embedding_model.onnx'
        previous = model.read_bytes()
        model.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'different frontend'):
            trainer.load_real_training(folder)
        model.write_bytes(previous)
        path = folder / 'positive_real_validation.npy'
        values = np.load(path)
        values[0, 0, 0] = np.nan
        np.save(path, values)
        meta_path = folder / 'real_manifest.json'
        meta = json.loads(meta_path.read_text())
        meta['files']['validation']['sha256'] = trainer.file_hash(path)
        meta_path.write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, 'invalid real feature values'):
            trainer.load_real_training(folder)

    def test_legacy_every_fifth_clip_features_need_an_explicit_rebuild(self):
        folder = self.root / 'features'
        folder.mkdir()
        (folder / 'positive_real.npy').write_bytes(b'legacy')
        with self.assertRaisesRegex(ValueError, 'legacy real features'):
            trainer.load_real_training(folder)

    def test_frontend_changed_during_build_never_publishes_a_successful_index(self):
        def changed(pcm):
            (self.root / 'models' / 'embedding_model.onnx').write_bytes(b'changed')
            return np.zeros((16, 96), dtype=np.float16)
        with patch.object(trainer, 'Features', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                trainer.build_real(self.args)
        self.assertFalse((self.root / 'features' / 'real_manifest.json').exists())


class ValidationThresholdTests(unittest.TestCase):
    def test_rounding_does_not_allow_a_checkpoint_above_the_false_alarm_budget(self):
        negative = np.zeros(89111)
        negative[:2] = 0.6
        threshold, recall, rates = trainer.validation_threshold(np.array([0.8, 0.9]), negative, 0.5)
        self.assertGreater(rates[0.5], 0.5)
        self.assertEqual(round(rates[0.5], 2), 0.5)
        self.assertEqual(threshold, 0.7)
        self.assertEqual(recall, 1.0)


if __name__ == '__main__':
    unittest.main()
