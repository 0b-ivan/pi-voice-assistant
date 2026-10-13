"""Small NetworkManager client for the PiTFT Wi-Fi picker.

Phase 1 activates only *existing* Wi-Fi profiles, by UUID. No password is
passed through argv or logs. NetworkManager owns the credentials. Operations
run in a worker thread; they must not block pi-ptt's GPIO/audio loop.
"""
import os
import re
import shutil
import subprocess

WLAN = "wlan0"
UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
CONNECTED = "WLAN verbunden."
ALREADY_CONNECTED = "Dieses WLAN ist bereits verbunden."
FAILED = "WLAN-Verbindung fehlgeschlagen. Vorheriges Netzwerk wird wiederhergestellt."
NOT_SAVED = "Netzwerk noch nicht gespeichert. Einrichtung über den Hotspot folgt."
NO_NETWORKS = "Keine WLAN-Netze gefunden."
RADIO_OFF = "WLAN ist ausgeschaltet."
UNAVAILABLE = "NetworkManager nicht verfügbar."
BUSY = "Netzwerkvorgang läuft. Bitte warten."
AP_ACTIVE = "Access Point aktiv. Erst im Menü Access Point beenden."


class WifiError(RuntimeError):
    """Safe, non-secret error message for the service journal."""


def split_terse(line):
    """Decode nmcli -t -e yes, including escaped colon and backslash."""
    fields, part, escaped = [], [], False
    for char in line:
        if escaped:
            part.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(part))
            part = []
        else:
            part.append(char)
    if escaped:
        part.append("\\")
    fields.append("".join(part))
    return fields


def clean_ssid(value):
    """Keep displayed network labels short and free of control characters."""
    return "".join(ch for ch in value if ch.isprintable())[:32].strip()


class Wifi:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def available(self):
        return shutil.which("nmcli") is not None

    def call(self, *args, timeout=15):
        try:
            result = self.runner(
                ["nmcli", *args], stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=timeout, check=False,
                env=dict(os.environ, LC_ALL="C"))
        except (OSError, subprocess.SubprocessError) as exc:
            raise WifiError("NetworkManager command unavailable or timed out") from exc
        if result.returncode:
            # Do not include command/daemon stderr: profiles or secrets may leak.
            raise WifiError("NetworkManager returned error " + str(result.returncode))
        return result.stdout

    def saved(self):
        """Known system/user Wi-Fi profiles (UUID never inferred from the SSID)."""
        output = self.call("-t", "-e", "yes", "-f", "NAME,UUID,TYPE", "connection", "show")
        profiles = []
        for line in output.splitlines():
            fields = split_terse(line)
            if len(fields) != 3:
                continue
            name, uuid, kind = fields
            if kind not in ("802-11-wireless", "wifi") or not UUID.fullmatch(uuid):
                continue
            try:
                ssid = self.call("-t", "-e", "no", "-g", "802-11-wireless.ssid",
                                 "connection", "show", "uuid", uuid).strip()
            except WifiError:
                continue
            if ssid:
                profiles.append(dict(uuid=uuid, ssid=clean_ssid(ssid),
                                     name=clean_ssid(name)))
        return profiles[:40]

    def scan(self):
        """(SSID, signal%) for visible APs; best BSSID of each SSID wins."""
        output = self.call("-t", "-e", "yes", "-f", "SSID,SIGNAL",
                           "device", "wifi", "list", "ifname", WLAN,
                           "--rescan", "yes", timeout=20)
        found = {}
        for line in output.splitlines():
            fields = split_terse(line)
            if len(fields) != 2:
                continue
            ssid, signal = fields
            ssid = clean_ssid(ssid)
            if not ssid:
                continue
            try:
                level = int(signal)
            except ValueError:
                continue
            if 0 <= level <= 100:
                found[ssid] = max(found.get(ssid, 0), level)
        return sorted(found.items(), key=lambda item: (-item[1], item[0]))[:32]

    def active_uuid(self):
        output = self.call("-t", "-e", "yes", "-f", "UUID,TYPE,DEVICE",
                           "connection", "show", "--active", timeout=5)
        for line in output.splitlines():
            fields = split_terse(line)
            if len(fields) == 3 and fields[1] in ("802-11-wireless", "wifi") and fields[2] == WLAN:
                return fields[0]
        return None

    def connect(self, uuid):
        """Activate a saved profile, try to recover the old one on failure."""
        if not UUID.fullmatch(str(uuid)):
            raise WifiError("Invalid saved Wi-Fi profile")
        if uuid not in {profile["uuid"] for profile in self.saved()}:
            raise WifiError("Wi-Fi profile is no longer available")
        previous = self.active_uuid()
        if previous == uuid:
            return False
        try:
            self.call("--wait", "20", "connection", "up", "uuid", uuid,
                      "ifname", WLAN, timeout=25)
            if self.active_uuid() != uuid:
                raise WifiError("Wi-Fi connection was not activated")
        except WifiError:
            if previous and previous != uuid:
                try:
                    self.call("--wait", "10", "connection", "up", "uuid",
                              previous, "ifname", WLAN, timeout=15)
                except WifiError:
                    pass
            raise
        return True
