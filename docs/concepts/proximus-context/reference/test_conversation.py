import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'src'))
import conversation as c
from memory import MemoryCore


class ConversationTests(unittest.TestCase):
    def add(self, state, i, **kwargs):
        return c.commit_turn(state, turn_id=str(i), question='Ziel Hamburg ' + str(i),
                             answer='Antwort ' + str(i), persona='servitor', owner='Ivan',
                             now=i, **kwargs)

    def test_compaction_preserves_source_not_assistant_claims(self):
        state = c.fresh('Ivan', 0)
        for i in range(30):
            state = self.add(state, i)
        self.assertEqual(len(state['turns']), 6)
        self.assertEqual(len(state['notes']), 6)
        self.assertEqual(state['notes'][-1]['id'], '23')
        self.assertNotIn('Antwort', json.dumps(state['notes']))

    def test_retries_do_not_duplicate_or_change_revision(self):
        state = self.add(c.fresh('Ivan', 0), 1)
        self.assertEqual(self.add(state, 1), state)
        self.assertEqual(state['revision'], 1)

    def test_speaker_and_persona(self):
        state = self.add(c.fresh('Ivan', 0), 1)
        with self.assertRaises(PermissionError):
            c.commit_turn(state, turn_id='2', question='q', answer='a', persona='mensch',
                          owner='unknown', now=2)
        state = c.commit_turn(state, turn_id='2', question='q', answer='a', persona='mensch',
                              owner='Ivan', now=2)
        self.assertEqual([t['p'] for t in state['turns']], ['servitor', 'mensch'])
        self.assertFalse(c.available(state, 'unknown', 2))

    def test_reset_forget_and_expiry(self):
        state = self.add(c.fresh('Ivan', 0), 1)
        self.assertFalse(c.available(state, 'Ivan', c.IDLE_SECONDS + 2))
        self.assertTrue(c.available(state, 'Ivan', c.IDLE_SECONDS + 2, resume=True))
        for fn in (c.reset, c.forget_context):
            cleared = fn(state, 'Ivan', 3)
            self.assertNotEqual(cleared['id'], state['id'])
            self.assertEqual(cleared['turns'] + cleared['notes'] + cleared['seen'], [])

    def test_unicode_transport_and_no_partial_pairs(self):
        state = c.fresh('Ivan', 0)
        for i in range(6):
            state = c.commit_turn(state, turn_id=str(i), question='🙂' * 800,
                                 answer='ä' * 800, persona='servitor', owner='Ivan', now=i)
        payload, encoded = c.pack(dict(facts=['f' * 200] * 100, directives=[]),
                                   state, 'Ivan', 6)
        self.assertLessEqual(len(encoded), c.HEADER_LIMIT)
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))), c.CHAR_LIMIT)
        self.assertEqual(json.loads(base64.b64decode(encoded)), payload)
        self.assertTrue(all(set(t) == {'q', 'a', 'p'} for t in payload['history']))
        with self.assertRaises(ValueError):
            c.pack(dict(directives=['x' * 13000]), state, 'Ivan', 6)

    def test_wrong_owner_and_expired_hide_legacy_history(self):
        state = self.add(c.fresh('Ivan', 0), 1)
        for owner, now in [('guest', 1), ('Ivan', c.IDLE_SECONDS + 2)]:
            payload, _ = c.pack(dict(facts=[], directives=[], history=[{'q': 'secret'}]),
                                state, owner, now)
            self.assertEqual(payload['history'], [])
            self.assertNotIn('conversation', payload)

    def test_unknown_schema_and_bad_collections_fail_closed(self):
        state = c.fresh('Ivan', 0)
        for key, value in [('version', 99), ('turns', 'invalid'), ('revision', True)]:
            bad = dict(state, **{key: value})
            with self.assertRaises(ValueError):
                c.validate(bad)

    def test_stick_reload_absence_and_write_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'proximus'
            root.mkdir()
            device = Path(folder) / 'device'
            device.touch()
            core = MemoryCore(root, device)
            adapter = c.ConversationAdapter(core)
            args = dict(turn_id='1', question='Hamburg', answer='Verstanden', persona='servitor')
            self.assertTrue(adapter.append('Ivan', 1, **args))
            restored = MemoryCore(root, device)
            self.assertEqual(restored.load()['conversation']['turns'][0]['q'], 'Hamburg')
            with patch.object(core, '_save', side_effect=OSError('full')):
                self.assertFalse(adapter.append('Ivan', 2, **dict(args, turn_id='2')))
            self.assertEqual(len(restored.load()['conversation']['turns']), 1)
            device.unlink()
            self.assertFalse(adapter.append('Ivan', 3, **dict(args, turn_id='3')))
            self.assertIsNone(core.load())
            device.touch()
            self.assertTrue(adapter.clear('Ivan', 4))
            self.assertEqual(core.load()['conversation']['turns'], [])


if __name__ == '__main__':
    unittest.main()
