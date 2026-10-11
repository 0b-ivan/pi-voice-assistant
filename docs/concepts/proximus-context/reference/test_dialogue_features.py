import copy
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'src'))
import conversation as c
import dialogue_features as f
from memory import MemoryCore

OWNER = 'profile-ivan-uuid'
STICK = 'filesystem-uuid:mount-nonce-1'


def stamp(iso):
    return int(datetime.fromisoformat(iso).timestamp())


class PendingTests(unittest.TestCase):
    def add(self, state, i, **extras):
        return c.commit_turn(state, expected=c.snapshot(state, OWNER, STICK), stick=STICK,
            owner=OWNER, now=i, turn_id=str(i), question='Nebenfrage',
            answer='Von welchem Bahnhof?', persona='servitor', **extras)

    def opened(self):
        return self.add(c.fresh(OWNER, 0), 1,
                        open_questions=[dict(field='start_station', start=0, end=20)])

    def test_open_question_survives_side_topics_and_prompt_window(self):
        state = self.opened()
        for i in range(2, 20):
            state = self.add(state, i)
        ctx = c.context(state, OWNER, 'Von Berlin', 20)
        self.assertEqual(ctx['conversation']['questions'][0]['quote'], 'Von welchem Bahnhof?')
        self.assertEqual(ctx['conversation']['slots'], {})  # assistant question is not user fact

    def test_answer_closes_specific_question_only(self):
        state = self.opened()
        state = self.add(state, 2, open_questions=[dict(field='arrival', start=0, end=20)])
        first, second = state['questions']
        state = self.add(state, 3, resolve_questions=[first['id']])
        self.assertEqual([q['id'] for q in f.pending_context(state)], [second['id']])
        self.assertEqual(state['questions'][0]['answer_id'], '3')

    def test_duplicate_field_and_unknown_resolution_do_not_guess(self):
        state = self.opened()
        with self.assertRaises(f.ClarificationNeeded):
            self.add(state, 2, open_questions=[dict(field='start_station', start=0, end=20)])
        with self.assertRaises(ValueError):
            self.add(state, 2, resolve_questions=['not-a-question'])

    def test_skipping_question_closes_it_without_inventing_answer(self):
        state = self.opened()
        state = self.add(state, 2, dismiss_questions=[state['questions'][0]['id']])
        self.assertEqual(f.pending_context(state), [])
        self.assertEqual(state['questions'][0]['status'], 'dismissed')
        self.assertIsNone(state['questions'][0]['answer_id'])

    def test_open_question_does_not_claim_confirmed_audio(self):
        state = self.opened()
        self.assertEqual(f.pending_context(state)[0]['delivery'], 'pending')
        state = c.set_delivery(state, OWNER, '1', 'failed')
        self.assertEqual(f.pending_context(state)[0]['delivery'], 'failed')

    def test_deletion_removes_pending_question_and_no_stale_resolution(self):
        state = self.opened()
        question_id = state['questions'][0]['id']
        state = c.forget_ids(state, OWNER, ['1'])
        self.assertEqual(state['questions'], [])
        with self.assertRaises(ValueError):
            self.add(state, 2, resolve_questions=[question_id])

    def test_bound_rejects_four_open_questions(self):
        state = c.fresh(OWNER, 0)
        for i in range(1, 4):
            state = self.add(state, i, open_questions=[dict(field=f'field_{i}', start=0, end=20)])
        with self.assertRaises(ValueError):
            self.add(state, 4, open_questions=[dict(field='field_4', start=0, end=20)])

    def test_source_pinned_beyond_48_turn_window(self):
        state = self.opened()
        for i in range(2, 55):
            state = self.add(state, i)
        self.assertEqual(state['questions'][0]['source_id'], '1')
        self.assertEqual(len(state['turns']), c.MAX_TURNS)

    def test_source_tampering_rejected(self):
        state = self.opened()
        state['questions'][0]['quote'] = 'Erfundene Frage'
        with self.assertRaises(ValueError):
            c.validate(state)

    def test_exported_question_source_in_dependency_ids(self):
        state = self.opened()
        for i in range(2, 10):
            state = self.add(state, i)
        _, _, ids = c.pack({}, state, OWNER, 'q', 10, model_fits=lambda p: True)
        self.assertIn('1', ids)


