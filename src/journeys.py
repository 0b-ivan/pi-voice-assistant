"""Spoken journeys: tram and train information, then optionally a Pi
reminder and/or a calendar entry, never without a confirmed proposal.

Flow: information -> choice -> concrete proposal -> consent -> execution ->
result. A timetable question never writes anything itself.

* ``parse(text, state)`` (Pi and server): is this utterance for the journey
  controller, and what does it ask? Runs before the calendar intent and the
  LLM, without the twelve-word limit of intents.match. ``state`` is the Pi's
  controller state from the turn snapshot (``trip``); without it nothing is
  claimed, so a Pi that does not know journeys never gets such a turn.
* ``Journeys`` (Pi only): one controller for both speech paths. The server
  only recognizes the request and the voice; it sends a structured
  ``journey`` event without audio, and the Pi acts once the turn has
  completed. Queries, the home coordinate and the CalDAV credentials stay
  on the Pi.

Consent: at most one open question for all writing actions. It belongs to
one proposal ID and is taken when the next turn is submitted (or B/E is
pressed), so only that turn or button press can answer it. No, B, stop,
timeout (90 s after the question was spoken), an unclear answer or a
different voice trigger nothing. Before acting, the journey is queried
again; a material change needs a new proposal. Every proposal ID runs at
most once, so a repeated event or the local fallback never acts twice.

The Pi reminder lives in RAM: the Pi has to be running, a restart drops it.
That is said before consent.
"""
import datetime
import re
import secrets
import time

import agenda as agenda_feed
import device_control
import transit

OFFER_SECONDS = 15 * 60       # "die zweite" refers to options this recent
NEED_SECONDS = 2 * 60         # a follow-up question waits this long
CONFIRM_SECONDS = 90          # after the proposal was spoken completely
PROPOSAL_MAX_SECONDS = 5 * 60  # also ends a proposal whose speech never finished
REMINDER_GRACE = datetime.timedelta(minutes=3)
CHANGE = datetime.timedelta(minutes=2)

STATES = ('on', 'offers', 'ask', 'origin', 'destination', 'calendar', 'choice')
MODES = ('tram', 'train', 'regional', 'long')
ACTIONS = ('remind', 'calendar')

_VEHICLE = (r'(straßenbahn\w*|strassenbahn\w*|tram|trams|zug|züge|zuge|bahn|s bahn|'
            r'regionalzug\w*|regionalbahn\w*|regionalexpress|regio|ice|intercity|fernzug\w*|'
            r'verbindung\w*|abfahrt\w*)')
_ASKS = re.compile(r'\b(wann|nächste[nrs]?|wie komme ich|verbindung\w*|abfahrt\w*|fahrplan|'
                   r'fährt|fahren|geht|gehen|welche[rn]?)\b')
_HOW = re.compile(r'\bwie (komme|kommen) (ich|wir)\b.*\b(nach|zum|zur|in die (innen)?stadt)\b')
_HOME = re.compile(r'\b(von |ab )?(zu ?hause|daheim|der wohnung)\b')
_HERE = re.compile(r'\b(von hier|ab hier|hier)\b')
_CITY = re.compile(r'\b(in die (innen)?stadt|in die city|stadtmitte|ins zentrum)\b')
_ORIGIN = re.compile(r'\b(?:ab|von|vom) (?:der |dem |den )?(?:haltestelle |bahnhof )?(.+?)'
                     r'(?= nach\b| in die\b| zum\b| zur\b| bis\b|$)')
_DESTINATION = re.compile(r'\b(?:nach|zum|zur|bis) (?:der |dem |zum )?(.+?)(?= von\b| ab\b|$)')
_TRAILING = re.compile(r'(\s+(los|fahren|fährt|kommen|komme|bitte|heute|jetzt|gerade|noch|'
                       r'ab|an|hin|mit der \w+|mit dem \w+))+$')
_ORDINAL = re.compile(r'\b(erste|zweite|dritte|letzte)[nrs]?\b|'
                      r'\b(?:nummer|option|verbindung) (eins|zwei|drei|1|2|3)\b')
_BARE_NUMBER = {'eins': 0, 'zwei': 1, 'drei': 2, '1': 0, '2': 1, '3': 2}
_ORDINALS = {'erste': 0, 'zweite': 1, 'dritte': 2, 'letzte': -1}
_FILTER = re.compile(r'\b(nur|bloß|lieber) (noch )?(?:mit )?(?:dem |der |den )?'
                     r'(regional\w*|regio|straßenbahn\w*|strassenbahn\w*|tram|ice|fernzüge|'
                     r'fernverkehr|züge|zug|bahn)\b|\bohne (ice|fernverkehr|fernzüge)\b')
_REMIND = re.compile(r'\b(erinner\w* (mich|uns)|weck\w* mich|sag mir bescheid|sag bescheid)\b')
_CALENDAR = re.compile(r'\b(kalender\w*|eintragen|trag\w* (\w+ ){0,5}ein)\b')
_BOTH = re.compile(r'\b(beides|beide|alles beides)\b')


def normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


def _mode(text):
    if re.search(r'\b(regional\w*|regio|ohne ice|ohne fernverkehr|ohne fernzüge)\b', text):
        return 'regional'
    if re.search(r'\b(ice|intercity|fernzug\w*|fernzüge|fernverkehr)\b', text):
        return 'long'
    if re.search(r'\b(straßenbahn\w*|strassenbahn\w*|trams?)\b', text):
        return 'tram'
    if re.search(r'\b(zug|züge|zuge|bahn|s bahn)\b', text):
        return 'train'
    return None


def _name(value):
    value = _TRAILING.sub('', value.strip())
    value = re.sub(r'^(der|die|das|dem|den)\s+', '', value)
    return value[:60].strip() or None


def _origin(text):
    if _HOME.search(text):
        return 'home'
    if _HERE.search(text):
        return 'here'
    match = _ORIGIN.search(text)
    name = _name(match.group(1)) if match else None
    return dict(name=name) if name else None


def _destination(text):
    if _CITY.search(text):
        return 'city'
    match = _DESTINATION.search(text)
    name = _name(match.group(1)) if match else None
    if name and (_HOME.fullmatch(name) or name == 'hause'):
        return 'home'           # "nach Hause": routed to the home coordinate
    return dict(name=name) if name else None


def _query(text):
    is_query = bool(_HOW.search(text)) or (
        re.search(rf'\b{_VEHICLE}\b', text) is not None and _ASKS.search(text) is not None)
    if not is_query:
        return None
    destination = _destination(text)
    origin = _origin(re.sub(r'\b(?:nach|zum|zur|bis) .*$', '', text)) if destination \
        else _origin(text)
    kind = 'departures' if destination is None and isinstance(origin, dict) else 'trip'
    return dict(type='query', kind=kind, origin=origin, destination=destination,
                mode=_mode(text))


def _index(text):
    match = _ORDINAL.search(text)
    if match:
        return _ORDINALS[match.group(1)] if match.group(1) else _BARE_NUMBER[match.group(2)]
    words = text.split()
    if len(words) <= 2 and words and words[-1] in _BARE_NUMBER:
        return _BARE_NUMBER[words[-1]]
    return None


def _actions(text):
    remind, calendar = bool(_REMIND.search(text)), bool(_CALENDAR.search(text))
    if _BOTH.search(text) or (remind and calendar):
        return ['remind', 'calendar']
    if calendar:
        return ['calendar']
    if remind:
        return ['remind']
    return None


def _short(text, words=4):
    """A bare answer such as a stop or calendar name, nothing the assistant
    would otherwise answer."""
    import intents
    return (0 < len(text.split()) <= words and intents.match(text) is None
            and not intents.is_stop(text))


def parse(text, state=None):
    """The journey request in ``text``, or None (not for this controller).

    Types: query, select, filter, action, fill, answer, cancel."""
    if state not in STATES:
        return None
    text = normalize(text)
    if not text:
        return None
    if state != 'on' and device_control.answer(text) == 'cancel':
        return dict(type='cancel')
    if state == 'ask':
        answer = device_control.answer(text)
        if answer == 'confirm':
            return dict(type='answer', answer='confirm')
    query = _query(text)
    if query is not None:
        return query
    if state == 'on':
        # "Trag die Fahrt in den Kalender ein" without options: say so instead
        # of reading out today's appointments.
        if _actions(text) and re.search(r'\b(fahrt|verbindung|zug|straßenbahn|bahn)\b', text):
            return dict(type='action', actions=_actions(text), index=None)
        return None
    if state in ('origin', 'destination'):
        found = _origin(text) if state == 'origin' else _destination(text)
        if found is None and _short(text):
            found = 'home' if _HOME.fullmatch(text) else dict(name=_name(text) or text)
        if found is not None:
            return dict(type='fill', field=state, value=found)
    if state == 'calendar' and _short(text):
        return dict(type='fill', field='calendar', value=text)
    actions = _actions(text)
    index = _index(text)
    if actions:
        return dict(type='action', actions=actions, index=index)
    match = _FILTER.search(text)
    if match and state in ('offers', 'ask', 'choice'):
        return dict(type='filter', mode=_mode(match.group(0)))
    if index is not None and state in ('offers', 'ask', 'choice'):
        return dict(type='select', index=index)
    if state == 'ask' and _short(text, 3):
        return dict(type='answer', answer='unclear')   # "vielleicht": nothing happens
    return None


def _clean_place(value, specials):
    if value in specials:
        return value
    if isinstance(value, dict) and isinstance(value.get('name'), str):
        name = normalize(value['name'])[:60]
        return dict(name=name) if name else None
    return None


