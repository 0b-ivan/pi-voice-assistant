"""Pi scheduling and acknowledged storage. tick() never performs network work."""
import array
import base64
import datetime
import hashlib
import ipaddress
import json
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request
import uuid
import wave
import zoneinfo

import nightwatch as nw
from security_audit import inventory, NoRedirect
from nightwatch_training import dataset_plan


def samples(core):
    manifest, probe = [], ''
    if not core.present():
        return dict(valid=0, manifest=[]), probe
    for path in sorted((core.root / 'voice' / 'wake').glob('*.wav'))[-200:]:
        try:
            if path.stat().st_size > 128 * 1024:
                continue
            with wave.open(str(path), 'rb') as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
                    continue
                pcm = audio.readframes(32001)
            if not 16000 <= len(pcm) <= 64000 or len(pcm) % 2:
                continue
            values = array.array('h', pcm)
            audible = sum(abs(v) > 200 for v in values)
            clipped = sum(abs(v) >= 32700 for v in values)
            if audible < len(values) // 100 or clipped > len(values) // 20:
                continue
            manifest.append(dict(sha256=hashlib.sha256(pcm).hexdigest(), frames=len(values), file=path.name))
            probe = base64.b64encode(pcm).decode()
        except (OSError, ValueError, wave.Error, EOFError):
            continue
    return dataset_plan(manifest), probe


class Client:
    def __init__(self, core, remote, start=2, end=5, idle_seconds=1200, opener=None,
                 clock=None, log=None):
        self.core, self.remote, self.store = core, remote, nw.Store(core)
        self.start, self.end, self.idle_seconds = start, end, idle_seconds
        self.clock = clock or (lambda: datetime.datetime.now(zoneinfo.ZoneInfo('Europe/Berlin')))
        self.log = log or (lambda *args, **kwargs: None)
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.abort = threading.Event()
        self.thread, self.attempted, self.last_check = None, None, 0
        self.base = None
        for url in remote.base_urls:
            parts = urllib.parse.urlsplit(url)
            try:
                address = ipaddress.ip_address(parts.hostname)
            except ValueError:
                continue
            if parts.scheme in ('http', 'https') and not parts.username \
                    and (address.is_private or address.is_loopback):
                self.base = url.rstrip('/')
                break

    def _rpc(self, body=None, ident=None):
        if not self.base:
            raise OSError('no literal LAN endpoint configured')
        url = self.base + '/v1/nightwatch'
        if ident is not None:
            url += '?id=' + urllib.parse.quote(ident)
        raw = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=raw, headers={
            'Authorization': 'Bearer ' + self.remote.token, 'User-Agent': 'Proximus-Nightwatch/1',
            **({'Content-Type': 'application/json'} if body is not None else {})})
        with self.opener.open(request, timeout=5) as response:
            raw = response.read(nw.MAX_REPORT + 1)
            if len(raw) > nw.MAX_REPORT:
                raise ValueError('night response too large')
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('invalid response')
        return data

    def _commit(self, report):
        report = nw.sanitize_report(report)
        if not self.store.save(report):
            return False
        self.log('nightwatch', state=report['state'], stored=True)
        # If ack fails, the server keeps its bounded spool. A later run retries
        # the identical report; saving it again does not duplicate its findings.
        try:
            self._rpc(dict(action='ack', id=report['id']))
        except (OSError, ValueError):
            pass
        return True

    def tick(self, quiet_seconds, eligible, snapshot, now=None):
        if not eligible:
            self.abort.set()
            return
        if self.thread is not None and self.thread.is_alive():
            return
        monotonic = time.monotonic()
        if monotonic - self.last_check < 30:
            return
        self.last_check = monotonic
        now = now or self.clock()
        night = nw.night_key(now, self.start, self.end)
        if night is None or night == self.attempted or quiet_seconds < self.idle_seconds:
            return
        if not self.base or not self.core.present():
            return
        data = self.store.load()
        if data is None or (data.get('last') or {}).get('night') == night:
            return
        self.attempted = night
        self.abort.clear()
        self.thread = threading.Thread(target=self._run, args=(night, snapshot),
                                       name='nightwatch-client', daemon=True)
        self.thread.start()

    def _run(self, night, snapshot):
        ident = uuid.uuid4().hex
        try:
            pending = self._rpc()
            if pending.get('report'):
                if not self._commit(pending['report']):
                    return
                if pending['report'].get('night') == night:
                    return
            if pending.get('running'):
                # Previous Pi process may have started this run; adopt its id.
                ident = pending['id']
            else:
                sample_data, probe = samples(self.core)
                if self.abort.is_set():
                    return
                if self.core.present():
                    nw.atomic_json(self.store.root / 'training' / 'manifest.json', sample_data)
                inv = inventory('pi')
                if self.abort.is_set():
                    return
                self._rpc(dict(action='start', id=ident, night=night, inventory=inv,
                               snapshot=snapshot, samples={key: sample_data.get(key, 0) for key in
                                    ('valid', 'sessions', 'duplicates', 'positive_split_ready')}, probe=probe))
            deadline = time.monotonic() + 1250
            while time.monotonic() < deadline:
                if self.abort.wait(1):
                    self._rpc(dict(action='cancel', id=ident))
                    # The result stays in the server spool until the next night.
                    return
                status = self._rpc(ident=ident)
                if status.get('report') is not None:
                    self._commit(status['report'])
                    return
            raise OSError('night run deadline exceeded')
        except (OSError, ValueError):
            report = dict(id=uuid.uuid4().hex, at=time.time(), night=night, state='failed',
                          findings=[], checked=[], limits=['Nachtprüfung oder Verbindung zum Server fehlgeschlagen'])
            self.store.save(report)
            self.log('nightwatch', state='failed', stored=self.store.error is None)

    def header(self):
        return nw.encode_view(self.store.view())
