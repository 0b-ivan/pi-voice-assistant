import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import logwatch
import web_context
import web_diagnostics


def entry(at, unit, message, priority=3):
    return logwatch.parse_entry(json.dumps({
        '_SYSTEMD_UNIT': unit, 'MESSAGE': message, 'PRIORITY': str(priority),
        '__REALTIME_TIMESTAMP': str(int(at * 1000000)),
    }))


class WebConversationTests(unittest.TestCase):
    def setUp(self):
        self.now = [10000.0]
        self.chats = web_context.WebConversations(clock=lambda: self.now[0])
        self.alice = 'cookie:' + 'a' * 64
        self.bob = 'cookie:' + 'b' * 64
        self.a = self.chats.create(self.alice)
        self.b = self.chats.create(self.bob)

    def test_context_follows_two_turns_and_is_isolated(self):
        self.chats.append(self.a, self.alice, 'Ich suche das Protokoll', 'Prüfauftrag läuft')
        self.chats.append(self.a, self.alice, 'Welche Prüfung?', 'Der Diagnoselauf')
        self.assertEqual(len(self.chats.history(self.a, self.alice, 'Ist es fertig?')), 2)
        self.assertEqual(self.chats.history(self.b, self.bob, 'Ist es fertig?'), [])
        with self.assertRaises(web_context.MissingChat):
            self.chats.history(self.a, self.bob, 'Protokoll')

    def test_parallel_singleflight_job_and_event_replay(self):
        job, created = self.chats.job_start(self.a, self.alice, 'letzte 2 Stunden')
        self.assertTrue(created)
        other, created = self.chats.job_start(self.a, self.alice, 'letzte 2 Stunden')
        self.assertFalse(created)
        self.assertEqual(job['id'], other['id'])
        self.chats.job_update(self.a, self.alice, job['id'], 'läuft', 'diagnose_running')
        self.chats.job_update(self.a, self.alice, job['id'], 'abgeschlossen',
                              'diagnose_done', text='Beleg')
        self.assertFalse(self.chats.job_update(
            self.a, self.alice, job['id'], 'abgeschlossen', 'diagnose_done', text='Doppelt'))
        first = self.chats.events(self.a, self.alice, 0)
        self.assertEqual(sum(e['stage'] == 'diagnose_done' for e in first['events']), 1)
        self.assertEqual(self.chats.events(self.a, self.alice, first['seq'])['events'], [])
        with self.assertRaises(web_context.MissingChat):
            self.chats.events(self.a, self.bob)

    def test_two_threads_get_distinct_jobs(self):
        a, _ = self.chats.job_start(self.a, self.alice, '2h')
        b, _ = self.chats.job_start(self.b, self.bob, '2h')
        self.assertNotEqual(a['id'], b['id'])
        self.assertNotEqual(self.chats.latest(self.a, self.alice)['id'],
                            self.chats.latest(self.b, self.bob)['id'])

    def test_forget_logout_expiry_and_restart_are_explicit(self):
        job, _ = self.chats.job_start(self.a, self.alice, '2h')
        self.chats.cancel(self.a, self.alice)
        self.assertEqual(self.chats.latest(self.a, self.alice)['status'], 'abgebrochen')
        self.chats.close_principal(self.alice)
        with self.assertRaises(web_context.MissingChat):
            self.chats.latest(self.a, self.alice)
        with self.assertRaises(web_context.MissingChat):
            web_context.WebConversations().events(self.b, self.bob)
        self.now[0] += web_context.CHAT_TTL + 1
        with self.assertRaises(web_context.MissingChat):
            self.chats.events(self.b, self.bob)

    def test_concurrent_start_deduplicates(self):
        ids = []
        barrier = threading.Barrier(8)
        def start():
            barrier.wait()
            ids.append(self.chats.job_start(self.a, self.alice, '2h')[0]['id'])
        threads = [threading.Thread(target=start) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(set(ids)), 1)


class WebDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.now = 12000.0
        self.c = dict(read=0, discarded=0, since=4800, until=12000,
                      limited=False, error=None)

    def test_fresh_six_errors_have_times_service_and_unknown_recovery(self):
        items = [entry(11900 + i, 'servitor-voice.service',
                       json.dumps({'event': 'turn_error', 'code': 'llm'}))
                 for i in range(6)]
        self.c['read'] = len(items)
        result = web_diagnostics.analyse(items, self.c)
        finding = next(item for item in result['findings'] if item['code'] == 'turn')
        self.assertEqual(finding['count'], 6)
        self.assertEqual(finding['units'], ['servitor-voice.service'])
        self.assertEqual(finding['first'], 11900)
        self.assertEqual(finding['last'], 11905)
        self.assertEqual(finding['recovery'], 'nicht belegt')
        self.assertIn('nicht bewiesen', web_diagnostics.describe(result).lower())

    def test_bad_log_cannot_exfiltrate_secrets(self):
        raw = 'Authorization: Bearer private-token user@example.com 10.1.2.3'
        self.assertEqual(web_diagnostics.evidence(entry(
            11950, 'servitor-llm.service', raw)), 'Freitext nicht freigegeben')
        item = entry(11950, 'servitor-llm.service',
                     json.dumps({'event': 'turn_error', 'stage': 'think',
                                 'code': 'llm', 'text': raw}))
        self.assertEqual(web_diagnostics.evidence(item),
                         'event=turn_error, stage=think, code=llm')
        self.assertNotIn('private-token', web_diagnostics.evidence(item))

    def test_command_is_fixed_and_foreign_service_discarded(self):
        captured = []
        payloads = [
            dict(_SYSTEMD_UNIT='servitor-voice.service', MESSAGE='ready',
                 PRIORITY='6', __REALTIME_TIMESTAMP='11900000000'),
            dict(_SYSTEMD_UNIT='postgresql.service', MESSAGE='access denied',
                 PRIORITY='3', __REALTIME_TIMESTAMP='11901000000')]
        def fake(command):
            captured.append(command)
            return ('\n'.join(json.dumps(p) for p in payloads)).encode(), 0, False, False
        rows, coverage = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(len(rows), 1)
        self.assertEqual(coverage['discarded'], 1)
        self.assertEqual(captured[0].count('-u'), 2)
        self.assertNotIn('postgresql.service', captured[0])
        self.assertNotIn('sh', captured[0])
        self.assertFalse(coverage['limited'])

    def test_missing_permissions_and_truncated_output_not_healthy(self):
        cases = [(b'', 1, False, True), (b'', -9, True, False)]
        for raw, rc, cut, warn in cases:
            rows, coverage = web_diagnostics.read(
                now=self.now, capture=lambda cmd: (raw, rc, cut, warn))
            self.assertEqual(rows, [])
            self.assertTrue(coverage['limited'])
            self.assertIn('unvollständig', web_diagnostics.describe(
                web_diagnostics.analyse(rows, coverage)))

    def test_previous_snapshot_is_never_used(self):
        def fake(command):
            return b'', 0, False, False
        rows, meta = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(rows, [])
        self.assertIn('Keine Protokolle', web_diagnostics.describe(
            web_diagnostics.analyse(rows, meta)))


if __name__ == '__main__':
    unittest.main()
