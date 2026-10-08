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

from alarms import ALARMS
from netprobe import ServerProbe, server_state  # noqa: F401 (server_state re-exported)
from power import Battery, BATTERY_SAMPLE_SECONDS, throttled_flags


DEVICE_NAME = os.environ.get('PI_DISPLAY_NAME', 'PROXIMUS')
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
    "wake_timeout": "BEREIT",
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
VOLUME_SHOW_SECONDS = 2.5
MENU_LABELS = (
    ('info', 'Systeminfo'),
    ('server', 'Server nutzen'),
    ('llm', 'Sprachkern'),
    ('wake', 'Aktivierungswort'),
    ('lore', 'Lore-Stufe'),
    ('wlan', 'WLAN'),
    ('alarms', 'Alarme'),
    ('led', 'Status-LED'),
    ('screen', 'Display aus'),
    ('status', 'Status ansagen'),
    ('close', 'Schließen'),
)
DEPLOYED_FILE = Path('/opt/pi-voice-assistant/src/DEPLOYED')
WAKE_WORD_LABELS = {'hey_jarvis': 'Hey Jarvis', 'hey_servitor': 'Hey Servitor'}
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
    if value.get('last_llm') in ('openrouter', 'offline', 'intent'):
        status['last_llm'] = value['last_llm']
    latency = value.get('last_latency_ms')
    if isinstance(latency, int) and not isinstance(latency, bool) and 0 <= latency < 600_000:
        status['last_latency_ms'] = latency
    volume, volume_at = value.get('volume'), value.get('volume_at')
    if (isinstance(volume, int) and not isinstance(volume, bool) and 0 <= volume <= 100
            and isinstance(volume_at, (int, float)) and not isinstance(volume_at, bool)):
        status['volume'] = volume
        status['volume_at'] = float(volume_at)
        if value.get('volume_limit') in ('min', 'max'):
            status['volume_limit'] = value['volume_limit']
    index = value.get('menu_index')
    if (isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(MENU_LABELS)
            and value.get('menu_page') in ('list', 'info')):
        status['menu_index'] = index
        status['menu_page'] = value['menu_page']
    for key, allowed in (('opt_server', ('on', 'off', 'none')), ('opt_led', ('on', 'off')),
                         ('opt_wake', ('on', 'off', 'none')),
                         ('opt_lore', ('off', 'light', 'full')),
                         ('opt_wlan', ('on', 'off')), ('opt_alarms', ('on', 'off')),
                         ('opt_llm', ('auto', 'local')),
                         ('alarm', tuple(ALARMS)),
                         ('wake_word', tuple(WAKE_WORD_LABELS)),
                         ('screen', ('on', 'off'))):
        if value.get(key) in allowed:
            status[key] = value[key]
    return status


def volume_overlay(status, now=None):
    """(percent, limit) while a volume change is recent, else None."""
    if 'volume' not in status:
        return None
    now = time.time() if now is None else now
    if not 0 <= now - status['volume_at'] < VOLUME_SHOW_SECONDS:
        return None
    return status['volume'], status.get('volume_limit')


def last_answer_text(status):
    """'Zuletzt 1,5 s · Server' — where the answer came from and how fast."""
    latency = status.get('last_latency_ms')
    if latency is None:
        return None
    if status.get('last_route') == 'pi':
        source = 'Pi lokal'
    elif status.get('last_llm') == 'offline':
        source = 'Offline-LLM'
    elif status.get('last_llm') == 'intent':
        source = 'direkt'
    else:
        source = 'Server'
    seconds = f'{latency / 1000:.1f}'.replace('.', ',')
    return f'Zuletzt {seconds} s · {source}'


