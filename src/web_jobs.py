"""Dauerhaftes Archiv der Web-Diagnoseaufträge auf CT 107.

Das Webgespräch selbst bleibt flüchtig (src/web_context.py). Hier liegt nur,
was ein Auftrag belegt hat: Kennung, Zeitraum, Status, Zeiten und die
deterministische Zusammenfassung. Keine Fragen, Antworten, Logauszüge,
Modelltexte oder Browserkennungen. Nach einem Neustart findet nur die
vollständige 24-stellige Kennung einen Eintrag: Sie ist eine Zufallszahl
(96 Bit), die ausschließlich dem auslösenden Browser angezeigt wurde.
"""
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

DAYS = 7
MAX_JOBS = 200
MAX_BYTES = 256 * 1024
FINAL = frozenset({'abgeschlossen', 'fehlgeschlagen', 'abgebrochen'})
FIELDS = ('id', 'period', 'status', 'created', 'started', 'finished')


def default_path(env):
    """SERVITOR_DIAGNOSE_ARCHIVE, sonst systemds StateDirectory, sonst RAM."""
    explicit = env.get('SERVITOR_DIAGNOSE_ARCHIVE', '').strip()
    if explicit:
        return None if explicit.lower() == 'off' else Path(explicit)
    state = env.get('STATE_DIRECTORY', '').split(':')[0].strip()
    return Path(state) / 'diagnose-jobs.json' if state else None


def _clean(item):
    if not isinstance(item, dict) or not re.fullmatch(r'[0-9a-f]{24}', str(item.get('id'))):
        return None
    record = {key: item.get(key) for key in FIELDS}
    if record['status'] not in FINAL | {'gestartet', 'läuft'}:
        return None
    for key in ('created', 'started', 'finished'):
        if record[key] is not None and not isinstance(record[key], (int, float)):
            return None
    if not isinstance(record['created'], (int, float)):
        return None
    record['period'] = str(record['period'] or '')[:80]
    summary = item.get('summary')
    record['summary'] = summary[:2000] if isinstance(summary, str) else None
    record['interrupted'] = bool(item.get('interrupted'))
    return record


class JobArchive:
    def __init__(self, path=None, clock=time.time, days=DAYS, log=None):
        self.path = Path(path) if path else None
        self.clock, self.days = clock, days
        self.log = log or (lambda message: None)
        self.lock = threading.Lock()
        self.jobs = {}
        self._load()

    def _load(self):
        if not self.path:
            return
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('zu groß')
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self.log(f'Diagnosearchiv unlesbar, beginne leer: {type(exc).__name__}')
            return
        changed = False
        for item in data.get('jobs', []) if isinstance(data, dict) else []:
            record = _clean(item)
            if record is None:
                changed = True
                continue
            if record['status'] not in FINAL:
                # Der Arbeitsthread ist mit dem alten Prozess beendet worden.
                record.update(status='abgebrochen', interrupted=True,
                              finished=record['finished'] or self.clock())
                changed = True
            self.jobs[record['id']] = record
        if self._prune() or changed:
            self._save()

    def _prune(self):
        cutoff = self.clock() - self.days * 86400
        before = len(self.jobs)
        recent = sorted((j for j in self.jobs.values() if j['created'] >= cutoff),
                        key=lambda j: j['created'])[-MAX_JOBS:]
        self.jobs = {j['id']: j for j in recent}
        return len(self.jobs) != before

    def _save(self):
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(prefix='.diagnose-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as out:
                    json.dump(dict(version=1, jobs=list(self.jobs.values())), out,
                              ensure_ascii=False, separators=(',', ':'))
                    out.flush()
                    os.fsync(out.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except OSError as exc:
            # Ohne Archiv bleibt der flüchtige Auftrag gültig; nur ein Neustart
            # verliert ihn dann wieder, wie vor diesem Archiv.
            self.log(f'Diagnosearchiv nicht schreibbar: {type(exc).__name__}')

    def record(self, job):
        """Übernimmt den aktuellen Stand eines Auftrags aus dem Webgespräch."""
        report = job.get('report')
        item = dict(job, summary=report.get('summary')
                    if isinstance(report, dict) else None)
        record = _clean(item)
        if record is None:
            return
        with self.lock:
            self.jobs[record['id']] = record
            self._prune()
            self._save()

    def get(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{24}', job_id):
            return None
        with self.lock:
            record = self.jobs.get(job_id)
            if record and record['created'] < self.clock() - self.days * 86400:
                return None
            return dict(record) if record else None