def clean_request(value):
    """Validate a request from the server (it may come from the network)."""
    if not isinstance(value, dict):
        return None
    kind = value.get('type')
    if kind == 'query':
        mode = value.get('mode') if value.get('mode') in MODES else None
        return dict(type='query', kind='departures' if value.get('kind') == 'departures'
                    else 'trip', origin=_clean_place(value.get('origin'), ('home', 'here')),
                    destination=_clean_place(value.get('destination'), ('city', 'home')),
                    mode=mode)
    if kind in ('select', 'action'):
        index = value.get('index')
        index = index if isinstance(index, int) and not isinstance(index, bool) \
            and -1 <= index <= 2 else None
        if kind == 'select':
            return dict(type='select', index=index) if index is not None else None
        actions = [a for a in ACTIONS if a in (value.get('actions') or [])]
        return dict(type='action', actions=actions, index=index) if actions else None
    if kind == 'filter':
        mode = value.get('mode')
        return dict(type='filter', mode=mode) if mode in MODES else None
    if kind == 'fill':
        field = value.get('field')
        if field == 'calendar' and isinstance(value.get('value'), str):
            return dict(type='fill', field=field, value=normalize(value['value'])[:60])
        if field in ('origin', 'destination'):
            place = _clean_place(value.get('value'), ('home', 'here', 'city'))
            return dict(type='fill', field=field, value=place) if place else None
        return None
    if kind == 'answer' and value.get('answer') in ('confirm', 'cancel', 'unclear'):
        return dict(type='answer', answer=value['answer'])
    if kind == 'cancel':
        return dict(type='cancel')
    return None


# --- spoken text ---------------------------------------------------------------------

WEEKDAYS = ('Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag')


def clock(moment, now=None):
    """'14 Uhr 32'; another day is named ('morgen 0 Uhr 12')."""
    text = agenda_feed._clock(moment)
    if now is None:
        return text
    days = (moment.date() - now.date()).days
    if days == 0:
        return text
    if days == 1:
        return f'morgen {text}'
    return f'{WEEKDAYS[moment.weekday()]}, {moment.day}.{moment.month}., {text}'


def at(moment, now):
    """'um 14 Uhr 20', on another day 'morgen um 0 Uhr'."""
    text = clock(moment, now)
    if text.startswith('morgen '):
        return f'morgen um {text[7:]}'
    return f'um {text}' if text[0].isdigit() else f'{text}'


def day_word(moment, now):
    days = (moment.date() - now.date()).days
    return 'heute' if days == 0 else 'morgen' if days == 1 else \
        f'am {WEEKDAYS[moment.weekday()]}, {moment.day}.{moment.month}.'


def _delay(leg):
    if not leg.get('realtime') or leg.get('dep_planned') is None or leg.get('dep') is None:
        return ''
    minutes = round((leg['dep'] - leg['dep_planned']).total_seconds() / 60)
    if minutes >= 1:
        return f', {minutes} Minuten später als geplant'
    return ''


def _platform(leg, key='platform_dep'):
    return f', Gleis {leg[key]}' if leg.get(key) else ''


def _toward(leg):
    return f' Richtung {leg["direction"]}' if leg.get('direction') else ''


def _minutes(value):
    return '1 Minute' if value == 1 else f'{value} Minuten'


def option_text(number, option, now):
    """One option: line/train, direction, departure, arrival, walk and time
    to leave; trains also with changes and platforms."""
    first = option['vehicles'][0]
    prefix = f'{number}. ' if number else ''
    if option['kind'] == 'departure':
        text = (f"{prefix}{first['label']}{_toward(first)}, {clock(first['dep'], now)}"
                f"{_delay(first)}{_platform(first)}.")
        return text + ('' if first['realtime'] else ' Laut Fahrplan.')
    parts = []
    if option['rail']:
        changes = option['transfers']
        parts.append(f"{prefix}{'Direkt' if not changes else _count(changes)}, "
                     f"Ankunft {clock(option['arr'], now) if option['arr'] else 'unbekannt'}"
                     f"{_platform(option['legs'][-1], 'platform_arr') if option['arr'] else ''}.")
        for leg in option['vehicles']:
            parts.append(f"{leg['label']}{_toward(leg)} ab {leg['origin'] or 'Start'} "
                         f"{clock(leg['dep'], now)}{_delay(leg)}{_platform(leg)}.")
    else:
        arrival = f", an {option['destination'] or 'Ziel'} {clock(option['arr'], now)}" \
            if option['arr'] else ', Ankunft unbekannt'
        parts.append(f"{prefix}{first['label']}{_toward(first)}, ab {first['origin'] or 'Start'} "
                     f"{clock(first['dep'], now)}{_delay(first)}{arrival}.")
    parts.append(_walk_text(option, now))
    if not option['realtime']:
        parts.append('Laut Fahrplan.')
    return ' '.join(part for part in parts if part)


def _count(changes):
    return '1 Umstieg' if changes == 1 else f'{changes} Umstiege'


def _walk_text(option, now):
    if option['walk_minutes'] is None:
        return 'Gehzeit unbekannt.'
    walk = f"{_minutes(option['walk_minutes'])} Fußweg, " if option['walk_minutes'] else ''
    if option['leave'] is None:
        return f'{walk.rstrip(", ")}.' if walk else ''
    return f"{walk}losgehen {clock(option['leave'], now)}."


