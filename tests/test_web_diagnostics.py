import datetime
import json
import socket
import sys
import threading
import unittest
import zoneinfo
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

    def test_receipt_is_confirmed_once_and_scoped(self):
        job, _ = self.chats.job_start(self.a, self.alice, '2h')
        self.assertFalse(self.chats.acknowledge(self.a, self.alice, job['id']))
        self.chats.job_update(self.a, self.alice, job['id'], 'abgeschlossen',
                              'diagnose_done', text='Fertig')
        self.assertTrue(self.chats.acknowledge(self.a, self.alice, job['id']))
        self.assertFalse(self.chats.acknowledge(self.a, self.alice, job['id']))
        with self.assertRaises(web_context.MissingChat):
            self.chats.acknowledge(self.a, self.bob, job['id'])
        self.assertEqual(sum(e['stage'] == 'diagnose_delivered'
                             for e in self.chats.events(self.a, self.alice)['events']), 1)

    def test_lookup_by_id_and_prefix_is_scoped_and_unambiguous(self):
        job, _ = self.chats.job_start(self.a, self.alice, '2h')
        self.assertEqual(self.chats.job(self.a, self.alice, job['id'])['id'], job['id'])
        self.assertEqual(self.chats.job(self.a, self.alice, job['id'][:8])['id'],
                         job['id'])
        self.assertIsNone(self.chats.job(self.a, self.alice, '0' * 24))
        with self.assertRaises(web_context.MissingChat):
            self.chats.job(self.a, self.bob, job['id'])
        self.assertIsNone(self.chats.job(self.b, self.bob, job['id']))

    def test_different_diagnostic_periods_have_distinct_jobs(self):
        current, _ = self.chats.job_start(self.a, self.alice, 'letzte 2 Stunden')
        historical, created = self.chats.job_start(self.a, self.alice, 'gestern')
        self.assertTrue(created)
        self.assertNotEqual(current['id'], historical['id'])

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
            if '--system' in command:
                return b'', 0, False, False
            return ('\n'.join(json.dumps(p) for p in payloads)).encode(), 0, False, False
        rows, coverage = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(len(rows), 1)
        self.assertEqual(coverage['discarded'], 1)
        self.assertEqual(captured[0].count('-u'), 2)
        self.assertIn('--system', captured[1])
        self.assertIn('0..4', captured[1])
        self.assertNotIn('-u', captured[1])
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

    def test_system_kernel_and_other_service_errors_only_aggregate(self):
        host = socket.gethostname()
        secret = 'client@example.net Authorization: Bearer sensitive-api-key'
        own = [dict(_SYSTEMD_UNIT='servitor-voice.service',
                    MESSAGE=json.dumps({'event': 'ready'}),
                    PRIORITY='6', __REALTIME_TIMESTAMP='11900000000')]
        system = [
            dict(_HOSTNAME=host, _TRANSPORT='kernel', PRIORITY='3',
                 MESSAGE='invoked oom-killer: killed process 99',
                 __REALTIME_TIMESTAMP='11901000000'),
            dict(_HOSTNAME=host, _TRANSPORT='kernel', PRIORITY='3',
                 MESSAGE='EXT4-fs error on virtual volume',
                 __REALTIME_TIMESTAMP='11902000000'),
        ] + [
            dict(_HOSTNAME=host, _SYSTEMD_UNIT='unrelated-foreign.service',
                 PRIORITY='3', MESSAGE=secret + ' out of memory',
                 __REALTIME_TIMESTAMP=str((11910 + i) * 1000000))
            for i in range(5)
        ]
        def fake(command):
            rows = system if '--system' in command else own
            return '\n'.join(json.dumps(row) for row in rows).encode(), 0, False, False
        rows, meta = web_diagnostics.read(now=self.now, capture=fake)
        self.assertFalse(meta['limited'])
        self.assertEqual(meta['sources']['own']['read'], 1)
        self.assertEqual(meta['sources']['ct107_system']['read'], 7)
        report = web_diagnostics.analyse(rows, meta)
        groups = {f['code']: f for f in report['findings']}
        self.assertEqual(groups['oom']['count'], 1)
        self.assertEqual(groups['disk_io']['count'], 1)
        self.assertEqual(groups['errors']['count'], 5)
        self.assertEqual(groups['errors']['units'], ['CT 107 (Systemjournal)'])
        self.assertFalse(groups['errors']['evidence'])
        output = web_diagnostics.describe(report)
        serialized = json.dumps(report, ensure_ascii=False)
        for forbidden in ('unrelated-foreign.service', 'sensitive-api-key',
                          'client@example.net', 'virtual volume', 'killed process'):
            self.assertNotIn(forbidden, serialized)
            self.assertNotIn(forbidden, output)
        self.assertIn('Systemjournal', output)
        self.assertIn('Wiederherstellung nicht belegt', output)

    def test_remote_host_cannot_be_mistaken_for_ct107(self):
        system = [dict(_HOSTNAME='fremder-proxmox-host', _TRANSPORT='kernel',
                       PRIORITY='3', MESSAGE='invoked oom-killer',
                       __REALTIME_TIMESTAMP='11900000000')]
        def fake(command):
            rows = system if '--system' in command else []
            return json.dumps(rows[0]).encode() if rows else b'', 0, False, False
        rows, coverage = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(rows, [])
        self.assertTrue(coverage['limited'])
        self.assertEqual(coverage['sources']['ct107_system']['read'], 0)
        self.assertNotIn('Arbeitsspeicher erschöpft',
                         web_diagnostics.describe(web_diagnostics.analyse(rows, coverage)))

    def test_system_journal_permission_failure_is_not_all_clear(self):
        def fake(command):
            return (b'', 1, False, True) if '--system' in command else (
                json.dumps(dict(_SYSTEMD_UNIT='servitor-voice.service',
                                MESSAGE='ready', PRIORITY='6',
                                __REALTIME_TIMESTAMP='11900000000')).encode(),
                0, False, False)
        rows, coverage = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(len(rows), 1)
        self.assertTrue(coverage['limited'])
        self.assertIn('ct107_system', web_diagnostics.describe(
            web_diagnostics.analyse(rows, coverage)))
        # Analyse ist ein reiner Datenpfad, LogWatch.check() darf niemals laufen.
        with mock.patch.object(logwatch, 'request', side_effect=AssertionError('repair')), \
             mock.patch.object(logwatch, 'unit_failed', side_effect=AssertionError('repair')):
            web_diagnostics.analyse(rows, coverage)

    def test_yesterday_bounds_are_not_a_live_two_hour_snapshot(self):
        captured = []
        def fake(cmd):
            captured.append(cmd)
            return b'', 0, False, False
        since, until = 100000, 186400
        _, coverage = web_diagnostics.read(
            now=190000, since=since, until=until, capture=fake)
        self.assertEqual((coverage['since'], coverage['until']), (since, until))
        for command in captured:
            self.assertIn('--since=@100000', command)
            self.assertIn('--until=@186400', command)
        with self.assertRaises(ValueError):
            web_diagnostics.read(now=190000, since=10, until=190000,
                                 capture=fake)

    def test_previous_snapshot_is_never_used(self):
        def fake(command):
            return b'', 0, False, False
        rows, meta = web_diagnostics.read(now=self.now, capture=fake)
        self.assertEqual(rows, [])
        self.assertIn('Keine Protokolle', web_diagnostics.describe(
            web_diagnostics.analyse(rows, meta)))


