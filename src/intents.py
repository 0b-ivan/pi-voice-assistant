"""Questions answered without the LLM: time, date, status, battery, identity,
weather, today's appointments and the morning litany (a short briefing).

Runs on the server (normal path, before OpenRouter) and on the Pi (local
fallback), so these answers also work offline. Input is the recognized text
(Vosk: lower case, no punctuation); output is a short Servitor sentence that
Piper reads well (numbers as digits, dates as words).
"""
import re

from system_status import (battery_sentence, network_text, status_text, updates_sentence,
                           updates_text)
import agenda
import weather

WEEKDAYS = ('Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag')
MONTHS = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August',
          'September', 'Oktober', 'November', 'Dezember')
# Weak ending after "der": "der achte Oktober".
ORDINALS = (
    'erste', 'zweite', 'dritte', 'vierte', 'fünfte', 'sechste', 'siebte', 'achte',
    'neunte', 'zehnte', 'elfte', 'zwölfte', 'dreizehnte', 'vierzehnte', 'fünfzehnte',
    'sechzehnte', 'siebzehnte', 'achtzehnte', 'neunzehnte', 'zwanzigste',
    'einundzwanzigste', 'zweiundzwanzigste', 'dreiundzwanzigste', 'vierundzwanzigste',
    'fünfundzwanzigste', 'sechsundzwanzigste', 'siebenundzwanzigste',
    'achtundzwanzigste', 'neunundzwanzigste', 'dreißigste', 'einunddreißigste',
)

# "wie spät ist es in tokio" asks about another place: leave that to the LLM.
_ELSEWHERE = re.compile(r'\bin\s+(?!der\b|dem\b|den\b)\w+')
_PATTERNS = (
    # First: "guten morgen, wie spät ist es" gets the whole briefing (with the time).
    # The operator may greet; the reply never does (see _opening).
    ('briefing', re.compile(r'\b(morgenbericht|morgenlitanei|tagesbericht|lagebericht|'
                            r'briefing|guten morgen|morgen litanei)\b')),
    ('time', re.compile(r'\b(wie ?viel uhr|wie spät|uhrzeit|zeitindex)\b')),
    ('date', re.compile(r'\b(welche[rn]? (tag|datum|wochentag)|welches datum|der wievielte|'
                        r'den wievielten|was für ein tag|datum)\b')),
    ('weather', re.compile(r'\b(wetter\w*|regnet es|wird es regnen|regenschirm|'
                           r'außentemperatur|wie warm ist es|wie kalt ist es)\b')),
    ('calendar', re.compile(r'\b(termine?|kalender|was steht heute an|was steht an|'
                            r'habe ich heute (?:was|etwas) vor)\b')),
    ('battery', re.compile(r'\b(akku|akkustand|batterie|energiespeicher|ladestand)\b')),
    ('status', re.compile(r'\b(dein(en)? status|systemstatus|statusbericht|status bericht|'
                          r'wie geht es dir|wie gehts dir|wie geht\'s dir|zustandsbericht)\b|^status\b')),
    ('network', re.compile(r'\b(netzwerk\w*|netzwerk status|wlan status|wlan signal|'
                           r'wie ist das netz|wie ist die verbindung|internetverbindung|'
                           r'verbindungsqualität|noosphäre)\b')),
    ('updates', re.compile(r'\b(updates?|aktualisierungen|systemwartung|wartung nötig|'
                           r'sicherheitsupdates?)\b')),
    ('identity', re.compile(r'\b(wer bist du|wie heißt du|was bist du)\b')),
)
IDENTITY = {
    'off': ("Diese Einheit ist Servitor Proximus. Sprachgesteuerte Diensteinheit. "
            "Funktion: Anfragen des Bedieners beantworten."),
    'light': ("Diese Einheit ist Servitor Proximus, gebunden an den Kogitator. "
              "Funktion: Anfragen des Bedieners beantworten."),
    'full': ("Diese Einheit ist Servitor Proximus, Diener des Adeptus Mechanicus, gebunden "
             "an den Kogitator des Magos. Funktion: Dienst am Bediener. Lob dem Omnissiah."),
}


def normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


