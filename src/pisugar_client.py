"""Optional read-only interface to the official PiSugar Power Manager.

The PiSugar 3 itself is already read by power.Battery over I2C. This adapter
adds firmware, charge range and shutdown settings without performing register
writes or running arbitrary PiSugar commands. An explicit PISUGAR_SOCKET path
is required; do not weaken pi-ptt.service's PrivateTmp isolation to expose
the vendor's default /tmp/pisugar-server.sock.
"""
import os
import re
import socket
import threading


READS = ('model', 'firmware_version', 'battery_charging_range',
         'safe_shutdown_level', 'safe_shutdown_delay', 'soft_poweroff',
         'rtc_alarm_enabled', 'rtc_alarm_time')


class Client:
    def __init__(self, path=None, timeout=0.35):
        self.path = path if path is not None else os.environ.get('PISUGAR_SOCKET', '')
        self.timeout = timeout

    def get(self, key):
        """One whitelisted query per connection; cannot execute shell actions."""
        if key not in READS:
            raise ValueError("PiSugar query not allowlisted")
        if not self.path:
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout)
                conn.connect(self.path)
                conn.sendall(('get ' + key + '\n').encode('ascii'))
                data = b''
                while len(data) < 512 and b'\n' not in data:
                    part = conn.recv(512 - len(data))
                    if not part:
                        break
                    data += part
            line = data.decode('utf-8', errors='replace').splitlines()[0]
        except (OSError, IndexError):
            return None
        prefix, sep, value = line.partition(':')
        if not sep or prefix.strip() != key:
            return None
        return value.strip()

    def read(self):
        """Return only validated, safe-to-speak fields or {} if not installed."""
        model = self.get('model')
        if not model or not model.lower().startswith('pisugar 3'):
            return {}
        data = {}
        version = self.get('firmware_version')
        if version and re.fullmatch(r'[\w.\-]{1,32}', version):
            data['pisugar_firmware'] = version
        charging = self.get('battery_charging_range')
        # The official web UI parses a bare comma-separated pair, e.g. "70,80".
        # Also accept the bracket notation used as a placeholder in the README.
        match = re.fullmatch(r'\[?\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\]?', charging or '')
        if match:
            start, stop = map(int, match.groups())
            if 0 <= start < stop <= 100:
                data['charge_restart_pct'] = start
                data['charge_limit_pct'] = stop
        for field, key, low, high in (
            ('safe_shutdown_level', 'safe_shutdown_pct', 0, 100),
            ('safe_shutdown_delay', 'safe_shutdown_delay_s', 0, 3600),
        ):
            value = self.get(field)
            if value and value.isdecimal() and low <= int(value) <= high:
                data[key] = int(value)
        soft = self.get('soft_poweroff')
        if soft in ('true', 'false'):
            data['pisugar_soft_poweroff'] = 'on' if soft == 'true' else 'off'
        rtc = self.get('rtc_alarm_enabled')
        if rtc in ('true', 'false'):
            data['pisugar_rtc_alarm'] = 'on' if rtc == 'true' else 'off'
        return data


class Poller:
    """At most one short background worker; never stall PTT or audio."""
    def __init__(self, client=None, interval=120.0):
        self.client = client or Client()
        self.interval = interval
        self._values = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.client.path or self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._run, name='pisugar-read', daemon=True)
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            values = self.client.read()
            with self._lock:
                self._values = values
            if self._stop.wait(self.interval):
                break

    def snapshot(self):
        with self._lock:
            return dict(self._values)

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
