"""Acknowledgement sound when a request is submitted: servo whirr + binary chirp.

Played when the button is released (or the wake-word recording ends on a
pause), so the operator hears at once that the unit took the request, long
before the answer arrives. Generated once with the standard library (no
sample files in the repository) and played through aplay in the background.
The playback device is not shared (plughw, no dmix): the answer waits for
the cue with settle() before it starts.
"""
import math
import subprocess
import wave
from array import array

import audio_output

RATE = 22050
# 'P' for Proximus, MSB first: high tone for 1, low tone for 0.
BITS = (1, 0, 1, 0, 0, 0, 0)
WHIRR_SECONDS = 0.11
BIT_SECONDS = 0.026
GAP_SECONDS = 0.008
PEAK = 7000


def _fade(i, length, edge):
    """Short linear fades against clicks at the start and end of a tone."""
    return min(1.0, i / edge, (length - i) / edge) if edge else 1.0


def _whirr(rate):
    """Servo motor spinning up: rising buzzy tone with a gear ripple."""
    length = int(WHIRR_SECONDS * rate)
    phase, out = 0.0, []
    for i in range(length):
        t = i / length
        freq = 260 + 520 * t * t
        phase += 2 * math.pi * freq / rate
        # Odd harmonics give the square-ish motor buzz.
        tone = math.sin(phase) + math.sin(3 * phase) / 3 + math.sin(5 * phase) / 5
        ripple = 0.65 + 0.35 * math.sin(2 * math.pi * 55 * i / rate)
        out.append(0.6 * tone * ripple * _fade(i, length, rate // 400))
    return out


def _chirp(rate, bits=BITS):
    out = []
    length, gap = int(BIT_SECONDS * rate), int(GAP_SECONDS * rate)
    for bit in bits:
        freq = 2350 if bit else 1500
        out.extend(math.sin(2 * math.pi * freq * i / rate) * _fade(i, length, rate // 1000)
                   for i in range(length))
        out.extend([0.0] * gap)
    return out


def samples(rate=RATE):
    """The cue as 16-bit samples: whirr, a short pause, then the bits."""
    sound = _whirr(rate) + [0.0] * int(0.02 * rate) + _chirp(rate)
    return array('h', (int(PEAK * max(-1.0, min(1.0, value))) for value in sound))


def write_wav(path, rate=RATE):
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples(rate).tobytes())


class Cue:
    """Plays the cue without blocking; settle() lets the answer wait for it."""

    def __init__(self, path, device, popen=subprocess.Popen, enabled=True):
        self.path, self.device, self.popen = path, device, popen
        self.enabled = enabled
        self.process = None
        self._ready = False

    def _prepare(self):
        if not self._ready:
            write_wav(self.path)
            self._ready = True

    def play(self):
        """Start the cue; False when it is switched off or could not start."""
        if not self.enabled:
            return False
        self.stop()
        try:
            self._prepare()
            self.process = self.popen(
                ['/usr/bin/aplay', '-q', '-D', audio_output.current(self.device),
                 str(self.path)],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.process = None
            return False
        return True

    def settle(self, timeout=0.6):
        """Wait (briefly) until the cue has finished, so the device is free."""
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()

    def stop(self):
        process, self.process = self.process, None
        if process is not None and process.poll() is None:
            process.kill()
