#!/opt/pi-voice-assistant/.venv/bin/python

import os
import subprocess
import sys
import tempfile

from voice_effects import apply_voice_profile, resolve_voice_profile

DEFAULT_MODEL = "/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx"
DEFAULT_AUDIO_DEVICE = "plughw:CARD=wm8960soundcard,DEV=0"
DEFAULT_PIPER_PYTHON = "/opt/pi-voice-assistant/.venv/bin/python"


def speak(text: str) -> None:
    text = text.strip()
    if not text:
        return

    model = os.environ.get("PIPER_MODEL", DEFAULT_MODEL)
    audio_device = os.environ.get("TTS_AUDIO_DEVICE", DEFAULT_AUDIO_DEVICE)
    piper_python = os.environ.get("PIPER_PYTHON", DEFAULT_PIPER_PYTHON)
    profile = resolve_voice_profile()

    fd, wav_file = tempfile.mkstemp(prefix="pi-tts-", suffix=".wav")
    os.close(fd)
    effect_file = None

    try:
        subprocess.run(
            [
                piper_python,
                "-m",
                "piper",
                "-m",
                model,
                "-f",
                wav_file,
                text,
            ],
            check=True,
        )

        playback_file = wav_file
        if profile != "normal":
            effect_fd, effect_file = tempfile.mkstemp(
                prefix="pi-tts-effect-", suffix=".wav"
            )
            os.close(effect_fd)
            playback_file = str(
                apply_voice_profile(
                    wav_file,
                    effect_file,
                    profile=profile,
                    runner=subprocess.run,
                )
            )

        subprocess.run(
            ["aplay", "-q", "-D", audio_device, playback_file],
            check=True,
        )
    finally:
        for path in (effect_file, wav_file):
            if path is None:
                continue
            try:
                os.remove(path)
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
