import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import servitor_server as ss
import nightwatch as nw
import nightwatch_server as ns
import memory
from test_servitor_server import FakePipeline, TOKEN
from test_nightwatch import report


INVENTORY = dict(system='pi', distro='debian', release='trixie', packages=[], gaps=[])


class BackgroundFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config(dict(SERVITOR_API_TOKEN=TOKEN, SERVITOR_WORKDIR=self.tmp.name,
                                SERVITOR_TRUSTED_PROXIES=''))
        self.pipeline = FakePipeline(self.tmp.name)
        self.pipeline.reply = Mock(side_effect=AssertionError('normal/cloud LLM must never be used'))
        self.service = ss.Service(config, self.pipeline)
        self.service.ready = True
        audit = Mock()
        audit.scan.return_value = [], [], []
        self.chat = Mock(return_value='391')
        self.manager = ns.Manager(self.service, Path(self.tmp.name) / 'night',
                                  audit_factory=lambda *a: audit, chat=self.chat)
        self.service.nightwatch = self.manager
        self.manager.last_activity -= 61

    def start(self, **extra):
        body = dict(id='a' * 32, night='2026-10-11', inventory=INVENTORY, snapshot={})
        body.update(extra)
        with patch.object(ns, 'inventory', return_value=INVENTORY):
            self.manager.start(body)
            self.manager.thread.join(3)
        self.assertFalse(self.manager.thread.is_alive())

class BackgroundTests(BackgroundFixture):
    def test_recovered_error_is_not_an_open_operation(self):
        now = time.time()
        snapshot = dict(log_findings={'stt': 4, 'oom': 1},
                        log_last={'stt': now - 100, 'oom': now - 100},
                        log_resolved={'stt': now - 50})
        items = ns.operations(snapshot, 'pi')
        self.assertEqual([item['key'] for item in items], ['oom'])
        self.assertIn('kleineres Modell', items[0]['advice'])

    def test_stale_or_missing_log_time_cannot_close_old_issues(self):
        now = time.time()
        self.assertFalse(ns.log_coverage(dict(log_findings={}), 'pi', now)[0])
        self.assertFalse(ns.log_coverage(dict(log_findings={}, log_checked_at=now - 3601), 'pi', now)[0])
        self.assertFalse(ns.log_coverage(dict(log_findings={}, log_checked_at=now + 121), 'pi', now)[0])
        self.assertTrue(ns.log_coverage(dict(log_findings={}, log_checked_at=now - 60), 'pi', now)[0])
        self.start(snapshot=dict(log_findings={}, log_checked_at=now - 7200))
        self.assertNotIn('operation:pi:log', self.manager.report['checked'])

    def test_new_copy_of_old_inventory_is_still_rejected(self):
        path = Path(self.tmp.name) / 'inventory.json'
        now = time.time()
        data = dict(INVENTORY, system='proxmox', at=now - 86401)
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ns.extra_inventory(path, now)
        data['at'] = now - 60
        path.write_text(json.dumps(data))
        self.assertEqual(ns.extra_inventory(path, now)['system'], 'proxmox')
        data['at'] = now + 121
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ns.extra_inventory(path, now)
        data.update(at=now - 60, system=None)
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            ns.extra_inventory(path, now)

    def test_background_uses_only_local_model_and_retains_unacknowledged_report(self):
        self.start()
        self.pipeline.reply.assert_not_called()
        self.chat.assert_called_once()
        state = self.manager.status()
        self.assertEqual(state['report']['id'], 'a' * 32)
        self.assertTrue((self.manager.root / 'pending.json').exists())
        recovered = ns.Manager(self.service, self.manager.root)
        self.assertEqual(recovered.status()['report']['id'], 'a' * 32)
        self.assertEqual(self.pipeline.files, [p for p in self.pipeline.files if not p.exists()])
        with self.assertRaises(RuntimeError):
            self.manager.start(dict(id='b' * 32, night='2026-10-11', inventory=INVENTORY))
        self.manager.ack('a' * 32)
        self.assertFalse((self.manager.root / 'pending.json').exists())

    def test_cancelled_inference_yields_foreground_and_is_not_claimed_complete(self):
        entered = threading.Event()
        def chat(prompt, guard, **kwargs):
            entered.set()
            guard.stop.wait(2)
            guard.check()
        self.manager.chat = chat
        with patch.object(ns, 'inventory', return_value=INVENTORY):
            self.manager.start(dict(id='a' * 32, night='2026-10-11', inventory=INVENTORY))
            self.assertTrue(entered.wait(2))
            self.manager.foreground()
            self.manager.thread.join(2)
        self.assertFalse(self.service.turn_lock.locked())
        self.assertEqual(self.manager.status()['report']['state'], 'cancelled')
        self.pipeline.reply.assert_not_called()

    def test_missing_local_model_does_not_fall_back_to_cloud(self):
        self.manager.chat = Mock(side_effect=OSError('local unavailable'))
        self.start()
        self.pipeline.reply.assert_not_called()
        self.assertTrue(any('Lokales Modell' in s for s in self.manager.report['limits']))
        self.assertNotEqual(self.manager.report['state'], 'done')

    def test_audio_cleanup_failure_still_releases_voice_lock(self):
        self.manager.guard = ns.Guard()
        limits = []
        with patch.object(Path, 'unlink', side_effect=OSError('readonly')):
            self.manager._pipeline_tests('', [], limits)
        self.assertFalse(self.service.turn_lock.locked())
        self.assertTrue(any('entfernt' in s for s in limits))

    def test_limited_logs_do_not_resolve_old_log_findings(self):
        self.service.logwatch = Mock()
        self.service.logwatch.snapshot_fields.return_value = dict(server_log_findings={'limited': 1})
        self.start(snapshot=dict(log_findings={'limited': 1}))
        self.assertNotIn('operation:pi:log', self.manager.report['checked'])
        self.assertNotIn('operation:server:log', self.manager.report['checked'])
        self.assertTrue(any('protokoll' in s for s in self.manager.report['limits']))

    def test_normal_request_never_puts_night_report_into_model_prompt(self):
        self.pipeline.reply = Mock(return_value=('Normale Antwort.', 'test'))
        view = dict(open=1, findings=[nw.finding('security:pi:debian', 'cve',
                    'Privater Sicherheitsbefund', 'Abhilfe prüfen.', 'security', 1)],
                    last=dict(state='done', night='2026-10-11', limits=[]))
        self.pipeline.transcript = 'erzähl mir etwas'
        self.service.run_turn([bytes(16000)], lambda e: None, 'wav', nightwatch_copy=view)
        self.assertNotIn('Privater Sicherheitsbefund', str(self.pipeline.reply.call_args))
        self.pipeline.reply.reset_mock()
        events = []
        self.pipeline.transcript = 'was ist offen'
        self.service.run_turn([bytes(16000)], events.append, 'wav', nightwatch_copy=view)
        self.assertIn('Privater Sicherheitsbefund', next(e['text'] for e in events if e['event'] == 'reply'))
        self.pipeline.reply.assert_not_called()

    def test_guest_cannot_read_night_report(self):
        view = dict(open=1, findings=[nw.finding('security:pi:debian', 'cve',
                    'Privater Sicherheitsbefund', 'Abhilfe prüfen.', 'security', 1)],
                    last=dict(state='done', night='2026-10-11', limits=[]))
        events = []
        self.pipeline.transcript = 'was ist offen'
        self.service.run_turn([bytes(16000)], events.append, 'wav', nightwatch_copy=view,
                              memory_copy=memory.guest_view(dict(facts=[], directives=[], history=[])))
        text = next(e['text'] for e in events if e['event'] == 'reply')
        self.assertNotIn('Privater Sicherheitsbefund', text)
        self.assertIn('nicht als Bediener', text)


