#!/usr/bin/env python3
"""PiTFT boot/status display for the Pi Voice Assistant."""

import os
from pathlib import Path
import shutil
import subprocess
import time


WIDTH = 240
HEIGHT = 240
ENV_FILE = Path("/etc/pi-voice-assistant.env")


def load_env():
    values = {}

    if not ENV_FILE.exists():
        return values

    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")

    return values


def font(size):
    from PIL import ImageFont

    path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def spi_ok():
    path = "/dev/spidev0.0"
    return os.path.exists(path) and os.access(path, os.R_OK | os.W_OK)


def audio_ok():
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        return False

    return "wm8960soundcard" in cards.lower()


def network_ok():
    ip = shutil.which("ip")

    if not ip:
        return False

    try:
        result = subprocess.run(
            [ip, "-4", "-brief", "address", "show", "up"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    for line in result.stdout.splitlines():
        if line.startswith("lo "):
            continue

        if "/" in line:
            return True

    return False


def service_ok():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "pi-ptt.service"],
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


def models_ok():
    env = load_env()

    vosk_model = Path(
        env.get(
            "VOSK_MODEL_PATH",
            "/opt/pi-voice-assistant/models/vosk-model-small-de-0.15",
        )
    )

    profile = env.get("TTS_VOICE_PROFILE", "normal").strip().lower()
    if profile == "servitor":
        tts_model = Path(
            env.get(
                "TTS_SERVITOR_MODEL",
                "/opt/pi-voice-assistant/tts/"
                "de_DE-thorsten_emotional-medium.onnx",
            )
        )
    else:
        tts_model = Path(
            env.get(
                "PIPER_MODEL",
                "/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx",
            )
        )

    return vosk_model.exists(), tts_model.exists()


def is_ready(states):
    """Network is informational; local Vosk operation must remain READY offline."""
    return (
        states["spi"]
        and states["audio"]
        and states["vosk"]
        and states["tts"]
        and states["voice"]
    )


def footer_status(states):
    if is_ready(states):
        return "SYSTEM READY", (0, 255, 100)

    return "BOOTING ...", (255, 180, 0)


def render(display, states):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(image)

    title_font = font(21)
    row_font = font(17)
    small_font = font(13)

    draw.text(
        (12, 8),
        "PI ASSISTANT",
        font=title_font,
        fill="white",
    )

    draw.line(
        (12, 37, 228, 37),
        fill=(90, 90, 90),
        width=1,
    )

    rows = [
        ("SPI", states["spi"]),
        ("AUDIO", states["audio"]),
        ("NETWORK", states["network"]),
        ("VOSK", states["vosk"]),
        ("TTS", states["tts"]),
        ("VOICE", states["voice"]),
    ]

    y = 48

    for label, ok in rows:
        state = "OK" if ok else "WAIT"
        color = (0, 220, 90) if ok else (255, 180, 0)

        draw.text(
            (12, y),
            f"{label:<8} ....",
            font=row_font,
            fill=(190, 190, 190),
        )

        draw.text(
            (182, y),
            state,
            font=row_font,
            fill=color,
        )

        y += 25

    draw.line(
        (12, 202, 228, 202),
        fill=(90, 90, 90),
        width=1,
    )

    footer, footer_color = footer_status(states)
    draw.text(
        (12, 211),
        footer,
        font=small_font,
        fill=footer_color,
    )

    display.image(image, 180)


def main():
    import board
    import digitalio
    from adafruit_rgb_display import st7789

    spi = board.SPI()

    cs = digitalio.DigitalInOut(board.CE0)
    dc = digitalio.DigitalInOut(board.D25)

    backlight = digitalio.DigitalInOut(board.D22)
    backlight.switch_to_output(value=True)

    display = st7789.ST7789(
        spi,
        cs=cs,
        dc=dc,
        rst=None,
        baudrate=24_000_000,
        width=WIDTH,
        height=HEIGHT,
        x_offset=0,
        y_offset=80,
    )

    previous = None

    while True:
        vosk, tts = models_ok()

        states = {
            "spi": spi_ok(),
            "audio": audio_ok(),
            "network": network_ok(),
            "vosk": vosk,
            "tts": tts,
            "voice": service_ok(),
        }

        if states != previous:
            print(states, flush=True)
            render(display, states)
            previous = states

        time.sleep(0.5)


if __name__ == "__main__":
    main()
