"""Systemwache, step 1: CT 107 watches Uptime Kuma through its public status pages.

Concept: docs/concepts/proximus-systemwache/KONZEPT.md. Every minute a daemon
thread on the server reads, per status page, ``/api/status-page/<slug>``
(groups, monitors) and ``/api/status-page/heartbeat/<slug>`` (recent beats,
24 h uptime). No key is needed; only monitors on a page are visible.

Each poll gives a normalized state per monitor. Compared with the confirmed
state it turns into events (new, gone, down, up, maintenance, unstable,
certificate, page unreachable/back), written to an event journal on CT 107.
Against noise: a change counts only after two polls in a row, more than
three changes per hour become one "unstable" event, PENDING (Kuma's retry
state) is no change at all.

Everything from Uptime Kuma is foreign data: monitor names are cut to a
plain character set before they are stored or spoken, error messages stay
in the journal and never reach a model or a command.

Configuration (``/etc/servitor-voice.env``): ``SERVITOR_KUMA_URL`` (e.g.
https://uptime.obivan.org) and ``SERVITOR_KUMA_PAGES`` (``cloud,vault``).
Without them the watch is off.
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

INTERVAL = 60.0               # seconds between polls
FIRST_DELAY = 20.0
TIMEOUT = 10.0                # per request
MAX_BODY = 2 * 1024 * 1024    # per response
CONFIRM = 2                   # polls in a row before a change counts
FLAP_WINDOW = 3600.0
FLAP_CHANGES = 3              # more confirmed changes per hour: "unstable"
CERT_DAYS = 14                # certificate warning below this many days
KEEP_EVENTS = 7 * 24 * 3600.0         # in memory, for answers
JOURNAL_DAYS = 90
JOURNAL_BYTES = 20 * 1024 * 1024
STATE_DIR = Path(os.environ.get('SERVITOR_WATCH_DIR', '/var/lib/servitor-voice/watch'))
BRIEFING_ITEMS = 2
ANSWER_ITEMS = 4
NIGHT = 12 * 3600.0           # recoveries this recent go into the morning litany

DOWN, UP, PENDING, MAINTENANCE = 0, 1, 2, 3
# kind: severity (0 critical, 1 fault, 2 notice, 3 recovery)
KINDS = {'down': 1, 'unreachable': 1, 'unstable': 1, 'cert': 2, 'new': 2, 'gone': 2,
         'maintenance': 2, 'up': 3, 'reachable': 3}

_NAME = re.compile(r"[^\w .:/()&+,'-]+")
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


def clean_name(value, limit=60):
    """A monitor or page name as plain text: no control or markup characters."""
    text = ' '.join(_NAME.sub(' ', str(value or '')).split())
    return text[:limit].strip() or '?'


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if low <= value <= high else None


def beat_time(text):
    """Kuma's heartbeat time ('2026-10-15 20:02:14', UTC) as epoch seconds."""
    try:
        when = datetime.datetime.strptime(str(text)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return when.replace(tzinfo=datetime.timezone.utc).timestamp()


def fetch_json(url, timeout=TIMEOUT, opener=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={'Accept': 'application/json',
                                                   'User-Agent': 'proximus-systemwache/1'})
    with opener(request, timeout=timeout) as response:
        body = response.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError('response too large')
    return json.loads(body)


def parse_page(slug, page, beats):
    """Monitors of one status page: {id: dict(name, page, group, type, status,
    at, ping, up24, cert_days, msg)}. ``status`` is the last beat (None: none yet)."""
    if not isinstance(page, dict) or not isinstance(beats, dict):
        raise ValueError('unexpected status page format')
    beat_lists = beats.get('heartbeatList') or {}
    uptimes = beats.get('uptimeList') or {}
    monitors = {}
    for group in page.get('publicGroupList') or []:
        if not isinstance(group, dict):
            continue
        for monitor in group.get('monitorList') or []:
            if not isinstance(monitor, dict) or _number(monitor.get('id'), 1, 10 ** 9) is None:
                continue
            ident = int(monitor['id'])
            history = beat_lists.get(str(ident)) or []
            last = history[-1] if history and isinstance(history[-1], dict) else {}
            status = last.get('status')
            monitors[ident] = dict(
                name=clean_name(monitor.get('name')), page=slug,
                group=clean_name(group.get('name')),
                type=clean_name(monitor.get('type'), 20),
                status=status if status in (DOWN, UP, PENDING, MAINTENANCE) else None,
                at=beat_time(last.get('time')),
                ping=_number(last.get('ping'), 0, 10 ** 6),
                up24=_number(uptimes.get(f'{ident}_24'), 0, 1),
                cert_days=(_number(monitor.get('certExpiryDaysRemaining'), -10 ** 4, 10 ** 4)
                           if monitor.get('validCert') is not None else None),
                # Foreign text: kept for the journal only, never spoken or prompted.
                msg=str(last.get('msg') or '')[:200])
    return monitors


def read_pages(base, slugs, fetch=fetch_json):
    """{slug: monitors or None (unreachable)} for every page."""
    out = {}
    for slug in slugs:
        quoted = urllib.parse.quote(slug)
        try:
            out[slug] = parse_page(slug, fetch(f'{base}/api/status-page/{quoted}'),
                                   fetch(f'{base}/api/status-page/heartbeat/{quoted}'))
        except (OSError, ValueError, TypeError, AttributeError):
            out[slug] = None
    return out


def _at(line):
    try:
        return float(json.loads(line).get('at') or 0)
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _level(status):
    """What counts as a change: PENDING is Kuma retrying, not a new state."""
    return {DOWN: 'down', UP: 'up', MAINTENANCE: 'maintenance'}.get(status)


class KumaWatch:
    """Confirmed state, pending changes and the event journal of one Kuma instance."""

    def __init__(self, base, slugs, directory=STATE_DIR, fetch=fetch_json,
                 clock=time.time, sleep=time.sleep, report=None):
        self.base = base.rstrip('/')
        self.slugs = [s for s in slugs if _SLUG.match(s)]
        self.directory = Path(directory)
        self.fetch, self.clock, self.sleep, self.report = fetch, clock, sleep, report
        self.monitors = {}      # id -> confirmed monitor (with 'level', 'since')
        self.pages = {}         # slug -> dict(ok, since)
        self.pending = {}       # key -> (level, polls)
        self.changes = {}       # id -> [confirmed change times]
        self.events = []
        self.checked_at = None
        self._trimmed_at = 0.0
        self._lock = threading.Lock()
        self._load()

    # --- persistence -------------------------------------------------------
    @property
    def _state_path(self):
        return self.directory / 'state' / 'kuma.json'

    @property
    def _journal_path(self):
        return self.directory / 'events.jsonl'

    def _load(self):
        try:
            data = json.loads(self._state_path.read_text())
            self.monitors = {int(k): v for k, v in data['monitors'].items()}
            self.pages = dict(data['pages'])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.monitors, self.pages = {}, {}
        cutoff = self.clock() - KEEP_EVENTS
        try:
            with self._journal_path.open() as journal:
                for line in journal:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get('source') == 'kuma' \
                            and (event.get('at') or 0) >= cutoff:
                        self.events.append(event)
        except OSError:
            pass

    def _save(self, new_events):
        try:
            (self.directory / 'state').mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self._state_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(monitors=self.monitors, pages=self.pages)))
            temporary.replace(self._state_path)
            if new_events:
                with self._journal_path.open('a') as journal:
                    for event in new_events:
                        journal.write(json.dumps(event, ensure_ascii=False) + '\n')
                self._trim()
        except OSError as exc:
            print(json.dumps(dict(event='watch_save_failed', error=str(exc))), flush=True)

    def _trim(self):
        """Keep the journal under 90 days and 20 MB (oldest lines go first)."""
        path, now = self._journal_path, self.clock()
        if path.stat().st_size <= JOURNAL_BYTES and now - self._trimmed_at < 86400:
            return   # the age limit is applied once a day
        self._trimmed_at = now
        cutoff = now - JOURNAL_DAYS * 86400
        lines = [line for line in path.read_text().splitlines() if _at(line) >= cutoff]
        while lines and sum(len(line) + 1 for line in lines) > JOURNAL_BYTES // 2:
            lines = lines[len(lines) // 4 or 1:]
        temporary = path.with_suffix('.tmp')
        temporary.write_text(''.join(line + '\n' for line in lines))
        temporary.replace(path)

    # --- one poll ----------------------------------------------------------
    def _event(self, out, now, kind, monitor=None, **fields):
        event = dict(at=now, source='kuma', kind=kind, severity=KINDS[kind], **fields)
        if monitor is not None:
            event.update(id=monitor['id'], name=monitor['name'], page=monitor['page'])
        out.append(event)

    def _confirmed(self, key, level):
        """True once ``level`` was seen CONFIRM polls in a row for ``key``."""
        previous, polls = self.pending.get(key, (None, 0))
        polls = polls + 1 if previous == level else 1
        if polls >= CONFIRM:
            self.pending.pop(key, None)
            return True
        self.pending[key] = (level, polls)
        return False

    def _flapping(self, ident, now):
        times = [t for t in self.changes.get(ident, []) if now - t < FLAP_WINDOW] + [now]
        self.changes[ident] = times
        return len(times) > FLAP_CHANGES

    def poll(self):
        """One round: read all pages, update the confirmed state, return new events."""
        with self._lock:
            now = self.clock()
            seen = read_pages(self.base, self.slugs, self.fetch)
            first = not self.monitors and not self.pages
            new = []
            for slug, monitors in seen.items():
                page = self.pages.get(slug)
                ok = monitors is not None
                if page is None:
                    self.pages[slug] = dict(ok=ok, since=now)
                elif page['ok'] != ok and self._confirmed(('page', slug), ok):
                    self.pages[slug] = dict(ok=ok, since=now)
                    self._event(new, now, 'reachable' if ok else 'unreachable', page=slug,
                                dur=round(now - page['since']) if ok else None)
                elif page['ok'] == ok:
                    self.pending.pop(('page', slug), None)
                if not ok:
                    continue
                for ident, current in monitors.items():
                    self._update(new, now, ident, dict(current, id=ident), first)
                for ident in [i for i, m in self.monitors.items()
                              if m['page'] == slug and i not in monitors]:
                    if self._confirmed(('gone', ident), True):
                        self._event(new, now, 'gone', self.monitors.pop(ident))
            for slug in [s for s in self.pages if s not in seen]:
                del self.pages[slug]   # page no longer configured
            self.checked_at = now
            self.events = [e for e in self.events + new if now - e['at'] < KEEP_EVENTS]
            self._save(new)
            if new and self.report:
                self.report(new)
            return new

    def _update(self, new, now, ident, current, first):
        self.pending.pop(('gone', ident), None)
        known = self.monitors.get(ident)
        level = _level(current['status'])
        if known is None:
            current.update(level=level, since=current['at'] or now)
            self.monitors[ident] = current
            if not first:
                self._event(new, now, 'new', current)
            return
        known.update({k: v for k, v in current.items() if k not in ('level', 'since')})
        cert = current.get('cert_days')
        if cert is not None and cert < CERT_DAYS and not known.get('cert_warned'):
            known['cert_warned'] = True
            self._event(new, now, 'cert', known, days=cert)
        elif cert is not None and cert >= CERT_DAYS:
            known.pop('cert_warned', None)
        if level is None or level == known['level']:
            self.pending.pop(('level', ident), None)
            return
        if not self._confirmed(('level', ident), level):
            return
        before, since = known['level'], known['since']
        known.update(level=level, since=now)
        if self._flapping(ident, now):
            if not any(e['kind'] == 'unstable' and e.get('id') == ident
                       and now - e['at'] < FLAP_WINDOW for e in self.events + new):
                self._event(new, now, 'unstable', known)
            return
        if level == 'up' and before in ('down', 'maintenance'):
            self._event(new, now, 'up', known, dur=round(now - since), was=before)
        elif level in ('down', 'maintenance'):
            self._event(new, now, level, known)

    # --- thread and snapshot -------------------------------------------------
    def start(self, interval=INTERVAL, first_delay=FIRST_DELAY):
        def run():
            self.sleep(first_delay)
            while True:
                try:
                    self.poll()
                except Exception as exc:  # never take the assistant down
                    print(json.dumps(dict(event='watch_failed', error=str(exc))), flush=True)
                self.sleep(interval)
        threading.Thread(target=run, name='systemwache', daemon=True).start()
        return self

    def snapshot_fields(self):
        """Compact fields for answers (no error texts, no URLs)."""
        if self.checked_at is None and not self.monitors:
            return {}
        monitors = [dict(id=i, name=m['name'], page=m['page'], level=m['level'],
                         since=m['since'], up24=m.get('up24'))
                    for i, m in sorted(self.monitors.items())]
        events = [{k: e[k] for k in ('at', 'kind', 'severity', 'name', 'page', 'dur', 'days')
                   if e.get(k) is not None} for e in self.events]
        return dict(watch_checked_at=self.checked_at, watch_monitors=monitors,
                    watch_pages={s: dict(p) for s, p in self.pages.items()},
                    watch_events=events)

    def status(self):
        """For GET /v1/status (operator only): also the last error message."""
        fields = self.snapshot_fields()
        if fields:
            messages = {i: m.get('msg') for i, m in self.monitors.items() if m.get('msg')}
            fields['watch_messages'] = messages
        return fields


def from_env(report=None, environ=os.environ):
    """The configured watch, or None when SERVITOR_KUMA_URL is not set."""
    base = environ.get('SERVITOR_KUMA_URL', '').strip()
    if not base.startswith(('https://', 'http://')):
        return None
    slugs = [s.strip() for s in environ.get('SERVITOR_KUMA_PAGES', '').split(',') if s.strip()]
    return KumaWatch(base, slugs, report=report) if slugs else None


# --- Spoken sentences ---------------------------------------------------------

def _clock(at, now, um=True):
    """'um 8 Uhr 10', 'gestern um 8 Uhr', 'am 12.10. um 3 Uhr'; um=False (after
    "seit"): '8 Uhr 10', 'gestern 8 Uhr', '12.10. 3 Uhr'."""
    when = datetime.datetime.fromtimestamp(at, now.tzinfo)
    clock = f"{when.hour} Uhr" if when.minute == 0 else f"{when.hour} Uhr {when.minute}"
    at_ = 'um ' if um else ''
    if when.date() == now.date():
        return f"{at_}{clock}"
    if when.date() == now.date() - datetime.timedelta(days=1):
        return f"gestern {at_}{clock}"
    return f"{'am ' if um else ''}{when.day}.{when.month}. {at_}{clock}"


def duration(seconds):
    """'2 Minuten', 'eine Stunde', '3 Stunden 10 Minuten', '2 Tage'."""
    minutes = max(1, round((seconds or 0) / 60))
    if minutes < 60:
        return 'eine Minute' if minutes == 1 else f'{minutes} Minuten'
    hours, rest = divmod(minutes, 60)
    if hours >= 48:
        return f'{round(hours / 24)} Tage'
    text = 'eine Stunde' if hours == 1 else f'{hours} Stunden'
    return f'{text} {rest} Minuten' if rest >= 5 and hours < 6 else text


def _monitors(snapshot):
    value = snapshot.get('watch_monitors')
    return [m for m in value if isinstance(m, dict)] if isinstance(value, list) else []


def _events(snapshot, now, window):
    value = snapshot.get('watch_events')
    events = [e for e in value if isinstance(e, dict) and e.get('kind') in KINDS
              and isinstance(e.get('at'), (int, float))] if isinstance(value, list) else []
    return [e for e in events if now.timestamp() - e['at'] < window]


def _down(monitors):
    return [m for m in monitors if m.get('level') == 'down']


def _unreachable(snapshot):
    pages = snapshot.get('watch_pages')
    return sorted(s for s, p in pages.items() if isinstance(p, dict) and not p.get('ok')) \
        if isinstance(pages, dict) else []


def _event_sentence(event, now):
    name = clean_name(event.get('name') or event.get('page'))
    when, since = _clock(event['at'], now), _clock(event['at'], now, um=False)
    kind = event['kind']
    times = event.get('times', 1)
    total = duration(event.get('dur'))
    if kind == 'up' and times > 1:
        return f"{name} war {times}-mal ausgefallen, zusammen {total}, seit {since} wieder da."
    if kind == 'up':
        return f"{name} war {total} ausgefallen, seit {since} wieder da."
    if kind == 'reachable' and times > 1:
        return (f"Die Statusseite {name} war {times}-mal nicht abrufbar, zusammen {total}, "
                f"seit {since} wieder erreichbar.")
    if kind == 'reachable' and event.get('dur'):
        return f"Die Statusseite {name} war {total} nicht abrufbar, seit {since} wieder erreichbar."
    if kind == 'down':
        return f"{name} ist {when} ausgefallen."
    if kind == 'unstable':
        return f"{name} ist instabil, mehrfach aus und wieder da, zuletzt {when}."
    if kind == 'maintenance':
        return f"{name} ist seit {since} in Wartung."
    if kind == 'new':
        return f"Neu überwacht: {name}."
    if kind == 'gone':
        return f"{name} wird nicht mehr überwacht."
    if kind == 'cert':
        return f"Das Zertifikat von {name} läuft in {event.get('days')} Tagen ab."
    if kind == 'unreachable':
        return f"Die Statusseite {name} ist {when} unerreichbar geworden."
    return f"Die Statusseite {name} ist {since} wieder erreichbar."


def _settled(events):
    """A recovery replaces the outage it ends, and repeated recoveries of one
    page or monitor become one entry: "war 4-mal nicht abrufbar, zusammen
    21 Minuten" instead of four "unerreichbar geworden" that sound current."""
    def key(e):
        return e.get('name') or e.get('page')
    back = {'down': 'up', 'unreachable': 'reachable'}
    recoveries = {}
    for e in events:
        if e['kind'] in ('up', 'reachable'):
            recoveries.setdefault((e['kind'], key(e)), []).append(e)
    out = []
    for e in events:
        if e['kind'] in back and any(r['at'] >= e['at']
                                     for r in recoveries.get((back[e['kind']], key(e)), ())):
            continue
        if e['kind'] in ('up', 'reachable'):
            group = recoveries[(e['kind'], key(e))]
            if e is not max(group, key=lambda r: r['at']):
                continue
            if len(group) > 1:
                e = dict(e, times=len(group), dur=sum(r.get('dur') or 0 for r in group))
        out.append(e)
    return out


def _head(lore):
    if lore in ('billy', 'billy_full'):
        return "Bei den Diensten:"
    return "Der Auspex der fernen Wachen meldet:" if lore == 'full' else "Systemwache:"


def briefing_sentence(snapshot, lore='off', now=None):
    """Current outages first, then at most two notable events of the night;
    None when everything is quiet."""
    if 'watch_checked_at' not in snapshot:
        return None
    now = now or datetime.datetime.now()
    monitors = _monitors(snapshot)
    parts = [f"{clean_name(m['name'])} ist seit {_clock(m['since'], now, um=False)} ausgefallen."
             for m in _down(monitors)]
    parts += [f"Die Statusseite {clean_name(s)} ist nicht abrufbar." for s in _unreachable(snapshot)]
    down_names = {m['name'] for m in _down(monitors)}
    night = [e for e in _events(snapshot, now, NIGHT)
             if e['kind'] in ('up', 'unstable', 'cert', 'new', 'gone')
             and e.get('name') not in down_names]
    night.sort(key=lambda e: (e['severity'], -e['at']))
    parts += [_event_sentence(e, now) for e in night[:max(0, BRIEFING_ITEMS - len(parts))]]
    if not parts:
        return None
    return f"{_head(lore)} {' '.join(parts)}"


# How Vosk small on CT 107 hears the service names (Piper voice, 16.10.2026):
# "optimal kummer", "sehr für systeme", "der clou", "next glück" ...
SPOKEN = tuple((re.compile(pattern), spelling) for pattern, spelling in (
    # Whisper small (SERVITOR_STT=whisper, same day): "Serva Systeme", "die Klug",
    # "dem Wout", "Bis die Cloud erreichbar".
    (r'\b(?:optimal|optimus|optimum|ob die|kimme|up time|uptime) (?:ge)?(?:kumm\w*|koma|kuma\w*)\b',
     'uptime kuma'),
    (r'\b(?:sehr|her) für systeme\b|\bserv[a-zäöü]* ?systeme?\b', 'serversysteme'),
    (r'\bden (?:sehr fand|serven)\b', 'den servern'),
    (r'\bnext (?:glück|klug|clou|club|lu)\b|\bnächst(?:es)? ?klu\w*', 'nextcloud'),
    (r'\b(?:fault|wollt|volt|wald) (?:waden|werden)\b|\bwaldwahn\b', 'vaultwarden'),
    (r'\baudio ?(?:books?|box) ?(?:fällt|helfen|hell|help|shelf|self)\b', 'audiobookshelf'),
    (r'\b(der|die) (?:clou|glut|knut|klug|club|clute)\b', r'\1 cloud'),
    (r'\b(wie geht (?:es |s |ist )?)dem (?:wow|bauen|baut|baul|waut|wout)\b', r'\1dem vault'),
    (r'^bis (die|der|das) ', r'ist \1 '),
    (r'\bwie geht (?:s |ist )?(?!es\b)', 'wie geht es '),
))

# Words of a question that name no service.
_GENERIC = {'geht', 'dienste', 'diensten', 'uptime', 'kuma', 'systemwache', 'server', 'servern',
            'serversysteme', 'systeme', 'überwachung', 'status', 'zustand', 'verändert',
            'ihnen', 'oder', 'meinen', 'obivan', 'erreichbar', 'online', 'probleme', 'alle',
            'laufen', 'gibt'}

# "Wie geht es Audiobookshelf?" / "Ist jellyfin.obivan.org erreichbar?"
_SUBJECT = re.compile(r'\bwie geht es (?:der |dem |den |die |das )?([\w.-]+)|'
                      r'\bist (?:die |der |das )?([\w.-]+(?: [\w.-]+){0,2}) (?:erreichbar|online|down)\b')


def respell(text):
    """The question in normalized form, Vosk's mishearings of service names fixed."""
    words = (w.strip('.-') for w in re.findall(r"[\wäöüß.-]+", str(text or '').lower()))
    text = ' '.join(w for w in words if w)
    for pattern, spelling in SPOKEN:
        text = pattern.sub(spelling, text)
    return text


def _matching(monitors, text):
    """Monitors whose status page or name shares a word (4+ letters) with the
    question: "der Cloud" is the page cloud, "Nextcloud" its containers.
    A monitor named exactly in the question ("vault.obivan.org") wins."""
    text = respell(text)
    exact = [m for m in monitors if len(m['name']) > 3 and m['name'].lower() in text]
    if exact:
        return exact
    words = {w for w in re.findall(r'\w{4,}', text) if w not in _GENERIC}
    return [m for m in monitors if m.get('page') in words
            or words & set(re.findall(r'\w{4,}', m['name'].lower()))]


def _unwatched(text):
    """The service a question names, or None: "audiobookshelf" for "Wie geht es
    Audiobookshelf?", None for "Wie geht es den Diensten?"."""
    match = _SUBJECT.search(respell(text))
    subject = match and (match.group(1) or match.group(2))
    if not subject:
        return None
    words = set(re.findall(r'\w{4,}', subject)) - _GENERIC
    return subject if words else None


def answer_text(snapshot, lore='off', now=None, question=None):
    """Answer to "Was ist bei Uptime Kuma los?" / "Wie geht es Nextcloud?"."""
    billy = lore in ('billy', 'billy_full')
    if snapshot.get('watch_denied'):
        return ("Das verrat ich nur dem Boss." if billy else
                "Zugriff verweigert. Die Systemwache ist dem Bediener vorbehalten.")
    if 'watch_checked_at' not in snapshot:
        if billy:
            return "Zu den Diensten hab ich gerade nichts, die Systemwache läuft auf dem Server."
        return ("Die Systemwache liefert keine Daten. Sie läuft auf meinem Server; "
                "ist dort SERVITOR_KUMA_URL gesetzt?")
    now = now or datetime.datetime.now()
    monitors = _monitors(snapshot)
    chosen = _matching(monitors, question)
    unwatched = None if chosen else _unwatched(question)
    if unwatched:
        # Never "all 8 are fine" for a service that is on no status page.
        name = clean_name(unwatched, 40)
        name = name if '.' in name else name[:1].upper() + name[1:]
        return (f"{_head(lore)} {name} steht auf keiner Statusseite, "
                f"dazu habe ich keine Daten.")
    chosen = chosen or monitors
    down = _down(chosen)
    maintenance = [m for m in chosen if m.get('level') == 'maintenance']
    total = len(chosen)
    scope = (clean_name(chosen[0]['name']) if len(chosen) == 1
             else f"Von {total} überwachten Diensten" if chosen is monitors
             else f"Von {total} passenden Diensten")
    if not total:
        verdict = "Uptime Kuma meldet keine Dienste."
    elif not down:
        verdict = (f"{scope} ist erreichbar." if total == 1 else
                   f"Alle {total} überwachten Dienste sind erreichbar." if chosen is monitors
                   else f"Alle {total} passenden Dienste sind erreichbar.")
        if maintenance:
            verdict += f" {len(maintenance)} davon in Wartung."
    elif total == 1:
        verdict = f"{scope} ist seit {_clock(down[0]['since'], now, um=False)} ausgefallen."
    else:
        names = ', '.join(f"{clean_name(m['name'])} seit {_clock(m['since'], now, um=False)}"
                          for m in down[:ANSWER_ITEMS])
        verdict = f"{scope} {'ist einer' if len(down) == 1 else f'sind {len(down)}'} ausgefallen: {names}."
    parts = [verdict]
    parts += [f"Die Statusseite {clean_name(s)} ist nicht abrufbar." for s in _unreachable(snapshot)]
    names = {m['name'] for m in chosen}
    recent = _settled([e for e in _events(snapshot, now, 24 * 3600.0)
                       if e.get('name') in names or (chosen is monitors and e['kind'] in
                                                     ('unreachable', 'reachable', 'gone'))])
    recent.sort(key=lambda e: (e['severity'], -e['at']))
    if recent:
        parts.append("In den letzten 24 Stunden:")
        parts += [_event_sentence(e, now) for e in recent[:ANSWER_ITEMS]]
    elif total:
        parts.append("In den letzten 24 Stunden keine Veränderung.")
    return f"{_head(lore)} {' '.join(parts)}"