def offers_text(options, query, now):
    target = query.get('destination_name') or query.get('origin_name') or ''
    if query['kind'] == 'departures':
        head = f"Nächste Abfahrten ab {query.get('origin_name')}." if len(options) > 1 \
            else f"Nächste Abfahrt ab {query.get('origin_name')}."
    else:
        head = (f"{len(options)} Verbindungen nach {target}." if len(options) > 1
                else f"Eine Verbindung nach {target}.")
    lines = [head]
    for number, option in enumerate(options, 1):
        lines.append(option_text(number if len(options) > 1 else None, option, now))
    if len(options) > 1:
        lines.append('Welche? Die erste' + (', zweite oder dritte?' if len(options) == 3
                                            else ' oder zweite?'))
    return ' '.join(lines)


NO_OPTIONS = {
    'trip': "Keine erreichbare Verbindung gefunden.",
    'departures': "Keine passende Abfahrt gefunden.",
}


# --- controller (Pi) ------------------------------------------------------------------

class Step:
    """What a turn does: speak ``text`` now, or run ``work`` in a background
    job and speak what ``apply(result, error)`` returns on the main thread."""

    def __init__(self, text=None, work=None, apply=None, name='journey'):
        self.text, self.work, self.apply, self.name = text, work, apply, name

    def run(self):
        """In the background job: (result, None) or (None, exception)."""
        try:
            return self.work(), None
        except Exception as exc:  # reported by apply() on the main thread
            return None, exc


def _now():
    return datetime.datetime.now(transit.TZ)


