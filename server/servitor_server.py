#!/usr/bin/env python3
"""Servitor LAN voice service: PCM upload -> Vosk -> OpenRouter -> Piper -> DSP.

The Pi keeps recording, buttons, display and playback. This service keeps the
Vosk and Piper models resident on a stronger host and returns the finished
Servitor audio. It reuses the Pi modules from ``src`` so the voice, speaker,
synthesis parameters and the PR #26 reference effect graph stay identical.

Protocol (HTTP/1.1, bearer token on every /v1 request):

* ``POST /v1/turn?format=wav|opus`` with raw 16 kHz mono s16le PCM as body
  (Content-Length or chunked; chunks are recognized while they arrive).
* ``POST /v1/speak?format=wav|opus`` with JSON ``{"text": "..."}``.
* ``GET /health`` without token: ``{"ok": true, "ready": bool}`` only.

Responses of /v1 are NDJSON events: ``stage`` (recognize, think, synthesize,
render), ``transcript``, ``reply``, ``audio`` (base64), ``done`` (timings) or
``error``. Only one turn runs at a time; a second one gets HTTP 503.
"""
import base64
import collections
import hmac
import http.server
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import wave

SRC = Path(os.environ.get('SERVITOR_SRC', Path(__file__).resolve().parent.parent / 'src'))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PCM_RATE = 16000
FORMATS = {'wav': 'audio/wav', 'opus': 'audio/ogg'}


class TurnError(Exception):
    def __init__(self, stage, code, message):
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


def _temporary_wav(prefix, directory):
    fd, name = tempfile.mkstemp(prefix=prefix, suffix='.wav', dir=directory)
    os.close(fd)  # reopened by name; never keep the mkstemp descriptor
    return Path(name)


