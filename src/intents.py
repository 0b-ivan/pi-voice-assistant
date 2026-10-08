"""Questions answered without the LLM: time, date, status, battery, identity.

Runs on the server (normal path, before OpenRouter) and on the Pi (local
fallback), so these answers also work offline. Input is the recognized text
(Vosk: lower case, no punctuation); output is a short Servitor sentence that
Piper reads well (numbers as digits, dates as words).
"""
import re

from system_status import battery_sentence, status_text

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
    ('identity', re.compile(r'\b(wer bist du|wie heißt du|was bist du)\b')),
)
IDENTITY = ("Diese Einheit ist SERVITOR. Sprachgesteuerte Diensteinheit. "
            "Funktion: Anfragen des Bedieners beantworten.")


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


def time_text(now):
    if now.minute == 0:
        return f"Zeitindex: {now.hour} Uhr."
    return f"Zeitindex: {now.hour} Uhr {now.minute}."


def date_text(now):
    return (f"Datum: {WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} "
            f"{MONTHS[now.month - 1]} {now.year}.")


def answer(intent, now, snapshot=None):
    snapshot = snapshot or {}
    if intent == 'time':
        return time_text(now)
    if intent == 'date':
        return date_text(now)
    if intent == 'status':
        return status_text(snapshot)
    if intent == 'battery':
        return battery_sentence(snapshot) or "Energiedaten nicht verfügbar."
    if intent == 'identity':
        return IDENTITY
    raise ValueError(f'unknown intent {intent!r}')
