"""Questions answered without the LLM: time, date, status, battery, identity.

Runs on the server (normal path, before OpenRouter) and on the Pi (local
fallback), so these answers also work offline. Input is the recognized text
(Vosk: lower case, no punctuation); output is a short Servitor sentence that
Piper reads well (numbers as digits, dates as words).
"""
import re

from system_status import battery_sentence, network_text, status_text, updates_text

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
    ('time', re.compile(r'\b(wie ?viel uhr|wie spät|uhrzeit|zeitindex)\b')),
    ('date', re.compile(r'\b(welche[rn]? (tag|datum|wochentag)|welches datum|der wievielte|'
                        r'den wievielten|was für ein tag|datum)\b')),
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
            if name in ('time', 'date') and _ELSEWHERE.search(text):
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
    if intent == 'identity':
        return IDENTITY.get(lore, IDENTITY['off'])
    raise ValueError(f'unknown intent {intent!r}')
