"""Who is speaking: voice embeddings (CT 107) compared with the operator's voiceprint.

The server computes a speaker embedding for every turn with sherpa-onnx and
the CAM++ model (VoxCeleb, 512 values, ~40 ms per turn on CT 107). The
operator's voiceprint is the mean of the embeddings of the enrollment
recordings; it lives on the memory stick and travels with the memory copy,
quantized to int8 (base64, ~700 characters).

Cosine similarity at or above THRESHOLD counts as the operator. Without an
enrolled voiceprint nothing changes; with one, an unknown voice gets no
personal memories and cannot change them. Every other outcome (too little
speech, no model, broken profile, error, two profiles too close) is no
recognized person either: decide() names it, the server answers in the
guest view. Scores are cosine similarities, not probabilities.
"""
import base64
import math
import os
import sys
from pathlib import Path

MODEL = Path(os.environ.get('SERVITOR_SPEAKER_MODEL',
                            '/opt/servitor-voice/models/speaker/campplus.onnx'))
THRESHOLD = float(os.environ.get('SERVITOR_SPEAKER_THRESHOLD', '0.5'))
MIN_SECONDS = 1.0       # less speech gives unreliable embeddings
DELTA = 0.05            # start value: best profile ahead of the second best by at least this
DIM_MAX = 1024

# decide() outcomes; only MATCHED names a person.
MATCHED, UNKNOWN, INSUFFICIENT, AMBIGUOUS, UNAVAILABLE = (
    'matched', 'unknown', 'insufficient', 'ambiguous', 'unavailable')


def normalize(vector):
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def encode(vector):
    """Unit vector -> base64 of int8 values."""
    values = bytes((max(-127, min(127, round(v * 127))) & 0xFF) for v in normalize(vector))
    return base64.b64encode(values).decode('ascii')


def decode(text):
    """base64 int8 -> unit vector, or None."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        return None
    if not 16 <= len(raw) <= DIM_MAX:
        return None
    return normalize([b - 256 if b > 127 else b for b in raw])


def cosine(a, b):
    if a is None or b is None or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b))


def average(vectors):
    vectors = [v for v in vectors if v]
    if not vectors:
        return None
    dim = len(vectors[0])
    vectors = [v for v in vectors if len(v) == dim]
    return normalize([sum(v[i] for v in vectors) / len(vectors) for i in range(dim)])


def _levels(pcm, rate, frame_seconds):
    """(samples, frame size, RMS per frame) of 16 kHz mono int16 PCM."""
    from array import array
    samples = array('h', pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    size = max(1, int(rate * frame_seconds))
    levels = [math.sqrt(sum(s * s for s in samples[i:i + size]) / size)
              for i in range(0, len(samples) - size + 1, size)]
    return samples, size, levels


SPEECH_FLOOR = 200.0    # RMS (~ -44 dBFS): below this a frame is never speech
NOISE_CAP = 600.0       # a "noise floor" above this is taken to be continuous speech


def speech_seconds(pcm, rate=16000, frame_seconds=0.03):
    """Seconds of speech frames, not the length of the recording: a long,
    mostly silent take is still too short. A frame counts when it is twice
    (6 dB) as loud as the quiet tenth of the take; the cap keeps a take
    spoken without pauses from measuring itself as noise. An energy
    estimate until the shared VAD path of the concept replaces it."""
    _, _, levels = _levels(pcm, rate, frame_seconds)
    if not levels:
        return 0.0
    floor = min(sorted(levels)[len(levels) // 10], NOISE_CAP)
    limit = max(SPEECH_FLOOR, floor * 2)
    return sum(level >= limit for level in levels) * frame_seconds


def trim_silence(pcm, rate=16000, frame_seconds=0.03, pad_seconds=0.2, min_seconds=MIN_SECONDS):
    """16 kHz mono int16 PCM without the quiet start and end (beep echo, waiting
    for the speaker); silence dilutes the embedding of a short take. Returns
    the input unchanged when too little speech (< min_seconds) would remain."""
    samples, size, levels = _levels(pcm, rate, frame_seconds)
    if not levels:
        return pcm
    floor = sorted(levels)[len(levels) // 5]
    limit = max(200.0, floor * 3, max(levels) * 0.05)
    loud = [i for i, level in enumerate(levels) if level >= limit]
    if not loud:
        return pcm
    pad = int(pad_seconds / frame_seconds)
    start = max(0, loud[0] - pad) * size
    end = min(len(samples), (loud[-1] + 1 + pad) * size)
    if end - start < min_seconds * rate:
        return pcm
    return pcm[start * 2:end * 2]


def identify(embedding, voiceprints, threshold=THRESHOLD):
    """(name, score) of the best enrolled voice above threshold, else (None, best)."""
    best_name, best = None, None
    for print_ in voiceprints or []:
        score = cosine(embedding, print_.get('vector'))
        if score is not None and (best is None or score > best):
            best_name, best = print_.get('name'), score
    if best is not None and best >= threshold:
        return best_name, best
    return None, best


def decide(embedding, voiceprints, threshold=THRESHOLD, delta=DELTA):
    """Structured identity result: {'status', 'name', 'best', 'second'}.

    'name' is set only for MATCHED: the best profile reaches the threshold
    and, with several profiles, leads the second best by at least delta.
    Nothing falls back to the runner-up when the best one misses."""
    scores = []
    for print_ in voiceprints or []:
        score = cosine(embedding, print_.get('vector'))
        if score is not None and math.isfinite(score):
            scores.append((score, print_.get('name')))
    if embedding is None or not scores:
        return dict(status=UNAVAILABLE, name=None, best=None, second=None)
    scores.sort(key=lambda item: item[0], reverse=True)
    best, name = scores[0]
    second = scores[1][0] if len(scores) > 1 else None
    result = dict(status=UNKNOWN, name=None, best=best, second=second)
    if best < threshold:
        return result
    if second is not None and best - second < delta:
        return dict(result, status=AMBIGUOUS)
    return dict(result, status=MATCHED, name=name)


class Embedder:
    """Server side, loaded on first use (keeps startup and tests light)."""

    def __init__(self, model=MODEL):
        self.model = Path(model)
        self._extractor = None

    @property
    def available(self):
        return self.model.is_file()

    def embed(self, pcm, rate=16000):
        """16 kHz mono int16 PCM -> unit embedding, or None if too short or the
        model gives no usable vector. Callers check speech_seconds() first, on
        the untrimmed take (trimming raises the measured noise floor)."""
        if len(pcm) < MIN_SECONDS * rate * 2:
            return None
        import numpy as np
        import sherpa_onnx
        if self._extractor is None:
            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model),
                                                                 num_threads=1)
            self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        samples = np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype=np.int16)
        stream = self._extractor.create_stream()
        stream.accept_waveform(rate, (samples.astype(np.float32) / 32768.0))
        stream.input_finished()
        vector = list(self._extractor.compute(stream))
        if not vector or len(vector) > DIM_MAX or not all(math.isfinite(v) for v in vector) \
                or not any(vector):
            return None
        return normalize(vector)
