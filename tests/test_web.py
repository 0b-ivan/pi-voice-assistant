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

    def call(self, path, payload=None, host='localhost', origin='http://localhost',
             content_type=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.web.server_address[1], timeout=10)
        headers = {'Host': host}
        if payload is not None:
            is_binary = isinstance(payload, bytes)
            headers['Content-Type'] = content_type or (
                'application/octet-stream' if is_binary else 'application/json')
            if origin is not None:
                headers['Origin'] = origin
        else:
            is_binary = False
        data = payload if is_binary else json.dumps(payload).encode() if payload is not None else None
        conn.request('POST' if payload is not None else 'GET', path, body=data, headers=headers)
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

    @staticmethod
    def parse_events(data):
        return [json.loads(line) for line in data.decode().splitlines() if line]

    def test_voice_uses_existing_recognition_and_same_answer_as_text(self):
        # Audio is already resampled to 16 kHz mono PCM16 by the web client.
        audio = b'\x01\x00' * 16000
        status, raw = self.call('/api/voice', audio)
        self.assertEqual(status, 200)
        voice_events = self.parse_events(raw)
        self.assertEqual(self.backend.service.pipeline.accepted, len(audio))
        self.assertEqual(next(e['text'] for e in voice_events if e['event'] == 'transcript'),
                         self.backend.service.pipeline.transcript)
        status, raw = self.call('/api/chat',
                                {'text': self.backend.service.pipeline.transcript})
        self.assertEqual(status, 200)
        typed_events = self.parse_events(raw)
        for event_type in ('reply', 'audio', 'done'):
            self.assertTrue(any(e['event'] == event_type for e in voice_events))
            self.assertTrue(any(e['event'] == event_type for e in typed_events))
        self.assertEqual(next(e['text'] for e in typed_events if e['event'] == 'reply'),
                         next(e['text'] for e in voice_events if e['event'] == 'reply'))
        self.assertEqual(next(e['model'] for e in typed_events if e['event'] == 'reply'),
                         next(e['model'] for e in voice_events if e['event'] == 'reply'))

    def test_voice_size_type_and_origin_are_checked(self):
        audio = b'\x01\x00' * 16000
        self.assertEqual(self.call('/api/voice', audio, origin='https://evil.example')[0], 403)
        self.assertEqual(self.call('/api/voice', audio, origin=None)[0], 403)
        self.assertEqual(self.call('/api/voice', audio, content_type='text/plain')[0], 415)
        self.assertEqual(self.call('/api/voice', b'\x01\x00' * 100)[0], 400)
        self.assertEqual(self.call('/api/voice', audio + b'\x01')[0], 400)
        self.assertEqual(self.call('/api/chat', {'text': 'Hallo'}, origin=None)[0], 403)

    def test_audio_worklet_is_served_without_exposing_credentials(self):
        status, js = self.call('/recorder-worklet.js')
        self.assertEqual(status, 200)
        self.assertIn(b'registerProcessor', js)
        self.assertNotIn(TOKEN.encode(), js)
        self.assertNotIn(b'OPENROUTER_API_KEY', js)

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
