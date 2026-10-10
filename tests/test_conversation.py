import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import conversation as c  # noqa: E402
import memory  # noqa: E402

IVAN, ANNA = 'a' * 32, 'b' * 32
HOUR = 3600


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'stick' / 'proximus'
        self.root.mkdir(parents=True)
        self.device = Path(self.tmp.name) / 'device'
        self.device.touch()
        self.now = 1_000_000
        self.stick = 'fs-1:mount-1'
        self.core = memory.MemoryCore(self.root, self.device, clock=lambda: self.now)
        self.store = c.Conversations(self.core, lambda: self.stick)
        self.n = 0

    def say(self, owner, question, answer='Verstanden.', updates=None, expected=None,
            turn_id=None, persona='servitor'):
        """One turn as the controller will run it: prepare, answer, commit."""
        snap, ctx = self.store.prepare(owner, question)
        self.n += 1
        turn_id = turn_id or f't{self.n}'
        exported = [h['id'] for h in ctx['history']] + [e['id'] for e in
                                                         ctx['conversation']['evidence']]
        if updates:
            updates = {k: dict(source_id=turn_id if v[0] is None else v[0],
                               start=question.index(v[1]) if v[0] is None else v[2],
                               end=(question.index(v[1]) + len(v[1])) if v[0] is None else v[3])
                       for k, v in updates.items()}
        self.now += 60
        result = self.store.append(owner, expected or snap, turn_id=turn_id, question=question,
                                   answer=answer, persona=persona, now=self.now,
                                   context_ids=exported, updates=updates)
        return turn_id, ctx, result

    def thread(self, owner):
        return self.store.read(owner)[1]


