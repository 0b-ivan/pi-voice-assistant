#!/usr/bin/env python3
"""Local hold-to-talk recorder with OpenRouter STT. Requires libgpiod Python API v2."""
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

from transcribe import TranscriptionError, transcribe


def event(name, **fields):
    print(json.dumps(dict(version=1, event=name, **fields)), flush=True)


def process_capture(path):
    """Transcribe one published capture; STT failure must not kill PTT."""
    event('processing', path=str(path))
    try:
        text = transcribe(path)
    except (OSError, TranscriptionError) as exc:
        event('stt_error', message=str(exc))
        return None
    event('transcript', text=text)
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
    button = Button(debounce, limit)
    recorder = Recorder(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt'),
                        os.environ.get('PTT_AUDIO_DEVICE', 'plughw:CARD=wm8960soundcard,DEV=0'), limit)
    settings = gpiod.LineSettings(direction=Direction.INPUT,
                                  active_low=active_low == '1',
                                  bias=Bias.PULL_UP if active_low == '1' else Bias.PULL_DOWN)

    def process_and_resync(capture, request):
        if capture is None:
            return
        process_capture(capture)
        button.resync(request.get_value(line) == Value.ACTIVE, time.monotonic())

    try:
        with gpiod.request_lines(chip, consumer='pi-ptt', config={line: settings}) as request:
            event('waiting_for_release', chip=chip, line=line, probe=args.probe)
            while not stop.is_set():
                action = button.update(request.get_value(line) == Value.ACTIVE, time.monotonic())
                try:
                    if action:
                        if args.probe:
                            event('button', action=action)
                        elif action == 'start':
                            recorder.start()
                        else:
                            process_and_resync(recorder.finish(action), request)
                    if recorder.process is not None and recorder.process.poll() is not None:
                        # Natural duration expiry or early device failure.
                        process_and_resync(recorder.finish('process_exit'), request)
                except (OSError, RuntimeError, wave.Error, EOFError) as exc:
                    button.failed()
                    recorder.close()
                    event('error', message=str(exc))
                stop.wait(0.01)
    finally:
        recorder.close()


if __name__ == '__main__':
    os.umask(0o077)
    main()
