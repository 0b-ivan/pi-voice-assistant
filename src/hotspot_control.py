"""Unprivileged PiTFT control of the single on-demand Proximus setup unit.

systemctl uses polkit over the system bus. pi-ptt keeps NoNewPrivileges=yes;
only systemd itself starts the root-owned setup service. Nothing from the
private info file is logged, passed on argv or put into display-status.json.
"""
import json
import os
from pathlib import Path
import subprocess
import time

UNIT = "proximus-setup.service"
INFO_FILE = Path("/run/proximus-setup/info")
SSID = "Proximus-Setup"
PORTAL = "http://192.168.77.1/"
MAX_INFO_AGE = 15 * 60 + 30


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
        stat = path.stat()
        if not stat.st_mode & 0o170000 == 0o100000:  # regular file
            return None
        age = (time.time() if now is None else now) - stat.st_mtime
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
    if ssid != SSID or portal != PORTAL or not isinstance(password, str):
        return None
    if not 8 <= len(password) <= 63 or any(not (33 <= ord(c) <= 126) for c in password):
        return None
    return {"ssid": ssid, "password": password, "url": portal}
