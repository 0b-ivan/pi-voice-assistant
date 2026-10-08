#!/usr/bin/env python3
"""PiTFT boot and live voice status display for the Pi Voice Assistant."""

import json
import math
from functools import lru_cache
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
    "llm_response": "SYNTHESE",
    "llm_discarded": "BEREIT",
    "cancelled": "BEREIT",
    "status": "SYNTHESE",
    "speech_started": "SYNTHESE",
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


PROGRESS_FILE = Path(os.environ.get('PI_DISPLAY_PROGRESS_FILE', '/run/pi-ptt/display-progress.json'))
STATUS_FILE = Path(os.environ.get('PI_DISPLAY_STATUS_FILE', '/run/pi-ptt/display-status.json'))
SERVER_PROBE_INTERVAL_SECONDS = 10.0
SERVER_PROBE_TIMEOUT_SECONDS = 0.5
ROUTE_LABELS = {'server': ('SERVER', (80, 210, 235)), 'pi': ('LOKAL', (255, 180, 0))}
SERVER_FOOTER = {
    'ok': ('CT107 OK', (120, 220, 160)),
    'down': ('CT107 AUS', (255, 180, 0)),
    'off': ('NUR PI', (150, 150, 150)),
}
ANIMATION_INTERVAL_SECONDS = 0.25
# state, description, icon, position in the five-step response sequence
PHASE_DETAILS = {
    ('tts', 'import'): ('STARTET', 'Sprachsystem laden', 'gear', 0),
    ('tts', 'model_load'): ('STARTET', 'Stimm-Modell laden', 'gear', 0),
    ('tts', 'warmup'): ('STARTET', 'Stimme vorbereiten', 'wave', 0),
    ('tts', 'worker_total'): ('SYNTHESE', 'Sprachausgabe starten', 'wave', 3),
    ('tts', 'synthesis'): ('SYNTHESE', 'Stimme erzeugen', 'wave', 3),
    ('tts', 'dsp_render'): ('RENDERN', 'Audioeffekte berechnen', 'sliders', 4),
    ('tts', 'playback'): ('AUSGABE', 'Audio abspielen', 'speaker', 5),
    ('stt', 'live_finalize'): ('ERKENNEN', 'Aufnahme auswerten', 'scan', 1),
    ('stt', 'recognition'): ('ERKENNEN', 'Sprache in Text', 'scan', 1),
}
STATE_DETAILS = {
    'BEREIT': ('Zum Sprechen halten', 'ready', 0),
    'ZUHÖREN': ('Sprache aufnehmen', 'mic', 0),
    'VERSTEHEN': ('Sprache in Text', 'scan', 1),
    'DENKEN': ('Antwort abwarten', 'gear', 2),
    'SYNTHESE': ('Stimme erzeugen', 'wave', 3),
    'SPRECHEN': ('Audio abspielen', 'speaker', 5),
    'FEHLER': ('Vorgang fehlgeschlagen', 'error', 0),
    'STARTET': ('Sprachsystem starten', 'gear', 0),
}
VOICE_COLORS.update(SYNTHESE=(80, 210, 235), RENDERN=(190, 140, 255),
                    AUSGABE=(255, 140, 0), ERKENNEN=(255, 200, 0))


def read_progress(path=None):
    try:
        value = json.loads((PROGRESS_FILE if path is None else Path(path)).read_text())
        if not isinstance(value, dict):
            return None
        if (value.get('stage'), value.get('metric')) not in PHASE_DETAILS:
            return None
        stamp = value.get('timestamp')
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def read_status(path=None):
    """Fixed identifiers/numbers from ptt.py; anything else is dropped."""
    try:
        value = json.loads((STATUS_FILE if path is None else Path(path)).read_text())
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    status = {}
    for key in ('route', 'last_route'):
        if value.get(key) in ('server', 'pi'):
            status[key] = value[key]
    if value.get('last_llm') in ('openrouter', 'offline'):
        status['last_llm'] = value['last_llm']
    latency = value.get('last_latency_ms')
    if isinstance(latency, int) and not isinstance(latency, bool) and 0 <= latency < 600_000:
        status['last_latency_ms'] = latency
    return status


def last_answer_text(status):
    """'Zuletzt 1,5 s · Server' — where the answer came from and how fast."""
    latency = status.get('last_latency_ms')
    if latency is None:
        return None
    if status.get('last_route') == 'pi':
        source = 'Pi lokal'
    elif status.get('last_llm') == 'offline':
        source = 'Offline-LLM'
    else:
        source = 'Server'
    seconds = f'{latency / 1000:.1f}'.replace('.', ',')
    return f'Zuletzt {seconds} s · {source}'


