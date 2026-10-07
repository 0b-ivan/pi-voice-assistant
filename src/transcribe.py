#!/usr/bin/env python3
"""Local Vosk speech-to-text adapter for bounded WAV captures and live PTT PCM."""

import argparse
from array import array
import json
import os
from pathlib import Path
import sys
import subprocess
import wave


DEFAULT_VOSK_MODEL_PATH = "/opt/pi-voice-assistant/models/vosk-model-small-de-0.15"
VOSK_SAMPLE_RATE = 16000
_VOSK_MODEL = None
_VOSK_MODEL_PATH = None


class TranscriptionError(RuntimeError):
    """Raised when local STT cannot produce a transcript."""


def _provider() -> str:
    provider = os.environ.get("STT_PROVIDER", "vosk").strip().lower()
    if provider != "vosk":
        raise TranscriptionError("STT_PROVIDER must be vosk")
    return provider


def _audio_path(path: str | os.PathLike[str]) -> Path:
    audio_path = Path(path)
    if not audio_path.is_file():
        raise TranscriptionError(f"audio file not found: {audio_path}")
    if audio_path.stat().st_size == 0:
        raise TranscriptionError("audio file is empty")
    return audio_path


def _downmix_and_resample(samples: array, channels: int, rate: int) -> bytes:
    if channels == 2:
        mono = array(
            "h",
            (
                (int(samples[index]) + int(samples[index + 1])) // 2
                for index in range(0, len(samples) - 1, 2)
            ),
        )
    else:
        mono = samples

    if rate == 48000:
        mono = array(
            "h",
            (
                (int(mono[index]) + int(mono[index + 1]) + int(mono[index + 2]))
                // 3
                for index in range(0, len(mono) - 2, 3)
            ),
        )
    elif rate != VOSK_SAMPLE_RATE:
        raise TranscriptionError(
            f"Vosk supports 16000 Hz or 48000 Hz input, got {rate} Hz"
        )

    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


def _iter_vosk_pcm(path: Path):
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            rate = audio.getframerate()
            if audio.getcomptype() != "NONE":
                raise TranscriptionError("Vosk requires uncompressed PCM WAV")
            if sample_width != 2:
                raise TranscriptionError(
                    f"Vosk requires 16-bit PCM WAV, got {sample_width * 8}-bit"
                )
            if channels not in (1, 2):
                raise TranscriptionError(
                    f"Vosk requires mono or stereo WAV, got {channels} channels"
                )
            if rate not in (VOSK_SAMPLE_RATE, 48000):
                raise TranscriptionError(
                    f"Vosk supports 16000 Hz or 48000 Hz input, got {rate} Hz"
                )

            while True:
                raw = audio.readframes(4800)
                if not raw:
                    break
                samples = array("h")
                samples.frombytes(raw)
                if sys.byteorder != "little":
                    samples.byteswap()
                pcm = _downmix_and_resample(samples, channels, rate)
                if pcm:
                    yield pcm
    except (wave.Error, EOFError) as exc:
        raise TranscriptionError(f"invalid WAV for Vosk: {exc}") from exc


def _vosk_module():
    vendor_path = Path(
        os.environ.get("VOSK_PYTHON_PATH", "/opt/pi-voice-assistant/vendor")
    )
    if vendor_path.is_dir() and str(vendor_path) not in sys.path:
        sys.path.insert(0, str(vendor_path))
    try:
        import vosk
    except (ImportError, OSError) as exc:
        raise TranscriptionError(
            "Vosk Python package is unavailable; run scripts/install-vosk.sh"
        ) from exc
    return vosk


def _load_vosk_model():
    global _VOSK_MODEL, _VOSK_MODEL_PATH

    model_path = Path(os.environ.get("VOSK_MODEL_PATH", DEFAULT_VOSK_MODEL_PATH))
    if not model_path.is_dir():
        raise TranscriptionError(f"Vosk model not found: {model_path}")

    if _VOSK_MODEL is not None and _VOSK_MODEL_PATH == model_path:
        return _VOSK_MODEL

    vosk = _vosk_module()
    try:
        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_path))
    except Exception as exc:
        raise TranscriptionError(f"failed to load Vosk model {model_path}: {exc}") from exc

    _VOSK_MODEL = model
    _VOSK_MODEL_PATH = model_path
    return model


