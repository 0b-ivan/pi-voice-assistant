"""Today's appointments from CalDAV (Nextcloud) for the morning litany.

Runs on the server only: the credentials (a Nextcloud app password) stay in
/etc/servitor-voice.env on CT 107. Configuration:

    CALDAV_URLS=https://cloud.example.org/remote.php/dav/calendars/USER/personal/
    CALDAV_USER=USER
    CALDAV_PASSWORD=app-password      # Nextcloud: Settings > Security > App passwords

Several calendars: comma-separated URLs. One REPORT per calendar asks for the
day's events with <C:expand>, so the server resolves recurring events and
time zones itself (results in UTC); no RRULE handling here. Unrecognized
voices never hear appointments (same rule as the memory).
"""
import base64
import datetime
import os
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

CACHE_SECONDS = 5 * 60
RETRY_SECONDS = 60
TIMEOUT_SECONDS = 4.0
MAX_SPOKEN = 5
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


def fetch(config, start, end, opener=urllib.request.urlopen, timeout=TIMEOUT_SECONDS):
    credentials = base64.b64encode(f'{config.user}:{config.password}'.encode()).decode()
    events = []
    for url in config.urls:
        request = urllib.request.Request(url, data=query_body(start, end), method='REPORT',
                                         headers={'Authorization': f'Basic {credentials}',
                                                  'Content-Type': 'application/xml; charset=utf-8',
                                                  'Depth': '1'})
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
    """Cached day view; get(now) may block for one round of requests (server)."""

    def __init__(self, config=None, fetcher=fetch, clock=time.monotonic):
        self.config = Config.from_env() if config is None else config
        self.fetcher, self.clock = fetcher, clock
        self.cache, self.day, self.next_at = None, None, 0.0
        self.lock = threading.Lock()

    @property
    def configured(self):
        return self.config is not None

    def get(self, now):
        """Today's remaining events, or None (not configured / unreachable)."""
        if self.config is None:
            return None
        with self.lock:
            if self.clock() >= self.next_at or self.day != now.date():
                start, end = day_bounds(now)
                try:
                    self.cache = self.fetcher(self.config, start, end)
                    self.next_at = self.clock() + CACHE_SECONDS
                except (OSError, ValueError, ET.ParseError):
                    self.cache = None if self.day != now.date() else self.cache
                    self.next_at = self.clock() + RETRY_SECONDS
                self.day = now.date()
            if self.cache is None:
                return None
            return upcoming(self.cache, now)


def _clock(moment):
    return f"{moment.hour} Uhr" if moment.minute == 0 else f"{moment.hour} Uhr {moment.minute}"


def sentence(events, lore='off'):
    """Spoken appointments; None without calendar data."""
    if events is None:
        return None
    if events == DENIED:
        from memory import GUEST_TEXT
        return GUEST_TEXT
    full = lore == 'full'
    if not events:
        return "Keine weiteren Direktiven für heute." if full else "Keine weiteren Termine heute."
    # One sentence per appointment: titles may contain commas themselves.
    parts = ["Direktiven des Tages." if full else "Termine heute."]
    for event in events[:MAX_SPOKEN]:
        title = event['summary'].rstrip('.!?')
        when = "Ganztägig" if event['all_day'] else _clock(event['start'])
        parts.append(f"{when}: {title}.")
    rest = len(events) - MAX_SPOKEN
    if rest > 0:
        parts.append(f"Und {rest} weitere.")
    return " ".join(parts)
