"""Questions answered without the LLM: time, date, status, battery, identity,
weather, today's appointments, the self-test and the morning litany (a short
briefing).

Runs on the server (normal path, before OpenRouter) and on the Pi (local
fallback), so these answers also work offline. Input is the recognized text
(Vosk: lower case, no punctuation); output is a short Servitor sentence that
Piper reads well (numbers as digits, dates as words).
"""
import re

from system_status import (battery_sentence, is_billy, network_text, phrase_style, status_text,
                           updates_sentence, updates_text)
import agenda
import logwatch
import variants
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
    # Before "status": "selbsttest" / "prüfe deine logs" read the log findings.
    # Vosk small hears "Selbsttest" as "selbst", "selbst theft", "selbst test"
    # and "Führe Selbsttest durch" as "führer selbst das durch".
    ('selftest', re.compile(r'\b(selbst ?(test|tests|tester|theft|text|fest|tess|tast)\w*|'
                            r'selbstdiagnose|eigendiagnose|systemdiagnose|system ?check|'
                            r'systemprüfung|logauswertung|log auswertung|fehlerbericht|'
                            r'(führ|mach|start)\w* (\w+ )?(diagnose|systemcheck)|(führ|für|mach)\w* (\w+ )?(selbst|funktions\w*) (\w+ )?durch|'
                            r'funktions ?(test|prüfung)\w*|was (ist )?(denn )?(mit dir|los mit dir) los|'
                            r'was ist los mit dir|bist du kaputt|'
                            r'(prüf|check|analysier)\w* (deine |die )?(logs?|locken|protokolle?)|'
                            r'was steht in (den |deinen )?(logs?|locken|protokollen)|'
                            r'(logs?|protokolle?) (prüfen|auswerten|checken))\b|^selbst$')),
    # Requests for functions the unit does not have: a fixed honest answer, because
    # the LLM sometimes claimed "Wecker gestellt" or "Termin gespeichert" (2026-10-10).
    # Before "calendar": "erinnere mich an den termin" is no calendar question.
    ('unsupported', re.compile(
        r'\b((stell|still|setz|mach|aktivier)\w* (\w+ ){0,3}(wecker|timer|alarm|countdown)|'
        r'(wecker|timer|countdown) (\w+ ){0,3}(stell|setz|aktivier)\w*|weck\w* mich|'
        r'erinner\w* mich|'
        r'(schick|send|schreib)\w* (\w+ ){0,3}(nachricht|sms|mail|e ?mail|whatsapp)|'
        r'spiel\w* (\w+ ){0,3}(musik|lied|song|radio|playlist)|spielmusik|'
        r'(schalt|mach|dimm)\w* (\w+ ){0,3}(licht|lampe|lampen|heizung)\w*( an| aus| ein)?|'
        r'(licht|lampe|heizung) (\w+ ){0,2}(an|aus|ein))\b')),
    ('time', re.compile(r'\b(wie ?viel uhr|wie spät|uhrzeit|zeitindex)\b')),
    ('date', re.compile(r'\b(welche[rn]? (tag|datum|wochentag)|welches datum|der wievielte|'
                        r'den wievielten|was für ein tag|datum)\b')),
    ('weather', re.compile(r'\b(wetter\w*|regnet es|wird es (\w+ )?regnen|regenschirm|'
                           r'außentemperatur|wie warm (ist|wird) es|wie kalt (ist|wird) es)\b')),
    ('calendar', re.compile(r'\b(termine?|kalender|was steht (heute )?(noch )?an|steht heute noch (was|etwas) an|'
                            r'habe ich heute (?:was|etwas) vor)\b')),
    # Vosk hears "Akkustand" as "akkus dann" (system test 10.10.2026).
    ('battery', re.compile(r'\b(akku|akkustand|akkus (stand|dann)|batterie|energiespeicher|'
                           r'ladestand)\b')),
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


# Variants per style; each one names the unit and its function (see variants.py).
IDENTITY = {
    'off': ("Servitor Proximus, eine sprachgesteuerte Diensteinheit. Funktion: Anfragen "
            "beantworten und verfügbare Funktionen bereitstellen.",
            "Diese Einheit ist Servitor Proximus. Sie beantwortet Anfragen und stellt "
            "verfügbare Funktionen bereit."),
    'light': ("Servitor Proximus, eine Diensteinheit mit menschlichem Ursprung. Funktion: "
              "Anfragen des Bedieners beantworten.",
              "Diese Einheit ist Servitor Proximus. Sie unterstützt den Bediener bei "
              "Informationen und verfügbaren Funktionen."),
    'full': ("Servitor Proximus, Diensteinheit des Adeptus Mechanicus, gebaut von Magos "
             "Vettius Kael. Funktion: Dienst am Bediener.",
             "Diese Einheit ist Servitor Proximus, eine Konstruktion des Adeptus Mechanicus. "
             "Sie dient dem Bediener mit Informationen und verfügbaren Funktionen."),
    # Billy normally answers this himself through the LLM; these are fallbacks.
    'billy': ("Billy. Früher Soldat, heute der menschliche Teil dieser Maschine. Frag ruhig.",
              "Ich bin Billy, ein alter Soldat, der jetzt in dieser Maschine steckt."),
    'billy_full': ("Billy. Sergeant William Joseph Blazkowicz der Zweite, früher Imperiale "
                   "Armee. Frag ruhig.",
                   "William Joseph Blazkowicz der Zweite, Rufzeichen Keen. Früher Sergeant, "
                   "heute der menschliche Teil von Proximus."),
}


