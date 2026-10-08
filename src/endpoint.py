"""End of an utterance without a button: speech first, then a pause.

Fed with the same 16 kHz mono int16 chunks the recorder streams; decides on
RMS against the noise floor measured at the start of the recording.
"""
import math
from array import array


def rms(pcm):
    samples = array('h')
    samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(value * value for value in samples) / len(samples))


class Endpointer:
    """feed() returns None while undecided, 'end' after speech plus a pause,
    'timeout' if no speech started within onset_timeout seconds."""

    def __init__(self, rate=16000, silence=0.9, onset_timeout=5.0, min_rms=300.0, factor=3.0):
        self.rate = rate
        self.silence = silence
        self.onset_timeout = onset_timeout
        self.min_rms = min_rms
        self.factor = factor
        self.floor = None
        self.elapsed = 0.0
        self.quiet = 0.0
        self.speaking = False
        self.result = None

    def feed(self, pcm):
        if self.result is not None or not pcm:
            return self.result
        seconds = len(pcm) / 2 / self.rate
        level = rms(pcm)
        if self.floor is None:
            self.floor = level
        threshold = max(self.min_rms, self.floor * self.factor)
        self.elapsed += seconds
        if level >= threshold:
            self.speaking = True
            self.quiet = 0.0
        else:
            if not self.speaking:
                self.floor = min(self.floor, level) * 0.5 + self.floor * 0.5
            self.quiet += seconds
        if self.speaking and self.quiet >= self.silence:
            self.result = 'end'
        elif not self.speaking and self.elapsed >= self.onset_timeout:
            self.result = 'timeout'
        return self.result
