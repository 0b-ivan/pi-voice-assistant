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
* ``POST /v1/ask?format=wav|opus`` with JSON ``{"text": "..."}``: a typed
  question that takes the way of a transcript (intents, LLM, voice); the
  browser client's text field.
* ``POST /v1/story?format=wav|opus`` with JSON ``{"story": state}``: the next
  section of an explicitly long story (src/story.py). Like a turn that asks
  for a long story, it answers with ``reply``, several ``audio`` events with
  a ``part`` number (each at most SERVITOR_MAX_TEXT_CHARS of text, so nothing
  is cut) and a ``story`` event carrying the state for the next request.
* ``POST /v1/hello`` with an SPX/1 ``hello`` (src/protocol.py): opens a
  session, answered with ``welcome``. A turn naming the session in
  ``X-Servitor-Session`` may send only the digest of a memory core the
  session already holds.
* ``POST /v1/message`` with one SPX/1 envelope, answered with ``ack``.
* ``GET /health`` without token: ``{"ok": true, "ready": bool}`` only.
* ``GET /web/`` without token: the browser client (``web/``, docs/web-client.md).
  Only its fixed files are served; every request it makes needs the token.

Responses of /v1 are NDJSON events: ``stage`` (recognize, think, synthesize,
render), ``transcript``, ``reply``, ``audio`` (base64), ``done`` (timings) or
``error``. Only one turn runs at a time; a second one gets HTTP 503.
A journey turn (src/journeys.py, only for Pis that send ``trip`` in their
status) ends with ``journey`` (the structured request, the recognized voice
and the proposal ID) and ``done``, without ``audio``: the Pi queries the
timetable and speaks the answer itself.
"""
import base64
import collections
import datetime
import hmac
import http.server
import json
import os
import re
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

import intents  # noqa: E402
import weather  # noqa: E402
import agenda  # noqa: E402
import device_control  # noqa: E402
import enroll  # noqa: E402
import journeys  # noqa: E402
import logwatch  # noqa: E402
import maintenance  # noqa: E402
import memory  # noqa: E402
import protocol  # noqa: E402
import speaker  # noqa: E402
import voice_profile  # noqa: E402
import voice_quality  # noqa: E402
import story as stories  # noqa: E402
from llm import NO_MEMORY  # noqa: E402
from mood import emergency, split_tag  # noqa: E402
from system_status import phrase_style, sanitize_snapshot  # noqa: E402

PCM_RATE = 16000
HANDLED = ('ping',)   # SPX/1 types /v1/message acts on; more come with later steps
FEATURES = ('voice2',)  # announced in the welcome: v2 voice core and enrollment clips
VOICE_MISSING = object()  # the Pi named a v2 voice core this server does not hold
VOICE_MIN_SECONDS = 1.5   # v2: less speech is 'insufficient' (concept, section 5)
CLIP_MAX_SECONDS = 12
FORMATS = {'wav': 'audio/wav', 'opus': 'audio/ogg'}
# TurnError codes caused by the input, not by the server (logwatch ignores them).
OPERATOR_CODES = ('no_speech', 'too_short', 'too_large', 'bad_request')
MAX_TYPED_CHARS = 500     # /v1/ask: about what a 30 s spoken question holds
# The browser client: published path -> (file in WEB, MIME type). Nothing else is served.
WEB = Path(__file__).resolve().parent.parent / 'web'
WEB_FILES = {
    '/web/': ('index.html', 'text/html; charset=utf-8'),
    '/web/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/web/capture-worklet.js': ('capture-worklet.js', 'text/javascript; charset=utf-8'),
    '/web/style.css': ('style.css', 'text/css; charset=utf-8'),
    '/web/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
    '/web/servo-skull.png': ('../assets/display/servo-skull.png', 'image/png'),
}
WEB_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
           "media-src 'self' blob: data:; connect-src 'self'; worker-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


# Fixed information answers the server may let the LLM say in fresh words
# (llm.rephrase keeps every number). Not: questions waiting for a
# confirmation, times, dates, stories, memory or device commands, and not
# the status report: it must come fast (its wording varies on the Pi side).
REPHRASED_INTENTS = ('battery', 'network', 'updates', 'hardware', 'power_detail',
                     'power_config', 'power_runtime', 'power_current')
# Long fixed answers are sent in parts: the first sentences play while the
# rest is still being synthesized (Pis that queue parts: snapshot story=on).
SPLIT_INTENTS = ('status', 'network', 'updates', 'hardware', 'selftest', 'power_detail',
                 'power_config', 'power_runtime')
FIRST_PART_CHARS = 45     # the first part: short, so the voice starts at once
NEXT_PART_CHARS = 120


def speech_parts(text, first=FIRST_PART_CHARS, following=NEXT_PART_CHARS):
    """``text`` cut at sentence ends into parts of at least ``first``, then
    ``following`` characters (the last may be shorter); one part if short."""
    sentences = [s for s in re.split(r'(?<=[.!?])\s+', text.strip()) if s]
    parts, current = [], ''
    for sentence in sentences:
        current = f'{current} {sentence}'.strip()
        if len(current) >= (following if parts else first):
            parts.append(current)
            current = ''
    if current:
        if parts and len(current) < first:
            parts[-1] = f'{parts[-1]} {current}'     # no tiny tail
        else:
            parts.append(current)
    return parts or [text]
if 'SERVITOR_REPHRASE_INTENTS' in os.environ:
    REPHRASED_INTENTS = tuple(name.strip() for name in
                              os.environ['SERVITOR_REPHRASE_INTENTS'].split(',') if name.strip())


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


def quick_text(text):
    """Vosk's text is good enough as it is: a stop word or a fixed request
    answered without the LLM (Whisper would only add a second)."""
    return intents.is_stop(text) or intents.match(text) is not None


class RealPipeline:
    """Resident models; every method is called under the service turn lock."""

    def __init__(self, workdir, clock=time.monotonic):
        self.workdir = workdir
        self.voice = None
        self.high = None      # Piper thorsten-high for both voices, if SERVITOR_HIGH_PIPER_MODEL
        self.clock = clock
        # After an OpenRouter failure, go straight to the local model for a
        # while instead of paying the full timeout on every turn of an outage.
        self.openrouter_retry_at = 0.0

    def load(self):
        import transcribe
        transcribe._load_vosk_model()
        import whisper_stt
        if whisper_stt.enabled():
            # Optional and never fatal: without it the turn stays on Vosk.
            try:
                whisper_stt.load()
                print(json.dumps(dict(event='whisper_ready', model=whisper_stt.MODEL)),
                      flush=True)
            except Exception as exc:
                print(json.dumps(dict(event='whisper_unavailable', error=str(exc))),
                      flush=True)
        model = os.environ['SERVITOR_PIPER_MODEL']
        from piper import PiperVoice
        self.voice = PiperVoice.load(model)
        high = os.environ.get('SERVITOR_HIGH_PIPER_MODEL', '').strip()
        if high:
            self.high = PiperVoice.load(high)
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
            else:
                print(json.dumps(dict(event='local_llm_ready')), flush=True)

    def recognizer(self):
        import whisper_stt
        if whisper_stt.loaded():
            return whisper_stt.HybridRecognizer(quick=quick_text)
        from transcribe import LiveVoskRecognizer
        return LiveVoskRecognizer()

    def night_recognizer(self):
        # Short Vosk probe; never invoke an optional long Whisper pass while
        # foreground work is waiting. Full ASR comparisons remain offline jobs.
        from transcribe import LiveVoskRecognizer
        return LiveVoskRecognizer()

    def llm_state(self):
        """'offline' while OpenRouter is being skipped after a failure."""
        if os.environ.get('SERVITOR_LOCAL_LLM') == '1' and self.clock() < self.openrouter_retry_at:
            return 'offline'
        return 'openrouter'

    def reply(self, text, lore=None, mode=None, memory=NO_MEMORY, persona=None, mood=None,
              story=None):
        """OpenRouter first; on any LLM error (offline, no credits, timeout)
        the resident llama.cpp server answers when SERVITOR_LOCAL_LLM=1.
        ``story``: a long story's state; the reply is its next section."""
        from llm import LLMError, free_model, generate_local_reply, generate_reply
        def successful(result):
            event = 'local_llm_ready' if result[1].startswith('local/') else 'openrouter_ready'
            print(json.dumps(dict(event=event)), flush=True)
            return result

        local = os.environ.get('SERVITOR_LOCAL_LLM') == '1'
        # "FREI": the low-restriction model; otherwise the configured one.
        chosen = free_model() if mode == 'free' else None
        if local and mode == 'local':
            # Operator chose "Sprachkern LOKAL" on the Pi: never call OpenRouter.
            return successful(generate_local_reply(text, lore=lore, memory=memory, persona=persona,
                                                   mood=mood, story=story))
        if local and self.clock() < self.openrouter_retry_at:
            primary = 'OpenRouter skipped after a recent failure'
        else:
            try:
                return successful(generate_reply(text, lore=lore, memory=memory, model=chosen,
                                                 persona=persona, mood=mood, story=story))
            except LLMError as exc:
                if not local:
                    raise
                primary = str(exc)
                retry = float(os.environ.get('SERVITOR_OPENROUTER_RETRY_SECONDS', '60'))
                self.openrouter_retry_at = self.clock() + retry
        print(json.dumps(dict(event='llm_fallback', reason=primary)), flush=True)
        try:
            return successful(generate_local_reply(text, lore=lore, memory=memory, persona=persona,
                                                   mood=mood, story=story))
        except LLMError as exc:
            raise LLMError(f'{primary}; local fallback failed: {exc}') from exc

    def rephrase(self, text, lore=None, persona=None, mode=None):
        """A fixed answer in fresh words (llm.rephrase), or None to keep it:
        switched off, "Sprachkern LOKAL", OpenRouter cooling down after a
        failure, unreachable, too slow, or the facts changed."""
        if os.environ.get('SERVITOR_REPHRASE', 'on').strip().lower() in ('off', '0', 'no',
                                                                         'false'):
            return None
        if mode == 'local' or self.clock() < self.openrouter_retry_at:
            return None
        from llm import LLMError, rephrase
        try:
            result = rephrase(text, lore=lore, persona=persona)
        except LLMError as exc:
            print(json.dumps(dict(event='rephrase_failed', reason=str(exc))), flush=True)
            return None
        if result is None:
            print(json.dumps(dict(event='rephrase_rejected')), flush=True)
        return result

    def synthesize(self, text, voice='servitor'):
        """Both voices speak with thorsten-high (calm) when it is loaded, else
        with the Servitor speaker; the rendering makes Proximus or Billy."""
        from voice_controls import _synthesize_voice, high_synthesis_config
        from pronounce import spoken
        target = _temporary_wav('syn-', self.workdir)
        try:
            with wave.open(str(target), 'wb') as output:
                if self.high is not None:
                    self.high.synthesize_wav(spoken(text), output,
                                             syn_config=high_synthesis_config())
                else:
                    _synthesize_voice(self.voice, text, output, 'servitor')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def render(self, source, voice='servitor'):
        from voice_effects import build_render_command
        target = _temporary_wav('dsp-', self.workdir)
        effect = voice
        if self.high is not None:
            effect = 'billy' if voice == 'natural' else 'servitor-high'
        try:
            subprocess.run(build_render_command(source, target, effect=effect), check=True,
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
        import nightwatch_server
        self.nightwatch = nightwatch_server.Manager(self)

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
        return device_control.reply(op, device.get('wlan'), style, device.get('cpu_mode'))

    def _identify(self, audio, memory_copy, emit, voice=None):
        """Compare the turn's voice with the enrolled voiceprints; returns the
        memory copy the turn may use. Only a clear match names a person; too
        little speech, no model, a broken profile, an error or two profiles
        too close all give the guest view (concept stimm-einlernen, section 2).
        ``voice``: the session's calibrated v2 profiles (they alone decide),
        VOICE_MISSING when the Pi named a core this server lacks, or None."""
        if voice is not None:
            return self._identify_v2(audio, memory_copy, emit, voice)
        if not memory.enrolled(memory_copy):
            return memory_copy      # nobody enrolled: nothing to protect
        listed = memory_copy.get('voiceprints') or []
        prints = [dict(name=p['name'], vector=speaker.decode(p['print'])) for p in listed]
        prints = [p for p in prints if p['vector']]
        result = dict(status=speaker.UNAVAILABLE, best=None)
        if prints and self.embedder.available:
            try:
                pcm = bytes(audio)
                if speaker.speech_seconds(pcm) < speaker.MIN_SECONDS:
                    result = dict(status=speaker.INSUFFICIENT, best=None)
                else:
                    # Without the pauses before and after the words: the
                    # enrolled voiceprint is built from trimmed speech too.
                    embedding = self.embedder.embed(speaker.trim_silence(pcm))
                    result = (speaker.decide(embedding, prints) if embedding is not None
                              else dict(status=speaker.INSUFFICIENT, best=None))
            except Exception as exc:  # never fail a turn over speaker recognition
                print(json.dumps(dict(event='speaker_error', error=type(exc).__name__)),
                      flush=True)
                result = dict(status=speaker.UNAVAILABLE, best=None)
        best = result.get('best')
        matched = result['status'] == speaker.MATCHED
        carried = self._carried(memory_copy, None if matched else memory_copy.get('trusted'),
                                [p['name'] for p in prints])
        emit(dict(event='speaker', status=result['status'], known=matched,
                  name=result['name'] if matched else None, carried=bool(carried),
                  score=None if best is None else round(best, 3)))
        if carried:
            return dict(memory_copy, speaker=carried)
        if not matched:
            return memory.guest_view(memory_copy, result['status'])
        return dict(memory_copy, speaker=result['name'])

    @staticmethod
    def _carried(memory_copy, trusted, names):
        """The person the Pi vouches for after a sure identification a few
        minutes ago (memory.Trust), if enrolled; short questions alone are no
        reliable voice proof (docs/memory.md)."""
        if isinstance(memory_copy, dict) and isinstance(trusted, str) and trusted in names:
            return trusted
        return None

    def _identify_v2(self, audio, memory_copy, emit, voice):
        status, best, ident, name = voice_profile.UNAVAILABLE, None, None, None
        if voice is VOICE_MISSING:
            emit(dict(event='session', voice='unknown'))   # the Pi sends its core again
        elif self.embedder.available:
            try:
                pcm = bytes(audio)
                if speaker.speech_seconds(pcm) < VOICE_MIN_SECONDS:
                    status = speaker.INSUFFICIENT
                else:
                    embedding = self.embedder.embed(speaker.trim_silence(pcm))
                    if embedding is None:
                        status = speaker.INSUFFICIENT
                    else:
                        result = voice_profile.decide(embedding, voice,
                                                      self.embedder.model_info())
                        status, best, ident = (result['status'], result['best'],
                                               result['profile_id'])
            except Exception as exc:  # never fail a turn over speaker recognition
                print(json.dumps(dict(event='speaker_error', error=type(exc).__name__)),
                      flush=True)
                status = voice_profile.UNAVAILABLE
        if status == voice_profile.MATCHED:
            name = next(p['name'] for p in voice if p['profile_id'] == ident)
        carried = None
        if name is None and isinstance(voice, list):
            carried = self._carried(memory_copy, (memory_copy or {}).get('trusted'),
                                    [p['name'] for p in voice])
        emit(dict(event='speaker', status=status, known=name is not None, profile=2,
                  name=name, carried=bool(carried),
                  score=None if best is None else round(best, 3)))
        if not isinstance(memory_copy, dict):
            return memory_copy      # no stick or no memory support: nothing personal
        if carried:
            ident = next(p['profile_id'] for p in voice if p['name'] == carried)
            return dict(memory_copy, speaker=carried, speaker_id=ident)
        if name is None:
            return memory.guest_view(memory_copy, status)
        return dict(memory_copy, speaker=name, speaker_id=ident)

    def enroll_clip(self, pcm):
        """POST /v2/enroll/clip: one enrollment take -> numbers, transcript and,
        with enough speech, a float32 embedding of the model in use. RAM only;
        the Pi judges the take (it knows the target sentence and room noise)."""
        quality = voice_quality.analyze(pcm)
        try:
            recognizer = self.pipeline.recognizer()
            if hasattr(recognizer, 'whisper'):
                recognizer.whisper = False      # the same spelling as everyday Vosk
            recognizer.accept_pcm(pcm[:len(pcm) - len(pcm) % 2])
            text = recognizer.finish()
        except Exception:  # no speech or a broken recognizer: no transcript
            text = ''
        reply = dict(quality=quality, transcript=text or '', vector=None,
                     model=self.embedder.model_info(), preprocessing=voice_quality.VERSION)
        if quality['speech_seconds'] >= speaker.MIN_SECONDS and self.embedder.available:
            embedding = self.embedder.embed(speaker.trim_silence(pcm))
            if embedding is not None:
                reply['vector'] = voice_profile.encode(embedding)
                reply['dim'] = len(embedding)
        return reply

    def _story_start(self, text, emit, fmt, device, timings):
        """An explicitly long story: plan it and tell its first, short section."""
        import lore
        from llm import lore_level, persona_name
        persona, level = persona_name(device.get('persona')), lore_level(device.get('lore'))
        request = stories.request(text)
        state = stories.start(request, persona, level, lore.packet(text, persona, level))
        print(json.dumps(dict(event='story_start', minutes=state['minutes'],
                              persona=persona, ids=state['ids'])), flush=True)
        self._story_section(state, emit, fmt, device, timings)

    def run_story(self, state, emit, fmt, device=None):
        """POST /v1/story: the next section of a running story."""
        timings = {}
        if state.get('done'):
            raise TurnError('think', 'bad_request', 'story already finished')
        self._story_section(state, emit, fmt, device or {}, timings)
        emit(dict(event='done', timings=timings))

    def _story_section(self, state, emit, fmt, device, timings):
        """One section: a single LLM call with the compact story state, then
        audio in small parts at sentence boundaries, each sent as soon as it
        is ready. The new state goes back last, as a ``story`` event."""
        started = time.monotonic()
        emit(dict(event='stage', stage='think'))
        try:
            raw, model = self.pipeline.reply(state['topic'], state['lore'],
                                             device.get('llm_mode'), NO_MEMORY,
                                             state['persona'], None, story=state)
        except Exception as exc:
            raise TurnError('think', 'llm', str(exc)) from exc
        timings['llm'] = round(time.monotonic() - started, 3)
        text, note = stories.split_note(split_tag(raw)[0])
        text = stories.trim_incomplete(memory.split_learned(text)[0])
        if not text:
            raise TurnError('think', 'llm', 'empty story section')
        state = stories.advance(state, text, note)
        emit(dict(event='reply', text=text, model=model, story=True))
        voice = device.get('voice', 'servitor')
        parts = stories.split_parts(text, self.config.max_text)
        for index, part in enumerate(parts):
            temporary = []
            try:
                emit(dict(event='stage', stage='synthesize'))
                try:
                    raw_audio = self.pipeline.synthesize(part, voice)
                    temporary.append(raw_audio)
                except Exception as exc:
                    raise TurnError('synthesize', 'tts', str(exc)) from exc
                try:
                    rendered = self.pipeline.render(raw_audio, voice)
                    temporary.append(rendered)
                    data = self.pipeline.encode(rendered, fmt)
                    duration = wav_duration_ms(rendered)
                except Exception as exc:
                    raise TurnError('render', 'dsp', str(exc)) from exc
            finally:
                for path in temporary:
                    Path(path).unlink(missing_ok=True)
            state['ms'] += duration
            emit(dict(event='audio', format=fmt, mime=FORMATS[fmt], duration_ms=duration,
                      bytes=len(data), part=index, parts=len(parts), text=part,
                      data=base64.b64encode(data).decode('ascii')))
        timings['story_section'] = round(time.monotonic() - started, 3)
        emit(dict(event='story', state=state))

    def _speak_parts(self, parts, voice, fmt, emit, timings, temporary):
        """Synthesize and send ``parts`` one after another as audio parts; the
        Pi plays the first while the next is made."""
        emit(dict(event='stage', stage='synthesize'))
        for index, part in enumerate(parts):
            started = time.monotonic()
            try:
                raw = self.pipeline.synthesize(part, voice)
                temporary.append(raw)
            except Exception as exc:
                raise TurnError('synthesize', 'tts', str(exc)) from exc
            try:
                rendered = self.pipeline.render(raw, voice)
                temporary.append(rendered)
                data = self.pipeline.encode(rendered, fmt)
                duration = wav_duration_ms(rendered)
            except Exception as exc:
                raise TurnError('render', 'dsp', str(exc)) from exc
            timings[f'part{index}'] = round(time.monotonic() - started, 3)
            emit(dict(event='audio', format=fmt, mime=FORMATS[fmt], duration_ms=duration,
                      bytes=len(data), part=index, parts=len(parts), text=part,
                      data=base64.b64encode(data).decode('ascii')))

    def run_turn(self, pcm_chunks, emit, fmt, text=None, device=None, memory_copy=NO_MEMORY,
                 transcribe_only=False, appointments=None, appointments_tomorrow=None,
                 voice=None, nightwatch_copy=None, typed=None):
        """Drive one turn. ``pcm_chunks`` is consumed only when ``text`` and
        ``typed`` are None. ``typed``: a typed question (web client, /v1/ask)
        that takes the whole way of a transcript; ``text`` is spoken as it is.

        ``device`` is the Pi's sanitized status snapshot; questions such as
        time, date or status are answered from it without the LLM.
        ``memory_copy``: the Pi's memory core (None: stick absent, NO_MEMORY:
        Pi without memory support). Changes go back as ``memory`` events.
        ``appointments``: today's calendar from the Pi (agenda.decode_header), or None."""
        timings = {}
        temporary = []
        split = False         # a long fixed answer goes out in parts (SPLIT_INTENTS)

        def timed(name, function, *args):
            started = time.monotonic()
            try:
                return function(*args)
            finally:
                timings[name] = round(time.monotonic() - started, 3)

        try:
            if text is None:
                keep, audio = False, b''
                if typed is not None:
                    # The web client typed its question: no audio, no voice check.
                    upload_end = time.monotonic()
                    text = typed
                else:
                    try:
                        recognizer = self.pipeline.recognizer()
                    except Exception as exc:
                        raise TurnError('recognize', 'stt', str(exc)) from exc
                    if transcribe_only and hasattr(recognizer, 'whisper'):
                        recognizer.whisper = False  # passphrases were enrolled in Vosk spelling
                    pending = b''
                    received = 0
                    # Keep the audio only when a voice must be recognized.
                    keep = memory.enrolled(memory_copy) or voice is not None
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
                    if received < int(PCM_RATE * 2 * protocol.MIN_TURN_SECONDS):
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
                trip_state = (device or {}).get('trip')
                trip = journeys.parse(text, trip_state) if trip_state else None
                if trip is not None:
                    # Trams/trains, reminder, calendar: the Pi queries and acts itself
                    # (home and calendar stay there). Structured, no LLM, no audio.
                    if keep:
                        memory_copy = timed('speaker', self._identify, audio, memory_copy,
                                            emit, voice)
                    guest = isinstance(memory_copy, dict) and memory.unknown_speaker(memory_copy)
                    name = memory_copy.get('speaker') if isinstance(memory_copy, dict) else None
                    emit(dict(event='journey', request=trip,
                              speaker=None if guest or not isinstance(name, str) else name,
                              guest=guest, proposal=(device or {}).get('trip_id')))
                    emit(dict(event='done', timings=timings))
                    return
                if intents.is_stop(text):
                    # "Stop", "Sei still": the Pi goes back to idle, nothing is spoken.
                    emit(dict(event='stop'))
                    emit(dict(event='done', timings=timings))
                    return
                if keep:
                    memory_copy = timed('speaker', self._identify, audio, memory_copy, emit,
                                        voice)
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
                    answer, model = enroll.MENU_HINT, 'local/enroll'
                    emit(dict(event='enroll', mode=enroll.command(intents.normalize(text))))
                elif enroll.command(intents.normalize(text)) and memory.unknown_speaker(memory_copy):
                    # A guest asking to be learned: say no instead of letting the LLM
                    # claim "Stimme erkannt" (system test 10.10.2026).
                    answer, model = memory.GUEST_TEXT, 'local/enroll'
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
                    if intent.startswith('night_') or intent == 'briefing':
                        snapshot['nightwatch'] = (dict(denied=True)
                            if isinstance(memory_copy, dict) and memory.unknown_speaker(memory_copy)
                            else nightwatch_copy)
                    state = getattr(self.pipeline, 'llm_state', None)
                    if state is not None:
                        snapshot['llm'] = state()
                    if intent in ('weather', 'briefing'):
                        snapshot['weather'] = timed('weather', self.weather.get)
                        day = (intents.weather_day(text, self.now().date()) if intent == 'weather'
                               else intents.briefing_day(self.now()))
                        snapshot['weather_day'] = day
                        if day is not None:
                            # The Pi shows its stored forecast with the answer.
                            emit(dict(event='show', screen='weather', day=day))
                    if intent in ('briefing', 'selftest') and self.logwatch is not None:
                        snapshot.update(self.logwatch.snapshot_fields('server_log'))
                        if intent == 'selftest':
                            self.logwatch.refresh(force=True)  # repair now, not in 30 min
                    if intent in ('calendar', 'briefing'):
                        day = intents.calendar_day(text, self.now().date()) if intent == 'calendar' else 0
                        snapshot['agenda_day'] = day
                        if isinstance(memory_copy, dict) and memory.unknown_speaker(memory_copy):
                            snapshot['agenda'] = agenda.DENIED
                        elif day == 1 and appointments_tomorrow is not None:
                            start, _ = agenda.day_bounds(self.now())
                            snapshot['agenda'] = agenda.upcoming(
                                appointments_tomorrow, start + datetime.timedelta(days=1))
                        elif day == 0 and appointments is not None:
                            snapshot['agenda'] = agenda.upcoming(appointments, self.now())
                    if (isinstance(memory_copy, dict) and memory_copy.get('speaker')
                            and not memory.unknown_speaker(memory_copy)):
                        snapshot['operator'] = memory_copy['speaker']  # recognized voice
                    answer = timed('intent', intents.answer, intent, self.now(), snapshot)
                    model = 'local/intent'
                    rephrase = getattr(self.pipeline, 'rephrase', None)
                    if intent in REPHRASED_INTENTS and rephrase is not None and answer:
                        # Same facts, fresh words: the server has an LLM at hand.
                        try:
                            varied = timed('rephrase', rephrase, answer, snapshot.get('lore'),
                                           snapshot.get('persona'), snapshot.get('llm_mode'))
                        except Exception as exc:  # noqa: BLE001 - the fixed text still works
                            print(json.dumps(dict(event='rephrase_failed', reason=str(exc))),
                                  flush=True)
                            varied = None
                        if varied:
                            answer, model = varied, 'local/intent+rephrase'
                    split = intent in SPLIT_INTENTS and (device or {}).get('story') == 'on'
                elif stories.request(text) is not None:
                    if (device or {}).get('story') != 'on':
                        # An older Pi would play only the last part: say so instead of
                        # handing out a story whose end is silently missing.
                        persona = 'mensch' if (device or {}).get('persona') == 'mensch' \
                            else 'servitor'
                        answer, model = stories.OLD_DEVICE[persona], 'local/story'
                    else:
                        self._story_start(text, emit, fmt, device or {}, timings)
                        emit(dict(event='done', timings=timings))
                        return
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
            parts = speech_parts(answer) if split else [answer]
            if len(parts) > 1:
                self._speak_parts(parts, (device or {}).get('voice', 'servitor'), fmt, emit,
                                  timings, temporary)
                timings['server_total'] = round(time.monotonic() - upload_end, 3)
                emit(dict(event='done', timings=timings))
                return
            emit(dict(event='stage', stage='synthesize'))
            try:
                voice = (device or {}).get('voice', 'servitor')
                raw = timed('synthesis', self.pipeline.synthesize, answer, voice)
                temporary.append(raw)
            except Exception as exc:
                raise TurnError('synthesize', 'tts', str(exc)) from exc
            emit(dict(event='stage', stage='render'))
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
        if path == '/v1/nightwatch':
            return self._nightwatch()
        if path in ('/', '/web'):
            self.send_response(302)
            self.send_header('Location', '/web/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if path in WEB_FILES:
            return self._web(path)
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

    def _web(self, path):
        """One fixed file of the browser client; no token (it asks for it)."""
        name, mime = WEB_FILES[path]
        try:
            body = (WEB / name).read_bytes()
        except OSError:
            return self._json(404, dict(error='not found'))
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', WEB_CSP)
        self.send_header('Permissions-Policy', 'microphone=(self)')
        self.end_headers()
        self.wfile.write(body)

    def _nightwatch(self):
        """Authenticated LAN-only job control; no arbitrary commands or paths."""
        import ipaddress
        import nightwatch
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if os.environ.get('SERVITOR_NIGHTWATCH', '1') == '0':
            return self._json(503, dict(error='nightwatch disabled'))
        peer = self.client_address[0]
        if peer in self.service.config.trusted_proxies or not ipaddress.ip_address(peer).is_private:
            return self._json(403, dict(error='nightwatch only on the LAN'))
        manager = self.service.nightwatch
        if self.command == 'GET':
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                return self._json(200, manager.status(query.get('id', [None])[0]))
            except ValueError:
                return self._json(404, dict(error='unknown run'))
        self.connection.settimeout(5)
        try:
            length = int(self.headers.get('Content-Length', ''))
            if not 0 < length <= nightwatch.MAX_REPORT:
                return self._json(413, dict(error='request too large'))
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('truncated body')
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError('expected object')
            action = data.get('action')
            if action == 'start':
                return self._json(202, dict(id=manager.start(data)))
            if action not in ('cancel', 'ack') or data.get('id') != manager.ident:
                raise ValueError('invalid action or run')
            if action == 'cancel':
                if manager.guard:
                    manager.guard.cancel()
            else:
                manager.ack(data['id'])
            return self._json(200, dict(ok=True))
        except RuntimeError:
            return self._json(409, dict(error='nightwatch busy or awaiting acknowledgement'))
        except (ValueError, TypeError, OSError):
            return self._json(400, dict(error='invalid nightwatch request'))

    def _voiceprint(self):
        """Enrollment: raw 16 kHz mono PCM -> int8 voice embedding."""
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        if not self.service.embedder.available:
            return self._json(503, dict(error='speaker model not installed'))
        self.service.nightwatch.foreground()
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(411, dict(error='length required'))
        if not 0 < length <= PCM_RATE * 2 * 30:
            return self._json(413, dict(error='1 to 30 s of audio'))
        pcm = self.rfile.read(length)
        # The same path as a turn's voice check (_identify): speech, not file
        # length, then the same trim. The numbers go back, never the audio.
        quality = voice_quality.analyze(pcm)
        if quality['speech_seconds'] < speaker.MIN_SECONDS:
            return self._json(400, dict(error='less than 1 s of speech', quality=quality))
        embedding = self.service.embedder.embed(speaker.trim_silence(pcm))
        if embedding is None:
            return self._json(400, dict(error='less than 1 s of speech', quality=quality))
        return self._json(200, {'print': speaker.encode(embedding),
                                'seconds': round(len(pcm) / (PCM_RATE * 2), 1),
                                'quality': quality})

    def _enroll_clip(self):
        self.close_connection = True
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        if not self.service.limiter.allow(self._client()):
            return self._json(429, dict(error='rate limited'), {'Retry-After': '30'})
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(411, dict(error='length required'))
        if not 0 < length <= PCM_RATE * 2 * CLIP_MAX_SECONDS:
            return self._json(413, dict(error=f'1 to {CLIP_MAX_SECONDS} s of audio'))
        pcm = self.rfile.read(length)
        if len(pcm) != length:
            return self._json(400, dict(error='truncated body'))
        with self.service.turn_lock:     # one model run at a time (concept, section 10)
            return self._json(200, self.service.enroll_clip(pcm))

    def do_PUT(self):
        """PUT /v2/session/<id>/core: the session's calibrated v2 voice profiles."""
        self.close_connection = True
        parts = urllib.parse.urlsplit(self.path).path.strip('/').split('/')
        if len(parts) != 4 or parts[:2] != ['v2', 'session'] or parts[3] != 'core':
            return self._json(404, dict(error='not found'))
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        ident = parts[2][:64]
        if not self.service.sessions.known(ident):
            return self._json(404, dict(error='unknown session'))
        try:
            length = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self._json(411, dict(error='length required'))
        if not 0 < length <= voice_profile.CORE_BYTES_MAX:
            return self._json(413, dict(error='voice core too large'))
        try:
            data = json.loads(self.rfile.read(length))
            profiles = voice_profile.read_core(data)
        except (ValueError, voice_profile.ProfileError) as exc:
            return self._json(400, dict(error=str(exc)[:200]))
        core = protocol.digest(data)
        self.service.sessions.set_voice(ident, core, profiles)
        return self._json(200, dict(core=core, profiles=len(profiles)))

    def _voice(self):
        """None without X-Servitor-Voice, else the session's matching profiles
        or VOICE_MISSING (unknown session or digest: strict, never v1)."""
        named = self.headers.get('X-Servitor-Voice', '')[:64]
        if not named:
            return None
        session = self.headers.get('X-Servitor-Session', '')[:64]
        profiles = self.service.sessions.voice(session, named) if session else None
        return profiles if profiles else VOICE_MISSING

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
                                                   handles=sorted(HANDLED),
                                                   features=list(FEATURES)))
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
        if url.path == '/v2/enroll/clip':
            return self._enroll_clip()
        if url.path == '/v1/nightwatch':
            return self._nightwatch()
        if url.path not in ('/v1/turn', '/v1/speak', '/v1/story', '/v1/ask'):
            self.close_connection = True
            return self._json(404, dict(error='not found'))
        self.close_connection = True  # never reuse a connection with an unread body
        self.connection.settimeout(self.service.config.idle_timeout)
        if not self.service.authorized(self.headers.get('Authorization')):
            return self._json(401, dict(error='unauthorized'))
        self.service.nightwatch.foreground()
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

        text = typed = None
        story_state = None
        if url.path in ('/v1/speak', '/v1/story', '/v1/ask'):
            if chunked:
                return self._json(411, dict(error='length required'))
            try:
                raw = self.rfile.read(length)
            except (OSError, TimeoutError):
                return
            if len(raw) != length:
                return self._json(400, dict(error='truncated body'))
            if url.path == '/v1/story':
                try:
                    story_state = stories.sanitize(json.loads(raw)['story'])
                except (ValueError, KeyError, TypeError):
                    story_state = None
                if story_state is None:
                    return self._json(400, dict(error='expected JSON {"story": state}'))
            else:
                try:
                    text = json.loads(raw)['text']
                except (ValueError, KeyError, TypeError):
                    text = None
                if not isinstance(text, str):
                    return self._json(400, dict(error='expected JSON {"text": "..."}'))
                text = text.strip()
                if not text:
                    return self._json(400, dict(error='text must not be empty'))
                if url.path == '/v1/ask':
                    if len(text) > MAX_TYPED_CHARS:
                        return self._json(413, dict(error='question too long'))
                    text, typed = None, text
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
        voice = self._voice()
        import nightwatch
        nightwatch_copy = nightwatch.decode_view(self.headers.get('X-Servitor-Nightwatch', ''))

        appointments = agenda.decode_header(self.headers.get('X-Servitor-Agenda', ''),
                                            self.service.now().tzinfo)
        appointments_tomorrow = agenda.decode_header(
            self.headers.get('X-Servitor-Agenda-Tomorrow', ''), self.service.now().tzinfo)
        if (session_event or {}).get('state') == 'unknown' or voice is VOICE_MISSING:
            # The memory (with the voiceprints) did not arrive: nobody can check
            # the voice, so the calendar stays unused too.
            appointments = appointments_tomorrow = None

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
                if story_state is not None:
                    self.service.run_story(story_state, emit, fmt, device=device)
                else:
                    self.service.run_turn(body, emit, fmt, text=text, device=device,
                                          memory_copy=memory_copy,
                                          transcribe_only=transcribe_only,
                                          appointments=appointments,
                                          appointments_tomorrow=appointments_tomorrow,
                                          voice=voice, nightwatch_copy=nightwatch_copy,
                                          typed=typed)
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
