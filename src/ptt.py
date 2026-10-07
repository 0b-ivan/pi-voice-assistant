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

from llm import configured_model, generate_reply
from remote_turn import RemoteCapableSpeech, RemoteTurnJob, RemoteTurnUplink, load_remote_config
from system_status import build_status_text
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

    def _pump_live_audio(self, proc, recognizer, uplink=None):
        recognizer_ok = recognizer is not None
        try:
            with self.raw.open('wb') as sink:
                while True:
                    chunk = proc.stdout.read(3200)
                    if not chunk:
                        break
                    sink.write(chunk)
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

    def start(self):
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

        recognizer = None
        if self.live_vosk_factory is not None:
            try:
                recognizer = self.live_vosk_factory()
            except (OSError, TranscriptionError) as exc:
                self._live_error = str(exc)

        uplink = None
        if self.uplink_factory is not None:
            try:
                uplink = self.uplink_factory()
            except (OSError, ValueError) as exc:
                event('remote_error', stage='connect', code='client', message=str(exc))
        self._uplink = uplink

        if recognizer is not None or uplink is not None:
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
                name='vosk-live' if recognizer is not None else 'remote-pump',
                daemon=True,
            )
            self._pump_thread.start()
            fields = dict(remote=True) if uplink is not None else {}
            event('recording', stt='vosk-live' if recognizer is not None else 'remote',
                  sample_rate=16000, channels=1, **fields)
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
    def __init__(self, recorder, speech, debounce, limit, probe=False, remote=False):
        self.recorder, self.speech, self.probe = recorder, speech, probe
        self.remote = remote
        self.remote_capture = None
        self.ptt = Button(debounce, limit)
        self.commands = {name: Button(debounce, math.inf) for name in 'BCDE'}
        self.job = None
        self.job_stage = None
        self.job_started_at = None
        self.speech_started_at = None

    @staticmethod
    def _mix_color(first, second, amount):
        amount = max(0.0, min(1.0, float(amount)))
        values = tuple(
            int(round(a + (b - a) * amount)) for a, b in zip(first, second)
        )
        # Quantize slightly so the main 10 ms loop does not flood I2C with
        # imperceptibly small RGB changes while still looking smooth.
        return tuple(max(0, min(255, int(round(value / 8)) * 8)) for value in values)

    @property
    def color(self):
        if self.recorder.process is not None:
            return (255, 0, 0)

        if self.job is not None:
            # Processing/thinking: hard red/yellow blink. This intentionally
            # looks different from the smooth turquoise/orange speech envelope.
            return (255, 208, 0) if int(time.monotonic() * 2) % 2 else (255, 0, 0)

        if self.speech.active:
            # Speech follows the actual streamed Piper cadence. Sentence pauses
            # are turquoise; voiced chunks fade toward orange.
            level = getattr(self.speech, 'voice_level', 1.0)
            try:
                level = float(level)
            except (TypeError, ValueError):
                level = 1.0
            return self._mix_color((0, 224, 208), (255, 104, 0), level)

        return (0, 255, 0)

    def cancel(self, held, now):
        self.speech.stop()
        self.speech_started_at = None
        self.recorder.finish('cancel', publish=False)
        if self.remote:
            self.recorder.drop_uplink()
        if self.job is not None:
            self.job.cancel()
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
        if uplink is not None:
            if uplink.error is None:
                self.remote_capture = capture
                self.job = RemoteTurnJob(uplink, capture.parent)
                self.job_stage = 'remote'
                self.job_started_at = time.monotonic()
                return
            uplink.cancel()
            event('remote_error', stage='upload', code='network', message=uplink.error)
            event('remote_fallback', target='stt')
        self._start_local_stt(capture)

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
        self.job = TranscriptionJob(generate_reply, text)
        self.job_stage = 'llm'
        self.job_started_at = time.monotonic()
        event('llm_start', model=configured_model())

    def _start_speech(self, text, **fields):
        try:
            event('speech_started', **fields)
            self.speech.start(text)
            self.speech_started_at = time.monotonic()
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
            event('transcript', text=text, provider='remote')
            print(f'ERKANNT: {text}', flush=True)
        elif kind == 'reply':
            text = str(item.get('text', ''))
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
            event('remote_done', host=job.result.get('host'))
            try:
                event('speech_started', source='remote')
                display_progress('tts', 'playback')
                self.speech.play(job.result['audio'])
                self.speech_started_at = time.monotonic()
            except (OSError, RuntimeError, ValueError) as exc:
                event('speech_error', message=str(exc))
            return
        event('remote_error', stage=job.error_stage, code=job.error_code, message=job.error)
        if not job.fallback_allowed:
            event('stt_error', message=job.error)
        elif job.reply:
            event('remote_fallback', target='tts')
            self._start_speech(job.reply, source='assistant', model=job.model)
        elif job.transcript:
            event('remote_fallback', target='llm')
            self._start_llm(job.transcript)
        elif job.error_stage in ('upload', 'recognize', 'stream') and capture is not None:
            event('remote_fallback', target='stt')
            event('processing', path=str(capture))
            self._start_local_stt(capture)
        else:
            event('stt_error', message=job.error)

    def tick(self, gpio_pressed, shim_pressed, now):
        held = gpio_pressed or shim_pressed[0]
        action = self.ptt.update(held, now)
        commands = [name for i, name in enumerate('BCDE', 1)
                    if self.commands[name].update(shim_pressed[i], now) == 'start']
        if self.probe:
            if action:
                event('button', button='PTT', action=action)
            for name in commands:
                event('button', button=name, action='start')
            return
        if 'B' in commands:
            self.cancel(held, now)
            action = None
            # Simultaneous B/E never starts a new status utterance.
            commands = []
        for name in commands:
            if name in 'CD':
                try:
                    change_volume(1 if name == 'D' else -1)
                    event('volume', direction='up' if name == 'D' else 'down', step=5)
                except (OSError, subprocess.SubprocessError) as exc:
                    event('mixer_error', message=str(exc))
            elif name == 'E':
                if (self.recorder.process is not None or action == 'start'
                        or (os.environ.get('PTT_MEMORY_MODE') in ('isolated', 'hybrid')
                            and self.job is not None)):
                    event('status_skipped', reason='recording_or_processing')
                else:
                    text = build_status_text(
                        processing=self.job is not None,
                        stt_provider=os.environ.get('STT_PROVIDER'),
                    )
                    try:
                        event('status', text=text)
                        event('speech_started', source='status')
                        self.speech.start(text)
                        self.speech_started_at = time.monotonic()
                    except (OSError, RuntimeError, ValueError) as exc:
                        event('speech_error', message=str(exc))
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
                event('llm_response', text=reply, model=model)
                print(f'SERVITOR: {reply}', flush=True)
                self._start_speech(reply, source='assistant', model=model)
            else:
                event('llm_error', message='unknown processing stage')

            self.ptt.resync(held, now)
            action = None
        if action == 'start':
            if (self.job is not None or (os.environ.get('PTT_MEMORY_MODE') == 'hybrid'
                    and getattr(self.speech, 'synthesizing', False))):
                event('busy', reason='processing')
            else:
                self.speech.stop()
                self.speech_started_at = None
                self.recorder.start()
        elif action in ('release', 'limit') and self.recorder.process is not None:
            self.submit(action)
        if self.recorder.process is not None and self.recorder.process.poll() is not None:
            self.submit('process_exit')
            self.ptt.resync(held, now)

        if (not self.probe and os.environ.get('PTT_MEMORY_MODE') == 'hybrid'
                and self.job is None and not self.speech.active
                and not getattr(self.speech, 'synthesizing', False)
                and self.recorder.process is None):
            prepare_vosk_worker()

    def close(self):
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
    if not args.probe:
        try:
            remote_config = load_remote_config()
        except ValueError as exc:
            # A broken remote setting must not take the local assistant down.
            event('remote_error', stage='config', code='config', message=str(exc))
            remote_config = None
        if remote_config is not None:
            uplink_factory = lambda: RemoteTurnUplink(remote_config)
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
    controller = VoiceController(recorder, speech, debounce, limit, args.probe,
                                 remote=uplink_factory is not None)
    shim = None
    if shim_enabled == '1':
        try:
            from button_shim import ButtonShim
            shim = ButtonShim()
            event('shim_ready', bus=1, address='0x3f')
        except (ImportError, OSError) as exc:
            event('shim_error', message=str(exc), fallback='GPIO17; restart to retry')

    try:
        with gpiod.request_lines(chip, consumer='pi-ptt', config={line: settings}) as request:
            event('waiting_for_release', chip=chip, line=line, probe=args.probe)
            while not stop.is_set():
                gpio_pressed = request.get_value(line) == Value.ACTIVE
                now = time.monotonic()
                pressed = (False,) * 5
                if shim is not None:
                    try:
                        pressed = shim.read()
                        if not args.probe:
                            shim.set_color(controller.color)
                    except OSError as exc:
                        event('shim_error', message=str(exc), fallback='GPIO17; restart to retry')
                        try:
                            shim.close()
                        except OSError:
                            pass
                        shim = None
                        controller.cancel(gpio_pressed, now)
                try:
                    controller.tick(gpio_pressed, pressed, now)
                except (OSError, RuntimeError, wave.Error, EOFError) as exc:
                    event('error', message=str(exc))
                    controller.cancel(gpio_pressed or pressed[0], now)
                stop.wait(0.01)
    finally:
        try:
            controller.close()
        finally:
            if shim is not None:
                try:
                    shim.close()
                except OSError as exc:
                    event('shim_error', message=str(exc))


if __name__ == '__main__':
    os.umask(0o077)
    main()
