"""Is CT 107 reachable? Polled in a daemon thread by the display and pi-ptt.

GET /health of the first ASSISTANT_BASE_URL needs no token and costs a few
milliseconds; a slow or dead server never blocks the caller's loop.
"""
import json
import os
from pathlib import Path
import threading
import time

ENV_FILE = Path('/etc/pi-voice-assistant.env')
SERVER_PROBE_INTERVAL_SECONDS = 10.0
# The Pi's WLAN (power save on) answers a LAN ping in 2 ms, now and then only
# after 150 ms, and /health in up to 0.85 s: 0.5 s took a slow answer for an
# outage. The probe runs in its own thread, so waiting longer blocks nothing.
SERVER_PROBE_TIMEOUT_SECONDS = 2.0
# A reachable server counts as down only after this many failed probes in a
# row, the next one SERVER_PROBE_RETRY_SECONDS after a miss.
SERVER_DOWN_AFTER = 2
SERVER_PROBE_RETRY_SECONDS = 2.0


def load_env(path=ENV_FILE):
    values = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def server_state(env=None):
    """'off' without ASSISTANT_BASE_URL, else 'ok'/'down' from GET /health."""
    import urllib.request
    env = (dict(os.environ) if os.environ.get('ASSISTANT_BASE_URL') else load_env()) \
        if env is None else env
    urls = [u.strip().rstrip('/') for u in env.get('ASSISTANT_BASE_URL', '').split(',')]
    urls = [u for u in urls if u]
    if not urls:
        return 'off'
    try:
        with urllib.request.urlopen(urls[0] + '/health',
                                    timeout=SERVER_PROBE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(256))
        return 'ok' if isinstance(payload, dict) and payload.get('ready') is True else 'down'
    except (OSError, ValueError):
        return 'down'


class ServerProbe:
    """Polls /health in a daemon thread so a slow server never stalls frames."""
    def __init__(self, interval=SERVER_PROBE_INTERVAL_SECONDS, probe=None):
        import threading
        self.interval = interval
        self.probe = probe or server_state
        self.state = None
        self.misses = 0         # failed probes in a row while the state is still 'ok'
        self._thread = threading.Thread(target=self._run, name='server-probe', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def step(self):
        """One probe; returns the seconds until the next."""
        state = self.probe()
        if state == 'down' and self.state == 'ok':
            self.misses += 1
            if self.misses < SERVER_DOWN_AFTER:
                return SERVER_PROBE_RETRY_SECONDS  # one late answer is no outage
        self.misses = 0
        self.state = state
        return self.interval

    def _run(self):
        while True:
            time.sleep(self.step())




def network_up():
    """True if a non-loopback IPv4 address is up, None if it cannot be told."""
    import shutil
    import subprocess
    ip = shutil.which('ip')
    if not ip:
        return None
    try:
        result = subprocess.run([ip, '-4', '-brief', 'address', 'show', 'up'],
                                capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return any(line.split()[0] != 'lo' and '/' in line
               for line in result.stdout.splitlines() if line.split())


INTERNET_TARGETS = (('openrouter.ai', 443), ('1.1.1.1', 443))


def internet_up(targets=INTERNET_TARGETS, timeout=2.0):
    """True if any well-known host accepts a TCP connection (DNS included)."""
    import socket
    for host, port in targets:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


class InternetProbe(ServerProbe):
    """Same daemon-thread polling as the server probe; state True/False."""

    def __init__(self, interval=30.0, probe=None):
        super().__init__(interval=interval, probe=probe or internet_up)
