"""Wake word detection with openWakeWord's ONNX models, without the package.

The openwakeword package pulls in scipy and scikit-learn (unused here) and
requires tflite-runtime, which has no wheel for Python 3.13 on the Pi. Its
streaming inference is three small ONNX models and some buffers, rebuilt
here with onnxruntime and numpy as already used by Piper:

  16 kHz int16 audio, 80 ms blocks (1280 samples)
    -> melspectrogram.onnx on the last 1280 + 480 samples, x / 10 + 2
    -> embedding_model.onnx on the last 76 mel frames -> one 96-d embedding
    -> <word>.onnx on the last 16 embeddings -> score 0..1

Buffer handling follows openwakeword 0.6.0 (utils.AudioFeatures,
model.Model.predict); scores matched the package exactly on test clips.

On the Pi Zero 2 W the embedding model costs ~36 of 45 ms per 80 ms block
(56 % of a core). With ``gate=True`` quiet blocks skip it: the cheap mel
spectrogram keeps running, the history gets a cached embedding of the
room's silence (recomputed every ``refresh`` quiet blocks) and the
classifier is not run (score 0). Blocks louder than the noise floor, and
about a second after them, are processed exactly as above.
"""
from collections import deque
from pathlib import Path

import numpy as np

FRAME = 1280            # 80 ms at 16 kHz
CONTEXT = 160 * 3       # extra samples the melspectrogram needs per block
MEL_WINDOW = 76         # mel frames per embedding
MEL_MAX = 10 * 97       # ~10 s of mel frames
FEATURE_MAX = 120       # ~10 s of embeddings
FEATURES = 16           # embeddings per classification
GATE_FACTOR = 2.5       # louder than this times the noise floor counts as sound
GATE_MIN_RMS = 150.0    # ... and at least this loud (int16 RMS)
GATE_HANGOVER = 12      # blocks (~1 s) processed exactly after the last sound
GATE_REFRESH = 25       # quiet blocks (~2 s) between fresh silence embeddings


def _session(path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options,
                                providers=['CPUExecutionProvider'])


