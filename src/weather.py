"""Local weather for the morning litany and "wie ist das Wetter".

Open-Meteo (no API key). The place comes from WEATHER_LAT / WEATHER_LON in
the environment (server: /etc/servitor-voice.env, Pi: /etc/pi-voice-assistant.env);
without them there is no weather and the answers leave it out. Results are
cached, so a briefing costs at most one request per CACHE_SECONDS.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

URL = 'https://api.open-meteo.com/v1/forecast'
CACHE_SECONDS = 20 * 60
RETRY_SECONDS = 2 * 60      # after a failed request
TIMEOUT_SECONDS = 3.0

# WMO weather codes, as Piper reads them well.
CODES = (
    ((0,), 'klar'), ((1,), 'überwiegend klar'), ((2,), 'teils bewölkt'), ((3,), 'bedeckt'),
    ((45, 48), 'Nebel'), ((51, 53, 55, 56, 57), 'Nieselregen'),
    ((61, 63, 65, 66, 67, 80, 81, 82), 'Regen'), ((71, 73, 75, 77, 85, 86), 'Schnee'),
    ((95, 96, 99), 'Gewitter'),
)


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


def parse(payload):
    """Open-Meteo JSON -> dict(now, high, low, rain, code); None if unusable."""
    try:
        current, daily = payload['current'], payload['daily']
        data = dict(now=_number(current.get('temperature_2m')),
                    code=current.get('weather_code'),
                    high=_number(daily.get('temperature_2m_max', [None])[0]),
                    low=_number(daily.get('temperature_2m_min', [None])[0]),
                    rain=_number(daily.get('precipitation_probability_max', [None])[0]))
    except (KeyError, TypeError, AttributeError, IndexError):
        return None
    return data if data['now'] is not None else None


def fetch(lat, lon, timeout=TIMEOUT_SECONDS, opener=urllib.request.urlopen):
    query = urllib.parse.urlencode(dict(
        latitude=lat, longitude=lon, timezone='auto', forecast_days=1,
        current='temperature_2m,weather_code',
        daily='temperature_2m_max,temperature_2m_min,precipitation_probability_max'))
    # Own User-Agent: Cloudflare-fronted hosts reject Python's default (error 1010).
    request = urllib.request.Request(f'{URL}?{query}', headers={'User-Agent': 'pi-voice-assistant/1'})
    with opener(request, timeout=timeout) as response:
        return parse(json.loads(response.read().decode('utf-8')))


class Forecast:
    """Cached weather. get() may block for one request (server thread);
    cached() never blocks and refreshes in the background (Pi main loop)."""

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


def _degrees(value):
    return f"minus {-value}" if value < 0 else str(value)


def sentence(data, lore='off'):
    """Spoken weather, or None without data."""
    if not data:
        return None
    sky = condition(data.get('code'))
    parts = [f"Außentemperatur {_degrees(data['now'])} Grad" + (f", {sky}" if sky else '') + '.']
    if data.get('high') is not None and data.get('low') is not None:
        parts.append(f"Heute {_degrees(data['low'])} bis {_degrees(data['high'])} Grad.")
    rain = data.get('rain')
    if rain is not None and rain >= 30:
        warning = "Niederschlag wahrscheinlich" if rain >= 60 else "Niederschlag möglich"
        parts.append(f"{warning}, {rain} Prozent.")
        if lore == 'full':
            parts.append("Schutz der Mechanik vor Feuchtigkeit empfohlen.")
    return ' '.join(parts)