def _env_int(name, default, minimum, maximum):
    value = int(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


class Config:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.token = env.get('SERVITOR_API_TOKEN', '').strip()
        if len(self.token) < 32:
            raise ValueError('SERVITOR_API_TOKEN must be set (at least 32 characters)')
        self.bind = env.get('SERVITOR_BIND', '0.0.0.0')
        self.port = int(env.get('SERVITOR_PORT', '8765'))
        self.max_seconds = int(env.get('SERVITOR_MAX_AUDIO_SECONDS', '30'))
        self.max_text = int(env.get('SERVITOR_MAX_TEXT_CHARS', '1200'))
        self.idle_timeout = float(env.get('SERVITOR_UPLOAD_IDLE_TIMEOUT', '10'))
        self.rate_limit = int(env.get('SERVITOR_RATE_LIMIT_PER_MINUTE', '20'))
        self.workdir = env.get('SERVITOR_WORKDIR') or tempfile.gettempdir()
        # CF-Connecting-IP is only believed from these peers (local cloudflared).
        self.trusted_proxies = {
            value.strip() for value in
            env.get('SERVITOR_TRUSTED_PROXIES', '127.0.0.1,::1').split(',') if value.strip()}

    @property
    def max_bytes(self):
        return PCM_RATE * 2 * self.max_seconds


class RealPipeline:
    """Resident models; every method is called under the service turn lock."""

    def __init__(self, workdir, clock=time.monotonic):
        self.workdir = workdir
        self.voice = None
        self.clock = clock
        # After an OpenRouter failure, go straight to the local model for a
        # while instead of paying the full timeout on every turn of an outage.
        self.openrouter_retry_at = 0.0

    def load(self):
        import transcribe
        transcribe._load_vosk_model()
        model = os.environ['SERVITOR_PIPER_MODEL']
        from piper import PiperVoice
        self.voice = PiperVoice.load(model)
        # Warm the ONNX session once so the first real turn pays no setup cost.
        self.synthesize('Bereit.').unlink()
        if os.environ.get('SERVITOR_LOCAL_LLM') == '1':
            # Page the GGUF in and cache the system prompt; never fatal.
            from llm import LLMError, generate_local_reply
            try:
                generate_local_reply('Bereit?')
            except LLMError as exc:
                print(json.dumps(dict(event='local_llm_warmup_failed', error=str(exc))),
                      flush=True)

    def recognizer(self):
        from transcribe import LiveVoskRecognizer
        return LiveVoskRecognizer()

    def reply(self, text):
        """OpenRouter first; on any LLM error (offline, no credits, timeout)
        the resident llama.cpp server answers when SERVITOR_LOCAL_LLM=1."""
        from llm import LLMError, generate_local_reply, generate_reply
        local = os.environ.get('SERVITOR_LOCAL_LLM') == '1'
        if local and self.clock() < self.openrouter_retry_at:
            primary = 'OpenRouter skipped after a recent failure'
        else:
            try:
                return generate_reply(text)
            except LLMError as exc:
                if not local:
                    raise
                primary = str(exc)
                retry = float(os.environ.get('SERVITOR_OPENROUTER_RETRY_SECONDS', '60'))
                self.openrouter_retry_at = self.clock() + retry
        print(json.dumps(dict(event='llm_fallback', reason=primary)), flush=True)
        try:
            return generate_local_reply(text)
        except LLMError as exc:
            raise LLMError(f'{primary}; local fallback failed: {exc}') from exc

    def synthesize(self, text):
        from voice_controls import _synthesize_voice
        target = _temporary_wav('syn-', self.workdir)
        try:
            with wave.open(str(target), 'wb') as output:
                _synthesize_voice(self.voice, text, output, 'servitor')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def render(self, source):
        from voice_effects import build_render_command
        target = _temporary_wav('dsp-', self.workdir)
        try:
            subprocess.run(build_render_command(source, target), check=True,
                           timeout=60, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def encode(self, source, fmt):
        if fmt == 'wav':
            return Path(source).read_bytes()
        command = [os.environ.get('TTS_FFMPEG_BIN', '/usr/bin/ffmpeg'), '-hide_banner',
                   '-loglevel', 'error', '-nostdin', '-i', str(source),
                   '-c:a', 'libopus', '-b:a', os.environ.get('SERVITOR_OPUS_BITRATE', '64k'),
                   '-f', 'ogg', 'pipe:1']
        return subprocess.run(command, check=True, timeout=60, capture_output=True).stdout


def wav_duration_ms(path):
    with wave.open(str(path), 'rb') as audio:
        return round(audio.getnframes() * 1000 / audio.getframerate())


class RateLimiter:
    def __init__(self, per_minute, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self.hits = collections.defaultdict(collections.deque)
        self.lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self.lock:
            hits = self.hits[key]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True


class Service:
    def __init__(self, config, pipeline):
        self.config, self.pipeline = config, pipeline
        self.turn_lock = threading.Lock()
        self.limiter = RateLimiter(config.rate_limit)
        self.ready = False

    def authorized(self, header):
        expected = f'Bearer {self.config.token}'.encode()
        return hmac.compare_digest((header or '').encode(), expected)

    def run_turn(self, pcm_chunks, emit, fmt, text=None):
        """Drive one turn. ``pcm_chunks`` is consumed only when ``text`` is None."""
        timings = {}
        temporary = []

        def timed(name, function, *args):
            started = time.monotonic()
            try:
                return function(*args)
            finally:
                timings[name] = round(time.monotonic() - started, 3)

        try:
            if text is None:
                try:
                    recognizer = self.pipeline.recognizer()
                except Exception as exc:
                    raise TurnError('recognize', 'stt', str(exc)) from exc
                pending = b''
                received = 0
                for chunk in pcm_chunks:
                    received += len(chunk)
                    if received > self.config.max_bytes:
                        raise TurnError('upload', 'too_large', 'audio exceeds limit')
                    pending += chunk
                    usable = len(pending) - len(pending) % 2
                    if usable:
                        recognizer.accept_pcm(pending[:usable])
                        pending = pending[usable:]
                upload_end = time.monotonic()
                if received < PCM_RATE * 2 // 5:
                    raise TurnError('upload', 'too_short', 'audio shorter than 0.2 s')
                emit(dict(event='stage', stage='recognize'))
                try:
                    text = timed('stt_finalize', recognizer.finish)
                except Exception as exc:
                    raise TurnError('recognize', 'no_speech', str(exc)) from exc
                emit(dict(event='transcript', text=text))
                emit(dict(event='stage', stage='think'))
                try:
                    answer, model = timed('llm', self.pipeline.reply, text)
                except Exception as exc:
                    raise TurnError('think', 'llm', str(exc)) from exc
                emit(dict(event='reply', text=answer, model=model))
            else:
                upload_end = time.monotonic()
                answer = text
            answer = answer.strip()[:self.config.max_text]
            emit(dict(event='stage', stage='synthesize'))
            try:
                raw = timed('synthesis', self.pipeline.synthesize, answer)
                temporary.append(raw)
            except Exception as exc:
                raise TurnError('synthesize', 'tts', str(exc)) from exc
            emit(dict(event='stage', stage='render'))
            try:
                rendered = timed('render', self.pipeline.render, raw)
                temporary.append(rendered)
                data = timed('encode', self.pipeline.encode, rendered, fmt)
                duration = wav_duration_ms(rendered)
            except Exception as exc:
                raise TurnError('render', 'dsp', str(exc)) from exc
            timings['server_total'] = round(time.monotonic() - upload_end, 3)
            emit(dict(event='audio', format=fmt, mime=FORMATS[fmt], duration_ms=duration,
                      bytes=len(data), data=base64.b64encode(data).decode('ascii')))
            emit(dict(event='done', timings=timings))
        finally:
            for path in temporary:
                Path(path).unlink(missing_ok=True)


def read_chunked(stream, limit):
    """Yield the decoded body of a chunked request, refusing oversized bodies."""
    total = 0
    while True:
        line = stream.readline(66)
        if not line.endswith(b'\n'):
            raise TurnError('upload', 'bad_request', 'malformed chunk header')
        try:
            size = int(line.split(b';', 1)[0].strip(), 16)
        except ValueError:
            raise TurnError('upload', 'bad_request', 'malformed chunk size') from None
        if size < 0:
            raise TurnError('upload', 'bad_request', 'malformed chunk size')
        if size == 0:
            while stream.readline(1024) not in (b'\r\n', b'\n', b''):
                pass
            return
        total += size
        if total > limit:
            raise TurnError('upload', 'too_large', 'audio exceeds limit')
        data = stream.read(size)
        if len(data) != size:
            raise TurnError('upload', 'bad_request', 'truncated chunk')
        if stream.readline(4) not in (b'\r\n', b'\n'):
            raise TurnError('upload', 'bad_request', 'missing chunk terminator')
        yield data


def read_length(stream, length, block=8192):
    remaining = length
    while remaining:
        data = stream.read(min(block, remaining))
        if not data:
            raise TurnError('upload', 'bad_request', 'truncated body')
        remaining -= len(data)
        yield data


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ServitorVoice/1'
    sys_version = ''

    @property
    def service(self):
        return self.server.service

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))

    def _client(self):
        # Behind a trusted local cloudflared the header names the real client;
        # any other peer could forge it, so LAN requests use the socket peer.
        peer = self.client_address[0]
        forwarded = self.headers.get('CF-Connecting-IP', '').strip()
        if forwarded and peer in self.service.config.trusted_proxies:
            return forwarded
        return peer

    def _json(self, status, payload, extra=None):
        body = (json.dumps(payload) + '\n').encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split('?', 1)[0] == '/health':
            self._json(200, dict(ok=True, ready=self.service.ready))
        else:
            self._json(404, dict(error='not found'))

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path not in ('/v1/turn', '/v1/speak'):
            self.close_connection = True
            return self._json(404, dict(error='not found'))
        self.close_connection = True  # never reuse a connection with an unread body
        self.connection.settimeout(self.service.config.idle_timeout)
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        if not self.service.ready:
            return self._json(503, dict(error='loading'), {'Retry-After': '5'})
        fmt = urllib.parse.parse_qs(url.query).get('format', ['wav'])[0]
        if fmt not in FORMATS:
            return self._json(400, dict(error='format must be wav or opus'))

        limit = self.service.config.max_bytes
        chunked = 'chunked' in self.headers.get('Transfer-Encoding', '').lower()
        length = self.headers.get('Content-Length')
        if not chunked:
            try:
                length = int(length)
            except (TypeError, ValueError):
                return self._json(411, dict(error='length required'))
            if not 0 <= length <= (limit if url.path == '/v1/turn' else 65536):
                return self._json(413, dict(error='body too large'))

        text = None
        if url.path == '/v1/speak':
            if chunked:
                return self._json(411, dict(error='length required'))
            try:
                raw = self.rfile.read(length)
            except (OSError, TimeoutError):
                return
            if len(raw) != length:
                return self._json(400, dict(error='truncated body'))
            try:
                text = json.loads(raw)['text']
            except (ValueError, KeyError, TypeError):
                text = None
            if not isinstance(text, str):
                return self._json(400, dict(error='expected JSON {"text": "..."}'))
            text = text.strip()
            if not text:
                return self._json(400, dict(error='text must not be empty'))
            body = iter(())
        else:
            body = read_chunked(self.rfile, limit) if chunked else read_length(self.rfile, length)

        if not self.service.turn_lock.acquire(blocking=False):
            return self._json(503, dict(error='busy'), {'Retry-After': '2'})
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/x-ndjson')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()

            def emit(payload):
                data = (json.dumps(payload, ensure_ascii=False) + '\n').encode()
                self.wfile.write(b'%x\r\n%s\r\n' % (len(data), data))
                self.wfile.flush()

            try:
                self.service.run_turn(body, emit, fmt, text=text)
            except TurnError as exc:
                emit(dict(event='error', stage=exc.stage, code=exc.code, message=exc.message))
            except (OSError, TimeoutError):
                raise
            except Exception as exc:  # report, keep serving
                emit(dict(event='error', stage='internal', code='internal', message=str(exc)))
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except (OSError, TimeoutError) as exc:
            self.log_message('turn aborted: %s', exc)
        finally:
            self.service.turn_lock.release()


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    config = Config()
    os.environ.setdefault('TTS_SERVITOR_AURA', 'reference')
    os.environ.setdefault('TTS_VOICE_PROFILE', 'servitor')
    pipeline = RealPipeline(config.workdir)
    service = Service(config, pipeline)
    server = Server((config.bind, config.port), Handler)
    server.service = service

    def load():
        started = time.monotonic()
        try:
            pipeline.load()
        except BaseException as exc:
            print(json.dumps(dict(event='load_failed', error=str(exc))), flush=True)
            os._exit(1)  # let systemd restart the service
        service.ready = True
        print(json.dumps(dict(event='ready', load_seconds=round(time.monotonic() - started, 3))),
              flush=True)

    threading.Thread(target=load, daemon=True).start()
    print(json.dumps(dict(event='listening', bind=config.bind, port=config.port)), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
