"""Dynamic, dependency-free system status text for Button SHIM E."""

from pathlib import Path
import shutil


THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")
MEMINFO_PATH = Path("/proc/meminfo")
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


def _disk_free_percent(path="/", disk_usage=shutil.disk_usage):
    try:
        usage = disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return max(0, min(100, round(usage.free * 100 / usage.total)))


def _uptime_words(path=UPTIME_PATH):
    raw = _read_text(path)
    if not raw:
        return None
    try:
        seconds = int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return None
    minutes = max(0, seconds // 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days} Tage {hours} Stunden"
    if hours:
        return f"{hours} Stunden {minutes} Minuten"
    return f"{minutes} Minuten"


def build_status_text(
    processing=False,
    stt_provider=None,
    thermal_path=THERMAL_PATH,
    meminfo_path=MEMINFO_PATH,
    uptime_path=UPTIME_PATH,
    disk_path="/",
    disk_usage=shutil.disk_usage,
):
    """Return a compact Servitor-style status using only locally readable data."""
    parts = []
    if processing:
        parts.append("VERARBEITUNGSPROTOKOLL AKTIV.")
        parts.append("AUFNAHME IN ANALYSE.")
    else:
        parts.append("SYSTEM NOMINAL.")
        parts.append("MASCHINENGEIST SYNCHRONISIERT.")

    temperature = _temperature_c(thermal_path)
    if temperature is not None:
        parts.append(f"KERNTEMPERATUR {temperature} GRAD.")

    memory = _memory_free_percent(meminfo_path)
    if memory is not None:
        parts.append(f"ARBEITSSPEICHER {memory} PROZENT FREI.")

    disk = _disk_free_percent(disk_path, disk_usage=disk_usage)
    if disk is not None:
        parts.append(f"DATENSPEICHER {disk} PROZENT FREI.")

    uptime = _uptime_words(uptime_path)
    if uptime:
        parts.append(f"LAUFZEIT {uptime}.")

    provider = (stt_provider or "").strip().lower()
    if provider == "vosk":
        parts.append("OFFLINE SPRACHERKENNUNG AKTIV.")
    elif provider:
        parts.append("SPRACHERKENNUNG KONFIGURIERT.")

    if processing:
        parts.append("BEFEHL IN BEARBEITUNG.")
    else:
        parts.append("SERVITOR EINHEIT BEREIT.")
        parts.append("BEFEHL ERWARTET.")

    return " ".join(parts)
