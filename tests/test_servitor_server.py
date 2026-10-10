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
import unittest.mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'server'))
import servitor_server as ss  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from transcribe import NoSpeechError  # noqa: E402

TOKEN = 'x' * 40


class FakeRecognizer:
    def __init__(self, owner):
        self.owner = owner

    def accept_pcm(self, pcm):
        assert len(pcm) % 2 == 0
        self.owner.accepted += len(pcm)

    def finish(self):
        if self.owner.transcript is None:
            raise NoSpeechError('Vosk returned no transcript')
        return self.owner.transcript


class FakePipeline:
    def __init__(self, workdir):
        self.workdir = workdir
        self.accepted = 0
        self.transcript = 'wie hoch ist der eiffelturm'
        self.fail_llm = False
        self.block = None
        self.files = []

    def recognizer(self):
        return FakeRecognizer(self)

    def reply(self, text, lore=None, mode=None, memory=None, persona=None, mood=None):
        self.lore, self.mode, self.memory, self.persona = lore, mode, memory, persona
        self.mood = mood
        if self.fail_llm:
            raise RuntimeError('OpenRouter request failed: timeout')
        if self.block:
            self.block.wait(5)
        return f'Antwort auf {text}{getattr(self, "suffix", "")}', 'test/model'

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

    def synthesize(self, text, voice='servitor'):
        self.voice = voice
        return self._wav('syn-', 22050)

    def convert(self, source):
        self.converted = source
        return self._wav('rvc-', 40000) if getattr(self, 'rvc', False) else None

    def render(self, source, voice='servitor'):
        self.render_voice, self.render_source = voice, source
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
            try:
                conn.request(method, path, body=iter([body[:1001], body[1001:]]),
                             headers=headers, encode_chunked=True)
            except (BrokenPipeError, ConnectionResetError):
                # The server may answer and close (e.g. too_large) before the
                # last chunk is out; its reply is already in the socket buffer.
                pass
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

    def test_status_needs_token_and_reports_updates(self):
        self.service.updates = dict(pending=2, security=1, lists_age_days=0)
        response, _ = self.request('/v1/status', method='GET', token=None)
        self.assertEqual(response.status, 401)
        response, data = self.request('/v1/status', method='GET')
        self.assertEqual(json.loads(data)['updates'], dict(pending=2, security=1, lists_age_days=0))

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
        # Announce the size only: the server answers 413 without reading the
        # body, so actually sending it raced with the close (broken pipe in CI).
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.putrequest('POST', '/v1/turn')
        conn.putheader('Authorization', f'Bearer {TOKEN}')
        conn.putheader('Content-Length', str(16000 * 2 * 3))
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        conn.close()
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

    def test_second_turn_waits_for_a_short_busy_server(self):
        self.pipeline.block = threading.Event()
        threading.Timer(0.3, self.pipeline.block.set).start()
        results = {}
        first = threading.Thread(target=lambda: results.update(
            first=self.request('/v1/turn', b'\1' * 16000)))
        first.start()
        for _ in range(100):
            if self.service.turn_lock.locked():
                break
            threading.Event().wait(0.02)
        response, _ = self.request('/v1/turn', b'\1' * 16000)
        first.join()
        self.assertEqual((response.status, results['first'][0].status), (200, 200))

    def test_turn_waits_while_the_server_loads(self):
        self.service.ready = False
        threading.Timer(0.3, lambda: setattr(self.service, 'ready', True)).start()
        response, _ = self.request('/v1/turn', b'\1' * 16000)
        self.assertEqual(response.status, 200)

    def test_second_turn_is_rejected_while_busy(self):
        self.service.config.busy_wait = 0.1
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
                pipeline = ss.RealPipeline(self.tmp.name)  # fresh retry window
                if isinstance(expected, tuple):
                    self.assertEqual(pipeline.reply('frage'), expected)
                else:
                    with self.assertRaisesRegex(expected, '402 no credits'):
                        pipeline.reply('frage')

    def test_openrouter_is_skipped_for_a_while_after_a_failure(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
        import llm
        now = [100.0]
        pipeline = ss.RealPipeline(self.tmp.name, clock=lambda: now[0])
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_LOCAL_LLM': '1',
                                                   'SERVITOR_OPENROUTER_RETRY_SECONDS': '60'}), \
                unittest.mock.patch('llm.generate_reply',
                                    side_effect=llm.LLMError('timed out')) as remote, \
                unittest.mock.patch('llm.generate_local_reply', return_value=('l', 'local/q')), \
                unittest.mock.patch('sys.stdout'):
            pipeline.reply('a')
            now[0] += 30
            pipeline.reply('b')
            self.assertEqual(remote.call_count, 1)  # skipped inside the window
            now[0] += 31
            pipeline.reply('c')
            self.assertEqual(remote.call_count, 2)  # retried after it

    def test_incomplete_headers_time_out_before_auth(self):
        import socket
        self.service.config.idle_timeout = 0.3
        with socket.create_connection(('127.0.0.1', self.server.server_address[1]), 5) as sock:
            sock.settimeout(3)
            sock.sendall(b'POST /v1/turn HTTP/1.1\r\nHost: x\r\n')  # never finished
            started = time.monotonic()
            self.assertEqual(sock.recv(1024), b'')  # server closed the connection
            self.assertLess(time.monotonic() - started, 2)

    def test_vosk_failure_during_upload_is_a_recognize_error(self):
        def broken(_pcm):
            raise RuntimeError('vosk crashed')
        original = self.pipeline.recognizer

        def recognizer():
            rec = original()
            rec.accept_pcm = broken
            return rec
        self.pipeline.recognizer = recognizer
        _, data = self.request('/v1/turn', b'\1' * 16000)
        last = self.events(data)[-1]
        self.assertEqual((last['event'], last['stage'], last['code']),
                         ('error', 'recognize', 'stt'))

    def test_broken_finalization_is_stt_not_no_speech(self):
        original = self.pipeline.recognizer

        def recognizer():
            rec = original()
            def finish():
                raise RuntimeError('live Vosk finalization failed: native error')
            rec.finish = finish
            return rec
        self.pipeline.recognizer = recognizer
        _, data = self.request('/v1/turn', b'\1' * 16000)
        last = self.events(data)[-1]
        self.assertEqual((last['stage'], last['code']), ('recognize', 'stt'))

    def test_lore_level_from_device_reaches_llm_and_intents(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps({'lore': 'full'})})
        conn.getresponse().read()
        conn.close()
        self.assertEqual(self.pipeline.lore, 'full')
        self.pipeline.transcript = 'wer bist du'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps({'lore': 'full'})})
        data = conn.getresponse().read()
        conn.close()
        reply = next(e for e in self.events(data) if e['event'] == 'reply')
        self.assertIn('Omnissiah', reply['text'])

    def test_persona_and_voice_from_device_reach_pipeline(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}',
            'X-Servitor-Status': json.dumps({'persona': 'mensch', 'voice': 'natural'})})
        conn.getresponse().read()
        conn.close()
        self.assertEqual((self.pipeline.persona, self.pipeline.voice, self.pipeline.render_voice),
                         ('mensch', 'natural', 'natural'))
        self.request('/v1/turn', b'\1' * 16000)        # older Pi: no fields, the machine
        self.assertEqual((self.pipeline.persona, self.pipeline.voice), (None, 'servitor'))

    def test_billy_voice_goes_through_rvc_when_it_answers(self):
        self.pipeline.rvc = True
        _, data = self.request('/v1/turn', b'\1' * 16000)   # the machine: no RVC
        self.assertFalse(hasattr(self.pipeline, 'converted'))
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/speak', body=json.dumps({'text': 'Gemerkt.'}), headers={
            'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps({'voice': 'natural'})})
        data = conn.getresponse().read()
        conn.close()
        events = self.events(data)
        self.assertEqual(self.pipeline.render_voice, 'rvc')
        self.assertTrue(self.pipeline.render_source.name.startswith('rvc-'))
        self.assertIn('convert', next(e for e in events if e['event'] == 'done')['timings'])
        self.assertIn('audio', [e['event'] for e in events])
        self.assertEqual(list(Path(self.tmp.name).glob('*.wav')), [])   # all cleaned up

    def test_stop_phrase_ends_the_turn_without_audio(self):
        self.pipeline.transcript = 'sei still'
        _, data = self.request('/v1/turn', b'\1' * 16000)
        names = [e['event'] for e in self.events(data)]
        self.assertIn('stop', names)
        for name in ('reply', 'audio'):
            self.assertNotIn(name, names)
        self.assertEqual(names[-1], 'done')

    def test_billy_identity_goes_to_the_llm(self):
        self.pipeline.transcript = 'wer bist du'
        for persona, expected in (('servitor', 'Servitor Proximus'), ('mensch', 'Antwort auf')):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1],
                                              timeout=10)
            conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
                'Authorization': f'Bearer {TOKEN}',
                'X-Servitor-Status': json.dumps({'persona': persona})})
            data = conn.getresponse().read()
            conn.close()
            reply = next(e for e in self.events(data) if e['event'] == 'reply')
            self.assertIn(expected, reply['text'])

    def turn_with_memory(self, state, copy=None, session=None, **status):
        import memory
        headers = {'Authorization': f'Bearer {TOKEN}',
                   'X-Servitor-Status': json.dumps(dict(status, memory=state))}
        if session:
            headers['X-Servitor-Session'] = session
        if copy is not None:
            headers['X-Servitor-Memory'] = memory.encode_header(copy)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers=headers)
        data = conn.getresponse().read()
        conn.close()
        return self.events(data)

    def envelope(self, path, kind, body=None, token=TOKEN):
        import protocol
        response, data = self.request(path, protocol.encode(protocol.message(kind, body)),
                                      token=token)
        return response, json.loads(data)

    def test_hello_opens_a_session(self):
        response, _ = self.envelope('/v1/hello', 'hello', dict(device='pi'), token='y' * 40)
        self.assertEqual(response.status, 401)
        response, welcome = self.envelope('/v1/hello', 'hello', dict(device='pi'))
        self.assertEqual(response.status, 200)
        self.assertEqual(welcome['type'], 'welcome')
        self.assertTrue(self.service.sessions.known(welcome['body']['session']))
        self.assertIn('ping', welcome['body']['handles'])
        response, error = self.envelope('/v1/hello', 'ping')
        self.assertEqual((response.status, error['error']), (400, 'type'))
        response, error = self.request('/v1/hello', b'{"v": 9}')
        self.assertEqual((response.status, json.loads(error)['error']), (400, 'version'))

    def test_message_is_acknowledged_and_handled_once(self):
        import protocol
        item = protocol.message('ping')
        acks = []
        for _ in range(2):
            response, data = self.request('/v1/message', protocol.encode(item))
            self.assertEqual(response.status, 200)
            acks.append(json.loads(data)['body'])
        self.assertEqual(acks, [dict(id=item['id'], duplicate=False),
                                dict(id=item['id'], duplicate=True)])
        response, data = self.envelope('/v1/message', 'welcome')
        self.assertEqual((response.status, data['error']), (400, 'unsupported'))

    def test_session_keeps_the_memory_core_between_turns(self):
        import protocol
        copy = dict(facts=['Bediener heißt Ivan'], directives=[], total_facts=1,
                    history=[dict(q='hallo', a='Gruß.')])
        _, welcome = self.envelope('/v1/hello', 'hello', dict(device='pi'))
        session = welcome['body']['session']
        events = self.turn_with_memory('on', copy, session=session)
        core = protocol.digest(protocol.split_memory(copy)[0])
        self.assertIn(dict(event='session', core=core), events)
        slim = dict(core=core, history=[dict(q='und jetzt', a='Bereit.')])
        events = self.turn_with_memory('on', slim, session=session)
        self.assertNotIn('session', [e['event'] for e in events])
        self.assertEqual(self.pipeline.memory['facts'], ['Bediener heißt Ivan'])
        self.assertEqual(self.pipeline.memory['history'], [dict(q='und jetzt', a='Bereit')])
        self.service.sessions = protocol.Sessions()              # server restarted
        events = self.turn_with_memory('on', slim, session=session)
        self.assertIn(dict(event='session', state='unknown'), events)
        self.assertIs(self.pipeline.memory, ss.NO_MEMORY)        # never "the stick is empty"
        events = self.turn_with_memory('on', copy)                # an older Pi: as before
        self.assertNotIn('session', [e['event'] for e in events])
        self.assertEqual(self.pipeline.memory['facts'], ['Bediener heißt Ivan'])

    def test_mood_reaches_llm_and_tag_is_not_spoken(self):
        self.pipeline.suffix = ' [stimmung:gereizt]'
        events = self.turn_with_memory('off', mood='gereizt', mood_level=80, mood_refuse='on')
        self.assertEqual(self.pipeline.mood, dict(emotion='gereizt', level=80, refuse=True))
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertNotIn('stimmung', reply['text'])
        self.assertIn(dict(event='mood', emotion='gereizt'), events)
        self.pipeline.transcript = 'hilfe es brennt'
        self.turn_with_memory('off', mood='gereizt', mood_level=80, mood_refuse='on')
        self.assertFalse(self.pipeline.mood['refuse'])
        self.turn_with_memory('off')                         # feelings off or an older Pi
        self.assertIsNone(self.pipeline.mood)

    def test_billy_words_for_memory_commands_and_intents(self):
        self.pipeline.transcript = 'merk dir dass ich kaffee mag'
        events = self.turn_with_memory('on', dict(facts=[], directives=[]), persona='mensch')
        self.assertEqual(next(e for e in events if e['event'] == 'reply')['text'], 'Gemerkt.')
        self.pipeline.transcript = 'wie spät ist es'
        events = self.turn_with_memory('on', dict(facts=[], directives=[]), persona='mensch')
        self.assertTrue(next(e for e in events if e['event'] == 'reply')['text']
                        .startswith('Es ist '))

    def test_memory_copy_reaches_llm_and_learned_lines_are_not_spoken(self):
        copy = dict(facts=['Bediener heißt Ivan'], directives=['Städte heißen Makropolen'],
                    history=[dict(q='hallo', a='Gruß.')], total_facts=1)
        self.pipeline.suffix = ' MERKE: Bediener mag Kaffee.'
        events = self.turn_with_memory('on', copy)
        self.assertEqual(self.pipeline.memory['facts'], ['Bediener heißt Ivan'])
        self.assertEqual(self.pipeline.memory['history'], [dict(q='hallo', a='Gruß')])
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertNotIn('MERKE', reply['text'])
        learned = [e for e in events if e['event'] == 'memory']
        self.assertEqual(learned, [dict(event='memory', op='add_fact',
                                        text='Bediener mag Kaffee', learned=True)])

    def test_memory_command_without_llm_and_without_stick(self):
        self.pipeline.transcript = 'installiere humor erweiterung'
        events = self.turn_with_memory('on', dict(facts=[], directives=[], history=[]))
        self.assertIn(dict(event='memory', op='add_directive',
                           text='Humor-Erweiterung installiert'), events)
        self.assertEqual(next(e for e in events if e['event'] == 'reply')['model'],
                         'local/memory')
        events = self.turn_with_memory('off')
        self.assertFalse([e for e in events if e['event'] == 'memory'])
        self.assertIn('Kein Gedächtnisspeicher', next(e for e in events if e['event'] == 'reply')['text'])

    def test_pi_without_memory_support_is_unchanged(self):
        import llm
        self.pipeline.transcript = 'merk dir dass ich ivan heiße'
        events = self.request('/v1/turn', b'\1' * 16000)[1]
        reply = next(e for e in self.events(events) if e['event'] == 'reply')
        self.assertEqual(reply['model'], 'test/model')
        self.assertIs(self.pipeline.memory, llm.NO_MEMORY)

    def test_free_mode_uses_the_free_model(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
        import llm
        pipeline = ss.RealPipeline(self.tmp.name)
        with unittest.mock.patch('llm.generate_reply', return_value=('f', 'x')) as remote:
            pipeline.reply('frage', mode='free')
            pipeline.reply('frage', mode='auto')
        self.assertEqual([c.kwargs['model'] for c in remote.call_args_list], [llm.free_model(), None])

    def test_local_mode_skips_openrouter(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
        import llm
        pipeline = ss.RealPipeline(self.tmp.name)
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_LOCAL_LLM': '1'}), \
                unittest.mock.patch('llm.generate_reply') as remote, \
                unittest.mock.patch('llm.generate_local_reply', return_value=('l', 'local/q')):
            self.assertEqual(pipeline.reply('frage', mode='local'), ('l', 'local/q'))
        remote.assert_not_called()

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

    def _rvc_server(self, status=200, body=None):
        """A stand-in for server/rvc_worker.py answering every POST alike."""
        reply = body if body is not None else Path(self.pipeline._wav('ref-', 40000)).read_bytes()
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append((self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(status)
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}', seen

    def test_real_pipeline_converts_through_the_rvc_worker(self):
        url, seen = self._rvc_server()
        clock = [100.0]
        pipeline = ss.RealPipeline(self.tmp.name, clock=lambda: clock[0])
        source = self.pipeline._wav('syn-', 22050)
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_RVC_URL': url + '/'}):
            converted = pipeline.convert(source)
        self.assertEqual(seen, [('/v1/convert', source.read_bytes())])
        self.assertEqual(ss.wav_duration_ms(converted), 500)
        converted.unlink()
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(pipeline.convert(source))   # RVC not configured
        self.assertEqual(len(seen), 1)

    def test_real_pipeline_rvc_failure_falls_back_and_pauses(self):
        url, seen = self._rvc_server(status=500, body=b'{"error": "convert_failed"}')
        clock = [100.0]
        pipeline = ss.RealPipeline(self.tmp.name, clock=lambda: clock[0])
        source = self.pipeline._wav('syn-', 22050)
        env = {'SERVITOR_RVC_URL': url, 'SERVITOR_RVC_RETRY_SECONDS': '60'}
        with unittest.mock.patch.dict(os.environ, env), \
                unittest.mock.patch('builtins.print') as printed:
            self.assertIsNone(pipeline.convert(source))
            self.assertIsNone(pipeline.convert(source))       # paused: not asked again
            self.assertEqual(len(seen), 1)
            clock[0] += 61
            self.assertIsNone(pipeline.convert(source))
            self.assertEqual(len(seen), 2)
        self.assertIn('rvc_fallback', printed.call_args_list[0].args[0])
        url, _ = self._rvc_server(body=b'not a wav')
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_RVC_URL': url}), \
                unittest.mock.patch('builtins.print'):
            clock[0] += 61
            self.assertIsNone(pipeline.convert(source))       # broken reply
        self.assertEqual([p.name[:4] for p in Path(self.tmp.name).glob('*.wav')],
                         ['syn-'])   # no converted file left behind



class IntentServerTest(ServerTest):
    def test_time_is_answered_without_llm_in_local_time(self):
        self.pipeline.transcript = 'wie spät ist es'
        self.pipeline.fail_llm = True  # would fail if the LLM were asked
        import datetime
        import zoneinfo
        fixed = datetime.datetime(2026, 10, 8, 4, 7, tzinfo=zoneinfo.ZoneInfo('Europe/Berlin'))
        with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
            _, data = self.request('/v1/turn', b'\1' * 16000)
        events = self.events(data)
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertEqual((reply['text'], reply['model']), ('Zeitindex: 4 Uhr 7.', 'local/intent'))
        self.assertNotIn('think', [e.get('stage') for e in events])
        self.assertEqual(events[-1]['event'], 'done')

    def test_briefing_fetches_weather_on_the_server(self):
        self.pipeline.transcript = 'guten morgen'
        self.pipeline.fail_llm = True
        self.service.weather = unittest.mock.Mock()
        self.service.weather.get.return_value = dict(now=12, code=3, high=14, low=6, rain=10)
        _, data = self.request('/v1/turn', b'\1' * 16000)
        reply = next(e for e in self.events(data) if e['event'] == 'reply')
        self.assertEqual(reply['model'], 'local/intent')
        self.assertIn('Außentemperatur 12 Grad, bedeckt.', reply['text'])
        self.assertNotIn('unknown', reply['text'])
        self.pipeline.transcript = 'wie spät ist es'
        self.request('/v1/turn', b'\1' * 16000)
        self.service.weather.get.assert_called_once()   # only weather questions ask

    def test_calendar_comes_from_the_pi_and_only_for_a_recognized_voice(self):
        import agenda
        import datetime
        import zoneinfo
        tz = zoneinfo.ZoneInfo('Europe/Berlin')
        fixed = datetime.datetime(2026, 10, 9, 7, 5, tzinfo=tz)
        dentist = dict(summary='Zahnarzt', start=fixed.replace(hour=9, minute=30), end=None,
                       all_day=False)

        def turn(header, memory_copy=None):
            headers = {'Authorization': f'Bearer {TOKEN}'}
            if header is not None:
                headers['X-Servitor-Agenda'] = header
            if memory_copy is not None:
                headers['X-Servitor-Status'] = json.dumps({'memory': 'on'})
                headers['X-Servitor-Memory'] = memory.encode_header(memory_copy)
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1],
                                              timeout=10)
            conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers=headers)
            data = conn.getresponse().read()
            conn.close()
            return next(e for e in self.events(data) if e['event'] == 'reply')['text']

        import memory
        self.pipeline.transcript = 'was steht heute an'
        self.pipeline.fail_llm = True
        with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
            self.assertEqual(turn(agenda.encode_header([dentist])),
                             'Termine heute. 9 Uhr 30: Zahnarzt.')
            self.assertEqual(turn(None), 'Kalenderdaten nicht verfügbar.')
            with unittest.mock.patch.object(ss.Service, '_identify',
                                            lambda self, audio, copy, emit:
                                            dict(copy, speaker='unknown')):
                text = turn(agenda.encode_header([dentist]),
                            dict(facts=[], directives=[], history=[],
                                 voiceprints=[dict(name='Ivan', print='AAAA')]))
        self.assertIn('nicht als Bediener erkannt', text)
        self.assertNotIn('Zahnarzt', text)

    def device_turn(self, transcript, status, memory_copy=None):
        import memory
        self.pipeline.transcript = transcript
        self.pipeline.fail_llm = True
        headers = {'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps(status)}
        if memory_copy is not None:
            headers['X-Servitor-Memory'] = memory.encode_header(memory_copy)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers=headers)
        events = self.events(conn.getresponse().read())
        conn.close()
        reply = next((e for e in events if e['event'] == 'reply'), {})
        return reply.get('text'), [e for e in events if e['event'] == 'device']

    def test_device_commands_need_a_pi_that_knows_them(self):
        text, device = self.device_turn('schalte das wlan aus', {'devctl': 'on', 'wlan': 'on'})
        self.assertEqual(text, 'WLAN deaktiviert. Lokaler Betrieb.')
        self.assertEqual(device, [{'event': 'device', 'op': 'wlan_off'}])
        text, device = self.device_turn('schalte das wlan aus', {'wlan': 'on'})   # older Pi
        self.assertEqual(device, [])

    def test_device_confirmation_and_maintenance(self):
        text, device = self.device_turn('fahr dich herunter', {'devctl': 'on'})
        self.assertIn('Bestätigen', text)
        self.assertEqual(device, [{'event': 'device', 'op': 'shutdown'}])
        text, device = self.device_turn('bestätigt', {'devctl': 'on', 'pending': 'shutdown'})
        self.assertEqual(text, 'Einheit fährt herunter.')
        self.assertEqual(device, [{'event': 'device', 'op': 'shutdown', 'confirm': True}])
        text, device = self.device_turn('nein', {'devctl': 'on', 'pending': 'shutdown'})
        self.assertEqual((text, device), ('Abgebrochen.', []))
        text, device = self.device_turn('bestätigt', {'devctl': 'on'})   # nothing pending
        self.assertEqual(device, [])
        text, device = self.device_turn('starte neu', {'devctl': 'on', 'maintenance': 'on',
                                                       'memory': 'off'})
        self.assertEqual(device, [])            # the maintenance mode's own reboot

    def test_unknown_voice_confirms_only_with_button_e(self):
        guest = dict(facts=[], directives=[], history=[],
                     voiceprints=[dict(name='Ivan', print='AAAA')])
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, copy, emit:
                                        dict(copy, speaker='unknown')):
            text, device = self.device_turn('fahr dich herunter',
                                            {'devctl': 'on', 'memory': 'on'}, guest)
            self.assertIn('Bestätigen nur mit Taste E', text)
            self.assertEqual(device, [{'event': 'device', 'op': 'shutdown'}])  # Pi waits for E
            text, device = self.device_turn(
                'bestätigt', {'devctl': 'on', 'pending': 'shutdown', 'memory': 'on'}, guest)
            self.assertIn('Taste E', text)
            self.assertEqual(device, [{'event': 'device', 'op': 'shutdown'}])  # asks again
            text, device = self.device_turn('wlan aus', {'devctl': 'on', 'wlan': 'on',
                                                         'memory': 'on'}, guest)
            self.assertEqual(device, [{'event': 'device', 'op': 'wlan_off'}])  # harmless

    def test_weather_answer_asks_the_pi_to_show_the_day(self):
        import datetime
        today = datetime.date(2026, 10, 10)
        days = [dict(date=(today + datetime.timedelta(days=i)).isoformat(), code=61,
                     high=9 + i, low=4, rain=80) for i in range(5)]
        self.service.weather = unittest.mock.Mock()
        self.service.weather.get.return_value = dict(now=7, code=61, high=9, low=4, rain=80,
                                                     days=days)
        fixed = datetime.datetime(2026, 10, 10, 7, 5)
        self.pipeline.fail_llm = True
        for transcript, day in (('wie wird das wetter morgen', 1), ('morgenbericht', 0),
                                ('wetter am freitag', None)):
            self.pipeline.transcript = transcript
            with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
                _, data = self.request('/v1/turn', b'\1' * 16000)
            events = self.events(data)
            shows = [e for e in events if e['event'] == 'show']
            reply = next(e for e in events if e['event'] == 'reply')['text']
            with self.subTest(transcript=transcript):
                self.assertEqual(shows, [] if day is None
                                 else [dict(event='show', screen='weather', day=day)])
        self.assertIn('fünf Tage', reply)

    def test_server_clock_uses_configured_timezone(self):
        self.assertEqual(str(self.service.now().tzinfo), 'Europe/Berlin')

    def test_status_uses_device_snapshot_header(self):
        self.pipeline.transcript = 'wie ist dein status'
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}',
            'X-Servitor-Status': json.dumps({'battery_pct': 83, 'battery_charging': True,
                                             'temp_c': 45, 'server': 'down', 'evil': 'x'})})
        data = conn.getresponse().read()
        conn.close()
        reply = next(e for e in self.events(data) if e['event'] == 'reply')
        self.assertIn('Energiespeicher 83 Prozent. Ladung aktiv.', reply['text'])
        self.assertIn('Verbindung zum Server stabil.', reply['text'])  # server knows it is up
        self.assertNotIn('nicht erreichbar', reply['text'])


if __name__ == '__main__':
    unittest.main()
