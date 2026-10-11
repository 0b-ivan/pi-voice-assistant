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

OWNER = 'profile-ivan-uuid'
STICK = 'filesystem-uuid:mount-nonce-1'


class StateHelpers:
    def fresh(self):
        return c.fresh(OWNER, 0)

    def add(self, state, i, **options):
        args = dict(expected=c.snapshot(state, OWNER, STICK), stick=STICK, turn_id=str(i),
                    question=f'Frage {i}', answer=f'Antwort {i}', persona='servitor', owner=OWNER, now=i)
        args.update(options)
        return c.commit_turn(state, **args)



class StateTests(StateHelpers, unittest.TestCase):
    def test_original_request_survives_more_than_old_twelve_turn_limit(self):
        state = self.add(self.fresh(), 1, question='Am Samstag nach Hamburg vor 11 Uhr.')
        for i in range(2, 25):
            state = self.add(state, i)
        ctx = c.context(state, OWNER, 'Und Sonntag?', 25)
        self.assertIn('Hamburg', ctx['conversation']['evidence'][0]['q'])
        self.assertEqual(len(ctx['history']), 4)

    def test_retrieval_finds_non_first_old_request(self):
        state = self.fresh()
        for i in range(1, 16):
            state = self.add(state, i, question='Meine Kamera heißt Arducam.' if i == 3 else f'Frage {i}')
        evidence = c.context(state, OWNER, 'Welche Arducam?', 16)['conversation']['evidence']
        self.assertIn('3', [e['id'] for e in evidence])

    def test_working_state_replaces_old_destination_and_keeps_user_source(self):
        state = self.add(self.fresh(), 1, question='Hamburg',
                         updates={'destination': dict(source_id='1', start=0, end=7)})
        state = self.add(state, 2, question='Berlin',
                         updates={'destination': dict(source_id='2', start=0, end=6)})
        self.assertEqual(state['slots']['destination']['quote'], 'Berlin')
        self.assertEqual(state['turns'][0]['q'], 'Hamburg')

    def test_old_source_cannot_restore_superseded_destination(self):
        state = self.add(self.fresh(), 1, question='Hamburg',
                         updates={'destination': dict(source_id='1', start=0, end=7)})
        state = self.add(state, 2, question='Berlin',
                         updates={'destination': dict(source_id='2', start=0, end=6)})
        with self.assertRaises(ValueError):
            self.add(state, 3, context_ids=['1'],
                     updates={'destination': dict(source_id='1', start=0, end=7)})
        self.assertEqual(state['slots']['destination']['quote'], 'Berlin')

    def test_slots_cannot_cite_assistant_or_unexported_source(self):
        state = self.add(self.fresh(), 1, question='Hallo', answer='Hamburg')
        with self.assertRaises(ValueError):
            self.add(state, 2, updates={'destination': dict(source_id='1', start=0, end=7)})
        with self.assertRaises(ValueError):
            self.add(state, 2, context_ids=['1'], updates={'destination': dict(source_id='1', start=0, end=7)})
        self.assertEqual(state['revision'], 1)  # failed patch never partially mutates

    def test_bad_slot_quote_is_rejected_on_reload(self):
        state = self.add(self.fresh(), 1, question='Hamburg',
                         updates={'destination': dict(source_id='1', start=0, end=7)})
        state['slots']['destination']['quote'] = 'Berlin'
        with self.assertRaises(ValueError):
            c.validate(state)

    def test_oversized_text_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            self.add(self.fresh(), 1, question='x' * (c.MAX_TEXT + 1))

    def test_expiry_changes_read_policy_without_erasing_or_resetting(self):
        state = self.add(self.fresh(), 1, question='Hamburg')
        expired = c.context(state, OWNER, 'Und morgen?', c.IDLE_SECONDS + 2)
        self.assertTrue(expired['conversation']['stale'])
        self.assertEqual(expired['history'], [])
        resumed = c.context(state, OWNER, 'Und morgen?', c.IDLE_SECONDS + 2, resume=True)
        self.assertEqual(resumed['history'][0]['q'], 'Hamburg')
        continued = self.add(state, c.IDLE_SECONDS + 3)
        self.assertEqual(continued['id'], state['id'])
        self.assertEqual(len(continued['turns']), 2)

    def test_clock_rollback_is_stale_without_erasure(self):
        state = self.add(self.fresh(), 5)
        self.assertTrue(c.context(state, OWNER, 'q', 1)['conversation']['stale'])
        self.assertEqual(len(state['turns']), 1)

    def test_owner_check_runs_before_context_export(self):
        state = self.add(self.fresh(), 1, question='secret')
        with self.assertRaises(PermissionError):
            c.context(state, 'profile-other', 'q', 2)
        with self.assertRaises(PermissionError):
            c.snapshot(state, 'unknown', STICK)

    def test_persona_switch_keeps_scope_and_content(self):
        state = self.add(self.fresh(), 1, question='Hamburg')
        state = self.add(state, 2, persona='mensch')
        self.assertEqual([h['p'] for h in c.context(state, OWNER, 'q', 3)['history']], ['servitor', 'mensch'])

    def test_duplicate_retry_is_noop_but_old_revision_is_not_new_turn(self):
        state = self.fresh()
        snap = c.snapshot(state, OWNER, STICK)
        state = self.add(state, 1)
        self.assertEqual(self.add(state, 1, expected=snap), state)
        with self.assertRaises(c.StaleTurn):
            self.add(state, 2, expected=snap)

    def test_wrong_mount_or_thread_rejects_late_result(self):
        state = self.fresh()
        snap = c.snapshot(state, OWNER, STICK)
        with self.assertRaises(c.StaleTurn):
            self.add(state, 1, expected=snap, stick='same-label:new-mount')
        with self.assertRaises(c.StaleTurn):
            self.add(self.fresh(), 1, expected=snap)

    def test_generated_reply_and_heard_reply_have_separate_status(self):
        state = self.add(self.fresh(), 1)
        self.assertIsNone(c.repeat_answer(state, OWNER))
        state = c.set_delivery(state, OWNER, '1', 'played')
        state = self.add(state, 2)
        state = c.set_delivery(state, OWNER, '2', 'interrupted')
        self.assertEqual(c.repeat_answer(state, OWNER), 'Antwort 1')
        with self.assertRaises(c.StaleTurn):
            c.set_delivery(state, OWNER, '2', 'played')
        self.assertEqual(len(state['turns']), 2)

    def test_targeted_deletion_removes_dependents_and_cards_not_independent_turn(self):
        state = self.add(self.fresh(), 1, question='Hamburg',
                         updates={'destination': dict(source_id='1', start=0, end=7)})
        state = self.add(state, 2, question='Und Sonntag?', context_ids=['1'])
        state = self.add(state, 3, question='Wie warm ist die CPU?', context_ids=[])
        found = c.matching_ids(state, OWNER, 'Hamburg')
        state = c.forget_ids(state, OWNER, found)
        self.assertEqual([t['id'] for t in state['turns']], ['3'])
        self.assertEqual(state['slots'], {})
        self.assertNotIn('Hamburg', c.compact(state))

    def test_deletion_epoch_prevents_retry_resurrection(self):
        initial = self.fresh()
        snap = c.snapshot(initial, OWNER, STICK)
        state = self.add(initial, 1, question='Hamburg')
        state = c.forget_ids(state, OWNER, ['1'])
        with self.assertRaises(c.StaleTurn):
            self.add(state, 1, expected=snap, question='Hamburg')
        with self.assertRaises(c.StaleTurn):
            c.set_delivery(state, OWNER, '1', 'played')

    def test_bound_keeps_pinned_goal_source_and_prunes_unbacked_cards(self):
        state = self.add(self.fresh(), 1, question='Hamburg',
                         updates={'destination': dict(source_id='1', start=0, end=7)})
        for i in range(2, c.MAX_TURNS + 2):
            state = self.add(state, i)
        self.assertEqual(len(state['turns']), c.MAX_TURNS)
        self.assertEqual(state['slots']['destination']['quote'], 'Hamburg')
        self.assertEqual(state['discarded'], 1)
        state['turns'] = [t for t in state['turns'] if t['id'] != '1']
        self.assertEqual(c._prune(state)['slots'], {})

    def test_malformed_collections_and_boolean_version_rejected(self):
        for key, value in [('turns', 'bad'), ('revision', True), ('version', True), ('owner', 'x' * 81)]:
            with self.assertRaises(ValueError):
                c.validate(dict(self.fresh(), **{key: value}))

    def test_unknown_dependency_rejected(self):
        with self.assertRaises(ValueError):
            self.add(self.fresh(), 1, context_ids=['not-stored'])

    def test_no_match_does_not_delete_everything(self):
        state = self.add(self.fresh(), 1, question='Hamburg')
        self.assertEqual(c.matching_ids(state, OWNER, 'Berlin'), [])
        with self.assertRaises(ValueError):
            c.forget_ids(state, OWNER, [])


