import base64
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import unittest.mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'server'))
import servitor_server as ss  # noqa: E402

TOKEN = 'x' * 40


class FakeRecognizer:
    def __init__(self, owner):
        self.owner = owner

    def accept_pcm(self, pcm):
        assert len(pcm) % 2 == 0
        self.owner.accepted += len(pcm)

    def finish(self):
        if self.owner.transcript is None:
            raise RuntimeError('Vosk returned no transcript')
        return self.owner.transcript


class FakePipeline:
    def __init__(self, workdir):
        self.workdir = workdir
        self.accepted = 0
        self.transcript = 'wie spät ist es'
        self.fail_llm = False
        self.block = None
        self.files = []

    def recognizer(self):
        return FakeRecognizer(self)

    def reply(self, text):
        if self.fail_llm:
            raise RuntimeError('OpenRouter request failed: timeout')
        if self.block:
            self.block.wait(5)
        return f'Antwort auf {text}', 'test/model'

    def _wav(self, prefix, rate):
        fd, name = tempfile.mkstemp(prefix=prefix, suffix='.wav', dir=self.workdir)
        os.close(fd)
        path = Path(name)
        with wave.open(str(path), 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(bytes(rate // 2 * 2))
        self.files.append(path)
        return path

    def synthesize(self, text):
        return self._wav('syn-', 22050)

    def render(self, source):
        return self._wav('dsp-', 48000)

    def encode(self, source, fmt):
        return Path(source).read_bytes()


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = ss.Config({'SERVITOR_API_TOKEN': TOKEN, 'SERVITOR_BIND': '127.0.0.1',
                            'SERVITOR_PORT': '0', 'SERVITOR_MAX_AUDIO_SECONDS': '2',
                            'SERVITOR_RATE_LIMIT_PER_MINUTE': '50',
                            'SERVITOR_WORKDIR': self.tmp.name})
        self.pipeline = FakePipeline(self.tmp.name)
        self.service = ss.Service(config, self.pipeline)
        self.service.ready = True
        self.server = ss.Server(('127.0.0.1', 0), ss.Handler)
        self.server.service = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, body=b'', token=TOKEN, chunked=False, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        if chunked:
            headers['Transfer-Encoding'] = 'chunked'
            conn.request(method, path, body=iter([body[:1001], body[1001:]]), headers=headers,
                         encode_chunked=True)
        else:
            conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    @staticmethod
    def events(data):
        return [json.loads(line) for line in data.decode().splitlines() if line]

    def test_health_without_token(self):
        response, data = self.request('/health', method='GET', token=None)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(data), {'ok': True, 'ready': True})

    def test_turn_requires_token(self):
        response, _ = self.request('/v1/turn', b'\0' * 16000, token='wrong' * 10)
        self.assertEqual(response.status, 401)

    def test_full_turn_chunked_upload(self):
        response, data = self.request('/v1/turn', b'\1' * 16000, chunked=True)
        self.assertEqual(response.status, 200)
        events = self.events(data)
        self.assertEqual([e.get('stage') for e in events if e['event'] == 'stage'],
                         ['recognize', 'think', 'synthesize', 'render'])
        self.assertEqual(self.pipeline.accepted, 16000)
        audio = next(e for e in events if e['event'] == 'audio')
        self.assertEqual(audio['duration_ms'], 500)
        self.assertTrue(base64.b64decode(audio['data']).startswith(b'RIFF'))
        done = events[-1]
        self.assertEqual(done['event'], 'done')
        self.assertIn('server_total', done['timings'])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])  # temp files removed

    def test_odd_chunk_alignment_is_preserved(self):
        self.request('/v1/turn', b'\1' * 16001, chunked=True)
        self.assertEqual(self.pipeline.accepted, 16000)

    def test_too_large_upload(self):
        response, _ = self.request('/v1/turn', b'\0' * (16000 * 2 * 3))
        self.assertEqual(response.status, 413)
        _, data = self.request('/v1/turn', b'\0' * (16000 * 2 * 3), chunked=True)
        self.assertEqual(self.events(data)[-1]['code'], 'too_large')

    def test_no_speech_and_llm_errors(self):
        self.pipeline.transcript = None
        _, data = self.request('/v1/turn', b'\1' * 16000)
        self.assertEqual(self.events(data)[-1]['code'], 'no_speech')
        self.pipeline.transcript = 'hallo'
        self.pipeline.fail_llm = True
        _, data = self.request('/v1/turn', b'\1' * 16000)
        last = self.events(data)[-1]
        self.assertEqual((last['stage'], last['code']), ('think', 'llm'))

    def test_speak_skips_stt_and_llm(self):
        _, data = self.request('/v1/speak', json.dumps({'text': 'Test.'}).encode())
        events = self.events(data)
        self.assertEqual([e.get('stage') for e in events if e['event'] == 'stage'],
                         ['synthesize', 'render'])
        self.assertEqual(self.pipeline.accepted, 0)

    def test_second_turn_is_rejected_while_busy(self):
        self.pipeline.block = threading.Event()
        results = {}
        first = threading.Thread(target=lambda: results.update(
            first=self.request('/v1/turn', b'\1' * 16000)))
        first.start()
        for _ in range(100):
            if self.service.turn_lock.locked():
                break
            threading.Event().wait(0.02)
        response, _ = self.request('/v1/turn', b'\1' * 16000)
        self.pipeline.block.set()
        first.join()
        self.assertEqual(response.status, 503)
        self.assertEqual(results['first'][0].status, 200)

    def test_rate_limit(self):
        limiter = ss.RateLimiter(2, clock=lambda: 0.0)
        self.assertTrue(limiter.allow('a'))
        self.assertTrue(limiter.allow('a'))
        self.assertFalse(limiter.allow('a'))
        self.assertTrue(limiter.allow('b'))

    def test_short_token_refused(self):
        with self.assertRaises(ValueError):
            ss.Config({'SERVITOR_API_TOKEN': 'short'})

    def test_openrouter_failure_falls_back_to_local_llm(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
        import llm
        pipeline = ss.RealPipeline(self.tmp.name)
        cases = (
            ('1', None, ('lokal', 'local/q')),
            ('0', None, llm.LLMError),
            ('1', llm.LLMError('down'), llm.LLMError),
        )
        for enabled, local_error, expected in cases:
            with self.subTest(enabled=enabled, local_error=local_error), \
                    unittest.mock.patch.dict(os.environ, {'SERVITOR_LOCAL_LLM': enabled}), \
                    unittest.mock.patch('llm.generate_reply',
                                        side_effect=llm.LLMError('402 no credits')), \
                    unittest.mock.patch('llm.generate_local_reply',
                                        return_value=('lokal', 'local/q'),
                                        side_effect=local_error), \
                    unittest.mock.patch('sys.stdout'):
                if isinstance(expected, tuple):
                    self.assertEqual(pipeline.reply('frage'), expected)
                else:
                    with self.assertRaisesRegex(expected, '402 no credits'):
                        pipeline.reply('frage')

    def raw_turn(self, body_bytes):
        """Send a hand-written chunked body; return the decoded NDJSON events."""
        import socket
        with socket.create_connection(('127.0.0.1', self.server.server_address[1]), 5) as sock:
            sock.sendall(b'POST /v1/turn HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer '
                         + TOKEN.encode() + b'\r\nTransfer-Encoding: chunked\r\n\r\n'
                         + body_bytes)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return self.events(response.read())

    def test_malformed_chunks_are_bad_requests(self):
        for body in (b'zz\r\n', b'4\r\n\1\1\1\1XX0\r\n\r\n'):
            with self.subTest(body=body):
                last = self.raw_turn(body)[-1]
                self.assertEqual((last['event'], last['code']), ('error', 'bad_request'))

    def test_speak_requires_string_text(self):
        for payload in ({'text': None}, {'text': ['a']}, {'text': 5}):
            with self.subTest(payload=payload):
                response, _ = self.request('/v1/speak', json.dumps(payload).encode())
                self.assertEqual(response.status, 400)

    def test_forwarded_ip_only_trusted_from_configured_proxy(self):
        handler = ss.Handler.__new__(ss.Handler)
        handler.server = self.server
        handler.headers = {'CF-Connecting-IP': '203.0.113.9'}
        handler.client_address = ('172.22.9.128', 5000)
        self.assertEqual(handler._client(), '172.22.9.128')
        handler.client_address = ('127.0.0.1', 5000)
        self.assertEqual(handler._client(), '203.0.113.9')

    def test_real_pipeline_temp_files_do_not_leak_descriptors(self):
        pipeline = ss.RealPipeline(self.tmp.name)
        pipeline.voice = object()
        fd_dir = '/proc/self/fd' if os.path.isdir('/proc/self/fd') else '/dev/fd'
        before = len(os.listdir(fd_dir))
        with unittest.mock.patch.dict(sys.modules, voice_controls=unittest.mock.Mock()), \
                unittest.mock.patch.dict(sys.modules, voice_effects=unittest.mock.Mock()), \
                unittest.mock.patch('servitor_server.subprocess.run'), \
                unittest.mock.patch('servitor_server.wave.open'):
            for _ in range(5):
                pipeline.synthesize('x').unlink()
                pipeline.render(Path('in.wav')).unlink()
        self.assertEqual(len(os.listdir(fd_dir)), before)


if __name__ == '__main__':
    unittest.main()
