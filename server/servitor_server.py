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
* ``POST /v1/hello`` with an SPX/1 ``hello`` (src/protocol.py): opens a
  session, answered with ``welcome``. A turn naming the session in
  ``X-Servitor-Session`` may send only the digest of a memory core the
  session already holds.
* ``POST /v1/message`` with one SPX/1 envelope, answered with ``ack``.
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
import urllib.error
import urllib.parse
import urllib.request
import wave

SRC = Path(os.environ.get('SERVITOR_SRC', Path(__file__).resolve().parent.parent / 'src'))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import intents  # noqa: E402
import weather  # noqa: E402
import agenda  # noqa: E402
import device_control  # noqa: E402
import enroll  # noqa: E402
import logwatch  # noqa: E402
import maintenance  # noqa: E402
import memory  # noqa: E402
import protocol  # noqa: E402
import speaker  # noqa: E402
from llm import NO_MEMORY  # noqa: E402
from mood import emergency, split_tag  # noqa: E402
from system_status import phrase_style, sanitize_snapshot  # noqa: E402

PCM_RATE = 16000
HANDLED = ('ping',)   # SPX/1 types /v1/message acts on; more come with later steps
FORMATS = {'wav': 'audio/wav', 'opus': 'audio/ogg'}
# TurnError codes caused by the input, not by the server (logwatch ignores them).
OPERATOR_CODES = ('no_speech', 'too_short', 'too_large', 'bad_request')


