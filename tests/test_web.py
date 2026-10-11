"""Integration checks: browser gateway must use the same server answer path."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'web'))
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'src'))
import gateway  # noqa: E402
import servitor_server as ss  # noqa: E402
from test_servitor_server import FakePipeline, TOKEN  # noqa: E402


class WebGatewayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = ss.Config({'SERVITOR_API_TOKEN': TOKEN, 'SERVITOR_BIND': '127.0.0.1',
                            'SERVITOR_PORT': '0', 'SERVITOR_WORKDIR': self.tmp.name})
        self.backend = ss.Server(('127.0.0.1', 0), ss.Handler)
        self.backend.service = ss.Service(config, FakePipeline(self.tmp.name))
        self.backend.service.ready = True
        self.back_thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.back_thread.start()
        self.web = gateway.make_server({
            'SERVITOR_API_TOKEN': TOKEN,
            'PROXIMUS_WEB_PORT': '0',
            'PROXIMUS_UPSTREAM_PORT': str(self.backend.server_address[1]),
        })
        self.web_thread = threading.Thread(target=self.web.serve_forever, daemon=True)
        self.web_thread.start()

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        self.backend.shutdown()
        self.backend.server_close()
        self.tmp.cleanup()

    def call(self, path, payload=None, host='localhost', origin='http://localhost'):
        conn = http.client.HTTPConnection('127.0.0.1', self.web.server_address[1], timeout=10)
        headers = {'Host': host}
        if payload is not None:
            headers.update({'Content-Type': 'application/json', 'Origin': origin})
        conn.request('POST' if payload is not None else 'GET', path,
                     body=json.dumps(payload).encode() if payload is not None else None,
                     headers=headers)
        response = conn.getresponse()
        status = response.status
        body = response.read()
        conn.close()
        return status, body

    def test_web_chat_runs_shared_intent_and_audio(self):
        status, raw = self.call('/api/chat', {'text': 'Wer bist du?'})
        self.assertEqual(status, 200)
        events = [json.loads(line) for line in raw.decode().splitlines() if line]
        self.assertEqual(next(e['model'] for e in events if e['event'] == 'reply'),
                         'local/intent')
        self.assertEqual(self.backend.service.pipeline.accepted, 0)
        self.assertTrue(any(e['event'] == 'audio' for e in events))

    def test_gateway_hides_token_and_reports_health(self):
        status, html = self.call('/')
        self.assertEqual(status, 200)
        self.assertIn(b'PROXIMUS', html)
        self.assertNotIn(TOKEN.encode(), html)
        status, raw = self.call('/api/health')
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)['ready'])

    def test_unknown_host_and_cross_origin_are_rejected(self):
        self.assertEqual(self.call('/', host='evil.example')[0], 403)
        self.assertEqual(self.call('/api/chat', {'text': 'Hallo'},
                                   origin='https://evil.example')[0], 403)

    def test_web_must_have_server_secret(self):
        with self.assertRaises(ValueError):
            gateway.make_server({'SERVITOR_API_TOKEN': 'short'})


if __name__ == '__main__':
    unittest.main()