# HTTP integration: real threaded handler and NDJSON, fake model/journal, no CT 107.
import http.client
import tempfile
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import servitor_server as ss
from test_servitor_server import FakePipeline, TOKEN


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config({'SERVITOR_API_TOKEN': TOKEN, 'SERVITOR_BIND': '127.0.0.1',
                            'SERVITOR_PORT': '0', 'SERVITOR_RATE_LIMIT_PER_MINUTE': '99',
                            'SERVITOR_WORKDIR': self.tmp.name})
        self.pipe = FakePipeline(self.tmp.name)
        self.service = ss.Service(config, self.pipe)
        self.service.ready = True
        self.server = ss.Server(('127.0.0.1', 0), ss.Handler)
        self.server.service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.browser = 'a' * 32

    def request(self, path, obj=None, method='POST', browser=None, chat=None):
        headers = {'Authorization': 'Bearer ' + TOKEN, 'X-Servitor-Client': 'web',
                   'X-Servitor-Browser': browser or self.browser}
        if chat:
            headers['X-Servitor-Conversation'] = chat
        body = b'' if obj is None else json.dumps(obj).encode()
        if obj is not None:
            headers['Content-Type'] = 'application/json'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        status, content = response.status, response.read()
        conn.close()
        return status, content

    def new(self, browser=None):
        status, body = self.request('/v1/web/conversation', browser=browser)
        self.assertEqual(status, 201)
        return json.loads(body)['id']

    def ask(self, chat, text, browser=None):
        status, body = self.request('/v1/ask?format=wav', dict(text=text),
                                    browser=browser, chat=chat)
        if status != 200:
            return status, json.loads(body)
        return status, [json.loads(line) for line in body.splitlines() if line]

    def await_job(self, chat, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = self.service.web_chats.latest(chat, 'token:' + self.browser)
            if job and job['status'] in web_context.FINAL:
                return job
            time.sleep(0.01)
        self.fail('Job did not finish')

    def test_web_history_is_in_model_but_never_pi_memory(self):
        chat = self.new()
        self.assertEqual(self.ask(chat, 'Wie heißt mein Protokoll?')[0], 200)
        status, events = self.ask(chat, 'Was habe ich gefragt?')
        self.assertEqual(status, 200)
        self.assertIn('history', self.pipe.memory)
        self.assertEqual(self.pipe.memory['speaker'], 'unknown')
        self.assertEqual(self.pipe.memory['history'][-1]['q'], 'Wie heißt mein Protokoll?')
        self.assertNotIn('facts', self.pipe.memory)

    def test_full_dialog_start_status_six_errors_and_one_delivery(self):
        chat = self.new()
        items = [entry(11900 + i, 'servitor-voice.service',
                       json.dumps({'event': 'turn_error', 'code': 'llm'}))
                 for i in range(6)]
        coverage = dict(read=6, discarded=0, since=4800, until=12000,
                        limited=False, error=None)
        with mock.patch.object(web_diagnostics, 'read', return_value=(items, coverage)):
            status, first = self.ask(chat, 'Selbsttest')
            self.assertEqual(status, 200)
            self.assertIn('Auftragskennung', next(e['text'] for e in first
                                                   if e['event'] == 'reply'))
            result = self.await_job(chat)
            self.assertEqual(result['status'], 'abgeschlossen')
            self.assertIn('6 Ereignisse', result['report']['summary'])
            status, followup = self.ask(chat, 'Wo ist das Protokoll?')
            self.assertEqual(status, 200)
            self.assertIn('6 Ereignisse', followup[1]['text'])
            status, followup = self.ask(chat, 'Was bedeuten die sechs Fehler?')
            self.assertEqual(status, 200)
            self.assertIn('nicht bewiesen', followup[1]['text'].lower())
            status, followup = self.ask(chat, 'Ist es inzwischen fertig?')
            self.assertEqual(status, 200)
            self.assertIn('Lesende Diagnose', followup[1]['text'])
            status, data = self.request('/v1/web/jobs?conversation=' + chat + '&after=0',
                                        method='GET')
            self.assertEqual(status, 200)
            events = json.loads(data)['events']
            self.assertEqual(sum(e['stage'] == 'diagnose_done' for e in events), 1)
            last = json.loads(data)['seq']
            status, data = self.request('/v1/web/jobs?conversation=' + chat
                                        + '&after=' + str(last), method='GET')
            self.assertEqual(json.loads(data)['events'], [])

    def test_actual_job_reference_is_not_hallucinated_by_llm(self):
        chat = self.new()
        principal = 'token:' + self.browser
        job, _ = self.service.web_chats.job_start(chat, principal, 'letzte 2 Stunden')
        self.service.web_chats.job_update(chat, principal, job['id'],
                                          'abgeschlossen', 'diagnose_done',
                                          report={'summary': 'Sechs belegte Fehler.'},
                                          text='Fertig')
        for suffix in (job['id'], job['id'][:8]):
            status, events = self.ask(chat, 'prüfe auftrag:' + suffix)
            self.assertEqual(status, 200)
            reply = next(e['text'] for e in events if e['event'] == 'reply')
            self.assertIn('Sechs belegte Fehler.', reply)
            self.assertIn(job['id'], reply)
        status, events = self.ask(chat, 'prüfe auftrag:' + 'f' * 24)
        self.assertEqual(status, 200)
        self.assertIn('in diesem Browsergespräch nicht', events[1]['text'])
        self.assertNotIn('Dienstaufzeichnungen nicht', events[1]['text'])
        status, _ = self.ask(chat, 'prüfe auftrag:' + job['id'], browser='b' * 32)
        self.assertEqual(status, 409)

    def test_yesterday_logs_and_conversation_are_separated(self):
        chat = self.new()
        local = datetime.datetime(2026, 10, 15, 19, 4,
                                  tzinfo=zoneinfo.ZoneInfo('Europe/Berlin'))
        yesterday = datetime.date(2026, 10, 14)
        start = datetime.datetime.combine(yesterday, datetime.time.min,
                                          tzinfo=local.tzinfo)
        stop = start + datetime.timedelta(days=1)
        coverage = dict(read=0, discarded=0, since=int(start.timestamp()),
                        until=int(stop.timestamp()), limited=False, error=None)
        def reader(**kwargs):
            self.assertEqual(kwargs, dict(since=int(start.timestamp()),
                                          until=int(stop.timestamp())))
            return [], coverage
        with (mock.patch.object(self.service, 'now', return_value=local),
              mock.patch.object(web_diagnostics, 'read', side_effect=reader) as read):
            status, events = self.ask(
                chat, 'was steht in denn Logs von gestern ? und worüber haben wir gesprochen ?')
            self.assertEqual(status, 200)
            reply = next(e['text'] for e in events if e['event'] == 'reply')
            self.assertIn('14.10.2026', reply)
            self.assertIn('Gesprächsinhalte', reply)
            job = self.await_job(chat)
            self.assertEqual(job['status'], 'abgeschlossen')
            self.assertEqual(read.call_count, 1)
        status, events = self.ask(chat, 'Was haben wir gestern gesprochen?')
        self.assertEqual(status, 200)
        reply = next(e['text'] for e in events if e['event'] == 'reply')
        self.assertIn('nicht feststellen', reply)
        self.assertNotIn('keine Gespräche', reply)

    def test_double_selftest_and_other_browser_cannot_read(self):
        chat = self.new()
        gate = threading.Event()
        coverage = dict(read=0, discarded=0, since=4800, until=12000,
                        limited=False, error=None)
        def slow():
            gate.wait(2)
            return [], coverage
        with mock.patch.object(web_diagnostics, 'read', side_effect=slow) as reader:
            self.ask(chat, 'Selbsttest')
            original = self.service.web_chats.latest(chat, 'token:' + self.browser)
            self.ask(chat, 'Selbsttest')
            repeated = self.service.web_chats.latest(chat, 'token:' + self.browser)
            self.assertEqual(original['id'], repeated['id'])
            self.assertEqual(reader.call_count, 1)
            status, _ = self.request('/v1/web/jobs?conversation=' + chat,
                                      method='GET', browser='b' * 32)
            self.assertEqual(status, 404)
            status, _ = self.ask(chat, 'Ist es fertig?', browser='b' * 32)
            self.assertEqual(status, 409)
            gate.set()
            self.await_job(chat)

    def test_browser_without_chat_cannot_trigger_selftest_repair(self):
        status, payload = self.ask('', 'Selbsttest')
        self.assertEqual(status, 409)
        self.assertIn('aktives Gespräch', payload['error'])

    def test_http_receipt_once_and_never_across_browser(self):
        chat = self.new()
        job, _ = self.service.web_chats.job_start(chat, 'token:' + self.browser, '2h')
        self.service.web_chats.job_update(
            chat, 'token:' + self.browser, job['id'], 'abgeschlossen',
            'diagnose_done', text='Fertig')
        args = dict(conversation=chat, job=job['id'])
        status, body = self.request('/v1/web/ack', args)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['newly_confirmed'])
        status, body = self.request('/v1/web/ack', args)
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['newly_confirmed'])
        status, body = self.request('/v1/web/ack', args, browser='b' * 32)
        self.assertEqual(status, 404)

    def test_browser_journal_redacts_structured_private_fields(self):
        journal = self.service.journal
        journal.add(json.dumps(dict(event='turn_error', code='llm',
                                    prompt='mein Passwort ist geheim',
                                    token='abc123', text='test@example.de')))
        visible = journal.since(0)['lines'][-1]['text']
        self.assertIn('turn_error', visible)
        self.assertNotIn('abc123', visible)
        self.assertNotIn('example.de', visible)
        self.assertNotIn('Passwort ist geheim', visible)

    def test_logout_cancels_and_forgets_web_conversation(self):
        chat = self.new()
        status, _ = self.request('/v1/logout')
        self.assertEqual(status, 200)
        status, _ = self.request('/v1/web/jobs?conversation=' + chat, method='GET')
        self.assertEqual(status, 404)
        another = self.new(browser='b' * 32)
        self.assertNotEqual(chat, another)


if __name__ == '__main__':
    unittest.main()
