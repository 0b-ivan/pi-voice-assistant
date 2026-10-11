"""Per-turn hints from the conversation itself, without an extra model call.

Follow-up questions ("nochmal", "einfacher", "warum?") are resolved against
the last answer, repeated openings and forms of address in the recent
answers are pointed out, and answers from the other persona only contribute
facts, never their style. Everything works on the history the memory stick
already provides; without it the hints that need history are left out.
"""
import re

_REPEAT = re.compile(
    r"^(bitte )?(nochmal|noch mal|noch einmal|wiederhol\w*|sag (das|es) (bitte )?nochmal|"
    r"was hast du (gerade |eben )?gesagt|wie bitte|genau so( nochmal)?|"
    r"das (bitte )?nochmal|(ich habe|ich hab) (dich |das )?nicht verstanden)\b")
_SIMPLER = re.compile(
    r"\b(einfacher|verständlicher|anders erklär\w*|erklär (das|es) (bitte )?anders|"
    r"in einfachen worten|versteh (ich|das) nicht|kapier (ich|das) nicht|zu kompliziert)\b")
_CONTINUE = re.compile(r"^(erzähl (bitte )?weiter|weiter erzählen|und dann|wie ging es weiter|"
                       r"was passierte dann|mach weiter|weiter)$")
_WHY = re.compile(r"^(und )?(warum|wieso|weshalb|wie das)\b")
_ALREADY = re.compile(r"\b(hab|habe) ich (doch )?(schon|bereits) gefragt\b|\bfrage habe ich doch\b")
_ADDRESS = {'mensch': re.compile(r'\b(Boss|Rekrut)\b'), 'servitor': re.compile(r'\bBediener\b')}
STYLE_NAMES = {'mensch': 'Billy', 'servitor': 'Servitor'}


def normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


def followup(text):
    """'repeat', 'simpler', 'continue', 'why', 'already' or None."""
    words = normalize(text)
    if not words:
        return None
    if _REPEAT.search(words) and len(words.split()) <= 7:
        return 'repeat'
    if _ALREADY.search(words):
        return 'already'
    if _SIMPLER.search(words):
        return 'simpler'
    if _CONTINUE.match(words):
        return 'continue'
    if _WHY.match(words) and len(words.split()) <= 4:
        return 'why'
    return None


def _opening(answer):
    words = re.findall(r"[\wäöüß']+", answer)
    return ' '.join(words[:2])


def hints(text, history, persona):
    """Lines for the dynamic part of the system prompt (may be empty).

    ``history``: [{'q', 'a', optional 'p' = persona of the answer}] or None
    (no memory stick: no history, nothing about earlier answers)."""
    out = []
    kind = followup(text)
    answers = [h for h in history or [] if isinstance(h, dict) and h.get('a')]
    if kind == 'repeat':
        if answers:
            out.append("Der Nutzer bittet um Wiederholung: Wiederhole deine letzte Antwort "
                       "inhaltlich gleich und möglichst wörtlich, ohne Vorwurf und ohne Zusatz.")
        else:
            out.append("Der Nutzer bittet um Wiederholung, aber es liegt kein Gesprächsverlauf "
                       "vor. Sag kurz, dass die letzte Antwort nicht mehr vorliegt, und frag "
                       "nach dem Thema.")
        return out
    if kind == 'simpler':
        out.append("Der Nutzer möchte die letzte Erklärung einfacher: Erkläre sie anders, mit "
                   "einem anderen Bild oder Beispiel, nicht nur mit Synonymen.")
    elif kind == 'why':
        out.append("Die kurze Rückfrage bezieht sich auf deine letzte Antwort; beantworte sie "
                   "in diesem Zusammenhang.")
    elif kind == 'already':
        out.append("Der Nutzer hat das schon gefragt; die vorige Antwort hat offenbar nicht "
                   "gereicht. Ruhig und klarer antworten, den entscheidenden Punkt zuerst, "
                   "ohne Vorwurf.")
    elif kind == 'continue':
        out.append("Der Nutzer möchte, dass du dort weitermachst, wo deine letzte Antwort "
                   "aufgehört hat; nichts davon wiederholen.")
    recent = answers[-3:]
    if len(recent) >= 2:
        openings = [_opening(h['a']) for h in recent]
        first = [o.split()[0].lower() for o in openings if o]
        if len(set(first)) < len(first):
            quoted = ', '.join(f'„{o}“' for o in openings if o)
            out.append(f"Deine letzten Antworten begannen mit {quoted}. Beginne diesmal "
                       "anders, am besten direkt mit der Sache.")
        pattern = _ADDRESS.get(persona)
        if pattern and sum(bool(pattern.search(h['a'])) for h in recent) >= 2:
            out.append("Die Anrede kam zuletzt mehrfach vor: Lass sie diesmal ganz weg.")
    others = {h.get('p') for h in answers[-4:]} - {persona}
    if others:
        out.append(f"Ein Teil des Verlaufs stammt aus einem anderen Sprechstil. Übernimm daraus "
                   f"nur Fakten über den Nutzer und das Gespräch, nicht den Stil; sprich jetzt "
                   f"ausschließlich als {STYLE_NAMES.get(persona, persona)}.")
    return out