def device_mood(device, text=''):
    """The Pi's mood for the prompt; None when its feelings are off (or an old Pi).
    The Pi allows refusing before it knows the words; a cry for help never is."""
    device = device or {}
    if 'mood' not in device:
        return None
    return dict(emotion=device['mood'], level=device.get('mood_level', 0),
                refuse=device.get('mood_refuse') == 'on' and not emergency(text))


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
        # CT 107 runs on UTC; spoken times and dates are for the user's clock.
        self.timezone = env.get('SERVITOR_TIMEZONE', 'Europe/Berlin')
        # Wait briefly instead of refusing: a refused turn falls back to the
        # much slower Pi. Short /v1/speak jobs and a restart (~10 s) fit in.
        self.busy_wait = float(env.get('SERVITOR_BUSY_WAIT_SECONDS', '8'))
        self.loading_wait = float(env.get('SERVITOR_LOADING_WAIT_SECONDS', '15'))
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
        # Same for Billy's RVC voice (server/rvc_worker.py).
        self.rvc_retry_at = 0.0

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

    def llm_state(self):
        """'offline' while OpenRouter is being skipped after a failure."""
        if os.environ.get('SERVITOR_LOCAL_LLM') == '1' and self.clock() < self.openrouter_retry_at:
            return 'offline'
        return 'openrouter'

    def reply(self, text, lore=None, mode=None, memory=NO_MEMORY, persona=None, mood=None):
        """OpenRouter first; on any LLM error (offline, no credits, timeout)
        the resident llama.cpp server answers when SERVITOR_LOCAL_LLM=1."""
        from llm import LLMError, free_model, generate_local_reply, generate_reply
        local = os.environ.get('SERVITOR_LOCAL_LLM') == '1'
        # "FREI": the low-restriction model; otherwise the configured one.
        chosen = free_model() if mode == 'free' else None
        if local and mode == 'local':
            # Operator chose "Sprachkern LOKAL" on the Pi: never call OpenRouter.
            return generate_local_reply(text, lore=lore, memory=memory, persona=persona,
                                        mood=mood)
        if local and self.clock() < self.openrouter_retry_at:
            primary = 'OpenRouter skipped after a recent failure'
        else:
            try:
                return generate_reply(text, lore=lore, memory=memory, model=chosen,
                                      persona=persona, mood=mood)
            except LLMError as exc:
                if not local:
                    raise
                primary = str(exc)
                retry = float(os.environ.get('SERVITOR_OPENROUTER_RETRY_SECONDS', '60'))
                self.openrouter_retry_at = self.clock() + retry
        print(json.dumps(dict(event='llm_fallback', reason=primary)), flush=True)
        try:
            return generate_local_reply(text, lore=lore, memory=memory, persona=persona,
                                        mood=mood)
        except LLMError as exc:
            raise LLMError(f'{primary}; local fallback failed: {exc}') from exc

    def synthesize(self, text, voice='servitor'):
        """Same speaker for both voice effects; only the rendering differs."""
        from voice_controls import _synthesize_voice
        target = _temporary_wav('syn-', self.workdir)
        try:
            with wave.open(str(target), 'wb') as output:
                _synthesize_voice(self.voice, text, output, 'servitor')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def convert(self, source):
        """Billy's own voice: Piper's WAV through the RVC worker
        (server/rvc_worker.py, SERVITOR_RVC_URL). Returns the converted WAV, or
        None when RVC is off or fails; the turn then keeps the plain natural
        voice and skips RVC for SERVITOR_RVC_RETRY_SECONDS."""
        url = os.environ.get('SERVITOR_RVC_URL', '').strip().rstrip('/')
        if not url or self.clock() < self.rvc_retry_at:
            return None
        timeout = float(os.environ.get('SERVITOR_RVC_TIMEOUT_SECONDS', '10'))
        target = _temporary_wav('rvc-', self.workdir)
        try:
            request = urllib.request.Request(
                f'{url}/v1/convert', data=Path(source).read_bytes(), method='POST',
                headers={'Content-Type': 'audio/wav'})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                target.write_bytes(response.read())
            wav_duration_ms(target)  # a broken reply fails here, not in FFmpeg
            return target
        except (OSError, ValueError, EOFError, ZeroDivisionError, wave.Error) as exc:
            target.unlink(missing_ok=True)
            retry = float(os.environ.get('SERVITOR_RVC_RETRY_SECONDS', '60'))
            self.rvc_retry_at = self.clock() + retry
            print(json.dumps(dict(event='rvc_fallback', reason=str(exc))), flush=True)
            return None

    def render(self, source, voice='servitor'):
        from voice_effects import build_render_command
        target = _temporary_wav('dsp-', self.workdir)
        try:
            subprocess.run(build_render_command(source, target, effect=voice), check=True,
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
        self.updates = None  # sysmon.pending_updates(), refreshed in the background
        self.embedder = speaker.Embedder()
        self.maintenance_dir = maintenance.DIR
        self.maintenance_at = None  # last accepted maintenance request (monotonic)
        self.weather = weather.Forecast()  # WEATHER_LAT/WEATHER_LON, else no weather
        self.logwatch = None  # logwatch.server_watch(), started by main()
        self.sessions = protocol.Sessions()
        self.seen = protocol.Seen()
        self.ready = False

    def authorized(self, header):
        expected = f'Bearer {self.config.token}'.encode()
        return hmac.compare_digest((header or '').encode(), expected)

    def now(self):
        import datetime
        import zoneinfo
        try:
            return datetime.datetime.now(zoneinfo.ZoneInfo(self.config.timezone))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            return datetime.datetime.now()

    @staticmethod
    def _maintenance_reply(op, device):
        active = device.get('maintenance') == 'on'
        if op == 'enter':
            return maintenance.ENTER_TEXT
        if op == 'exit':
            return maintenance.EXIT_TEXT
        if op in maintenance.DENIED:
            return maintenance.DENIED_TEXT
        if not active:
            return maintenance.NEED_MODE_TEXT
        return maintenance.confirm_prompt(op, device)

    @staticmethod
    def _device_reply(text, device, memory_copy, emit):
        """WLAN/reboot/shutdown by voice (device_control): the sentence, or None.
        The Pi acts on the 'device' event; only Pis that know it (devctl) get one."""
        if device.get('devctl') != 'on':
            return None
        style = phrase_style(device.get('lore'), device.get('persona'))
        pending = device.get('pending')
        answer = device_control.answer(text) if pending else None
        op = device_control.command(text)
        if op == 'reboot' and device.get('maintenance') == 'on':
            op = None   # maintenance mode: "starte neu" is its action, confirmed with E
        if answer is None and op is None:
            return None
        if isinstance(memory_copy, dict) and memory.unknown_speaker(memory_copy):
            # Short commands often miss the voice threshold: no spoken
            # confirmation then, but button E at the device still works.
            if answer == 'confirm' or op in device_control.CONFIRM_OPS:
                target = pending if answer == 'confirm' else op
                emit(dict(event='device', op=target))   # (re)asks; the Pi waits for E
                return device_control.button_only_text(target, style)
        if answer == 'confirm':
            emit(dict(event='device', op=pending, confirm=True))
            return device_control.start_text(pending, style)
        if answer == 'cancel':
            return device_control.cancelled_text(style)
        emit(dict(event='device', op=op))
        return device_control.reply(op, device.get('wlan'), style)

    def _identify(self, audio, memory_copy, emit):
        """Compare the turn's voice with the enrolled voiceprint; returns the
        memory copy the turn may use (guest view for an unknown voice)."""
        prints = [dict(name=p['name'], vector=speaker.decode(p['print']))
                  for p in memory_copy.get('voiceprints', [])]
        prints = [p for p in prints if p['vector']]
        if not prints or not self.embedder.available:
            return memory_copy
        try:
            embedding = self.embedder.embed(bytes(audio))
        except Exception as exc:  # never fail a turn over speaker recognition
            print(json.dumps(dict(event='speaker_error', message=str(exc))), flush=True)
            return memory_copy
        if embedding is None:  # very short: no reliable decision, treat as operator
            return memory_copy
        name, score = speaker.identify(embedding, prints)
        emit(dict(event='speaker', known=name is not None,
                  score=None if score is None else round(score, 3)))
        if name is None:
            return memory.guest_view(memory_copy)
        return dict(memory_copy, speaker=name)

    def run_turn(self, pcm_chunks, emit, fmt, text=None, device=None, memory_copy=NO_MEMORY,
                 transcribe_only=False, appointments=None):
        """Drive one turn. ``pcm_chunks`` is consumed only when ``text`` is None.

        ``device`` is the Pi's sanitized status snapshot; questions such as
        time, date or status are answered from it without the LLM.
        ``memory_copy``: the Pi's memory core (None: stick absent, NO_MEMORY:
        Pi without memory support). Changes go back as ``memory`` events.
        ``appointments``: today's calendar from the Pi (agenda.decode_header), or None."""
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
                # Keep the audio only when a voice must be recognized.
                keep = isinstance(memory_copy, dict) and bool(memory_copy.get('voiceprints'))
                audio = bytearray()
                for chunk in pcm_chunks:
                    received += len(chunk)
                    if received > self.config.max_bytes:
                        raise TurnError('upload', 'too_large', 'audio exceeds limit')
                    if keep:
                        audio += chunk
                    pending += chunk
                    usable = len(pending) - len(pending) % 2
                    if usable:
                        try:
                            recognizer.accept_pcm(pending[:usable])
                        except Exception as exc:
                            raise TurnError('recognize', 'stt', str(exc)) from exc
                        pending = pending[usable:]
                upload_end = time.monotonic()
                if received < PCM_RATE * 2 // 5:
                    raise TurnError('upload', 'too_short', 'audio shorter than 0.2 s')
                emit(dict(event='stage', stage='recognize'))
                try:
                    text = timed('stt_finalize', recognizer.finish)
                except Exception as exc:
                    # Only an empty transcript is final for the Pi; a broken
                    # finalization must still let it retry with local Vosk.
                    from transcribe import NoSpeechError
                    code = 'no_speech' if isinstance(exc, NoSpeechError) else 'stt'
                    raise TurnError('recognize', code, str(exc)) from exc
                emit(dict(event='transcript', text=text))
                if transcribe_only:  # enrollment answers: the Pi stores them itself
                    emit(dict(event='done', timings=timings))
                    return
                if intents.is_stop(text):
                    # "Stop", "Sei still": the Pi goes back to idle, nothing is spoken.
                    emit(dict(event='stop'))
                    emit(dict(event='done', timings=timings))
                    return
                if keep:
                    memory_copy = timed('speaker', self._identify, audio, memory_copy, emit)
                intent = intents.match(text, (device or {}).get('persona'))
                command = (memory.command(intents.normalize(text))
                           if memory_copy is not NO_MEMORY else None)
                service_op = maintenance.command(intents.normalize(text))
                device_reply = self._device_reply(intents.normalize(text), device or {},
                                                  memory_copy, emit)
                if device_reply is not None:
                    answer, model = device_reply, 'local/device'
                elif (enroll.command(intents.normalize(text)) and memory_copy is not NO_MEMORY
                        and not memory.unknown_speaker(memory_copy)):
                    # Only the operator (or anyone before enrollment) may start it.
                    answer, model = enroll.ANNOUNCE, 'local/enroll'
                    emit(dict(event='enroll', mode=enroll.command(intents.normalize(text))))
                elif service_op is not None and memory_copy is not NO_MEMORY:
                    # Only Pis that know maintenance send a memory state too.
                    answer = self._maintenance_reply(service_op, device or {})
                    emit(dict(event='maintenance', op=service_op))
                    model = 'local/maintenance'
                elif command is not None:
                    op, argument = command
                    style = phrase_style((device or {}).get('lore'),
                                         (device or {}).get('persona'))
                    answer = memory.reply(op, argument, memory_copy, style)
                    if (memory_copy is not None and op in ('add_fact', 'add_directive', 'forget')
                            and not memory.unknown_speaker(memory_copy)):
                        emit(dict(event='memory', op=op, text=memory.clean_text(argument)))
                    model = 'local/memory'
                elif intent is not None:
                    snapshot = dict(device or {}, server='ok')
                    state = getattr(self.pipeline, 'llm_state', None)
                    if state is not None:
                        snapshot['llm'] = state()
                    if intent in ('weather', 'briefing'):
                        snapshot['weather'] = timed('weather', self.weather.get)
                        day = (intents.weather_day(text, self.now().date())
                               if intent == 'weather' else 0)
                        snapshot['weather_day'] = day
                        if day is not None:
                            # The Pi shows its stored forecast with the answer.
                            emit(dict(event='show', screen='weather', day=day))
                    if intent in ('briefing', 'selftest') and self.logwatch is not None:
                        snapshot.update(self.logwatch.snapshot_fields('server_log'))
                        if intent == 'selftest':
                            self.logwatch.refresh(force=True)  # repair now, not in 30 min
                    if intent in ('calendar', 'briefing'):
                        if isinstance(memory_copy, dict) and memory.unknown_speaker(memory_copy):
                            snapshot['agenda'] = agenda.DENIED
                        elif appointments is not None:
                            snapshot['agenda'] = agenda.upcoming(appointments, self.now())
                    if (isinstance(memory_copy, dict) and memory_copy.get('speaker')
                            and not memory.unknown_speaker(memory_copy)):
                        snapshot['operator'] = memory_copy['speaker']  # recognized voice
                    answer = timed('intent', intents.answer, intent, self.now(), snapshot)
                    model = 'local/intent'
                else:
                    emit(dict(event='stage', stage='think'))
                    try:
                        lore = (device or {}).get('lore')
                        mode = (device or {}).get('llm_mode')
                        persona = (device or {}).get('persona')
                        answer, model = timed('llm', self.pipeline.reply, text, lore, mode,
                                              memory_copy, persona, device_mood(device, text))
                    except Exception as exc:
                        raise TurnError('think', 'llm', str(exc)) from exc
                    # The model's [stimmung:...] reaction is never spoken.
                    answer, feeling = split_tag(answer)
                    if feeling:
                        emit(dict(event='mood', emotion=feeling))
                    # Facts/directives the model picked up are never spoken.
                    answer, learned = memory.split_learned(answer)
                    if (memory_copy not in (None, NO_MEMORY)
                            and not memory.unknown_speaker(memory_copy)):
                        for op, value in learned:
                            emit(dict(event='memory', op=op, text=value, learned=True))
                emit(dict(event='reply', text=answer, model=model))
            else:
                upload_end = time.monotonic()
                answer = text
            answer = answer.strip()[:self.config.max_text]
            emit(dict(event='stage', stage='synthesize'))
            try:
                voice = (device or {}).get('voice', 'servitor')
                raw = timed('synthesis', self.pipeline.synthesize, answer, voice)
                temporary.append(raw)
            except Exception as exc:
                raise TurnError('synthesize', 'tts', str(exc)) from exc
            emit(dict(event='stage', stage='render'))
            if voice == 'natural' and hasattr(self.pipeline, 'convert'):
                # Never fails the turn: without RVC, Billy keeps Piper's voice.
                converted = timed('convert', self.pipeline.convert, raw)
                if converted:
                    temporary.append(converted)
                    raw, voice = converted, 'rvc'
            try:
                rendered = timed('render', self.pipeline.render, raw, voice)
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

    @property
    def timeout(self):
        # StreamRequestHandler.setup() applies this to the accepted socket, so
        # a client that never finishes its request line/headers times out
        # before authentication and rate limiting are even reached.
        return self.server.service.config.idle_timeout

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
        path = self.path.split('?', 1)[0]
        if path == '/health':
            self._json(200, dict(ok=True, ready=self.service.ready))
        elif path == '/v1/status':
            # Maintenance data only with the token (the URL is public).
            if not self.service.authorized(self.headers.get('Authorization')):
                return self._json(401, dict(error='unauthorized'))
            state = getattr(self.service.pipeline, 'llm_state', None)
            self._json(200, dict(updates=self.service.updates,
                                 llm=state() if state else None,
                                 maintenance=maintenance.status(self.service.maintenance_dir)))
        else:
            self._json(404, dict(error='not found'))

    def _voiceprint(self):
        """Enrollment: raw 16 kHz mono PCM -> int8 voice embedding."""
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        if not self.service.embedder.available:
            return self._json(503, dict(error='speaker model not installed'))
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(411, dict(error='length required'))
        if not 0 < length <= PCM_RATE * 2 * 30:
            return self._json(413, dict(error='1 to 30 s of audio'))
        pcm = self.rfile.read(length)
        embedding = self.service.embedder.embed(pcm)
        if embedding is None:
            return self._json(400, dict(error='audio shorter than 1 s'))
        return self._json(200, {'print': speaker.encode(embedding),
                                'seconds': round(len(pcm) / (PCM_RATE * 2), 1)})

    def _maintenance(self):
        """Update or reboot CT 107 on request of the Pi (button-confirmed there)."""
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if self.client_address[0] in self.service.config.trusted_proxies:
            # Through the public tunnel: never. Only the LAN path may ask.
            return self._json(403, dict(error='maintenance only on the LAN'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            data = json.loads(self.rfile.read(min(length, 1024)) or b'{}')
        except (ValueError, OSError):
            return self._json(400, dict(error='expected JSON {"action": "update|reboot"}'))
        action = data.get('action') if isinstance(data, dict) else None
        if action not in maintenance.ACTIONS:
            return self._json(400, dict(error='action must be update or reboot'))
        if action not in maintenance.SERVER_ACTIONS:
            # The unit must never update its own backend.
            return self._json(403, dict(error=f'{action} of the server is not allowed'))
        if not maintenance.installed(self.service.maintenance_dir):
            return self._json(503, dict(error='maintenance worker not installed'))
        now = time.monotonic()
        last = self.service.maintenance_at
        if last is not None and now - last < maintenance.SERVER_MIN_INTERVAL:
            return self._json(429, dict(error='too soon'), {'Retry-After': str(
                int(maintenance.SERVER_MIN_INTERVAL - (now - last)) + 1)})
        maintenance.request(action, self.service.maintenance_dir)
        self.service.maintenance_at = now
        print(json.dumps(dict(event='maintenance_request', action=action)), flush=True)
        return self._json(202, dict(accepted=action))

    def _envelope(self):
        """One SPX/1 message from the body, or None after an error reply."""
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            self._json(411, dict(error='length required'))
            return None
        if not 0 < length <= protocol.MAX_MESSAGE:
            self._json(413, dict(error='message too large'))
            return None
        try:
            return protocol.decode(self.rfile.read(length))
        except protocol.ProtocolError as exc:
            self._json(400, dict(error=exc.code, message=str(exc)))
            return None

    def _hello(self):
        """Open a session: SPX/1 hello in, welcome with the session id out."""
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        item = self._envelope()
        if item is None:
            return None
        if item['type'] != 'hello':
            return self._json(400, dict(error='type', message='expected hello'))
        session = self.service.sessions.open(item['body'].get('device', ''))
        welcome = protocol.message('welcome', dict(session=session,
                                                   idle=self.service.sessions.idle,
                                                   handles=sorted(HANDLED)))
        return self._json(200, welcome)

    def _message(self):
        """One SPX/1 envelope; a resent one (same id) is acknowledged again,
        but handled only once."""
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        item = self._envelope()
        if item is None:
            return None
        if item['type'] not in HANDLED:
            return self._json(400, dict(error='unsupported', message=item['type']))
        first = self.service.seen.first(item['id'])
        # ping needs no work; later message types are handled here when first.
        return self._json(200, protocol.message('ack', dict(id=item['id'],
                                                            duplicate=not first)))

    def _memory(self, device):
        """(memory copy for the turn, session event to send or None)."""
        state = (device or {}).get('memory')
        if state == 'off':
            return None, None
        if state != 'on':
            return NO_MEMORY, None
        raw = memory.parse_header(self.headers.get('X-Servitor-Memory', ''))
        if raw is None:
            return None, None
        session = self.headers.get('X-Servitor-Session', '')[:64]
        known = bool(session) and self.service.sessions.known(session)
        if 'core' in raw:                           # only history + digest of the core
            full = self.service.sessions.restore(session, raw) if known else None
            if full is None:     # restarted server or another core: send it in full next time
                return NO_MEMORY, dict(event='session', state='unknown')
            return memory.sanitize(full), None
        if known:
            return memory.sanitize(raw), dict(event='session',
                                              core=self.service.sessions.remember(session, raw))
        return memory.sanitize(raw), None

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/v1/hello':
            return self._hello()
        if url.path == '/v1/message':
            return self._message()
        if url.path == '/v1/maintenance':
            return self._maintenance()
        if url.path == '/v1/voiceprint':
            return self._voiceprint()
        if url.path not in ('/v1/turn', '/v1/speak'):
            self.close_connection = True
            return self._json(404, dict(error='not found'))
        self.close_connection = True  # never reuse a connection with an unread body
        self.connection.settimeout(self.service.config.idle_timeout)
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        deadline = time.monotonic() + self.service.config.loading_wait
        while not self.service.ready and time.monotonic() < deadline:
            time.sleep(0.2)
        if not self.service.ready:
            return self._json(503, dict(error='loading'), {'Retry-After': '5'})
        fmt = urllib.parse.parse_qs(url.query).get('format', ['wav'])[0]
        transcribe_only = urllib.parse.parse_qs(url.query).get('mode', [''])[0] == 'transcribe'
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
        device = None
        header = self.headers.get('X-Servitor-Status', '')
        if header and len(header) <= 1024:
            try:
                device = sanitize_snapshot(json.loads(header))
            except ValueError:
                device = None
        memory_copy, session_event = self._memory(device)

        appointments = agenda.decode_header(self.headers.get('X-Servitor-Agenda', ''),
                                            self.service.now().tzinfo)

        if not self.service.turn_lock.acquire(timeout=max(0.0, self.service.config.busy_wait)):
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

            if session_event:
                emit(session_event)
            try:
                self.service.run_turn(body, emit, fmt, text=text, device=device,
                                      memory_copy=memory_copy, transcribe_only=transcribe_only,
                                      appointments=appointments)
            except TurnError as exc:
                # Stage and code only in the journal (the message may quote the operator).
                # Silence or a too short press is no fault: the self-test skips those.
                name = 'turn_rejected' if exc.code in OPERATOR_CODES else 'turn_error'
                print(json.dumps(dict(event=name, stage=exc.stage, code=exc.code)), flush=True)
                emit(dict(event='error', stage=exc.stage, code=exc.code, message=exc.message))
            except (OSError, TimeoutError):
                raise
            except Exception as exc:  # report, keep serving
                print(json.dumps(dict(event='turn_error', stage='internal', code='internal',
                                      error=type(exc).__name__)), flush=True)
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
    if os.environ.get('SERVITOR_LOGWATCH', '1') != '0':
        def selftest_report(result):
            print(json.dumps(dict(event='selftest', findings=result['findings'],
                                  repairs=result['repairs'])), flush=True)
        service.logwatch = logwatch.server_watch(report=selftest_report).start()

    print(json.dumps(dict(event='listening', bind=config.bind, port=config.port)), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