def server_state(env=None):
    """'off' without ASSISTANT_BASE_URL, else 'ok'/'down' from GET /health."""
    import urllib.request
    env = load_env() if env is None else env
    urls = [u.strip().rstrip('/') for u in env.get('ASSISTANT_BASE_URL', '').split(',')]
    urls = [u for u in urls if u]
    if not urls:
        return 'off'
    try:
        with urllib.request.urlopen(urls[0] + '/health',
                                    timeout=SERVER_PROBE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(256))
        return 'ok' if isinstance(payload, dict) and payload.get('ready') is True else 'down'
    except (OSError, ValueError):
        return 'down'


class ServerProbe:
    """Polls /health in a daemon thread so a slow server never stalls frames."""
    def __init__(self, interval=SERVER_PROBE_INTERVAL_SECONDS, probe=None):
        import threading
        self.interval = interval
        self.probe = probe or server_state
        self.state = None
        self._thread = threading.Thread(target=self._run, name='server-probe', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        while True:
            self.state = self.probe()
            time.sleep(self.interval)


def cpu_temp_c(path='/sys/class/thermal/thermal_zone0/temp'):
    try:
        return round(int(Path(path).read_text().strip()) / 1000)
    except (OSError, ValueError):
        return None


def wifi_dbm(path='/proc/net/wireless'):
    try:
        lines = Path(path).read_text().splitlines()[2:]
    except OSError:
        return None
    for line in lines:
        name, _, rest = line.partition(':')
        fields = rest.split()
        if name.strip() and len(fields) >= 3:
            try:
                return int(float(fields[2]))
            except ValueError:
                return None
    return None


def screen_details(state, event, progress):
    """A later controller event (especially cancel/error) supersedes old work."""
    stamp = event['timestamp'] if event else 0
    if (progress and state != 'FEHLER' and event
            and progress['timestamp'] >= stamp):
        state, description, icon, step = PHASE_DETAILS[(progress['stage'], progress['metric'])]
        return state, description, icon, step, progress['timestamp']
    description, icon, step = STATE_DETAILS.get(state, ('', 'gear', 0))
    if event and event['event'] == 'stt_loading':
        description = 'Spracherkennung laden'
    elif event and event['event'] == 'tts_loading':
        description = 'Stimm-Modell laden'
    return state, description, icon, step, stamp


def draw_activity_icon(draw, kind, center, color, tick):
    x, y = center
    angle = tick * math.tau / 32  # One quiet rotation every eight seconds.
    if kind == 'gear':
        points = []
        for i in range(64):
            radius = 17 if i % 8 in (0, 1, 6, 7) else 21
            a = angle + i * math.tau / 64
            points.append((x + math.cos(a)*radius, y + math.sin(a)*radius))
        draw.polygon(points, fill=color)
        draw.ellipse((x-9, y-9, x+9, y+9), fill='black')
        draw.ellipse((x-3, y-3, x+3, y+3), fill=color)
    elif kind in ('wave', 'sliders'):
        for i in range(5):
            xx = x-16+i*8
            height = 5 + int(12 * (1+math.sin(tick*.45+i*1.3))/2)
            draw.line((xx, y-17, xx, y+17), fill=(55,65,75), width=2)
            if kind == 'sliders':
                yy = y-12+height
                draw.rounded_rectangle((xx-3, yy-4, xx+3, yy+4), radius=2, fill=color)
            else:
                draw.line((xx, y-height, xx, y+height), fill=color, width=4)
    elif kind == 'speaker':
        draw.polygon([(x-18,y-6),(x-11,y-6),(x-3,y-14),(x-3,y+14),(x-11,y+6),(x-18,y+6)], fill=color)
        for radius in (12, 20):
            if radius == 12 or tick % 4 < 3:
                draw.arc((x-radius,y-radius,x+radius,y+radius), -55,55,fill=color,width=2)
    elif kind == 'mic':
        draw.rounded_rectangle((x-6,y-18,x+6,y+5),radius=6,outline=color,width=2)
        draw.arc((x-12,y-7,x+12,y+13),0,180,fill=color,width=2)
        draw.line((x,y+13,x,y+20),fill=color,width=2)
        draw.line((x-7,y+20,x+7,y+20),fill=color,width=2)
        draw.ellipse((x+15,y-17,x+19,y-13),fill=color if tick%4<2 else (65,35,35))
    elif kind == 'scan':
        draw.rounded_rectangle((x-15,y-19,x+15,y+19),radius=3,outline=color,width=2)
        for offset in (-10,-2,6):
            draw.line((x-8,y+offset,x+8,y+offset),fill=(100,100,100),width=2)
        yy = y-14+(tick%12)*2.5
        draw.line((x-18,yy,x+18,yy),fill=color,width=2)
    elif kind == 'error':
        draw.polygon([(x,y-20),(x-21,y+17),(x+21,y+17)],outline=color,width=2)
        draw.text((x-5,y-12),'!',font=font(24),fill=color)
    else:
        draw.ellipse((x-19,y-19,x+19,y+19),outline=color,width=2)
        draw.line((x-10,y,x-3,y+7,x+11,y-9),fill=color,width=3)


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


@lru_cache(maxsize=12)
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


def _right(draw, x_right, y, text, size, fill):
    width = draw.textlength(text, font=font(size))
    draw.text((x_right - width, y), text, font=font(size), fill=fill)


def render_voice(display, state, network, details=None, tick=0, elapsed=0, info=None):
    """info: route, server ('ok'/'down'/'off'), temp_c, wifi_dbm, clock, last."""
    from PIL import Image, ImageDraw
    info = info or {}
    image = Image.new('RGB', (WIDTH, HEIGHT), 'black')
    draw = ImageDraw.Draw(image)
    description, icon, step = details or STATE_DETAILS.get(state, ('', 'gear', 0))
    color = VOICE_COLORS.get(state, (220, 220, 220))
    idle = state in ('BEREIT', 'FEHLER')
    draw.text((12, 10), 'PI ASSISTANT', font=font(20), fill='white')
    temp = info.get('temp_c')
    if temp is not None:
        _right(draw, 228, 15, f'{temp}°C', 13,
               (255, 120, 90) if temp >= 70 else (145, 155, 165))
    draw.line((12, 40, 228, 40), fill=(65,65,65))
    # Where the work happens: during a turn as published by ptt.py, when idle
    # where the next turn will go (server reachable or local fallback).
    if idle:
        route = 'server' if info.get('server') == 'ok' else 'pi'
        draw.text((12, 53), 'NÄCHSTE ANFRAGE', font=font(11), fill=(135,145,150))
    else:
        route = info.get('route')
        draw.text((12, 53), 'AKTUELLER SCHRITT', font=font(11), fill=(135,145,150))
    if route in ROUTE_LABELS:
        label, label_color = ROUTE_LABELS[route]
        _right(draw, 228, 53, label, 11, label_color)
    draw_activity_icon(draw, icon, (34, 101), color, tick)
    draw.text((65, 87), state, font=font(22 if len(state)<10 else 19), fill=color)
    draw.text((12, 139), description, font=font(14), fill=(215,220,225))
    if not idle:
        draw.text((12, 165), f'Seit {max(0, int(elapsed))} s', font=font(12), fill=(145,155,165))
    elif info.get('last'):
        draw.text((12, 165), info['last'], font=font(12), fill=(145,155,165))
    if step:
        for i in range(1,6):
            xx = 160+(i-1)*14
            draw.ellipse((xx,170,xx+6,176), fill=color if i<=step else (45,45,45))
    draw.line((12,197,228,197), fill=(65,65,65))
    server_text, server_color = SERVER_FOOTER.get(info.get('server'), ('VOICE LIVE', (170,170,170)))
    draw.text((12, 209), server_text, font=font(13), fill=server_color)
    if not network:
        link, link_color = 'OFFLINE', (180, 180, 180)
    elif info.get('wifi_dbm') is not None:
        dbm = info['wifi_dbm']
        link = f'WLAN {dbm}'
        link_color = (120,220,160) if dbm >= -67 else (255,180,0) if dbm >= -78 else (255,90,90)
    else:
        link, link_color = 'NET OK', (120, 220, 160)
    draw.text((100, 209), link, font=font(13), fill=link_color)
    if info.get('clock'):
        _right(draw, 228, 209, info['clock'], 13, (170, 170, 170))
    display.image(image, 180)


class PartialDisplay:
    """Transfer only changed pixels; avoid converting a full frame each tick."""
    def __init__(self, hardware):
        self.hardware = hardware
        self.previous = None

    def image(self, image, rotation=0):
        from PIL import ImageChops
        oriented = image.rotate(rotation, expand=True) if rotation else image
        box = (0, 0, oriented.width, oriented.height)
        if self.previous is not None and self.previous.size == oriented.size:
            box = ImageChops.difference(oriented, self.previous).getbbox()
        if box is None:
            return
        self.hardware.image(oriented.crop(box), rotation=0, x=box[0], y=box[1])
        self.previous = oriented.copy()


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

    display = PartialDisplay(display)
    states = None
    server = ServerProbe().start()
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
            temp, wifi = cpu_temp_c(), wifi_dbm()

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

        if states is None or not is_ready(states) or voice_state is None:
            screen = ("boot", tuple(sorted((states or {}).items())))
            if screen != previous_screen and states is not None:
                render_boot(display, states)
                previous_screen = screen
        else:
            current = screen_details(voice_state, current_event, read_progress())
            shown, description, icon, step, started = current
            tick = int(now / ANIMATION_INTERVAL_SECONDS) if shown not in ('BEREIT', 'FEHLER') else 0
            elapsed = max(0, time.time() - started)
            status = read_status()
            info = dict(route=status.get('route'), server=server.state, temp_c=temp,
                        wifi_dbm=wifi, clock=time.strftime('%H:%M'),
                        last=last_answer_text(status))
            screen = ('voice', current, states['network'], tick, tuple(sorted(info.items())))
            if screen != previous_screen:
                render_voice(display, shown, states['network'],
                             (description, icon, step), tick, elapsed, info)
                previous_screen = screen

        time.sleep(EVENT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
