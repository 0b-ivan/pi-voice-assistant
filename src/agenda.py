"""Today's and tomorrow's appointments from CalDAV (Nextcloud).

Runs on the Pi: the credentials (a Nextcloud app password) stay in
/etc/pi-voice-assistant.env. A background thread refreshes the day every few
minutes; the Pi sends today's remaining appointments and tomorrow's with every turn
(X-Servitor-Agenda and X-Servitor-Agenda-Tomorrow), so the server needs no credentials and the Pi still
knows them offline. Configuration:

    CALDAV_URLS=https://cloud.example.org/remote.php/dav/calendars/USER/personal/
    CALDAV_USER=USER
    CALDAV_PASSWORD=app-password      # Nextcloud: Settings > Security > App passwords

Several calendars: comma-separated URLs. One REPORT per calendar asks for the
day's events with <C:expand>, so Nextcloud resolves recurring events and
time zones itself (results in UTC); no RRULE handling here. The server
never speaks appointments to an unrecognized voice (same rule as the memory).
"""
import base64
import datetime
import json
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

CACHE_SECONDS = 5 * 60
RETRY_SECONDS = 60
TIMEOUT_SECONDS = 8.0     # background thread: a slow Nextcloud blocks nothing
MAX_SPOKEN = 5
MAX_SENT = 12             # appointments per turn header
MAX_TITLE = 80
HEADER_LIMIT = 4096
DENIED = 'denied'          # snapshot value for an unrecognized voice

NS = {'d': 'DAV:', 'c': 'urn:ietf:params:xml:ns:caldav'}


class Config:
    def __init__(self, urls, user, password):
        self.urls, self.user, self.password = urls, user, password

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        urls = [u.strip() for u in env.get('CALDAV_URLS', '').split(',') if u.strip()]
        user, password = env.get('CALDAV_USER', ''), env.get('CALDAV_PASSWORD', '')
        if not urls or not user or not password:
            return None
        return cls(urls, user, password)


def _stamp(moment):
    return moment.astimezone(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def query_body(start, end):
    span = f'start="{_stamp(start)}" end="{_stamp(end)}"'
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        f'<d:prop><c:calendar-data><c:expand {span}/></c:calendar-data></d:prop>'
        '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
        f'<c:time-range {span}/>'
        '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
    ).encode('utf-8')


def _unfold(text):
    return re.sub(r'\r?\n[ \t]', '', text).splitlines()


def _unescape(value):
    return re.sub(r'\\([\\;,nN])', lambda m: ' ' if m.group(1) in 'nN' else m.group(1), value)


def _moment(params, value, tz):
    """DTSTART/DTEND -> (aware datetime or date, all_day)."""
    value = value.strip()
    if 'VALUE=DATE' in params.upper() or re.fullmatch(r'\d{8}', value):
        return datetime.datetime.strptime(value[:8], '%Y%m%d').date(), True
    moment = datetime.datetime.strptime(value.rstrip('Z')[:15], '%Y%m%dT%H%M%S')
    if value.endswith('Z'):
        return moment.replace(tzinfo=datetime.timezone.utc).astimezone(tz), False
    zone = re.search(r'TZID=([^;:]+)', params)
    if zone:
        import zoneinfo
        try:
            return moment.replace(tzinfo=zoneinfo.ZoneInfo(zone.group(1).strip('"'))) \
                .astimezone(tz), False
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            pass
    return moment.replace(tzinfo=tz), False      # floating time: the operator's clock


def parse_ics(text, tz):
    """VEVENTs of one iCalendar object -> [dict(summary, start, end, all_day)]."""
    events, current = [], None
    for line in _unfold(text):
        if line == 'BEGIN:VEVENT':
            current = {}
        elif line == 'END:VEVENT' and current is not None:
            if 'start' in current and current.get('status') != 'CANCELLED':
                current.setdefault('summary', 'Termin ohne Titel')
                current.setdefault('end', None)
                current.pop('status', None)
                events.append(current)
            current = None
        elif current is not None and ':' in line:
            head, value = line.split(':', 1)
            name, _, params = head.partition(';')
            name = name.upper()
            try:
                if name == 'SUMMARY':
                    current['summary'] = _unescape(value).strip() or 'Termin ohne Titel'
                elif name == 'DTSTART':
                    current['start'], current['all_day'] = _moment(params, value, tz)
                elif name == 'DTEND':
                    current['end'], _ = _moment(params, value, tz)
                elif name == 'STATUS':
                    current['status'] = value.strip().upper()
            except ValueError:
                continue
    return events


