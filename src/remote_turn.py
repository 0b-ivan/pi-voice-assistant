"""Client for the Servitor voice service (server/servitor_server.py).

While the push-to-talk button is held, 16 kHz mono s16le PCM is streamed as a
chunked ``POST /v1/turn`` body. After release the NDJSON reply is read in a
background job: stages, transcript, reply, base64 audio and timings. The PTT
controller maps those events to the display and plays the audio locally.

No GPIO, ALSA capture or Piper in here. The bearer token is only sent as a
header and never logged.
"""
from dataclasses import dataclass
import base64
import binascii
import http.client
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import urllib.parse
import wave

from voice_controls import _terminate_process_group


FORMATS = ('wav', 'opus')
MAX_RESPONSE_LINE = 16 * 1024 * 1024
MAX_AUDIO_BYTES = 8 * 1024 * 1024
# Only "heard nothing" is final. Upload limits (too_short/too_large) differ
# between Pi and server, so the saved capture still goes to local Vosk.
NO_FALLBACK_CODES = {'no_speech'}

_END = object()
_CANCEL = object()


class RemoteTurnError(RuntimeError):
    def __init__(self, stage, code, message):
        super().__init__(message)
        self.stage, self.code, self.message = stage, code, message


@dataclass(frozen=True)
class RemoteConfig:
    base_urls: tuple
    token: str
    audio_format: str = 'wav'
    connect_timeout: float = 1.5
    response_timeout: float = 25.0
    access_client_id: str = ''
    access_client_secret: str = ''

    @property
    def hosts(self):
        return [urllib.parse.urlsplit(url).netloc for url in self.base_urls]


def _status_error(response):
    try:
        detail = json.loads(response.read(512) or b'{}').get('error', '')
    except (ValueError, AttributeError, OSError):
        detail = ''
    code = {401: 'unauthorized', 429: 'rate_limited', 503: 'busy'}.get(
        response.status, f'http_{response.status}')
    return RemoteTurnError('upload', code, f'HTTP {response.status} {detail}'.strip())


def _float(env, name, default, minimum, maximum):
    value = float(env.get(name, '') or default)
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return value


def load_remote_config(env=None):
    """Return None when ASSISTANT_BASE_URL is empty: local-only operation."""
    env = os.environ if env is None else env
    urls = tuple(url.strip().rstrip('/')
                 for url in env.get('ASSISTANT_BASE_URL', '').split(',') if url.strip())
    if not urls:
        return None
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        try:
            parts.port  # raises ValueError for non-numeric or out-of-range ports
        except ValueError:
            parts = None
        if parts is None or parts.scheme not in ('http', 'https') or not parts.hostname:
            raise ValueError('ASSISTANT_BASE_URL entries must be http(s)://host[:port]')
    token = env.get('ASSISTANT_TOKEN', '').strip()
    if len(token) < 32:
        raise ValueError('ASSISTANT_TOKEN must be set (at least 32 characters)')
    audio_format = env.get('ASSISTANT_AUDIO_FORMAT', 'wav').strip().lower() or 'wav'
    if audio_format not in FORMATS:
        raise ValueError('ASSISTANT_AUDIO_FORMAT must be wav or opus')
    client_id = env.get('ASSISTANT_CF_ACCESS_CLIENT_ID', '').strip()
    client_secret = env.get('ASSISTANT_CF_ACCESS_CLIENT_SECRET', '').strip()
    if bool(client_id) != bool(client_secret):
        raise ValueError('set both ASSISTANT_CF_ACCESS_CLIENT_ID and _SECRET or neither')
    return RemoteConfig(
        base_urls=urls,
        token=token,
        audio_format=audio_format,
        connect_timeout=_float(env, 'ASSISTANT_CONNECT_TIMEOUT_SECONDS', 1.5, 0.2, 10),
        response_timeout=_float(env, 'ASSISTANT_RESPONSE_TIMEOUT_SECONDS', 25, 2, 120),
        access_client_id=client_id,
        access_client_secret=client_secret,
    )