class WakeWord:
    """Feed 16 kHz mono int16 PCM; returns the highest score of the blocks
    completed by that call (None while less than 80 ms is buffered)."""

    def __init__(self, model_dir, word='hey_jarvis_v0.1', session=_session, seed=0,
                 gate=False, shadows=()):
        model_dir = Path(model_dir)
        self.word = word
        self._mel = session(model_dir / 'melspectrogram.onnx')
        self._embedding = session(model_dir / 'embedding_model.onnx')
        self._classifier = session(model_dir / f'{word}.onnx')
        self._mel_input = self._mel.get_inputs()[0].name
        self._embedding_input = self._embedding.get_inputs()[0].name
        self._classifier_input = self._classifier.get_inputs()[0].name
        # Shadow words: scored on the same embeddings (cheap), never trigger.
        self._shadows = {}
        for name in shadows:
            classifier = session(model_dir / f'{name}.onnx')
            self._shadows[name] = (classifier, classifier.get_inputs()[0].name)
        self.gate = gate
        self.reset(seed)

    def reset(self, seed=0):
        """Start fresh, e.g. after the microphone was handed to a recording."""
        self._raw = np.zeros(0, dtype=np.int16)
        self._pending = np.zeros(0, dtype=np.int16)
        self._mel_buffer = np.ones((MEL_WINDOW, 32), dtype=np.float32)
        # Like openwakeword: start from embeddings of random noise, so the
        # classifier never sees an all-zero history.
        noise = np.random.default_rng(seed).integers(-1000, 1000, 16000 * 4).astype(np.int16)
        self._features = deque(self._embeddings_of(noise), maxlen=FEATURE_MAX)
        self._floor = None
        self._active = 0           # remaining blocks to process exactly
        self._quiet = GATE_REFRESH  # quiet blocks since the silence embedding
        self._silence = None
        self.exact_blocks = self.gated_blocks = 0
        self.last_rms = 0.0
        self.shadow_scores = {name: 0.0 for name in getattr(self, '_shadows', {})}
        self._segment_peaks = dict(self.shadow_scores)
        self.segment_peaks = []   # [{word: peak}] per loud stretch, for evaluation

    def _loud(self, block):
        rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
        self.last_rms = rms
        if self._floor is None:
            self._floor = rms
        loud = rms > max(GATE_MIN_RMS, self._floor * GATE_FACTOR)
        if not loud:  # track the room noise slowly, only while quiet
            self._floor = 0.95 * self._floor + 0.05 * rms
        return loud

    def _melspectrogram(self, samples):
        spec = self._mel.run(None, {self._mel_input: samples[None, :].astype(np.float32)})[0]
        return np.squeeze(spec) / 10 + 2

    def _embed(self, window):
        x = window.astype(np.float32)[None, :, :, None]
        return np.squeeze(self._embedding.run(None, {self._embedding_input: x})[0])

    def stats(self):
        """Gate counters since the last call (for the service journal)."""
        total = self.exact_blocks + self.gated_blocks
        result = dict(blocks=total, exact_pct=round(self.exact_blocks * 100 / total) if total else 0,
                      floor_rms=round(self._floor or 0), last_rms=round(self.last_rms))
        self.exact_blocks = self.gated_blocks = 0
        return result

    def _embeddings_of(self, audio):
        spec = self._melspectrogram(audio)
        return [self._embed(spec[i:i + MEL_WINDOW])
                for i in range(0, spec.shape[0] - MEL_WINDOW + 1, 8)]

    def process(self, pcm):
        audio = np.frombuffer(pcm, dtype=np.int16) if isinstance(pcm, (bytes, bytearray)) else pcm
        self._pending = np.concatenate((self._pending, audio))
        best = None
        while self._pending.shape[0] >= FRAME:
            block, self._pending = self._pending[:FRAME], self._pending[FRAME:]
            self._raw = np.concatenate((self._raw, block))[-(FRAME + CONTEXT):]
            self._mel_buffer = np.vstack((self._mel_buffer,
                                          self._melspectrogram(self._raw)))[-MEL_MAX:]
            if self.gate and self._loud(block):
                self._active = GATE_HANGOVER + 1  # this block plus the hangover
            exact = not self.gate or self._active > 0
            if self._active:
                self._active -= 1
            if exact:
                self.exact_blocks += 1
                self._features.append(self._embed(self._mel_buffer[-MEL_WINDOW:]))
                x = np.array(list(self._features)[-FEATURES:], dtype=np.float32)[None, :, :]
                score = float(np.squeeze(
                    self._classifier.run(None, {self._classifier_input: x})[0]))
                for name, (classifier, name_input) in self._shadows.items():
                    value = float(np.squeeze(classifier.run(None, {name_input: x})[0]))
                    self.shadow_scores[name] = value
                    self._segment_peaks[name] = max(self._segment_peaks[name], value)
                self._quiet = GATE_REFRESH  # next quiet block gets a fresh silence embedding
            else:
                if self._shadows and any(self._segment_peaks.values()):
                    # A loud stretch just ended: keep its peak shadow scores.
                    self.segment_peaks.append(dict(self._segment_peaks))
                    del self.segment_peaks[:-20]
                    self._segment_peaks = {name: 0.0 for name in self._shadows}
                self.shadow_scores = {name: 0.0 for name in self._shadows}
                self.gated_blocks += 1
                if self._silence is None or self._quiet >= GATE_REFRESH:
                    self._silence = self._embed(self._mel_buffer[-MEL_WINDOW:])
                    self._quiet = 0
                self._quiet += 1
                self._features.append(self._silence)
                score = 0.0
            best = score if best is None else max(best, score)
        return best


class Detector:
    """Score threshold with patience (consecutive blocks) and a cooldown."""

    def __init__(self, wakeword, threshold=0.5, patience=2, cooldown=2.0, shadow_thresholds=None):
        self.wakeword = wakeword
        self.threshold = threshold
        self.patience = patience
        self.cooldown = cooldown
        self._streak = 0
        self._quiet_until = 0.0
        self.shadow_thresholds = dict(shadow_thresholds or {})
        self._shadow_streak = {name: 0 for name in self.shadow_thresholds}
        self._shadow_quiet = {name: 0.0 for name in self.shadow_thresholds}
        self.shadow_hits = []     # (word, score) the shadow word would have triggered

    def _shadow(self, now):
        for name, threshold in self.shadow_thresholds.items():
            score = self.wakeword.shadow_scores.get(name, 0.0)
            if now < self._shadow_quiet[name]:
                continue
            self._shadow_streak[name] = self._shadow_streak[name] + 1 if score >= threshold else 0
            if self._shadow_streak[name] >= self.patience:
                self._shadow_streak[name] = 0
                self._shadow_quiet[name] = now + self.cooldown
                self.shadow_hits.append((name, round(score, 3)))

    def feed(self, pcm, now):
        score = self.wakeword.process(pcm)
        if score is None:
            return False
        if self.shadow_thresholds:
            self._shadow(now)
        if now < self._quiet_until:
            self._streak = 0
            return False
        self._streak = self._streak + 1 if score >= self.threshold else 0
        if self._streak >= self.patience:
            self._streak = 0
            self._quiet_until = now + self.cooldown
            return True
        return False
