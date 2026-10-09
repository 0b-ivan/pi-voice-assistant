"""Bluetooth speakers through bluetoothctl (BlueZ) and bluez-alsa.

pi-ptt needs the group bluetooth (D-Bus policy of BlueZ) and bluez-alsa
running (deploy/install-bluetooth.sh). Only audio sinks are offered. A paired
and trusted speaker is reconnected in the background; while it is connected,
audio_output sends all playback there.
"""
import re
import subprocess
import threading
import time

AUDIO_SINK = '0000110b-0000-1000-8000-00805f9b34fb'   # A2DP sink UUID
SCAN_SECONDS = 12
CONNECTED = "Bluetooth-Lautsprecher verbunden. Ausgabe über Bluetooth."
DISCONNECTED = "Bluetooth-Lautsprecher getrennt. Ausgabe über den eingebauten Lautsprecher."
SCANNING = "Suche Lautsprecher. Lautsprecher in den Kopplungsmodus versetzen."
CONNECTING = "Verbinde Lautsprecher."
NONE_FOUND = "Kein Lautsprecher gefunden."
NONE_PAIRED = "Kein Lautsprecher gekoppelt."
PAIRING = "Kopplung läuft."
PAIR_FAILED = "Kopplung fehlgeschlagen. Lautsprecher im Kopplungsmodus?"
FORGOTTEN = "Lautsprecher entfernt."
UNAVAILABLE = "Bluetooth nicht verfügbar."
BUSY = "Bluetooth ist beschäftigt. Bitte warten."


def phrases():
    return [CONNECTED, DISCONNECTED, SCANNING, CONNECTING, NONE_FOUND, NONE_PAIRED, PAIRING, PAIR_FAILED,
            FORGOTTEN, UNAVAILABLE, BUSY]
_MAC = re.compile(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$')


def run(*args, timeout=20, runner=subprocess.run):
    """bluetoothctl output, or '' when it is missing or fails."""
    try:
        result = runner(['bluetoothctl', *args], capture_output=True, text=True,
                        timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ''
    return result.stdout or ''


def valid(mac):
    return bool(_MAC.match(str(mac)))


def devices(output):
    """[(mac, name)] from 'bluetoothctl devices' output."""
    found = []
    for line in output.splitlines():
        parts = line.strip().split(' ', 2)
        if len(parts) >= 2 and parts[0] == 'Device' and valid(parts[1]):
            found.append((parts[1], parts[2].strip() if len(parts) > 2 else parts[1]))
    return found


def info(output):
    """Flags of interest from 'bluetoothctl info <mac>'."""
    text = output.lower()
    return dict(paired='paired: yes' in text, trusted='trusted: yes' in text,
                connected='connected: yes' in text, sink=AUDIO_SINK in text)


class Bluetooth:
    def __init__(self, run=run, clock=time.monotonic):
        self.run = run
        self.clock = clock
        self.lock = threading.Lock()
        self.connected = None        # (mac, name) of the active speaker
        self.scanning = False
        self.found = []              # last scan: [(mac, name)] audio sinks

    def available(self):
        return 'Controller' in self.run('list', timeout=5)

    def speakers(self, paired_only=True):
        """Known audio sinks: [(mac, name, flags)]."""
        listing = (self.run('devices', 'Paired', timeout=10) if paired_only
                   else self.run('devices', timeout=10))
        result = []
        for mac, name in devices(listing):
            flags = info(self.run('info', mac, timeout=10))
            if flags['sink'] or not paired_only:
                result.append((mac, name, flags))
        return result

    def _sinks(self, cache):
        """Audio sinks known to BlueZ now: [(mac, name, paired)]."""
        sinks = []
        for mac, name in devices(self.run('devices', timeout=10)):
            if mac not in cache:
                cache[mac] = info(self.run('info', mac, timeout=10))
            if cache[mac]['sink']:
                sinks.append((mac, name, cache[mac]['paired']))
        return sinks

    def scan(self, seconds=SCAN_SECONDS, on_update=None, poll=2.0, popen=subprocess.Popen,
             sleep=time.sleep):
        """Discover speakers for ``seconds``; ``on_update(list)`` gets the growing
        list every ``poll`` seconds. Returns [(mac, name, paired)]."""
        with self.lock:
            self.scanning = True
        cache, sinks, process = {}, [], None
        try:
            self.run('power', 'on', timeout=10)
            try:
                process = popen(['bluetoothctl', '--timeout', str(seconds), 'scan', 'on'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                process = None
            deadline = self.clock() + seconds
            while True:
                sinks = self._sinks(cache)
                if on_update is not None:
                    on_update(sinks)
                if self.clock() >= deadline or (process is not None and process.poll() is not None):
                    break
                sleep(poll)
            self.found = sinks
            return sinks
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
            with self.lock:
                self.scanning = False

    def pair(self, mac, name=None):
        """Pair (if needed), trust and connect a speaker; True on success."""
        if not valid(mac):
            return False
        if not info(self.run('info', mac, timeout=10))['paired']:
            self.run('pair', mac, timeout=30)
        self.run('trust', mac, timeout=10)
        return self.connect(mac, name)

    def connect(self, mac, name=None):
        if not valid(mac):
            return False
        self.run('connect', mac, timeout=30)
        flags = info(self.run('info', mac, timeout=10))
        if flags['connected']:
            self.connected = (mac, name or mac)
            return True
        return False

    def disconnect(self):
        if self.connected:
            self.run('disconnect', self.connected[0], timeout=15)
        self.connected = None

    def forget(self, mac):
        if not valid(mac):
            return False
        if self.connected and self.connected[0] == mac:
            self.connected = None
        return 'removed' in self.run('remove', mac, timeout=15).lower()

    def refresh(self, reconnect=True):
        """Background check: is a paired speaker connected? Reconnect if wanted."""
        speakers = self.speakers()
        live = next(((mac, name) for mac, name, f in speakers if f['connected']), None)
        if live is None and reconnect:
            for mac, name, flags in speakers:
                if flags['trusted'] and self.connect(mac, name):
                    return self.connected
        self.connected = live
        return live
