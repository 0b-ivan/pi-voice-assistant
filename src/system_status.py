"""Servitor status: snapshot of the Pi plus the spoken status text."""

import os
import re
from pathlib import Path
import shutil

import logwatch
from mood import EMOTIONS


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
    'wifi_dbm': (int, -120, 0),
    'net_ms': (int, 0, 60000),
    'lan_ms': (int, 0, 60000),
    'updates': (int, 0, 9999),
    'updates_security': (int, 0, 9999),
    'server_updates': (int, 0, 9999),
    'server_updates_security': (int, 0, 9999),
    'apt_age_days': (int, 0, 3650),
    'mood_level': (int, 0, 100),
}
SNAPSHOT_FLAGS = ('battery_charging', 'battery_plugged')
SNAPSHOT_STATES = {'server': ('ok', 'down', 'off'), 'llm': ('openrouter', 'offline'),
                   'lore': ('off', 'light', 'full'), 'wlan': ('on', 'off'),
                   'persona': ('servitor', 'mensch'), 'voice': ('servitor', 'natural'),
                   'mood': EMOTIONS, 'mood_refuse': ('on', 'off'),
                   'llm_mode': ('auto', 'free', 'local'), 'memory': ('on', 'off'),
                   'dns': ('ok', 'fail'), 'maintenance': ('on', 'off'),
                   # device_control: the Pi knows spoken device commands, and the
                   # reboot/shutdown question it is waiting to have confirmed.
                   'devctl': ('on',), 'pending': ('reboot', 'shutdown'),
                   # The Pi plays multi-part long stories (story.py, /v1/story).
                   'story': ('on',),
                   # journeys.py: the Pi knows journeys, and what it is waiting for.
                   'trip': ('on', 'offers', 'ask', 'origin', 'destination', 'calendar',
                            'choice')}


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
    # ID of the journey proposal waiting for consent (journeys.Journeys.snapshot).
    if clean.get('trip') == 'ask' and isinstance(value.get('trip_id'), str) \
            and re.fullmatch(r'[0-9a-f]{16}', value['trip_id']):
        clean['trip_id'] = value['trip_id']
    # Self-test (logwatch): finding codes with counts, repairs done.
    findings = logwatch.clean_findings(value.get('log_findings'))
    if findings is not None:
        clean['log_findings'] = findings
        clean['log_repairs'] = logwatch.clean_repairs(value.get('log_repairs')) or []
        clean['log_last'] = logwatch.clean_last(value.get('log_last'))
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
                     llm_mode=None, extra=None, persona=None, voice=None,
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
        persona=persona,
        voice=voice,
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


# Wording of the fixed sentences: the Servitor's lore level, or Billy (the
# human module) plain or with full lore. The LLM gets persona and lore itself.
STYLES = ('off', 'light', 'full', 'billy', 'billy_full')


def phrase_style(lore=None, persona=None):
    lore = lore if lore in ('off', 'light', 'full') else 'off'
    if persona == 'mensch':
        return 'billy_full' if lore == 'full' else 'billy'
    return lore


def _style(snapshot, lore=None):
    """An explicit style or lore level wins; else what the snapshot says."""
    return lore or phrase_style(snapshot.get('lore'), snapshot.get('persona'))


def is_billy(style):
    return style in ('billy', 'billy_full')


def battery_sentence(snapshot, style='off'):
    percent = snapshot.get('battery_pct')
    if percent is None:
        return None
    if is_billy(style):
        if snapshot.get('battery_charging'):
            return f"Akku bei {percent} Prozent, ich lade gerade."
        if snapshot.get('battery_plugged'):
            return f"Akku bei {percent} Prozent, hänge am Netz."
        return f"Akku bei {percent} Prozent."
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

    lore = _style(snapshot, lore)
    billy = is_billy(lore)
    trouble = any(w.startswith("Warnung") for w in warnings)
    if processing:
        parts = ["Bin noch dran." if billy else "Direktive in Bearbeitung."]
    elif billy:
        parts = ["Lagebericht." + (" Nicht alles rund." if trouble else "")]
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
        sentence = battery_sentence(snapshot, lore)
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
    if snapshot.get('memory') == 'off':
        parts.append("Gedächtniskern fehlt.")
    maintenance = updates_sentence(snapshot, short=True)
    if maintenance:
        parts.append(maintenance)
    uptime = snapshot.get('uptime_s')
    if uptime is not None:
        parts.append(f"Laufzeit {_uptime_from_seconds(uptime)}.")
    if not processing:
        if billy:
            parts.append("Für den Imperator. Was brauchst du?" if lore == 'billy_full'
                         else "Was brauchst du?" if trouble else "Sonst alles ruhig, Boss.")
        elif lore == 'full':
            parts.append("Der Maschinengeist ist besänftigt. Lob dem Omnissiah.")
        elif lore == 'light' and not any(w.startswith("Warnung") for w in warnings):
            parts.append("Maschinengeist ruhig. Befehl erwartet.")
        else:
            parts.append("Befehl erwartet.")
    return " ".join(parts)


