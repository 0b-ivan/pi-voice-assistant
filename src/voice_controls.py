"""Nonblocking STT job, owned speech subprocess and WM8960 playback volume."""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import wave

def _speech_event(name, **fields):
    import json
    print(json.dumps(dict(version=1, event=name, **fields)), flush=True)


from voice_effects import (
    build_playback_command,
    build_stream_playback_command,
    resolve_voice_profile,
)


class TranscriptionJob:
    def __init__(self, transcribe, path):
        self.cancelled = False
        self.done = threading.Event()
        self.result = None
        self.error = None

        def run():
            try:
                self.result = transcribe(path)
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, name='stt', daemon=True)
        self.thread.start()

    def cancel(self):
        # Native Vosk calls cannot be interrupted safely. Keep this job's
        # capture/model exclusively owned until done, and discard its result.
        self.cancelled = True


def _terminate_process_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=1)
    # The leader may terminate before an aplay/ffmpeg child that ignores SIGTERM.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _load_piper_voice(model):
    """Import Piper from its dedicated venv into the long-running service."""
    venv = Path(os.environ.get("PIPER_VENV", "/opt/pi-voice-assistant/.venv"))
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = venv / "lib" / version / "site-packages"
    if not site_packages.is_dir():
        raise RuntimeError(f"Piper site-packages missing: {site_packages}")
    if str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    try:
        from piper import PiperVoice
    except (ImportError, OSError) as exc:
        raise RuntimeError(f"failed to import Piper from {site_packages}: {exc}") from exc
    try:
        return PiperVoice.load(model)
    except Exception as exc:
        raise RuntimeError(f"failed to load Piper model {model}: {exc}") from exc