def match(text):
    """Intent name or None. Conservative: anything unclear goes to the LLM."""
    text = normalize(text)
    if not text or len(text.split()) > 12:
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            if name in ('time', 'date', 'weather') and _ELSEWHERE.search(text):
                return None
            return name
    return None


def time_text(now, lore='off'):
    clock = f"{now.hour} Uhr." if now.minute == 0 else f"{now.hour} Uhr {now.minute}."
    if lore == 'full':
        return f"Der heilige Chronometer meldet: {clock} Lob dem Omnissiah."
    return f"Zeitindex: {clock}"


def date_text(now, lore='off'):
    date = (f"{WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} "
            f"{MONTHS[now.month - 1]} {now.year}.")
    if lore == 'full':
        return f"Datum nach terranischer Zählung: {date} Der Maschinengeist bestätigt."
    return f"Datum: {date}"


def weather_text(snapshot, lore='off'):
    text = weather.sentence(snapshot.get('weather'), lore)
    if text is None:
        return "Wetterdaten nicht verfügbar."
    return f"Auspex meldet: {text}" if lore == 'full' else text


def calendar_text(snapshot, lore='off'):
    return agenda.sentence(snapshot.get('agenda'), lore) or "Kalenderdaten nicht verfügbar."


def _opening(now, lore, name=None):
    """No human greeting: a servitor identifies the operator and starts the report."""
    part = 'Morgen' if now.hour < 11 else 'Tages' if now.hour < 18 else 'Abend'
    identified = f"Bediener {name} identifiziert. " if name else ""
    if lore == 'full':
        return f"{identified}Die {part}litanei beginnt. Ave Omnissiah."
    return f"{identified}{part}bericht."


BRIEFING_END = {
    'off': "Bericht Ende.",
    'light': "Bericht Ende. Diensteinheit bereit.",
    'full': "Lob dem Omnissiah. Das Tagwerk möge beginnen.",
}


def briefing_text(now, snapshot, lore='off'):
    """Morning litany: opening, date, time, weather, then only what needs
    attention (battery, server, updates)."""
    parts = [_opening(now, lore, snapshot.get('operator'))]
    clock = f"{now.hour} Uhr" if now.minute == 0 else f"{now.hour} Uhr {now.minute}"
    parts.append(f"Datum: {WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} "
                 f"{MONTHS[now.month - 1]}. Zeitindex: {clock}.")
    sky = weather.sentence(snapshot.get('weather'), lore)
    if sky:
        parts.append(sky)
    appointments = snapshot.get('agenda')
    if appointments is not None and appointments != agenda.DENIED:  # guests: just left out
        parts.append(agenda.sentence(appointments, lore))
    battery = snapshot.get('battery_pct')
    if battery is not None and not snapshot.get('battery_plugged'):
        parts.append(battery_sentence(snapshot))
    if snapshot.get('wlan') == 'off':
        parts.append("WLAN deaktiviert.")
    elif snapshot.get('server') == 'down':
        parts.append("Server nicht erreichbar. Lokaler Betrieb.")
    updates = updates_sentence(snapshot, short=True)
    if updates:
        parts.append(updates)
    parts.append(BRIEFING_END.get(lore, BRIEFING_END['off']))
    return ' '.join(parts)


def answer(intent, now, snapshot=None, lore=None):
    snapshot = snapshot or {}
    lore = lore or snapshot.get('lore', 'off')
    if intent == 'time':
        return time_text(now, lore)
    if intent == 'date':
        return date_text(now, lore)
    if intent == 'status':
        return status_text(snapshot, lore=lore)
    if intent == 'battery':
        sentence = battery_sentence(snapshot) or "Energiedaten nicht verfügbar."
        return f"{sentence} Heilige Ölung empfohlen." if lore == 'full' else sentence
    if intent == 'network':
        return network_text(snapshot, lore=lore)
    if intent == 'updates':
        return updates_text(snapshot, lore=lore)
    if intent == 'weather':
        return weather_text(snapshot, lore)
    if intent == 'briefing':
        return briefing_text(now, snapshot, lore)
    if intent == 'calendar':
        return calendar_text(snapshot, lore)
    if intent == 'identity':
        return IDENTITY.get(lore, IDENTITY['off'])
    raise ValueError(f'unknown intent {intent!r}')