class TimeTests(unittest.TestCase):
    def test_tomorrow_remains_fixed_after_reload(self):
        captured = stamp('2026-10-11T22:30:00+00:00')  # 12 October locally already
        result = f.resolve_time('morgen um neun', captured, 'Europe/Berlin')
        self.assertEqual(result['date'], '2026-10-13')
        self.assertEqual(result['utc'], '2026-10-13T07:00:00+00:00')
        ref = f.make_time_ref('morgen um neun', 0, 14, captured, 'Europe/Berlin')
        f.validate_times([copy.deepcopy(ref)], 'morgen um neun')
        self.assertEqual(ref['date'], '2026-10-13')

    def test_date_only_does_not_invent_midnight(self):
        result = f.resolve_time('morgen', stamp('2026-10-11T10:00:00+00:00'), 'Europe/Berlin')
        self.assertEqual(result['precision'], 'date')
        self.assertIsNone(result['utc'])

    def test_dst_gap_requires_clarification(self):
        with self.assertRaises(f.ClarificationNeeded):
            f.resolve_time('morgen um 2:30', stamp('2026-03-28T12:00:00+00:00'), 'Europe/Berlin')

    def test_dst_fold_requires_clarification(self):
        with self.assertRaises(f.ClarificationNeeded):
            f.resolve_time('morgen um 2:30', stamp('2026-10-24T12:00:00+00:00'), 'Europe/Berlin')

    def test_invalid_or_unsupported_time_does_not_guess(self):
        for text in ('morgen um 25', 'morgen um 9:70', 'nächsten Freitag', 'morgen früh'):
            with self.assertRaises(f.ClarificationNeeded):
                f.resolve_time(text, 1000, 'Europe/Berlin')

    def test_changed_utc_resolution_rejected(self):
        ref = f.make_time_ref('morgen um 9', 0, 11, 1000, 'Europe/Berlin')
        ref['utc'] = '2030-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):
            f.validate_times([ref], 'morgen um 9')

    def test_source_and_timestamp_reach_model_context(self):
        q = 'morgen um 9'
        captured = stamp('2026-10-11T10:00:00+00:00')
        ref = f.make_time_ref(q, 0, len(q), captured, 'Europe/Berlin')
        state = c.fresh(OWNER, captured)
        state = c.commit_turn(state, expected=c.snapshot(state, OWNER, STICK), stick=STICK,
            turn_id='1', question=q, answer='Verstanden', owner=OWNER, persona='servitor',
            now=captured + 10, time_refs=[ref])
        self.assertEqual(c.context(state, OWNER, 'q', captured + 20)['history'][0]['time_refs'][0]['captured_at'], captured)


class FeatureStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / 'proximus'
        root.mkdir()
        self.device = Path(self.tmp.name) / 'device'
        self.device.touch()
        self.core = MemoryCore(root, self.device)
        self.adapter = f.FeatureAdapter(self.core, lambda: STICK if self.device.exists() else None)
        self.adapter.ensure(OWNER, 0)

    def add(self, i, owner=OWNER, question='Hamburg', **options):
        return self.adapter.append(owner, i, expected=self.adapter.request_snapshot(owner),
            turn_id=str(i), question=question, answer='Verstanden', persona='servitor', **options)

    def correct(self, revision=0, turn='1', start=0, end=7, key='home_city'):
        return self.adapter.correct_fact(OWNER, key=key, source_thread=self.adapter.read(OWNER)['id'],
            source_turn=turn, start=start, end=end, expected_revision=revision)

    def test_relative_date_survives_actual_memory_core_reload(self):
        captured = stamp('2026-10-11T10:00:00+00:00')
        q = 'morgen um neun'
        ref = f.make_time_ref(q, 0, len(q), captured, 'Europe/Berlin')
        self.add(captured + 10, question=q, time_refs=[ref])
        fresh = f.FeatureAdapter(MemoryCore(self.core.root, self.device), lambda: STICK)
        saved = fresh.read(OWNER)['turns'][0]['time_refs'][0]
        self.assertEqual(saved['date'], '2026-10-12')
        self.assertEqual(saved['captured_at'], captured)

    def test_invalid_mount_hides_metadata_and_refuses_content_actions(self):
        self.add(1)
        self.correct()
        before = self.core.path.read_bytes()
        self.adapter.generation = lambda: None
        self.assertEqual(self.adapter.threads(OWNER), [])
        self.assertIsNone(self.adapter.explain_fact(OWNER, 'home_city'))
        self.assertFalse(self.adapter.set_recording(OWNER, enabled=False))
        self.assertFalse(self.adapter.name_thread(OWNER, 'Should not save'))
        self.assertEqual(self.core.path.read_bytes(), before)

    def test_fact_correction_replaces_one_key_and_keeps_other_owner(self):
        self.add(1)
        self.correct()
        self.adapter.ensure('other-profile', 0)
        self.add(1, owner='other-profile', question='Berlin')
        self.add(2, question='Leipzig')
        self.correct(revision=1, turn='2')
        fact = self.adapter.explain_fact(OWNER, 'home_city')
        self.assertEqual(fact['value'], 'Leipzig')
        self.assertEqual(fact['source']['role'], 'user')
        self.assertEqual(self.adapter.read('other-profile')['turns'][0]['q'], 'Berlin')

    def test_fact_correction_needs_user_source_and_valid_revision(self):
        self.add(1)
        self.correct()
        with self.assertRaises(c.StaleTurn):
            self.correct(revision=0)
        with self.assertRaises(ValueError):
            self.correct(revision=1, turn='not-existing')
        with self.assertRaises(f.ClarificationNeeded):
            self.correct(revision=1, key='favorite_cities')

    def test_pause_blocks_turns_and_fact_learning_survives_reload(self):
        self.add(1)
        self.adapter.set_recording(OWNER, enabled=False)
        before = self.core.path.read_bytes()
        self.assertFalse(self.add(2, question='Private secret'))
        with self.assertRaises(f.RecordingPaused):
            self.correct()
        self.assertEqual(self.core.path.read_bytes(), before)
        fresh = f.FeatureAdapter(MemoryCore(self.core.root, self.device), lambda: STICK)
        self.assertFalse(fresh.core.load()['conversations']['owners'][OWNER]['recording'])
        self.assertNotIn('Private secret', fresh.core.path.read_text())

    def test_private_thread_ends_at_explicit_new_thread(self):
        self.adapter.set_recording(OWNER, enabled=False, scope='thread')
        self.assertFalse(self.add(1, question='Private secret'))
        self.adapter.new_thread(OWNER, 2)
        self.assertTrue(self.add(3, question='Ordinary'))
        self.assertNotIn('Private secret', self.core.path.read_text())

    def test_owner_pause_does_not_end_with_new_thread(self):
        self.adapter.set_recording(OWNER, enabled=False)
        self.adapter.new_thread(OWNER, 2)
        self.assertFalse(self.add(3))

    def test_old_turn_does_not_save_after_pause_resume(self):
        snap = self.adapter.request_snapshot(OWNER)
        self.adapter.set_recording(OWNER, enabled=False)
        self.adapter.set_recording(OWNER, enabled=True)
        with self.assertRaises(c.StaleTurn):
            self.adapter.append(OWNER, 1, expected=snap, turn_id='old', question='Private secret',
                answer='a', persona='servitor')
        self.assertTrue(self.add(2, question='New ordinary question'))

    def test_named_archive_is_selected_instead_of_wrong_thread(self):
        self.add(1)
        self.adapter.name_thread(OWNER, 'Reiseplanung')
        first_id = self.adapter.read(OWNER)['id']
        self.adapter.new_thread(OWNER, 2)
        self.adapter.name_thread(OWNER, 'Pi Umbau')
        self.adapter.open_thread(OWNER, 'Reiseplanung')
        self.assertEqual(self.adapter.read(OWNER)['id'], first_id)
        self.assertEqual(len(self.adapter.threads(OWNER)), 2)

    def test_ambiguous_archive_and_duplicate_title_require_clarification(self):
        self.adapter.name_thread(OWNER, 'Reise Hamburg')
        self.adapter.new_thread(OWNER, 1)
        with self.assertRaises(f.ClarificationNeeded):
            self.adapter.name_thread(OWNER, 'Reise Hamburg')
        self.adapter.name_thread(OWNER, 'Reise Berlin')
        with self.assertRaises(f.ClarificationNeeded):
            self.adapter.open_thread(OWNER, 'Reise')

    def test_other_owner_cannot_open_or_explain_my_archive(self):
        self.add(1)
        self.correct()
        self.adapter.name_thread(OWNER, 'Reiseplanung')
        self.adapter.ensure('other-profile', 0)
        with self.assertRaises(f.ClarificationNeeded):
            self.adapter.open_thread('other-profile', 'Reiseplanung')
        self.assertIsNone(self.adapter.explain_fact('other-profile', 'home_city'))

    def test_why_explanation_uses_actual_quote_not_model_reasoning(self):
        self.add(1)
        self.correct()
        fact = self.adapter.explain_fact(OWNER, 'home_city')
        self.assertEqual(fact['source']['quote'], 'Hamburg')
        self.assertEqual(fact['source']['turn_id'], '1')
        self.assertIsNone(self.adapter.explain_fact(OWNER, 'occupation'))

    def test_slot_explanation_requires_retained_source(self):
        self.add(1, updates={'destination': dict(source_id='1', start=0, end=7)})
        result = self.adapter.explain_slot(OWNER, 'destination')
        self.assertEqual(result['quote'], 'Hamburg')
        self.assertEqual(result['role'], 'user')
        self.assertIsNone(self.adapter.explain_slot(OWNER, 'missing'))

    def test_paused_title_cannot_persist_private_content(self):
        self.adapter.set_recording(OWNER, enabled=False)
        with self.assertRaises(f.RecordingPaused):
            self.adapter.name_thread(OWNER, 'Private secret')
        self.assertNotIn('Private secret', self.core.path.read_text())

    def test_failed_pause_write_does_not_claim_success(self):
        with patch.object(self.core, '_save', side_effect=OSError('full')):
            self.assertFalse(self.adapter.set_recording(OWNER, enabled=False))
        self.assertTrue(self.core.load()['conversations']['owners'][OWNER]['recording'])

    def test_deleted_thread_is_not_found_by_old_name(self):
        self.adapter.name_thread(OWNER, 'Reiseplanung')
        self.adapter.new_thread(OWNER, 2, delete=True)
        with self.assertRaises(f.ClarificationNeeded):
            self.adapter.open_thread(OWNER, 'Reiseplanung')

    def test_correction_write_epoch_rejects_in_flight_old_context(self):
        self.add(1)
        snap = self.adapter.request_snapshot(OWNER)
        self.correct()
        with self.assertRaises(c.StaleTurn):
            self.adapter.append(OWNER, 2, expected=snap, turn_id='late', question='q',
                answer='Hamburg is current', persona='servitor')


