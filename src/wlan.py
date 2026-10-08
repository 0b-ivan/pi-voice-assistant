"""Switch the Pi's WLAN radio with rfkill (soft block).

Needs write access to /dev/rfkill for the service user, e.g. the udev rule
deploy/90-rfkill-netdev.rules (group netdev). With WLAN off the Pi has no
network unless Ethernet is plugged in: no server, no OpenRouter, no SSH.
"""
from pathlib import Path
import subprocess

RFKILL = '/usr/sbin/rfkill'
SYSFS = Path('/sys/class/rfkill')


def wlan_blocked(root=SYSFS):
    """True/False for the first WLAN radio, None if there is none."""
    for device in sorted(root.glob('rfkill*')):
        try:
            if (device / 'type').read_text().strip() == 'wlan':
                return (device / 'soft').read_text().strip() == '1'
        except OSError:
            continue
    return None


def set_wlan(on, run=subprocess.run):
    """Raises OSError/CalledProcessError if rfkill is missing or not permitted."""
    run([RFKILL, 'unblock' if on else 'block', 'wlan'], check=True, timeout=5,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
