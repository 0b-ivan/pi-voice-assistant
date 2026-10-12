"""Unprivileged PiTFT control of the single on-demand Proximus setup unit.

systemctl uses polkit over the system bus. pi-ptt keeps NoNewPrivileges=yes;
only systemd itself starts the root-owned setup service. Nothing from the
private info file is logged, passed on argv or put into display-status.json.
"""
import json
import os
import re
import stat
from pathlib import Path
import subprocess
import time
import hotspot_config as ap_config

UNIT = ap_config.UNIT
INFO_FILE = ap_config.INFO_FILE
SSID = ap_config.SSID
PORTAL = ap_config.PORTAL_URL
MAX_INFO_AGE = ap_config.SESSION_SECONDS + 30


class HotspotError(RuntimeError):
    """Only safe, secret-free messages for the user."""


def _systemctl(*args, timeout=10):
    try:
        return subprocess.run(
            ["/usr/bin/systemctl", "--no-ask-password", *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=False, timeout=timeout,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_active():
    return _systemctl("is-active", "--quiet", UNIT, timeout=5)


def control(action):
    """Request only start/stop for the setup unit; polkit checks unit+verb."""
    if action not in ("start", "stop"):
        raise ValueError("Unsupported setup action")
    return _systemctl(action, UNIT, timeout=30)


def read_info(path=INFO_FILE, now=None):
    """Read bounded live session data; reject stale, malformed or foreign info.

    Only call this after is_active(). The directory is root:obivan 0750
    and the info file obivan 0600. This function never returns a partial key.
    """
    path = Path(path)
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            return None
        age = (time.time() if now is None else now) - metadata.st_mtime
        if age < -60 or age > MAX_INFO_AGE:
            return None
        with path.open(encoding="utf-8") as fd:
            raw = fd.read(1025)
        if len(raw) > 1024:
            return None
        info = json.loads(raw)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    if not isinstance(info, dict):
        return None
    ssid, password, portal = info.get("ssid"), info.get("password"), info.get("url")
    issued_at, expires_at = info.get("issued_at"), info.get("expires_at")
    timestamp = time.time() if now is None else now
    if ssid != SSID or portal != PORTAL or not isinstance(password, str):
        return None
    if not 8 <= len(password) <= 63 or any(not (33 <= ord(c) <= 126) for c in password):
        return None
    if (type(issued_at) not in (int, float) or
            issued_at > timestamp + 60 or issued_at < timestamp - MAX_INFO_AGE or
            not ap_config.live_expiration(expires_at, timestamp) or
            not 0 < expires_at - issued_at <= ap_config.SESSION_SECONDS + 1):
        return None
    return {"ssid": ssid, "password": password, "url": portal,
            "issued_at": issued_at, "expires_at": expires_at}

def read_clients(path=ap_config.CLIENTS_FILE, now=None):
    """Bounded, short-lived AP station list for the physical display only."""
    path = Path(path)
    t = time.time() if now is None else now
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            return None
        if t < metadata.st_mtime - 60 or t - metadata.st_mtime > ap_config.CLIENT_FRESH_SECONDS:
            return None
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            return None
        data = json.loads(raw)
    except (OSError, ValueError, UnicodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    updated = data.get("updated_at")
    if type(updated) not in (float, int) or updated > t + 60 or t - updated > ap_config.CLIENT_FRESH_SECONDS:
        return None
    values = data.get("clients")
    if not isinstance(values, list) or len(values) > 24:
        return None
    clients = []
    for row in values:
        if not isinstance(row, dict):
            return None
        mac, level = row.get("mac"), row.get("signal_dbm")
        if (not isinstance(mac, str) or
                not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", mac) or
                (level is not None and (type(level) is not int or not -110 <= level <= 0))):
            return None
        clients.append(dict(mac=mac, signal_dbm=level))
    joined = data.get("last_joined_at")
    if joined is not None and type(joined) not in (float, int):
        return None
    if joined is not None and (joined > t + 60 or joined < t - ap_config.SESSION_SECONDS - 10):
        joined = None
    return dict(clients=clients, updated_at=updated, last_joined_at=joined)
