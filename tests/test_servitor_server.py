import base64
import http.client
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
import memory  # noqa: E402
import system_status  # noqa: E402

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

    nextcloud_result = None   # ((kind, value), model) for nextcloud_reply, None: no tools

    def nextcloud_enabled(self):
        return self.nextcloud_result is not None

    def nextcloud_reply(self, text, lore=None, mode=None, memory=None, persona=None, mood=None,
                        local=(), nextcloud=True):
        self.nextcloud_asked = getattr(self, 'nextcloud_asked', 0) + 1
        self.tools_asked = getattr(self, 'tools_asked', []) + [
            (text, [t.name for t in local], nextcloud)]
        return self.nextcloud_result

    agenda_result = None   # events for nextcloud_agenda (the browser's briefing)

    def nextcloud_agenda(self, now):
        self.agenda_asked = getattr(self, 'agenda_asked', 0) + 1
        return self.agenda_result

    def nextcloud_call(self, proposal):
        self.nextcloud_written = getattr(self, 'nextcloud_written', []) + [proposal.name]
        return proposal.done_text()

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

    def render(self, source, voice='servitor'):
        self.render_voice = voice
        return self._wav('dsp-', 48000)

    def encode(self, source, fmt):
        return Path(source).read_bytes()


class ServerTest(unittest.TestCase):
    def test_local_mode_uses_real_measurements_and_fixed_model_identity(self):
        cases = ('wie voll ist dein akku',
                 'kannst du bitte einmal nachsehen wie hoch dein akku gerade wirklich ist und ob du lädst',
                 'sag mir bitte einmal deine messwerte und deinen aktuellen status ohne etwas zu raten',
                 'welches sprachmodell bist du')
        self.pipeline.fail_llm = True  # any model invocation would fail this test
        for persona in ('servitor', 'mensch'):
            for question in cases:
                self.pipeline.transcript = question
                events = []
                self.service.run_turn([b'\1' * 16000], events.append, 'wav', device=dict(
                    llm_mode='local', persona=persona, battery_pct=80, battery_charging=True))
                reply = next(e for e in events if e['event'] == 'reply')
                self.assertEqual(reply['model'], 'local/intent')
                if 'sprachmodell' in question:
                    self.assertIn('Billy' if persona == 'mensch' else 'Proximus', reply['text'])
                else:
                    self.assertIn('80 Prozent', reply['text'])
                    self.assertNotIn('48 Prozent', reply['text'])
        self.pipeline.transcript = 'wie voll ist dein akku'
        events = []
        self.service.run_turn([b'\1' * 16000], events.append, 'wav', device=dict(llm_mode='local'))
        self.assertEqual(next(e['text'] for e in events if e['event'] == 'reply'),
                         'Energiedaten nicht verfügbar.')

    def test_tomorrow_calendar_header_is_used_and_legacy_client_gets_no_guesses(self):
        import agenda
        import datetime
        import zoneinfo
        now = datetime.datetime(2026, 10, 10, 23, 0, tzinfo=zoneinfo.ZoneInfo('Europe/Berlin'))
        early = dict(summary='Frühtermin', start=(now + datetime.timedelta(days=1)).replace(hour=6),
                     end=None, all_day=False)
        today = dict(early, summary='Termin heute', start=now)
        self.pipeline.transcript = 'was steht morgen an'
        self.pipeline.fail_llm = True

        def turn(tomorrow=None, guest=False):
            headers = {'Authorization': f'Bearer {TOKEN}',
                       'X-Servitor-Agenda': agenda.encode_header([today])}
            if tomorrow is not None:
                headers['X-Servitor-Agenda-Tomorrow'] = tomorrow
            if guest:
                import speaker
                copy = dict(facts=[], directives=[], history=[], voiceprints=[dict(
                    name='Ivan', print=speaker.encode([1.0, 0.0]))])
                headers['X-Servitor-Status'] = json.dumps(dict(memory='on'))
                headers['X-Servitor-Memory'] = memory.encode_header(copy)
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
            try:
                conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers=headers)
                events = self.events(conn.getresponse().read())
                return next(e['text'] for e in events if e['event'] == 'reply')
            finally:
                conn.close()

        with unittest.mock.patch.object(ss.Service, 'now', return_value=now):
            self.assertEqual(turn(agenda.encode_header([early])), 'Termine morgen. 6 Uhr: Frühtermin.')
            self.assertEqual(turn(), 'Kalenderdaten für morgen nicht verfügbar.')
            self.assertEqual(turn(agenda.encode_header([])), 'Keine weiteren Termine morgen.')
            self.assertEqual(turn('invalid header'), 'Kalenderdaten für morgen nicht verfügbar.')
            with unittest.mock.patch.object(ss.Service, '_identify',
                                             lambda self, audio, copy, emit, voice=None: memory.guest_view(copy)):
                self.assertIn('nicht als Bediener erkannt', turn(agenda.encode_header([early]), guest=True))

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

    def test_ready_event_only_on_change_or_after_failure(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
        import llm
        pipeline = ss.RealPipeline(self.tmp.name)
        replies = [('a', 'x/model'), ('b', 'x/model'), llm.LLMError('timed out'),
                   ('c', 'x/model'), ('d', 'x/model')]
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_LOCAL_LLM': '0'}), \
                unittest.mock.patch('llm.generate_reply', side_effect=replies), \
                unittest.mock.patch('sys.stdout') as stdout:
            pipeline.reply('1')
            pipeline.reply('2')
            with self.assertRaises(llm.LLMError):
                pipeline.reply('3')
            pipeline.reply('4')   # first success after the failure: recovery proof
            pipeline.reply('5')
        lines = [c.args[0] for c in stdout.write.call_args_list if 'openrouter_ready' in c.args[0]]
        self.assertEqual(len(lines), 2)

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
        self.assertIn('Adeptus Mechanicus', reply['text'])

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
        self.assertIn(next(e for e in events if e['event'] == 'reply')['text'],
                      memory.BILLY_REPLIES['add_fact'])
        self.pipeline.transcript = 'wie spät ist es'
        events = self.turn_with_memory('on', dict(facts=[], directives=[]), persona='mensch')
        self.assertRegex(next(e for e in events if e['event'] == 'reply')['text'],
                         r'^(Es ist |Gerade ist es )?\d+ Uhr')

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

    def test_ask_takes_the_way_of_a_transcript_without_audio(self):
        self.pipeline.transcript = None  # any recognition would fail
        _, data = self.request('/v1/ask', json.dumps({'text': 'Wie hoch ist der Eiffelturm?'}).encode())
        events = self.events(data)
        self.assertEqual(self.pipeline.accepted, 0)
        self.assertEqual(events[0], dict(event='transcript', text='Wie hoch ist der Eiffelturm?'))
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertEqual(reply['text'], 'Antwort auf Wie hoch ist der Eiffelturm?')
        self.assertEqual([e['event'] for e in events][-2:], ['audio', 'done'])

    def test_journey_without_journey_support_is_answered_without_llm(self):
        self.pipeline.fail_llm = True
        _, data = self.request('/v1/ask', json.dumps({'text': 'Wann fährt der nächste Bus zum Bahnhof'}).encode())
        events = self.events(data)
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertEqual((reply['model'], reply['text']), ('local/journey', ss.JOURNEY_ELSEWHERE['servitor']))
        self.assertFalse(any(e['event'] == 'journey' for e in events))
        self.assertEqual([e['event'] for e in events][-2:], ['audio', 'done'])

    def test_ask_answers_fixed_requests_without_llm(self):
        self.pipeline.fail_llm = True
        _, data = self.request('/v1/ask', json.dumps({'text': 'wie spät ist es'}).encode())
        reply = next(e for e in self.events(data) if e['event'] == 'reply')
        self.assertEqual(reply['model'], 'local/intent')

    def test_ask_validates_text_and_token(self):
        response, _ = self.request('/v1/ask', b'{"text": "x"}', token='y' * 40)
        self.assertEqual(response.status, 401)
        for payload, status in (({'text': ''}, 400), ({'text': 3}, 400), ({}, 400),
                                ({'text': 'x' * (ss.MAX_TYPED_CHARS + 1)}, 413)):
            with self.subTest(payload=payload):
                response, _ = self.request('/v1/ask', json.dumps(payload).encode())
                self.assertEqual(response.status, status)

    def test_password_login_gives_a_web_session_that_opens_turns_but_not_maintenance(self):
        self.service.config.web_password = ss.hash_password('kogitator-7')
        port = self.server.server_address[1]

        def call(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                return response, response.read()
            finally:
                conn.close()

        response, _ = call('POST', '/v1/login', json.dumps({'password': 'falsch'}))
        self.assertEqual(response.status, 401)
        self.assertIsNone(response.getheader('Set-Cookie'))
        response, _ = call('POST', '/v1/login', json.dumps({'password': 'kogitator-7', 'remember': True}))
        self.assertEqual(response.status, 200)
        cookie = response.getheader('Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn(f'Max-Age={ss.WebSessions.LONG}', cookie)
        session = cookie.split(';')[0]
        web = {'Cookie': session, 'X-Servitor-Client': 'web'}
        _, data = call('GET', '/v1/session', headers=web)
        self.assertTrue(json.loads(data)['ok'])
        response, data = call('POST', '/v1/ask', json.dumps({'text': 'wie spät ist es'}), web)
        self.assertEqual(response.status, 200)
        self.assertTrue(any(e['event'] == 'reply' for e in self.events(data)))
        # Without the client header (a foreign page posting a form) the cookie is worthless.
        response, _ = call('POST', '/v1/ask', json.dumps({'text': 'x'}), {'Cookie': session})
        self.assertEqual(response.status, 401)
        for method, path in (('GET', '/v1/status'), ('POST', '/v1/maintenance'), ('GET', '/v1/nightwatch')):
            with self.subTest(path=path):
                response, _ = call(method, path, b'{}' if method == 'POST' else None, web)
                self.assertEqual(response.status, 401)
        response, _ = call('POST', '/v1/logout', b'', web)
        self.assertIn('Max-Age=0', response.getheader('Set-Cookie'))
        _, data = call('GET', '/v1/session', headers=web)
        self.assertFalse(json.loads(data)['ok'])

    def test_password_login_locks_after_five_failures_and_is_off_without_a_hash(self):
        response, _ = self.request('/v1/login', json.dumps({'password': 'x'}).encode(), token=None)
        self.assertEqual(response.status, 503)
        self.service.config.web_password = ss.hash_password('kogitator-7')
        for _ in range(ss.WebSessions.TRIES):
            response, _ = self.request('/v1/login', json.dumps({'password': 'x'}).encode(), token=None)
            self.assertEqual(response.status, 401)
        response, _ = self.request('/v1/login', json.dumps({'password': 'kogitator-7'}).encode(), token=None)
        self.assertEqual(response.status, 429)

    def test_password_hash_round_trip(self):
        stored = ss.hash_password('Ave Omnissiah')
        self.assertTrue(stored.startswith('scrypt$'))
        self.assertTrue(ss.check_password('Ave Omnissiah', stored))
        self.assertFalse(ss.check_password('ave omnissiah', stored))
        self.assertFalse(ss.check_password('x', 'kaputt'))

    def test_journal_accepts_only_local_proximus_services(self):
        journal = self.service.journal
        for name, unit in (('Fremder Host-Dienst', 'ssh.service'),
                           ('Fremder Container', 'cron.service'),
                           ('Alle Kernelmeldungen', 'kernel')):
            journal.add_journald(json.dumps(dict(MESSAGE=name, PRIORITY='4',
                                                 _SYSTEMD_UNIT=unit)))
        journal.add_journald(json.dumps(dict(MESSAGE='Nicht lokaler Proximus',
                                             PRIORITY='4', _SYSTEMD_UNIT='servitor-voice.service',
                                             _HOSTNAME='wgz-pve-01')))
        journal.add_journald(json.dumps(dict(MESSAGE='172.22.9.1 "POST /v1/ask HTTP/1.1" 200 -',
                                             _SYSTEMD_UNIT='servitor-voice.service')))
        journal.add_journald(json.dumps(dict(MESSAGE='{"event": "ready", "load_seconds": 3.2}',
                                             PRIORITY='6', _SYSTEMD_UNIT='servitor-voice.service',
                                             __REALTIME_TIMESTAMP='1760000000000000')))
        journal.add_journald(json.dumps(dict(MESSAGE='Sprachkern bereit', PRIORITY='4',
                                             _SYSTEMD_UNIT='servitor-llm.service')))
        journal.add_journald('kein json')
        response, _ = self.request('/v1/journal', method='GET', token=None)
        self.assertEqual(response.status, 401)
        response, data = self.request('/v1/journal?after=0', method='GET')
        lines = json.loads(data)['lines']
        self.assertEqual([(line['unit'], line['prio']) for line in lines],
                         [('servitor-voice', 6), ('servitor-llm', 4)])
        self.assertEqual(lines[0]['ts'], 1760000000.0)
        self.assertEqual(lines[0]['text'], '{"event": "ready"}')  # Private/diagnostic payload omitted
        last = json.loads(data)['seq']
        _, data = self.request(f'/v1/journal?after={last}', method='GET')
        self.assertEqual(json.loads(data)['lines'], [])

    def test_api_requests_are_logged_as_events_with_their_client(self):
        lines = []
        with unittest.mock.patch('builtins.print', lambda *a, **k: lines.append(a[0])):
            self.request('/v1/ask', json.dumps({'text': 'wie spät ist es'}).encode())
            self.request('/health', method='GET', token=None)
            self.request('/web/app.js', method='GET', token=None)
        requests = [json.loads(line) for line in lines if '"request"' in line]
        self.assertEqual(requests, [dict(event='request', client='pi', method='POST',
                                         path='/v1/ask', status=200)])

    def test_a_wrong_token_is_not_logged_as_the_pi(self):
        lines = []
        with unittest.mock.patch('builtins.print', lambda *a, **k: lines.append(a[0])):
            self.request('/v1/ask', json.dumps({'text': 'wie spät ist es'}).encode(),
                         token='falsch')
        requests = [json.loads(line) for line in lines if '"request"' in line]
        self.assertEqual(requests, [dict(event='request', client='anon', method='POST',
                                         path='/v1/ask', status=401)])

    def test_journal_excludes_forwarded_logs_even_with_allowed_unit(self):
        self.service.journal.add_journald(json.dumps(dict(
            MESSAGE='Unzulässiger Fremdhost', PRIORITY='6',
            _SYSTEMD_UNIT='servitor-voice.service', _HOSTNAME='wgz-pve-01')))
        self.assertEqual(self.service.journal.since(0)['lines'], [])

    def test_journal_reconnects_after_restart_even_if_new_counter_is_higher(self):
        old = ss.Journal()
        old.add('alte Sitzung')
        new = self.service.journal
        new.add('Proximus verbunden', unit='servitor-voice')
        new.add('Sprachkern bereit', unit='servitor-llm')
        _, raw = self.request(f'/v1/journal?after={old.seq}&instance={old.instance}', method='GET')
        data = json.loads(raw)
        self.assertEqual([entry['text'] for entry in data['lines']], ['Proximus verbunden', 'Sprachkern bereit'])
        self.assertEqual(data['instance'], new.instance)
        _, raw = self.request(f'/v1/journal?after={new.seq}&instance={new.instance}', method='GET')
        self.assertEqual(json.loads(raw)['lines'], [])
        # Older browsers without an instance recover from a reset counter too.
        _, raw = self.request('/v1/journal?after=9999', method='GET')
        self.assertEqual(len(json.loads(raw)['lines']), 2)

    def test_journal_keeps_times_and_drops_colour_codes(self):
        journal = self.service.journal
        journal.add('\x1b[0;93m2026-10-14 19:15:33.070364312 [W:onnxruntime] Failed to persist\x1b[m')
        journal.add('neighbour fe80::1c2b:3aff:fe11:22 reachable')
        texts = [l['text'] for l in journal.since(0)['lines']]
        self.assertEqual(texts, ['2026-10-14 19:15:33.070364312 [W:onnxruntime] Failed to persist',
                                 'neighbour *:* reachable'])

    def test_journal_follows_journalctl_output(self):
        script = Path(self.tmp.name) / 'fake-journalctl'
        script.write_text('#!/bin/sh\necho \'{"MESSAGE": "Proximus bereit", "_SYSTEMD_UNIT": "servitor-voice.service"}\'\n'
                          'echo "No journal files were found." >&2\nexit 9\n')
        script.chmod(0o755)
        stop = threading.Event()
        thread = ss.follow_system_journal(self.service.journal, [str(script)], stop=stop)
        try:
            for _ in range(50):
                data = self.service.journal.since(0)
                if 'Status 9' in data['source']['error']:
                    break
                time.sleep(0.05)
            texts = [line['text'] for line in data['lines']]
            self.assertIn('Proximus bereit', texts)
            self.assertIn('journalctl: No journal files were found.', texts)
            self.assertIn('Status 9', data['source']['error'])
        finally:
            stop.set()
            thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_tee_fallback_without_journalctl(self):
        tee = ss.Tee(open(os.devnull, 'w'), self.service.journal)
        tee.write('half')
        tee.write(' a line\n192.168.1.77 "GET /web/ HTTP/1.1" 200 -\n')
        self.assertEqual([l['text'] for l in self.service.journal.since(0)['lines']], ['half a line'])

    def test_web_client_is_served_without_token_and_only_its_files(self):
        response, _ = self.request('/web', method='GET', token=None)
        self.assertEqual((response.status, response.getheader('Location')), (302, '/web/'))
        for path, mime in (('/', 'text/html'), ('/web/', 'text/html'), ('/web/app.js', 'text/javascript'),
                           ('/web/capture-worklet.js', 'text/javascript'),
                           ('/web/style.css', 'text/css'), ('/web/servo-skull.png', 'image/png'),
                           ('/web/manifest.webmanifest', 'application/manifest+json')):
            with self.subTest(path=path):
                response, data = self.request(path, method='GET', token=None)
                self.assertEqual(response.status, 200)
                self.assertTrue(response.getheader('Content-Type').startswith(mime))
                self.assertIn("default-src 'self'", response.getheader('Content-Security-Policy'))
                self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
                self.assertTrue(data)
        for path in ('/web/../server/servitor_server.py', '/web/index.html', '/web/%2e%2e/README.md',
                     '/web/app.js.map'):
            with self.subTest(path=path):
                response, _ = self.request(path, method='GET', token=None)
                self.assertEqual(response.status, 404)

    def test_web_client_references_only_served_files(self):
        import re
        html = (ss.WEB / 'index.html').read_text()
        for name in re.findall(r'(?:src|href)="([^"]+)"', html):
            if not name.startswith('#'):          # sigils inside the page
                self.assertIn(name, ss.WEB_FILES)
        css = (ss.WEB / 'style.css').read_text()
        for name in re.findall(r'url\("(/[^"]+)"\)', css):
            self.assertIn(name, ss.WEB_FILES)       # fonts, the skull
        for name, _ in ss.WEB_FILES.values():
            self.assertTrue((ss.WEB / name).is_file(), name)
        script = (ss.WEB / 'app.js').read_text()
        # Turns plus the read-only token check of the call; never maintenance.
        for endpoint in re.findall(r"'(/v1/[a-z]+)'", script):
            self.assertIn(endpoint, ('/v1/turn', '/v1/ask', '/v1/story', '/v1/status', '/v1/journal',
                                     '/v1/login', '/v1/logout', '/v1/session'))
        self.assertIn("addModule('/web/capture-worklet.js')", script)

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
        self.assertIn(reply['text'], ('Zeitindex: 4 Uhr 7.', 'Es ist 4 Uhr 7.'))
        self.assertEqual(reply['model'], 'local/intent')
        self.assertNotIn('think', [e.get('stage') for e in events])
        self.assertEqual(events[-1]['event'], 'done')

    def test_briefing_fetches_weather_on_the_server(self):
        import datetime
        import zoneinfo
        self.pipeline.transcript = 'guten morgen'
        self.pipeline.fail_llm = True
        self.service.weather = unittest.mock.Mock()
        self.service.weather.get.return_value = dict(now=12, code=3, high=14, low=6, rain=10)
        fixed = datetime.datetime(2026, 10, 8, 8, 0,
                                  tzinfo=zoneinfo.ZoneInfo('Europe/Berlin'))
        with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
            _, data = self.request('/v1/turn', b'\1' * 16000)
        reply = next(e for e in self.events(data) if e['event'] == 'reply')
        self.assertEqual(reply['model'], 'local/intent')
        self.assertIn('Außentemperatur 12 Grad, bedeckt.', reply['text'])
        self.assertNotIn('unknown', reply['text'])
        self.pipeline.transcript = 'wie spät ist es'
        self.request('/v1/turn', b'\1' * 16000)
        self.service.weather.get.assert_called_once()   # only weather questions ask

    def test_briefing_and_selftest_name_pi_and_server_log_findings(self):
        import logwatch
        self.pipeline.fail_llm = True
        watch = logwatch.LogWatch(logwatch.SERVER_RULES, logwatch.SERVER_UNITS,
                                  reader=lambda since, units: ([], False),
                                  sleep=lambda s: None)
        watch.check()
        watch.result['findings'] = {'crash_llm': 1}
        self.service.logwatch = watch

        def reply(text, status):
            self.pipeline.transcript = text
            _, data = self.request_with_status(status)
            return next(e for e in self.events(data) if e['event'] == 'reply')['text']

        status = dict(log_findings={'undervoltage': 2, 'bogus': 1}, log_repairs=['display'])
        text = reply('guten morgen', status)
        self.assertIn('Selbsttest meldet: Meine Stromversorgung ist eingebrochen, zweimal. '
                      'Mein lokaler Sprachkern ist abgestürzt.', text)
        self.assertNotIn('bogus', text)
        answer = reply('selbsttest', status)
        self.assertIn('Meine Stromversorgung ist eingebrochen', answer)
        self.assertIn('Meine Anzeige habe ich neu gestartet.', answer)
        watch.result['findings'] = {}
        self.assertNotIn('Selbsttest', reply('guten morgen', dict(log_findings={})))

    def test_watch_answers_from_the_server_and_feeds_the_briefing(self):
        import systemwache
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_systemwache import FakeKuma
        self.pipeline.fail_llm = True
        kuma = FakeKuma()
        with tempfile.TemporaryDirectory() as directory:
            watch = systemwache.KumaWatch('https://u', ['cloud', 'vault'], directory=directory,
                                          fetch=kuma)
            watch.poll()
            kuma.beat(16, systemwache.DOWN, slug='vault')
            watch.poll()
            watch.poll()
            self.service.watch = watch

            def reply(text):
                self.pipeline.transcript = text
                _, data = self.request_with_status({})
                return next(e for e in self.events(data) if e['event'] == 'reply')

            answer = reply('was ist bei uptime kuma los')
            self.assertEqual(answer['model'], 'local/intent')
            self.assertIn('Von 8 überwachten Diensten ist einer ausgefallen: vault.obivan.org',
                          answer['text'])
            self.assertIn('Systemwache: vault.obivan.org ist seit', reply('guten morgen')['text'])

    def kuma_watch(self, directory):
        import systemwache
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_systemwache import FakeKuma
        kuma = FakeKuma()
        watch = systemwache.KumaWatch('https://u', ['cloud', 'vault'], directory=directory,
                                      fetch=kuma)
        watch.poll()
        kuma.beat(16, systemwache.DOWN, slug='vault')
        watch.poll()
        watch.poll()
        self.service.watch = watch
        return watch

    def web_ask(self, text, browser='a' * 32):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/ask', body=json.dumps({'text': text}).encode(),
                     headers={'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Client': 'web',
                              'X-Servitor-Browser': browser})
        events = self.events(conn.getresponse().read())
        conn.close()
        return next(e for e in events if e['event'] == 'reply')

    def test_kuma_details_go_to_the_model_with_the_watch_tool_in_the_web(self):
        self.pipeline.fail_llm = True
        self.pipeline.nextcloud_result = (('answer', 'Vault war gestern 12 Minuten weg.'),
                                          'test/model')
        with tempfile.TemporaryDirectory() as directory:
            self.kuma_watch(directory)
            answer = self.web_ask('Wie lange war Vault gestern down?')
            self.assertEqual(answer['text'], 'Vault war gestern 12 Minuten weg.')
            # The Systemwache tool only: Nextcloud stays with the operator's Pi voice.
            self.assertEqual(self.pipeline.tools_asked,
                             [('Wie lange war Vault gestern down?', ['kuma_status'], False)])
            self.web_ask('Hatte Vaultwarden heute Ärger?')                # free wording
            self.assertEqual(self.pipeline.tools_asked[-1][1], ['kuma_status'])
            answer = self.web_ask('Was ist bei Uptime Kuma los?')        # the fixed answer
            self.assertTrue(answer['model'].startswith('local/intent'))
            self.assertEqual(len(self.pipeline.tools_asked), 2)
            self.pipeline.fail_llm = False
            self.web_ask('Wie hoch ist der Eiffelturm?')
            self.assertEqual(len(self.pipeline.tools_asked), 2)          # no tools off topic

    def test_the_signed_in_web_client_is_the_operator(self):
        self.web_ask('Wie hoch ist der Eiffelturm?')
        self.assertEqual(self.pipeline.memory, dict(history=[], speaker='Ivan', channel='web'))
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_WEB_OPERATOR': 'off'}):
            self.web_ask('Wie hoch ist der Eiffelturm?')
        self.assertNotIsInstance(self.pipeline.memory, dict)   # no operator, no memory
        self.assertIsNone(ss.web_operator_name({'SERVITOR_WEB_OPERATOR': 'off'}))
        self.assertEqual(ss.web_operator_name({}), 'Ivan')

    def test_web_operator_gets_nextcloud_and_confirms_in_the_same_browser(self):
        import nextcloud_mcp
        proposal = nextcloud_mcp.Proposal('nc_calendar_create_todo', {'summary': 'Müll'})
        self.pipeline.nextcloud_result = (('answer', 'Freitag: Zahnarzt.'), 'test/model')
        answer = self.web_ask('hi! was steht im Kalender?')   # no Pi agenda in the browser
        self.assertEqual(answer['text'], 'Freitag: Zahnarzt.')
        self.assertEqual(self.pipeline.tools_asked[-1][2], True)
        self.pipeline.nextcloud_result = (('propose', proposal), 'test/model')
        self.assertEqual(self.web_ask('Leg eine Aufgabe Müll rausbringen an')['text'],
                         proposal.question())
        self.assertEqual(self.web_ask('ja', browser='b' * 32)['text'], nextcloud_mcp.CANCELLED)
        self.assertEqual(getattr(self.pipeline, 'nextcloud_written', []), [])
        self.web_ask('Leg eine Aufgabe Müll rausbringen an')
        self.assertEqual(self.web_ask('ja')['text'], 'Aufgabe angelegt.')
        self.assertEqual(self.pipeline.nextcloud_written, ['nc_calendar_create_todo'])

    def test_the_web_litany_reads_the_calendar_from_nextcloud(self):
        import datetime
        import zoneinfo
        tz = zoneinfo.ZoneInfo('Europe/Berlin')
        fixed = datetime.datetime(2026, 10, 26, 7, 5, tzinfo=tz)
        self.pipeline.agenda_result = [
            dict(summary='Musikwiege', start=fixed.replace(hour=11, minute=0), end=None,
                 all_day=False),
            dict(summary='Zahnarzt', start=fixed.replace(day=27, hour=9, minute=30), end=None,
                 all_day=False)]
        with unittest.mock.patch.object(ss.Service, 'now', lambda self: fixed):
            text = self.web_ask('Morgenbericht')['text']
        self.assertIn('Termine heute. 11 Uhr: Musikwiege.', text)
        self.assertIn('Zahnarzt', text)
        self.assertEqual(self.pipeline.agenda_asked, 1)
        self.device_turn('morgenbericht', {})          # the Pi brings its own calendar
        self.assertEqual(self.pipeline.agenda_asked, 1)

    def test_kuma_tool_down_falls_back_to_the_fixed_answer(self):
        self.pipeline.fail_llm = True
        self.pipeline.nextcloud_result = None          # OpenRouter or the tool path failed
        with tempfile.TemporaryDirectory() as directory:
            self.kuma_watch(directory)
            answer = self.web_ask('Wie lange war Vault gestern down?')
        self.assertEqual(answer['model'], 'local/intent')
        self.assertIn('ausgefallen: vault.obivan.org seit', answer['text'])

    def test_kuma_tool_needs_the_operator_or_the_signed_in_web(self):
        self.pipeline.fail_llm = True
        self.pipeline.nextcloud_result = (('answer', 'unused'), 'test/model')
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        with tempfile.TemporaryDirectory() as directory:
            self.kuma_watch(directory)
            text, _ = self.device_turn('wie lange war vault gestern down', {})  # no memory
            self.assertIn('vault.obivan.org', text)
            with unittest.mock.patch.object(ss.Service, '_identify',
                                            lambda self, audio, c, emit, voice=None:
                                            dict(c, speaker='unknown')):
                text, _ = self.device_turn('wie lange war vault gestern down',
                                           {'memory': 'on'}, copy)
            self.assertIn('Bediener vorbehalten', text)
            self.assertEqual(getattr(self.pipeline, 'tools_asked', []), [])
            with unittest.mock.patch.object(ss.Service, '_identify',
                                            lambda self, audio, c, emit, voice=None:
                                            dict(c, speaker='Ivan')):
                text, _ = self.device_turn('wie lange war vault gestern down',
                                           {'memory': 'on'}, copy)
            self.assertEqual(text, 'unused')
            self.assertEqual(self.pipeline.tools_asked[-1][1:], (['kuma_status'], False))

    def request_with_status(self, status):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000,
                     headers={'Authorization': f'Bearer {TOKEN}',
                              'X-Servitor-Status': json.dumps(status)})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

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
                                            lambda self, audio, copy, emit, voice=None:
                                            dict(copy, speaker='unknown')):
                text = turn(agenda.encode_header([dentist]),
                            dict(facts=[], directives=[], history=[],
                                 voiceprints=[dict(name='Ivan', print='AAAA')]))
        self.assertIn('nicht als Bediener erkannt', text)
        self.assertNotIn('Zahnarzt', text)

    def test_briefing_names_today_and_tomorrow(self):
        import agenda
        import datetime
        import zoneinfo
        tz = zoneinfo.ZoneInfo('Europe/Berlin')
        fixed = datetime.datetime(2026, 10, 9, 7, 5, tzinfo=tz)
        dentist = dict(summary='Zahnarzt', start=fixed.replace(hour=9, minute=30), end=None,
                       all_day=False)
        barber = dict(summary='Friseur', start=fixed.replace(day=10, hour=10, minute=0),
                      end=None, all_day=False)
        headers = {'Authorization': f'Bearer {TOKEN}',
                   'X-Servitor-Agenda': agenda.encode_header([dentist]),
                   'X-Servitor-Agenda-Tomorrow': agenda.encode_header([barber])}
        self.pipeline.transcript = 'morgenbericht'
        self.pipeline.fail_llm = True
        with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1],
                                              timeout=10)
            conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers=headers)
            data = conn.getresponse().read()
            conn.close()
        text = next(e for e in self.events(data) if e['event'] == 'reply')['text']
        self.assertIn('Termine heute. 9 Uhr 30: Zahnarzt. Termine morgen. 10 Uhr: Friseur.', text)
        self.assertIn('Server bereit', text)

    def test_guest_cannot_start_enrollment(self):
        # "Lerne meine Stimme" from an unknown voice: a refusal, not the LLM.
        import memory
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, copy, emit, voice=None:
                                        dict(copy, speaker='unknown')):
            text, _ = self.device_turn('lerne meine stimme', {'memory': 'on'}, copy)
        self.assertEqual(text, memory.GUEST_TEXT)

    def test_enroll_command_without_a_memory_copy_does_not_crash(self):
        # Status without memory (old Pi, dropped header) or an unknown session
        # after a server restart: llm.NO_MEMORY reached unknown_speaker() and
        # the turn failed with AttributeError (audit 14.10.2026).
        import enroll
        for transcript in ('stimme kennenlernen', 'lerne meine stimme'):
            with self.subTest(transcript=transcript):
                text, _ = self.device_turn(transcript, {'devctl': 'on'})
                self.assertEqual(text, enroll.MENU_ONLY)

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
                                        lambda self, audio, copy, emit, voice=None:
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

    def test_nextcloud_write_needs_the_operators_yes(self):
        import nextcloud_mcp
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        who = ['Ivan']
        proposal = nextcloud_mcp.Proposal('nc_calendar_create_event',
                                          {'title': 'Zahnarzt', 'start_datetime': '2026-10-21T10:00'})
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, c, emit, voice=None:
                                        dict(c, speaker=who[0])):
            self.pipeline.nextcloud_result = (('propose', proposal), 'test/model')
            text, _ = self.device_turn('trag morgen um zehn den zahnarzt in den kalender ein',
                                       {'memory': 'on'}, copy)
            self.assertEqual(text, proposal.question())
            self.assertEqual(getattr(self.pipeline, 'nextcloud_written', []), [])
            who[0] = 'unknown'                     # short "ja" below the voice threshold
            text, _ = self.device_turn('ja', {'memory': 'on'}, copy)
            self.assertEqual(text, nextcloud_mcp.VOICE_UNSURE)
            who[0] = 'Ivan'
            text, _ = self.device_turn('ja bitte eintragen', {'memory': 'on'}, copy)
            self.assertEqual(text, 'Termin eingetragen.')
            self.assertEqual(self.pipeline.nextcloud_written, ['nc_calendar_create_event'])
            text, _ = self.device_turn('ja', {'memory': 'on'}, copy)   # ran once, nothing open
            self.assertNotEqual(text, 'Termin eingetragen.')
            self.assertEqual(self.pipeline.nextcloud_written, ['nc_calendar_create_event'])

    def test_nextcloud_proposal_is_dropped_by_no_or_another_question(self):
        import nextcloud_mcp
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        proposal = nextcloud_mcp.Proposal('nc_calendar_create_todo', {'summary': 'Müll'})
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, c, emit, voice=None:
                                        dict(c, speaker='Ivan')):
            self.pipeline.nextcloud_result = (('propose', proposal), 'test/model')
            self.device_turn('leg eine aufgabe müll rausbringen an', {'memory': 'on'}, copy)
            text, _ = self.device_turn('nein', {'memory': 'on'}, copy)
            self.assertEqual(text, nextcloud_mcp.CANCELLED)
            self.device_turn('leg eine aufgabe müll rausbringen an', {'memory': 'on'}, copy)
            self.device_turn('wie hoch ist der eiffelturm', {'memory': 'on'}, copy)
            text, _ = self.device_turn('ja', {'memory': 'on'}, copy)
            self.assertNotEqual(text, 'Aufgabe angelegt.')
            self.assertEqual(getattr(self.pipeline, 'nextcloud_written', []), [])

    def test_nextcloud_tools_only_for_the_recognized_operator(self):
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        self.pipeline.nextcloud_result = (('answer', 'Morgen: Zahnarzt.'), 'test/model')
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, c, emit, voice=None:
                                        dict(c, speaker='unknown')):
            self.device_turn('was steht in meinem kalender nächste woche', {'memory': 'on'}, copy)
        self.assertEqual(getattr(self.pipeline, 'nextcloud_asked', 0), 0)
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, c, emit, voice=None:
                                        dict(c, speaker='Ivan')):
            text, _ = self.device_turn('was steht in meinem kalender nächste woche',
                                       {'memory': 'on'}, copy)
            self.assertEqual(text, 'Morgen: Zahnarzt.')
            self.device_turn('wie hoch ist der eiffelturm', {'memory': 'on'}, copy)
        self.assertEqual(self.pipeline.nextcloud_asked, 1)   # no tools off topic

    def test_a_handed_over_calendar_question_never_falls_back_to_guessing(self):
        import nextcloud_mcp
        copy = dict(facts=[], directives=[], history=[],
                    voiceprints=[dict(name='Ivan', print='AAAA')])
        self.pipeline.nextcloud_result = (('answer', 'unused'), 'test/model')
        self.pipeline.nextcloud_reply = lambda *args, **kwargs: None   # MCP or OpenRouter down
        with unittest.mock.patch.object(ss.Service, '_identify',
                                        lambda self, audio, c, emit, voice=None:
                                        dict(c, speaker='Ivan')):
            text, _ = self.device_turn('was steht in meinem kalender nächste woche',
                                       {'memory': 'on'}, copy)
        self.assertEqual(text, nextcloud_mcp.UNAVAILABLE)

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

    def test_the_browser_gets_the_weather_card_along(self):
        import datetime
        today = datetime.date(2026, 10, 10)
        days = [dict(date=(today + datetime.timedelta(days=i)).isoformat(), code=code,
                     high=9 + i, low=4, rain=80 if code == 61 else 10)
                for i, code in enumerate((0, 61, 3, 71, 95, 45))]
        self.service.weather = unittest.mock.Mock()
        self.service.weather.get.return_value = dict(now=7, code=0, days=days)
        fixed = datetime.datetime(2026, 10, 10, 7, 5)
        with unittest.mock.patch.object(ss.Service, 'now', return_value=fixed):
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1],
                                              timeout=10)
            conn.request('POST', '/v1/ask', body=json.dumps(
                {'text': 'Wie wird das Wetter morgen?'}).encode(),
                headers={'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Client': 'web',
                         'X-Servitor-Browser': 'a' * 32})
            events = self.events(conn.getresponse().read())
        show = next(e for e in events if e['event'] == 'show')
        card = show['weather']
        self.assertEqual((show['day'], card['day']), (1, 1))
        self.assertEqual(card['chosen'], dict(date='2026-10-11', weekday='So', kind='rain',
                                              label='REGEN', high=10, low=4, rain=80,
                                              title='Morgen'))
        self.assertEqual([d['kind'] for d in card['days']],
                         ['clear', 'rain', 'cloudy', 'snow', 'thunder'])

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
        self.assertTrue(any(server in reply['text']                   # server knows it is up
                            for server in system_status.STATUS_FACTS['server']), reply['text'])
        self.assertNotIn('nicht erreichbar', reply['text'])

    def turn_reply(self, transcript, status):
        self.pipeline.transcript = transcript
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps(status)})
        data = conn.getresponse().read()
        conn.close()
        return next(e for e in self.events(data) if e['event'] == 'reply')

    def test_information_answers_get_fresh_words_from_the_llm(self):
        calls = []

        def rephrase(text, lore=None, persona=None, mode=None):
            calls.append((text, persona, mode))
            return 'Ich habe 83 Prozent im Akku und lade.'
        self.pipeline.rephrase = rephrase
        reply = self.turn_reply('wie ist der akkustand', {'battery_pct': 83,
                                                         'battery_charging': True})
        self.assertEqual(reply['text'], 'Ich habe 83 Prozent im Akku und lade.')
        self.assertEqual(reply['model'], 'local/intent+rephrase')
        self.assertIn('Energiespeicher 83 Prozent', calls[0][0])   # the fixed facts went in
        # Times are not rephrased, nor the status report: it must be fast.
        self.turn_reply('wie spät ist es', {})
        status = self.turn_reply('wie ist dein status', {'battery_pct': 83})
        self.assertEqual(len(calls), 1)
        self.assertEqual(status['model'], 'local/intent')

    def test_rephrase_failure_keeps_the_fixed_answer(self):
        for result in (None, RuntimeError('boom')):
            def rephrase(text, lore=None, persona=None, mode=None, result=result):
                if isinstance(result, Exception):
                    raise result
                return result
            self.pipeline.rephrase = rephrase
            reply = self.turn_reply('wie ist der akkustand', {'battery_pct': 83})
            self.assertIn('Energiespeicher 83 Prozent', reply['text'])
            self.assertEqual(reply['model'], 'local/intent')

    def turn_events(self, transcript, status):
        self.pipeline.transcript = transcript
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', '/v1/turn', body=b'\1' * 16000, headers={
            'Authorization': f'Bearer {TOKEN}', 'X-Servitor-Status': json.dumps(status)})
        data = conn.getresponse().read()
        conn.close()
        return self.events(data)

    def test_long_status_comes_in_parts_for_pis_that_queue_them(self):
        status = {'battery_pct': 83, 'temp_c': 45, 'uptime_s': 7380, 'wifi_dbm': -60,
                  'net_ms': 40, 'story': 'on'}
        events = self.turn_events('wie ist dein status', status)
        audio = [e for e in events if e['event'] == 'audio']
        self.assertGreater(len(audio), 1)
        self.assertEqual([e['part'] for e in audio], list(range(len(audio))))
        self.assertTrue(all(e['parts'] == len(audio) for e in audio))
        self.assertLessEqual(len(audio[0]['text']), 80)              # the voice starts early
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertEqual(' '.join(e['text'] for e in audio), reply['text'])
        self.assertEqual(events[-1]['event'], 'done')
        self.assertFalse(any(e['event'] == 'story' for e in events))  # no story state
        # An older Pi (no story support) gets one file as before.
        audio = [e for e in self.turn_events('wie ist dein status', dict(status, story=None))
                 if e['event'] == 'audio']
        self.assertEqual(len(audio), 1)
        self.assertNotIn('part', audio[0])

    def test_speech_parts(self):
        self.assertEqual(ss.speech_parts('Kurz. Ende.'), ['Kurz. Ende.'])
        text = ('Status nominal. Energiespeicher 80 Prozent. Akkubetrieb. Kerntemperatur 48 '
                'Grad. Verbindung zum Server stabil. Laufzeit zwei Stunden. Befehl erwartet.')
        parts = ss.speech_parts(text)
        self.assertEqual(' '.join(parts), text)
        self.assertGreaterEqual(len(parts[0]), ss.FIRST_PART_CHARS)
        self.assertTrue(all(p.endswith('.') for p in parts))
        self.assertTrue(all(len(p) >= ss.FIRST_PART_CHARS for p in parts))