class Journeys:
    def __init__(self, client, config, agenda=None, write_calendar=None, now=_now,
                 clock=time.monotonic, log=None, put=agenda_feed.put_event,
                 calendars=agenda_feed.calendars):
        self.client, self.config, self.agenda = client, config, agenda
        self.write_calendar = normalize(write_calendar or '') or None
        self.now, self.clock = now, clock
        self.log = log or (lambda name, **fields: None)
        self.put, self.list_calendars = put, calendars
        self.query = None          # last resolved query (places, mode, names)
        self.options, self.options_at = [], 0.0
        self.selected = None
        self.need = None           # dict(field, until, request/actions)
        self.proposal = None
        self.executed = set()
        self.reminders = []
        self.calendar_cache = None

    @classmethod
    def from_env(cls, agenda=None, env=None, log=None):
        import os
        env = os.environ if env is None else env
        config = transit.Config.from_env(env)
        if config is None:
            return None
        return cls(transit.Client(config), config, agenda,
                   write_calendar=env.get('CALDAV_WRITE_CALENDAR', ''), log=log)

    # --- state -------------------------------------------------------------------------

    def _offers_live(self):
        return bool(self.options) and self.clock() - self.options_at <= OFFER_SECONDS

    def state(self):
        if self._proposal_live():
            return 'ask'
        if self.need is not None and self.clock() <= self.need['until']:
            return self.need['field']
        if self._offers_live():
            return 'offers'
        return 'on'

    def snapshot(self):
        """Fields for the turn snapshot (the server routes follow-ups by them)."""
        state = self.state()
        fields = dict(trip=state)
        if state == 'ask':
            fields['trip_id'] = self.proposal['id']
        return fields

    def _proposal_live(self):
        proposal = self.proposal
        if proposal is None:
            return False
        now = self.clock()
        if now - proposal['created'] > PROPOSAL_MAX_SECONDS or (
                proposal['expires'] is not None and now > proposal['expires']):
            self.log('journey', result='expired', id=proposal['id'])
            self.proposal = None
            return False
        return True

    def take(self):
        """The open proposal, used up by the turn or button press that answers
        it; None if there is none or it expired."""
        if not self._proposal_live():
            return None
        proposal, self.proposal = self.proposal, None
        return proposal

    def parse(self, text, taken=None):
        return parse(text, 'ask' if taken is not None else self.state())

    def spoken(self):
        """The proposal question has been said completely: 90 s from now."""
        if self.proposal is not None and self.proposal['expires'] is None:
            self.proposal['expires'] = self.clock() + CONFIRM_SECONDS

    def cancel(self):
        dropped = self.proposal is not None or self.need is not None
        if self.proposal is not None:
            self.log('journey', result='cancelled', id=self.proposal['id'])
        self.proposal, self.need = None, None
        return dropped

    # --- turns -------------------------------------------------------------------------

    def turn(self, request, speaker=None, guest=False, taken=None, proposal_id=None,
             button=False):
        """One journey turn (``request`` from parse/clean_request). ``taken``: the
        proposal this turn took when it was submitted."""
        kind = request['type']
        if kind != 'answer' and taken is not None:
            self.log('journey', result='replaced', id=taken['id'])
        if kind == 'cancel':
            self.cancel()
            self.options, self.selected = [], None
            return Step("Abgebrochen. Nichts eingetragen." if taken else "Abgebrochen.")
        if kind == 'answer':
            return self._answer(request['answer'], taken, speaker, guest, proposal_id, button)
        need, self.need = self.need, None
        if kind == 'query':
            return self._query(request, guest)
        if kind == 'fill':
            return self._fill(request, need, speaker, guest)
        if kind == 'filter':
            if self.query is None:
                return Step("Keine Fahrt angefragt.")
            return self._run_query(dict(self.query, mode=request['mode']))
        if kind == 'select':
            actions = need['actions'] if need and need['field'] == 'choice' else None
            return self._select(request['index'], actions, speaker, guest)
        if kind == 'action':
            if request.get('index') is not None:
                step = self._select(request['index'], None, speaker, guest, quiet=True)
                if step is not None:
                    return step
            return self._action(request['actions'], speaker, guest)
        return Step("Nicht verstanden.")

    def _ask(self, field, text, **extra):
        self.need = dict(field=field, until=self.clock() + NEED_SECONDS, **extra)
        return Step(text)

    def _query(self, request, guest):
        query = dict(kind=request['kind'], origin=request['origin'],
                     destination=request['destination'], mode=request['mode'])
        return self._resolve(query, guest)

    def _resolve(self, query, guest):
        origin, destination = query['origin'], query['destination']
        if origin == 'here':
            return self._ask('origin', "Ohne Ortung weiß ich nicht, wo hier ist. Von zu Hause "
                             "oder ab welcher Haltestelle?", query=dict(query, origin=None))
        if origin == 'home' and guest:
            return self._ask('origin', "Den Startpunkt zu Hause nenne ich nur dem Bediener. "
                             "Ab welcher Haltestelle?", query=dict(query, origin=None))
        if origin == 'home' and self.config.home is None:
            return self._ask('origin', "Kein Zuhause eingerichtet. Ab welcher Haltestelle?",
                             query=dict(query, origin=None))
        if destination == 'home' and (guest or self.config.home is None):
            return self._ask('destination', "Zuhause ist hier nicht verfügbar. Welche "
                             "Haltestelle?", query=dict(query, destination=None))
        if query['kind'] == 'trip' and origin is None:
            return self._ask('origin', "Von wo aus? Von zu Hause oder ab welcher Haltestelle?",
                             query=query)
        if destination == 'city' and not self.config.city_stop:
            return self._ask('destination', "Für in die Stadt ist noch keine Zielhaltestelle "
                             "festgelegt. Welche Haltestelle?", query=dict(query, destination=None))
        if query['kind'] == 'trip' and destination is None:
            return self._ask('destination', "Wohin?", query=query)
        return self._run_query(query)

    def _fill(self, request, need, speaker, guest):
        if need is None or need['field'] != request['field']:
            return Step("Keine offene Frage.")
        if request['field'] == 'calendar':
            found = self._match_calendar(request['value'], need['calendars'])
            if found is None:
                names = ', '.join(name for _, name in need['calendars'])
                return self._ask('calendar', f"Kalender nicht erkannt. Zur Wahl: {names}.",
                                 **{k: v for k, v in need.items() if k not in ('field', 'until')})
            return self._propose(need['option'], need['actions'], found, speaker, guest)
        query = dict(need['query'])
        query[request['field']] = request['value']
        if request['field'] == 'destination' and query['kind'] == 'departures':
            query['kind'] = 'trip'
        return self._resolve(query, guest)

    def _place(self, value, mode):
        """('stop', id) / ('coord', lat, lon) and a spoken name, in the background job."""
        if value == 'home':
            return ('coord',) + tuple(self.config.home), 'Hause'
        if value == 'city':
            return ('stop', self.config.city_stop), 'Stadt'
        stop = self.client.find_stop(value['name'], mode)
        if stop is None:
            raise LookupError(value['name'])
        return ('stop', stop['id']), stop['name']

    @staticmethod
    def _private(option, query):
        """EFA names a coordinate by its street address: the home end of a
        journey is called 'zu Hause' in speech, calendar and journal instead."""
        option['query'] = query
        if query.get('origin') == 'home':
            option['legs'][0]['origin'] = 'zu Hause'
        if query.get('destination') == 'home':
            option['legs'][-1]['destination'] = 'zu Hause'
            option['destination'] = 'zu Hause'
        return option

    def _run_query(self, query):
        def work():
            now = self.now()
            resolved = dict(query)
            resolved['origin_place'], resolved['origin_name'] = self._place(query['origin'],
                                                                            query['mode'])
            if query['kind'] == 'departures':
                found = self.client.departures(resolved['origin_place'][1], now)
                return resolved, transit.departure_options(found, now, query['mode']), now
            resolved['destination_place'], resolved['destination_name'] = self._place(
                query['destination'], query['mode'])
            count = transit.MAX_OPTIONS * (2 if query['mode'] else 1)
            found = self.client.trips(resolved['origin_place'], resolved['destination_place'],
                                      now, count=count)
            return resolved, transit.trip_options(found, now, self.config.buffer_minutes,
                                                  query['mode']), now

        def apply(result, error):
            if error is not None:
                if isinstance(error, LookupError):
                    return f"Haltestelle {error.args[0]} nicht gefunden."
                if isinstance(error, transit.TransitError) and error.kind == 'no_result':
                    return NO_OPTIONS[query['kind']]
                self.log('journey', result='query_failed', message=str(error))
                return "Fahrplanauskunft nicht erreichbar."
            resolved, options, now = result
            for option in options:
                self._private(option, resolved)
            self.query = resolved
            self.options, self.options_at = options, self.clock()
            self.selected = 0 if len(options) == 1 else None
            self.log('journey', result='options', kind=query['kind'], count=len(options),
                     mode=query['mode'])
            if not options:
                return NO_OPTIONS[query['kind']]
            return offers_text(options, resolved, now)
        return Step(work=work, apply=apply, name='query')

    def _select(self, index, actions, speaker, guest, quiet=False):
        if not self._offers_live():
            return Step("Keine Verbindungen zur Auswahl.")
        if index == -1:
            index = len(self.options) - 1
        if index >= len(self.options):
            return Step(f"Es gibt nur {len(self.options)} Verbindungen.")
        self.selected = index
        if actions:
            return self._action(actions, speaker, guest)
        if quiet:
            return None
        option = self.options[index]
        return Step(f"Gewählt: {option_text(None, option, self.now())} "
                    "Erinnerung, Kalender oder beides?")

    def _action(self, actions, speaker, guest):
        if guest:
            return Step("Erinnerungen und Kalendereinträge nur für den Bediener.")
        if not self._offers_live():
            return Step("Erst eine Fahrt abfragen.")
        if self.selected is None:
            return self._ask('choice', "Welche Verbindung? Die erste"
                             + (', zweite oder dritte?' if len(self.options) == 3
                                else ' oder zweite?'), actions=actions)
        option = self.options[self.selected]
        if 'calendar' not in actions:
            return self._propose(option, actions, None, speaker, guest)
        if self.agenda is None or not getattr(self.agenda, 'configured', False):
            return Step("Kein Kalender eingerichtet. Nichts eingetragen.")
        if self.calendar_cache is not None:
            return self._with_calendars(option, actions, self.calendar_cache, speaker, guest)

        def work():
            return self.list_calendars(self.agenda.config)

        def apply(result, error):
            if error is not None:
                self.log('journey', result='calendar_list_failed', message=str(error))
                return "Kalender nicht erreichbar. Nichts eingetragen."
            self.calendar_cache = result
            step = self._with_calendars(option, actions, result, speaker, guest)
            return step.text
        return Step(work=work, apply=apply, name='calendars')

    def _with_calendars(self, option, actions, found, speaker, guest):
        if not found:
            return Step("Kein beschreibbarer Kalender gefunden.")
        if self.write_calendar:
            chosen = self._match_calendar(self.write_calendar, found)
            if chosen is not None:
                return self._propose(option, actions, chosen, speaker, guest)
        if len(found) == 1:
            return self._propose(option, actions, found[0], speaker, guest)
        names = ', '.join(name for _, name in found)
        return self._ask('calendar', f"In welchen Kalender? {names}.", calendars=found,
                         option=option, actions=actions)

    @staticmethod
    def _match_calendar(value, found):
        value = normalize(value)
        for url, name in found:
            if value in (normalize(name), url.rstrip('/').rsplit('/', 1)[-1].lower()):
                return (url, name)
        hits = [(url, name) for url, name in found if value and value in normalize(name)]
        return hits[0] if len(hits) == 1 else None

    def _alarm(self, option):
        """Time of the reminder: the time to leave; at a departure board (no
        walk known) a few minutes before departure, said as such."""
        if option['leave'] is not None:
            return option['leave']
        if option['kind'] == 'departure':
            return option['dep'] - datetime.timedelta(minutes=max(1, self.config.buffer_minutes))
        return None

    def _propose(self, option, actions, calendar, speaker, guest, changed=False):
        if guest:
            return Step("Erinnerungen und Kalendereinträge nur für den Bediener.")
        now = self.now()
        alarm = self._alarm(option)
        if 'remind' in actions and alarm is None:
            return Step("Gehzeit unbekannt, deshalb keine Erinnerung möglich. Nichts gesetzt.")
        if alarm is not None and alarm < transit._minute(now):
            return Step("Diese Fahrt ist nicht mehr erreichbar. Nichts gesetzt.")
        self.proposal = dict(id=secrets.token_hex(8), option=option, actions=tuple(actions),
                             calendar=calendar, speaker=speaker, created=self.clock(),
                             expires=None, alarm=alarm)
        self.log('journey', result='proposed', id=self.proposal['id'], actions=list(actions),
                 calendar=calendar[1] if calendar else None)
        return Step(("Die Fahrt hat sich geändert. " if changed else "")
                    + proposal_text(self.proposal, now))

    def _answer(self, answer, taken, speaker, guest, proposal_id, button):
        if taken is None or (proposal_id is not None and proposal_id != taken['id']):
            return Step("Keine offene Bestätigung. Nichts ausgeführt.")
        if answer == 'cancel':
            self.log('journey', result='cancelled', id=taken['id'])
            return Step("Abgebrochen. Nichts eingetragen.")
        if answer != 'confirm':
            self.log('journey', result='unclear', id=taken['id'])
            return Step("Unklare Antwort. Nichts eingetragen.")
        if not button and (guest or speaker != taken['speaker']):
            self.log('journey', result='voice_refused', id=taken['id'])
            return Step("Stimme passt nicht zum Vorschlag. Nichts eingetragen. Bestätigung "
                        "nur durch denselben Bediener oder Taste E.")
        if taken['id'] in self.executed:
            return Step("Bereits ausgeführt.")
        self.executed.add(taken['id'])
        self.log('journey', result='confirmed', id=taken['id'], button=button)
        return Step(work=lambda: self._execute(taken),
                    apply=lambda result, error: self._executed(taken, result, error),
                    name='execute')

    # --- execution (background job, then main thread) ------------------------------------

    def _refresh(self, option):
        query, now = option['query'], self.now()
        if option['kind'] == 'departure':
            found = transit.departure_options(
                self.client.departures(query['origin_place'][1], now), now, None, limit=20)
        else:
            # From the planned start of the chain, so a delayed journey is found again.
            begin = option['legs'][0].get('dep_planned') or option['vehicles'][0]['dep_planned']
            journeys = self.client.trips(query['origin_place'], query['destination_place'],
                                         min(now, begin - datetime.timedelta(minutes=1)),
                                         count=transit.MAX_OPTIONS * 2)
            found = [item for item in (transit.plan(j, self.config.buffer_minutes)
                                       for j in journeys) if item is not None]
        for item in found:
            if item['key'] == option['key']:
                return self._private(item, query), now
        return None, now

    @staticmethod
    def _material(old, new):
        def moved(a, b):
            return (a is None) != (b is None) or (a is not None and abs(a - b) >= CHANGE)
        first_old, first_new = old['vehicles'][0], new['vehicles'][0]
        return (moved(old['dep'], new['dep']) or moved(old['leave'], new['leave'])
                or moved(old.get('arr'), new.get('arr'))
                or first_old.get('platform_dep') != first_new.get('platform_dep'))

    def _execute(self, proposal):
        """Background: query the journey again, then write the calendar entry."""
        option = proposal['option']
        try:
            fresh, now = self._refresh(option)
        except transit.TransitError as exc:
            return dict(result='unavailable', message=str(exc))
        if fresh is None:
            return dict(result='gone')
        if fresh['vehicles'][0]['cancelled']:
            return dict(result='gone')
        if self._material(option, fresh):
            return dict(result='changed', option=fresh)
        alarm = self._alarm(fresh)
        if (alarm or fresh['dep']) < transit._minute(now):
            return dict(result='missed')
        calendar = None
        if 'calendar' in proposal['actions']:
            url, _ = proposal['calendar']
            ics = journey_ics(fresh, proposal['id'], alarm)
            calendar = self.put(self.agenda.config, url, event_uid(fresh), ics)
            if calendar in ('created', 'exists'):
                try:
                    self.agenda.refresh()
                except Exception:   # the entry exists; a stale agenda is no failure
                    pass
        return dict(result='done', option=fresh, calendar=calendar, alarm=alarm)

    def _executed(self, proposal, result, error):
        if error is not None:
            self.log('journey', result='failed', id=proposal['id'], message=str(error))
            if 'calendar' in proposal['actions']:
                return "Ergebnis unklar. Bitte im Kalender nachsehen. Keine Erinnerung gesetzt."
            return "Fehler. Keine Erinnerung gesetzt."
        outcome = result['result']
        self.log('journey', result=outcome, id=proposal['id'],
                 calendar=result.get('calendar'))
        if outcome == 'unavailable':
            return "Fahrplan nicht erreichbar, Fahrt nicht geprüft. Nichts eingetragen."
        if outcome == 'gone':
            return "Die gewählte Fahrt gibt es nicht mehr. Nichts eingetragen."
        if outcome == 'missed':
            return "Die Fahrt ist nicht mehr erreichbar. Nichts eingetragen."
        if outcome == 'changed':
            return self._propose(result['option'], proposal['actions'], proposal['calendar'],
                                 proposal['speaker'], False, changed=True).text
        now, parts = self.now(), []
        option, alarm = result['option'], result['alarm']
        if 'remind' in proposal['actions']:
            self.reminders.append(dict(id=proposal['id'], at=alarm, dep=option['dep'],
                                       text=reminder_text(option)))
            parts.append(f"Erinnerung hier am Gerät {at(alarm, now)} gesetzt.")
        calendar = result.get('calendar')
        if calendar is not None:
            name = proposal['calendar'][1]
            parts.append({
                'created': f"In {name} eingetragen.",
                'exists': f"Die Fahrt steht bereits in {name}.",
                'denied': f"{name} verweigert den Zugriff. Nicht eingetragen.",
                'failed': "Kalendereintrag fehlgeschlagen.",
                'unknown': "Ob der Kalendereintrag angekommen ist, ist unklar. Bitte prüfen.",
            }[calendar])
        return ' '.join(parts)

    @staticmethod
    def finish(step, outcome, job_error=None):
        """Main thread, after the background job of ``step`` (``outcome`` from
        Step.run, or None when the job itself failed)."""
        result, error = outcome if outcome is not None else (None, RuntimeError(job_error))
        return step.apply(result, error)

    def due(self, now=None):
        """Pi reminders whose time has come: each said exactly once, never
        after the journey has been missed."""
        if not self.reminders:
            return []
        now = now or self.now()
        texts, waiting = [], []
        for reminder in self.reminders:
            if now < reminder['at']:
                waiting.append(reminder)
            elif now <= reminder['at'] + REMINDER_GRACE and now < reminder['dep']:
                self.log('journey', result='reminded', id=reminder['id'])
                texts.append(reminder['text'])
            else:
                self.log('journey', result='reminder_missed', id=reminder['id'])
        self.reminders = waiting
        return texts