class CommandTests(unittest.TestCase):
    def test_command_precedence_preserves_names_and_corrected_value(self):
        self.assertEqual(f.command('Speichere das als Reiseplanung'), ('name_thread', 'Reiseplanung'))
        self.assertEqual(f.command('Korrigiere meinen Wohnort auf Leipzig'), ('correct_home_city', 'Leipzig'))
        self.assertEqual(f.command('Speichere dieses Gespräch nicht'), ('pause', 'thread'))
        self.assertEqual(f.command('Speichere wieder'), ('record', 'owner'))

    def test_archive_shorthand_requires_known_title(self):
        self.assertIsNone(f.command('Öffne das Fenster'))
        self.assertIsNone(f.command('Öffne Reiseplanung'))
        self.assertEqual(f.command('Öffne Reiseplanung', ['Reiseplanung']), ('open_thread', 'Reiseplanung'))
        self.assertEqual(f.command('Öffne das Gespräch Reiseplanung'), ('open_thread', 'Reiseplanung'))

    def test_quoted_instruction_and_general_question_are_not_commands(self):
        for phrase in ('Erkläre den Satz Speichere wieder', 'Wie pausiert man das Gedächtnis?',
                       'Was bedeutet korrigiere meinen Wohnort?', '"Speichere dieses Gespräch nicht"'):
            self.assertIsNone(f.command(phrase))


if __name__ == '__main__':
    unittest.main()
