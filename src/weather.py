"""Local weather: five days from Open-Meteo, kept on the Pi for offline use.

Open-Meteo (no API key). The place comes from WEATHER_LAT / WEATHER_LON in
the environment (server: /etc/servitor-voice.env, Pi: /etc/pi-voice-assistant.env);
without them there is no weather and the answers leave it out.

Server: Forecast caches one request per CACHE_SECONDS for its answers.
Pi: Keeper refreshes the five-day forecast every KEEP_SECONDS in a thread and
stores it only on the memory stick (memory.ROOT/weather.json), like everything
the unit remembers: after a restart and without network it still knows today's
and the next days' weather. Without the stick the forecast lives in RAM only.
A copy for the display goes to the runtime directory (tmpfs, display-weather.json). Stored data is matched to
the calendar day; the current temperature counts only while it is fresh.
"""
import datetime
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

URL = 'https://api.open-meteo.com/v1/forecast'
DAYS = 5
CACHE_SECONDS = 20 * 60
RETRY_SECONDS = 2 * 60      # after a failed request (server)
KEEP_SECONDS = 30 * 60      # Pi refresh
KEEP_RETRY_SECONDS = 5 * 60
CURRENT_FRESH_SECONDS = 3 * 3600   # older: no "Außentemperatur", only the day's range
TIMEOUT_SECONDS = 3.0
USER_AGENT = 'pi-voice-assistant/1'   # Cloudflare-fronted hosts reject Python's default
FILE = 'weather.json'

# WMO weather codes, as Piper reads them well.
CODES = (
    ((0,), 'klar'), ((1,), 'überwiegend klar'), ((2,), 'teils bewölkt'), ((3,), 'bedeckt'),
    ((45, 48), 'Nebel'), ((51, 53, 55, 56, 57), 'Nieselregen'),
    ((61, 63, 65, 66, 67, 80, 81, 82), 'Regen'), ((71, 73, 75, 77, 85, 86), 'Schnee'),
    ((95, 96, 99), 'Gewitter'),
)
WEEKDAYS = ('Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag')


def condition(code):
    for codes, text in CODES:
        if code in codes:
            return text
    return None


def location(env=None):
    """(lat, lon) or None when unset or invalid."""
    env = os.environ if env is None else env
    try:
        lat, lon = float(env['WEATHER_LAT']), float(env['WEATHER_LON'])
    except (KeyError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value)