WIFI_WEAK_DBM = -78
SLOW_MS = 400
STALE_LISTS_DAYS = 7


def _wifi_quality(dbm):
    return 'gut' if dbm >= -67 else 'mittel' if dbm > WIFI_WEAK_DBM else 'schwach'


def network_text(snapshot, lore=None):
    """Spoken network report from the Pi's measurements."""
    snapshot = sanitize_snapshot(snapshot)
    lore = _style(snapshot, lore)
    if snapshot.get('wlan') == 'off':
        return ("WLAN ist aus, ich bin offline." if is_billy(lore)
                else "WLAN deaktiviert. Keine Netzwerkverbindung.")
    parts = ["Funkcheck." if is_billy(lore) else
             "Netzwerkbericht." if lore != 'full' else "Abtastung der Noosphäre."]
    dbm = snapshot.get('wifi_dbm')
    if dbm is not None:
        parts.append(f"WLAN-Signal minus {abs(dbm)} dBm, {_wifi_quality(dbm)}.")
    lan = snapshot.get('lan_ms')
    server = snapshot.get('server')
    if server == 'ok':
        parts.append(f"Server erreichbar, {lan} Millisekunden." if lan is not None
                     else "Server erreichbar.")
    elif server == 'down':
        parts.append("Server nicht erreichbar.")
    net = snapshot.get('net_ms')
    if net is None:
        parts.append("Internet nicht erreichbar.")
    else:
        slow = " Verbindung langsam." if net >= SLOW_MS else ""
        parts.append(f"Internet erreichbar, Latenz {net} Millisekunden.{slow}")
    if snapshot.get('dns') == 'fail':
        parts.append("Namensauflösung gestört.")
    elif snapshot.get('dns') == 'ok':
        parts.append("Namensauflösung in Ordnung.")
    if lore == 'full':
        parts.append("Die Noosphäre ist vermessen.")
    return " ".join(parts)


def _count(n, one, many):
    return f"{n} {one if n == 1 else many}"


def updates_sentence(snapshot, short=False):
    """Pending updates of the Pi and the server, or None if nothing is due."""
    texts = []
    for prefix, name in (('updates', 'Pi'), ('server_updates', 'Server')):
        pending = snapshot.get(prefix)
        if not pending:
            continue
        security = snapshot.get(f'{prefix}_security', 0)
        text = f"{name}: {_count(pending, 'Aktualisierung', 'Aktualisierungen')}"
        if security:
            text += f", davon {security} sicherheitsrelevant"
        texts.append(text)
    if not texts:
        return None
    return ("Wartung empfohlen. " if short else "Systemwartung erforderlich. ") + \
        ". ".join(texts) + "."


def updates_text(snapshot, lore=None):
    snapshot = sanitize_snapshot(snapshot)
    lore = _style(snapshot, lore)
    sentence = updates_sentence(snapshot)
    if 'updates' not in snapshot and 'server_updates' not in snapshot:
        return "Wartungsdaten noch nicht erhoben."
    parts = [sentence or "Keine Aktualisierungen ausstehend."]
    age = snapshot.get('apt_age_days')
    if age is not None and age >= STALE_LISTS_DAYS:
        parts.append(f"Paketlisten sind {age} Tage alt, Angaben unsicher.")
    if lore == 'full':
        parts.append("Die Riten der Wartung sind fällig." if sentence
                     else "Der Maschinengeist ist rein.")
    elif lore == 'billy_full' and sentence:
        parts.append("Soll sich ein Techpriester drum kümmern.")
    return " ".join(parts)
