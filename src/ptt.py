#!/usr/bin/env python3
"""Local hold-to-talk recorder with selectable STT. Requires libgpiod Python API v2."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import shlex
import subprocess
import threading
import time
import wave

import functools
from llm import LORE_LEVELS, configured_model, generate_reply, lore_level
import alarm_audio
import maintenance
import memory as memory_core
import sysmon
from alarms import (ALARMS, SHUTDOWN_FAILED, SHUTDOWN_NOW, WAKE_PHRASES, AlarmMonitor,
                    memory_phrase)
from endpoint import Endpointer
from netprobe import InternetProbe, network_up
import wlan as wlan_radio
from menu import ITEMS as MENU_ITEMS, Menu
from remote_turn import RemoteCapableSpeech, RemoteTurnJob, RemoteTurnUplink, load_remote_config
import datetime
import intents
from power import Battery, throttled_flags
from system_status import collect_snapshot, status_text
from transcribe import (
    LiveVoskRecognizer, RemoteLiveVoskRecognizer, TranscriptionError, prepare_vosk, transcribe_with_provider, prepare_vosk_worker, stop_prepared_vosk
)
from runtime_metrics import display_progress, phase
from voice_controls import ResidentSpeechOutput, SpeechOutput, TranscriptionJob, change_volume


DISPLAY_EVENTS = {
    'stt_loading', 'stt_ready', 'stt_live_error', 'stt_error',
    'tts_loading', 'tts_ready', 'tts_error',
    'waiting_for_release', 'recording', 'capture_ready', 'processing',
    'transcript', 'transcript_discarded', 'cancelled', 'busy',
    'llm_start', 'llm_response', 'llm_discarded', 'llm_error',
    'status', 'speech_started', 'speech_finished', 'speech_error',
    'wake_timeout',
}
DISPLAY_ERROR_EVENTS = {'stt_error', 'llm_error', 'tts_error', 'speech_error'}
DISPLAY_ERROR_HOLD_SECONDS = 3.0
_display_last_error_at = None


def display_event_path():
    runtime_dir = os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt')
    default = str(Path(runtime_dir) / 'display-event.json')
    return Path(os.environ.get('PTT_DISPLAY_EVENT_PATH', default))


def publish_display_event(name):
    """Publish the latest display state without persisting sensitive event fields."""
    global _display_last_error_at

    if name not in DISPLAY_EVENTS:
        return

    now = time.time()
    if name in DISPLAY_ERROR_EVENTS:
        _display_last_error_at = now

    path = display_event_path()
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        if not path.parent.is_dir():
            return
        payload = {
            'version': 1,
            'event': name,
            'timestamp': now,
        }
        if (
            _display_last_error_at is not None
            and now - _display_last_error_at < DISPLAY_ERROR_HOLD_SECONDS
        ):
            payload['error_timestamp'] = _display_last_error_at
        tmp.write_text(json.dumps(payload) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


# Wake word: published label per model file, echo pause after own speech.
WAKE_LABELS = {'hey_jarvis_v0.1': 'hey_jarvis', 'hey_servitor': 'hey_servitor'}
WAKE_ECHO_PAUSE = 0.6
# Display status: fixed identifiers and numbers only, never transcripts or
# reply text. route/last_route: 'server' (CT 107) or 'pi'; last_llm:
# 'openrouter' or 'offline' (local model on the server).
DISPLAY_STATUS_VALUES = {
    'route': {'server', 'pi'},
    'last_route': {'server', 'pi'},
    'last_llm': {'openrouter', 'offline', 'intent'},
    'volume_limit': {'min', 'max'},
    'menu_page': {'list', 'info'},
    'opt_server': {'on', 'off', 'none'},
    'opt_led': {'on', 'off'},
    'screen': {'on', 'off'},
    'power': {'awake', 'rest', 'sleep'},
    'memory': {'on', 'off'},
    'maint': {'on', 'off'},
    'maint_index': set(range(len(maintenance.ITEMS))),
    'maint_confirm': set(maintenance.ITEMS),
    'maint_pi': set(maintenance.STATES),
    'maint_server': set(maintenance.STATES),
    'opt_wake': {'on', 'off', 'none'},
    'opt_lore': set(LORE_LEVELS),
    'opt_wlan': {'on', 'off'},
    'opt_alarms': {'on', 'off'},
    'opt_llm': {'auto', 'local'},
    'alarm': set(ALARMS),
    'wake_word': set(WAKE_LABELS.values()),
}
# SHIM LED palette (APA102 at the driver's fixed low global brightness).
LED_OFF = (0, 0, 0)
LED_READY = (0, 90, 30)          # idle, next turn goes to the server or local-only setup
LED_READY_LOCAL = (120, 70, 0)   # idle, server switched off or last turn fell back
LED_RECORDING = (255, 0, 0)
LED_SERVER = (0, 170, 255)       # processing on CT 107 (display: SERVER, cyan)
LED_LOCAL = (255, 120, 0)        # processing on the Pi (display: LOKAL, amber)
LED_SPEAKING = (255, 100, 0)
LED_MENU = (150, 0, 255)
LED_PULSE_SECONDS = 0.6          # bright/dim half period while processing
SHIM_RETRY_SECONDS = 2.0        # reconnect the Button SHIM after an I2C error ...
SHIM_RETRY_MAX_SECONDS = 60.0   # ... backing off to this while it keeps failing
WLAN_GRACE_SECONDS = 60.0       # no link alarms while WLAN reconnects
# Idle power stages: 'rest' dims the display and calms the skull, 'sleep'
# switches screen and LED off (optionally WLAN); the wake word keeps listening.
REST_SECONDS = 30.0
SLEEP_SECONDS = 600.0
LED_ALARM = (255, 60, 0)         # slow blink while a critical alarm is active
CRITICAL_ALARMS = {'undervoltage', 'battery', 'memory', 'temperature'}
# Holding C/D repeats the volume step after a short pause.
VOLUME_REPEAT_DELAY = 0.45
VOLUME_REPEAT_INTERVAL = 0.15
_display_status = {}


def display_status_path():
    runtime_dir = os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt')
    default = str(Path(runtime_dir) / 'display-status.json')
    return Path(os.environ.get('PTT_DISPLAY_STATUS_PATH', default))


def llm_kind(model):
    model = str(model or '')
    if model == 'local/intent':
        return 'intent'
    return 'offline' if model.startswith('local/') else 'openrouter'


def publish_display_status(**fields):
    """Merge whitelisted status fields and atomically replace the status file."""
    for key, value in fields.items():
        if value is None:
            _display_status.pop(key, None)  # unknown now: never show a stale value
            continue
        if key in DISPLAY_STATUS_VALUES:
            if value not in DISPLAY_STATUS_VALUES[key]:
                continue
        elif key == 'last_latency_ms':
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
        elif key == 'volume':
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                continue
        elif key == 'menu_index':
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < len(MENU_ITEMS):
                continue
        elif key in ('upd_pi', 'upd_pi_sec', 'upd_srv', 'upd_srv_sec'):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9999:
                continue
        elif key == 'volume_at':
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                continue
        else:
            continue
        _display_status[key] = value
    path = display_status_path()
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        if not path.parent.is_dir():
            return
        payload = dict(version=1, timestamp=time.time(), **_display_status)
        tmp.write_text(json.dumps(payload) + '\n', encoding='utf-8')
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def event(name, **fields):
    print(json.dumps(dict(version=1, event=name, **fields)), flush=True)
    publish_display_event(name)


def process_capture(path):
    """Transcribe one published capture; STT failure must not kill PTT."""
    event('processing', path=str(path))
    try:
        text, provider = transcribe_with_provider(path)
    except (OSError, TranscriptionError) as exc:
        event('stt_error', message=str(exc))
        return None
    event('transcript', text=text, provider=provider)
    print(f'ERKANNT: {text}', flush=True)
    return text


class Button:
    """Stable-level debounce; require release at boot and after a limit/error."""
    def __init__(self, debounce=0.04, limit=30):
        self.debounce = debounce
        self.limit = limit
        self.raw = None
        self.stable = None
        self.changed = 0
        self.armed = False
        self.started = None

    def update(self, pressed, now):
        if pressed != self.raw:
            self.raw, self.changed = pressed, now
        action = None
        if now - self.changed >= self.debounce and pressed != self.stable:
            self.stable = pressed
            if not pressed:
                self.armed = True
                if self.started is not None:
                    action = 'release'
                    self.started = None
            elif self.armed:
                self.armed = False
                self.started = now
                action = 'start'
        if self.started is not None and now - self.started >= self.limit:
            self.started = None
            action = 'limit'
        return action

    def failed(self):
        self.started = None
        self.armed = self.stable is False

    def resync(self, pressed, now):
        """Resync GPIO after blocking processing; held buttons require release."""
        self.raw = pressed
        self.stable = pressed
        self.changed = now
        self.started = None
        self.armed = not pressed


class Recorder:
    def __init__(self, directory, device, limit, live_vosk_factory=None, uplink_factory=None):
        self.directory = Path(directory)
        self.device = device
        self.limit = limit
        self.live_vosk_factory = live_vosk_factory
        self.uplink_factory = uplink_factory
        self.uplink_enabled = True  # menu "Server nutzen"
        self._uplink = None
        self.process = None
        self.raw = self.directory / 'capture.part.pcm'
        self.partial = self.directory / 'capture.part.wav'
        self.ready = self.directory / 'capture.wav'
        self._pump_thread = None
        self._live_result = None
        self._live_error = None
        self._capture_rate = 48000
        self._capture_channels = 2
        self._live_recognizer = None
        self._endpoint = None  # set for wake-word recordings without a button
        self.endpoint_result = None

    def _pump_live_audio(self, proc, recognizer, uplink=None):
        recognizer_ok = recognizer is not None
        try:
            with self.raw.open('wb') as sink:
                while True:
                    chunk = proc.stdout.read(3200)
                    if not chunk:
                        break
                    sink.write(chunk)
                    if self._endpoint is not None and self.endpoint_result is None:
                        self.endpoint_result = self._endpoint.feed(chunk)
                    if uplink is not None:
                        uplink.accept_pcm(chunk)
                    if recognizer_ok:
                        try:
                            recognizer.accept_pcm(chunk)
                        except (OSError, TranscriptionError) as exc:
                            self._live_error = str(exc)
                            recognizer_ok = False
                            if hasattr(recognizer, 'cancel'):
                                recognizer.cancel()
            if uplink is not None:
                uplink.finish()
            if recognizer_ok:
                try:
                    self._live_result = (recognizer.finish(), 'vosk')
                except (OSError, TranscriptionError) as exc:
                    self._live_error = str(exc)
        finally:
            if proc.stdout is not None:
                proc.stdout.close()

    def start(self, auto_stop=False):
        """auto_stop: no button to release (wake word); endpoint_result turns
        'end' after speech plus a pause or 'timeout' if nobody speaks."""
        # A timed-out native recognizer still owns its file and result fields.
        # Never reset them or start a second recognizer before it has exited.
        if self._pump_thread is not None:
            if self._pump_thread.is_alive():
                raise RuntimeError('previous live Vosk audio pump is still draining')
            self._pump_thread = None
        if self.process is not None:
            raise RuntimeError('recording is already active')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ready.unlink(missing_ok=True)
        self.raw.unlink(missing_ok=True)
        self.partial.unlink(missing_ok=True)
        self._live_result = None
        self._live_error = None
        self._pump_thread = None
        self.drop_uplink()
        self._endpoint = Endpointer() if auto_stop else None
        self.endpoint_result = None

        uplink = None
        if self.uplink_factory is not None and self.uplink_enabled:
            try:
                uplink = self.uplink_factory()
            except (OSError, ValueError) as exc:
                event('remote_error', stage='connect', code='client', message=str(exc))
        self._uplink = uplink

        # The server recognizes the stream; shadowing it with local Vosk costs
        # the Pi CPU, swap and ~1 s finalize. The fallback transcribes the WAV.
        recognizer = None
        if self.live_vosk_factory is not None and uplink is None:
            try:
                recognizer = self.live_vosk_factory()
            except (OSError, TranscriptionError) as exc:
                self._live_error = str(exc)

        if recognizer is not None or uplink is not None or auto_stop:
            self._live_recognizer = recognizer
            self._capture_rate = 16000
            self._capture_channels = 1
            self.process = subprocess.Popen([
                '/usr/bin/arecord', '-q', '-D', self.device, '-t', 'raw',
                '-f', 'S16_LE', '-r', '16000', '-c', '1',
                '-d', str(math.ceil(self.limit))],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
            self._pump_thread = threading.Thread(
                target=self._pump_live_audio,
                args=(self.process, recognizer, uplink),
                name='vosk-live' if recognizer is not None else 'capture-pump',
                daemon=True,
            )
            self._pump_thread.start()
            fields = dict(remote=True) if uplink is not None else {}
            if auto_stop:
                fields['trigger'] = 'wake'
            stt = 'vosk-live' if recognizer is not None else 'remote' if uplink is not None else 'file'
            event('recording', stt=stt, sample_rate=16000, channels=1, **fields)
            return

        isolated = os.environ.get('PTT_MEMORY_MODE') in ('isolated', 'hybrid')
        self._capture_rate = 16000 if isolated else 48000
        self._capture_channels = 1 if isolated else 2
        self.process = subprocess.Popen([
            '/usr/bin/arecord', '-q', '-D', self.device, '-t', 'raw',
            '-f', 'S16_LE', '-r', str(self._capture_rate), '-c', str(self._capture_channels),
            '-d', str(math.ceil(self.limit)), str(self.raw)],
            stdin=subprocess.DEVNULL)
        event('recording')

    def finish(self, reason, publish=True):
        proc, self.process = self.process, None
        if proc is None:
            return None
        try:
            interrupted = proc.poll() is None
            if interrupted:
                proc.send_signal(signal.SIGINT)
            try:
                code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
                raise RuntimeError('arecord did not stop within two seconds')

            if not publish:
                self.drop_uplink()
            if not publish and self._live_recognizer is not None:
                if hasattr(self._live_recognizer, 'cancel'):
                    self._live_recognizer.cancel()
            if self._pump_thread is not None:
                with phase('stt', 'live_finalize'):
                    self._pump_thread.join(timeout=10)
                if self._pump_thread.is_alive():
                    raise RuntimeError('live Vosk audio pump did not drain within ten seconds')

            if not publish:
                return None
            if code != 0 and not (interrupted and code in (1, -signal.SIGINT)):
                raise RuntimeError(f'arecord exit status {code}')

            rate = self._capture_rate
            channels = self._capture_channels
            frame_bytes = channels * 2
            min_frames = rate // 10
            max_frames = (math.ceil(self.limit) + 1) * rate
            size = self.raw.stat().st_size
            if (
                size % frame_bytes
                or not min_frames * frame_bytes <= size <= max_frames * frame_bytes
            ):
                raise RuntimeError('empty, unaligned or oversized PCM recording')

            with wave.open(str(self.partial), 'wb') as audio:
                audio.setnchannels(channels)
                audio.setsampwidth(2)
                audio.setframerate(rate)
                audio.writeframes(self.raw.read_bytes())
            with wave.open(str(self.partial), 'rb') as audio:
                frames = audio.getnframes()
                if (
                    audio.getnchannels(),
                    audio.getsampwidth(),
                    audio.getframerate(),
                ) != (channels, 2, rate):
                    raise RuntimeError('unexpected WAV format')
                if frames < min_frames or frames > max_frames:
                    raise RuntimeError('empty, too short or oversized recording')
                if len(audio.readframes(frames)) != frames * frame_bytes:
                    raise RuntimeError('truncated WAV payload')

            os.replace(self.partial, self.ready)
            event(
                'capture_ready',
                path=str(self.ready),
                reason=reason,
                format='wav',
                encoding='PCM_S16_LE',
                sample_rate=rate,
                channels=channels,
                frames=frames,
                live_stt=self._live_result is not None,
            )
            return self.ready
        finally:
            if self._pump_thread is None or not self._pump_thread.is_alive():
                self.raw.unlink(missing_ok=True)
                self.partial.unlink(missing_ok=True)
                self._pump_thread = None
                self._live_recognizer = None

    def take_uplink(self):
        uplink, self._uplink = self._uplink, None
        return uplink

    def drop_uplink(self):
        uplink = self.take_uplink()
        if uplink is not None:
            uplink.cancel()

    def take_live_transcript(self):
        result, error = self._live_result, self._live_error
        self._live_result = None
        self._live_error = None
        if error is not None:
            raise TranscriptionError(error)
        return result

    def close(self):
        self.finish('shutdown', publish=False)
        self.drop_uplink()
        self.ready.unlink(missing_ok=True)


class VoiceController:
    """One capture/STT/LLM slot, with A and GPIO17 combined as hold-to-talk."""
    def __init__(self, recorder, speech, debounce, limit, probe=False, remote=False,
                 wake=None, wake_word=None):
        self.recorder, self.speech, self.probe = recorder, speech, probe
        self.wake = wake                  # WakeListener or None
        self.wake_word = wake_word        # published label, e.g. 'hey_jarvis'
        self.wake_enabled = wake is not None
        self.wake_recording = False       # current recording ends on a pause
        self.wake_resume_at = 0.0
        self.wake_stats_at = 0.0
        self.remote = remote
        self.remote_capture = None
        # Release-to-playback bookkeeping for the display's "last answer" line.
        self.turn_released_at = None
        self.turn_route = None
        self.turn_llm = None
        self.volume_repeat = {}  # 'C'/'D' -> monotonic time of the next repeat
        self.menu = Menu()
        self.pitft = (Button(debounce, math.inf), Button(debounce, math.inf))
        self.remote_enabled = remote
        self.led_enabled = True
        self.screen_on = True
        self.lore = lore_level(os.environ.get('PTT_LORE_LEVEL'))  # off / light / full
        self.battery = None      # power.Battery reading, refreshed by main()
        self.throttled = None
        self.remote_failed = False
        self.server_probe = None  # netprobe.ServerProbe when a server is configured
        self.alarms = AlarmMonitor()
        self.alarms_enabled = os.environ.get('PTT_ALARMS', '1') != '0'
        self.alarm_queue = []    # sentences waiting until the unit is idle
        self.wlan_on = True
        self.link_grace_until = 0.0  # link alarms wait while WLAN reconnects
        self.power = 'awake'
        self.last_activity = time.monotonic()
        self.rest_after = float(os.environ.get('PTT_REST_SECONDS', REST_SECONDS))
        self.sleep_after = float(os.environ.get('PTT_SLEEP_SECONDS', SLEEP_SECONDS))
        self.sleep_wlan_off = os.environ.get('PTT_SLEEP_WLAN', 'keep') == 'off'
        self.wlan_slept = False      # WLAN was switched off by sleep, not by the user
        self.listen_after_greeting = False  # wake word woke us: listen after the greeting
        self.memory = memory_core.MemoryCore()
        self.memory_present = self.memory.present()
        self.turn_transcript = None
        self.network_watch = self.update_watch = None  # sysmon watches, started by main()
        self.maint = maintenance.Mode()
        self.alarms.notice_store = maintenance.NoticeStore()
        self.maint_jobs = {}          # target -> start time (time.time()) of a running action
        self.internet_probe = None  # netprobe.InternetProbe
        self.llm_mode = 'local' if os.environ.get('PTT_LLM_MODE', 'auto') == 'local' else 'auto'
        self.shutting_down = False
        self.ptt = Button(debounce, limit)
        self.commands = {name: Button(debounce, math.inf) for name in 'BCDE'}
        self.job = None
        self.job_stage = None
        self.job_started_at = None
        self.speech_started_at = None

    @staticmethod
    def _scale(color, factor):
        return tuple(int(round(value * factor)) for value in color)

    @property
    def color(self):
        """SHIM LED. Recording and errors always show; the rest follows the
        menu's LED switch. Few distinct colours on purpose: each change costs
        ~190 I2C writes, so animations are two-level pulses, not fades."""
        now = time.monotonic()
        if self.recorder.process is not None:
            return LED_RECORDING
        if (_display_last_error_at is not None
                and time.time() - _display_last_error_at < DISPLAY_ERROR_HOLD_SECONDS):
            return LED_RECORDING if int(now * 4) % 2 == 0 else LED_OFF
        if (self.alarms_enabled and CRITICAL_ALARMS.intersection(self.alarms.active)
                and self.job is None and not self.speech.active):
            return LED_ALARM if int(now) % 2 == 0 else LED_OFF
        if not self.led_enabled or self.power == 'sleep':
            return LED_OFF
        if self.menu.open:
            return LED_MENU
        if self.job is not None:
            base = LED_SERVER if self.turn_route == 'server' else LED_LOCAL
            return base if int(now / LED_PULSE_SECONDS) % 2 == 0 else self._scale(base, 0.25)
        if self.speech.active:
            # Three brightness steps follow the speech envelope.
            level = getattr(self.speech, 'voice_level', 1.0)
            try:
                level = max(0.0, min(1.0, float(level)))
            except (TypeError, ValueError):
                level = 1.0
            return self._scale(LED_SPEAKING, (0.3, 0.6, 1.0)[min(2, int(level * 3))])
        local = self.remote and (not self.remote_enabled or self.turn_route == 'pi')
        ready = LED_READY_LOCAL if local else LED_READY
        return self._scale(ready, 0.3) if self.power == 'rest' else ready

    def cancel(self, held, now):
        self.listen_after_greeting = False
        self.speech.stop()
        self.speech_started_at = None
        self.recorder.finish('cancel', publish=False)
        self.wake_recording = False
        if self.remote:
            self.recorder.drop_uplink()
        if self.job is not None:
            self.job.cancel()
        self.turn_released_at = None
        self.ptt.resync(held, now)
        event('cancelled')

    def submit(self, reason):
        try:
            capture = self.recorder.finish(reason)
        except BaseException:
            if self.remote:
                self.recorder.drop_uplink()
            raise
        uplink = self.recorder.take_uplink() if self.remote else None
        if capture is None:
            if uplink is not None:
                uplink.cancel()
            return
        event('processing', path=str(capture))
        self.turn_released_at = time.monotonic()
        self.turn_llm = None
        if uplink is not None:
            if uplink.error is None:
                self.remote_capture = capture
                self.job = RemoteTurnJob(uplink, capture.parent)
                self.job_stage = 'remote'
                self.job_started_at = time.monotonic()
                self._set_route('server')
                return
            uplink.cancel()
            # Keep the server's reason (unauthorized/rate_limited/busy) when it
            # rejected the upload early; otherwise it was the network.
            rejection = getattr(uplink, 'rejection', None)
            self.remote_failed = True
            event('remote_error', stage='upload',
                  code=rejection.code if rejection is not None else 'network',
                  message=rejection.message if rejection is not None else uplink.error)
            event('remote_fallback', target='stt')
        self._set_route('pi')
        self._start_local_stt(capture)

    def _volume_step(self, name):
        direction = 1 if name == 'D' else -1
        try:
            level = change_volume(direction)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.volume_repeat.pop(name, None)
            event('mixer_error', message=str(exc))
            return
        event('volume', direction='up' if direction > 0 else 'down', db=level['db'],
              percent=level['percent'], limit=level['limit'])
        publish_display_status(volume=level['percent'], volume_limit=level['limit'],
                               volume_at=time.time())

    def _publish_menu(self):
        if not self.remote:
            server = 'none'
        else:
            server = 'on' if self.remote_enabled else 'off'
        if self.wake is None:
            wake = 'none'
        else:
            wake = 'on' if self.wake_enabled else 'off'
        publish_display_status(menu_index=self.menu.index, menu_page=self.menu.page,
                               opt_server=server, opt_wake=wake, wake_word=self.wake_word,
                               opt_lore=self.lore,
                               opt_wlan='on' if self.wlan_on else 'off',
                               opt_alarms='on' if self.alarms_enabled else 'off',
                               opt_llm=self.llm_mode,
                               opt_led='on' if self.led_enabled else 'off',
                               screen='on' if self.screen_on else 'off')

    def _menu_confirm(self, now):
        item = self.menu.confirm(now)
        if item == 'maintenance':
            self._maintenance_op('enter', speak=True)
            return
        if item == 'server' and self.remote:
            self.remote_enabled = not self.remote_enabled
            self.recorder.uplink_enabled = self.remote_enabled
            event('menu', item='server', value='on' if self.remote_enabled else 'off')
        elif item == 'wake' and self.wake is not None:
            self.wake_enabled = not self.wake_enabled
            if not self.wake_enabled:
                self._release_microphone()
            event('menu', item='wake', value='on' if self.wake_enabled else 'off')
        elif item == 'lore':
            self.lore = LORE_LEVELS[(LORE_LEVELS.index(self.lore) + 1) % len(LORE_LEVELS)]
            event('menu', item='lore', value=self.lore)
        elif item == 'llm':
            self.llm_mode = 'local' if self.llm_mode == 'auto' else 'auto'
            event('menu', item='llm', value=self.llm_mode)
        elif item == 'wlan':
            self.set_wlan(not self.wlan_on)
        elif item == 'alarms':
            self.alarms_enabled = not self.alarms_enabled
            if not self.alarms_enabled:
                self.alarm_queue = []
            event('menu', item='alarms', value='on' if self.alarms_enabled else 'off')
        elif item == 'led':
            self.led_enabled = not self.led_enabled
            event('menu', item='led', value='on' if self.led_enabled else 'off')
        elif item == 'screen':
            self.screen_on = False
            event('menu', item='screen', value='off')
        elif item == 'status':
            self._speak_status(action=None)
        elif item is not None:
            event('menu', item=item)
        self._publish_menu()

    def server_state(self):
        """'off' (none/disabled), 'ok' or 'down'; the probe wins over the last turn."""
        if not self.remote or not self.remote_enabled or not self.wlan_on:
            return 'off'
        probed = getattr(self.server_probe, 'state', None)
        if probed in ('ok', 'down'):
            return probed
        return 'down' if self.remote_failed else 'ok'

    def status_snapshot(self):
        server = self.server_state()
        return collect_snapshot(battery=self.battery, throttled=self.throttled, server=server,
                                lore=self.lore, wlan='on' if self.wlan_on else 'off',
                                llm_mode=self.llm_mode,
                                extra=dict(sysmon.snapshot_fields(
                                    getattr(self.network_watch, 'result', None)
                                    if self.wlan_on else None,
                                    getattr(self.update_watch, 'result', None)),
                                    memory='on' if self.memory_present else 'off',
                                    maintenance='on' if self.maint.active else 'off'))

    def check_alarms(self, now, network=None):
        """Called every ~10 s by main(); queues alarm sentences to speak."""
        # WLAN off, or just switched back on and still connecting: links unknown.
        links = self.wlan_on and now >= self.link_grace_until
        if network is None and links:
            network = network_up()
        internet = getattr(self.internet_probe, 'state', None) if links else None
        texts = self.alarms.update(self.status_snapshot(), now,
                                   network=network if links else None,
                                   server=self.server_state() if links else 'off',
                                   lore=self.lore, internet=internet)
        texts += self._check_memory()
        maintenance_texts = self._check_maintenance()  # spoken even with alarms muted
        if self.power != 'sleep':  # maintenance can wait until someone is around
            # Wall clock: the last announcement survives service restarts.
            notice = self.alarms.updates_notice(self.status_snapshot(), time.time(), self.lore)
            if notice:
                texts.append(notice)
        for text in texts:
            event('alarm', text=text, active=self.alarms.active)
        if self.alarms_enabled:
            self.alarm_queue.extend(texts)
        self.alarm_queue.extend(maintenance_texts)
        active = self.alarms.active
        publish_display_status(alarm=active[0] if active else None)
        if self.alarms.shutdown_due(now) and not self.shutting_down:
            self.shutdown()

    # --- Maintenance mode ---------------------------------------------------

    def _publish_maintenance(self):
        jobs = {target: (self._maintenance_state(target) or {}).get('state')
                for target in maintenance.TARGETS}
        snapshot = self.status_snapshot() if self.maint.active else {}
        publish_display_status(upd_pi=snapshot.get('updates'),
                               upd_pi_sec=snapshot.get('updates_security'),
                               upd_srv=snapshot.get('server_updates'),
                               upd_srv_sec=snapshot.get('server_updates_security'))
        publish_display_status(maint='on' if self.maint.active else 'off',
                               maint_index=self.maint.index if self.maint.active else None,
                               maint_confirm=self.maint.pending,
                               maint_pi=jobs['pi'], maint_server=jobs['server'])

    def _maintenance_state(self, target):
        cache = getattr(self, '_maint_status', {})
        return cache.get(target)

    def _say(self, text):
        try:
            self._say_alarm([text], source='maintenance')
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _maintenance_op(self, op, speak=True):
        """Voice or menu: enter/exit or ask to confirm an action. Returns the sentence."""
        if op == 'enter':
            self.maint.enter()
            # From the menu, confirm() has already closed it: publish either way,
            # or the display keeps showing the stale menu.
            self.menu.close()
            self._publish_menu()
            text = maintenance.ENTER_TEXT
        elif op == 'exit':
            self.maint.exit()
            text = maintenance.EXIT_TEXT
        elif not self.maint.active:
            text = maintenance.NEED_MODE_TEXT
        else:
            self.maint.index = maintenance.ITEMS.index(op)
            self.maint.ask(op)
            text = maintenance.confirm_prompt(op, self.status_snapshot())
        event('maintenance', op=op, active=self.maint.active)
        self._publish_maintenance()
        if speak:
            self._say(text)
        return text

    def _maintenance_buttons(self, confirm, cancel):
        if cancel:
            if self.maint.pending is not None:
                self.maint.pending = None
                self._say(maintenance.CANCEL_TEXT)
            else:
                self._maintenance_op('exit')
        elif confirm:
            item = self.maint.take_confirmed()
            if item is not None:
                self._run_maintenance(item)
            else:
                chosen = maintenance.ITEMS[self.maint.index]
                if chosen == 'exit':
                    self._maintenance_op('exit')
                else:
                    self._maintenance_op(chosen)
        self._publish_maintenance()

    def _run_maintenance(self, item):
        action, target = maintenance.split(item)
        if target == 'pi':
            try:
                maintenance.request(action)
                outcome = 'accepted'
            except OSError:
                outcome = 'not_installed'
        else:
            outcome = maintenance.request_server(action)
        event('maintenance_run', action=action, target=target, outcome=outcome)
        if outcome == 'accepted':
            self.maint_jobs[target] = time.time()
            self._say(maintenance.START_TEXT[(action, target)])
        else:
            self._say(maintenance.FAIL_TEXT[outcome])

    def _check_maintenance(self):
        """Every ~10 s: follow running actions and announce their result."""
        if self.maint.expired():
            self._publish_maintenance()
        if self.maint.idle_too_long() and not self.maint_jobs:
            self.maint.exit()
            event('maintenance', op='exit', active=False, reason='idle')
        if not self.maint_jobs and not self.maint.active:
            return []
        cache = getattr(self, '_maint_status', {})
        out = []
        for target in maintenance.TARGETS:
            data = (maintenance.status() if target == 'pi'
                    else maintenance.server_status())
            cache[target] = data
            started = self.maint_jobs.get(target)
            if (started is not None and data and data.get('at', 0) >= int(started) - 1
                    and data['state'] in ('done', 'failed')):
                del self.maint_jobs[target]
                event('maintenance_done', target=target, **data)
                out.append(maintenance.result_text(target, data, self.lore))
                if self.update_watch is not None:
                    self.update_watch.refresh()
        self._maint_status = cache
        self._publish_maintenance()
        return out

    def _check_memory(self):
        """Announce plugging or pulling the memory stick."""
        present = self.memory.present()
        publish_display_status(memory='on' if present else 'off')
        if present == self.memory_present:
            return []
        self.memory_present = present
        counts = self.memory.counts() if present else None
        event('memory_core', present=present, **(counts or {}))
        if present and counts is None:
            return []  # plugged but not readable yet: next check
        return [memory_phrase(present, (counts or {}).get('facts'), self.lore)]

    def _memory_command(self, text):
        """Local fallback: memory commands answered and applied on the Pi."""
        command = memory_core.command(intents.normalize(text))
        if command is None:
            return None
        op, argument = command
        context = self.memory.context()
        reply = memory_core.reply(op, argument, context, self.lore)
        if context is not None and op in ('add_fact', 'add_directive', 'forget'):
            self.memory.apply(dict(op=op, text=argument))
            event('memory', op=op)
        return reply

    def _learn(self, reply):
        """Strip MERKE/DIREKTIVE lines from an LLM reply and store them."""
        spoken, learned = memory_core.split_learned(reply)
        for op, value in learned:
            if self.memory.apply(dict(op=op, text=value)):
                event('memory', op=op, learned=True)
        return spoken

    def shutdown(self):
        """Battery empty: say so, then power off (polkit rule for obivan)."""
        self.shutting_down = True
        event('shutdown', reason='battery')
        self.speech.stop()
        try:
            self._say_alarm([SHUTDOWN_NOW])
            deadline = time.monotonic() + 8
            while self.speech.active and time.monotonic() < deadline:
                time.sleep(0.1)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            subprocess.run(['/usr/bin/systemctl', 'poweroff'], check=True, timeout=15,
                           stdin=subprocess.DEVNULL, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self.shutting_down = False
            event('shutdown_error', message=str(exc))
            self.alarm_queue.append(SHUTDOWN_FAILED)

    def _speak_alarms(self):
        if not self.alarm_queue or not self._idle() or self.menu.open:
            return
        texts, self.alarm_queue = self.alarm_queue, []
        try:
            self._say_alarm(texts)
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _say_alarm(self, texts, source='alarm'):
        """Play prerecorded clips (no synthesis, works offline and under
        load); fall back to live synthesis when a clip is missing."""
        play = getattr(self.speech, 'play', None)
        path = (Path(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt')) / 'alarm.wav'
                if play is not None else None)
        if path is not None and alarm_audio.assemble(texts, path):
            event('speech_started', source=source, clips=True)
            play(path)
            return
        event('speech_started', source=source, clips=False)
        self.speech.start(' '.join(texts))

    def set_wlan(self, on):
        try:
            wlan_radio.set_wlan(on)
        except (OSError, subprocess.SubprocessError) as exc:
            event('wlan_error', message=str(exc))
            return False
        self.wlan_on = on
        if on:
            self.link_grace_until = time.monotonic() + WLAN_GRACE_SECONDS
        if self.remote:
            self.recorder.uplink_enabled = on and self.remote_enabled
        event('wlan', value='on' if on else 'off')
        return True

    def _release_microphone(self):
        """The wake listener holds the capture device; free it before recording."""
        if self.wake is not None and self.wake.running:
            self.wake.stop()

    def _idle(self):
        return (self.recorder.process is None and self.job is None
                and not self.speech.active and not getattr(self.speech, 'synthesizing', False))

    def _wake_tick(self, now):
        if self.wake.error:
            event('wake_error', message=self.wake.error)
            self.wake.error = None
            self.wake_resume_at = now + 30.0  # retry later, buttons keep working
        if self.wake.take_detection() and self._idle():
            event('wake', word=self.wake_word)
            if self.menu.open:
                self.menu.close()
                self._publish_menu()
            if self.power == 'sleep':
                # Asleep: announce the warm-up first, then listen.
                self.last_activity = now
                self._greet()
                self.listen_after_greeting = True
            else:
                self._start_wake_recording()
        if self.wake_recording and self.recorder.process is not None:
            result = self.recorder.endpoint_result
            if result == 'end':
                self.wake_recording = False
                self.submit('silence')
            elif result == 'timeout':
                self.wake_recording = False
                self.recorder.finish('wake_timeout', publish=False)
                if self.remote:
                    self.recorder.drop_uplink()
                event('wake_timeout')
        shadow = getattr(self.wake, 'take_shadow', lambda: ([], []))()
        if isinstance(shadow, tuple) and len(shadow) == 2:
            hits, peaks = shadow
            for word, score in hits:
                event('wake_shadow', word=word, score=score)
            for peak in peaks:
                if max(peak.values(), default=0) >= 0.05:  # skip plain room noise
                    event('wake_shadow_peak', **{k: round(v, 3) for k, v in peak.items()})
        detector = getattr(self.wake, 'detector', None)
        if detector is not None and now >= self.wake_stats_at:
            self.wake_stats_at = now + 60.0
            event('wake_stats', **detector.wakeword.stats())
        listen = self.wake_enabled and self._idle() and now >= self.wake_resume_at
        if listen and not self.wake.running:
            self.wake.start()
        elif not listen and self.wake.running:
            self.wake.stop()

    def _start_wake_recording(self):
        self._release_microphone()
        self.speech_started_at = None
        self.recorder.start(auto_stop=True)
        self.wake_recording = True

    def _greet(self):
        """Short prerecorded line when waking from sleep."""
        try:
            self._say_alarm([WAKE_PHRASES.get(self.lore, WAKE_PHRASES['light'])], source='wake')
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _speak_status(self, action):
        if (self.recorder.process is not None or action == 'start'
                or (os.environ.get('PTT_MEMORY_MODE') in ('isolated', 'hybrid')
                    and self.job is not None)):
            event('status_skipped', reason='recording_or_processing')
            return
        text = status_text(self.status_snapshot(), processing=self.job is not None)
        try:
            event('status', text=text)
            event('speech_started', source='status')
            self.speech.start(text)
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _pitft_input(self, pitft_pressed, now):
        up = self.pitft[0].update(pitft_pressed[0], now) == 'start'
        down = self.pitft[1].update(pitft_pressed[1], now) == 'start'
        if (up or down) and self.maint.active and self.power == 'awake':
            self.maint.move(-1 if up else 1)
            self._publish_maintenance()
            return
        if up or down:
            if self.power != 'awake':
                self._wake_up(now)  # first press only wakes the display
            elif not self.screen_on:
                self.screen_on = True  # first press only wakes the screen
                event('menu', item='screen', value='on')
            else:
                self.menu.move(-1 if up else 1, now)
            self._publish_menu()
        elif self.menu.expire(now):
            self._publish_menu()

    def _set_route(self, route):
        self.turn_route = route
        publish_display_status(route=route)

    def _turn_spoken(self):
        """First audio of an answer: publish how long the user waited."""
        if self.turn_released_at is None:
            return
        latency = round((time.monotonic() - self.turn_released_at) * 1000)
        self.turn_released_at = None
        publish_display_status(last_route=self.turn_route, last_llm=self.turn_llm,
                               last_latency_ms=latency)

    def _start_local_stt(self, capture):
        if capture is not None:
            try:
                live_result = self.recorder.take_live_transcript()
            except TranscriptionError as exc:
                event('stt_live_error', message=str(exc), fallback='wav')
                live_result = None
            if live_result is not None:
                self.job = TranscriptionJob(lambda _path: live_result, capture)
            else:
                self.job = TranscriptionJob(transcribe_with_provider, capture)
            self.job_stage = 'stt'
            self.job_started_at = time.monotonic()

    def _start_llm(self, text):
        self.turn_transcript = text
        op = maintenance.command(intents.normalize(text))
        if op is not None:
            reply = self._maintenance_op(op, speak=False)
            event('llm_response', text=reply, model='local/maintenance')
            self._start_speech(reply, source='assistant', model='local/maintenance')
            return
        reply = self._memory_command(text)
        if reply is not None:
            event('llm_response', text=reply, model='local/memory')
            self._start_speech(reply, source='assistant', model='local/memory')
            return
        intent = intents.match(text)
        if intent is None and self.llm_mode == 'local':
            # The Pi's own LLM path is OpenRouter; "LOKAL" forbids it.
            reply = "Daten unzureichend. Lokaler Sprachkern nicht erreichbar."
            event('llm_response', text=reply, model='local/none')
            self._start_speech(reply, source='assistant', model='local/none')
            return
        if intent is not None:
            # Time, date, status ...: answered on the Pi, also without network.
            reply = intents.answer(intent, datetime.datetime.now(), self.status_snapshot())
            self.turn_llm = 'intent'
            event('llm_response', text=reply, model='local/intent')
            print(f'SERVITOR: {reply}', flush=True)
            self._start_speech(reply, source='assistant', model='local/intent')
            return
        context = self.memory.context()
        self.job = TranscriptionJob(functools.partial(generate_reply, lore=self.lore,
                                                      memory=context), text)
        self.job_stage = 'llm'
        self.job_started_at = time.monotonic()
        event('llm_start', model=configured_model())

    def _start_speech(self, text, **fields):
        try:
            event('speech_started', **fields)
            self.speech.start(text)
            self.speech_started_at = time.monotonic()
            self._turn_spoken()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _remote_progress(self, item):
        """Map server NDJSON events onto the existing journal/display events."""
        kind = item.get('event')
        if kind == 'stage':
            stage = item.get('stage')
            if stage == 'recognize':
                display_progress('stt', 'live_finalize')
            elif stage == 'think':
                event('llm_start', model='remote')
            elif stage == 'synthesize':
                display_progress('tts', 'synthesis')
            elif stage == 'render':
                display_progress('tts', 'dsp_render')
        elif kind == 'transcript':
            text = str(item.get('text', ''))
            self.turn_transcript = text
            event('transcript', text=text, provider='remote')
            print(f'ERKANNT: {text}', flush=True)
        elif kind == 'maintenance':
            if item.get('op') in maintenance.ITEMS + ('enter',):
                self._maintenance_op(item['op'], speak=False)  # the server's reply speaks
        elif kind == 'memory':
            if self.memory.apply(item):
                event('memory', op=item.get('op'), learned=bool(item.get('learned')))
        elif kind == 'reply':
            text = str(item.get('text', ''))
            if self.turn_transcript:
                self.memory.remember_turn(self.turn_transcript, text)
            self.turn_llm = llm_kind(item.get('model'))
            event('llm_response', text=text, model=item.get('model'))
            print(f'SERVITOR: {text}', flush=True)
        elif kind == 'audio':
            event('remote_audio', format=item.get('format'),
                  duration_ms=item.get('duration_ms'), bytes=item.get('bytes'))
        elif kind == 'done':
            event('latency', stage='remote', metric='server',
                  timings=item.get('timings') or {})

    def _finish_remote(self, job):
        capture, self.remote_capture = self.remote_capture, None
        for item in job.drain():
            self._remote_progress(item)
        if job.cancelled:
            event('transcript_discarded')
            return
        if job.error is None:
            self.remote_failed = False
            event('remote_done', host=job.result.get('host'))
            try:
                event('speech_started', source='remote')
                display_progress('tts', 'playback')
                self.speech.play(job.result['audio'])
                self.speech_started_at = time.monotonic()
                self._turn_spoken()
            except (OSError, RuntimeError, ValueError) as exc:
                event('speech_error', message=str(exc))
            return
        event('remote_error', stage=job.error_stage, code=job.error_code, message=job.error)
        if job.error_stage in ('upload', 'stream'):
            self.remote_failed = True
        if not job.fallback_allowed:
            event('stt_error', message=job.error)
            self.turn_released_at = None
            return
        self._set_route('pi')
        if job.reply:
            event('remote_fallback', target='tts')
            self._start_speech(job.reply, source='assistant', model=job.model)
        elif job.transcript:
            event('remote_fallback', target='llm')
            self._start_llm(job.transcript)
        elif (job.error_stage in ('upload', 'recognize', 'stream', 'internal')
              and capture is not None):
            event('remote_fallback', target='stt')
            event('processing', path=str(capture))
            self._start_local_stt(capture)
        else:
            event('stt_error', message=job.error)

    def tick(self, gpio_pressed, shim_pressed, now, pitft_pressed=(False, False)):
        held = gpio_pressed or shim_pressed[0]
        action = self.ptt.update(held, now)
        commands = [name for i, name in enumerate('BCDE', 1)
                    if self.commands[name].update(shim_pressed[i], now) == 'start']
        if self.probe:
            if action:
                event('button', button='PTT', action=action)
            for name in commands:
                event('button', button=name, action='start')
            for name, button, pressed in zip(('UP', 'DOWN'), self.pitft, pitft_pressed):
                if button.update(pressed, now) == 'start':
                    event('button', button=name, action='start')
            return
        self._pitft_input(pitft_pressed, now)
        if self.maint.active and not self.menu.open and ('B' in commands or 'E' in commands):
            self._maintenance_buttons('E' in commands, 'B' in commands)
            commands = [name for name in commands if name not in 'BE']
        if 'B' in commands and self.menu.open:
            # B leaves the menu first; a second press cancels as usual.
            self.menu.close()
            self._publish_menu()
            commands = []
        if 'B' in commands:
            self.cancel(held, now)
            action = None
            # Simultaneous B/E never starts a new status utterance.
            commands = []
        for name in list(self.volume_repeat):
            if not self.commands[name].stable:
                del self.volume_repeat[name]
            elif name not in commands and now >= self.volume_repeat[name]:
                self.volume_repeat[name] = now + VOLUME_REPEAT_INTERVAL
                self._volume_step(name)
        for name in commands:
            if name in 'CD':
                self.volume_repeat[name] = now + VOLUME_REPEAT_DELAY
                self._volume_step(name)
            elif name == 'E':
                if self.menu.open:
                    self._menu_confirm(now)
                else:
                    self._speak_status(action)
        code = self.speech.poll()
        if code is not None:
            if self.speech_started_at is not None:
                event(
                    'latency',
                    stage='tts',
                    metric='playback_total',
                    latency_ms=round(
                        (time.monotonic() - self.speech_started_at) * 1000
                    ),
                )
                self.speech_started_at = None
            event('speech_finished' if code == 0 else 'speech_error', returncode=code)
            self.wake_resume_at = now + WAKE_ECHO_PAUSE  # do not hear our own tail
            if self.listen_after_greeting:
                self.listen_after_greeting = False
                if self._idle() and not self.menu.open:
                    self._start_wake_recording()
        if self.job is not None and self.job_stage == 'remote' and not self.job.done.is_set():
            for item in self.job.drain():
                self._remote_progress(item)
        if self.job is not None and self.job.done.is_set():
            job, self.job = self.job, None
            stage, self.job_stage = self.job_stage, None
            started_at, self.job_started_at = self.job_started_at, None
            if started_at is not None and stage in ('stt', 'llm'):
                event(
                    'latency',
                    stage=stage,
                    latency_ms=round((time.monotonic() - started_at) * 1000),
                )

            if stage == 'remote':
                self._finish_remote(job)
            elif job.cancelled:
                event('transcript_discarded' if stage == 'stt' else 'llm_discarded')
            elif job.error is not None:
                event('stt_error' if stage == 'stt' else 'llm_error', message=job.error)
            elif stage == 'stt':
                text, provider = job.result
                event('transcript', text=text, provider=provider)
                print(f'ERKANNT: {text}', flush=True)
                self._start_llm(text)
            elif stage == 'llm':
                reply, model = job.result
                reply = self._learn(reply)
                self.memory.remember_turn(self.turn_transcript or '', reply)
                self.turn_llm = llm_kind(model)
                event('llm_response', text=reply, model=model)
                print(f'SERVITOR: {reply}', flush=True)
                self._start_speech(reply, source='assistant', model=model)
            else:
                event('llm_error', message='unknown processing stage')

            self.ptt.resync(held, now)
            action = None
        if action == 'start' and self.menu.open:
            self.menu.close()
            self._publish_menu()
        if action == 'start' and self.wake_recording and self.recorder.process is not None:
            # Button pressed during a wake-word recording: it ends on release now.
            self.wake_recording = False
            action = None
        if action == 'start':
            if (self.job is not None or (os.environ.get('PTT_MEMORY_MODE') == 'hybrid'
                    and getattr(self.speech, 'synthesizing', False))):
                event('busy', reason='processing')
            else:
                self.speech.stop()
                self.speech_started_at = None
                self._release_microphone()
                self.recorder.start()
        elif action in ('release', 'limit') and self.recorder.process is not None:
            self.submit(action)
        if self.recorder.process is not None and self.recorder.process.poll() is not None:
            self.wake_recording = False
            self.submit('process_exit')
            self.ptt.resync(held, now)
        if self.wake is not None and not self.probe:
            self._wake_tick(now)
        if not self.probe:
            self._speak_alarms()
            pressed = action is not None or commands or any(pitft_pressed)
            self._update_power(now, pressed)

        if not self.probe and os.environ.get('PTT_MEMORY_MODE') == 'hybrid':
            # The standby Vosk worker (~190 MB of 415) only serves the local
            # fallback. While the server answers it is freed: next to Piper,
            # display and wake word only ~50 MB stayed available.
            if self.server_state() == 'ok':
                stop_prepared_vosk()
            elif (self.job is None and not self.speech.active
                    and not getattr(self.speech, 'synthesizing', False)
                    and self.recorder.process is None):
                prepare_vosk_worker()

    def _busy(self):
        return (self.recorder.process is not None or self.job is not None
                or self.speech.active or getattr(self.speech, 'synthesizing', False)
                or self.menu.open or bool(self.alarm_queue) or self.maint.active)

    def _wake_up(self, now):
        self.last_activity = now
        self._update_power(now, False)

    def _update_power(self, now, pressed=False):
        """awake -> rest -> sleep while nothing happens; any activity wakes."""
        if pressed or self._busy():
            self.last_activity = now
        idle = now - self.last_activity
        power = ('sleep' if idle >= self.sleep_after else
                 'rest' if idle >= self.rest_after else 'awake')
        if power == self.power:
            return
        previous, self.power = self.power, power
        event('power', state=power)
        publish_display_status(power=power)
        if power == 'sleep' and self.sleep_wlan_off and self.wlan_on:
            self.wlan_slept = self.set_wlan(False)
        elif previous == 'sleep' and self.wlan_slept:
            self.wlan_slept = False
            self.set_wlan(True)
        if previous == 'sleep' and self._idle() and not self.alarm_queue:
            # Woken by a display/SHIM button: say so. Talking (PTT), alarms
            # and status replies already speak or listen; the wake word
            # greets on its own before listening.
            self._greet()

    def close(self):
        if self.wake is not None:
            self.wake.stop()
        stop_prepared_vosk()
        if self.job is not None:
            self.job.cancel()
        self.speech.stop()
        self.recorder.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', action='store_true', help='button events only; no audio/STT')
    args = parser.parse_args()
    import gpiod
    from gpiod.line import Bias, Direction, Value
    if not hasattr(gpiod, 'request_lines'):
        parser.error('libgpiod Python API v2 required; install python3-libgpiod on Trixie')
    chip = os.environ.get('PTT_GPIO_CHIP', '/dev/gpiochip0')
    line = int(os.environ.get('PTT_GPIO_LINE', '17'))
    active_low = os.environ.get('PTT_ACTIVE_LOW', '1')
    if active_low not in ('0', '1'):
        parser.error('PTT_ACTIVE_LOW must be 0 or 1')
    limit = float(os.environ.get('PTT_MAX_SECONDS', '30'))
    debounce = float(os.environ.get('PTT_DEBOUNCE_MS', '40')) / 1000
    if not math.isfinite(limit) or not 1 <= limit <= 120:
        parser.error('PTT_MAX_SECONDS must be finite, between 1 and 120')
    if not math.isfinite(debounce) or not 0.01 <= debounce <= 0.5:
        parser.error('PTT_DEBOUNCE_MS must be finite, between 10 and 500')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    shim_enabled = os.environ.get('PTT_BUTTON_SHIM', '0')
    if shim_enabled not in ('0', '1'):
        parser.error('PTT_BUTTON_SHIM must be 0 or 1')
    runtime_dir = os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt')
    memory_mode = os.environ.get('PTT_MEMORY_MODE', 'resident')
    if memory_mode not in ('resident', 'isolated', 'hybrid'):
        parser.error('PTT_MEMORY_MODE must be resident, isolated or hybrid')
    live_vosk_factory = None
    if not args.probe:
        provider = os.environ.get('STT_PROVIDER', 'vosk').strip().lower()
        if provider != 'vosk':
            parser.error('STT_PROVIDER must be vosk; OpenRouter is LLM-only')
        event('memory_mode', mode=memory_mode)
    if not args.probe and memory_mode == 'resident':
        event('stt_loading', provider='vosk', mode='live')
        try:
            prepare_vosk()
        except (OSError, TranscriptionError) as exc:
            event('stt_error', message=str(exc), fallback='wav')
        else:
            live_vosk_factory = LiveVoskRecognizer
            event('stt_ready', provider='vosk', mode='live',
                  sample_rate=16000, channels=1)
    if not args.probe and memory_mode == 'hybrid':
        live_vosk_factory = RemoteLiveVoskRecognizer
    uplink_factory = None
    controller_ref = []  # filled once the controller exists (uplink status snapshot)
    if not args.probe:
        try:
            remote_config = load_remote_config()
        except ValueError as exc:
            # A broken remote setting must not take the local assistant down.
            event('remote_error', stage='config', code='config', message=str(exc))
            remote_config = None
        if remote_config is not None:
            def uplink_factory():
                controller = controller_ref[0] if controller_ref else None
                status = controller.status_snapshot() if controller else None
                memory_copy = (memory_core.encode_header(controller.memory.context())
                               if controller else None)
                return RemoteTurnUplink(remote_config, status=status, memory=memory_copy)
            event('remote_ready', hosts=remote_config.hosts, format=remote_config.audio_format)
    recorder = Recorder(
        runtime_dir,
        os.environ.get('PTT_AUDIO_DEVICE', 'plughw:CARD=wm8960soundcard,DEV=0'),
        limit,
        live_vosk_factory=live_vosk_factory,
        uplink_factory=uplink_factory,
    )
    settings = gpiod.LineSettings(direction=Direction.INPUT,
                                  active_low=active_low == '1',
                                  bias=Bias.PULL_UP if active_low == '1' else Bias.PULL_DOWN)

    fallback_command = os.environ.get(
        'PTT_SPEAK_COMMAND',
        '/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py')
    if args.probe:
        # Probe mode must never load a TTS model or touch the audio device.
        speech = SpeechOutput('/usr/bin/true')
    elif memory_mode == 'isolated':
        # speak.py invokes Piper in its venv, then playback. Both processes
        # are owned by SpeechOutput's group and ended before new capture/STT.
        speech = SpeechOutput(
            f'{shlex.quote(os.sys.executable)} '
            '/opt/pi-voice-assistant/src/speak.py')
        event('tts_ready', mode='isolated', profile=os.environ.get('TTS_VOICE_PROFILE', 'normal'))
    else:
        profile = os.environ.get('TTS_VOICE_PROFILE', 'normal')
        if profile.strip().lower() == 'servitor':
            model = os.environ.get(
                'TTS_SERVITOR_MODEL',
                '/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx')
        else:
            model = os.environ.get(
                'PIPER_MODEL',
                '/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx')
        event('tts_loading', mode='resident', model=model, profile=profile)
        try:
            speech = ResidentSpeechOutput(
                model,
                os.environ.get(
                    'TTS_AUDIO_DEVICE',
                    'plughw:CARD=wm8960soundcard,DEV=0'),
                runtime_dir,
                profile=profile)
        except Exception as exc:
            # TTS must not take PTT/STT down. Keep the old command path as a
            # compatibility fallback if the in-process Piper import/load fails.
            event('tts_error', message=str(exc), fallback='command')
            speech = SpeechOutput(fallback_command)
        else:
            event('tts_ready', mode='resident', model=model, profile=speech.profile)
    if uplink_factory is not None:
        speech = RemoteCapableSpeech(
            speech, os.environ.get('TTS_AUDIO_DEVICE', 'plughw:CARD=wm8960soundcard,DEV=0'))
    wake, wake_label = None, None
    wake_word = os.environ.get('PTT_WAKE_WORD', '').strip()
    if wake_word and not args.probe:
        wake_dir = Path(os.environ.get('PTT_WAKE_MODEL_DIR',
                                       '/opt/pi-voice-assistant/models/wakeword'))
        threshold = float(os.environ.get('PTT_WAKE_THRESHOLD', '0.5'))
        # Candidate words are scored in the shadow (logged, never trigger),
        # e.g. a freshly trained proximus.onnx, until they are good enough.
        shadows = {}
        for name in os.environ.get('PTT_WAKE_SHADOW', 'proximus').split(','):
            name = name.strip()
            if name and name != wake_word and (wake_dir / f'{name}.onnx').is_file():
                try:
                    meta = json.loads((wake_dir / f'{name}.json').read_text())
                    shadows[name] = float(meta.get('threshold', 0.7))
                except (OSError, ValueError, TypeError):
                    shadows[name] = 0.7
        if not (wake_dir / f'{wake_word}.onnx').is_file():
            event('wake_error', message=f'model {wake_word} missing in {wake_dir}')
        else:
            def detector_factory():
                # numpy/onnxruntime live in the Piper venv (also used by Piper).
                venv = Path(os.environ.get('PIPER_VENV', '/opt/pi-voice-assistant/.venv'))
                site = venv / 'lib' / f'python{os.sys.version_info.major}.{os.sys.version_info.minor}' / 'site-packages'
                if site.is_dir() and str(site) not in os.sys.path:
                    os.sys.path.insert(0, str(site))
                from wakeword import Detector, WakeWord
                return Detector(WakeWord(wake_dir, wake_word, gate=True, shadows=list(shadows)),
                                threshold=threshold, shadow_thresholds=shadows)
            from wake_listener import WakeListener
            wake = WakeListener(os.environ.get('PTT_AUDIO_DEVICE',
                                               'plughw:CARD=wm8960soundcard,DEV=0'),
                                detector_factory)
            wake_label = WAKE_LABELS.get(wake_word)
            event('wake_ready', word=wake_word, threshold=threshold, shadow=shadows)
    controller = VoiceController(recorder, speech, debounce, limit, args.probe,
                                 remote=uplink_factory is not None,
                                 wake=wake, wake_word=wake_label)
    controller_ref.append(controller)
    wlan_setting = os.environ.get('PTT_WLAN', '').strip().lower()
    if wlan_setting in ('on', 'off') and not args.probe:
        if not controller.set_wlan(wlan_setting == 'on'):
            controller.wlan_on = wlan_radio.wlan_blocked() is not True
    elif not args.probe:
        controller.wlan_on = wlan_radio.wlan_blocked() is not True
    if uplink_factory is not None:
        from netprobe import ServerProbe
        controller.server_probe = ServerProbe(interval=15.0).start()
    if not args.probe:
        controller.internet_probe = InternetProbe().start()
        controller.network_watch = sysmon.network_watch().start()
        controller.update_watch = sysmon.update_watch().start()
    battery_monitor = Battery()
    next_power = 0.0
    shim = None
    led = None
    shim_retry_at, shim_backoff = None, SHIM_RETRY_SECONDS

    def open_shim(now):
        """(Re)connect the Button SHIM; on failure retry later with backoff,
        so a transient I2C error (EIO) does not disable it until a restart."""
        nonlocal shim, led, shim_retry_at, shim_backoff
        try:
            from button_shim import ButtonShim, LedWriter
            shim = ButtonShim()
            if not args.probe:
                led = LedWriter(shim)
        except ImportError as exc:
            event('shim_error', message=str(exc), fallback='GPIO17')
            shim_retry_at = None
            return
        except OSError as exc:
            event('shim_error', message=str(exc), fallback='GPIO17',
                  retry_seconds=shim_backoff)
            shim_retry_at = now + shim_backoff
            shim_backoff = min(shim_backoff * 2, SHIM_RETRY_MAX_SECONDS)
            return
        event('shim_ready', bus=1, address='0x3f')
        shim_retry_at, shim_backoff = None, SHIM_RETRY_SECONDS

    if shim_enabled == '1':
        open_shim(time.monotonic())

    # PiTFT buttons (upper, lower): menu. Empty PTT_PITFT_BUTTONS disables them.
    pitft_lines = tuple(int(value) for value in
                        os.environ.get('PTT_PITFT_BUTTONS', '23,24').split(',') if value.strip())
    if len(pitft_lines) not in (0, 2) or line in pitft_lines:
        parser.error('PTT_PITFT_BUTTONS must be two GPIO lines other than PTT_GPIO_LINE, or empty')
    menu_settings = gpiod.LineSettings(direction=Direction.INPUT, active_low=True,
                                       bias=Bias.PULL_UP)
    config = {line: settings, **{pin: menu_settings for pin in pitft_lines}}
    if not args.probe:
        controller._publish_menu()

    def stop_shim():
        nonlocal shim, led
        if led is not None:
            led.close()
            led = None
        if shim is not None:
            try:
                shim.close()
            except OSError as exc:
                event('shim_error', message=str(exc))
            shim = None

    try:
        with gpiod.request_lines(chip, consumer='pi-ptt', config=config) as request:
            event('waiting_for_release', chip=chip, line=line, probe=args.probe,
                  menu_lines=list(pitft_lines))
            while not stop.is_set():
                gpio_pressed = request.get_value(line) == Value.ACTIVE
                pitft = tuple(request.get_value(pin) == Value.ACTIVE for pin in pitft_lines) \
                    or (False, False)
                now = time.monotonic()
                if not args.probe and now >= next_power:
                    controller.battery = battery_monitor.read()
                    controller.throttled = throttled_flags()
                    controller.check_alarms(now)
                    next_power = now + 10.0
                pressed = (False,) * 5
                if shim is None and shim_retry_at is not None and now >= shim_retry_at:
                    open_shim(now)
                if shim is not None:
                    try:
                        if led is not None and led.error is not None:
                            raise led.error
                        pressed = shim.read()
                        if led is not None:
                            led.request(controller.color)
                    except OSError as exc:
                        event('shim_error', message=str(exc), fallback='GPIO17',
                              retry_seconds=SHIM_RETRY_SECONDS)
                        stop_shim()
                        controller.cancel(gpio_pressed, now)
                        shim_retry_at = now + SHIM_RETRY_SECONDS
                try:
                    controller.tick(gpio_pressed, pressed, now, pitft)
                except (OSError, RuntimeError, wave.Error, EOFError) as exc:
                    event('error', message=str(exc))
                    controller.cancel(gpio_pressed or pressed[0], now)
                stop.wait(0.01)
    finally:
        try:
            controller.close()
        finally:
            stop_shim()


if __name__ == '__main__':
    os.umask(0o077)
    main()
