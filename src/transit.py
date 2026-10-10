"""Public transport from MoBY/EFA (WVV trams, DB Regio and DB Fernverkehr).

The WVV timetable runs on MoBY (Bahnland Bayern); the same EFA server also
returns DB RE/RB and ICE, so no separate DB API is needed. Three GET
endpoints, rapidJSON, no account, cookie or key:

* ``XML_STOPFINDER_REQUEST`` stops and places by name,
* ``XML_DM_REQUEST`` departures at one stop,
* ``XML_TRIP_REQUEST2`` routed journeys, including the footpaths: the walk
  from a coordinate to the first stop and between platforms when changing.

Runs on the Pi only: the home coordinate (TRANSIT_HOME) never leaves it.
Queries run in a background job (src/journeys.py), each with a timeout and
a short cache. Times come in UTC and are handled in Europe/Berlin, so the
date, midnight and daylight-saving changes need no special cases.

Configuration (/etc/pi-voice-assistant.env, all optional):

    TRANSIT_HOME=50.00,10.00          # "von zu Hause": lat,lon (private, never in git)
    TRANSIT_CITY_STOP=3700315         # "in die Stadt": stop ID chosen by the operator
    TRANSIT_BUFFER_MINUTES=2          # added before the routed walk
    TRANSIT_PLACES=rathaus=3700315    # spoken names -> stop IDs (else the stop finder)
    TRANSIT_EFA_VERSION=11.0.6.72
    TRANSIT_BASE_URL=https://whitelabel.bahnland-bayern.de/efa/
    TRANSIT_ENABLED=off               # switch the feature off
"""
import datetime
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import zoneinfo

BASE_URL = 'https://whitelabel.bahnland-bayern.de/efa/'
VERSION = '11.0.6.72'
USER_AGENT = 'pi-voice-assistant/1'
TIMEOUT_SECONDS = 8.0
CACHE_SECONDS = 45
MAX_BYTES = 4 * 1024 * 1024
TZ = zoneinfo.ZoneInfo('Europe/Berlin')
MAX_OPTIONS = 3

# EFA product classes seen in live answers (10.10.2026): footpath 99/100,
# tram 4, DB Regio 13, ICE 16. The train type attribute decides first, the
# class only when it is missing.
WALK_CLASSES = {97, 98, 99, 100}
LONG_TYPES = {'ICE', 'IC', 'EC', 'ECE', 'EN', 'NJ', 'RJ', 'RJX', 'TGV', 'FLX', 'ICD'}
REGIONAL_TYPES = {'RE', 'RB', 'IRE', 'MEX', 'ALX', 'AG', 'BRB', 'ERB', 'RS', 'FEX'}
RAIL = {'regional', 'long', 'sbahn', 'train'}
# "No journey/departure found" is an answer, not a failure of the service.
NO_RESULT_CODES = {-4000, -4001, -4005, -4008, -8010, -8011}


class TransitError(Exception):
    """``kind``: 'network', 'provider' (fatal EFA error) or 'no_result'."""

    def __init__(self, kind, message=''):
        super().__init__(message or kind)
        self.kind = kind


class Config:
    def __init__(self, base_url=BASE_URL, version=VERSION, home=None, city_stop=None,
                 buffer_minutes=2, places=None):
        self.base_url = base_url if base_url.endswith('/') else base_url + '/'
        self.version, self.home, self.city_stop = version, home, city_stop
        self.buffer_minutes, self.places = buffer_minutes, dict(places or {})

    @classmethod
    def from_env(cls, env=None):
        """None when switched off (TRANSIT_ENABLED=off)."""
        env = os.environ if env is None else env
        if env.get('TRANSIT_ENABLED', '').strip().lower() in ('0', 'off', 'no', 'false'):
            return None
        home = None
        raw = env.get('TRANSIT_HOME', '').strip()
        if raw:
            try:
                lat, lon = (float(part) for part in raw.split(','))
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    home = (lat, lon)
            except ValueError:
                home = None
        try:
            buffer_minutes = min(30, max(0, int(env.get('TRANSIT_BUFFER_MINUTES', '') or 2)))
        except ValueError:
            buffer_minutes = 2
        places = {}
        for item in env.get('TRANSIT_PLACES', '').split(','):
            name, _, stop = item.partition('=')
            if name.strip() and stop.strip():
                places[normalize_place(name)] = stop.strip()
        return cls(base_url=env.get('TRANSIT_BASE_URL', '').strip() or BASE_URL,
                   version=env.get('TRANSIT_EFA_VERSION', '').strip() or VERSION,
                   home=home, city_stop=env.get('TRANSIT_CITY_STOP', '').strip() or None,
                   buffer_minutes=buffer_minutes, places=places)