class BudgetTests(StateHelpers, unittest.TestCase):
    def test_unicode_and_base64_end_to_end(self):
        state = self.fresh()
        for i in range(1, 7):
            state = self.add(state, i, question='🙂' * 1000, answer='ä' * 500)
        payload, header, ids = c.pack(dict(facts=['fact'] * 100, directives=[]), state, OWNER, 'q', 8,
                                      model_fits=lambda p: True)
        self.assertLessEqual(len(header), c.HEADER_LIMIT)
        self.assertLessEqual(len(c.compact(payload)), c.CHAR_LIMIT)
        self.assertEqual(json.loads(base64.b64decode(header)), payload)
        self.assertIn('6', ids)
        self.assertTrue(all(h['q'] and h['a'] for h in payload['history']))

    def test_model_budget_checks_even_small_wire_packet(self):
        state = self.add(self.fresh(), 1)
        with self.assertRaises(c.BudgetError):
            c.pack(dict(facts=[], directives=[]), state, OWNER, 'q', 2, model_fits=lambda p: False)

    def test_relevance_ranked_fact_survives_before_lower_ranked(self):
        state = self.add(self.fresh(), 1)
        base = dict(facts=['Relevant Hamburg', 'irrelevant' * 100], directives=[])
        p, _, _ = c.pack(base, state, OWNER, 'Hamburg', 2,
                          model_fits=lambda p: len(c.compact(p)) < 600)
        self.assertEqual(p['facts'], ['Relevant Hamburg'])
        self.assertEqual(base['facts'][1], 'irrelevant' * 100)  # export only

    def test_last_pair_overflow_is_explicit(self):
        state = self.add(self.fresh(), 1, question='🙂' * 2048, answer='🙂' * 2048)
        with self.assertRaises(c.BudgetError):
            c.pack({}, state, OWNER, 'q', 2, model_fits=lambda p: True)

    def test_wrong_owner_cannot_export_even_base_copy(self):
        state = self.add(self.fresh(), 1)
        with self.assertRaises(PermissionError):
            c.pack(dict(facts=['secret']), state, 'other', 'q', 2, model_fits=lambda p: True)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / 'proximus'
        root.mkdir()
        self.device = Path(self.tmp.name) / 'device'
        self.device.touch()
        self.core = MemoryCore(root, self.device)
        self.mount = STICK
        self.adapter = c.ConversationAdapter(self.core, lambda: self.mount if self.device.exists() else None)
        self.adapter.ensure(OWNER, 0)

    def append(self, owner=OWNER, now=1, **options):
        state = self.adapter.read(owner)
        args = dict(expected=dict(c.snapshot(state, owner, self.mount), write_epoch=self.core.load()['conversations']['owners'][owner].get('write_epoch', 0)), turn_id=str(now),
                    question='Hamburg', answer='Verstanden', persona='servitor')
        args.update(options)
        return self.adapter.append(owner, now, **args)

    def test_reload_absence_and_write_failure(self):
        self.assertTrue(self.append())
        restored = MemoryCore(self.core.root, self.device)
        self.assertIn('conversations', restored.load())
        with patch.object(self.core, '_save', side_effect=OSError('full')):
            self.assertFalse(self.append(now=2))
        self.assertEqual(len(self.adapter.read(OWNER)['turns']), 1)
        self.device.unlink()
        self.assertIsNone(self.adapter.read(OWNER))
        self.assertFalse(self.adapter.append(OWNER, 2, turn_id='2'))

    def test_separate_persons_do_not_share_threads(self):
        self.adapter.ensure('other-profile', 0)
        self.append()
        self.append('other-profile', question='Berlin')
        self.assertEqual(self.adapter.read(OWNER)['turns'][0]['q'], 'Hamburg')
        self.assertEqual(self.adapter.read('other-profile')['turns'][0]['q'], 'Berlin')

    def test_new_thread_archives_but_delete_removes_current(self):
        self.append()
        old_id = self.adapter.read(OWNER)['id']
        self.adapter.new_thread(OWNER, 2)
        own = self.core.load()['conversations']['owners'][OWNER]
        self.assertIn(old_id, [s['id'] for s in own['threads']])
        self.append(now=3)
        deleted_id = self.adapter.read(OWNER)['id']
        self.adapter.new_thread(OWNER, 4, delete=True)
        own = self.core.load()['conversations']['owners'][OWNER]
        self.assertNotIn(deleted_id, [s['id'] for s in own['threads']])
        self.assertEqual(self.core.load()['history'], [])

    def test_old_thread_snapshot_cannot_write_after_new_thread(self):
        snap = c.snapshot(self.adapter.read(OWNER), OWNER, self.mount)
        self.adapter.new_thread(OWNER, 2)
        with self.assertRaises(c.StaleTurn):
            self.append(now=3, expected=snap)

    def test_thread_count_bounded(self):
        for i in range(1, 10):
            self.adapter.new_thread(OWNER, i)
        self.assertEqual(len(self.core.load()['conversations']['owners'][OWNER]['threads']), c.MAX_THREADS)

    def test_owner_byte_quota_prunes_only_own_material(self):
        self.adapter.ensure('other-profile', 0)
        self.append('other-profile', question='Independent')
        for i in range(1, 22):
            self.append(now=i, question='🙂' * 2048, answer='🙂' * 2048)
        own = self.core.load()['conversations']['owners'][OWNER]
        self.assertLessEqual(len(c.compact(own).encode('utf-8')), c.OWNER_BYTES)
        self.assertEqual(self.adapter.read('other-profile')['turns'][0]['q'], 'Independent')

    def test_unknown_index_version_not_overwritten(self):
        self.core._change(lambda d: d.update(conversations=dict(version=99, owners={})))
        before = self.core.path.read_bytes()
        with self.assertRaises(ValueError):
            self.adapter.ensure('new-owner', 0)
        self.assertEqual(self.core.path.read_bytes(), before)

    def test_new_mount_rejects_request_for_old_stick(self):
        snap = c.snapshot(self.adapter.read(OWNER), OWNER, self.mount)
        self.mount = 'same-label:different-mount'
        with self.assertRaises(c.StaleTurn):
            self.append(expected=snap)


if __name__ == '__main__':
    unittest.main()
