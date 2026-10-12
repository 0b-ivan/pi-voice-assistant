"""Shared, fixed local configuration for the temporary Proximus setup AP.

Imported by the root provisioning unit, the unprivileged voice controller and
the PiTFT display. No credentials or externally supplied values live here.
Changing the network requires an intentional code/configuration review.
"""
from pathlib import Path

UNIT = "proximus-setup.service"
IFACE = "wlan0"
SSID = "Proximus-Setup"
ADDRESS = "192.168.77.1"
PORTAL_URL = "http://" + ADDRESS + "/"
SESSION_SECONDS = 15 * 60
INFO_FILE = Path("/run/proximus-setup/info")
DISPLAY_VIEW_FILE = Path("/run/pi-ptt/display-people.json")


def live_expiration(value, now):
    """Strict Unix expiry check, shared by QR/text view and voice service."""
    return (type(value) in (int, float) and
            now < value <= now + SESSION_SECONDS + 60)