class IdleTimeoutLogTests(unittest.TestCase):
    def test_idle_keepalive_timeout_is_not_logged_other_errors_are(self):
        handler = ss.Handler.__new__(ss.Handler)   # no socket needed
        handler.client_address = ('172.22.2.100', 40000)
        with unittest.mock.patch.object(sys, 'stderr') as stderr:
            handler.log_error('Request timed out: %r', TimeoutError('timed out'))
            stderr.write.assert_not_called()
            handler.log_error('code %d, message %s', 400, 'Bad request syntax')
        written = ''.join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn('172.22.2.100 code 400, message Bad request syntax', written)


class RephraseGuardTests(unittest.TestCase):
    def setUp(self):
        import llm
        self.llm = llm

    def test_same_facts(self):
        fixed = ("Status eingeschränkt. Warnung: Unterspannung. Energiespeicher 83 Prozent. "
                 "Zellspannung 4,02 Volt. Laufzeit zwei Stunden.")
        good = ("Achtung, Unterspannung! Der Speicher hat 83 Prozent, die Zelle 4,02 Volt, "
                "und ich laufe seit zwei Stunden.")
        self.assertTrue(self.llm.same_facts(fixed, good))
        for bad in ("Achtung, Unterspannung! 84 Prozent, 4,02 Volt.",          # changed
                    "Achtung, Unterspannung! 83 Prozent.",                       # dropped
                    "Alles gut. 83 Prozent, 4,02 Volt, zwei Stunden.",           # no warning
                    "Achtung: [stimmung:ruhig] 83 Prozent, 4,02 Volt.",          # markup
                    "Achtung. 83 Prozent, 4,02 Volt. " + "Blabla. " * 40,         # too long
                    ""):
            self.assertFalse(self.llm.same_facts(fixed, bad), bad)
        self.assertFalse(self.llm.same_facts("Akku bei 80 Prozent.",
                                             "Energiespeicher bei 80 Prozent.", 'mensch'))

    def test_rephrase_checks_the_answer_and_needs_a_key(self):
        with unittest.mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k'}):
            with unittest.mock.patch.object(self.llm, '_chat',
                                            return_value='Ich habe 80 Prozent im Akku.') as chat:
                self.assertEqual(self.llm.rephrase('Energiespeicher 80 Prozent.'),
                                 'Ich habe 80 Prozent im Akku.')
                messages = chat.call_args.kwargs['messages']
                self.assertIn('Energiespeicher 80 Prozent.', messages[-1]['content'])
            with unittest.mock.patch.object(self.llm, '_chat', return_value='Ich habe 90 Prozent.'):
                self.assertIsNone(self.llm.rephrase('Energiespeicher 80 Prozent.'))
        with unittest.mock.patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            with self.assertRaises(self.llm.LLMError):
                self.llm.rephrase('Energiespeicher 80 Prozent.')

    def test_real_pipeline_respects_switch_and_local_mode(self):
        pipeline = ss.RealPipeline.__new__(ss.RealPipeline)
        pipeline.clock, pipeline.openrouter_retry_at = (lambda: 100.0), 0.0
        with unittest.mock.patch('llm.rephrase', return_value='neu') as rephrase:
            self.assertEqual(pipeline.rephrase('alt'), 'neu')
            self.assertIsNone(pipeline.rephrase('alt', mode='local'))
            with unittest.mock.patch.dict(os.environ, {'SERVITOR_REPHRASE': 'off'}):
                self.assertIsNone(pipeline.rephrase('alt'))
            pipeline.openrouter_retry_at = 200.0              # OpenRouter cooling down
            self.assertIsNone(pipeline.rephrase('alt'))
            self.assertEqual(rephrase.call_count, 1)