def _open_connection(url, timeout):
    parts = urllib.parse.urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    return cls(parts.hostname, parts.port, timeout=timeout)


class RemoteTurnUplink:
    """Streams one capture to /v1/turn. ``accept_pcm`` never blocks or raises.

    The connection is opened in a sender thread as soon as recording starts, so
    a slow or missing server cannot back up arecord. Base URLs are tried in
    order; once the body has started there is no switching to the next one.
    """

    def __init__(self, config, connect=_open_connection, status=None):
        self.config = config
        # Pi status snapshot (numbers only) so the server can answer "status".
        self.status = status
        self._connect = connect
        self._queue = queue.SimpleQueue()
        self._lock = threading.Lock()
        self.connection = None
        self.host = None
        self.error = None
        self.rejection = None
        self.cancelled = False
        self.sent_bytes = 0
        self.upload_finished_at = None
        self.thread = threading.Thread(target=self._send, name='remote-uplink', daemon=True)
        self.thread.start()

    def accept_pcm(self, pcm):
        if pcm and self.error is None and not self.cancelled:
            self._queue.put(bytes(pcm))

    def finish(self):
        self._queue.put(_END)

    def cancel(self):
        self.cancelled = True
        self._queue.put(_CANCEL)
        self.close()

    def close(self):
        with self._lock:
            connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _request(self, url):
        connection = self._connect(url, self.config.connect_timeout)
        try:
            connection.connect()
            # From here on: per-operation timeout for upload and the reply.
            connection.sock.settimeout(self.config.response_timeout)
            path = urllib.parse.urlsplit(url).path.rstrip('/')
            connection.putrequest('POST', f'{path}/v1/turn?format={self.config.audio_format}',
                                  skip_accept_encoding=True)
            connection.putheader('Authorization', f'Bearer {self.config.token}')
            # Access credentials only travel encrypted (Internet path), never
            # to a plain-HTTP LAN endpoint configured alongside it.
            if self.config.access_client_id and urllib.parse.urlsplit(url).scheme == 'https':
                connection.putheader('CF-Access-Client-Id', self.config.access_client_id)
                connection.putheader('CF-Access-Client-Secret', self.config.access_client_secret)
            connection.putheader('Content-Type', 'application/octet-stream')
            connection.putheader('Transfer-Encoding', 'chunked')
            connection.putheader('Accept', 'application/x-ndjson')
            if self.status:
                connection.putheader('X-Servitor-Status',
                                     json.dumps(self.status, separators=(',', ':')))
            connection.endheaders()
        except BaseException:
            connection.close()
            raise
        return connection

    def _open(self):
        errors = []
        for url in self.config.base_urls:
            if self.cancelled:
                return None
            try:
                connection = self._request(url)
            except (OSError, http.client.HTTPException) as exc:
                errors.append(f'{urllib.parse.urlsplit(url).netloc}: {exc or type(exc).__name__}')
                continue
            with self._lock:
                if self.cancelled:
                    connection.close()
                    return None
                self.connection = connection
            self.host = urllib.parse.urlsplit(url).netloc
            return connection
        raise OSError('; '.join(errors) or 'no server configured')

    def _send(self):
        try:
            connection = self._open()
            while connection is not None:
                item = self._queue.get()
                if item is _CANCEL or self.cancelled:
                    return
                if item is _END:
                    connection.send(b'0\r\n\r\n')
                    self.upload_finished_at = time.monotonic()
                    return
                connection.send(b'%x\r\n%s\r\n' % (len(item), item))
                self.sent_bytes += len(item)
        except (OSError, http.client.HTTPException) as exc:
            if not self.cancelled:
                self.error = str(exc) or type(exc).__name__
                self.rejection = self._early_rejection()
            self.close()

    def _early_rejection(self):
        """The server answers 401/429/503 before reading the body, then closes."""
        connection = self.connection
        if connection is None or connection.sock is None:
            return None
        try:
            connection.sock.settimeout(1)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            return None
        return None if response.status == 200 else _status_error(response)

    def wait_uploaded(self, timeout):
        self.thread.join(timeout)
        if self.thread.is_alive():
            # Possibly still in DNS/connect: cancel so a late connection is
            # closed by _open() instead of being published and left open.
            self.cancel()
            raise RemoteTurnError('upload', 'network', 'upload did not finish in time')
        if self.cancelled:
            raise RemoteTurnError('upload', 'cancelled', 'cancelled')
        if self.rejection is not None:
            raise self.rejection
        if self.error is not None:
            raise RemoteTurnError('upload', 'network', self.error)

    def responses(self):
        """Yield the server's NDJSON events after the upload has completed."""
        connection = self.connection
        if connection is None:
            raise RemoteTurnError('upload', 'network', 'connection closed')
        response = connection.getresponse()
        if response.status != 200:
            raise _status_error(response)
        while True:
            line = response.readline(MAX_RESPONSE_LINE)
            if not line:
                return
            if not line.endswith(b'\n') and len(line) >= MAX_RESPONSE_LINE:
                raise RemoteTurnError('stream', 'protocol', 'response line too long')
            if line.strip():
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise RemoteTurnError('stream', 'protocol', 'event is not an object')
                yield item


