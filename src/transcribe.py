#!/usr/bin/env python3
"""Local Vosk speech-to-text adapter for bounded WAV captures and live PTT PCM."""

import argparse
from array import array
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import threading
import queue
from contextlib import nullcontext
from runtime_metrics import phase, process_ready
import wave


DEFAULT_VOSK_MODEL_PATH = "/opt/pi-voice-assistant/models/vosk-model-small-de-0.15"
VOSK_SAMPLE_RATE = 16000
_VOSK_MODEL = None
_VOSK_MODEL_PATH = None


class TranscriptionError(RuntimeError):
    """Raised when local STT cannot produce a transcript."""


class NoSpeechError(TranscriptionError):
    """Recognition worked but heard no words; retrying elsewhere won't help."""


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


def _stt_phase(metric):
    return (phase('stt', metric, stream=sys.stderr)
            if os.environ.get('VOICE_WORKER_TIMINGS') == '1' else nullcontext())


def _vosk_module():
    vendor_path = Path(
        os.environ.get("VOSK_PYTHON_PATH", "/opt/pi-voice-assistant/vendor")
    )
    if vendor_path.is_dir() and str(vendor_path) not in sys.path:
        sys.path.insert(0, str(vendor_path))
    try:
        with (_stt_phase('import') if 'vosk' not in sys.modules else nullcontext()):
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
        with _stt_phase('model_load'):
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
            raise NoSpeechError("Vosk returned no transcript")
        return text


def transcribe_vosk(path: str | os.PathLike[str]) -> str:
    audio_path = _audio_path(path)
    model = _load_vosk_model()

    with _stt_phase('recognition'):
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



_PREPARED_VOSK = None
_PREPARED_LOCK = threading.Lock()
_PREPARED_LAST_START = 0.0


def _reap_prepared(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    for stream in (proc.stdin, proc.stdout):
        if stream is not None:
            stream.close()


def stop_prepared_vosk():
    """Release a standby model before speech; never touch a claimed STT job."""
    global _PREPARED_VOSK
    with _PREPARED_LOCK:
        proc, _PREPARED_VOSK = _PREPARED_VOSK, None
    if proc is not None:
        _reap_prepared(proc)


def prepare_vosk_worker():
    """Start Vosk during idle time; stdout stays empty until a WAV is supplied."""
    global _PREPARED_VOSK, _PREPARED_LAST_START
    if os.environ.get('PTT_MEMORY_MODE') != 'hybrid':
        return
    with _PREPARED_LOCK:
        if _PREPARED_VOSK is not None:
            if _PREPARED_VOSK.poll() is None:
                return
            _reap_prepared(_PREPARED_VOSK)
            _PREPARED_VOSK = None
        if time.monotonic() - _PREPARED_LAST_START < 5:
            return
        _PREPARED_LAST_START = time.monotonic()
        environment = dict(os.environ, PTT_MEMORY_MODE='resident',
            VOICE_WORKER_TIMINGS='1', VOICE_WORKER_STARTED_AT=str(time.monotonic()))
        _PREPARED_VOSK = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '--prepared-worker'],
            env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True,
        )


def _take_prepared_vosk():
    global _PREPARED_VOSK
    with _PREPARED_LOCK:
        proc, _PREPARED_VOSK = _PREPARED_VOSK, None
    if proc is not None and proc.poll() is not None:
        _reap_prepared(proc)
        return None
    return proc



