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
from llm import (LORE_LEVELS, PERSONAS, configured_model, free_model, generate_reply,
                 lore_level, persona_name)
import agenda as agenda_feed
import alarm_audio
import audio_output
import bluetooth
import boardled
import cue as cue_sound
import device_control
import enroll
import people
import maintenance
import memory as memory_core
import sysmon
from alarms import (ALARMS, SHUTDOWN_FAILED, SHUTDOWN_NOW, WAKE_PHRASES, AlarmMonitor,
                    shutdown_text,
                    memory_phrase)
from endpoint import Endpointer
from netprobe import InternetProbe, network_up
import weather
import wlan as wlan_radio
from menu import GROUPS as MENU_GROUPS, ITEMS as MENU_ITEMS, Menu
from remote_turn import RemoteCapableSpeech, RemoteTurnJob, RemoteTurnUplink, load_remote_config
from protocol import ClientSession
import datetime
import intents
from power import Battery, throttled_flags
from ptt_config import LLM_MODES, ConfigError, PttConfig
from system_status import collect_snapshot, phrase_style, status_text
from transcribe import (
    LiveVoskRecognizer, RemoteLiveVoskRecognizer, TranscriptionError, prepare_vosk, transcribe_with_provider, prepare_vosk_worker, stop_prepared_vosk
)
from runtime_metrics import display_progress, phase
from mood import EMOTIONS, Mood, split_tag
from settings import Settings
from voice_effects import VOICE_EFFECTS, voice_effect
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
WAKE_LABELS = {'hey_jarvis_v0.1': 'hey_jarvis', 'hey_servitor': 'hey_servitor',
               'proximus': 'proximus'}
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
    'opt_bt': {'on', 'off', 'none'},
    'menu_group': {'top', *MENU_GROUPS},
    'maint': {'on', 'off'},
    'enroll': {'intro', 'wake', 'ask', 'process', 'done', 'auth'},
    'people': {'on', 'off'},
    'people_rev': set(range(10000)),
    'enroll_rec': {'on', 'off'},
    'enroll_step': set(range(0, 101)),
    'enroll_total': set(range(0, 101)),
    'maint_index': set(range(len(maintenance.ITEMS))),
    'maint_confirm': set(maintenance.ITEMS),
    'maint_pi': set(maintenance.STATES),
    'maint_server': set(maintenance.STATES),
    'opt_wake': {'on', 'off', 'none'},
    'opt_cue': {'on', 'off', 'none'},
    'opt_lore': set(LORE_LEVELS),
    'opt_persona': set(PERSONAS),
    'opt_voice': set(VOICE_EFFECTS),
    'opt_emotions': {'on', 'off'},
    'mood': set(EMOTIONS),
    'opt_wlan': {'on', 'off'},
    'opt_alarms': {'on', 'off'},
    'opt_llm': set(LLM_MODES),
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
REST_LED = 0.35                 # LED brightness while resting, like the dimmed screen
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