class ContextTests(Rig):
    def test_goal_survives_fifteen_side_questions_and_new_goal_replaces_it(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Am Samstag nach Hamburg vor 11 Uhr.',
                 updates={'destination': (None, 'Hamburg')})
        for i in range(15):
            self.say(IVAN, f'Wie viele Kerne hat die CPU, Frage {i}?')
        _, ctx, _ = self.say(IVAN, 'Und Sonntag?')
        self.assertIn('Hamburg', ctx['conversation']['evidence'][0]['q'])   # first request
        self.assertEqual(ctx['conversation']['slots']['destination']['quote'], 'Hamburg')
        self.assertEqual(len(ctx['history']), 4)
        self.say(IVAN, 'Lieber Berlin statt Hamburg.', updates={'destination': (None, 'Berlin')})
        _, ctx, _ = self.say(IVAN, 'Und wann?')
        self.assertEqual(ctx['conversation']['slots']['destination']['quote'], 'Berlin')
        self.assertEqual(self.thread(IVAN)['turns'][0]['q'], 'Am Samstag nach Hamburg vor 11 Uhr.')

    def test_old_matching_turn_is_found_besides_the_first_request(self):
        self.store.ensure(IVAN)
        for i in range(12):
            self.say(IVAN, 'Meine Kamera heißt Arducam.' if i == 5 else f'Nebenfrage {i}')
        _, ctx, _ = self.say(IVAN, 'Welche Auflösung hat die Arducam?')
        self.assertIn('Meine Kamera heißt Arducam.', [e['q'] for e in
                                                      ctx['conversation']['evidence']])

    def test_slot_must_quote_user_text_of_an_exported_turn(self):
        self.store.ensure(IVAN)
        first, _, _ = self.say(IVAN, 'Hallo', answer='Hamburg ist schön.')
        with self.assertRaises(ValueError):    # assistant text is no source
            self.say(IVAN, 'Ja', updates={'destination': (first, None, 0, 7)})
        self.assertEqual(len(self.thread(IVAN)['turns']), 1)   # nothing half written

    def test_pause_after_twelve_hours_keeps_everything_and_resume_brings_it_back(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Wir planen die Reise nach Hamburg.')
        self.now += 13 * HOUR
        snap, ctx = self.store.prepare(IVAN, 'Und morgen?')
        self.assertEqual(ctx['history'], [])
        self.assertTrue(ctx['conversation']['stale'])
        # Restart: a new core on the same stick still has the turn.
        self.core = memory.MemoryCore(self.root, self.device, clock=lambda: self.now)
        self.store = c.Conversations(self.core, lambda: self.stick)
        self.assertEqual(self.store.resume(IVAN)['first'], 'Wir planen die Reise nach Hamburg.')
        _, ctx = self.store.prepare(IVAN, 'Und morgen?')
        self.assertEqual(len(ctx['history']), 1)
        self.say(IVAN, 'Und morgen?')
        self.assertIsNone(self.store.read(IVAN)[0]['resume'])   # until the next stored turn

    def test_unicode_and_oversized_text(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Größe von Ärmelkanal 🚢 – „Grüße“')
        self.assertEqual(self.thread(IVAN)['turns'][0]['q'], 'Größe von Ärmelkanal 🚢 – „Grüße“')
        with self.assertRaises(ValueError):
            self.say(IVAN, 'x' * (c.MAX_TEXT + 1))


class SafetyTests(Rig):
    def test_two_people_are_separate_and_quota_never_evicts_the_other(self):
        self.store.ensure(IVAN)
        self.store.ensure(ANNA)
        self.say(ANNA, 'Ich fahre nach Leipzig.')
        for i in range(60):
            self.say(IVAN, f'Lange Frage {i} ' + 'ä' * 1500, answer='Antwort ' + 'ö' * 1500)
        _, ctx = self.store.prepare(ANNA, 'Wohin?')
        self.assertEqual([h['q'] for h in ctx['history']], ['Ich fahre nach Leipzig.'])
        own = self.store.read(IVAN)[0]
        self.assertLessEqual(c._owner_size(own), c.OWNER_BYTES)
        self.assertGreater(self.thread(IVAN)['discarded'], 0)
        self.assertNotIn('Leipzig', json.dumps(own, ensure_ascii=False))

    def test_owner_limit_evicts_nobody(self):
        for i in range(c.MAX_OWNERS):
            self.assertTrue(self.store.ensure(f'{i:032x}'))
        with self.assertRaises(c.QuotaError):
            self.store.ensure('f' * 32)

    def test_duplicate_turn_id_is_stored_once(self):
        self.store.ensure(IVAN)
        snap, _ = self.store.prepare(IVAN, 'Hallo')
        args = dict(turn_id='x1', question='Hallo', answer='Gegrüßt.', persona='servitor',
                    now=self.now)
        self.assertTrue(self.store.append(IVAN, snap, **args))
        self.assertFalse(self.store.append(IVAN, snap, **args))
        self.assertEqual(len(self.thread(IVAN)['turns']), 1)

    def test_late_reply_after_reset_or_thread_switch_is_refused(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Erste Frage')
        late, _ = self.store.prepare(IVAN, 'Zweite Frage')
        self.store.forget_thread(IVAN)
        with self.assertRaises(c.StaleTurn):
            self.say(IVAN, 'Zweite Frage', expected=late)
        self.say(IVAN, 'Thema A')
        late, _ = self.store.prepare(IVAN, 'Noch zu A')
        self.store.new_thread(IVAN)
        with self.assertRaises(c.StaleTurn):
            self.say(IVAN, 'Noch zu A', expected=late)
        self.assertEqual(self.thread(IVAN)['turns'], [])

    def test_other_stick_or_absent_stick_writes_nothing(self):
        self.store.ensure(IVAN)
        snap, _ = self.store.prepare(IVAN, 'Hallo')
        self.stick = 'fs-1:mount-2'          # same stick plugged in again
        with self.assertRaises(c.StaleTurn):
            self.store.append(IVAN, snap, turn_id='x', question='Hallo', answer='Ja.',
                              persona='servitor', now=self.now)
        self.stick = None
        self.assertIsNone(self.store.append(IVAN, snap, turn_id='y', question='Hallo',
                                            answer='Ja.', persona='servitor', now=self.now))
        self.stick = 'fs-1:mount-2'
        self.assertEqual(self.thread(IVAN)['turns'], [])

    def test_read_only_stick_reports_failure(self):
        self.store.ensure(IVAN)
        os.chmod(self.root, 0o500)
        self.addCleanup(os.chmod, self.root, 0o700)
        if os.access(self.root, os.W_OK):
            self.skipTest('running as root')
        self.assertIsNone(self.store.new_thread(IVAN))
        self.assertEqual(c.reply('new_thread', None), "Gedächtniskern nicht verfügbar. "
                                                      "Nichts geändert.")

    def test_unknown_schema_is_never_overwritten(self):
        (self.root / 'memory.json').write_text(json.dumps(dict(
            version=1, facts=[], directives=[], history=[],
            conversations=dict(version=9, owners={}))))
        with self.assertRaises(c.UnsupportedSchema):
            self.store.ensure(IVAN)
        self.assertEqual(json.loads((self.root / 'memory.json').read_text())
                         ['conversations']['version'], 9)

    def test_oversized_memory_file_is_not_parsed_or_replaced(self):
        path = self.root / 'memory.json'
        path.write_text('{"facts": [' + '"x",' * (memory.MAX_FILE_BYTES // 4) + '"x"]}')
        size = path.stat().st_size
        self.assertIsNone(self.core.load())
        self.assertEqual(path.stat().st_size, size)


class PlaybackTests(Rig):
    def test_delivery_is_separate_and_late_events_after_forget_are_ignored(self):
        self.store.ensure(IVAN)
        first, _, _ = self.say(IVAN, 'Erzähl was', answer='Erste Antwort.')
        second, _, _ = self.say(IVAN, 'Noch was', answer='Zweite Antwort.')
        revision = self.thread(IVAN)['revision']
        self.assertTrue(self.store.delivered(IVAN, first, 'played'))
        self.assertTrue(self.store.delivered(IVAN, second, 'failed'))     # TTS error
        state = self.thread(IVAN)
        self.assertEqual(state['revision'], revision)          # no new turn or revision
        self.assertEqual(c.last_answer(state), ('Erste Antwort.', 'played'))
        with self.assertRaises(c.StaleTurn):
            self.store.delivered(IVAN, first, 'interrupted')    # final stays final
        self.store.forget_thread(IVAN)
        self.assertFalse(self.store.delivered(IVAN, second, 'played'))

    def test_unconfirmed_answer_is_reported_as_such(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Frage', answer='Antwort.')
        self.assertEqual(c.last_answer(self.thread(IVAN)), ('Antwort.', 'pending'))


class ForgetTests(Rig):
    def test_targeted_forget_removes_followups_and_keeps_other_threads(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Unabhängig: wie warm ist es?')
        self.store.new_thread(IVAN)
        hamburg, _, _ = self.say(IVAN, 'Ich will nach Hamburg.',
                                 updates={'destination': (None, 'Hamburg')})
        self.say(IVAN, 'Welcher Zug ist am schnellsten?')     # had Hamburg in context
        late, _ = self.store.prepare(IVAN, 'Und zurück?')
        self.assertEqual(self.store.forget_matching(IVAN, 'Hamburg'), (1, 1))
        state = self.thread(IVAN)
        self.assertEqual(state['turns'], [])
        self.assertEqual(state['slots'], {})
        threads = self.store.threads(IVAN)
        self.assertIn('Unabhängig: wie warm ist es?', [t['first'] for t in threads])
        with self.assertRaises(c.StaleTurn):                   # cannot recreate it
            self.say(IVAN, 'Und zurück?', expected=late)
        self.assertEqual(self.store.forget_matching(IVAN, 'Leipzig'), (0, 0))
        self.assertIn('nichts gefunden', c.reply('forget_matching', (0, 0)))
        self.assertIn('nicht nach jeder Umschreibung', c.reply('forget_matching', (1, 1)))

    def test_new_thread_archives_forget_deletes_and_three_threads_at_most(self):
        self.store.ensure(IVAN)
        for topic in ('Alpha', 'Beta', 'Gamma', 'Delta'):
            self.say(IVAN, f'Thema {topic}')
            result = self.store.new_thread(IVAN)
        self.assertEqual(result, 'dropped_oldest')
        firsts = [t['first'] for t in self.store.threads(IVAN)]
        self.assertEqual(len(firsts), c.MAX_THREADS)
        self.assertNotIn('Thema Alpha', firsts)
        self.assertEqual(self.store.new_thread(IVAN), 'empty')
        self.assertEqual(self.store.forget_all(IVAN), 2)
        self.assertEqual([t['turns'] for t in self.store.threads(IVAN)], [0])


class ArchiveTests(Rig):
    def test_name_list_open_and_switch_back(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'Wir planen die Reise.')
        self.assertEqual(self.store.name(IVAN, 'Reiseplanung.'), 'Reiseplanung')
        self.store.new_thread(IVAN)
        self.say(IVAN, 'Rezept für Kuchen')
        self.assertEqual(self.store.name(IVAN, 'reiseplanung'), 'duplicate')
        self.store.name(IVAN, 'Kuchen')
        late_b, _ = self.store.prepare(IVAN, 'Mehr Zucker?')
        info = self.store.resume(IVAN, 'Reiseplanung')          # A -> B -> A
        self.assertEqual(info['first'], 'Wir planen die Reise.')
        with self.assertRaises(c.StaleTurn):
            self.say(IVAN, 'Mehr Zucker?', expected=late_b)
        self.assertEqual(self.store.resume(IVAN, 'Urlaub'), None)
        self.assertEqual(self.store.titles(IVAN), ['Kuchen', 'Reiseplanung'])  # newest first
        self.assertIn('Reiseplanung', c.reply('list_threads', self.store.threads(IVAN),
                                              self.now))

    def test_partial_title_must_be_unambiguous(self):
        self.store.ensure(IVAN)
        self.say(IVAN, 'a')
        self.store.name(IVAN, 'Reise Hamburg')
        self.store.new_thread(IVAN)
        self.say(IVAN, 'b')
        self.store.name(IVAN, 'Reise Berlin')
        self.assertEqual(self.store.resume(IVAN, 'reise'), 'ambiguous')
        self.assertEqual(self.store.resume(IVAN, 'hamburg')['title'], 'Reise Hamburg')


class MigrationTests(Rig):
    def test_old_history_moves_once_to_the_unambiguous_owner(self):
        self.core.remember_turn('wie hoch ist der eiffelturm', '330 Meter', persona='mensch')
        self.assertTrue(self.store.ensure(c.DEVICE_OPERATOR, legacy=True))
        turns = self.thread(c.DEVICE_OPERATOR)['turns']
        self.assertEqual([(t['q'], t['p'], t['delivery']) for t in turns],
                         [('wie hoch ist der eiffelturm', 'mensch', 'pending')])
        self.assertEqual(self.core.load()['history'], [])
        self.assertFalse(self.store.ensure(c.DEVICE_OPERATOR, legacy=True))   # once

    def test_legacy_owner_and_stable_profile_ids(self):
        self.assertEqual(c.legacy_owner([]), c.DEVICE_OPERATOR)
        self.assertEqual(c.legacy_owner([IVAN]), IVAN)
        self.assertIsNone(c.legacy_owner([IVAN, ANNA]))         # never copy into several
        self.core.save_voiceprint('Ivan', 'AAAA', 3)
        first = self.core.profile_ids()['Ivan']
        self.core.save_voiceprint('Ivan', 'AAAA', 2)             # refining keeps the id
        self.assertEqual(self.core.profile_ids(), {'Ivan': first})


class CommandTests(unittest.TestCase):
    def test_phrases(self):
        cases = {
            'Neues Gespräch': ('new_thread', ''),
            'lass uns ein neues thema anfangen': ('new_thread', ''),
            'Vergiss dieses Gespräch': ('forget_thread', ''),
            'lösche diesen Gesprächsverlauf': ('forget_thread', ''),
            'Vergiss alle unsere Gespräche': ('forget_all', ''),
            'Setze unser Gespräch fort': ('resume', ''),
            'Was war unser Thema?': ('topic', ''),
            'Welche Gespräche hast du gespeichert?': ('list_threads', ''),
            'Nenne dieses Gespräch Reiseplanung': ('name_thread', 'reiseplanung'),
            'Speichere das als Einkauf': ('name_thread', 'einkauf'),
            'Öffne das Gespräch Reiseplanung': ('open_thread', 'reiseplanung'),
        }
        for text, expected in cases.items():
            self.assertEqual(c.command(text), expected, text)

    def test_device_and_memory_commands_stay_untouched(self):
        self.assertIsNone(c.command('Öffne das Fenster'))
        self.assertEqual(c.command('Öffne Reiseplanung', titles=['Reiseplanung']),
                         ('open_thread', 'reiseplanung'))
        self.assertIsNone(c.command('Öffne Reiseplanung'))
        for text in ('Vergiss Hamburg', 'Merk dir dass ich Tee mag', 'Was ist das Wetter'):
            self.assertIsNone(c.command(text), text)
        self.assertEqual(memory.command('vergiss hamburg'), ('forget', 'hamburg'))


if __name__ == '__main__':
    unittest.main()