def _env_float(name, default):
    value = float(os.environ.get(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _servitor_synthesis_config(voice):
    try:
        from piper.config import SynthesisConfig
    except ImportError as exc:
        raise RuntimeError("Piper SynthesisConfig unavailable") from exc

    num_speakers = getattr(getattr(voice, "config", None), "num_speakers", None)
    speaker_id = int(os.environ.get(
        "TTS_PIPER_SPEAKER_ID", "0" if num_speakers == 1 else "4"
    ))
    if num_speakers is not None and not 0 <= speaker_id < num_speakers:
        raise ValueError(
            f"TTS_PIPER_SPEAKER_ID={speaker_id} outside model speaker range "
            f"0..{num_speakers - 1}"
        )

    return (
        SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=_env_float("TTS_PIPER_LENGTH_SCALE", 1.02),
            noise_scale=_env_float("TTS_PIPER_NOISE_SCALE", 0.22),
            noise_w_scale=_env_float("TTS_PIPER_NOISE_W_SCALE", 0.18),
        ),
        _env_float("TTS_PIPER_SENTENCE_SILENCE", 0.32),
    )


def _synthesize_voice(voice, text, audio, profile):
    """Write Piper audio, with restrained command cadence for Servitor fallback."""
    if profile != "servitor":
        voice.synthesize_wav(text, audio)
        return

    syn_config, sentence_silence = _servitor_synthesis_config(voice)
    wrote_format = False
    chunks = voice.synthesize(text, syn_config=syn_config)
    for index, chunk in enumerate(chunks):
        if not wrote_format:
            audio.setframerate(chunk.sample_rate)
            audio.setsampwidth(chunk.sample_width)
            audio.setnchannels(chunk.sample_channels)
            wrote_format = True
        if index > 0 and sentence_silence:
            silence_samples = int(chunk.sample_rate * sentence_silence)
            audio.writeframes(
                bytes(silence_samples * chunk.sample_width * chunk.sample_channels)
            )
        audio.writeframes(chunk.audio_int16_bytes)

    if not wrote_format:
        raise RuntimeError("Piper produced no audio chunks")


class _SpeechJob:
    def __init__(self):
        self.done = threading.Event()
        self.process = None
        self.result = None
        self.error = None
        self.thread = None
        self.playback_started_at = None
        self.activity_end = 0.0
        self.activity_segments = []


class ResidentSpeechOutput:
    """Keep Piper loaded and stream Servitor PCM to FFmpeg as soon as it exists."""

    def __init__(self, model, audio_device, runtime_dir, loader=None, popen=None,
                 profile=None):
        self.model = str(model)
        self.audio_device = audio_device
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile = resolve_voice_profile(profile)
        self.playback_mode = os.environ.get("TTS_PLAYBACK_MODE", "stream").strip().lower()
        if self.playback_mode not in ("stream", "buffered"):
            raise ValueError("TTS_PLAYBACK_MODE must be stream or buffered")
        self.voice = (loader or _load_piper_voice)(self.model)
        self._popen = popen or subprocess.Popen
        self._synthesis_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._job = None

    @property
    def active(self):
        with self._state_lock:
            return self._job is not None and not self._job.done.is_set()

    def _current(self, job):
        with self._state_lock:
            return self._job is job

    def _register_activity(self, job, active, duration):
        if duration <= 0:
            return
        now = time.monotonic()
        with self._state_lock:
            if self._job is not job:
                return
            if job.playback_started_at is None:
                job.playback_started_at = now
            elapsed = max(0.0, now - job.playback_started_at)
            start = max(job.activity_end, elapsed)
            end = start + duration
            job.activity_segments.append((start, end, bool(active)))
            job.activity_end = end

    @property
    def voice_level(self):
        """0..1 speech envelope used by the SHIM LED; pauses resolve to zero."""
        with self._state_lock:
            job = self._job
            if job is None or job.done.is_set():
                return 0.0
            if self.profile != "servitor" or self.playback_mode == "buffered":
                return 1.0
            started = job.playback_started_at
            segments = tuple(job.activity_segments)
        if started is None:
            return 0.0

        elapsed = time.monotonic() - started
        for start, end, active in segments:
            if start <= elapsed < end:
                if not active:
                    return 0.0
                edge = min(0.12, max(0.01, (end - start) / 2))
                attack = min(1.0, (elapsed - start) / edge)
                release = min(1.0, (end - elapsed) / edge)
                return max(0.0, min(1.0, attack, release))
        return 0.0

    @property
    def voice_active(self):
        return self.voice_level > 0.08

    @staticmethod
    def _write_pcm(proc, data):
        if not data:
            return
        if proc.stdin is None:
            raise RuntimeError("FFmpeg stdin pipe unavailable")
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError("FFmpeg closed the PCM stream") from exc

    def _run_servitor(self, job, text):
        proc = None
        started_at = time.monotonic()
        _speech_event("tts_synthesis_start", profile=self.profile, chars=len(text))
        with self._synthesis_lock:
            if not self._current(job):
                return

            syn_config, sentence_silence = _servitor_synthesis_config(self.voice)
            chunks = iter(self.voice.synthesize(text, syn_config=syn_config))
            try:
                chunk = next(chunks)
            except StopIteration as exc:
                raise RuntimeError("Piper produced no audio chunks") from exc

            first_chunk_at = time.monotonic()
            first_chunk_latency_ms = round((first_chunk_at - started_at) * 1000)
            _speech_event(
                "tts_first_chunk",
                profile=self.profile,
                latency_ms=first_chunk_latency_ms,
                bytes=len(chunk.audio_int16_bytes),
            )
            _speech_event(
                "latency",
                stage="tts",
                metric="first_chunk",
                latency_ms=first_chunk_latency_ms,
            )

            sample_rate = int(chunk.sample_rate)
            sample_width = int(chunk.sample_width)
            channels = int(chunk.sample_channels)
            if sample_width != 2:
                raise RuntimeError(
                    f"Servitor stream requires 16-bit PCM, got {sample_width * 8}-bit"
                )
            if sample_rate <= 0 or channels <= 0:
                raise RuntimeError("invalid Piper PCM format")

            proc_started_at = time.monotonic()
            proc = self._popen(
                build_stream_playback_command(
                    sample_rate,
                    channels,
                    self.audio_device,
                    profile=self.profile,
                ),
                stdin=subprocess.PIPE,
                start_new_session=True,
            )
            _speech_event(
                "tts_playback_start",
                profile=self.profile,
                latency_ms=round((time.monotonic() - started_at) * 1000),
                ffmpeg_start_ms=round((time.monotonic() - proc_started_at) * 1000),
            )
            with self._state_lock:
                if self._job is not job:
                    _terminate_process_group(proc)
                    return
                job.process = proc

            expected_format = (sample_rate, sample_width, channels)
            index = 0
            try:
                while True:
                    if not self._current(job):
                        return
                    chunk_format = (
                        int(chunk.sample_rate),
                        int(chunk.sample_width),
                        int(chunk.sample_channels),
                    )
                    if chunk_format != expected_format:
                        raise RuntimeError(
                            f"Piper PCM format changed mid-stream: "
                            f"{expected_format} -> {chunk_format}"
                        )

                    if index > 0 and sentence_silence:
                        silence_frames = int(sample_rate * sentence_silence)
                        silence = bytes(silence_frames * sample_width * channels)
                        self._register_activity(job, False, sentence_silence)
                        self._write_pcm(proc, silence)

                    pcm = chunk.audio_int16_bytes
                    bytes_per_second = sample_rate * sample_width * channels
                    duration = len(pcm) / bytes_per_second
                    self._register_activity(job, True, duration)
                    self._write_pcm(proc, pcm)

                    try:
                        chunk = next(chunks)
                    except StopIteration:
                        break
                    index += 1
            except RuntimeError:
                if not self._current(job):
                    return
                raise
            finally:
                if proc.stdin is not None and not proc.stdin.closed:
                    try:
                        proc.stdin.close()
                    except BrokenPipeError:
                        pass

        code = proc.wait()
        if self._current(job):
            job.result = code

    def _run_file(self, job, text):
        path = None
        try:
            fd, name = tempfile.mkstemp(
                prefix="speech-", suffix=".wav", dir=self.runtime_dir
            )
            os.close(fd)
            path = Path(name)
            with self._synthesis_lock:
                if not self._current(job):
                    return
                with wave.open(str(path), "wb") as audio:
                    _synthesize_voice(self.voice, text, audio, self.profile)
            if not self._current(job):
                return

            proc = self._popen(
                build_playback_command(
                    path,
                    self.audio_device,
                    profile=self.profile,
                ),
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            with self._state_lock:
                if self._job is not job:
                    _terminate_process_group(proc)
                    return
                job.process = proc
            code = proc.wait()
            if self._current(job):
                job.result = code
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def _run(self, job, text):
        try:
            if self.profile == "servitor" and self.playback_mode == "stream":
                self._run_servitor(job, text)
            else:
                self._run_file(job, text)
        except Exception as exc:
            if self._current(job):
                job.error = str(exc)
                job.result = 1
                _speech_event("speech_error", message=str(exc))
        finally:
            job.done.set()

    def start(self, text):
        text = text.strip()
        if not text:
            return
        self.stop()
        job = _SpeechJob()
        with self._state_lock:
            self._job = job
        job.thread = threading.Thread(
            target=self._run, args=(job, text), name="tts", daemon=True
        )
        job.thread.start()

    def poll(self):
        with self._state_lock:
            job = self._job
            if job is None or not job.done.is_set():
                return None
            self._job = None
            return 1 if job.result is None else job.result

    def stop(self):
        with self._state_lock:
            job, self._job = self._job, None
            proc = None if job is None else job.process
        if proc is not None and proc.poll() is None:
            _terminate_process_group(proc)


class SpeechOutput:
    def __init__(self, command):
        self.command = shlex.split(command)
        if not self.command:
            raise ValueError('PTT_SPEAK_COMMAND must not be empty')
        self.process = None

    @property
    def active(self):
        return self.process is not None and self.process.poll() is None

    @property
    def voice_level(self):
        return 1.0 if self.active else 0.0

    def start(self, text):
        self.stop()
        self.process = subprocess.Popen(
            [*self.command, text], stdin=subprocess.DEVNULL,
            start_new_session=True)

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        if code is not None:
            self.process = None
        return code

    def stop(self):
        proc, self.process = self.process, None
        if proc is None:
            return
        # Own process group includes speak.py and its playback child. Never kill
        # unrelated playback processes by name.
        _terminate_process_group(proc)


def change_volume(direction):
    """One 5 percentage point step of digital Playback; analog/input untouched."""
    subprocess.run([
        '/usr/bin/amixer', '-q', '-c', 'wm8960soundcard',
        'sset', 'Playback', '5%+' if direction > 0 else '5%-'],
        check=True, timeout=2, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