class WhisperQuickPathTests(unittest.TestCase):
    def test_fixed_requests_and_stop_keep_the_vosk_text(self):
        self.assertTrue(ss.quick_text('wie spät ist es'))
        self.assertTrue(ss.quick_text('sei still'))
        self.assertFalse(ss.quick_text('wie hoch ist der eiffelturm'))

    def test_transcribe_mode_keeps_vosk_for_passphrases(self):
        seen = []

        class Recognizer:
            whisper = True

            def accept_pcm(self, pcm):
                pass

            def finish(self):
                seen.append(self.whisper)
                return 'omnissiah segne diese maschine'

        class Pipeline:
            def recognizer(self):
                return Recognizer()

        config = ss.Config({'SERVITOR_API_TOKEN': 'x' * 40})
        service = ss.Service(config, Pipeline())
        events = []
        service.run_turn([b'\1\0' * 8000], events.append, 'wav', transcribe_only=True)
        self.assertEqual(seen, [False])
        self.assertIn(dict(event='transcript', text='omnissiah segne diese maschine'), events)

if __name__ == '__main__':
    unittest.main()


class HighVoiceTest(unittest.TestCase):
    """RealPipeline: with thorsten-high loaded both voices speak with it, calm;
    the rendering makes Proximus (base chain + machine DSP) or Billy (limiter)."""

    def setUp(self):
        import types
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        class SynthesisConfig:
            def __init__(self, **fields):
                self.__dict__.update(fields)

        config = types.ModuleType('piper.config')
        config.SynthesisConfig = SynthesisConfig
        piper = types.ModuleType('piper')
        piper.config = config
        patcher = unittest.mock.patch.dict(sys.modules, {'piper': piper, 'piper.config': config})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def write(output):
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(bytes(100))

    def renders(self, pipeline):
        with unittest.mock.patch.object(ss.subprocess, 'run') as run:
            for voice in ('natural', 'servitor'):
                pipeline.render(Path(self.tmp.name) / 'x.wav', voice).unlink(missing_ok=True)
        return [call[0][0] for call in run.call_args_list]

    def test_both_voices_use_thorsten_high_calm(self):
        pipeline = ss.RealPipeline(self.tmp.name)
        pipeline.voice, pipeline.high = unittest.mock.Mock(), unittest.mock.Mock()
        pipeline.high.synthesize_wav.side_effect = lambda text, out, syn_config: self.write(out)
        with unittest.mock.patch.dict(os.environ, {'SERVITOR_HIGH_NOISE_SCALE': '0.3'}):
            for voice in ('natural', 'servitor'):
                pipeline.synthesize('Lob dem Omnissiah.', voice).unlink()
        from pronounce import spoken
        for call in pipeline.high.synthesize_wav.call_args_list:
            config = call[1]['syn_config']
            self.assertEqual(call[0][0], spoken('Lob dem Omnissiah.'))
            self.assertEqual((config.noise_scale, config.noise_w_scale, config.length_scale),
                             (0.3, 0.4, 1.05))
        pipeline.voice.synthesize.assert_not_called()
        from voice_effects import BILLY_FILTER_GRAPH, SERVITOR_HIGH_BASE_CHAIN
        billy, proximus = self.renders(pipeline)
        self.assertIn(BILLY_FILTER_GRAPH, billy)
        graph = proximus[proximus.index('-filter_complex') + 1]
        self.assertTrue(graph.startswith('[0:a]' + SERVITOR_HIGH_BASE_CHAIN + 'aresample=24000'))
        self.assertIn('flanger', graph)                 # the machine DSP stays on top

    def test_without_thorsten_high_everything_stays_as_before(self):
        pipeline = ss.RealPipeline(self.tmp.name)
        with unittest.mock.patch('voice_controls._synthesize_voice',
                                 side_effect=lambda v, t, out, p: self.write(out)) as synth:
            pipeline.synthesize('Gemerkt.', 'natural').unlink(missing_ok=True)
        self.assertEqual(synth.call_args[0][3], 'servitor')
        from voice_effects import NATURAL_FILTER_GRAPH, SERVITOR_HIGH_BASE_CHAIN
        billy, proximus = self.renders(pipeline)
        self.assertIn(NATURAL_FILTER_GRAPH, billy)
        self.assertNotIn(SERVITOR_HIGH_BASE_CHAIN, ' '.join(proximus))
