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
SERVER_PROBE_TIMEOUT_SECONDS = 0.5


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
        self._thread = threading.Thread(target=self._run, name='server-probe', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        while True:
            self.state = self.probe()
            time.sleep(self.interval)




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
