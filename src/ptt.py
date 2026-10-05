#!/usr/bin/env python3
"""Local hold-to-talk recorder with selectable STT. Requires libgpiod Python API v2."""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import wave

from transcribe import TranscriptionError, transcribe_with_provider
from voice_controls import SpeechOutput, TranscriptionJob, change_volume


def event(name, **fields):
    print(json.dumps(dict(version=1, event=name, **fields)), flush=True)


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
    def __init__(self, directory, device, limit):
        self.directory = Path(directory)
        self.device = device
        self.limit = limit
        self.process = None
        self.raw = self.directory / 'capture.part.pcm'
        self.partial = self.directory / 'capture.part.wav'
        self.ready = self.directory / 'capture.wav'

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ready.unlink(missing_ok=True)
        self.raw.unlink(missing_ok=True)
        self.partial.unlink(missing_ok=True)
        self.process = subprocess.Popen([
            '/usr/bin/arecord', '-q', '-D', self.device, '-t', 'raw',
            '-f', 'S16_LE', '-r', '48000', '-c', '2',
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
                return None
            # ALSA may return 1 when SIGINT interrupts a blocking PCM read.
            # Accept that only when we requested the stop; WAV is built here.
            if code != 0 and not (interrupted and code in (1, -signal.SIGINT)):
                raise RuntimeError(f'arecord exit status {code}')
            size = self.raw.stat().st_size
            if size % 4 or not 4800 * 4 <= size <= (math.ceil(self.limit) + 1) * 48000 * 4:
                raise RuntimeError('empty, unaligned or oversized PCM recording')
            with wave.open(str(self.partial), 'wb') as audio:
                audio.setnchannels(2)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(self.raw.read_bytes())
            with wave.open(str(self.partial), 'rb') as audio:
                frames = audio.getnframes()
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (2, 2, 48000):
                    raise RuntimeError('unexpected WAV format')
                if frames < 4800 or frames > (math.ceil(self.limit) + 1) * 48000:
                    raise RuntimeError('empty, too short or oversized recording')
                if len(audio.readframes(frames)) != frames * 4:
                    raise RuntimeError('truncated WAV payload')
            os.replace(self.partial, self.ready)
            event('capture_ready', path=str(self.ready), reason=reason,
                  format='wav', encoding='PCM_S16_LE', sample_rate=48000,
                  channels=2, frames=frames)
            return self.ready
        finally:
            self.raw.unlink(missing_ok=True)
            self.partial.unlink(missing_ok=True)

    def close(self):
        self.finish('shutdown', publish=False)
        self.ready.unlink(missing_ok=True)


class VoiceController:
    """One audio capture/STT slot, with A and GPIO17 combined as hold-to-talk."""
    def __init__(self, recorder, speech, debounce, limit, probe=False):
        self.recorder, self.speech, self.probe = recorder, speech, probe
        self.ptt = Button(debounce, limit)
        self.commands = {name: Button(debounce, math.inf) for name in 'BCDE'}
        self.job = None

    @property
    def color(self):
        if self.recorder.process is not None:
            return (255, 0, 0)
        if self.job is not None:
            return (0, 0, 255)
        if self.speech.active:
            return (0, 255, 255)
        return (0, 255, 0)

    def cancel(self, held, now):
        self.speech.stop()
        self.recorder.finish('cancel', publish=False)
        if self.job is not None:
            self.job.cancel()
        self.ptt.resync(held, now)
        event('cancelled')

    def submit(self, reason):
        capture = self.recorder.finish(reason)
        if capture is not None:
            event('processing', path=str(capture))
            self.job = TranscriptionJob(transcribe_with_provider, capture)

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
                if self.recorder.process is not None or action == 'start':
                    event('status_skipped', reason='recording')
                else:
                    state = 'Ich verarbeite die Aufnahme.' if self.job else 'Ich bin bereit.'
                    mode = 'Offline-Spracherkennung.' if os.environ.get('STT_PROVIDER') == 'vosk' else 'Spracherkennung konfiguriert.'
                    try:
                        self.speech.start(state + ' ' + mode)
                        event('status', text=state + ' ' + mode)
                    except OSError as exc:
                        event('speech_error', message=str(exc))
        code = self.speech.poll()
        if code is not None:
            event('speech_finished' if code == 0 else 'speech_error', returncode=code)
        if self.job is not None and self.job.done.is_set():
            job, self.job = self.job, None
            if job.cancelled:
                event('transcript_discarded')
            elif job.error is not None:
                event('stt_error', message=job.error)
            else:
                text, provider = job.result
                event('transcript', text=text, provider=provider)
                print(f'ERKANNT: {text}', flush=True)
            self.ptt.resync(held, now)
            action = None
        if action == 'start':
            if self.job is not None:
                event('busy', reason='processing')
            else:
                self.speech.stop()
                self.recorder.start()
        elif action in ('release', 'limit') and self.recorder.process is not None:
            self.submit(action)
        if self.recorder.process is not None and self.recorder.process.poll() is not None:
            self.submit('process_exit')
            self.ptt.resync(held, now)

    def close(self):
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
    recorder = Recorder(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt'),
                        os.environ.get('PTT_AUDIO_DEVICE', 'plughw:CARD=wm8960soundcard,DEV=0'), limit)
    settings = gpiod.LineSettings(direction=Direction.INPUT,
                                  active_low=active_low == '1',
                                  bias=Bias.PULL_UP if active_low == '1' else Bias.PULL_DOWN)

    speech = SpeechOutput(os.environ.get('PTT_SPEAK_COMMAND',
        '/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py'))
    controller = VoiceController(recorder, speech, debounce, limit, args.probe)
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