def _result_text(payload: str, source: str) -> str:
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TranscriptionError(f"invalid Vosk {source} result: {exc}") from exc
    text = result.get("text") if isinstance(result, dict) else None
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TranscriptionError(f"invalid Vosk {source} result: {result!r}")
    return text.strip()


def prepare_vosk() -> None:
    """Load the local model now so the first PTT press never pays model startup."""
    _provider()
    _load_vosk_model()


class LiveVoskRecognizer:
    """Incremental 16-kHz mono recognizer for audio captured while PTT is held."""

    def __init__(self):
        _provider()
        try:
            vosk = _vosk_module()
            self.recognizer = vosk.KaldiRecognizer(
                _load_vosk_model(), VOSK_SAMPLE_RATE
            )
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(
                f"failed to initialize live Vosk recognizer: {exc}"
            ) from exc
        self.parts = []
        self.finished = False

    def accept_pcm(self, pcm: bytes) -> None:
        if self.finished:
            raise TranscriptionError("live Vosk recognizer is already finalized")
        if not pcm:
            return
        if len(pcm) % 2:
            raise TranscriptionError("live Vosk requires aligned 16-bit PCM")
        try:
            if self.recognizer.AcceptWaveform(pcm):
                text = _result_text(self.recognizer.Result(), "segment")
                if text:
                    self.parts.append(text)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"live Vosk failed: {exc}") from exc

    def finish(self) -> str:
        if self.finished:
            raise TranscriptionError("live Vosk recognizer is already finalized")
        self.finished = True
        try:
            final_text = _result_text(self.recognizer.FinalResult(), "final")
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"live Vosk finalization failed: {exc}") from exc
        if final_text:
            self.parts.append(final_text)
        text = " ".join(self.parts).strip()
        if not text:
            raise TranscriptionError("Vosk returned no transcript")
        return text


def transcribe_vosk(path: str | os.PathLike[str]) -> str:
    audio_path = _audio_path(path)
    model = _load_vosk_model()

    try:
        vosk = _vosk_module()
        recognizer = vosk.KaldiRecognizer(model, VOSK_SAMPLE_RATE)
        parts = []
        for pcm in _iter_vosk_pcm(audio_path):
            if recognizer.AcceptWaveform(pcm):
                text = _result_text(recognizer.Result(), "segment")
                if text:
                    parts.append(text)
        final_text = _result_text(recognizer.FinalResult(), "final")
        if final_text:
            parts.append(final_text)
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError(f"Vosk transcription failed: {exc}") from exc

    text = " ".join(parts).strip()
    if not text:
        raise TranscriptionError("Vosk returned no transcript")
    return text


def transcribe_with_provider(path: str | os.PathLike[str]) -> tuple[str, str]:
    _provider()
    if os.environ.get("PTT_MEMORY_MODE", "resident") == "isolated":
        # Never load the native model in the controller. Reap this worker before
        # returning its transcript so subsequent Piper cannot overlap Vosk.
        environment = dict(os.environ, PTT_MEMORY_MODE="resident")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), str(path)],
                env=environment, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError("isolated Vosk exceeded 120 seconds") from exc
        if result.returncode:
            raise TranscriptionError(
                f"isolated Vosk failed ({result.returncode}): {result.stderr[-2000:]}"
            )
        text = result.stdout.strip()
        if not text:
            raise TranscriptionError("isolated Vosk returned no transcript")
        return text, "vosk"
    return transcribe_vosk(path), "vosk"


def transcribe(path: str | os.PathLike[str]) -> str:
    text, _provider_name = transcribe_with_provider(path)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="WAV file to transcribe")
    args = parser.parse_args()
    try:
        text, provider = transcribe_with_provider(args.audio)
        print(text)
        print(f"STT_PROVIDER_USED={provider}", file=os.sys.stderr)
    except (OSError, TranscriptionError) as exc:
        print(f"STT_ERROR: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