class EndpointTests(BackgroundFixture):
    def test_auth_proxy_rejection_and_request_validation(self):
        server = ss.Server(('127.0.0.1', 0), ss.Handler)
        server.service = self.service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(body=None, token=TOKEN, length=None):
                conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                headers = {'Authorization': 'Bearer ' + token} if token else {}
                if length is not None:
                    headers['Content-Length'] = str(length)
                conn.request('POST' if body is not None else 'GET', '/v1/nightwatch', body=body, headers=headers)
                response = conn.getresponse()
                response.read()
                status = response.status
                conn.close()
                return status
            self.assertEqual(request(token=None), 401)
            self.service.config.trusted_proxies = {'127.0.0.1'}
            self.assertEqual(request(), 403)
            self.service.config.trusted_proxies.clear()
            self.assertEqual(request(), 200)
            self.assertEqual(request(b'[]'), 400)
            self.assertEqual(request(json.dumps({'action': 'shell', 'command': 'anything'})), 400)
            # Server rejects the length before reading the upload; sending
            # the oversized body can race its close and cause a broken pipe.
            self.assertEqual(request(b'', length=nw.MAX_REPORT + 1), 413)
            body = json.dumps(dict(action='start', id='a' * 32, night='2026-10-11', inventory=INVENTORY))
            with patch.object(ns, 'inventory', return_value=INVENTORY):
                self.assertEqual(request(body), 202)
                self.manager.thread.join(3)
            self.assertEqual(request(json.dumps(dict(action='ack', id='b' * 32))), 400)
            self.assertEqual(request(json.dumps(dict(action='ack', id='a' * 32))), 200)
        finally:
            server.shutdown()
            server.server_close()


class LocalTransportTests(unittest.TestCase):
    def test_loopback_stream_has_no_cloud_credentials(self):
        captured = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                captured['headers'] = dict(self.headers)
                captured['body'] = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"391"}}]}\n\ndata: [DONE]\n')
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'must-not-be-sent'}):
                result = ns.local_chat('17 mal 23', ns.Guard(),
                    url=f'http://127.0.0.1:{server.server_port}/v1/chat/completions')
            self.assertEqual(result, '391')
            self.assertNotIn('Authorization', captured['headers'])
            self.assertTrue(captured['body']['stream'])
            for url in ('https://openrouter.ai/api/v1/chat/completions',
                        'http://localhost:8766/v1/chat/completions',
                        'http://127.0.0.1:8766/v1/chat/completions?redirect=anything'):
                with self.assertRaises(ValueError):
                    ns.local_chat('test', ns.Guard(), url=url)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
