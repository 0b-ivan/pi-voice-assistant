#!/usr/bin/env python3
"""PiTFT boot and live voice status display for the Pi Voice Assistant."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import time


WIDTH = 240
HEIGHT = 240
ENV_FILE = Path("/etc/pi-voice-assistant.env")
VOICE_EVENT_FILE = Path(
    os.environ.get("PI_DISPLAY_EVENT_FILE", "/run/pi-ptt/display-event.json")
)
PROBE_INTERVAL_SECONDS = 2.0
EVENT_INTERVAL_SECONDS = 0.1
ERROR_HOLD_SECONDS = 3.0

VOICE_EVENT_STATES = {
    "stt_loading": "STARTET",
    "stt_ready": "STARTET",
    "tts_loading": "STARTET",
    "tts_ready": "STARTET",
    "waiting_for_release": "BEREIT",
    "recording": "ZUHÖREN",
    "capture_ready": "VERSTEHEN",
    "processing": "VERSTEHEN",
    "stt_live_error": "VERSTEHEN",
    "busy": "VERSTEHEN",
    "transcript": "DENKEN",
    "transcript_discarded": "BEREIT",
    "llm_start": "DENKEN",
    "llm_response": "SPRECHEN",
    "llm_discarded": "BEREIT",
    "cancelled": "BEREIT",
    "status": "SPRECHEN",
    "speech_started": "SPRECHEN",
    "speech_finished": "BEREIT",
    "stt_error": "FEHLER",
    "llm_error": "FEHLER",
    "tts_error": "FEHLER",
    "speech_error": "FEHLER",
}

VOICE_COLORS = {
    "BEREIT": (0, 255, 100),
    "ZUHÖREN": (255, 80, 80),
    "VERSTEHEN": (255, 200, 0),
    "DENKEN": (255, 170, 0),
    "SPRECHEN": (255, 140, 0),
    "FEHLER": (255, 70, 70),
}


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


def collect_system_states():
    vosk, tts = models_ok()
    return {
        "spi": spi_ok(),
        "audio": audio_ok(),
        "network": network_ok(),
        "vosk": vosk,
        "tts": tts,
        "voice": service_ok(),
    }


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


def read_voice_event(path=None):
    path = VOICE_EVENT_FILE if path is None else Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    name = payload.get("event")
    timestamp = payload.get("timestamp")
    error_timestamp = payload.get("error_timestamp")
    if not isinstance(name, str) or not isinstance(timestamp, (int, float)):
        return None
    if error_timestamp is not None and not isinstance(error_timestamp, (int, float)):
        return None

    return {
        "event": name,
        "timestamp": float(timestamp),
        "error_timestamp": (
            None if error_timestamp is None else float(error_timestamp)
        ),
    }


def voice_state_for_event(name):
    return VOICE_EVENT_STATES.get(name)


def render_boot(display, states):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(image)

    title_font = font(21)
    row_font = font(17)
    small_font = font(13)

    draw.text((12, 8), "PI ASSISTANT", font=title_font, fill="white")
    draw.line((12, 37, 228, 37), fill=(90, 90, 90), width=1)

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
        draw.text((182, y), state, font=row_font, fill=color)
        y += 25

    draw.line((12, 202, 228, 202), fill=(90, 90, 90), width=1)
    footer, footer_color = footer_status(states)
    draw.text((12, 211), footer, font=small_font, fill=footer_color)
    display.image(image, 180)


def render_voice(display, state, network):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), "black")
    draw = ImageDraw.Draw(image)

    title_font = font(20)
    state_font = font(27 if len(state) <= 9 else 23)
    small_font = font(13)
    color = VOICE_COLORS.get(state, (220, 220, 220))

    draw.text((12, 10), "PI ASSISTANT", font=title_font, fill="white")
    draw.line((12, 40, 228, 40), fill=(90, 90, 90), width=1)

    box = draw.textbbox((0, 0), state, font=state_font)
    text_width = box[2] - box[0]
    draw.text(
        ((WIDTH - text_width) // 2, 101),
        state,
        font=state_font,
        fill=color,
    )

    draw.line((12, 197, 228, 197), fill=(90, 90, 90), width=1)
    draw.text((12, 209), "VOICE LIVE", font=small_font, fill=(170, 170, 170))
    draw.text(
        (143, 209),
        "NET OK" if network else "OFFLINE",
        font=small_font,
        fill=(120, 220, 160) if network else (180, 180, 180),
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

    states = None
    voice_state = None
    last_event = None
    error_until = None
    next_probe = 0.0
    previous_screen = None

    while True:
        now = time.monotonic()

        if states is None or now >= next_probe:
            states = collect_system_states()
            next_probe = now + PROBE_INTERVAL_SECONDS

        current_event = read_voice_event()
        if current_event is not None and current_event != last_event:
            last_event = current_event
            mapped = voice_state_for_event(current_event["event"])
            if mapped is not None and mapped != "FEHLER":
                voice_state = mapped

        if current_event is not None and current_event["error_timestamp"] is not None:
            remaining = (
                current_event["error_timestamp"]
                + ERROR_HOLD_SECONDS
                - time.time()
            )
            if remaining > 0:
                voice_state = "FEHLER"
                error_until = now + remaining
            elif voice_state == "FEHLER":
                error_until = now

        if (
            voice_state == "FEHLER"
            and error_until is not None
            and now >= error_until
            and states is not None
            and is_ready(states)
        ):
            mapped = (
                None
                if current_event is None
                else voice_state_for_event(current_event["event"])
            )
            voice_state = (
                mapped
                if mapped not in (None, "FEHLER", "STARTET")
                else "BEREIT"
            )
            error_until = None

        if states is None or not is_ready(states) or voice_state in (None, "STARTET"):
            screen = ("boot", tuple(sorted((states or {}).items())))
            if screen != previous_screen and states is not None:
                render_boot(display, states)
                previous_screen = screen
        else:
            screen = ("voice", voice_state, states["network"])
            if screen != previous_screen:
                render_voice(display, voice_state, states["network"])
                previous_screen = screen

        time.sleep(EVENT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
