"""System and network watch: pending updates, WLAN signal, latency, DNS.

Everything runs in daemon threads and only reads: no root, no package
changes. Results are plain numbers that go into the status snapshot, the
alarms and the spoken "Netzwerk"/"Updates" answers.

Updates: ``apt-get -s upgrade`` (a simulation, works without root) counts
pending packages and those from a -security suite. It is only as fresh as
the package lists, which apt-daily refreshes when
APT::Periodic::Update-Package-Lists is enabled (see docs/memory.md), so the
age of the lists is reported too. The server (CT 107) answers the same
numbers on the authenticated GET /v1/status.
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

APT_LISTS = Path('/var/lib/apt/lists')
WIRELESS = Path('/proc/net/wireless')
INTERNET_HOST = ('1.1.1.1', 443)
DNS_NAME = 'openrouter.ai'


def pending_updates(run=subprocess.run, lists=APT_LISTS, now=None):
    """dict(pending, security, lists_age_days) or None if apt is unavailable."""
    try:
        result = run(['nice', '-n', '15', 'apt-get', '-s', '-o', 'Debug::NoLocking=1', 'upgrade'],
                     capture_output=True, text=True, timeout=300, check=False,
                     env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    installs = [line for line in result.stdout.splitlines() if line.startswith('Inst ')]
    security = sum(1 for line in installs if re.search(r'security', line, re.IGNORECASE))
    try:
        newest = max(p.stat().st_mtime for p in Path(lists).glob('*Release'))
        age = int(((now or time.time()) - newest) // 86400)
    except (OSError, ValueError):
        age = None
    return dict(pending=len(installs), security=security, lists_age_days=age)


def wifi_dbm(path=WIRELESS):
    try:
        for line in Path(path).read_text().splitlines()[2:]:
            fields = line.split()
            if fields and fields[0].rstrip(':').startswith('wl'):
                return int(float(fields[3].rstrip('.')))
    except (OSError, ValueError, IndexError):
        pass
    return None


def connect_ms(host, port, timeout=2.0, tries=2):
    """Fastest TCP connect time in ms, None if unreachable."""
    best = None
    for _ in range(tries):
        started = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                elapsed = int((time.monotonic() - started) * 1000)
        except OSError:
            continue
        best = elapsed if best is None else min(best, elapsed)
    return best


def dns_ok(name=DNS_NAME):
    try:
        return bool(socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM))
    except (OSError, UnicodeError):
        return False


def lan_target(env=None):
    """Host and port of the first http:// server URL (the LAN path)."""
    env = os.environ if env is None else env
    for url in env.get('ASSISTANT_BASE_URL', '').split(','):
        parts = urllib.parse.urlsplit(url.strip())
        if parts.scheme == 'http' and parts.hostname:
            return parts.hostname, parts.port or 80
    return None


def network_snapshot():
    """One measurement round (takes up to a few seconds)."""
    result = dict(wifi_dbm=wifi_dbm(), net_ms=connect_ms(*INTERNET_HOST),
                  dns='ok' if dns_ok() else 'fail')
    target = lan_target()
    if target:
        result['lan_ms'] = connect_ms(*target, timeout=1.0)
    return result


def server_status(env=None, opener=None):
    """Authenticated GET /v1/status of the first configured server URL."""
    import urllib.request
    env = os.environ if env is None else env
    urls = [u.strip().rstrip('/') for u in env.get('ASSISTANT_BASE_URL', '').split(',') if u.strip()]
    token = env.get('ASSISTANT_TOKEN', '').strip()
    if not urls or not token:
        return None
    from remote_turn import USER_AGENT
    request = urllib.request.Request(f'{urls[0]}/v1/status', headers={
        'Authorization': f'Bearer {token}', 'User-Agent': USER_AGENT})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=10) as response:
            data = json.loads(response.read())
    except (OSError, ValueError):
        return None
    updates = data.get('updates') if isinstance(data, dict) else None
    if not isinstance(updates, dict):
        return None
    return {k: updates[k] for k in ('pending', 'security', 'lists_age_days')
            if isinstance(updates.get(k), int)}


class Watch:
    """Calls ``measure`` every ``interval`` seconds in a daemon thread."""

    def __init__(self, measure, interval, first_delay=0.0, name='watch'):
        self.measure, self.interval, self.first_delay = measure, interval, first_delay
        self.result, self.updated_at = None, None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        time.sleep(self.first_delay)
        while True:
            try:
                self.result = self.measure()
                self.updated_at = time.time()
            except Exception:  # a broken probe must never stop the assistant
                self.result = None
            time.sleep(self.interval)


def update_watch(interval=6 * 3600):
    def measure():
        return dict(pi=pending_updates(), server=server_status())
    return Watch(measure, interval, first_delay=120.0, name='update-watch')


def network_watch(interval=30.0):
    return Watch(network_snapshot, interval, first_delay=5.0, name='network-watch')


def snapshot_fields(network=None, updates=None):
    """Watch results as status-snapshot fields (sanitized again by the caller)."""
    fields = {}
    for key in ('wifi_dbm', 'net_ms', 'lan_ms', 'dns'):
        if network and network.get(key) is not None:
            fields[key] = network[key]
    pi = (updates or {}).get('pi') or {}
    server = (updates or {}).get('server') or {}
    for prefix, source in (('updates', pi), ('server_updates', server)):
        if isinstance(source.get('pending'), int):
            fields[prefix] = source['pending']
            fields[f'{prefix}_security'] = source.get('security', 0)
    if isinstance(pi.get('lists_age_days'), int):
        fields['apt_age_days'] = pi['lists_age_days']
    return fields