def decode_opus(source, target, ffmpeg=None, run=subprocess.run):
    ffmpeg = ffmpeg or os.environ.get('TTS_FFMPEG_BIN', '/usr/bin/ffmpeg')
    run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
         '-i', str(source), '-f', 'wav', '-c:a', 'pcm_s16le', str(target)],
        check=True, timeout=20, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class RemoteTurnJob:
    """Reads one server reply in the background.

    Progress events are queued for the controller's main loop (``drain``) so
    all journal/display publishing stays on one thread. Transcript and reply
    are kept even on failure: the controller resumes locally from there.
    """

    def __init__(self, uplink, directory, decode=decode_opus):
        self.uplink = uplink
        self.directory = Path(directory)
        self.decode = decode
        self.cancelled = False
        self.done = threading.Event()
        self.result = None
        self.error = None
        self.error_stage = None
        self.error_code = None
        self.transcript = None
        self.reply = None
        self.model = None
        self._events = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._run, name='remote-turn', daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled = True
        self.uplink.cancel()

    def drain(self):
        items = []
        while True:
            try:
                items.append(self._events.get_nowait())
            except queue.Empty:
                return items

    def _store_audio(self, item):
        fmt = item.get('format')
        if fmt not in FORMATS:
            raise RemoteTurnError('stream', 'protocol', 'unknown audio format')
        try:
            data = base64.b64decode(item.get('data', ''), validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise RemoteTurnError('stream', 'protocol', 'invalid audio payload') from exc
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise RemoteTurnError('stream', 'protocol', 'empty or oversized audio')
        target = self.directory / 'remote-reply.wav'
        partial = self.directory / f'.remote-reply.{fmt}.part'
        decoded = self.directory / '.remote-reply.decoded.wav'
        try:
            partial.write_bytes(data)
            if fmt == 'opus':
                self.decode(partial, decoded)
                partial.unlink(missing_ok=True)
                partial = decoded
            with wave.open(str(partial), 'rb') as audio:
                if audio.getnframes() <= 0 or audio.getsampwidth() != 2:
                    raise RemoteTurnError('stream', 'protocol', 'unexpected WAV format')
            os.replace(partial, target)
        except (OSError, EOFError, wave.Error, subprocess.SubprocessError) as exc:
            raise RemoteTurnError('play', 'audio', f'cannot prepare audio: {exc}') from exc
        finally:
            partial.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)
        return target

    def _run(self):
        audio = None
        timings = {}
        try:
            self.uplink.wait_uploaded(self.uplink.config.response_timeout)
            deadline = time.monotonic() + self.uplink.config.response_timeout
            for item in self.uplink.responses():
                if self.cancelled:
                    return
                if time.monotonic() > deadline:
                    raise RemoteTurnError('stream', 'network', 'server reply timed out')
                kind = item.get('event')
                if kind == 'error':
                    raise RemoteTurnError(str(item.get('stage', 'server')),
                                          str(item.get('code', 'server')),
                                          str(item.get('message', 'server error')))
                if kind == 'transcript':
                    self.transcript = str(item.get('text', '')).strip() or None
                elif kind == 'reply':
                    self.reply = str(item.get('text', '')).strip() or None
                    self.model = item.get('model')
                elif kind == 'audio':
                    audio = self._store_audio(item)
                    item = {key: value for key, value in item.items() if key != 'data'}
                elif kind == 'done':
                    timings = item.get('timings') or {}
                self._events.put(item)
            if audio is None:
                raise RemoteTurnError('stream', 'incomplete', 'reply ended without audio')
            self.result = dict(audio=audio, timings=timings, host=self.uplink.host)
        except RemoteTurnError as exc:
            self.error, self.error_stage, self.error_code = exc.message, exc.stage, exc.code
        except (OSError, http.client.HTTPException, ValueError) as exc:
            self.error = str(exc) or type(exc).__name__
            self.error_stage, self.error_code = 'stream', 'network'
        finally:
            self.uplink.close()
            self.done.set()

    @property
    def fallback_allowed(self):
        return self.error is not None and self.error_code not in NO_FALLBACK_CODES


