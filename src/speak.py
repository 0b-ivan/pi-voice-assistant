#!/opt/pi-voice-assistant/.venv/bin/python

import os
import subprocess
import sys
import tempfile

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

    fd, wav_file = tempfile.mkstemp(prefix="pi-tts-", suffix=".wav")
    os.close(fd)

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
                "--",
                text,
            ],
            check=True,
        )

        subprocess.run(
            ["aplay", "-q", "-D", audio_device, wav_file],
            check=True,
        )
    finally:
        try:
            os.remove(wav_file)
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
