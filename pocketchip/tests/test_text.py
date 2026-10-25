from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import threading
import unittest
from unittest.mock import patch
import urllib.error

from proximus_os.text_server import Server, make_backend
from proximus_os.transport import ClientConfig, TextClient, json_request

TOKEN = 'test-only-' + 'a' * 54


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, method='POST', path='/v1/text', body=b'{"text":"Hallo"}', token=TOKEN,
            headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    fields = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    fields.update(headers or {})
    try:
        conn.request(method, path, body=body, headers=fields)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


class TextProtocolTests(unittest.TestCase):
    def test_real_http_roundtrip_is_text_only(self):
        calls = []
        server = Server(('127.0.0.1', 0), TOKEN, lambda text: calls.append(text) or 'Antwort als Text.')
        with serving(server) as url:
            client = TextClient(ClientConfig(url, TOKEN))
            self.assertEqual(client.ask('  Hallo  '), 'Antwort als Text.')
            self.assertIn('erreichbar', client.status())
            self.assertEqual(calls, ['Hallo'])
            self.assertEqual(request(server)[1], {'mode': 'text', 'reply': 'Antwort als Text.'})
            self.assertEqual(request(server, 'GET', '/health', body=None, token='')[0], 200)

    def test_authentication_precedes_backend_and_private_status(self):
        calls = []
        server = Server(('127.0.0.1', 0), TOKEN, lambda text: calls.append(text))
        with serving(server):
            self.assertEqual(request(server, token='wrong')[0], 401)
            self.assertEqual(request(server, 'GET', '/v1/status', body=None, token='wrong')[0], 401)
            self.assertEqual(calls, [])
            self.assertEqual(request(server, path='/v1/ask')[0], 404)

    def test_bad_inputs_never_invoke_model(self):
        calls = []
        server = Server(('127.0.0.1', 0), TOKEN, lambda text: calls.append(text))
        with serving(server):
            for body in (b'[]', b'{', b'{"text":4}', b'{"text":""}', b'\xff',
                         b'{"text":"Hallo", "audio":true}', json.dumps({'text': 'x' * 2001}).encode()):
                self.assertEqual(request(server, body=body)[0], 400)
            self.assertEqual(request(server, body=b'x' * 8193)[0], 413)
            self.assertEqual(request(server, headers={'Content-Type': 'text/plain'})[0], 400)
            self.assertEqual(request(server, headers={'Transfer-Encoding': 'chunked'})[0], 400)
            self.assertEqual(calls, [])

    def test_backend_failure_is_redacted_and_does_not_fabricate_reply(self):
        def fail(text):
            raise RuntimeError('secret-token private-backend-stack')
        server = Server(('127.0.0.1', 0), TOKEN, fail)
        with serving(server):
            code, data = request(server)
            self.assertEqual(code, 502)
            self.assertNotIn('secret', json.dumps(data))
            self.assertNotIn('reply', data)

    def test_busy_backend_returns_503_without_second_inference(self):
        started, release = threading.Event(), threading.Event()
        calls = []
        def block(text):
            calls.append(text)
            started.set()
            release.wait(2)
            return 'fertig'
        server = Server(('127.0.0.1', 0), TOKEN, block)
        with serving(server) as url:
            first = threading.Thread(target=lambda: TextClient(ClientConfig(url, TOKEN)).ask('erste'))
            first.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(request(server)[0], 503)
                self.assertEqual(calls, ['erste'])
            finally:
                release.set()
                first.join(3)

    def test_invalid_or_audio_legacy_response_is_rejected(self):
        client = TextClient(ClientConfig('http://127.0.0.1', TOKEN))
        for result in ({'reply': 'old', 'audio': 'abc'}, {'mode': 'text', 'reply': 3},
                       {'mode': 'text', 'reply': 'x', 'audio': 'abc'}, {'mode': 'text', 'reply': ''}):
            with patch('proximus_os.transport.json_request', return_value=result):
                with self.assertRaises(ValueError):
                    client.ask('Hallo')

    def test_backend_payload_is_text_without_speech_pipeline(self):
        with patch('proximus_os.text_server.json_request', return_value={
                'choices': [{'message': {'content': 'Antwort'}}]}) as call:
            self.assertEqual(make_backend('http://127.0.0.1:8766', 'local')('Frage'), 'Antwort')
            self.assertTrue(call.call_args.args[0].endswith('/v1/chat/completions'))
            payload = call.call_args.kwargs['payload']
            self.assertEqual(set(payload), {'model', 'messages', 'max_tokens', 'temperature', 'stream'})
            self.assertIn('PocketCHIP', payload['messages'][0]['content'])
            self.assertNotIn('synthesize', payload['messages'][0]['content'])

    def test_redirect_does_not_forward_token(self):
        received = []
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                received.append(self.path)
                self.send_response(302)
                self.send_header('Location', '/stolen')
                self.end_headers()
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Redirect)) as url:
            with self.assertRaises(ValueError):
                json_request(url + '/v1/status', TOKEN)
        self.assertEqual(received, ['/v1/status'])

    def test_server_response_limit(self):
        class Large(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'x' * 32769)
        with serving(ThreadingHTTPServer(('127.0.0.1', 0), Large)) as url:
            with self.assertRaises(ValueError):
                json_request(url)


if __name__ == '__main__':
    unittest.main()
