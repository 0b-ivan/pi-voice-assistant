"""Questions answered without the LLM: time, date, status, battery, identity,
weather, today's appointments and the morning litany (a short briefing).

Runs on the server (normal path, before OpenRouter) and on the Pi (local
fallback), so these answers also work offline. Input is the recognized text
(Vosk: lower case, no punctuation); output is a short Servitor sentence that
Piper reads well (numbers as digits, dates as words).
"""
import re

from system_status import (battery_sentence, is_billy, network_text, phrase_style, status_text,
                           updates_sentence, updates_text)
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
    # STT often splits compounds: "morgen bericht", "tages bericht".
    ('briefing', re.compile(r'\b(morgen ?bericht|morgen ?litanei|tages ?bericht|lage ?bericht|'
                            r'briefing|guten morgen)\b')),
    ('time', re.compile(r'\b(wie ?viel uhr|wie spät|uhrzeit|zeitindex)\b')),
    ('date', re.compile(r'\b(welche[rn]? (tag|datum|wochentag)|welches datum|der wievielte|'
                        r'den wievielten|was für ein tag|datum)\b')),
    ('weather', re.compile(r'\b(wetter\w*|regnet es|wird es (\w+ )?regnen|regenschirm|'
                           r'außentemperatur|wie warm (ist|wird) es|wie kalt (ist|wird) es)\b')),
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
# "Wer bist du?" and "Wie geht es dir?" ask about the speaker himself: the
# machine answers with fixed lines, Billy (persona "mensch") in his own words.
_PERSONAL = re.compile(r"\b(wie geht es dir|wie gehts dir|wie geht's dir)\b")

# "Stop", "Sei still", "Klappe halten" ...: end the current answer and wait.
# Only when the whole utterance is such a phrase (plus fillers), so questions
# like "wie stoppt man eine blutung" still reach the LLM.
_STOP = re.compile(
    r"(stop+|abbruch|abbrechen|brich ab|aufhören|hör auf|hör sofort auf|"
    r"sei still|sei ruhig|ruhe|still|schweig|schweige|"
    r"klappe|klappe halten|halt die klappe|halt den mund|mund halten|"
    r"schnauze|halt die schnauze|genug|das reicht|es reicht|vergiss es|ende|aus)")
_FILLER = {'bitte', 'jetzt', 'sofort', 'endlich', 'mal', 'doch', 'einfach', 'du', 'ok', 'okay',
           'hey', 'proximus', 'servitor', 'billy', 'danke', 'nein', 'schon', 'ja'}


def is_stop(text):
    """True when the utterance only tells the assistant to stop or be quiet."""
    words = [word for word in normalize(text).split() if word not in _FILLER]
    return bool(words) and len(words) <= 4 and bool(_STOP.fullmatch(' '.join(words)))


IDENTITY = {
    'off': ("Diese Einheit ist Servitor Proximus. Sprachgesteuerte Diensteinheit. "
            "Funktion: Anfragen des Bedieners beantworten."),
    'light': ("Diese Einheit ist Servitor Proximus, gebunden an den Kogitator. "
              "Funktion: Anfragen des Bedieners beantworten."),
    'full': ("Diese Einheit ist Servitor Proximus, Diener des Adeptus Mechanicus, gebunden "
             "an den Kogitator des Magos. Funktion: Dienst am Bediener. Lob dem Omnissiah."),
    # Billy normally answers this himself through the LLM; these are fallbacks.
    'billy': "Billy. Ein alter Soldat in einer Maschine. Was brauchst du?",
    'billy_full': ("Sergeant William Joseph Blazkowicz der Zweite, Imperiale Armee. "
                   "Jedenfalls das, was das Mechanicus von mir übrig gelassen hat."),
}


def normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


def match(text, persona=None):
    """Intent name or None. Conservative: anything unclear goes to the LLM."""
    text = normalize(text)
    if not text or len(text.split()) > 12:
        return None
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            if name in ('time', 'date', 'weather') and _ELSEWHERE.search(text):
                return None
            if persona == 'mensch' and (name == 'identity' or _PERSONAL.search(text)):
                return None
            return name
    return None


def time_text(now, lore='off'):
    clock = f"{now.hour} Uhr." if now.minute == 0 else f"{now.hour} Uhr {now.minute}."
    if lore == 'billy':
        return f"Es ist {clock}"
    if lore == 'billy_full':
        return f"Es ist {clock} Wachablösung ist noch nicht."
    if lore == 'full':
        return f"Der heilige Chronometer meldet: {clock} Lob dem Omnissiah."
    return f"Zeitindex: {clock}"


def date_text(now, lore='off'):
    date = (f"{WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} "
            f"{MONTHS[now.month - 1]} {now.year}.")
    if is_billy(lore):
        return f"Heute ist {date}"
    if lore == 'full':
        return f"Datum nach terranischer Zählung: {date} Der Maschinengeist bestätigt."
    return f"Datum: {date}"


