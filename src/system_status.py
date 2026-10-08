"""Servitor status: snapshot of the Pi plus the spoken status text."""

import os
from pathlib import Path
import shutil


THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
MEMINFO_PATH = Path("/proc/meminfo")
LOADAVG_PATH = Path("/proc/loadavg")
UPTIME_PATH = Path("/proc/uptime")


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None


def _temperature_c(path=THERMAL_PATH):
    raw = _read_text(path)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value > 1000:
        value /= 1000
    if not -40 <= value <= 150:
        return None
    return round(value)


def _memory_free_percent(path=MEMINFO_PATH):
    raw = _read_text(path)
    if not raw:
        return None
    values = {}
    for line in raw.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        try:
            values[key] = int(rest.strip().split()[0])
        except (ValueError, IndexError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return max(0, min(100, round(available * 100 / total)))


def _system_load_percent(path=LOADAVG_PATH, cpu_count=os.cpu_count):
    raw = _read_text(path)
    if not raw:
        return None
    try:
        load_1m = float(raw.split()[0])
    except (ValueError, IndexError):
        return None
    cores = cpu_count() or 1
    if cores <= 0:
        cores = 1
    return max(0, min(999, round(load_1m * 100 / cores)))


def _disk_free_percent(path="/", disk_usage=shutil.disk_usage):
    try:
        usage = disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return max(0, min(100, round(usage.free * 100 / usage.total)))


def _de_number(value):
    """German cardinal number words for compact TTS telemetry (0..999)."""
    value = int(value)
    if not 0 <= value <= 999:
        return str(value)

    ones = {
        0: "null",
        1: "eins",
        2: "zwei",
        3: "drei",
        4: "vier",
        5: "fünf",
        6: "sechs",
        7: "sieben",
        8: "acht",
        9: "neun",
        10: "zehn",
        11: "elf",
        12: "zwölf",
        13: "dreizehn",
        14: "vierzehn",
        15: "fünfzehn",
        16: "sechzehn",
        17: "siebzehn",
        18: "achtzehn",
        19: "neunzehn",
    }
    tens = {
        20: "zwanzig",
        30: "dreißig",
        40: "vierzig",
        50: "fünfzig",
        60: "sechzig",
        70: "siebzig",
        80: "achtzig",
        90: "neunzig",
    }

    if value < 20:
        return ones[value]
    if value < 100:
        ten = (value // 10) * 10
        unit = value % 10
        if unit == 0:
            return tens[ten]
        unit_word = "ein" if unit == 1 else ones[unit]
        return f"{unit_word}und{tens[ten]}"

    hundreds, rest = divmod(value, 100)
    prefix = "einhundert" if hundreds == 1 else f"{ones[hundreds]}hundert"
    return prefix if rest == 0 else f"{prefix}{_de_number(rest)}"


def _duration_words(value, singular, plural, feminine=False):
    if value == 1:
        number = "eine" if feminine else "ein"
        return f"{number} {singular}"
    return f"{_de_number(value)} {plural}"


SNAPSHOT_FIELDS = {
    # name: (type, minimum, maximum)
    'temp_c': (int, -40, 150),
    'load_pct': (int, 0, 999),
    'mem_free_pct': (int, 0, 100),
    'disk_free_pct': (int, 0, 100),
    'uptime_s': (int, 0, 10 ** 9),
    'battery_pct': (int, 0, 100),
    'swap_used_pct': (int, 0, 100),
    'throttled': (int, 0, 0xFFFFFFFF),
}
SNAPSHOT_FLAGS = ('battery_charging', 'battery_plugged')
SNAPSHOT_STATES = {'server': ('ok', 'down', 'off'), 'llm': ('openrouter', 'offline'),
                   'lore': ('off', 'light', 'full'), 'wlan': ('on', 'off'),
                   'llm_mode': ('auto', 'local'), 'memory': ('on', 'off')}


def sanitize_snapshot(value):
    """Keep only known fields with plausible values (data may come from the
    network: the Pi sends its snapshot to the server with every turn)."""
    if not isinstance(value, dict):
        return {}
    clean = {}
    for name, (kind, low, high) in SNAPSHOT_FIELDS.items():
        item = value.get(name)
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        item = int(item)
        if low <= item <= high:
            clean[name] = item
    for name in SNAPSHOT_FLAGS:
        if isinstance(value.get(name), bool):
            clean[name] = value[name]
    for name, allowed in SNAPSHOT_STATES.items():
        if value.get(name) in allowed:
            clean[name] = value[name]
    return clean


def _swap_used_percent(path=MEMINFO_PATH):
    values = {}
    for line in (_read_text(path) or '').splitlines():
        key, _, rest = line.partition(':')
        try:
            values[key] = int(rest.split()[0])
        except (ValueError, IndexError):
            continue
    total = values.get('SwapTotal')
    if not total or values.get('SwapFree') is None:
        return None
    return max(0, min(100, round((total - values['SwapFree']) * 100 / total)))


def collect_snapshot(battery=None, throttled=None, server=None, lore=None, wlan=None,
                     llm_mode=None, extra=None,
                     thermal_path=THERMAL_PATH, meminfo_path=MEMINFO_PATH,
                     loadavg_path=LOADAVG_PATH, uptime_path=UPTIME_PATH,
                     disk_path="/", disk_usage=shutil.disk_usage, cpu_count=os.cpu_count):
    """Numbers only, readable without privileges; battery comes from power.Battery."""
    raw = dict(
        temp_c=_temperature_c(thermal_path),
        load_pct=_system_load_percent(loadavg_path, cpu_count=cpu_count),
        mem_free_pct=_memory_free_percent(meminfo_path),
        disk_free_pct=_disk_free_percent(disk_path, disk_usage=disk_usage),
        throttled=throttled,
        server=server,
        lore=lore,
        wlan=wlan,
        llm_mode=llm_mode,
        swap_used_pct=_swap_used_percent(meminfo_path),
    )
    text = _read_text(uptime_path)
    try:
        raw['uptime_s'] = int(float(text.split()[0])) if text else None
    except (ValueError, IndexError):
        pass
    raw.update(extra or {})
    if battery:
        raw.update(battery_pct=battery.get('percent'),
                   battery_charging=bool(battery.get('charging')),
                   battery_plugged=bool(battery.get('plugged')))
    return sanitize_snapshot({k: v for k, v in raw.items() if v is not None})


def _uptime_from_seconds(seconds):
    minutes = max(0, seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return (f"{_duration_words(days, 'Tag', 'Tage')} "
                f"{_duration_words(hours, 'Stunde', 'Stunden', feminine=True)}")
    if hours:
        return (f"{_duration_words(hours, 'Stunde', 'Stunden', feminine=True)} "
                f"{_duration_words(minutes, 'Minute', 'Minuten', feminine=True)}")
    return _duration_words(minutes, 'Minute', 'Minuten', feminine=True)


def battery_sentence(snapshot):
    percent = snapshot.get('battery_pct')
    if percent is None:
        return None
    if snapshot.get('battery_charging'):
        return f"Energiespeicher {percent} Prozent. Ladung aktiv."
    if snapshot.get('battery_plugged'):
        return f"Energiespeicher {percent} Prozent. Netzbetrieb."
    return f"Energiespeicher {percent} Prozent. Akkubetrieb."


def status_text(snapshot, processing=False, lore=None):
    """Servitor status for button E and the spoken "status" question.

    Warnings first, then what matters day to day: energy, temperature,
    connection and language core. Load and storage only when unusual.
    """
    snapshot = sanitize_snapshot(snapshot)
    warnings = []
    throttled = snapshot.get('throttled', 0)
    if throttled & 0x1:
        warnings.append("Warnung: Unterspannung.")
    elif throttled & 0x6:
        warnings.append("Warnung: Rechenleistung gedrosselt.")
    battery = snapshot.get('battery_pct')
    low_battery = (battery is not None and battery <= 15
                   and not snapshot.get('battery_plugged'))
    if low_battery:
        warnings.append(f"Warnung: Energiespeicher kritisch, {battery} Prozent.")
    temperature = snapshot.get('temp_c')
    hot = temperature is not None and temperature >= 75
    if hot:
        warnings.append(f"Warnung: Kerntemperatur {temperature} Grad.")
    if snapshot.get('mem_free_pct', 100) <= 10:
        warnings.append("Warnung: Arbeitsspeicher knapp.")
    if snapshot.get('disk_free_pct', 100) <= 10:
        warnings.append("Warnung: Datenspeicher knapp.")
    if snapshot.get('wlan') == 'off':
        warnings.append("WLAN deaktiviert. Nur lokaler Betrieb.")
    elif snapshot.get('server') == 'down':
        warnings.append("Server nicht erreichbar. Lokaler Betrieb.")

    lore = lore or snapshot.get('lore', 'off')
    if processing:
        parts = ["Direktive in Bearbeitung."]
    elif lore == 'full':
        parts = ["Status-Litanei beginnt."]
        if any(w.startswith("Warnung") for w in warnings):
            parts.append("Makel am Maschinengeist erkannt.")
    elif any(w.startswith("Warnung") for w in warnings):
        parts = ["Status eingeschränkt."]
    else:
        parts = ["Status nominal."]
    parts += warnings
    if not low_battery:
        sentence = battery_sentence(snapshot)
        if sentence:
            parts.append(sentence)
    if temperature is not None and not hot:
        parts.append(f"Kerntemperatur {temperature} Grad.")
    if snapshot.get('load_pct', 0) >= 80:
        parts.append(f"Systemlast {snapshot['load_pct']} Prozent.")
    server = snapshot.get('server')
    if server == 'ok':
        parts.append("Verbindung zum Server stabil.")
        if snapshot.get('llm_mode') == 'local':
            parts.append("Sprachkern lokal.")
        elif snapshot.get('llm') == 'offline':
            parts.append("Sprachkern im Notbetrieb.")
    elif server == 'off' and snapshot.get('wlan') != 'off':
        parts.append("Nur lokaler Betrieb.")
    uptime = snapshot.get('uptime_s')
    if uptime is not None:
        parts.append(f"Laufzeit {_uptime_from_seconds(uptime)}.")
    if not processing:
        if lore == 'full':
            parts.append("Der Maschinengeist ist besänftigt. Lob dem Omnissiah.")
        elif lore == 'light' and not any(w.startswith("Warnung") for w in warnings):
            parts.append("Maschinengeist ruhig. Befehl erwartet.")
        else:
            parts.append("Befehl erwartet.")
    return " ".join(parts)
