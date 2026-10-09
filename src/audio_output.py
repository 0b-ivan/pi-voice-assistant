"""Where Proximus' voice goes: the WM8960 speaker or a Bluetooth speaker.

pi-ptt sets the Bluetooth sink when a speaker connects (bluetooth.py); every
playback asks current() at play time, so switching needs no restart. The
ALSA device of a Bluetooth speaker comes from bluez-alsa
(libasound2-plugin-bluez).
"""
import threading

_lock = threading.Lock()
_bluetooth = None          # MAC of the connected Bluetooth speaker


def set_bluetooth(mac):
    global _bluetooth
    with _lock:
        _bluetooth = mac


def bluetooth():
    return _bluetooth


def current(default):
    """ALSA device for the next playback."""
    mac = _bluetooth
    return f'bluealsa:DEV={mac},PROFILE=a2dp' if mac else default
