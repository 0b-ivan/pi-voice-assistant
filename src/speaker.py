"""Who is speaking: voice embeddings (CT 107) compared with the operator's voiceprint.

The server computes a speaker embedding for every turn with sherpa-onnx and
the CAM++ model (VoxCeleb, 512 values, ~40 ms per turn on CT 107). The
operator's voiceprint is the mean of the embeddings of the enrollment
recordings; it lives on the memory stick and travels with the memory copy,
quantized to int8 (base64, ~700 characters).

Cosine similarity at or above THRESHOLD counts as the operator. Without an
enrolled voiceprint nothing changes; with one, an unknown voice gets no
personal memories and cannot change them.
"""
import base64
import math
import os
from pathlib import Path

MODEL = Path(os.environ.get('SERVITOR_SPEAKER_MODEL',
                            '/opt/servitor-voice/models/speaker/campplus.onnx'))
THRESHOLD = float(os.environ.get('SERVITOR_SPEAKER_THRESHOLD', '0.5'))
MIN_SECONDS = 1.0       # shorter audio gives unreliable embeddings
DIM_MAX = 1024


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


class Embedder:
    """Server side, loaded on first use (keeps startup and tests light)."""

    def __init__(self, model=MODEL):
        self.model = Path(model)
        self._extractor = None

    @property
    def available(self):
        return self.model.is_file()

    def embed(self, pcm, rate=16000):
        """16 kHz mono int16 PCM -> unit embedding, or None if too short."""
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
        return normalize(list(self._extractor.compute(stream)))
