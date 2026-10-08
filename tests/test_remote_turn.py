import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import servitor_server as ss  # noqa: E402
from test_servitor_server import FakePipeline, TOKEN  # noqa: E402
from ptt import Recorder, VoiceController  # noqa: E402
from remote_turn import (  # noqa: E402
    NO_FALLBACK_CODES, RemoteCapableSpeech, RemoteTurnJob, RemoteTurnUplink, load_remote_config,
)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ConfigTests(unittest.TestCase):
    def test_empty_url_means_local_only(self):
        self.assertIsNone(load_remote_config({}))
        self.assertIsNone(load_remote_config({'ASSISTANT_BASE_URL': ' , '}))

    def test_parses_url_list_and_defaults(self):
        config = load_remote_config({
            'ASSISTANT_BASE_URL': 'http://172.22.9.107:8765/, https://servitor.example.org',
            'ASSISTANT_TOKEN': TOKEN})
        self.assertEqual(config.base_urls,
                         ('http://172.22.9.107:8765', 'https://servitor.example.org'))
        self.assertEqual(config.hosts, ['172.22.9.107:8765', 'servitor.example.org'])
        self.assertEqual(config.audio_format, 'wav')
        self.assertNotIn(TOKEN, repr(config.hosts))

    def test_rejects_bad_settings(self):
        base = {'ASSISTANT_BASE_URL': 'http://host:8765', 'ASSISTANT_TOKEN': TOKEN}
        for change in ({'ASSISTANT_TOKEN': 'short'},
                       {'ASSISTANT_BASE_URL': 'ftp://host'},
                       {'ASSISTANT_AUDIO_FORMAT': 'mp3'},
                       {'ASSISTANT_CF_ACCESS_CLIENT_ID': 'only-id'},
                       {'ASSISTANT_CONNECT_TIMEOUT_SECONDS': '0'},
                       {'ASSISTANT_BASE_URL': 'http://host:notaport'},
                       {'ASSISTANT_BASE_URL': 'http://host:99999'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                load_remote_config({**base, **change})


class LiveServerCase(unittest.TestCase):
    """Real servitor_server over loopback with the fake model pipeline."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config({'SERVITOR_API_TOKEN': TOKEN, 'SERVITOR_MAX_AUDIO_SECONDS': '2',
                            'SERVITOR_RATE_LIMIT_PER_MINUTE': '50',
                            'SERVITOR_WORKDIR': self.tmp.name})
        self.pipeline = FakePipeline(self.tmp.name)
        self.service = ss.Service(config, self.pipeline)
        self.service.ready = True
        self.server = ss.Server(('127.0.0.1', 0), ss.Handler)
        self.server.service = self.service
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.client_dir = Path(self.tmp.name) / 'client'
        self.client_dir.mkdir()

    def config(self, urls=None, token=TOKEN, fmt='wav'):
        return load_remote_config({'ASSISTANT_BASE_URL': urls or self.url,
                                   'ASSISTANT_TOKEN': token, 'ASSISTANT_AUDIO_FORMAT': fmt,
                                   'ASSISTANT_RESPONSE_TIMEOUT_SECONDS': '5'})

    def turn(self, config=None, pcm=b'\1\0' * 8000, decode=None):
        uplink = RemoteTurnUplink(config or self.config())
        for start in range(0, len(pcm), 3200):
            uplink.accept_pcm(pcm[start:start + 3200])
        uplink.finish()
        kwargs = {} if decode is None else {'decode': decode}
        job = RemoteTurnJob(uplink, self.client_dir, **kwargs)
        self.assertTrue(job.done.wait(10))
        return job


class RemoteTurnTests(LiveServerCase):
    def test_streamed_turn_returns_playable_audio_and_progress(self):
        job = self.turn()
        self.assertIsNone(job.error)
        self.assertEqual(self.pipeline.accepted, 16000)
        self.assertEqual((job.transcript, job.reply, job.model),
                         ('wie hoch ist der eiffelturm', 'Antwort auf wie hoch ist der eiffelturm', 'test/model'))
        with wave.open(str(job.result['audio']), 'rb') as audio:
            self.assertEqual(audio.getframerate(), 48000)
        events = job.drain()
        self.assertEqual([e['stage'] for e in events if e['event'] == 'stage'],
                         ['recognize', 'think', 'synthesize', 'render'])
        audio_event = next(e for e in events if e['event'] == 'audio')
        self.assertNotIn('data', audio_event)
        self.assertIn('server_total', events[-1]['timings'])
        self.assertEqual(sorted(p.name for p in self.client_dir.iterdir()),
                         ['remote-reply.wav'])

    def test_opus_reply_is_decoded_before_playback(self):
        def decode(source, target):
            self.assertEqual(Path(source).suffix, '.part')
            Path(target).write_bytes(Path(source).read_bytes())  # fake: payload is WAV
        job = self.turn(self.config(fmt='opus'), decode=decode)
        self.assertIsNone(job.error)
        self.assertTrue(job.result['audio'].exists())

    def test_unreachable_first_url_falls_through_to_second(self):
        config = self.config(f'http://127.0.0.1:{free_port()},{self.url}')
        job = self.turn(config)
        self.assertIsNone(job.error)
        self.assertEqual(job.result['host'], self.url.split('//')[1])

    def test_unreachable_server_is_an_upload_error(self):
        uplink = RemoteTurnUplink(self.config(f'http://127.0.0.1:{free_port()}'))
        uplink.accept_pcm(b'\0' * 3200)
        uplink.finish()
        uplink.thread.join(5)
        self.assertIsNotNone(uplink.error)

    def test_llm_failure_keeps_transcript_for_local_resume(self):
        self.pipeline.fail_llm = True
        job = self.turn()
        self.assertEqual((job.error_stage, job.error_code), ('think', 'llm'))
        self.assertEqual(job.transcript, 'wie hoch ist der eiffelturm')
        self.assertTrue(job.fallback_allowed)

    def test_no_speech_is_not_retried_locally(self):
        self.pipeline.transcript = None
        job = self.turn()
        self.assertEqual(job.error_code, 'no_speech')
        self.assertFalse(job.fallback_allowed)

    def test_upload_limits_still_fall_back_locally(self):
        for code in ('too_short', 'too_large'):
            job = RemoteTurnJob.__new__(RemoteTurnJob)
            job.error, job.error_code = 'limit', code
            self.assertTrue(job.fallback_allowed)

    def test_stalled_connect_is_cancelled_on_timeout(self):
        release = threading.Event()

        class Stalled:
            def __init__(self, *args):
                release.wait(5)
                self.sock = Mock()
                self.closed = False

            def connect(self):
                pass

            def putrequest(self, *args, **kwargs):
                pass

            putheader = endheaders = putrequest

            def close(self):
                self.closed = True

        created = []
        uplink = RemoteTurnUplink(self.config(), connect=lambda *a: created.append(Stalled()) or created[-1])
        uplink.finish()
        with self.assertRaises(Exception):
            uplink.wait_uploaded(0.1)
        self.assertTrue(uplink.cancelled)
        release.set()
        uplink.thread.join(5)
        self.assertTrue(created[0].closed)
        self.assertIsNone(uplink.connection)

    def test_access_credentials_only_sent_over_https(self):
        sent = {}

        class Recording:
            def __init__(self, url):
                self.url, self.sock, self.headers = url, Mock(), {}
                sent[url] = self.headers

            def connect(self):
                pass

            def putrequest(self, *args, **kwargs):
                pass

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def close(self):
                pass

        env = {'ASSISTANT_TOKEN': TOKEN, 'ASSISTANT_CF_ACCESS_CLIENT_ID': 'id',
               'ASSISTANT_CF_ACCESS_CLIENT_SECRET': 'secret'}
        for url in ('http://172.22.9.107:8765', 'https://servitor.example.org'):
            config = load_remote_config({**env, 'ASSISTANT_BASE_URL': url})
            uplink = RemoteTurnUplink(config, connect=lambda u, _t: Recording(u))
            uplink.cancel()
            uplink.thread.join(5)
            uplink._request(url)
        self.assertNotIn('CF-Access-Client-Secret', sent['http://172.22.9.107:8765'])
        self.assertEqual(sent['https://servitor.example.org']['CF-Access-Client-Secret'],
                         'secret')
        self.assertIn('Authorization', sent['http://172.22.9.107:8765'])

    def test_wrong_token_is_reported(self):
        job = self.turn(self.config(token='y' * 40))
        self.assertEqual(job.error_code, 'unauthorized')
        self.assertTrue(job.fallback_allowed)

    def test_cancel_during_upload_stops_cleanly(self):
        uplink = RemoteTurnUplink(self.config())
        uplink.accept_pcm(b'\0' * 3200)
        uplink.cancel()
        uplink.thread.join(5)
        self.assertFalse(uplink.thread.is_alive())
        self.assertIsNone(uplink.error)
        self.assertIsNone(uplink.connection)


class RecorderUplinkTests(unittest.TestCase):
    def test_pump_streams_every_chunk_and_ends_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            uplink = Mock()
            recorder = Recorder(tmp, 'dev', 1)
            proc = Mock()
            proc.stdout.read.side_effect = [b'a' * 3200, b'b' * 100, b'']
            recorder._pump_live_audio(proc, None, uplink)
            self.assertEqual([c.args[0] for c in uplink.accept_pcm.call_args_list],
                             [b'a' * 3200, b'b' * 100])
            uplink.finish.assert_called_once()
            self.assertEqual(recorder.raw.stat().st_size, 3300)
            self.assertIsNone(recorder.take_live_transcript())

    def test_start_streams_through_pump_without_local_vosk(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(StringIO()) as out:
            uplink = Mock()
            recorder = Recorder(tmp, 'dev', 1, uplink_factory=lambda: uplink)
            with patch('ptt.subprocess.Popen') as popen, \
                    patch.object(Recorder, '_pump_live_audio'):
                recorder.start()
            command = popen.call_args.args[0]
            self.assertNotIn(str(recorder.raw), command)
            self.assertEqual(command[command.index('-r') + 1], '16000')
            self.assertIs(recorder.take_uplink(), uplink)
        started = json.loads(out.getvalue().splitlines()[-1])
        self.assertEqual((started['event'], started['stt'], started['remote']),
                         ('recording', 'remote', True))

    def test_local_live_vosk_only_runs_without_uplink(self):
        for uplink, expected_calls in ((Mock(), 0), (None, 1)):
            with self.subTest(remote=uplink is not None), tempfile.TemporaryDirectory() as tmp, \
                    redirect_stdout(StringIO()):
                factory = Mock(return_value=Mock())
                uplink_factory = None if uplink is None else (lambda: uplink)
                recorder = Recorder(tmp, 'dev', 1, live_vosk_factory=factory,
                                    uplink_factory=uplink_factory)
                with patch('ptt.subprocess.Popen'), patch.object(Recorder, '_pump_live_audio'):
                    recorder.start()
                self.assertEqual(factory.call_count, expected_calls)


class FakeJob:
    def __init__(self, **fields):
        self.done = threading.Event()
        self.done.set()
        self.cancelled = False
        self.error = self.error_stage = self.error_code = None
        self.transcript = self.reply = self.model = None
        self.result = None
        self.events = []
        self.__dict__.update(fields)

    def drain(self):
        events, self.events = self.events, []
        return events

    @property
    def fallback_allowed(self):
        return self.error is not None and self.error_code not in NO_FALLBACK_CODES


class RemoteControllerTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.recorder.take_live_transcript.return_value = None
        self.speech.active = False
        self.speech.poll.return_value = None
        self.c = VoiceController(self.recorder, self.speech, .04, 30, remote=True)
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.progress = Path(self.tmp.name) / 'display-progress.json'
        env = patch.dict(os.environ, {'PI_DISPLAY_PROGRESS_FILE': str(self.progress),
                                      'PTT_DISPLAY_EVENT_PATH':
                                          str(Path(self.tmp.name) / 'display-event.json')})
        env.start()
        self.addCleanup(env.stop)

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()
                if line.startswith('{')]

    def finish(self, job):
        self.c.job, self.c.job_stage = job, 'remote'
        self.c.remote_capture = Path('/tmp/capture.wav')
        self.c.tick(False, (False,) * 5, 1.0)

    def test_submit_hands_healthy_uplink_to_remote_job(self):
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        uplink = Mock(error=None)
        self.recorder.take_uplink.return_value = uplink
        with patch('ptt.RemoteTurnJob') as job:
            self.c.submit('release')
        job.assert_called_once_with(uplink, Path('/tmp'))
        self.assertEqual(self.c.job_stage, 'remote')

    def test_failed_upload_falls_back_to_local_stt(self):
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        self.recorder.take_uplink.return_value = Mock(error='connection refused', rejection=None)
        with patch('ptt.TranscriptionJob') as job:
            self.c.submit('release')
        job.assert_called_once()
        self.assertEqual(self.c.job_stage, 'stt')
        names = [e['event'] for e in self.events()]
        self.assertIn('remote_fallback', names)

    def test_early_rejection_keeps_its_code(self):
        from remote_turn import RemoteTurnError
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        self.recorder.take_uplink.return_value = Mock(
            error='Broken pipe',
            rejection=RemoteTurnError('upload', 'unauthorized', 'HTTP 401 unauthorized'))
        with patch('ptt.TranscriptionJob') as job:
            self.c.submit('release')
        job.assert_called_once()  # local fallback still runs
        error = next(e for e in self.events() if e['event'] == 'remote_error')
        self.assertEqual((error['code'], error['message']),
                         ('unauthorized', 'HTTP 401 unauthorized'))

    def test_progress_maps_to_display_and_audio_is_played(self):
        job = FakeJob(result=dict(audio=Path('/tmp/remote-reply.wav'), host='h'), events=[
            dict(event='stage', stage='recognize'),
            dict(event='transcript', text='hallo'),
            dict(event='stage', stage='think'),
            dict(event='reply', text='Antwort', model='m'),
            dict(event='stage', stage='synthesize'),
            dict(event='stage', stage='render'),
            dict(event='done', timings={'server_total': 1.9}),
        ])
        self.finish(job)
        names = [e['event'] for e in self.events()]
        for name in ('transcript', 'llm_start', 'llm_response', 'speech_started', 'latency'):
            self.assertIn(name, names)
        self.assertLess(names.index('transcript'), names.index('llm_response'))
        self.speech.play.assert_called_once_with(Path('/tmp/remote-reply.wav'))
        progress = json.loads(self.progress.read_text())
        self.assertEqual((progress['stage'], progress['metric']), ('tts', 'playback'))

    def test_llm_failure_resumes_with_local_llm(self):
        job = FakeJob(error='timeout', error_stage='think', error_code='llm', transcript='hallo')
        with patch('ptt.TranscriptionJob') as worker:
            self.finish(job)
        worker.assert_called_once()
        self.assertEqual(worker.call_args.args[1], 'hallo')
        self.assertEqual(self.c.job_stage, 'llm')

    def test_tts_failure_speaks_reply_locally(self):
        job = FakeJob(error='dsp', error_stage='render', error_code='dsp',
                      transcript='hallo', reply='Antwort')
        self.finish(job)
        self.speech.start.assert_called_once_with('Antwort')
        self.speech.play.assert_not_called()

    def test_no_speech_is_an_stt_error_without_fallback(self):
        job = FakeJob(error='Vosk returned no transcript', error_stage='recognize',
                      error_code='no_speech')
        with patch('ptt.TranscriptionJob') as worker:
            self.finish(job)
        worker.assert_not_called()
        self.assertIn('stt_error', [e['event'] for e in self.events()])

    def test_internal_server_error_falls_back_to_local_stt(self):
        job = FakeJob(error='boom', error_stage='internal', error_code='internal')
        with patch('ptt.TranscriptionJob') as worker:
            self.finish(job)
        worker.assert_called_once()
        self.assertEqual(self.c.job_stage, 'stt')

    def test_cancel_drops_open_uplink(self):
        self.c.cancel(False, 1.0)
        self.recorder.drop_uplink.assert_called_once()


class PlaybackTests(unittest.TestCase):
    def test_play_stops_local_speech_and_reports_exit(self):
        speech = Mock(active=False)
        speech.poll.return_value = None
        player = Mock()
        player.poll.side_effect = [None, None, 0]
        popen = Mock(return_value=player)
        out = RemoteCapableSpeech(speech, 'plughw:test', popen=popen)
        out.play(Path('/tmp/reply.wav'))
        speech.stop.assert_called_once()
        self.assertEqual(popen.call_args.args[0],
                         ['/usr/bin/aplay', '-q', '-D', 'plughw:test', '/tmp/reply.wav'])
        self.assertTrue(out.active)
        self.assertIsNone(out.poll())
        self.assertEqual(out.poll(), 0)
        self.assertIsNone(out.player)

    def test_start_delegates_text_to_local_speech(self):
        speech = Mock()
        out = RemoteCapableSpeech(speech, 'dev', popen=Mock())
        out.start('Hallo')
        speech.start.assert_called_once_with('Hallo')



class DisplayStatusTests(RemoteControllerTests):
    def status(self):
        path = Path(self.tmp.name) / 'display-status.json'
        return json.loads(path.read_text())

    def setUp(self):
        super().setUp()
        import ptt
        ptt._display_status.clear()
        env = patch.dict(os.environ, {'PTT_DISPLAY_STATUS_PATH':
                                      str(Path(self.tmp.name) / 'display-status.json')})
        env.start()
        self.addCleanup(env.stop)

    def test_server_turn_publishes_route_and_last_answer(self):
        self.recorder.finish.return_value = Path('/tmp/capture.wav')
        self.recorder.take_uplink.return_value = Mock(error=None)
        with patch('ptt.RemoteTurnJob'):
            self.c.submit('release')
        self.assertEqual(self.status()['route'], 'server')
        job = FakeJob(result=dict(audio=Path('/tmp/r.wav'), host='h'), events=[
            dict(event='reply', text='Antwort', model='local/qwen3-4b')])
        self.finish_keep_release(job)
        status = self.status()
        self.assertEqual((status['last_route'], status['last_llm']), ('server', 'offline'))
        self.assertIsInstance(status['last_latency_ms'], int)
        self.assertNotIn('Antwort', json.dumps(status))

    def finish_keep_release(self, job):
        self.c.job, self.c.job_stage = job, 'remote'
        self.c.remote_capture = Path('/tmp/capture.wav')
        self.c.tick(False, (False,) * 5, 1.0)

    def test_fallback_switches_route_to_pi(self):
        import ptt
        ptt.publish_display_status(last_llm='offline')  # from an earlier turn
        self.c.turn_released_at = 0.0
        job = FakeJob(error='dsp', error_stage='render', error_code='dsp',
                      transcript='hallo', reply='Antwort', model='openai/x')
        self.finish_keep_release(job)
        status = self.status()
        self.assertEqual((status['route'], status['last_route']), ('pi', 'pi'))
        self.assertNotIn('last_llm', status)  # no stale value from an earlier turn

    def test_volume_status_fields(self):
        import ptt
        ptt.publish_display_status(volume=35, volume_limit='max', volume_at=1000.5)
        status = self.status()
        self.assertEqual((status['volume'], status['volume_limit'], status['volume_at']),
                         (35, 'max', 1000.5))
        ptt.publish_display_status(volume=101, volume_limit=None, volume_at=True)
        status = self.status()
        self.assertEqual(status['volume'], 35)
        self.assertNotIn('volume_limit', status)

    def test_publish_rejects_unknown_fields_and_values(self):
        import ptt
        ptt.publish_display_status(route='mars', last_latency_ms=-1, text='geheim',
                                   last_llm='offline')
        self.assertEqual({k: v for k, v in self.status().items()
                          if k not in ('version', 'timestamp')}, {'last_llm': 'offline'})


class LocalIntentTests(RemoteControllerTests):
    def test_local_fallback_answers_time_without_llm(self):
        job = FakeJob(error='timeout', error_stage='think', error_code='llm',
                      transcript='wie spät ist es')
        with patch('ptt.TranscriptionJob') as worker:
            self.finish(job)
        worker.assert_not_called()  # no LLM job
        spoken = self.speech.start.call_args.args[0]
        self.assertTrue(spoken.startswith('Zeitindex:'))
        response = next(e for e in self.events() if e['event'] == 'llm_response')
        self.assertEqual(response['model'], 'local/intent')

    def test_uplink_sends_status_snapshot_header(self):
        sent = {}

        class Recording:
            def __init__(self, url):
                self.sock = Mock()

            def connect(self):
                pass

            def putrequest(self, *args, **kwargs):
                pass

            def putheader(self, name, value):
                sent[name] = value

            def endheaders(self):
                pass

            def close(self):
                pass

        config = load_remote_config({'ASSISTANT_BASE_URL': 'http://h:1', 'ASSISTANT_TOKEN': 'x' * 40})
        uplink = RemoteTurnUplink(config, connect=lambda u, _t: Recording(u),
                                  status={'battery_pct': 83})
        uplink.cancel()
        uplink.thread.join(5)
        uplink._request('http://h:1')
        self.assertEqual(json.loads(sent['X-Servitor-Status']), {'battery_pct': 83})


if __name__ == '__main__':
    unittest.main()