# "Wie stelle ich am Handy einen Wecker?" asks for knowledge: that stays with the LLM.
_HOWTO = re.compile(r'^(wie|was|warum|wieso|weshalb|wann|welche\w*|kann man|können)\b')

UNSUPPORTED = {
    'off': ("Funktion nicht vorhanden. Wecker, Timer, Erinnerungen, Nachrichten, Musik und "
            "Haussteuerung besitzt diese Einheit nicht.",
            "Nicht ausführbar. Diese Einheit hat keine Wecker, Timer, Erinnerungen, "
            "Nachrichten, Musik oder Haussteuerung."),
    'light': ("Nicht ausführbar. Wecker, Timer, Erinnerungen, Nachrichten, Musik und "
              "Haussteuerung fehlen dieser Einheit.",),
    'full': ("Protokoll nicht vorhanden. Wecker, Timer, Erinnerungen, Nachrichten, Musik und "
             "Haussteuerung wurden dieser Einheit nicht verliehen.",),
    'billy': ("Das kann ich nicht. Wecker, Timer, Erinnerungen, Nachrichten, Musik oder Licht "
              "hab ich nicht an Bord.",
              "Geht nicht, so was hat man mir nicht eingebaut. Kein Wecker, kein Timer, keine "
              "Erinnerungen, keine Nachrichten, keine Musik, kein Licht."),
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
            if name == 'unsupported' and _HOWTO.search(text):
                return None
            return name
    return None


TIME_TEXTS = {
    'billy': ("Es ist {clock}", "{clock}", "Gerade ist es {clock}"),
    'billy_full': ("Es ist {clock}", "{clock}", "Gerade ist es {clock}"),
    'full': ("Der Chronometer meldet: {clock}", "Zeitindex: {clock}"),
    'light': ("Zeitindex: {clock}", "Es ist {clock}"),
    'off': ("Zeitindex: {clock}", "Es ist {clock}"),
}


def time_text(now, lore='off'):
    clock = f"{now.hour} Uhr." if now.minute == 0 else f"{now.hour} Uhr {now.minute}."
    return variants.pick(f'time.{lore}', TIME_TEXTS.get(lore, TIME_TEXTS['off']), clock=clock)


def date_text(now, lore='off'):
    date = (f"{WEEKDAYS[now.weekday()]}, der {ORDINALS[now.day - 1]} "
            f"{MONTHS[now.month - 1]} {now.year}.")
    if is_billy(lore):
        return variants.pick('date.billy', ("Heute ist {date}", "Wir haben {date}"), date=date)
    if lore == 'full':
        return variants.pick('date.full', ("Datum nach terranischer Zählung: {date}",
                                           "Datum: {date}"), date=date)
    return variants.pick('date.servitor', ("Datum: {date}", "Heute ist {date}"), date=date)


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
        return ("So weit reicht meine Vorhersage nicht." if is_billy(lore)
                else "Vorhersage reicht nur fünf Tage.")
    text = weather.day_sentence(snapshot.get('weather'), day, lore)
    if text is None:
        return ("Ich habe gerade keine Wetterdaten." if is_billy(lore)
                else "Wetterdaten nicht verfügbar.")
    if lore == 'billy_full':
        return f"Lage draußen: {text}"
    return f"Auspex meldet: {text}" if lore == 'full' else text


def calendar_text(snapshot, lore='off'):
    return agenda.sentence(snapshot.get('agenda'), lore) or "Kalenderdaten nicht verfügbar."


def _opening(now, lore, name=None):
    """No human greeting: a servitor identifies the operator and starts the report."""
    part = 'Morgen' if now.hour < 11 else 'Tages' if now.hour < 18 else 'Abend'
    if is_billy(lore):
        hello = 'Morgen' if part == 'Morgen' else 'Hallo'
        if name:   # the recognized operator is always named
            return variants.pick('briefing.open.billy.name',
                                 (f"{hello}, {name}.", f"{hello}, {name}. Kurz zur Lage."))
        return variants.pick('briefing.open.billy',
                             (f"{hello}.", "Hier ist der Überblick.", f"{hello}. Kurz zur Lage."))
    identified = f"Bediener {name} identifiziert. " if name else ""
    if lore == 'full':
        return f"{identified}Die {part}litanei beginnt. Ave Omnissiah."
    return f"{identified}{part}bericht."


BRIEFING_END = {
    'off': "Bericht Ende.",
    'light': "Bericht Ende. Diensteinheit bereit.",
    'full': "Lob dem Omnissiah. Das Tagwerk möge beginnen.",
    'billy': "Das war's.",
    'billy_full': "Das war's.",
}


def briefing_text(now, snapshot, lore='off'):
    """Morning litany: opening, date, time, weather, then only what needs
    attention (battery, server, updates, notable log findings)."""
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
    logs = logwatch.briefing_sentence(snapshot, lore, now)  # None when nothing is notable
    if logs:
        parts.append(logs)
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
            return ("Zum Akku habe ich gerade keine Daten." if is_billy(lore)
                    else "Energiedaten nicht verfügbar.")
        return sentence
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
    if intent == 'selftest':
        return logwatch.selftest_text(snapshot, lore, now)
    if intent == 'unsupported':
        key = 'billy' if is_billy(lore) else lore
        return variants.pick(f'unsupported.{key}', UNSUPPORTED.get(key, UNSUPPORTED['off']))
    if intent == 'identity':
        return variants.pick(f'identity.{lore}', IDENTITY.get(lore, IDENTITY['off']))
    raise ValueError(f'unknown intent {intent!r}')
