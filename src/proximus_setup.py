"""On-demand, locally confined Proximus Wi-Fi provisioning hotspot.

The single-radio Zero 2 W switches between AP and station; AP+STA is NOT
assumed. Never auto-start this service. A private WPA2 password is shown
only on the physical PiTFT, never in the public display snapshot or journal.
NoDogSplash/RaspAP are not needed: NetworkManager supplies DHCP and DNS.
"""
import html
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

from network_control import Wifi, WifiError

IFACE = "wlan0"
AP_NAME = "Proximus-Setup"
AP_IP = "192.168.77.1"
AP_URL = "http://" + AP_IP + "/"
MAX_AGE = 15 * 60
MAX_ATTEMPTS = 5
MARKER = "# Created by Proximus setup; root-managed, do not edit.\n"
KEY_DIR = Path("/etc/NetworkManager/system-connections")
INFO_FILE = Path("/run/pi-ptt/proximus-setup-info")
TABLE = "proximus_setup"
NFT_RULES = """table inet proximus_setup {
 chain input {
  type filter hook input priority -40; policy accept;
  iifname "wlan0" udp dport { 53, 67 } accept
  iifname "wlan0" tcp dport { 53, 80 } accept
  iifname "wlan0" drop
 }
 chain forward {
  type filter hook forward priority -40; policy accept;
  iifname "wlan0" drop
 }
}
"""


class SetupError(RuntimeError):
    pass


def network_name(name):
    """Valid Unicode SSID (<=32 bytes), one keyfile line, no control chars."""
    return (isinstance(name, str) and 1 <= len(name.encode("utf-8")) <= 32
            and all(ch.isprintable() for ch in name) and name.strip() == name)


def password_ok(password):
    return (isinstance(password, str) and 8 <= len(password) <= 63
            and all(32 <= ord(ch) <= 126 for ch in password))


def keyfile_text(ssid, password, identity, ap=False):
    if not network_name(ssid) or not password_ok(password):
        raise SetupError("Invalid Wi-Fi credentials")
    def escape(value):
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
    mode = "ap" if ap else "infrastructure"
    ipv4 = ("method=shared\naddress1=" + AP_IP + "/24\n") if ap else "method=auto\n"
    ipv6 = "disabled" if ap else "auto"
    return (MARKER + "[connection]\n"
            + "id=" + ("proximus-setup" if ap else "Proximus: " + escape(ssid))
            + "\nuuid=" + str(identity)
            + "\ntype=wifi\ninterface-name=wlan0\nautoconnect=false\n"
            + "\n[wifi]\nmode=" + mode + "\nssid=" + escape(ssid)
            + "\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk=" + escape(password)
            + "\n\n[ipv4]\n" + ipv4 + "\n[ipv6]\nmethod=" + ipv6 + "\n")