def parse_multistatus(body, tz):
    root = ET.fromstring(body)
    events = []
    for data in root.iterfind('.//c:calendar-data', NS):
        if data.text:
            events.extend(parse_ics(data.text, tz))
    return events


USER_AGENT = 'pi-voice-assistant/1'   # Cloudflare rejects Python's default (error 1010)
_DAV = '{DAV:}'
_CALDAV = '{urn:ietf:params:xml:ns:caldav}'


def _request(url, credentials, method, body, depth):
    return urllib.request.Request(url, data=body, method=method, headers={
        'Authorization': f'Basic {credentials}', 'User-Agent': USER_AGENT,
        'Content-Type': 'application/xml; charset=utf-8', 'Depth': depth})


def _propfind(url, credentials, prop, depth, opener, timeout):
    body = (f'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
            f'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>{prop}</d:prop></d:propfind>')
    with opener(_request(url, credentials, 'PROPFIND', body.encode(), depth),
                timeout=timeout) as response:
        return ET.fromstring(response.read())


def discover(url, credentials, opener=urllib.request.urlopen, timeout=TIMEOUT_SECONDS):
    """Calendar URLs below a CalDAV root or account URL (e.g. .../remote.php/dav):
    current-user-principal -> calendar-home-set -> calendars with VEVENT."""
    from urllib.parse import urljoin
    root = _propfind(url, credentials, '<d:current-user-principal/>', '0', opener, timeout)
    principal = root.find(f'.//{_DAV}current-user-principal/{_DAV}href')
    if principal is None:
        return [url]
    home = _propfind(urljoin(url, principal.text), credentials, '<c:calendar-home-set/>', '0',
                     opener, timeout).find(f'.//{_CALDAV}calendar-home-set/{_DAV}href')
    if home is None:
        return [url]
    listing = _propfind(urljoin(url, home.text), credentials,
                        '<d:resourcetype/><c:supported-calendar-component-set/>', '1', opener,
                        timeout)
    found = []
    for response in listing.findall(f'{_DAV}response'):
        href = response.find(f'{_DAV}href')
        is_calendar = response.find(f'.//{_DAV}resourcetype/{_CALDAV}calendar') is not None
        components = [c.get('name') for c in response.iter(f'{_CALDAV}comp')]
        if href is not None and is_calendar and (not components or 'VEVENT' in components):
            found.append(urljoin(url, href.text))
    return found


def fetch(config, start, end, opener=urllib.request.urlopen, timeout=TIMEOUT_SECONDS):
    credentials = base64.b64encode(f'{config.user}:{config.password}'.encode()).decode()
    events = []
    urls = []
    for url in config.urls:
        # A calendar URL is used as is; anything else (DAV root, account) is searched.
        urls += [url] if '/calendars/' in url else discover(url, credentials, opener, timeout)
    for url in urls:
        request = _request(url, credentials, 'REPORT', query_body(start, end), '1')
        with opener(request, timeout=timeout) as response:
            events.extend(parse_multistatus(response.read(), start.tzinfo))
    return events