def power_line(battery, throttled):
    """Idle detail line: power warnings first, then the battery state."""
    if throttled is not None:
        if throttled & 0x1:
            return 'UNTERSPANNUNG!', (255, 70, 70)
        if throttled & 0x6:
            return 'CPU gedrosselt', (255, 180, 0)
        if throttled & 0x10000:
            return 'Unterspannung seit Start', (255, 180, 0)
    if not battery:
        return None
    volts = f"{battery['mv'] / 1000:.2f}".replace('.', ',')
    if battery['charging']:
        state = 'lädt'
    elif battery['plugged']:
        state = 'Netz · voll'
    else:
        state = 'Akkubetrieb'
    color = (255, 90, 90) if battery['percent'] <= 15 and not battery['plugged'] else (145, 155, 165)
    return f"Akku {battery['percent']} % · {volts} V · {state}", color


def battery_view(battery):
    """The battery values as drawn (percent, 10 mV steps, state)."""
    if not battery:
        return None
    return (battery['percent'], round(battery['mv'] / 10), battery['plugged'],
            battery['charging'])


def battery_color(battery):
    if battery['charging']:
        return (80, 210, 235)
    if battery['percent'] <= 15:
        return (255, 70, 70)
    if battery['percent'] <= 40:
        return (255, 180, 0)
    return (120, 220, 160)


def draw_battery(draw, x_right, y, battery):
    """Small battery gauge plus percentage, right-aligned at x_right."""
    color = battery_color(battery)
    text = f"{battery['percent']}%"
    text_width = draw.textlength(text, font=font(13))
    draw.text((x_right - text_width, y), text, font=font(13), fill=color)
    right = x_right - text_width - 5
    left = right - 20
    top = y + 3
    draw.rectangle((left, top, right - 2, top + 10), outline=color, width=1)
    draw.rectangle((right - 1, top + 3, right, top + 7), fill=color)
    fill_width = round(15 * max(0, min(100, battery['percent'])) / 100)
    if fill_width:
        draw.rectangle((left + 2, top + 2, left + 2 + fill_width, top + 8), fill=color)
    if battery['charging']:
        cx = (left + right) // 2 - 1
        draw.polygon(((cx + 2, top - 1), (cx - 3, top + 6), (cx, top + 6),
                      (cx - 2, top + 12), (cx + 4, top + 4), (cx + 1, top + 4)),
                     fill=(255, 255, 255))
    return left


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

    draw.text((12, 8), DEVICE_NAME, font=title_font, fill="white")
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


ENVELOPE_FILE = Path(os.environ.get('PI_DISPLAY_ENVELOPE_FILE', '/run/pi-ptt/speech-envelope.json'))
SKULL_STATES = ('BEREIT', 'ZUHÖREN', 'VERSTEHEN', 'ERKENNEN', 'DENKEN', 'SYNTHESE',
                'RENDERN', 'AUSGABE', 'SPRECHEN')
THINKING_STATES = ('VERSTEHEN', 'ERKENNEN', 'DENKEN', 'SYNTHESE', 'RENDERN')
# Same meaning as the SHIM LED (ptt.LED_*): ready, local, listening, processing, speaking.
LED_LIKE = dict(ready=(0, 200, 80), local=(255, 170, 0), listen=(255, 30, 30),
                server=(0, 170, 255), speak=(255, 110, 0))
RAIN_LEFT = (2, 14, 26, 38)
RAIN_RIGHT = (191, 203, 215, 227)


def status_light(state, info):
    """The colour the SHIM LED shows in this state (for the skull's lens and the rain)."""
    if state == 'ZUHÖREN':
        return LED_LIKE['listen']
    if state in ('AUSGABE', 'SPRECHEN'):
        return LED_LIKE['speak']
    if state in THINKING_STATES:
        return LED_LIKE['server'] if info.get('route') == 'server' else LED_LIKE['local']
    return LED_LIKE['ready'] if info.get('server') in ('ok', None) else LED_LIKE['local']


_RAIN = None


def rain():
    global _RAIN
    if _RAIN is None:
        from skull import Rain
        _RAIN = Rain(RAIN_LEFT + RAIN_RIGHT, top=43, rows=12)
    return _RAIN