def normalize_place(name):
    text = ' '.join(re.findall(r"[\wäöüß]+", str(name).lower()))
    return re.sub(r'\bhbf\b', 'hauptbahnhof', join_letters(text))


def join_letters(text):
    """Vosk spells abbreviations: 'd j k sportzentrum' -> 'djk sportzentrum'."""
    return re.sub(r'\b\w(?: \w\b)+', lambda m: m.group(0).replace(' ', ''), text)


# --- requests --------------------------------------------------------------------

def _common(config):
    return dict(outputFormat='rapidJSON', version=config.version, language='de')


def _local(now):
    now = now.astimezone(TZ)
    return now.strftime('%Y%m%d'), now.strftime('%H%M')


def _place(prefix, place):
    """('coord', lat, lon) or ('stop', id) -> EFA type_/name_ parameters."""
    if place[0] == 'coord':
        lat, lon = place[1], place[2]
        return {f'type_{prefix}': 'coord',
                f'name_{prefix}': f'{lon:.6f}:{lat:.6f}:WGS84[dd.ddddd]'}
    stop = str(place[1])
    return {f'type_{prefix}': 'stopID' if stop.isdigit() else 'any', f'name_{prefix}': stop}


def trip_params(config, origin, destination, now, count=MAX_OPTIONS):
    date, clock = _local(now)
    params = _common(config)
    params.update(_place('origin', origin))
    params.update(_place('destination', destination))
    params.update(sl3plusTripMacro='1', itdDate=date, itdTime=clock,
                  itdTripDateTimeDepArr='dep', useRealtime='1', locationServerActive='1',
                  calcNumberOfTrips=str(count), ptOptionsActive='1', itOptionsActive='1',
                  allInterchangesAsLegs='1', coordOutputFormat='WGS84[dd.ddddd]')
    return params


def departure_params(config, stop, now, limit=20):
    date, clock = _local(now)
    params = _common(config)
    params.update(type_dm='stopID' if str(stop).isdigit() else 'any', name_dm=str(stop),
                  mode='direct', useRealtime='1', itdDate=date, itdTime=clock,
                  itdDateTimeDepArr='dep', limit=str(limit), locationServerActive='1')
    return params


def stopfinder_params(config, name):
    params = _common(config)
    params.update(type_sf='any', name_sf=join_letters(name), locationServerActive='1',
                  coordOutputFormat='WGS84[dd.ddddd]')
    return params


