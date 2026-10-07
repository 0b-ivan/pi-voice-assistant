#!/opt/pi-voice-assistant/.venv/bin/python

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from runtime_metrics import phase

from voice_effects import build_playback_command, build_render_command, resolve_voice_profile

DEFAULT_MODEL = "/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx"
DEFAULT_SERVITOR_MODEL = (
    "/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx"
)
DEFAULT_AUDIO_DEVICE = "plughw:CARD=wm8960soundcard,DEV=0"
DEFAULT_PIPER_PYTHON = "/opt/pi-voice-assistant/.venv/bin/python"


def _piper_command(piper_python, model, wav_file, text, profile):
    command = [
        piper_python,
        "-m",
        "piper",
        "-m",
        model,
    ]
    if profile == "servitor":
        command.extend([
            "-s",
            os.environ.get("TTS_PIPER_SPEAKER_ID", "4"),
            "--length-scale",
            os.environ.get("TTS_PIPER_LENGTH_SCALE", "1.10"),
            "--noise-scale",
            os.environ.get("TTS_PIPER_NOISE_SCALE", "0.30"),
            "--noise-w-scale",
            os.environ.get("TTS_PIPER_NOISE_W_SCALE", "0.25"),
            "--sentence-silence",
            os.environ.get("TTS_PIPER_SENTENCE_SILENCE", "0.32"),
        ])
    command.extend(["-f", wav_file])
    return command


def speak(text: str) -> None:
    text = text.strip()
    if not text:
        return

    profile = resolve_voice_profile()
    if profile == "servitor":
        model = os.environ.get("TTS_SERVITOR_MODEL", DEFAULT_SERVITOR_MODEL)
    else:
        model = os.environ.get("PIPER_MODEL", DEFAULT_MODEL)
    audio_device = os.environ.get("TTS_AUDIO_DEVICE", DEFAULT_AUDIO_DEVICE)
    piper_python = os.environ.get("PIPER_PYTHON", DEFAULT_PIPER_PYTHON)

    fd, wav_file = tempfile.mkstemp(prefix="pi-tts-", suffix=".wav")
    os.close(fd)

    rendered_file = None
    try:
        with phase('tts', 'worker_total'):
            if os.environ.get('PTT_MEMORY_MODE') == 'isolated':
                environment = dict(os.environ, VOICE_WORKER_STARTED_AT=str(time.monotonic()))
                subprocess.run(
                    [piper_python, str(Path(__file__).with_name('piper_worker.py')),
                     model, wav_file, profile],
                    input=text, text=True, check=True, env=environment,
                )
            else:
                subprocess.run(
                    _piper_command(piper_python, model, wav_file, text, profile),
                    input=text, text=True, check=True,
                )
        if profile == 'servitor' and os.environ.get('TTS_DSP_MODE') == 'buffered':
            fd, rendered_file = tempfile.mkstemp(prefix='pi-dsp-', suffix='.wav')
            os.close(fd)
            with phase('tts', 'dsp_render'):
                subprocess.run(build_render_command(wav_file, rendered_file),
                               check=True, timeout=120)
            playback = ['/usr/bin/aplay', '-q', '-D', audio_device,
                        '-B', '500000', rendered_file]
        else:
            playback = build_playback_command(wav_file, audio_device, profile=profile)
        with phase('tts', 'playback'):
            subprocess.run(playback, check=True)

    finally:
        for target in (wav_file, rendered_file):
            if target:
                try:
                    os.remove(target)
                except FileNotFoundError:
                    pass


def main() -> int:
    if len(sys.argv) < 2:
        print(f'Usage: {sys.argv[0]} "Text zum Sprechen"', file=sys.stderr)
        return 2

    speak(" ".join(sys.argv[1:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