def read_envelope(path=None):
    """Loudness curve of the reply being played (written by ptt.py)."""
    try:
        value = json.loads((ENVELOPE_FILE if path is None else Path(path)).read_text())
        levels = value['levels']
        if (isinstance(value['start'], (int, float)) and isinstance(value['step'], (int, float))
                and value['step'] > 0 and isinstance(levels, list)
                and all(isinstance(v, int) and 0 <= v <= 100 for v in levels)):
            return dict(start=float(value['start']), step=float(value['step']), levels=levels)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _draw_header(draw, info):
    draw.text((12, 12), DEVICE_NAME, font=font(17), fill='white')
    right_edge = 228
    if info.get('battery'):
        right_edge = draw_battery(draw, 228, 14, info['battery']) - 8
    temp = info.get('temp_c')
    if temp is not None:
        _right(draw, right_edge, 15, f'{temp}°C', 12,
               (255, 120, 90) if temp >= 70 else (145, 155, 165))
    draw.line((12, 40, 228, 40), fill=(65, 65, 65))


def render_skull(display, skull, state, network, level, info=None, frame=0, details=None):
    """The servo skull: red eye glowing with ``level``, the other eye in the
    status-LED colour, litany streams ("thoughts") falling on both sides."""
    from PIL import Image, ImageDraw
    info = info or {}
    image = Image.new('RGB', (WIDTH, HEIGHT), 'black')
    draw = ImageDraw.Draw(image)
    _draw_header(draw, info)
    light = status_light(state, info)
    if state in THINKING_STATES:
        mode, gain = 'think', 1.0
    elif state in ('AUSGABE', 'SPRECHEN'):
        mode, gain = 'speak', 0.45 + 0.55 * min(1.0, max(0.0, (level - 0.55) / 0.45))
    else:
        mode, gain = 'idle', 0.55
    rain().draw(image, frame, mode, light, font(10), gain)
    image.paste(skull.frame(level, light), ((WIDTH - skull.base.width) // 2, 42))
    if state not in ('BEREIT', 'AUSGABE', 'SPRECHEN'):
        description = (details or STATE_DETAILS.get(state, ('', 'gear', 0)))[0]
        text = f'{state} · {description}' if description else state
        color = VOICE_COLORS.get(state, light)
    elif state == 'BEREIT':
        if info.get('alarm'):
            text, color = ALARMS[info['alarm']][1], (255, 70, 70)
        elif info.get('last'):
            text, color = info['last'], (145, 155, 165)
        elif info.get('wake'):
            text, color = f"„{info['wake']}“ oder Taste", (145, 155, 165)
        else:
            text, color = 'Zum Sprechen halten', (145, 155, 165)
    else:
        text, color = 'AUSGABE', VOICE_COLORS['AUSGABE']
    width = draw.textlength(text, font=font(11))
    draw.text(((WIDTH - width) / 2, 181), text, font=font(11), fill=color)
    _draw_footer(draw, network, info)
    display.image(image, 180)


def render_voice(display, state, network, details=None, tick=0, elapsed=0, info=None):
    """info: route, server ('ok'/'down'/'off'), temp_c, wifi_dbm, clock, last."""
    from PIL import Image, ImageDraw
    info = info or {}
    image = Image.new('RGB', (WIDTH, HEIGHT), 'black')
    draw = ImageDraw.Draw(image)
    description, icon, step = details or STATE_DETAILS.get(state, ('', 'gear', 0))
    color = VOICE_COLORS.get(state, (220, 220, 220))
    idle = state in ('BEREIT', 'FEHLER')
    _draw_header(draw, info)
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
    volume = info.get('volume')
    if volume is not None:
        # Volume feedback replaces the lower lines for a moment.
        percent, limit = volume
        draw.text((12, 139), 'LAUTSTÄRKE', font=font(14), fill=(215, 220, 225))
        label = {'max': 'MAX', 'min': 'MIN'}.get(limit, f'{percent} %')
        _right(draw, 228, 139, label, 14, (80, 210, 235))
        draw.rectangle((12, 164, 228, 178), outline=(80, 210, 235), width=1)
        width = round(212 * percent / 100)
        if width:
            draw.rectangle((14, 166, 14 + width, 176), fill=(80, 210, 235))
        _draw_footer(draw, network, info)
        display.image(image, 180)
        return
    if state == 'BEREIT' and info.get('wake'):
        description = f"„{info['wake']}“ oder Taste"
    draw.text((12, 139), description, font=font(14), fill=(215,220,225))
    if not idle:
        draw.text((12, 165), f'Seit {max(0, int(elapsed))} s', font=font(12), fill=(145,155,165))
    elif info.get('last'):
        draw.text((12, 161), info['last'], font=font(12), fill=(145,155,165))
    if idle:
        power = power_line(info.get('battery'), info.get('throttled'))
        if info.get('alarm'):
            power = (ALARMS[info['alarm']][1], (255, 70, 70))
        if power:
            draw.text((12, 179), power[0], font=font(11), fill=power[1])
    if step:
        for i in range(1,6):
            xx = 160+(i-1)*14
            draw.ellipse((xx,170,xx+6,176), fill=color if i<=step else (45,45,45))
    _draw_footer(draw, network, info)
    display.image(image, 180)


def _menu_value(item, status):
    if item == 'server':
        return {'on': 'AN', 'off': 'AUS', 'none': '—'}.get(status.get('opt_server'), '')
    if item == 'led':
        return {'on': 'AN', 'off': 'AUS'}.get(status.get('opt_led'), '')
    if item == 'llm':
        return {'auto': 'AUTO', 'local': 'LOKAL'}.get(status.get('opt_llm'), '')
    if item in ('wlan', 'alarms'):
        return {'on': 'AN', 'off': 'AUS'}.get(status.get(f'opt_{item}'), '')
    if item == 'lore':
        return {'off': 'AUS', 'light': 'DEZENT', 'full': 'VOLL'}.get(status.get('opt_lore'), '')
    if item == 'wake':
        return {'on': 'AN', 'off': 'AUS', 'none': '—'}.get(status.get('opt_wake'), '')
    return ''


def render_menu(display, status, info=None):
    """Menu list: PiTFT buttons move, SHIM E confirms, SHIM B closes."""
    from PIL import Image, ImageDraw
    info = info or {}
    image = Image.new('RGB', (WIDTH, HEIGHT), 'black')
    draw = ImageDraw.Draw(image)
    accent = (150, 120, 255)
    draw.text((12, 12), 'MENÜ', font=font(17), fill='white')
    if info.get('battery'):
        draw_battery(draw, 228, 14, info['battery'])
    draw.line((12, 40, 228, 40), fill=(65, 65, 65))
    selected = status.get('menu_index', 0)
    visible = 7  # scroll so the selection stays on screen
    first = max(0, min(selected - 3, len(MENU_LABELS) - visible))
    for row, (item, label) in enumerate(MENU_LABELS):
        if not first <= row < first + visible:
            continue
        y = 45 + (row - first) * 21
        if row == selected:
            draw.rectangle((10, y - 1, 230, y + 18), fill=(40, 32, 70))
            draw.text((14, y), '›', font=font(15), fill=accent)
        color = accent if row == selected else (215, 220, 225)
        draw.text((28, y), label, font=font(15), fill=color)
        value = _menu_value(item, status)
        if value:
            value_color = (120, 220, 160) if value == 'AN' else (170, 170, 170)
            _right(draw, 226, y + 1, value, 13, value_color)
    draw.line((12, 197, 228, 197), fill=(65, 65, 65))
    draw.text((12, 209), '▲▼ wählen · E: OK · B: zurück', font=font(11), fill=(145, 155, 165))
    display.image(image, 180)


def uptime_text(path='/proc/uptime'):
    try:
        seconds = int(float(Path(path).read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None
    days, rest = divmod(seconds, 86400)
    hours, minutes = rest // 3600, rest % 3600 // 60
    return f'{days} d {hours} h' if days else f'{hours} h {minutes} min'


def mem_available_mb(path='/proc/meminfo'):
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def ipv4_address():
    ip = shutil.which('ip')
    if not ip:
        return None
    try:
        result = subprocess.run([ip, '-4', '-brief', 'address', 'show', 'up'],
                                capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] != 'lo' and len(fields) >= 3:
            return fields[2].split('/')[0]
    return None


def deployed_version(path=None):
    try:
        return (DEPLOYED_FILE if path is None else Path(path)).read_text().split()[0][:7]
    except (OSError, IndexError):
        return None


def system_info_rows(env=None, battery=None):
    env = load_env() if env is None else env
    urls = [u.strip() for u in env.get('ASSISTANT_BASE_URL', '').split(',') if u.strip()]
    server = None
    if urls:
        import urllib.parse
        server = urllib.parse.urlsplit(urls[0]).hostname
    mem = mem_available_mb()
    rows = [
        ('IP', ipv4_address()),
        ('Laufzeit', uptime_text()),
        ('RAM frei', None if mem is None else f'{mem} MB'),
        ('Server', server or 'keiner'),
        ('Version', deployed_version()),
    ]
    if battery:
        volts = f"{battery['mv'] / 1000:.2f}".replace('.', ',')
        rows.append(('Akku', f"{battery['percent']} % · {volts} V"))
    return [(label, value) for label, value in rows if value]


def render_info(display, rows):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (WIDTH, HEIGHT), 'black')
    draw = ImageDraw.Draw(image)
    draw.text((12, 12), 'SYSTEMINFO', font=font(17), fill='white')
    draw.line((12, 40, 228, 40), fill=(65, 65, 65))
    for row, (label, value) in enumerate(rows[:6]):
        y = 50 + row * 24
        draw.text((12, y), label, font=font(12), fill=(135, 145, 150))
        _right(draw, 228, y, value, 13, (215, 220, 225))
    draw.line((12, 197, 228, 197), fill=(65, 65, 65))
    draw.text((12, 209), 'E oder ▲▼: zurück', font=font(11), fill=(145, 155, 165))
    display.image(image, 180)


def _draw_footer(draw, network, info):
    draw.line((12,197,228,197), fill=(65,65,65))
    server_text, server_color = SERVER_FOOTER.get(info.get('server'), ('VOICE LIVE', (170,170,170)))
    draw.text((12, 209), server_text, font=font(13), fill=server_color)
    if info.get('wlan') == 'off':
        link, link_color = 'WLAN AUS', (150, 150, 150)
    elif not network:
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


class PartialDisplay:
    """Transfer only changed pixels; avoid converting a full frame each tick."""
    def __init__(self, hardware):
        self.hardware = hardware
        self.previous = None

    BANDS = 3  # vertical bands: the two rain strips change without the middle

    def image(self, image, rotation=0):
        from PIL import ImageChops
        oriented = image.rotate(rotation, expand=True) if rotation else image
        if self.previous is None or self.previous.size != oriented.size:
            boxes = [(0, 0, oriented.width, oriented.height)]
        else:
            diff = ImageChops.difference(oriented, self.previous)
            step = oriented.width // self.BANDS
            boxes = []
            for band in range(self.BANDS):
                left = band * step
                right = oriented.width if band == self.BANDS - 1 else left + step
                box = diff.crop((left, 0, right, oriented.height)).getbbox()
                if box is not None:
                    boxes.append((left + box[0], box[1], left + box[2], box[3]))
        for box in boxes:
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
    screen_lit = True
    try:
        from skull import Skull, idle_level, speaking_level
        skull = Skull()
    except Exception:  # never let the artwork take the status display down
        skull = None
    envelope, envelope_mtime = None, None
    info_rows, next_info = None, 0.0
    states = None
    server = ServerProbe().start()
    battery_monitor = Battery()
    battery = None
    throttled = None
    next_battery = 0.0
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
        if now >= next_battery:
            # Temperature and WLAN jitter by one unit; sampling them with the
            # battery (5 s) instead of every 2 s saves most idle redraws.
            temp, wifi = cpu_temp_c(), wifi_dbm()
            battery = battery_monitor.read()
            throttled = throttled_flags()
            next_battery = now + BATTERY_SAMPLE_SECONDS

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

        status = read_status()
        lit = status.get('screen') != 'off'
        if lit != screen_lit:
            backlight.value = lit
            screen_lit = lit
        if status.get('menu_index') is not None and states is not None and is_ready(states):
            if status['menu_page'] == 'info':
                if info_rows is None or now >= next_info:
                    info_rows, next_info = system_info_rows(battery=battery), now + 2.0
                screen = ('info', tuple(info_rows))
                if screen != previous_screen:
                    render_info(display, info_rows)
                    previous_screen = screen
            else:
                screen = ('menu', tuple(sorted(status.items())), battery_view(battery))
                if screen != previous_screen:
                    render_menu(display, status, dict(battery=battery))
                    previous_screen = screen
        elif states is None or not is_ready(states) or voice_state is None:
            screen = ("boot", tuple(sorted((states or {}).items())))
            if screen != previous_screen and states is not None:
                render_boot(display, states)
                previous_screen = screen
        else:
            current = screen_details(voice_state, current_event, read_progress())
            shown, description, icon, step, started = current
            tick = int(now / ANIMATION_INTERVAL_SECONDS) if shown not in ('BEREIT', 'FEHLER') else 0
            elapsed = max(0, time.time() - started)
            # "Server nutzen: AUS" in the menu means Pi-only until switched back.
            server_shown = 'off' if status.get('opt_server') == 'off' else server.state
            info = dict(route=status.get('route'), server=server_shown, temp_c=temp,
                        wifi_dbm=wifi, clock=time.strftime('%H:%M'),
                        last=last_answer_text(status), throttled=throttled,
                        battery=battery, volume=volume_overlay(status),
                        alarm=status.get('alarm'), wlan=status.get('opt_wlan'),
                        wake=(WAKE_WORD_LABELS.get(status.get('wake_word'))
                              if status.get('opt_wake') == 'on' else None))
            # Redraw only when something visible changes: the averaged battery
            # voltage moves by a few mV on almost every sample.
            shown_info = dict(info, battery=battery_view(battery))
            if skull is not None and shown in SKULL_STATES and info.get('volume') is None:
                fps = 4 if shown in ('BEREIT', 'ZUHÖREN') else 10
                frame = int(now * fps)
                if shown not in ('AUSGABE', 'SPRECHEN'):
                    # Breathing is slow; the rain sets the frame rate.
                    level = idle_level(frame / fps)
                else:
                    try:
                        mtime = ENVELOPE_FILE.stat().st_mtime
                    except OSError:
                        mtime = None
                    if mtime != envelope_mtime:
                        envelope, envelope_mtime = read_envelope(), mtime
                    level = speaking_level(time.time(), envelope)
                screen = ('skull', shown, states['network'], frame,
                          tuple(sorted(shown_info.items())))
                if screen != previous_screen:
                    render_skull(display, skull, shown, states['network'], level, info,
                                 frame, (description, icon, step))
                    previous_screen = screen
            else:
                screen = ('voice', current, states['network'], tick,
                          tuple(sorted(shown_info.items())))
                if screen != previous_screen:
                    render_voice(display, shown, states['network'],
                                 (description, icon, step), tick, elapsed, info)
                    previous_screen = screen

        time.sleep(EVENT_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