class EnrollIO:
    """Hardware side of enroll.Session: clips through aplay, arecord, server."""

    def __init__(self, runtime=None, config=None):
        config = config if config is not None else PttConfig.from_env()
        self.runtime = Path(runtime or config.runtime_dir)
        self.output = config.output_device
        self.input = config.capture_device
        self.process = None
        self.beep_path = self.runtime / 'enroll-beep.wav'
        enroll.beep_wav(self.beep_path)

    def _run(self, args, timeout):
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
        finally:
            self.process = None

    def abort(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()

    def _play(self, path, timeout=60):
        self._run(['/usr/bin/aplay', '-q', '-D', audio_output.current(self.output), str(path)],
                  timeout)

    def say(self, text):
        path = self.runtime / 'enroll-say.wav'
        if not alarm_audio.assemble([text], path):
            config = load_remote_config()
            if config is None:
                return
            try:
                from remote_turn import USER_AGENT
                path.write_bytes(alarm_audio._render(config.base_urls[0], config.token, text,
                                                     USER_AGENT))
            except (OSError, RuntimeError, ValueError):
                return
        self._play(path)

    def beep(self):
        self._play(self.beep_path, 5)

    def record(self, path, seconds, attempts=6):
        """Record; while the capture device is still busy (e.g. the wake-word
        listener releasing it), wait and try again instead of losing the take."""
        for attempt in range(attempts):
            started = time.monotonic()
            self.process = subprocess.Popen(
                ['/usr/bin/arecord', '-q', '-D', self.input, '-t', 'wav', '-f', 'S16_LE',
                 '-r', '16000', '-c', '1', '-d', str(seconds), str(path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                start_new_session=True)
            try:
                _, error = self.process.communicate(timeout=seconds + 5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                _, error = self.process.communicate()
            code, self.process = self.process.returncode, None
            if code == 0 and Path(path).is_file():
                return True
            if code is not None and code < 0:  # terminated by abort()
                return False
            event('enroll_record_error', attempt=attempt + 1, code=code,
                  message=(error or b'').decode(errors='replace').strip()[:200])
            time.sleep(max(0.0, 0.5 - (time.monotonic() - started)))
        return False

    def transcribe(self, pcm):
        return enroll.transcribe(pcm)

    def voiceprint(self, pcm):
        return enroll.voiceprint(pcm)

    def publish(self, stage=None, step=None, total=None, rec=None):
        publish_display_status(enroll=stage, enroll_step=step if stage else None,
                               enroll_total=total if stage else None,
                               enroll_rec=(('on' if rec else 'off') if rec is not None else None)
                               if stage else None)


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
        elif key == 'mood_level':
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                continue
        elif key == 'menu_index':
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 20:
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
    def __init__(self, directory, device, limit, live_vosk_factory=None, uplink_factory=None,
                 isolated=False):
        self.directory = Path(directory)
        self.isolated = isolated  # PttConfig.isolated_capture: 16 kHz mono for the STT worker
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

        self._capture_rate = 16000 if self.isolated else 48000
        self._capture_channels = 1 if self.isolated else 2
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


def _after_network(function, text, timeout=25.0, step=0.5):
    """Run ``function(text)`` once an IPv4 address is up (WLAN just switched on)."""
    deadline = time.monotonic() + timeout
    while network_up() is False and time.monotonic() < deadline:
        time.sleep(step)
    return function(text)


class VoiceController:
    """One capture/STT/LLM slot, with A and GPIO17 combined as hold-to-talk."""
    def __init__(self, recorder, speech, debounce, limit, probe=False, remote=False,
                 wake=None, wake_word=None, cue=None, config=None):
        self.config = config if config is not None else PttConfig.from_env()
        self.recorder, self.speech, self.probe = recorder, speech, probe
        self.cue = cue                    # cue.Cue: acknowledgement sound on submit
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
        # Menu choices survive a restart; the environment only sets the defaults.
        self.settings = Settings()
        saved = {key: value for key, value in self.settings.load().items()
                 if isinstance(value, str)}
        self.lore = lore_level(saved.get('lore') or self.config.lore)
        self.persona = persona_name(saved.get('persona') or self.config.persona)
        self.voice = voice_effect(saved.get('voice') or self.config.voice_effect)
        emotions = saved.get('emotions') or self.config.emotions
        self.mood = Mood(enabled=emotions.strip().lower() != 'off')
        self.mood_shown = None
        self._apply_voice_effect()
        self.battery = None      # power.Battery reading, refreshed by main()
        self.throttled = None
        self.remote_failed = False
        self.server_probe = None  # netprobe.ServerProbe when a server is configured
        self.alarms = AlarmMonitor()
        self.alarms_enabled = self.config.alarms
        self.alarm_queue = []    # sentences waiting until the unit is idle
        self.wlan_on = True
        self.link_grace_until = 0.0  # link alarms wait while WLAN reconnects
        self.power = 'awake'
        self.last_activity = time.monotonic()
        self.rest_after = self.config.rest_seconds
        self.sleep_after = self.config.sleep_seconds
        self.sleep_wlan_off = self.config.sleep_wlan_off
        self.wlan_slept = False      # WLAN was switched off by sleep, not by the user
        self.listen_after_greeting = False  # wake word woke us: listen after the greeting
        self.weather = weather.Forecast()  # cached only: never blocks the loop
        self.agenda = agenda_feed.Agenda()  # CALDAV_*; refreshed by a thread (main)
        self.memory = memory_core.MemoryCore()
        self.memory_present = self.memory.present()
        try:  # restart: neutral, with a faint echo of the newest remembered turns
            data = self.memory.load() if self.memory_present else None
            self.mood.baseline((data or {}).get('history'), time.time())
        except (OSError, ValueError, AttributeError):
            pass
        self.turn_transcript = None
        self.network_watch = self.update_watch = None  # sysmon watches, started by main()
        self.maint = maintenance.Mode()
        self.enroll = None                 # running enroll.Session or people.Flow
        self.people = people.Browser()
        self.board_leds = boardled.BoardLeds()
        self.bt = None                     # bluetooth.Bluetooth, started by main()
        self.bt_wanted = True              # reconnect the paired speaker automatically
        self.bt_busy = False               # scan/pair running in a thread
        self.bt_notes = []                 # sentences from the Bluetooth thread
        self.picker = None                 # dict(title, items=[(mac, name)], index, action)
        self.people_rev = 0
        self.enroll_after_speech = None    # 'enroll'/'refine': start once the reply is spoken
        # Spoken device commands (device_control): a reboot/shutdown waiting for
        # "bestätigt" or E, the one taken over by the current turn, and the
        # confirmed one that runs once its announcement has been spoken.
        self.device_pending = None
        self.device_pending_until = 0.0
        self.turn_device_pending = None
        self.device_after_speech = None
        self.device_after_until = 0.0     # never act on a stale confirmation
        self.alarms.notice_store = maintenance.NoticeStore()
        self.maint_jobs = {}          # target -> start time (time.time()) of a running action
        self.internet_probe = None  # netprobe.InternetProbe
        self.llm_mode = self.config.llm_mode
        self.shutting_down = False
        self.ptt = Button(debounce, limit)
        self.commands = {name: Button(debounce, math.inf) for name in 'BCDE'}
        self.job = None
        self.job_stage = None
        self.job_started_at = None
        self.speech_started_at = None

    @property
    def style(self):
        """Wording of fixed sentences: lore level, or Billy (system_status.phrase_style)."""
        return phrase_style(self.lore, self.persona)

    def _apply_voice_effect(self):
        if isinstance(getattr(self.speech, 'effect', None), str):
            self.speech.effect = self.voice

    def _set_personality(self, persona=None, voice=None, lore=None, emotions=None):
        """Change and remember the personality switches of the menu."""
        self.persona = persona or self.persona
        self.voice = voice or self.voice
        self.lore = lore or self.lore
        if emotions is not None:
            self.mood.enabled = emotions
            if not emotions:
                self.mood.reset()
        self._apply_voice_effect()
        self.settings.save(persona=self.persona, voice=self.voice, lore=self.lore,
                           emotions='on' if self.mood.enabled else 'off')
        self._publish_mood()

    def _publish_mood(self):
        """Current emotion for the display (Billy's face, the Servitor's glitches)."""
        emotion, level = self.mood.current(time.time())
        shown = (emotion, round(level * 10))
        if shown != self.mood_shown:
            self.mood_shown = shown
            publish_display_status(mood=emotion, mood_level=round(level * 100))

    def _mood_fields(self):
        if not self.mood.enabled:
            return {}
        emotion, level = self.mood.current(time.time())
        return dict(mood=emotion, mood_level=round(level * 100))

    def turn_snapshot(self, text=None):
        """Status for one LLM turn: the snapshot plus whether Proximus may refuse."""
        snapshot = self.status_snapshot()
        if self.mood.enabled:
            turn = self.mood.turn(text, time.time())
            snapshot.update(mood=turn['emotion'], mood_level=turn['level'],
                            mood_refuse='on' if turn['refuse'] else 'off')
        return snapshot

    def _mood_state(self, text):
        """Mood argument for the Pi's own LLM call (None: feelings off)."""
        if not self.mood.enabled:
            return None
        turn = self.mood.turn(text, time.time())
        return dict(emotion=turn['emotion'], level=turn['level'], refuse=turn['refuse'])

    @staticmethod
    def _scale(color, factor):
        return tuple(int(round(value * factor)) for value in color)

    def _color(self):
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
        return LED_READY_LOCAL if local else LED_READY

    @property
    def color(self):
        """SHIM LED; dimmed like the screen while resting (recording, errors and
        critical alarms excepted: they come before any dimming)."""
        color = self._color()
        bright = color in (LED_RECORDING, LED_ALARM)
        return self._scale(color, REST_LED) if self.power == 'rest' and not bright else color

    def cancel(self, held, now):
        if self.device_after_speech:   # B during "Einheit fährt herunter.": stays on
            event('device', op=self.device_after_speech, result='cancelled')
            self.device_after_speech = None
        self.device_pending = None
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

    def stop_by_voice(self):
        """"Stop", "Sei still", "Klappe halten" ...: no answer, drop announcements
        that were waiting, back to idle (the wake word listens again)."""
        self.speech.stop()
        self.speech_started_at = None
        self.alarm_queue = []
        self.enroll_after_speech = None
        self.listen_after_greeting = False
        self.turn_released_at = None
        event('cancelled', source='voice')

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
        # This turn may answer a pending reboot/shutdown question; either way
        # the question is used up (anything else drops it).
        self.turn_device_pending = self._device_take_pending()
        if self.cue is not None and self.cue.play():
            event('cue')
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
                               menu_group=self.menu.group or ('top' if self.menu.open else None),
                               opt_server=server, opt_wake=wake, wake_word=self.wake_word,
                               opt_lore=self.lore, opt_persona=self.persona,
                               opt_voice=self.voice,
                               opt_emotions='on' if self.mood.enabled else 'off',
                               opt_wlan='on' if self.wlan_on else 'off',
                               opt_alarms='on' if self.alarms_enabled else 'off',
                               opt_llm=self.llm_mode,
                               opt_led='on' if self.led_enabled else 'off',
                               opt_cue='none' if self.cue is None else
                               'on' if self.cue.enabled else 'off',
                               screen='on' if self.screen_on else 'off',
                               opt_bt='none' if self.bt is None else
                               'on' if self.bt.connected else 'off')

    def _menu_confirm(self, now):
        item = self.menu.confirm(now)
        if item in ('enroll', 'refine'):
            self._publish_menu()
            self._start_enroll(item)
            return
        if item == 'people':
            self._publish_menu()
            self._open_people()
            return
        if item in ('bt_speaker', 'bt_scan', 'bt_forget', 'bt_connect'):
            self._bluetooth_item(item)
            self._publish_menu()
            return
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
            self._set_personality(
                lore=LORE_LEVELS[(LORE_LEVELS.index(self.lore) + 1) % len(LORE_LEVELS)])
            event('menu', item='lore', value=self.lore)
        elif item == 'persona':
            self._set_personality(persona='mensch' if self.persona == 'servitor' else 'servitor')
            event('menu', item='persona', value=self.persona)
        elif item == 'voice_fx':
            self._set_personality(voice='natural' if self.voice == 'servitor' else 'servitor')
            event('menu', item='voice_fx', value=self.voice)
        elif item == 'emotions':
            self._set_personality(emotions=not self.mood.enabled)
            event('menu', item='emotions', value='on' if self.mood.enabled else 'off')
        elif item == 'human':
            # Shortcut: Billy with his own voice, or back to the machine. Lore stays.
            human = self.persona == 'mensch' and self.voice == 'natural'
            self._set_personality(persona='servitor' if human else 'mensch',
                                  voice='servitor' if human else 'natural')
            event('menu', item='human', value='off' if human else 'on')
        elif item == 'llm':
            self.llm_mode = LLM_MODES[(LLM_MODES.index(self.llm_mode) + 1) % len(LLM_MODES)]
            event('menu', item='llm', value=self.llm_mode)
        elif item == 'wlan':
            self.set_wlan(not self.wlan_on)
        elif item == 'alarms':
            self.alarms_enabled = not self.alarms_enabled
            if not self.alarms_enabled:
                self.alarm_queue = []
            event('menu', item='alarms', value='on' if self.alarms_enabled else 'off')
        elif item == 'cue' and self.cue is not None:
            self.cue.enabled = not self.cue.enabled
            event('menu', item='cue', value='on' if self.cue.enabled else 'off')
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
                                llm_mode=self.llm_mode, persona=self.persona,
                                voice=self.voice,
                                extra=dict(sysmon.snapshot_fields(
                                    getattr(self.network_watch, 'result', None)
                                    if self.wlan_on else None,
                                    getattr(self.update_watch, 'result', None)),
                                    memory='on' if self.memory_present else 'off',
                                    maintenance='on' if self.maint.active else 'off',
                                    devctl='on', pending=self._device_pending_now(),
                                    **self._mood_fields()))

    def check_alarms(self, now, network=None):
        """Called every ~10 s by main(); queues alarm sentences to speak."""
        # WLAN off, or just switched back on and still connecting: links unknown.
        links = self.wlan_on and now >= self.link_grace_until
        if network is None and links:
            network = network_up()
        internet = getattr(self.internet_probe, 'state', None) if links else None
        snapshot = self.status_snapshot()
        self.mood.sense(snapshot, time.time())
        self._publish_mood()
        texts = self.alarms.update(snapshot, now,
                                   network=network if links else None,
                                   server=self.server_state() if links else 'off',
                                   lore=self.style, internet=internet)
        texts += self._check_memory()
        notes, self.bt_notes = self.bt_notes, []
        texts += notes
        maintenance_texts = self._check_maintenance()  # spoken even with alarms muted
        if self.power != 'sleep':  # maintenance can wait until someone is around
            # Wall clock: the last announcement survives service restarts.
            notice = self.alarms.updates_notice(self.status_snapshot(), time.time(), self.style)
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
                               upd_srv=None, upd_srv_sec=None)
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

    # --- Spoken device commands (device_control) ---------------------------------

    def _device_pending_now(self):
        if self.device_pending and time.monotonic() <= self.device_pending_until:
            return self.device_pending
        return None

    def _device_take_pending(self):
        """The pending reboot/shutdown question, used up; None if none or expired."""
        op, self.device_pending = self.device_pending, None
        if op is not None and time.monotonic() > self.device_pending_until:
            event('device', op=op, result='expired')
            return None
        return op

    def _device_turn(self, text, pending, commands=True):
        """Local path: answer to a pending question or (``commands``) a device
        command; the sentence to speak, or None when the text is something else."""
        if pending is not None:
            answer = device_control.answer(text)
            if answer == 'confirm':
                return self._device_confirmed(pending, speak=False)
            if answer == 'cancel':
                event('device', op=pending, result='cancelled')
                return device_control.cancelled_text(self.style)
            event('device', op=pending, result='dropped')
        if not commands:
            return None
        op = device_control.command(text)
        if op == 'reboot' and self.maint.active:
            return None   # the maintenance mode handles "starte neu" (confirmed with E)
        if op is None:
            return None
        return self._device_command(op)

    def _device_command(self, op):
        """A recognized command: ask (reboot/shutdown) or switch WLAN now."""
        wlan = 'on' if self.wlan_on else 'off'
        text = device_control.reply(op, wlan, self.style)
        if op in device_control.CONFIRM_OPS:
            self.device_pending = op
            self.device_pending_until = time.monotonic() + device_control.CONFIRM_SECONDS
            event('device', op=op, result='ask')
        elif op == 'sleep':
            self._device_arm(op)            # once "Ruhemodus." has been said
        elif (op == 'wlan_on') != self.wlan_on:
            ok = self.set_wlan(op == 'wlan_on')
            event('device', op=op, result='done' if ok else 'failed')
            if not ok:
                return device_control.failed_text(self.style)
        return text

    def _device_confirmed(self, op, speak):
        """Confirmed reboot/shutdown: announce it, then act once that is spoken."""
        event('device', op=op, result='confirmed')
        text = device_control.start_text(op, self.style)
        self._device_arm(op)
        if speak:
            self._say(text)
            if not self.speech.active:      # nothing to wait for
                op, self.device_after_speech = self.device_after_speech, None
                self._device_run(op)
        return text

    def _device_arm(self, op):
        """Run ``op`` once the announcement has been spoken: within a minute,
        and not if B cut it off (cancel)."""
        self.device_after_speech = op
        self.device_after_until = time.monotonic() + 60.0

    def _device_event(self, item):
        """Server path: the server recognized a command or the confirmation."""
        op = item.get('op')
        if op not in device_control.OPS:
            return
        if item.get('confirm'):
            pending, self.turn_device_pending = self.turn_device_pending, None
            if op == pending:   # only what this Pi asked, within the time limit
                event('device', op=op, result='confirmed')
                self._device_arm(op)            # the server's reply announces it
            return
        self.turn_device_pending = None
        if op == 'sleep':
            self._device_arm(op)            # after the server's reply
        elif op in device_control.CONFIRM_OPS:
            if not (op == 'reboot' and self.maint.active):
                self.device_pending = op
                self.device_pending_until = time.monotonic() + device_control.CONFIRM_SECONDS
                event('device', op=op, result='ask')
        elif (op == 'wlan_on') != self.wlan_on:
            if not self.set_wlan(op == 'wlan_on'):
                event('device', op=op, result='failed')
                self.alarm_queue.append(device_control.failed_text(self.style))

    def _device_run(self, op):
        if op == 'sleep':
            # The usual idle path (_update_power): screen and LED off, WLAN as
            # configured; a button or the wake word wakes it as always.
            event('device', op=op, result='done')
            self.last_activity = time.monotonic() - self.sleep_after - 1.0
        elif op == 'shutdown':
            self.power_off('command')
        elif op == 'reboot':
            event('reboot', reason='command')
            try:
                if maintenance.installed():
                    maintenance.request('reboot')   # root worker (deploy/maintenance)
                else:
                    subprocess.run(['/usr/bin/systemctl', 'reboot'], check=True, timeout=15,
                                   stdin=subprocess.DEVNULL, capture_output=True)
            except (OSError, subprocess.SubprocessError) as exc:
                event('reboot_error', message=str(exc))
                self.alarm_queue.append(device_control.failed_text(self.style))

    def _wlan_on_demand(self):
        """WLAN is off but a request needs the network: switch it on (not while
        asleep with WLAN off by design, and not without the rfkill permission)."""
        if self.wlan_on or not self.set_wlan(True):
            return False
        event('device', op='wlan_on', result='auto')
        return True

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
        elif op in maintenance.DENIED:
            text = maintenance.DENIED_TEXT
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
            self._publish_maintenance()  # or the display keeps the WARTUNG screen
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
                out.append(maintenance.result_text(target, data, self.style))
                if self.update_watch is not None:
                    self.update_watch.refresh()
        self._maint_status = cache
        self._publish_maintenance()
        return out

    # --- Getting to know the operator ------------------------------------

    # --- Bluetooth speakers ------------------------------------------------

    def start_bluetooth(self, interval=30.0):
        """Watch for the paired speaker in a daemon thread (main() calls this)."""
        self.bt = bluetooth.Bluetooth()

        def watch():
            while True:
                if not self.bt_busy:
                    self._bt_refresh()
                time.sleep(interval)
        threading.Thread(target=watch, name='bluetooth', daemon=True).start()

    def _bt_refresh(self):
        before = audio_output.bluetooth()   # what playback currently uses
        live = self.bt.refresh(reconnect=self.bt_wanted)
        mac = live[0] if live else None
        audio_output.set_bluetooth(mac)
        if mac != before:
            event('bluetooth', connected=mac is not None)
            self.bt_notes.append(bluetooth.CONNECTED if mac else bluetooth.DISCONNECTED)

    def _bt_job(self, work):
        def run():
            self.bt_busy = True
            try:
                work()
            finally:
                self.bt_busy = False
        threading.Thread(target=run, name='bluetooth-job', daemon=True).start()

    def _bluetooth_item(self, item):
        if self.bt is None or not self.bt.available():
            self._say(bluetooth.UNAVAILABLE)
            return
        if self.bt_busy and item != 'bt_connect':
            self._say(bluetooth.BUSY)
            return
        if item == 'bt_speaker':
            if self.bt.connected:
                self.bt_wanted = False
                self._bt_job(lambda: (self.bt.disconnect(), self._bt_refresh()))
            else:
                self.bt_wanted = True
                self._bt_job(self._bt_refresh)
        elif item == 'bt_scan':
            self._say(bluetooth.SCANNING)
            # The list shows up at once and grows while the scan runs.
            self.picker = dict(title='SUCHE …', items=[], index=0, action='pair')
            self._publish_picker()

            def update(found):
                picker = self.picker
                if picker is None or picker['action'] != 'pair':
                    return                        # closed or replaced meanwhile
                picker['items'] = [(mac, name + (' ✓' if paired else ''))
                                   for mac, name, paired in found]
                picker['index'] = min(picker['index'], len(picker['items']))
                self._publish_picker()

            def scan():
                found = self.bt.scan(on_update=update)
                if self.picker is not None and self.picker['action'] == 'pair':
                    if found:
                        self.picker['title'] = 'LAUTSPRECHER'
                        self._publish_picker()
                    else:
                        self.picker = None
                        self._publish_picker()
                        self.bt_notes.append(bluetooth.NONE_FOUND)
            self._bt_job(scan)
        elif item == 'bt_connect':
            paired = [(mac, name) for mac, name, _ in self.bt.speakers()]
            if not paired:
                self._say(bluetooth.NONE_PAIRED)
                return
            self.picker = dict(title='VERBINDEN', items=paired, index=0, action='pair')
            self._publish_picker()
        elif item == 'bt_forget':
            paired = [(mac, name) for mac, name, _ in self.bt.speakers()]
            if not paired:
                self._say(bluetooth.NONE_PAIRED)
                return
            self.picker = dict(title='ENTFERNEN', items=paired, index=0, action='forget')
            self._publish_picker()

    def _publish_picker(self):
        path = self.config.runtime_dir / 'display-people.json'
        if self.picker:
            items = [name[:24] for _, name in self.picker['items']] + ['Zurück']
            try:
                people.write_view(path, dict(page='list', index=self.picker['index'], items=items,
                                             person='', title=self.picker['title']))
            except OSError:
                pass
        self.people_rev = (self.people_rev + 1) % 10000
        publish_display_status(people='on' if self.picker else 'off', people_rev=self.people_rev)

    def _picker_buttons(self, confirm):
        picker, self.picker = self.picker, None
        index = picker['index']
        if confirm and index < len(picker['items']):
            mac, name = picker['items'][index]
            if picker['action'] == 'pair':
                self._say(bluetooth.PAIRING if not name.endswith('✓') and picker['title'] != 'VERBINDEN'
                          else bluetooth.CONNECTING)
                name = name.removesuffix(' ✓')

                def pair():
                    ok = self.bt.pair(mac, name)
                    self.bt_wanted = True
                    if ok:
                        self._bt_refresh()
                    else:
                        self.bt_notes.append(bluetooth.PAIR_FAILED)
                self._bt_job(pair)
            else:
                self.bt.forget(mac)
                audio_output.set_bluetooth(self.bt.connected[0] if self.bt.connected else None)
                self._say(bluetooth.FORGOTTEN)
        self._publish_picker()

    def _open_people(self):
        if not self.memory.present():
            self._say(enroll.NEED_STICK)
            return
        self.people.open(name for name, _ in self.memory.people())
        self._publish_people()

    def _publish_people(self):
        path = self.config.runtime_dir / 'display-people.json'
        if self.people.active:
            try:
                people.write_view(path, self.people.view())
            except OSError:
                pass
        self.people_rev = (self.people_rev + 1) % 10000
        publish_display_status(people='on' if self.people.active else 'off',
                               people_rev=self.people_rev)

    def _people_buttons(self, confirm, cancel, now):
        browser = self.people
        if browser.delete_until is not None:
            pending, browser.delete_until = browser.delete_until, None
            if confirm and now <= pending and self.memory.delete_profile(browser.person):
                event('people', action='deleted')
                self._say(people.DELETED)
                browser.open(name for name, _ in self.memory.people())
            elif cancel or confirm:
                self._say(maintenance.CANCEL_TEXT)
            self._publish_people()
            return
        if cancel:
            browser.back()
        elif confirm:
            choice = browser.select()
            if choice and choice[0] == 'close':
                browser.close()
            elif choice and choice[0] == 'action':
                if not self.remote or self.server_state() != 'ok':
                    self._say(people.NO_SERVER)
                else:
                    self.speech.stop()
                    self._release_microphone()
                    self.enroll = people.Flow(self.memory, EnrollIO(config=self.config), browser.person, choice[1])
                    event('people', action=choice[1])
                    self.enroll.start()
        self._publish_people()

    def _people_flow_done(self, flow, result, now):
        browser = self.people
        browser.weak = bool(result.get('weak'))
        if result.get('details'):
            browser.details, browser.page = result['details'], 'details'
        if result.get('delete_pending'):
            browser.delete_until = now + people.DELETE_CONFIRM_SECONDS
        self._publish_people()
        if result.get('refine'):
            self._start_enroll('refine', target=flow.name)

    def _start_enroll(self, mode='enroll', target=None):
        if self.enroll is not None:
            return
        if not self.memory.present():
            self._say(enroll.NEED_STICK)
            return
        if not self.remote or self.server_state() != 'ok':
            self._say(enroll.NEED_SERVER)
            return
        self.speech.stop()
        self._release_microphone()
        if self.menu.open:
            self.menu.close()
            self._publish_menu()
        self.enroll = enroll.Session(self.memory, EnrollIO(config=self.config), mode=mode, target=target)
        event('enroll', state='start', mode=mode)
        self.enroll.start()

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
        return [memory_phrase(present, (counts or {}).get('facts'), self.style)]

    def _memory_command(self, text):
        """Local fallback: memory commands answered and applied on the Pi."""
        command = memory_core.command(intents.normalize(text))
        if command is None:
            return None
        op, argument = command
        context = self.memory.context()
        reply = memory_core.reply(op, argument, context, self.style)
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
            self._say_alarm([shutdown_text(SHUTDOWN_NOW, self.style)])
            deadline = time.monotonic() + 8
            while self.speech.active and time.monotonic() < deadline:
                time.sleep(0.1)
        except (OSError, RuntimeError, ValueError):
            pass
        self.power_off('battery')

    def power_off(self, reason):
        """systemctl poweroff (polkit rule deploy/50-pi-voice-poweroff.rules)."""
        self.shutting_down = True
        if reason != 'battery':
            event('shutdown', reason=reason)
        try:
            subprocess.run(['/usr/bin/systemctl', 'poweroff'], check=True, timeout=15,
                           stdin=subprocess.DEVNULL, capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            self.shutting_down = False
            event('shutdown_error', message=str(exc))
            self.alarm_queue.append(shutdown_text(SHUTDOWN_FAILED, self.style))

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
        path = self.config.runtime_dir / 'alarm.wav' if play is not None else None
        voice = self.voice if isinstance(getattr(self, 'voice', None), str) else 'servitor'
        if path is not None and alarm_audio.assemble(texts, path, voice=voice):
            event('speech_started', source=source, clips=True)
            play(path)
            return
        event('speech_started', source=source, clips=False)
        self._settle_cue()
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
        # A running enrollment/authentication owns the microphone: the wake
        # listener must not grab it back in the same tick.
        session = getattr(self, 'enroll', None)
        return (self.recorder.process is None and self.job is None
                and not self.speech.active and not getattr(self.speech, 'synthesizing', False)
                and (session is None or not session.running))

    def _wake_tick(self, now):
        if self.wake.error:
            event('wake_error', message=self.wake.error)
            self.wake.error = None
            self.wake_resume_at = now + 30.0  # retry later, buttons keep working
        if self.wake.take_detection() and self._idle():
            detector = getattr(self.wake, 'detector', None)
            event('wake', word=getattr(detector, 'last_word', None) or self.wake_word)
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
            self._say_alarm([WAKE_PHRASES.get(self.style, WAKE_PHRASES['light'])], source='wake')
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _speak_status(self, action):
        if (self.recorder.process is not None or action == 'start'
                or (self.config.isolated_capture and self.job is not None)):
            event('status_skipped', reason='recording_or_processing')
            return
        text = status_text(self.status_snapshot(), processing=self.job is not None)
        try:
            event('status', text=text)
            event('speech_started', source='status')
            self._settle_cue()
            self.speech.start(text)
            self.speech_started_at = time.monotonic()
        except (OSError, RuntimeError, ValueError) as exc:
            event('speech_error', message=str(exc))

    def _pitft_input(self, pitft_pressed, now):
        up = self.pitft[0].update(pitft_pressed[0], now) == 'start'
        down = self.pitft[1].update(pitft_pressed[1], now) == 'start'
        if (up or down) and self.picker and self.power == 'awake':
            items = self.picker['items']
            self.picker['index'] = (self.picker['index'] + (-1 if up else 1)) % (len(items) + 1)
            self._publish_picker()
            return
        if (up or down) and self.people.active and self.power == 'awake':
            self.people.move(-1 if up else 1)
            self._publish_people()
            return
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
        self.mood.hear(text, time.time())
        pending, self.turn_device_pending = self.turn_device_pending, None
        # A pending question first: "abbrechen" is also a stop word.
        reply = self._device_turn(intents.normalize(text), pending, commands=False)
        if reply is None and intents.is_stop(text):
            self.stop_by_voice()
            return
        if reply is None:
            reply = self._device_turn(intents.normalize(text), None)
        if reply is not None:
            event('llm_response', text=reply, model='local/device')
            self._start_speech(reply, source='assistant', model='local/device')
            return
        if enroll.command(intents.normalize(text)):
            self.enroll_after_speech = enroll.command(intents.normalize(text))
            reply = enroll.ANNOUNCE
            event('llm_response', text=reply, model='local/enroll')
            self._start_speech(reply, source='assistant', model='local/enroll')
            return
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
        intent = intents.match(text, self.persona)
        if intent is None and not self.wlan_on and self._wlan_on_demand():
            if self.llm_mode == 'local':
                # The local model runs on CT 107: only the next request can use it.
                reply = device_control.auto_wlan_text(self.style, retry=True)
                event('llm_response', text=reply, model='local/device')
                self._start_speech(reply, source='assistant', model='local/device')
                return
            self._say(device_control.auto_wlan_text(self.style))
            wait_network = True
        else:
            wait_network = False
        if intent is None and self.llm_mode == 'local':
            # The Pi's own LLM path is OpenRouter; "LOKAL" forbids it.
            reply = ("Ohne Server kann ich gerade nicht nachdenken, Boss."
                     if self.persona == 'mensch'
                     else "Daten unzureichend. Lokaler Sprachkern nicht erreichbar.")
            event('llm_response', text=reply, model='local/none')
            self._start_speech(reply, source='assistant', model='local/none')
            return
        if intent is not None:
            # Time, date, status ...: answered on the Pi, also without network.
            snapshot = self.status_snapshot()
            if intent in ('weather', 'briefing'):
                snapshot['weather'] = self.weather.cached()
            if intent in ('calendar', 'briefing'):
                snapshot['agenda'] = self.agenda.today()
            reply = intents.answer(intent, datetime.datetime.now(), snapshot)
            self.turn_llm = 'intent'
            event('llm_response', text=reply, model='local/intent')
            print(f'SERVITOR: {reply}', flush=True)
            self._start_speech(reply, source='assistant', model='local/intent')
            return
        context = self.memory.context()
        model = free_model() if self.llm_mode == 'free' else None
        reply_function = functools.partial(generate_reply, lore=self.lore,
                                           memory=context, model=model,
                                           persona=self.persona,
                                           mood=self._mood_state(text))
        if wait_network:
            reply_function = functools.partial(_after_network, reply_function)
        self.job = TranscriptionJob(reply_function, text)
        self.job_stage = 'llm'
        self.job_started_at = time.monotonic()
        event('llm_start', model=configured_model())

    def _settle_cue(self):
        """The playback device is not shared: let the cue end before speech."""
        if self.cue is not None:
            self.cue.settle()

    def _start_speech(self, text, **fields):
        try:
            event('speech_started', **fields)
            self._settle_cue()
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
            self.mood.hear(text, time.time())
            event('transcript', text=text, provider='remote')
            print(f'ERKANNT: {text}', flush=True)
        elif kind == 'enroll':
            # after the server's announcement
            self.enroll_after_speech = 'refine' if item.get('mode') == 'refine' else 'enroll'
        elif kind == 'device':
            self._device_event(item)
        elif kind == 'maintenance':
            if item.get('op') in maintenance.ITEMS + ('enter',):
                self._maintenance_op(item['op'], speak=False)  # the server's reply speaks
        elif kind == 'mood':
            self.mood.react(item.get('emotion'), time.time())
            self._publish_mood()
        elif kind == 'memory':
            if self.memory.apply(item):
                event('memory', op=item.get('op'), learned=bool(item.get('learned')))
        elif kind == 'reply':
            text = str(item.get('text', ''))
            if self.turn_transcript and not intents.is_stop(self.turn_transcript):
                self.memory.remember_turn(self.turn_transcript, text,
                                          mood=self.mood.label(time.time()))
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
        if job.transcript and intents.is_stop(job.transcript):
            # Also with an older server that still sent a spoken reply.
            if job.error is None:
                self.remote_failed = False
            self.stop_by_voice()
            return
        if job.error is None:
            self.remote_failed = False
            event('remote_done', host=job.result.get('host'))
            try:
                event('speech_started', source='remote')
                display_progress('tts', 'playback')
                self._settle_cue()
                self.speech.play(job.result['audio'], text=job.reply)
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
        if self.enroll is not None and not self.probe:
            if self.enroll.running:
                # The session owns microphone and speaker; only B (cancel) counts.
                if 'B' in commands:
                    self.enroll.cancel()
                self.last_activity = now
                return
            finished, self.enroll = self.enroll, None
            result = finished.result or {}
            event('enroll', **{k: v for k, v in result.items() if k != 'details'})
            if isinstance(finished, people.Flow):
                self._people_flow_done(finished, result, now)
            self.ptt.resync(held, now)
            self.wake_resume_at = now + WAKE_ECHO_PAUSE
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
        if self.picker and not self.menu.open and ('B' in commands or 'E' in commands):
            self._picker_buttons('E' in commands)
            commands = [name for name in commands if name not in 'BE']
        if self.people.active and not self.menu.open and ('B' in commands or 'E' in commands):
            self._people_buttons('E' in commands, 'B' in commands, now)
            commands = [name for name in commands if name not in 'BE']
        if (self.device_pending and not self.menu.open
                and ('B' in commands or 'E' in commands)):
            op = self._device_take_pending()
            if op is not None and 'E' in commands and 'B' not in commands:
                self._device_confirmed(op, speak=True)
            elif op is not None:
                event('device', op=op, result='cancelled')
                self._say(device_control.cancelled_text(self.style))
            commands = [name for name in commands if name not in 'BE']
        if self.maint.active and not self.menu.open and ('B' in commands or 'E' in commands):
            self._maintenance_buttons('E' in commands, 'B' in commands)
            commands = [name for name in commands if name not in 'BE']
        if 'B' in commands and self.menu.open:
            # B goes one level up and closes at the top; a further press cancels as usual.
            self.menu.back(now)
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
            if self.enroll_after_speech:
                mode, self.enroll_after_speech = self.enroll_after_speech, None
                self._start_enroll(mode)
            if self.device_after_speech:
                op, self.device_after_speech = self.device_after_speech, None
                if time.monotonic() <= self.device_after_until:
                    self._device_run(op)
                else:
                    event('device', op=op, result='expired')
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
                reply, feeling = split_tag(reply)
                self.mood.react(feeling, time.time())
                self._publish_mood()
                reply = self._learn(reply)
                self.memory.remember_turn(self.turn_transcript or '', reply,
                                          mood=self.mood.label(time.time()))
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
            if (self.job is not None or (self.config.hybrid
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

        if not self.probe and self.config.hybrid:
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
                or self.menu.open or bool(self.alarm_queue) or self.maint.active
                or self.people.active)

    def _wake_up(self, now):
        self.last_activity = now
        self._update_power(now, False)

    def _update_power(self, now, pressed=False):
        """awake -> rest -> sleep while nothing happens; any activity wakes."""
        quiet = now - self.last_activity
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
        if power == 'sleep':
            error = self.board_leds.off()
            if error and not getattr(self, '_board_led_error', None):
                self._board_led_error = error
                event('board_led_error', message=error)
        elif previous == 'sleep':
            self.board_leds.restore()
            self.mood.woke_up(quiet, time.time())
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
    try:
        config = PttConfig.from_env()
    except ConfigError as exc:
        parser.error(str(exc))
    chip, line = config.gpio_chip, config.gpio_line
    limit, debounce = config.max_seconds, config.debounce
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runtime_dir = config.runtime_dir
    memory_mode = config.memory_mode
    live_vosk_factory = None
    if not args.probe:
        if config.stt_provider != 'vosk':
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
            session = ClientSession()

            def uplink_factory():
                controller = controller_ref[0] if controller_ref else None
                status = controller.turn_snapshot() if controller else None
                memory_copy = (memory_core.encode_header(
                    session.memory_payload(controller.memory.context())) if controller else None)
                appointments = (agenda_feed.encode_header(controller.agenda.today())
                                if controller else None)
                return RemoteTurnUplink(remote_config, status=status, memory=memory_copy,
                                        agenda=appointments, session=session)
            event('remote_ready', hosts=remote_config.hosts, format=remote_config.audio_format)
    recorder = Recorder(
        runtime_dir,
        config.capture_device,
        limit,
        live_vosk_factory=live_vosk_factory,
        uplink_factory=uplink_factory,
        isolated=config.isolated_capture,
    )
    settings = gpiod.LineSettings(direction=Direction.INPUT,
                                  active_low=config.active_low,
                                  bias=Bias.PULL_UP if config.active_low else Bias.PULL_DOWN)

    fallback_command = config.speak_command
    if args.probe:
        # Probe mode must never load a TTS model or touch the audio device.
        speech = SpeechOutput('/usr/bin/true')
    elif memory_mode == 'isolated':
        # speak.py invokes Piper in its venv, then playback. Both processes
        # are owned by SpeechOutput's group and ended before new capture/STT.
        speech = SpeechOutput(
            f'{shlex.quote(os.sys.executable)} '
            '/opt/pi-voice-assistant/src/speak.py')
        event('tts_ready', mode='isolated', profile=config.voice_profile)
    else:
        profile = config.voice_profile
        if profile.strip().lower() == 'servitor':
            model = config.servitor_model
        else:
            model = config.piper_model
        event('tts_loading', mode='resident', model=model, profile=profile)
        try:
            speech = ResidentSpeechOutput(
                model,
                config.output_device,
                str(runtime_dir),
                profile=profile)
        except Exception as exc:
            # TTS must not take PTT/STT down. Keep the old command path as a
            # compatibility fallback if the in-process Piper import/load fails.
            event('tts_error', message=str(exc), fallback='command')
            speech = SpeechOutput(fallback_command)
        else:
            event('tts_ready', mode='resident', model=model, profile=speech.profile)
    if uplink_factory is not None:
        speech = RemoteCapableSpeech(speech, config.output_device)
    wake, wake_label = None, None
    wake_word = config.wake_word
    if wake_word and not args.probe:
        wake_dir = config.wake_model_dir
        threshold = config.wake_threshold
        # Candidate words are scored in the shadow (logged, never trigger),
        # e.g. a freshly trained proximus.onnx, until they are good enough.
        # A model's .json can make it a second, real wake word:
        # {"threshold": 0.9, "patience": 1, "active": true}.
        shadows, active_words = {}, {}
        for name in config.wake_shadow:
            if name != wake_word and (wake_dir / f'{name}.onnx').is_file():
                try:
                    meta = json.loads((wake_dir / f'{name}.json').read_text())
                    shadows[name] = float(meta.get('threshold', 0.7))
                    if meta.get('active') is True:
                        active_words[name] = max(1, int(meta.get('patience', 2)))
                except (OSError, ValueError, TypeError):
                    shadows[name] = 0.7
        if not (wake_dir / f'{wake_word}.onnx').is_file():
            event('wake_error', message=f'model {wake_word} missing in {wake_dir}')
        else:
            def detector_factory():
                # numpy/onnxruntime live in the Piper venv (also used by Piper).
                site = config.piper_venv / 'lib' / f'python{os.sys.version_info.major}.{os.sys.version_info.minor}' / 'site-packages'
                if site.is_dir() and str(site) not in os.sys.path:
                    os.sys.path.insert(0, str(site))
                from wakeword import Detector, WakeWord
                return Detector(WakeWord(wake_dir, wake_word, gate=True, shadows=list(shadows)),
                                threshold=threshold, shadow_thresholds=shadows,
                                active=active_words)
            from wake_listener import WakeListener
            wake = WakeListener(config.capture_device, detector_factory)
            # The display names the trained word when it is active.
            wake_label = next((WAKE_LABELS[w] for w in active_words if w in WAKE_LABELS),
                              WAKE_LABELS.get(wake_word))
            event('wake_ready', word=wake_word, threshold=threshold, shadow=shadows,
                  active=active_words)
    acknowledge = None
    if not args.probe:
        acknowledge = cue_sound.Cue(
            runtime_dir / 'cue.wav', config.output_device, enabled=config.cue)
    controller = VoiceController(recorder, speech, debounce, limit, args.probe,
                                 remote=uplink_factory is not None,
                                 wake=wake, wake_word=wake_label, cue=acknowledge,
                                 config=config)
    controller_ref.append(controller)
    if not args.probe and controller.agenda.start().configured:
        event('agenda_ready', calendars=len(controller.agenda.config.urls))
    wlan_setting = config.wlan
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
        if config.bluetooth:
            controller.start_bluetooth()
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

    if config.button_shim:
        open_shim(time.monotonic())

    # PiTFT buttons (upper, lower): menu. Empty PTT_PITFT_BUTTONS disables them.
    pitft_lines = config.pitft_buttons
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