ENVELOPE_STEP = 0.05


def envelope_path():
    runtime = Path(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt'))
    return Path(os.environ.get('PI_DISPLAY_ENVELOPE_FILE', str(runtime / 'speech-envelope.json')))


def speech_envelope(path, step=ENVELOPE_STEP):
    """Loudness per 50 ms as 0..100 of the loudest window (16-bit WAV)."""
    from array import array
    with wave.open(str(path), 'rb') as audio:
        rate, channels = audio.getframerate(), audio.getnchannels()
        samples = array('h')
        samples.frombytes(audio.readframes(audio.getnframes()))
    window = max(1, int(rate * step)) * channels
    levels = []
    for start in range(0, len(samples), window):
        chunk = samples[start:start + window:4 * channels] or samples[start:start + 1]
        levels.append(math.sqrt(sum(v * v for v in chunk) / len(chunk)))
    peak = max(levels) if levels else 0
    return [round(100 * (level / peak) ** 0.7) if peak else 0 for level in levels]


def publish_envelope(path, started):
    """For the display's eye; computed after playback started, never blocking it."""
    target = envelope_path()
    try:
        levels = speech_envelope(path)
        temporary = target.with_name(f'.{target.name}.tmp')
        temporary.write_text(json.dumps(dict(start=started, step=ENVELOPE_STEP, levels=levels)))
        os.replace(temporary, target)
    except (OSError, EOFError, wave.Error, ValueError):
        pass


class RemoteCapableSpeech:
    """Local speech output plus playback of finished server audio files."""

    def __init__(self, speech, device, popen=subprocess.Popen):
        self.speech = speech
        self.device = device
        self.popen = popen
        self.player = None

    @property
    def active(self):
        return (self.player is not None and self.player.poll() is None) or self.speech.active

    @property
    def voice_level(self):
        if self.player is not None and self.player.poll() is None:
            return 1.0
        return getattr(self.speech, 'voice_level', 1.0)

    @property
    def synthesizing(self):
        return getattr(self.speech, 'synthesizing', False)

    def start(self, text):
        self._stop_player()
        self.speech.start(text)

    def play(self, path):
        self.stop()
        self.player = self.popen(
            ['/usr/bin/aplay', '-q', '-D', self.device, str(path)],
            stdin=subprocess.DEVNULL, start_new_session=True)
        started = time.time()
        threading.Thread(target=publish_envelope, args=(path, started),
                         name='speech-envelope', daemon=True).start()

    def poll(self):
        if self.player is not None:
            code = self.player.poll()
            if code is not None:
                self.player = None
            return code
        return self.speech.poll()

    def _stop_player(self):
        player, self.player = self.player, None
        if player is not None and player.poll() is None:
            _terminate_process_group(player)

    def stop(self):
        self._stop_player()
        self.speech.stop()