def private_write(path, content, owner=None):
    """Root-only (or physical-user-only) atomic text file, never follow links."""
    path = Path(path)
    if not path.parent.is_dir():
        raise SetupError("Runtime directory missing")
    tmp = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        if owner:
            account = pwd.getpwnam(owner)
            os.chown(tmp, account.pw_uid, account.pw_gid)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def command(args, *, input_text=None, timeout=25, check=True):
    try:
        result = subprocess.run(args, input=input_text, text=True, capture_output=True,
                                timeout=timeout, check=False,
                                env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError("Network command failed or timed out") from exc
    if check and result.returncode:
        # stderr may contain SSIDs/secrets: never log or return it.
        raise SetupError("Network command returned a failure")
    return result.stdout


def clean_stale():
    """Remove only our marked ephemeral AP profiles, including after a crash."""
    for path in KEY_DIR.glob("proximus-setup-*.nmconnection"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not content.startswith(MARKER):
            continue
        match = re.search(r"(?m)^uuid=([0-9a-f-]{36})$", content)
        if match:
            command(["nmcli", "connection", "delete", "uuid", match.group(1)],
                    timeout=8, check=False)
        path.unlink(missing_ok=True)
    INFO_FILE.unlink(missing_ok=True)
    command(["nft", "delete", "table", "inet", TABLE], timeout=5, check=False)


class Provisioning:
    def __init__(self, *, directory=KEY_DIR, info_path=INFO_FILE, run=command,
                 wifi=None, clock=time.monotonic):
        self.directory, self.info_path, self.run = Path(directory), Path(info_path), run
        self.wifi = wifi or Wifi()
        self.clock = clock
        self.ap_uuid = str(uuid.uuid4())
        self.ap_path = self.directory / ("proximus-setup-" + self.ap_uuid + ".nmconnection")
        self.psk = secrets.token_urlsafe(12)  # 16 ASCII chars, WPA2-valid
        self.csrf = secrets.token_urlsafe(32)
        self.found = []
        self.busy = False
        self.error = ""
        self.attempts = 0
        self.done = False
        self.previous = None
        self.server = None
        self.lock = threading.Lock()

    def setup(self):
        for tool in ("nmcli", "nft", "dnsmasq"):
            if shutil.which(tool) is None:
                raise SetupError("Missing dependency: " + tool)
        if not self.directory.is_dir():
            raise SetupError("NetworkManager profile directory missing")
        # The PiTFT control explicitly starts this service; do not enable the
        # radio implicitly after a user disabled it.
        self.previous = self.wifi.active_uuid()
        try:
            self.found = [name for name, _level in self.wifi.scan()]
        except WifiError:
            self.found = []
        # Install isolation first: NM IPv4 shared otherwise NATs to eth0.
        self.run(["nft", "-f", "-"], input_text=NFT_RULES, timeout=5)
        try:
            private_write(self.ap_path, keyfile_text(AP_NAME, self.psk, self.ap_uuid, ap=True))
            self.run(["nmcli", "connection", "load", str(self.ap_path)])
            self.run(["nmcli", "--wait", "20", "connection", "up", "uuid", self.ap_uuid,
                      "ifname", IFACE], timeout=25)
            private_write(self.info_path, json.dumps(
                dict(ssid=AP_NAME, password=self.psk, url=AP_URL)), owner="obivan")
        except Exception:
            self.cleanup()
            raise

    def _drop_profile(self, identity, path):
        self.run(["nmcli", "connection", "delete", "uuid", identity],
                 timeout=8, check=False)
        Path(path).unlink(missing_ok=True)

    def cleanup(self):
        self.info_path.unlink(missing_ok=True)
        self._drop_profile(self.ap_uuid, self.ap_path)
        self.run(["nft", "delete", "table", "inet", TABLE], timeout=5, check=False)
        if self.previous and not self.done:
            try:
                self.run(["nmcli", "--wait", "12", "connection", "up", "uuid",
                          self.previous, "ifname", IFACE], timeout=16, check=False)
            except SetupError:
                pass

    def connect(self, ssid, password):
        """Switch to STA only after HTTP response. Restore AP after failure."""
        identity = str(uuid.uuid4())
        path = self.directory / ("proximus-net-" + identity + ".nmconnection")
        try:
            private_write(path, keyfile_text(ssid, password, identity))
            self.run(["nmcli", "connection", "load", str(path)])
            self.run(["nmcli", "--wait", "25", "connection", "up", "uuid", identity,
                      "ifname", IFACE], timeout=30)
            self.run(["nmcli", "connection", "modify", "uuid", identity,
                      "connection.autoconnect", "yes"])
            self.done = True
            if self.server:
                self.server.shutdown()
        except (SetupError, OSError):
            self._drop_profile(identity, path)
            try:
                self.run(["nmcli", "--wait", "20", "connection", "up", "uuid",
                          self.ap_uuid, "ifname", IFACE], timeout=25)
            except SetupError:
                # Recovering a completely lost radio needs local device access.
                if self.server:
                    self.server.shutdown()
            with self.lock:
                self.error = "Verbindung fehlgeschlagen. Passwort prüfen und erneut versuchen."
        finally:
            with self.lock:
                self.busy = False

    def try_connect(self, ssid, password, token):
        with self.lock:
            if (self.busy or self.done or self.attempts >= MAX_ATTEMPTS
                    or not hmac.compare_digest(token, self.csrf)):
                return False
            if not network_name(ssid) or not password_ok(password):
                return False
            self.attempts += 1
            self.error = ""
            self.busy = True
        threading.Thread(target=self.connect, args=(ssid, password),
                         name="wifi-provision", daemon=True).start()
        return True

    def page(self):
        with self.lock:
            busy, error, attempts = self.busy, self.error, self.attempts
        options = "".join('<option value="' + html.escape(name, quote=True) + '">'
                          + html.escape(name) + '</option>' for name in self.found)
        status = ("Verbindung wird hergestellt. Das Setup-WLAN schaltet sich kurz ab."
                  if busy else error)
        disabled = "disabled" if busy or attempts >= MAX_ATTEMPTS else ""
        return """<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proximus · WLAN einrichten</title>
<style>body{font:16px system-ui,sans-serif;color:#ebf5f5;background:#101922;
max-width:440px;margin:10vh auto;padding:25px}h1{color:#86dedc}
label{display:block;margin-top:18px}input,select,button{
display:block;box-sizing:border-box;width:100%;padding:14px;margin-top:7px;
border-radius:7px;border:1px solid #5e858c;background:#202f3b;color:white}
button{background:#24666b;cursor:pointer}small{color:#9bb9be}
</style><h1>PROXIMUS</h1><p>WLAN-Konfiguration</p>
<p role="status">""" + html.escape(status) + """</p>
<form method="post" action="/connect">
<input type="hidden" name="token" value='""" + html.escape(self.csrf, quote=True) + """'>
<label>Gefundenes Netzwerk<select name="ssid">
<option value="">Netzwerk auswählen</option>""" + options + """</select></label>
<label>SSID manuell (optional)<input name="manual_ssid" autocomplete="off"
maxlength="32" placeholder="Nur für versteckte Netze"></label>
<label>WLAN-Passwort<input type="password" name="password"
autocomplete="new-password" required minlength="8" maxlength="63"></label>
<button """ + disabled + """>Verbinden</button></form>
<small>Diese Seite funktioniert nur über Proximus-Setup.
Bei Erfolg wird der Hotspot beendet. Bei Fehler erscheint er erneut.</small>
</html>"""

    def serve(self):
        app = self

        class Portal(BaseHTTPRequestHandler):
            server_version = "ProximusSetup"
            sys_version = ""

            def log_message(self, *_args):
                pass  # no HTTP data, SSIDs or passwords in journald

            def response(self, code, body, content_type="text/html; charset=utf-8"):
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; style-src 'unsafe-inline'; "
                                 "form-action 'self'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self):
                return self.headers.get("Host", "") in (AP_IP, AP_IP + ":80")

            def do_GET(self):
                if not self._host_ok():
                    self.response(403, "Verboten")
                elif self.path in ("/", "/index.html"):
                    self.response(200, app.page())
                elif self.path in ("/generate_204", "/hotspot-detect.html"):
                    # Useful when a captive OS probe already reached our host.
                    self.send_response(302)
                    self.send_header("Location", AP_URL)
                    self.end_headers()
                else:
                    self.response(404, "Nicht gefunden")

            def do_POST(self):
                if not self._host_ok() or self.path != "/connect":
                    self.response(403, "Verboten")
                    return
                if self.headers.get("Content-Type", "").split(";")[0] != "application/x-www-form-urlencoded":
                    self.response(415, "Ungültiger Inhalt")
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= size <= 512:
                        raise ValueError()
                    fields = urllib.parse.parse_qs(
                        self.rfile.read(size).decode("utf-8"), strict_parsing=True)
                    if not all(len(fields.get(key, [])) == 1
                               for key in ("password", "token")):
                        raise ValueError()
                    if any(len(fields.get(key, [])) > 1
                           for key in ("ssid", "manual_ssid")):
                        raise ValueError()
                except (ValueError, UnicodeDecodeError):
                    self.response(400, "Ungültige Anfrage")
                    return
                ssid = (fields.get("manual_ssid", [""])[0]
                        or fields.get("ssid", [""])[0])
                if not app.try_connect(ssid, fields["password"][0],
                                       fields["token"][0]):
                    self.response(403, "Ungültige Auswahl oder Sitzungsfreigabe")
                    return
                self.response(202, "<h1>Verbinde …</h1>"
                                   "<p>Bei Erfolg wird das Setup-WLAN beendet.</p>"
                                   "<p>Bei Fehler bitte erneut mit Proximus-Setup verbinden.</p>")

        self.server = ThreadingHTTPServer((AP_IP, 80), Portal)
        self.server.daemon_threads = True
        timer = threading.Timer(MAX_AGE, self.server.shutdown)
        timer.daemon = True
        timer.start()
        try:
            self.server.serve_forever(poll_interval=.25)
        finally:
            timer.cancel()
            self.server.server_close()


def main():
    if os.geteuid() != 0:
        raise SystemExit("proximus_setup requires root (systemd on-demand only)")
    if "--cleanup" in sys.argv:
        clean_stale()
        return
    clean_stale()
    app = Provisioning()
    signal.signal(signal.SIGTERM, lambda *_args: sys.exit(0))
    try:
        app.setup()
        app.serve()
    except (SetupError, OSError) as exc:
        print("proximus-setup: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
    finally:
        app.cleanup()


if __name__ == "__main__":
    main()