def proposal_text(proposal, now):
    """The concrete question: journey times and reminder times separately."""
    option, alarm, actions = proposal['option'], proposal['alarm'], proposal['actions']
    start, end = option['dep'], option['arr']
    span = f"um {clock(start)}" if end is None else f"von {clock(start)} bis {clock(end, start)}"
    when = f"{day_word(start, now)} {span}"
    first = option['vehicles'][0]
    what = f"{first['label']}{_toward(first)}"
    parts = []
    if 'calendar' in actions:
        reminder = (f" und eine Kalender-Erinnerung {at(alarm, now)} setzen"
                    if alarm is not None else " ohne Erinnerung, Gehzeit unbekannt,")
        parts.append(f"diese Fahrt mit {what} {when} in {proposal['calendar'][1]} "
                     f"eintragen{reminder}")
    if 'remind' in actions:
        lead = "dich" if 'calendar' in actions else f"dich für {what} {when}"
        detail = ' vor Abfahrt' if option['leave'] is None else ''
        parts.append(f"{lead} {at(alarm, now)}{detail} hier am Gerät erinnern")
    question = f"Soll ich {' und '.join(parts)}?"
    if 'remind' in actions:
        question += (" Die Erinnerung am Gerät liegt nur im Arbeitsspeicher: das Gerät muss "
                     "laufen, ein Neustart löscht sie.")
    return question + " Ja oder Taste E."