def day_bounds(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + datetime.timedelta(days=1)


def upcoming(events, now):
    """Today's events still ahead (or running), all-day ones first, by start."""
    today = now.date()
    # All-day DTEND is exclusive (a one-day event ends the next day).
    whole = [e for e in events if e['all_day'] and e['start'] <= today
             and (e['end'] > today if e['end'] else e['start'] == today)]
    timed = [e for e in events if not e['all_day'] and e['start'].date() == today
             and ((e['end'] > now) if e['end'] else e['start'] >= now)]
    timed.sort(key=lambda e: e['start'])
    seen, result = set(), []
    for event in whole + timed:  # one calendar may be shared into another
        key = (event['summary'], str(event['start']))
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


class Agenda:
    """Pi side: today and tomorrow, refreshed in a background thread; reads
    never block the button loop."""

    def __init__(self, config=None, fetcher=fetch, clock=time.monotonic,
                 now=lambda: datetime.datetime.now().astimezone()):
        self.config = Config.from_env() if config is None else config
        self.fetcher, self.clock, self.now = fetcher, clock, now
        self.cache, self.day = None, None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.error = None

    @property
    def configured(self):
        return self.config is not None

    def refresh(self):
        """One round of requests; True when the calendar answered."""
        now = self.now()
        start, end = day_bounds(now)
        end += datetime.timedelta(days=1)  # tomorrow, in the same background request
        try:
            events = self.fetcher(self.config, start, end)
        except (OSError, ValueError, ET.ParseError) as exc:
            self.error = str(exc) or type(exc).__name__
            with self.lock:
                if self.day != now.date():   # never speak yesterday's list as today's
                    self.cache, self.day = None, None
            return False
        with self.lock:
            self.cache, self.day, self.error = events, now.date(), None
        return True

    def start(self):
        """Refresh now and then every CACHE_SECONDS (RETRY_SECONDS after a failure)."""
        if self.config is None:
            return self

        def loop():
            while not self.stop_event.is_set():
                ok = self.refresh()
                self.stop_event.wait(CACHE_SECONDS if ok else RETRY_SECONDS)
        threading.Thread(target=loop, name='agenda', daemon=True).start()
        return self

    def stop(self):
        self.stop_event.set()

    def today(self, now=None):
        """Today's remaining events, or None (not configured / no data yet)."""
        now = now or self.now()
        with self.lock:
            if self.cache is None or self.day != now.date():
                return None
            return upcoming(self.cache, now)

    def tomorrow(self, now=None):
        """Tomorrow's events; no synchronous request in the button loop."""
        now = now or self.now()
        with self.lock:
            if self.cache is None or self.day != now.date():
                return None
            start, _ = day_bounds(now)
            return upcoming(self.cache, start + datetime.timedelta(days=1))


def _iso(moment):
    return moment.isoformat()


def encode_header(events):
    """Pi side: today's remaining appointments for X-Servitor-Agenda (None: no data)."""
    if events is None:
        return None
    items = [dict(t=e['summary'][:MAX_TITLE], s=_iso(e['start']),
                  e=_iso(e['end']) if e['end'] else None, a=bool(e['all_day']))
             for e in events[:MAX_SENT]]
    raw = json.dumps(items, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    value = base64.b64encode(raw).decode('ascii')
    return value if len(value) <= HEADER_LIMIT else None


def decode_header(value, tz):
    """Server side: validate the appointments sent by the Pi; None if absent or broken."""
    if not value or len(value) > HEADER_LIMIT:
        return None
    try:
        items = json.loads(base64.b64decode(value, validate=True).decode('utf-8'))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(items, list):
        return None
    events = []
    for item in items[:MAX_SENT]:
        if not isinstance(item, dict) or not isinstance(item.get('t'), str):
            continue
        all_day = item.get('a') is True
        try:
            if all_day:
                start = datetime.date.fromisoformat(item['s'])
                end = datetime.date.fromisoformat(item['e']) if item.get('e') else None
            else:
                start = datetime.datetime.fromisoformat(item['s'])
                end = datetime.datetime.fromisoformat(item['e']) if item.get('e') else None
                if start.tzinfo is None or (end is not None and end.tzinfo is None):
                    continue
                start, end = start.astimezone(tz), end.astimezone(tz) if end else None
        except (KeyError, TypeError, ValueError):
            continue
        title = re.sub(r'\s+', ' ', item['t'])[:MAX_TITLE].strip() or 'Termin ohne Titel'
        events.append(dict(summary=title, start=start, end=end, all_day=all_day))
    return events


def _clock(moment):
    return f"{moment.hour} Uhr" if moment.minute == 0 else f"{moment.hour} Uhr {moment.minute}"


def sentence(events, lore='off', day=0):
    """Spoken appointments; None without calendar data."""
    if events is None:
        return None
    if events == DENIED:
        from memory import GUEST_TEXT
        return GUEST_TEXT
    full = lore == 'full'
    label = 'morgen' if day == 1 else 'heute'
    if not events:
        return f"Keine weiteren Direktiven für {label}." if full else f"Keine weiteren Termine {label}."
    # One sentence per appointment: titles may contain commas themselves.
    parts = [("Direktiven für morgen." if day else "Direktiven des Tages.") if full
             else f"Termine {label}."]
    for event in events[:MAX_SPOKEN]:
        title = event['summary'].rstrip('.!?')
        when = "Ganztägig" if event['all_day'] else _clock(event['start'])
        parts.append(f"{when}: {title}.")
    rest = len(events) - MAX_SPOKEN
    if rest > 0:
        parts.append(f"Und {rest} weitere.")
    return " ".join(parts)