class RemoteLiveVoskRecognizer:
    """Feed PCM without blocking capture; reap the native worker before TTS."""
    def __init__(self):
        self.proc = _take_prepared_vosk()
        if self.proc is None:
            environment = dict(os.environ, PTT_MEMORY_MODE='resident',
                VOICE_WORKER_TIMINGS='1', VOICE_WORKER_STARTED_AT=str(time.monotonic()))
            self.proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--prepared-worker'],
                env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.pending = queue.Queue(maxsize=512) # >30 seconds at 100 ms/chunk.
        self.cancelled = False
        self.failure = None
        self.finishing = False
        self.state_lock = threading.Lock()
        try:
            self.proc.stdin.write('@LIVE16000\n')
            self.proc.stdin.flush()
        except OSError as exc:
            _reap_prepared(self.proc)
            raise TranscriptionError(f'live Vosk worker failed to start: {exc}') from exc
        self.writer = threading.Thread(target=self._write, name='vosk-pcm-writer', daemon=True)
        self.writer.start()

    def _write(self):
        try:
            while not self.cancelled:
                data = self.pending.get()
                if data is None:
                    break
                self.proc.stdin.buffer.write(data)
                self.proc.stdin.buffer.flush()
        except (OSError, ValueError) as exc:
            self.failure = str(exc)
        finally:
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    def accept_pcm(self, pcm):
        if self.cancelled or self.failure is not None:
            raise TranscriptionError(self.failure or 'live Vosk cancelled')
        try:
            self.pending.put_nowait(pcm)
        except queue.Full as exc:
            raise TranscriptionError('live Vosk PCM queue exceeded recording limit') from exc

    def cancel(self):
        with self.state_lock:
            self.cancelled = True
        try:
            self.pending.put_nowait(None)
        except queue.Full:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        self.writer.join(timeout=1)
        with self.state_lock:
            if not self.finishing:
                _reap_prepared(self.proc)

    def finish(self):
        with self.state_lock:
            self.finishing = True
        try:
            timeout = float(os.environ.get('VOSK_LIVE_FINALIZE_TIMEOUT_SECONDS', '6'))
        except ValueError:
            timeout = 6
        if not 0 < timeout <= 6:
            timeout = 6
        deadline = time.monotonic() + timeout
        try:
            if self.cancelled:
                raise TranscriptionError('live Vosk cancelled')
            with _stt_phase('live_finalize'):
                self.pending.put_nowait(None)
                self.writer.join(timeout=max(0, deadline-time.monotonic()))
                if self.writer.is_alive():
                    raise TranscriptionError('live Vosk worker did not drain within its deadline')
                if self.failure is not None:
                    raise TranscriptionError(f'live Vosk PCM writer failed: {self.failure}')
                # The writer closed stdin; communicate must not flush it again.
                self.proc.stdin = None
                output, _ = self.proc.communicate(timeout=max(0, deadline-time.monotonic()))
                if self.proc.returncode != 0:
                    raise TranscriptionError(f'live Vosk worker failed ({self.proc.returncode})')
                text = output.strip()
                if not text:
                    raise TranscriptionError('Vosk returned no transcript')
                return text
        except (subprocess.TimeoutExpired, OSError, ValueError, queue.Full) as exc:
            raise TranscriptionError(f'live Vosk worker finalization failed: {exc}') from exc
        finally:
            self.cancel()
            with self.state_lock:
                self.finishing = False
            _reap_prepared(self.proc)


def transcribe_with_provider(path: str | os.PathLike[str]) -> tuple[str, str]:
    _provider()
    if os.environ.get("PTT_MEMORY_MODE", "resident") in ("isolated", "hybrid"):
        # Never load the native model in the controller. Reap this worker before
        # returning its transcript so subsequent Piper cannot overlap Vosk.
        environment = dict(os.environ, PTT_MEMORY_MODE="resident",
                           VOICE_WORKER_TIMINGS="1",
                           VOICE_WORKER_STARTED_AT=str(time.monotonic()))
        proc = _take_prepared_vosk() if os.environ.get('PTT_MEMORY_MODE') == 'hybrid' else None
        try:
            if proc is None:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve()), str(path)],
                    env=environment, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=120,
                )
            else:
                output, errors = proc.communicate(str(path) + '\n', timeout=120)
                result = subprocess.CompletedProcess(proc.args, proc.returncode,
                                                     output, errors or '')
        except subprocess.TimeoutExpired as exc:
            if proc is not None:
                _reap_prepared(proc)
            raise TranscriptionError("isolated Vosk exceeded 120 seconds") from exc
        finally:
            if proc is not None:
                _reap_prepared(proc)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr, flush=True)
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
    parser.add_argument("audio", nargs="?", help="WAV file to transcribe")
    parser.add_argument("--prepared-worker", action="store_true")
    args = parser.parse_args()
    try:
        if os.environ.get('VOICE_WORKER_TIMINGS') == '1':
            process_ready('stt')
        if args.prepared_worker:
            prepare_vosk()
            print(json.dumps(dict(version=1, event='stt_prepared')), file=sys.stderr, flush=True)
            args.audio = sys.stdin.buffer.readline().decode('utf-8').strip()
            if not args.audio:
                return 0
            if args.audio == '@LIVE16000':
                recognizer = LiveVoskRecognizer()
                with _stt_phase('live_recognition'):
                    while True:
                        pcm = sys.stdin.buffer.read(3200)
                        if not pcm:
                            break
                        recognizer.accept_pcm(pcm)
                    text = recognizer.finish()
                print(text, flush=True)
                print('STT_PROVIDER_USED=vosk', file=sys.stderr, flush=True)
                # This dedicated worker only reads audio. Let the OS release
                # its native model instead of traversing it during Python GC.
                os._exit(0)
        if not args.audio:
            parser.error('audio is required')
        text, provider = transcribe_with_provider(args.audio)
        print(text, flush=True)
        print(f"STT_PROVIDER_USED={provider}", file=os.sys.stderr, flush=True)
        if args.prepared_worker:
            os._exit(0)
    except (OSError, TranscriptionError) as exc:
        print(f"STT_ERROR: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