def reminder_text(option):
    first = option['vehicles'][0]
    if option['kind'] == 'departure':
        return (f"Erinnerung: {first['label']}{_toward(first)} fährt um {clock(first['dep'])}"
                f"{_platform(first)}.")
    return (f"Zeit zu gehen. {first['label']}{_toward(first)} fährt um {clock(first['dep'])} "
            f"ab {first['origin'] or 'der Haltestelle'}{_platform(first)}.")


def event_uid(option):
    """Stable: the same journey gives the same resource (no duplicates)."""
    return f"journey-{option['key']}@pi-voice-assistant"


def journey_ics(option, proposal_id, alarm, stamp=None):
    vehicles = option['vehicles']
    query = option.get('query') or {}
    target = query.get('destination_name') or option.get('destination') or ''
    lines = ', '.join(leg['label'] for leg in vehicles)
    summary = f"Fahrt nach {target} ({lines})" if target and option['kind'] != 'departure' \
        else f"{lines}{_toward(vehicles[0])}"
    details = []
    for leg in option['legs']:
        start = leg['dep'].strftime('%H:%M') if leg.get('dep') else '?'
        end = leg['arr'].strftime('%H:%M') if leg.get('arr') else '?'
        if leg['kind'] == 'walk':
            minutes = -(-leg['duration'] // 60) if leg.get('duration') is not None else None
            distance = f", {leg['distance']} m" if leg.get('distance') else ''
            details.append(f"{start} Fußweg {minutes if minutes is not None else '?'} min"
                           f"{distance} nach {leg.get('destination') or '?'}")
        else:
            platform = f" (Gleis {leg['platform_dep']})" if leg.get('platform_dep') else ''
            arrival = f" an {leg.get('destination')} {end}" if leg.get('destination') else ''
            details.append(f"{start} {leg['label']}{_toward(leg)} ab {leg.get('origin') or '?'}"
                           f"{platform}{arrival}")
    if alarm is not None and option['leave'] is not None:
        details.append(f"Losgehen: {option['leave'].strftime('%H:%M')}")
    if not option['realtime']:
        details.append('Zeiten laut Fahrplan.')
    return agenda_feed.event_ics(
        event_uid(option), option['dep'], option.get('arr'), summary,
        description='\n'.join(details), location=vehicles[0].get('origin') or '',
        alarm=alarm, alarm_text=f"Losgehen: {summary}", stamp=stamp)
