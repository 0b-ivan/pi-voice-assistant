"""Night reports, separate from personal memory and never added to LLM context."""
import base64
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.parse

MAX_FINDINGS = 128
MAX_REPORT = 512 * 1024
HEADER_LIMIT = 12000
OPEN = ('open', 'decision', 'working')


def clean(value, limit=300):
    return re.sub(r'\s+', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def source_url(value):
    try:
        url = urllib.parse.urlsplit(value)
        return value[:500] if url.scheme == 'https' and url.hostname and not url.username else ''
    except (ValueError, TypeError):
        return ''


def finding(scope, key, title, advice, category='operation', priority=2, sources=(), **extra):
    ident = hashlib.sha256(f'{scope}:{category}:{key}'.encode()).hexdigest()[:24]
    return dict(id=ident, scope=scope, category=category, key=key, title=title,
                advice=advice, priority=priority, sources=list(sources), **extra)


def sanitize_finding(item):
    if not isinstance(item, dict) or not re.fullmatch(r'[a-f0-9]{24}', str(item.get('id', ''))):
        return None
    if item.get('category') not in ('security', 'operation', 'improvement', 'training'):
        return None
    scope = clean(item.get('scope'), 80)
    if not re.fullmatch(r'[\w:.-]{1,80}', scope):
        return None
    priority = item.get('priority')
    if type(priority) is not int or not 0 <= priority <= 3:
        return None
    out = {key: clean(item.get(key), limit) for key, limit in (
        ('id', 24), ('scope', 80), ('category', 20), ('key', 180), ('title', 220),
        ('advice', 500), ('version', 120), ('fixed', 120), ('assessment', 80), ('note', 600))}
    if not out['title']:
        return None
    out['priority'] = priority
    if item.get('known_exploited') is True:
        out['known_exploited'] = True
    sources = item.get('sources', [])
    out['sources'] = [url for value in sources[:5] if (url := source_url(value))] \
        if isinstance(sources, list) else []
    out['status'] = item.get('status') if item.get('status') in (*OPEN, 'resolved', 'dismissed') else 'open'
    for key in ('first_seen', 'last_seen', 'observed_at'):
        value = item.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            out[key] = value
    return out


def sanitize_report(data):
    if not isinstance(data, dict):
        raise ValueError('report must be an object')
    run_id = data.get('id', '')
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('invalid run id')
    if data.get('state') not in ('done', 'partial', 'cancelled', 'failed'):
        raise ValueError('invalid state')
    items = data.get('findings', [])
    if not isinstance(items, list) or len(items) > MAX_FINDINGS:
        raise ValueError('too many findings')
    findings = [sanitize_finding(item) for item in items]
    if any(item is None for item in findings):
        raise ValueError('invalid finding')
    at = data.get('at')
    if type(at) not in (int, float) or not math.isfinite(at) or at < 0:
        raise ValueError('invalid timestamp')
    checked = data.get('checked', [])
    limits = data.get('limits', [])
    if not isinstance(checked, list) or not isinstance(limits, list):
        raise ValueError('invalid coverage')
    return dict(id=run_id, at=at, night=clean(data.get('night'), 10), state=data['state'],
                findings=findings, checked=[clean(s, 80) for s in checked[:32]],
                limits=[clean(s, 200) for s in limits[:24]])


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(prefix='.night-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(data, out, ensure_ascii=False, separators=(',', ':'))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Store:
    """Only the Pi owns this store; absent/unreadable core is never a clean result."""
    def __init__(self, core, clock=time.time):
        self.core, self.clock = core, clock
        self.lock = threading.RLock()
        self.error = None

    @property
    def root(self):
        return self.core.root / 'nightwatch'

    def load(self):
        with self.lock:
            if not self.core.present():
                self.error = 'Speicherkern nicht verfügbar.'
                return None
            path = self.root / 'findings.json'
            try:
                if not path.exists():
                    self.error = None
                    return dict(version=1, findings=[], last=None)
                if path.stat().st_size > MAX_REPORT:
                    raise ValueError('store too large')
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict) or not isinstance(data.get('findings'), list):
                    raise ValueError('invalid store')
                items = [sanitize_finding(item) for item in data['findings']]
                if any(item is None for item in items):
                    raise ValueError('invalid stored finding')
                data['findings'] = items
                if data.get('last') is not None:
                    data['last'] = sanitize_report(data['last'])
                self.error = None
                return data
            except (OSError, ValueError):
                self.error = 'Nachtberichte auf dem Speicherkern nicht lesbar.'
                return None  # retain the damaged file; never replace it with an empty registry

    def save(self, report):
        report = sanitize_report(report)
        with self.lock:
            data = self.load()
            if data is None:
                return False
            items = {item['id']: item for item in data['findings']}
            seen = set()
            for item in report['findings']:
                previous = items.get(item['id'], {})
                item = dict(item, first_seen=previous.get('first_seen', report['at']),
                            last_seen=report['at'])
                if previous.get('status') in ('decision', 'working', 'dismissed'):
                    item['status'] = previous['status']
                if previous.get('known_exploited') and not item.get('known_exploited'):
                    # Failure/absence of optional priority enrichment is not
                    # evidence that known exploitation has ceased.
                    item['known_exploited'] = True
                    item['priority'] = 0
                    item['note'] = previous.get('note', '')
                    item['sources'] = list(dict.fromkeys(previous['sources'] + item['sources']))[:5]
                items[item['id']] = item
                seen.add(item['id'])
            # Only fully checked scopes may close absent findings. Partial scans,
            # model suggestions and capped results never close older issues.
            for item in items.values():
                if item['id'] not in seen and item['scope'] in report['checked'] \
                        and item['status'] in OPEN:
                    item['status'] = 'resolved'
            values = sorted(items.values(), key=lambda x: (x['status'] not in OPEN,
                            x['priority'], not x.get('known_exploited', False),
                            x['category'] != 'security', -x.get('last_seen', 0)))
            if len(values) > 512 or len(json.dumps(values).encode()) > MAX_REPORT // 2:
                self.error = 'Nachtregister voll. Alte Einträge müssen archiviert werden.'
                return False  # do not silently drop open security findings
            try:
                if not self.core.present():
                    raise OSError('core removed')
                atomic_json(self.root / 'runs' / f"{report['id']}.json", report)
                atomic_json(self.root / 'findings.json', dict(version=1, findings=values, last=report))
                for old in sorted((self.root / 'runs').glob('*.json'),
                                  key=lambda p: p.stat().st_mtime, reverse=True)[90:]:
                    old.unlink()
            except OSError:
                self.error = 'Nachtbericht konnte nicht dauerhaft gespeichert werden.'
                return False
            return True

    def view(self):
        data = self.load()
        if data is None:
            return dict(error=self.error)
        items = [item for item in data['findings'] if item['status'] in OPEN]
        last = data.get('last')
        # Keep this header bounded; full citations/notes stay on the core.
        selected = items[:4]
        for category in ('training', 'improvement'):
            extra = next((item for item in items if item['category'] == category and item not in selected), None)
            if extra:
                selected.append(extra)
        selected += [item for item in items if item not in selected][:6 - len(selected)]
        compact = [{key: item[key] for key in ('id', 'scope', 'category', 'key', 'title',
                    'advice', 'priority', 'status')} for item in selected]
        for row, item in zip(compact, selected):
            if item.get('known_exploited'):
                row['known_exploited'] = True
        return dict(open=len(items), findings=compact, last=({key: last[key] for key in
                    ('at', 'state', 'night')} | dict(limits=last['limits'][:3]) if last else None))


def encode_view(view):
    raw = base64.b64encode(json.dumps(view, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    return raw if len(raw) <= HEADER_LIMIT else ''


def decode_view(raw):
    try:
        if not isinstance(raw, str) or len(raw) > HEADER_LIMIT:
            return None
        data = json.loads(base64.b64decode(raw, validate=True))
        if not isinstance(data, dict):
            return None
        if data.get('error'):
            return dict(error=clean(data['error']))
        count = data.get('open')
        if type(count) is not int or not 0 <= count <= 512:
            return None
        items = data.get('findings')
        if not isinstance(items, list) or len(items) > 6:
            return None
        items = [sanitize_finding(item) for item in items]
        if any(item is None for item in items):
            return None
        last = data.get('last')
        if last is not None:
            if not isinstance(last, dict) or last.get('state') not in ('done', 'partial', 'failed', 'cancelled'):
                return None
            limits = last.get('limits', [])
            if not isinstance(limits, list):
                return None
            last = dict(state=last['state'], night=clean(last.get('night'), 10),
                        limits=[clean(s, 160) for s in limits[:3]])
        return dict(open=count, findings=items, last=last)
    except (ValueError, TypeError, UnicodeError):
        return None


def intent(text):
    text = clean(text.lower()).strip(' .?!')
    if re.fullmatch(r'(?:bitte )?(?:was ist (?:noch )?offen|welche (?:punkte|probleme) sind offen)', text):
        return 'night_open'
    if re.fullmatch(r'(?:welche|was für) sicherheits(?:probleme|lücken|luecken) (?:sind|ist) (?:noch )?offen', text):
        return 'night_security'
    if re.fullmatch(r'(?:was hast du (?:heute |letzte )?nacht (?:geprüft|gefunden)|nacht ?bericht|nacht ?prüfung)', text):
        return 'night_report'
    if re.fullmatch(r'welche verbesserungen schlägst du vor', text):
        return 'night_improvements'
    return None


def answer(kind, view, brief=False):
    if view is None:
        return None if brief else 'Noch kein Nachtbericht verfügbar.'
    if view.get('denied'):
        return None if brief else 'Stimme nicht als Bediener erkannt. Nachtberichte nicht freigegeben.'
    if view.get('error'):
        return view['error']
    last = view.get('last')
    if not last:
        return None if brief else 'Noch keine Nachtprüfung auf dem Speicherkern abgelegt.'
    parts = ['Nachtprüfung abgeschlossen.' if last['state'] == 'done' else 'Nachtprüfung unvollständig.']
    if last.get('night'):
        parts.append(f"Stand {last['night']}.")
    findings = view.get('findings', [])
    if kind == 'night_security':
        findings = [item for item in findings if item['category'] == 'security']
    elif kind == 'night_improvements':
        findings = [item for item in findings if item['category'] in ('training', 'improvement')]
    count = view.get('open', 0)
    if kind == 'night_open' or brief:
        parts.append(f'{count} Punkte offen.' if count else 'Keine offenen Punkte im erfassten Bestand.')
    if not findings and kind == 'night_security':
        parts.append('Keine Sicherheitsbefunde in der abrufbaren Auswahl. Gesamtstand mit Was ist offen.')
    for item in findings[:3 if brief else 5]:
        parts.append(item['title'] + '.')
        if item.get('known_exploited'):
            parts.append('Bekannte Ausnutzung gemeldet; zuerst prüfen.')
        if not brief and item['advice']:
            parts.append(item['advice'])
    if last.get('limits'):
        parts.append('Einschränkung: ' + last['limits'][0])
    return ' '.join(parts)


def night_key(now, start=2, end=5):
    """Local-time night date, including windows crossing midnight and DST."""
    if start < end:
        return now.date().isoformat() if start <= now.hour < end else None
    if now.hour >= start:
        return now.date().isoformat()
    if now.hour < end:
        return (now.date() - datetime.timedelta(days=1)).isoformat()
    return None