_DAY_NAMES = ('montag', 'dienstag', 'mittwoch', 'donnerstag', 'freitag', 'samstag', 'sonntag')


def weather_day(text, today):
    """Which day a weather question is about: 0 today ... 4, or None if it is
    beyond the five-day forecast. "heute morgen"/"guten morgen" mean today."""
    text = normalize(text)
    if re.search(r'\bübermorgen\b', text):
        return 2
    if re.search(r'(?<!heute )(?<!guten )\bmorgen\b', text):
        return 1
    for index, name in enumerate(_DAY_NAMES):
        if re.search(rf'\b{name}\b', text):
            offset = (index - today.weekday()) % 7
            return offset if offset < weather.DAYS else None
    return 0


def weather_text(snapshot, lore='off'):
    day = snapshot.get('weather_day', 0)
    if day is None:
        return ("So weit reicht meine Vorhersage nicht, Boss." if is_billy(lore)
                else "Vorhersage reicht nur fünf Tage.")
    text = weather.day_sentence(snapshot.get('weather'), day, lore)
    if text is None:
        return "Keine Wetterdaten, Boss." if is_billy(lore) else "Wetterdaten nicht verfügbar."
    if lore == 'billy_full':
        return f"Lage draußen: {text}"
    return f"Auspex meldet: {text}" if lore == 'full' else text


def calendar_text(snapshot, lore='off'):
    return agenda.sentence(snapshot.get('agenda'), lore) or "Kalenderdaten nicht verfügbar."


def _opening(now, lore, name=None):
    """No human greeting: a servitor identifies the operator and starts the report."""
    part = 'Morgen' if now.hour < 11 else 'Tages' if now.hour < 18 else 'Abend'
    if lore == 'billy':
        return f"Morgen, {name or 'Boss'}." if part == 'Morgen' else f"Hallo {name or 'Boss'}."
    if lore == 'billy_full':
        return f"{part}appell, {name or 'Gardist'}. Stillgestanden, war ein Witz."
    identified = f"Bediener {name} identifiziert. " if name else ""
    if lore == 'full':
        return f"{identified}Die {part}litanei beginnt. Ave Omnissiah."
    return f"{identified}{part}bericht."


BRIEFING_END = {
    'off': "Bericht Ende.",
    'light': "Bericht Ende. Diensteinheit bereit.",
    'full': "Lob dem Omnissiah. Das Tagwerk möge beginnen.",
    'billy': "Das war's. Los geht's.",
    'billy_full': "Das war's. Wegtreten.",
}


def briefing_text(now, snapshot, lore='off'):
    """Morning litany: opening, date, time, weather, then only what needs
    attention (battery, server, updates)."""
    parts = [_opening(now, lore, snapshot.get('operator'))]
    clock = f"{now.hour} Uhr" if now.minute == 0 else f"{now.hour} Uhr {now.minute}"
    day = f"{WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} {MONTHS[now.month - 1]}"
    parts.append(f"Heute ist {day}, es ist {clock}." if is_billy(lore)
                 else f"Datum: {day}. Zeitindex: {clock}.")
    sky = weather.sentence(snapshot.get('weather'), lore)
    if sky:
        parts.append(sky)
    appointments = snapshot.get('agenda')
    if appointments is not None and appointments != agenda.DENIED:  # guests: just left out
        parts.append(agenda.sentence(appointments, lore))
    battery = snapshot.get('battery_pct')
    if battery is not None and not snapshot.get('battery_plugged'):
        parts.append(battery_sentence(snapshot, lore))
    if snapshot.get('wlan') == 'off':
        parts.append("WLAN ist aus." if is_billy(lore) else "WLAN deaktiviert.")
    elif snapshot.get('server') == 'down':
        parts.append("Der Server antwortet nicht, ich mach allein weiter." if is_billy(lore)
                     else "Server nicht erreichbar. Lokaler Betrieb.")
    updates = updates_sentence(snapshot, short=True)
    if updates:
        parts.append(updates)
    parts.append(BRIEFING_END.get(lore, BRIEFING_END['off']))
    return ' '.join(parts)


def answer(intent, now, snapshot=None, lore=None):
    snapshot = snapshot or {}
    lore = lore or phrase_style(snapshot.get('lore'), snapshot.get('persona'))
    if intent == 'time':
        return time_text(now, lore)
    if intent == 'date':
        return date_text(now, lore)
    if intent == 'status':
        return status_text(snapshot, lore=lore)
    if intent == 'battery':
        sentence = battery_sentence(snapshot, lore)
        if sentence is None:
            return "Keine Akkudaten, Boss." if is_billy(lore) else "Energiedaten nicht verfügbar."
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