def _code(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse(payload):
    """Open-Meteo JSON -> dict(now, code, high, low, rain, days=[...]); None if unusable.
    now/code: current conditions; high/low/rain: today; days: one dict per day
    (date ISO, code, high, low, rain)."""
    try:
        current, daily = payload['current'], payload['daily']
        days = []
        for i, date in enumerate(daily['time'][:DAYS]):
            datetime.date.fromisoformat(date)
            days.append(dict(date=date,
                             code=_code(daily.get('weather_code', [])[i]),
                             high=_number(daily.get('temperature_2m_max', [])[i]),
                             low=_number(daily.get('temperature_2m_min', [])[i]),
                             rain=_number(daily.get('precipitation_probability_max', [])[i])))
        now = _number(current.get('temperature_2m'))
    except (KeyError, TypeError, AttributeError, IndexError, ValueError):
        return None
    if now is None or not days:
        return None
    today = days[0]
    return dict(now=now, code=_code(current.get('weather_code')), high=today['high'],
                low=today['low'], rain=today['rain'], days=days)


def fetch(lat, lon, timeout=TIMEOUT_SECONDS, opener=urllib.request.urlopen):
    query = urllib.parse.urlencode(dict(
        latitude=lat, longitude=lon, timezone='auto', forecast_days=DAYS,
        current='temperature_2m,weather_code',
        daily='weather_code,temperature_2m_max,temperature_2m_min,'
              'precipitation_probability_max'))
    request = urllib.request.Request(f'{URL}?{query}', headers={'User-Agent': USER_AGENT})
    with opener(request, timeout=timeout) as response:
        return parse(json.loads(response.read().decode('utf-8')))


def view(data, today=None, now=None):
    """Stored data -> what is true today: dict(now, code, high, low, rain, days)
    with days starting today; ``now`` (current temperature) only while fresh.
    None when the data has no entry for today (too old)."""
    if not isinstance(data, dict) or not isinstance(data.get('days'), list):
        return None
    today = (today or datetime.date.today()).isoformat()
    days = [d for d in data['days'] if isinstance(d, dict) and d.get('date', '') >= today]
    if not days or days[0].get('date') != today:
        return None
    updated = data.get('updated')
    now = time.time() if now is None else now
    fresh = (isinstance(updated, (int, float)) and 0 <= now - updated <= CURRENT_FRESH_SECONDS
             and data['days'][0].get('date') == today)
    first = days[0]
    return dict(now=data.get('now') if fresh else None,
                code=data.get('code') if fresh and data.get('code') is not None
                else first.get('code'),
                high=first.get('high'), low=first.get('low'), rain=first.get('rain'),
                days=days)


class Forecast:
    """Server: cached weather. get() may block for one request."""

    def __init__(self, place=None, fetcher=fetch, clock=time.monotonic):
        self.place = location() if place is None else place
        self.fetcher, self.clock = fetcher, clock
        self.data, self.next_at = None, 0.0
        self.lock = threading.Lock()
        self.refreshing = False

    def _refresh(self):
        try:
            data = self.fetcher(*self.place)
        except (OSError, ValueError):
            data = None
        with self.lock:
            if data is not None:
                self.data = dict(data, at=self.clock())
            self.next_at = self.clock() + (CACHE_SECONDS if data is not None else RETRY_SECONDS)
            self.refreshing = False

    def _fresh(self):
        """Cached data, dropped when older than three cache periods."""
        if self.data is None or self.clock() - self.data['at'] > 3 * CACHE_SECONDS:
            return None
        return {k: v for k, v in self.data.items() if k != 'at'}

    def get(self):
        if self.place is None:
            return None
        if self.clock() >= self.next_at:
            self._refresh()
        return self._fresh()

    def cached(self):
        if self.place is None:
            return None
        with self.lock:
            start = self.clock() >= self.next_at and not self.refreshing
            if start:
                self.refreshing = True
        if start:
            threading.Thread(target=self._refresh, name='weather', daemon=True).start()
        return self._fresh()


def _write(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    try:
        tmp.write_text(json.dumps(data, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('days'), list):
        return None
    if not isinstance(data.get('updated'), (int, float)):
        return None
    return data


def default_stores():
    """Where the Pi keeps the forecast: only the memory stick, when plugged in."""
    try:
        import memory
        core = memory.MemoryCore()
        if core.present():
            return [core.root / FILE]
    except Exception:   # never let the stick break the weather
        pass
    return []


def display_path():
    return Path(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt')) / 'display-weather.json'


class Keeper:
    """Pi: the five-day forecast, refreshed in a thread and kept on disk.
    Reads never block the button loop."""

    def __init__(self, place=None, fetcher=fetch, stores=default_stores, clock=time.time,
                 display=display_path):
        self.place = location() if place is None else place
        self.fetcher, self.stores, self.clock, self.display = fetcher, stores, clock, display
        self.data = None
        self.error = None
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    @property
    def configured(self):
        return self.place is not None

    def load(self):
        """The forecast stored on the stick (if newer), also for the display."""
        found = [d for d in (_read(p) for p in self.stores()) if d is not None]
        if found:
            with self.lock:
                newest = max(found, key=lambda d: d['updated'])
                if self.data is None or newest['updated'] > self.data['updated']:
                    self.data = newest
            _write(self.display(), self.data)
        return self.data

    def refresh(self):
        """One request; True when Open-Meteo answered (then stored everywhere)."""
        try:
            data = self.fetcher(*self.place)
        except (OSError, ValueError) as exc:
            data, self.error = None, str(exc) or type(exc).__name__
        if data is None:
            return False
        data = dict(data, updated=self.clock())
        with self.lock:
            self.data, self.error = data, None
        for path in self.stores():
            _write(path, data)
        _write(self.display(), data)
        return True

    def start(self):
        if self.place is None:
            return self
        self.load()

        def loop():
            while not self.stop_event.is_set():
                ok = self.refresh()
                self.stop_event.wait(KEEP_SECONDS if ok else KEEP_RETRY_SECONDS)
        threading.Thread(target=loop, name='weather', daemon=True).start()
        return self

    def stop(self):
        self.stop_event.set()

    def today(self, today=None):
        """What is true today (view), or None without usable data."""
        with self.lock:
            data = self.data
        return view(data, today, self.clock())


def _degrees(value):
    return f"minus {-value}" if value < 0 else str(value)


def _rain(rain, lore):
    if rain is None or rain < 30:
        return []
    warning = "Niederschlag wahrscheinlich" if rain >= 60 else "Niederschlag möglich"
    parts = [f"{warning}, {rain} Prozent."]
    if lore == 'full':
        parts.append("Schutz der Mechanik vor Feuchtigkeit empfohlen.")
    return parts


def sentence(data, lore='off'):
    """Spoken weather for today, or None without data."""
    if not data:
        return None
    sky = condition(data.get('code'))
    parts = []
    if data.get('now') is not None:
        parts.append(f"Außentemperatur {_degrees(data['now'])} Grad"
                     + (f", {sky}" if sky else '') + '.')
        sky = None
    if data.get('high') is not None and data.get('low') is not None:
        parts.append(f"Heute {_degrees(data['low'])} bis {_degrees(data['high'])} Grad"
                     + (f", {sky}" if sky else '') + '.')
    if not parts:
        return None
    return ' '.join(parts + _rain(data.get('rain'), lore))


def day_label(offset, date):
    if offset == 1:
        return "Morgen"
    if offset == 2:
        return "Übermorgen"
    return WEEKDAYS[datetime.date.fromisoformat(date).weekday()]


def day_sentence(data, offset, lore='off'):
    """Spoken forecast for today + ``offset`` days (0 = today), or None."""
    if offset == 0:
        return sentence(data, lore)
    days = (data or {}).get('days') or []
    if not 0 < offset < len(days):
        return None
    day = days[offset]
    if day.get('high') is None or day.get('low') is None:
        return None
    sky = condition(day.get('code'))
    text = (f"{day_label(offset, day['date'])}: {_degrees(day['low'])} bis "
            f"{_degrees(day['high'])} Grad" + (f", {sky}" if sky else '') + '.')
    return ' '.join([text] + _rain(day.get('rain'), lore))
