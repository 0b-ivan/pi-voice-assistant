"""On-demand, read-only PiTFT network diagnostics.

No scan, no credential reads, no shell, no background polling. Results are
only published to the owner-readable PiTFT picker; never to the public status
snapshot or the voice/LLM path.
"""
import ipaddress
import re
import subprocess

import hotspot_config as ap_config
import hotspot_control
from network_control import Wifi, WifiError, split_terse

_FIELDS = "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS"
_INDEX = re.compile(r"\[\d+\]$")


def _safe_text(text, limit=32):
    return "".join(c for c in str(text) if c.isprintable() and c not in "\r\n\t")[:limit]


def _ipv4(value, prefix=False):
    try:
        if prefix:
            parsed = ipaddress.ip_interface(value)
            return str(parsed) if parsed.version == 4 else None
        parsed = ipaddress.ip_address(value)
        return str(parsed) if parsed.version == 4 else None
    except ValueError:
        return None


def _device(wifi, interface):
    try:
        data = wifi.call("-t", "-e", "yes", "-f", _FIELDS,
                         "device", "show", interface, timeout=5)
    except WifiError:
        return {}
    result = {}
    for line in data.splitlines()[:64]:
        parts = split_terse(line)
        if len(parts) != 2:
            continue
        key = _INDEX.sub("", parts[0])
        if key not in _FIELDS.split(","):
            continue
        if key not in result and parts[1].strip():
            result[key] = parts[1].strip()
    return result


def _state(device):
    code = (device.get("GENERAL.STATE") or "").split(" ", 1)[0]
    if code == "100":
        return "verbunden"
    if code in ("40", "50", "60", "70", "80", "90"):
        return "verbinde..."
    if code in ("10", "20", "30"):
        return "getrennt"
    return "unbekannt"


def _station_link():
    """No active Wi-Fi scan; reads only current station link information."""
    try:
        result = subprocess.run(
            ["iw", "dev", ap_config.IFACE, "link"],
            capture_output=True, text=True, timeout=3, check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode or len(result.stdout) > 4096:
        return {}
    details = {}
    for line in result.stdout.splitlines()[:30]:
        value = line.strip()
        if value.startswith("SSID:"):
            details["ssid"] = _safe_text(value[5:].strip())
        elif value.startswith("signal:"):
            match = re.search(r"(-?\d+)\s*dBm", value)
            if match and -110 <= int(match.group(1)) <= 0:
                details["dbm"] = int(match.group(1))
    return details


def rows(wifi=None, *, ap_active=None, clients=None, station=None):
    """Short, ordered strings for a scrollable 240x240 display.

    Optional inputs allow hardware-independent tests. Every command is read
    only. Errors result in a meaningful partial view, not a failed menu.
    """
    wifi = wifi if wifi is not None else Wifi()
    eth = _device(wifi, "eth0")
    wlan = _device(wifi, ap_config.IFACE)
    if ap_active is None:
        ap_active = hotspot_control.is_active()
    if ap_active and clients is None:
        clients = hotspot_control.read_clients()
    if not ap_active and station is None:
        station = _station_link()
    station = station or {}

    output = ["LAN: " + _state(eth)]
    if addr := _ipv4(eth.get("IP4.ADDRESS"), prefix=True):
        output.append("LAN-IP: " + addr)
    if gateway := _ipv4(eth.get("IP4.GATEWAY")):
        output.append("LAN-GW: " + gateway)
    if ap_active:
        output.append("WLAN: Access Point")
        output.append("SSID: " + ap_config.SSID)
    else:
        output.append("WLAN: " + _state(wlan))
        if station.get("ssid"):
            output.append("SSID: " + _safe_text(station["ssid"], 32))
        elif _state(wlan) == "verbunden":
            name = _safe_text(wlan.get("GENERAL.CONNECTION", ""), 24)
            if name:
                output.append("Profil: " + name)
        if type(station.get("dbm")) is int and -110 <= station["dbm"] <= 0:
            output.append("Signal: " + str(station["dbm"]) + " dBm")
    if addr := _ipv4(wlan.get("IP4.ADDRESS"), prefix=True):
        output.append("WLAN-IP: " + addr)
    if gateway := _ipv4(wlan.get("IP4.GATEWAY")):
        output.append("WLAN-GW: " + gateway)
    for interface in (wlan, eth):
        if dns := _ipv4(interface.get("IP4.DNS")):
            output.append("DNS: " + dns)
            break
    if ap_active:
        if clients is None:
            output.append("AP-Clients: unbekannt")
        else:
            count = len(clients.get("clients", []))
            output.append("AP-Clients: " + str(count))
        output.append("Portal: " + ap_config.ADDRESS)
    return output[:12]