def fetch_json(url, params, opener=urllib.request.urlopen, timeout=TIMEOUT_SECONDS):
    request = urllib.request.Request(f'{url}?{urllib.parse.urlencode(params)}', headers={
        'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(MAX_BYTES + 1)
    except OSError as exc:      # URLError, HTTPError, timeouts
        raise TransitError('network', str(exc) or type(exc).__name__) from exc
    if len(raw) > MAX_BYTES:
        raise TransitError('provider', 'answer too large')
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise TransitError('provider', 'no JSON') from exc
    if not isinstance(payload, dict):
        raise TransitError('provider', 'unexpected answer')
    return payload


def messages(payload):
    """(warnings, errors) from EFA systemMessages as (code, text)."""
    warnings, errors = [], []
    for item in payload.get('systemMessages') or []:
        if not isinstance(item, dict):
            continue
        entry = (item.get('code'), str(item.get('text') or item.get('module') or ''))
        (errors if item.get('type') == 'error' else warnings).append(entry)
    return warnings, errors


def _check(payload, results):
    """Errors only count when nothing usable came back."""
    warnings, errors = messages(payload)
    if results or not errors:
        return warnings
    codes = {code for code, _ in errors}
    if codes & NO_RESULT_CODES:
        raise TransitError('no_result', str(errors[0]))
    raise TransitError('provider', str(errors[0]))


# --- normalization ---------------------------------------------------------------

def _time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def classify(transportation):
    transportation = transportation if isinstance(transportation, dict) else {}
    product = transportation.get('product') or {}
    cls = product.get('class')
    name = str(product.get('name') or '').lower()
    props = transportation.get('properties') or {}
    train = str(props.get('trainType') or props.get('trainName') or '').upper()
    if cls in WALK_CLASSES or 'fußweg' in name or 'fussweg' in name or 'footpath' in name:
        return 'walk'
    if train in LONG_TYPES or cls == 16 or 'fernverkehr' in name:
        return 'long'
    if train in REGIONAL_TYPES or cls == 13 or 'regional' in name:
        return 'regional'
    if train == 'S' or cls == 1 or 's-bahn' in name:
        return 'sbahn'
    if cls == 4 or 'straßenbahn' in name or 'tram' in name:
        return 'tram'
    if cls in (5, 6, 7, 10, 11) or 'bus' in name:
        return 'bus'
    if cls == 0 or 'zug' in name:
        return 'train'           # a train of unknown kind: never counted as regional
    return 'other'


def _label(transportation, kind):
    transportation = transportation if isinstance(transportation, dict) else {}
    props = transportation.get('properties') or {}
    number = str(transportation.get('disassembledName') or transportation.get('number') or '')
    if kind in RAIL:
        train = str(props.get('trainType') or props.get('trainName') or '').strip()
        train_number = str(props.get('trainNumber') or '').strip()
        if train and train_number:
            return f'{train} {train_number}'
    if kind == 'tram' and number:
        return f'Straßenbahn {number}'
    if kind == 'bus' and number:
        return f'Bus {number}'
    return str(transportation.get('name') or number or 'Verbindung').strip()


def _stop_name(point):
    point = point if isinstance(point, dict) else {}
    parent = point.get('parent') if isinstance(point.get('parent'), dict) else {}
    return str(point.get('disassembledName') or parent.get('disassembledName')
               or point.get('name') or parent.get('name') or '').strip() or None


def _platform(point):
    props = (point or {}).get('properties') or {}
    value = props.get('platformName') or props.get('platform') or props.get('plannedPlatformName')
    value = str(value or '').strip()
    return re.sub(r'^(gleis|bstg\.?|steig)\s*', '', value, flags=re.I) or None


def _cancelled(item):
    if not isinstance(item, dict):
        return False
    if item.get('isCancelled') is True:
        return True
    status = item.get('realtimeStatus') or []
    return any('CANCEL' in str(s).upper() for s in status) if isinstance(status, list) else False


def _notes(leg):
    found = []
    for key in ('infos', 'hints'):
        for item in leg.get(key) or []:
            if isinstance(item, dict):
                text = str(item.get('title') or item.get('subtitle') or item.get('content') or '')
                text = re.sub(r'<[^>]+>', ' ', text)
                text = ' '.join(text.split())[:160]
                if text and text not in found:
                    found.append(text)
    return found[:3]


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def parse_leg(leg):
    transportation = leg.get('transportation') or {}
    kind = classify(transportation)
    origin, destination = leg.get('origin') or {}, leg.get('destination') or {}
    realtime = leg.get('isRealtimeControlled') is True
    dep_planned = _time(origin.get('departureTimePlanned'))
    arr_planned = _time(destination.get('arrivalTimePlanned'))
    # "Estimated" alone proves nothing: only a realtime-controlled leg uses it.
    dep = (_time(origin.get('departureTimeEstimated')) if realtime else None) or dep_planned
    arr = (_time(destination.get('arrivalTimeEstimated')) if realtime else None) or arr_planned
    duration = _number(leg.get('duration'))
    if duration is None and dep and arr:
        duration = max(0, int((arr - dep).total_seconds()))
    operator = (transportation.get('operator') or {}).get('name')
    direction = (transportation.get('destination') or {}).get('name')
    return dict(kind=kind, label=_label(transportation, kind) if kind != 'walk' else 'Fußweg',
                direction=str(direction).strip() if direction else None,
                operator=str(operator).strip() if operator else None,
                origin=_stop_name(origin), destination=_stop_name(destination),
                dep=dep, dep_planned=dep_planned, arr=arr, arr_planned=arr_planned,
                realtime=realtime, platform_dep=_platform(origin),
                platform_arr=_platform(destination), duration=duration,
                distance=_number(leg.get('distance')),
                cancelled=_cancelled(leg) or _cancelled(origin) or _cancelled(destination),
                notes=_notes(leg))


def parse_trips(payload):
    """Journeys as ordered chains of legs (dicts). Raises TransitError when the
    provider failed and nothing came back."""
    journeys = []
    for raw in payload.get('journeys') or []:
        if not isinstance(raw, dict):
            continue
        legs = [parse_leg(leg) for leg in raw.get('legs') or [] if isinstance(leg, dict)]
        if legs:
            journeys.append(dict(legs=legs, additional=raw.get('isAdditional') is True))
    _check(payload, journeys)
    return journeys


def parse_departures(payload):
    found = []
    for raw in payload.get('stopEvents') or []:
        if not isinstance(raw, dict):
            continue
        transportation = raw.get('transportation') or {}
        kind = classify(transportation)
        realtime = raw.get('isRealtimeControlled') is True
        planned = _time(raw.get('departureTimePlanned'))
        dep = (_time(raw.get('departureTimeEstimated')) if realtime else None) or planned
        if planned is None:
            continue
        location = raw.get('location') or {}
        direction = (transportation.get('destination') or {}).get('name')
        found.append(dict(kind=kind, label=_label(transportation, kind),
                          direction=str(direction).strip() if direction else None,
                          origin=_stop_name(location), dep=dep, dep_planned=planned,
                          realtime=realtime, platform_dep=_platform(location),
                          cancelled=_cancelled(raw), notes=_notes(raw)))
    _check(payload, found)
    return found


def parse_stops(payload):
    stops = []
    for raw in payload.get('locations') or []:
        if not isinstance(raw, dict) or raw.get('type') not in ('stop', 'platform'):
            continue
        props = raw.get('properties') or {}
        ident = str(props.get('stopId') or raw.get('id') or '').strip()
        if not ident:
            continue
        stops.append(dict(id=ident, name=str(raw.get('disassembledName') or raw.get('name')
                                             or ident).strip(),
                          best=raw.get('isBest') is True,
                          quality=_number(raw.get('matchQuality')) or 0,
                          classes=[c for c in raw.get('productClasses') or []
                                   if isinstance(c, int)]))
    _check(payload, stops)
    return stops


# --- planning --------------------------------------------------------------------

def _minute(moment):
    return moment.replace(second=0, microsecond=0)


def journey_key(vehicles):
    raw = '|'.join(f"{leg['label']}@{leg['dep_planned'].isoformat() if leg['dep_planned'] else ''}"
                   for leg in vehicles)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def plan(journey, buffer_minutes):
    """Add walk time, departure time and the time to leave, or None for a
    journey that is no public-transport trip (footpath only, cancelled leg).

    Direct trams/buses: leave = first vehicle's departure - routed access
    walk (rounded up to minutes) - buffer; waiting is no walking. Train
    journeys: the buffer goes before the start of the whole routed chain, so
    connections and transfer walks stay as routed. Unknown walk durations
    stay unknown (leave None) instead of being guessed."""
    legs = journey['legs']
    vehicles = [leg for leg in legs if leg['kind'] != 'walk']
    if not vehicles or any(leg['cancelled'] for leg in vehicles):
        return None
    first = vehicles[0]
    if first['dep'] is None:
        return None
    access_legs = legs[:legs.index(first)]
    durations = [leg['duration'] for leg in access_legs]
    access = None if None in durations else sum(durations)
    walk_minutes = None if access is None else math.ceil(access / 60)
    rail = any(leg['kind'] in RAIL for leg in vehicles)
    buffer = datetime.timedelta(minutes=buffer_minutes)
    leave = None
    if rail:
        start = legs[0]['dep'] if access_legs else first['dep']
        if start is None and walk_minutes is not None:
            start = first['dep'] - datetime.timedelta(minutes=walk_minutes)
        if start is not None:
            leave = _minute(start - buffer)
    elif walk_minutes is not None:
        leave = _minute(first['dep'] - datetime.timedelta(minutes=walk_minutes) - buffer)
    last = legs[-1]
    return dict(journey, kind='trip', vehicles=vehicles, rail=rail, dep=first['dep'],
                arr=last['arr'], origin=first['origin'], destination=last['destination'],
                access=access, walk_minutes=walk_minutes, leave=leave,
                transfers=len(vehicles) - 1,
                realtime=all(leg['realtime'] for leg in vehicles),
                key=journey_key(vehicles))


def matches_mode(item, mode):
    kinds = [leg['kind'] for leg in item.get('vehicles') or [item]]
    if mode == 'tram':
        return all(kind == 'tram' for kind in kinds)
    if mode == 'train':
        return any(kind in RAIL for kind in kinds)
    if mode == 'regional':
        rail = [kind for kind in kinds if kind in RAIL]
        return bool(rail) and all(kind in ('regional', 'sbahn') for kind in rail)
    if mode == 'long':
        return 'long' in kinds
    return True


def reachable(item, now):
    if item.get('leave') is not None:
        return item['leave'] >= _minute(now)
    return item['dep'] is not None and item['dep'] >= now


def trip_options(journeys, now, buffer_minutes, mode=None, limit=MAX_OPTIONS):
    """At most ``limit`` reachable journeys, by time to leave."""
    planned = [plan(journey, buffer_minutes) for journey in journeys]
    found, seen = [], set()
    for item in planned:
        if item is None or item['key'] in seen or not matches_mode(item, mode):
            continue
        if not reachable(item, now):
            continue
        seen.add(item['key'])
        found.append(item)
    found.sort(key=lambda item: (item['leave'] or item['dep'], item['dep']))
    return found[:limit]


def departure_options(departures, now, mode=None, limit=MAX_OPTIONS):
    found, seen = [], set()
    for item in departures:
        if item['cancelled'] or item['kind'] == 'walk' or item['dep'] < now:
            continue
        if not matches_mode(item, mode):
            continue
        key = journey_key([item])
        if key in seen:
            continue
        seen.add(key)
        found.append(dict(item, kind='departure', vehicles=[item], rail=item['kind'] in RAIL,
                          arr=None, walk_minutes=None, leave=None, transfers=0, key=key,
                          legs=[item]))
    found.sort(key=lambda item: item['dep'])
    return found[:limit]


# --- client ----------------------------------------------------------------------

class Client:
    """EFA queries with a timeout and a short cache; call from a background job."""

    def __init__(self, config, opener=urllib.request.urlopen, clock=time.monotonic,
                 timeout=TIMEOUT_SECONDS):
        self.config, self.opener, self.clock, self.timeout = config, opener, clock, timeout
        self.cache = {}
        self.lock = threading.Lock()

    def _get(self, endpoint, params):
        key = (endpoint, tuple(sorted(params.items())))
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
            if hit is not None and now - hit[0] < CACHE_SECONDS:
                return hit[1]
        payload = fetch_json(self.config.base_url + endpoint, params, self.opener, self.timeout)
        with self.lock:
            self.cache = {k: v for k, v in self.cache.items() if now - v[0] < CACHE_SECONDS}
            self.cache[key] = (now, payload)
        return payload

    def trips(self, origin, destination, now, count=MAX_OPTIONS):
        params = trip_params(self.config, origin, destination, now, count)
        return parse_trips(self._get('XML_TRIP_REQUEST2', params))

    def departures(self, stop, now):
        params = departure_params(self.config, stop, now)
        return parse_departures(self._get('XML_DM_REQUEST', params))

    def find_stop(self, name, mode=None):
        """Best stop for a spoken name: configured places first, then the stop
        finder (preferring stops served by the asked kind of vehicle)."""
        alias = self.config.places.get(normalize_place(name))
        if alias:
            return dict(id=alias, name=name.strip().title())
        stops = parse_stops(self._get('XML_STOPFINDER_REQUEST',
                                      stopfinder_params(self.config, name)))
        if not stops:
            return None
        wanted = {'tram': {4}, 'train': {0, 1, 13, 16}, 'regional': {0, 1, 13},
                  'long': {0, 16}}.get(mode)

        def rank(stop):
            serves = bool(wanted and wanted & set(stop['classes']))
            return (serves, stop['best'], stop['quality'])
        return max(stops, key=rank)
