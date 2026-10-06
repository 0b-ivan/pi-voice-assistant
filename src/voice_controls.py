"""Nonblocking STT job, owned speech subprocess and WM8960 playback volume."""
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import wave

from voice_effects import build_playback_command, resolve_voice_profile


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


def _synthesize_voice(voice, text, audio, profile):
    """Write Piper audio, with a restrained command cadence for Servitor."""
    if profile != "servitor":
        voice.synthesize_wav(text, audio)
        return

    try:
        from piper.config import SynthesisConfig
    except ImportError as exc:
        raise RuntimeError("Piper SynthesisConfig unavailable") from exc

    speaker_id = int(os.environ.get("TTS_PIPER_SPEAKER_ID", "4"))
    num_speakers = getattr(getattr(voice, "config", None), "num_speakers", None)
    if num_speakers is not None and not 0 <= speaker_id < num_speakers:
        raise ValueError(
            f"TTS_PIPER_SPEAKER_ID={speaker_id} outside model speaker range 0..{num_speakers - 1}"
        )

    syn_config = SynthesisConfig(
        speaker_id=speaker_id,
        length_scale=_env_float("TTS_PIPER_LENGTH_SCALE", 1.10),
        noise_scale=_env_float("TTS_PIPER_NOISE_SCALE", 0.30),
        noise_w_scale=_env_float("TTS_PIPER_NOISE_W_SCALE", 0.25),
    )
    sentence_silence = _env_float("TTS_PIPER_SENTENCE_SILENCE", 0.32)

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
            audio.writeframes(bytes(silence_samples * chunk.sample_width * chunk.sample_channels))
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


class ResidentSpeechOutput:
    """Keep Piper loaded; synthesize off the control loop and own our playback."""
    def __init__(self, model, audio_device, runtime_dir, loader=None, popen=None,
                 profile=None):
        self.model = str(model)
        self.audio_device = audio_device
        self.runtime_dir = Path(runtime_dir)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.profile = resolve_voice_profile(profile)
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

    def _run(self, job, text):
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
        except Exception as exc:
            if self._current(job):
                job.error = str(exc)
                job.result = 1
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
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
