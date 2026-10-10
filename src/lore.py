"""Proximus' and Billy's lore archive: compact engrams and a small search.

The full author text lives in docs/concepts/proximus-billy-lore/. At runtime
only lore_engrams.json is read: one entry per episode (E01-E33), family saga
(S01-S06) or basic fact (B01-B05), each with a Billy version and, where the
servitor may know it, a separate redacted servitor version. Kael's decision
(author entry A01) is deliberately not in the file, so no question can pull
it into the model's context.

The search is deterministic: keywords and names, umlaut-folded, with
compound words matched by prefix. Visibility (persona, lore level) is checked
before scoring. Nothing here calls a model, and a missing or broken archive
only means: no engrams.
"""
from collections import deque
import json
import os
from pathlib import Path
import re

ARCHIVE = Path(os.environ.get('SERVITOR_LORE_ARCHIVE',
                              Path(__file__).resolve().with_name('lore_engrams.json')))
LORE_LEVELS = ('off', 'light', 'full')
MIN_SCORE = 2.0

# Everyday default: a few engrams within a small budget; personal questions
# get what a coherent answer needs (budgets are characters of engram text).
LIMITS = {'light': (3, 2400), 'full': (3, 2800)}
PERSONAL_LIMITS = {'light': (5, 4800), 'full': (6, 6400)}

_cache = {}


def fold(text):
    """Lower case, umlauts and ß folded, punctuation gone: 'Höllenläufer' ->
    'hoellenlaeufer'. Used for both query and keywords."""
    text = str(text).lower().replace('ß', 'ss')
    for umlaut, plain in (('ä', 'ae'), ('ö', 'oe'), ('ü', 'ue')):
        text = text.replace(umlaut, plain)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def load(path=None):
    """Validated engrams (list of dicts); [] when the file is missing or broken."""
    path = Path(path or ARCHIVE)
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        items = data['engramme']
    except (OSError, ValueError, KeyError, TypeError):
        return []
    engrams = []
    for item in items if isinstance(items, list) else []:
        if not (isinstance(item, dict) and isinstance(item.get('id'), str)
                and (isinstance(item.get('billy'), str) or isinstance(item.get('servitor'), str))):
            continue
        entry = dict(item)
        entry['_keys'] = [fold(k) for k in item.get('stichworte') or [] if isinstance(k, str)]
        entry['_names'] = [fold(k) for k in item.get('namen') or [] if isinstance(k, str)]
        engrams.append(entry)
    _cache[path] = (stamp, engrams)
    return engrams


def by_id(engrams=None):
    return {e['id']: e for e in (load() if engrams is None else engrams)}


def text_for(engram, persona):
    """The version this persona may see, or None."""
    key = 'billy' if persona == 'mensch' else 'servitor'
    text = engram.get(key)
    return text if isinstance(text, str) and text.strip() else None


def visible(engram, persona, lore):
    """Lore 'off' injects nothing; otherwise the persona needs its own version."""
    return lore in ('light', 'full') and text_for(engram, persona) is not None


LABELS = {
    'mensch': {'basis': 'Grunddaten', 'episode': 'eigene Erinnerung',
               'sage': 'Familienüberlieferung, keine eigene Erinnerung'},
    'servitor': {'basis': 'Grunddaten', 'episode': 'Dienstakte',
                 'sage': 'Archivüberlieferung, keine eigene Erinnerung'},
}


def label(engram, persona):
    persona = 'mensch' if persona == 'mensch' else 'servitor'
    return LABELS[persona].get(engram.get('art'), 'Archiv')


# --- Query analysis ----------------------------------------------------------

_PERSONAL = re.compile(
    r'\b(du|dein\w*|dich|dir|euch|euer\w*|ihr|erzaehl\w*|erinner\w*|vergangenheit|frueher|'
    r'kindheit|familie|vater|mutter|eltern|ahnen|vorfahren|warst|hast du|bist du)\b')
_GAMES = re.compile(
    r'\b(doom\w*|wolfenstein|new order|new colossus|youngblood|commander keen|'
    r'b ?j blazkowicz|id software|machinegames)\b')
# Own past or family: then a game name means the family saga, not the game.
_OWN = re.compile(r'\b(dein\w*|dich|dir|euch|euer\w*|vergangenheit|kindheit|familie|vater|mutter|'
                  r'eltern|ahnen|vorfahren|sage|sagen|ueberlieferung|warst du|bist du)\b')
_GENERIC = {'krieg', 'geschichte', 'heute', 'tag', 'erinnerung', 'maschine', 'kopf'}


def personal(query):
    """Does the question ask about the speaker's own past or person?"""
    return bool(_PERSONAL.search(fold(query)))


def game_question(query):
    """A plain question about a named game ("Was ist Doom?"): answered with
    general knowledge, without family lore, at every lore level."""
    folded = fold(query)
    return bool(_GAMES.search(folded)) and not _OWN.search(folded)


def _has(key, folded, tokens):
    if not key:
        return False
    if ' ' in key:
        return re.search(rf'\b{re.escape(key)}\b', folded) is not None
    if key in tokens:
        return True
    if key.isdigit() or len(key) < 5:
        return False
    # Compounds: "kindheitserinnerung" holds "kindheit", "funkgeraete" "funkgeraet".
    return any(token.startswith(key) or (len(key) >= 6 and key in token) for token in tokens)


def score(engram, query):
    folded = fold(query)
    tokens = set(folded.split())
    total = 0.0
    for name in engram.get('_names', []):
        if _has(name, folded, tokens):
            total += 4.0
    for key in engram.get('_keys', []):
        if _has(key, folded, tokens):
            if ' ' in key:
                total += 3.0
            elif key in _GENERIC:
                total += 0.5
            else:
                total += 2.0
    return total


def search(query, persona, lore, personal_question=None, engrams=None, limit=None,
           budget=None):
    """Matching engrams for one question, best first, within a budget.

    ``lore`` 'off' and plain game questions return nothing. A personal
    question gets all fitting entries up to a larger budget; the first match
    is always included even when it alone exceeds the budget."""
    if lore not in ('light', 'full') or game_question(query):
        return []
    engrams = load() if engrams is None else engrams
    if personal_question is None:
        personal_question = personal(query)
    default_limit, default_budget = (PERSONAL_LIMITS if personal_question else LIMITS)[lore]
    limit = default_limit if limit is None else limit
    budget = default_budget if budget is None else budget
    scored = []
    for engram in engrams:
        if not visible(engram, persona, lore):
            continue
        points = score(engram, query)
        if points >= MIN_SCORE:
            scored.append((-points, engram.get('reihe', 999), engram['id'], engram))
    scored.sort(key=lambda item: item[:3])
    chosen, used = [], 0
    for _, _, _, engram in scored:
        if len(chosen) >= limit:
            break
        size = len(text_for(engram, persona))
        if chosen and used + size > budget:
            continue
        chosen.append(engram)
        used += size
    return chosen


def fragment_for(query, engrams=None):
    """A releasable human fragment for a servitor breakthrough: only when the
    question names a person or place of that engram. The fragment is the only
    thing released, never the Billy text, and only for that one answer."""
    engrams = load() if engrams is None else engrams
    folded = fold(query)
    tokens = set(folded.split())
    for engram in sorted(engrams, key=lambda e: e.get('reihe', 999)):
        fragment = engram.get('fragment')
        if isinstance(fragment, str) and any(_has(n, folded, tokens)
                                             for n in engram.get('_names', [])):
            return engram['id'], fragment
    return None


def block(engrams, persona):
    """Prompt section with the chosen engrams (empty string: none)."""
    if not engrams:
        return ''
    lines = ["Archiv-Engramme zu dieser Anfrage. Feste Projekt-Lore, nur verwenden, wenn sie "
             "zur Frage passen. Keine neuen Lebensdaten, Schicksale oder Fähigkeiten erfinden; "
             "kleine beschreibende Einzelheiten sind erlaubt. Überlieferung ist keine eigene "
             "Erinnerung. Erzähle in eigenen Worten, nicht als Aufzählung:"]
    for engram in engrams:
        lines.append(f"[{engram['id']}, {label(engram, persona)}: {engram.get('thema', '')}] "
                     f"{text_for(engram, persona)}")
    return '\n'.join(lines)


# --- Short-lived state (RAM only, lost on restart) -----------------------------

class Recent:
    """IDs of engrams used in the last answers: no unasked repetition of the
    same anecdote, and "erzähl weiter" knows where it was. Holds IDs only."""

    def __init__(self, size=6):
        self.ids = deque(maxlen=size)

    def add(self, ids):
        for ident in ids:
            if ident in self.ids:
                self.ids.remove(ident)
            self.ids.append(ident)

    def last(self):
        return self.ids[-1] if self.ids else None

    def clear(self):
        self.ids.clear()


RECENT = Recent()

COOLDOWN = 3             # answers between two servitor breakthroughs
STRONG = 70              # a residual emotion this strong may break through alone
_KAEL_OR_PAST = re.compile(r'\b(kael|magos|phobos|trupp|kamerad\w*|billy|blazkowicz|'
                           r'erinner\w*|vergangenheit|tor|schott)\b')


class Breakthroughs:
    """Rare human fragments in servitor answers: only with feelings on, a
    fitting occasion and a non-neutral residual emotion; never two answers in
    a row. Allowing one never unlocks the deleted names for later answers."""

    def __init__(self, cooldown=COOLDOWN):
        self.cooldown = cooldown
        self.turn = 0
        self.last = -10 ** 6

    def allow(self, query, mood, lore, engrams=None):
        """None, or dict(fragment=str or None) for this one answer."""
        if not mood:
            return None              # feelings off: never
        self.turn += 1
        emotion, level = mood.get('emotion', 'neutral'), mood.get('level', 0) or 0
        if emotion == 'neutral' or self.turn - self.last <= self.cooldown:
            return None
        found = fragment_for(query, engrams) if lore != 'off' else None
        occasion = found is not None or bool(_KAEL_OR_PAST.search(fold(query))) or level >= STRONG
        if not occasion:
            return None
        self.last = self.turn
        return dict(fragment=found[1] if found else None, source=found[0] if found else None)


BREAKTHROUGHS = Breakthroughs()


# --- Packets for long stories ---------------------------------------------------

PACKETS = {
    'kindheit': ('B01', 'E01', 'E02', 'E03', 'E22', 'E04'),
    'dienst': ('B02', 'E05', 'E23', 'E06', 'E24', 'E13', 'E27', 'E28'),
    'trupp': ('B03', 'E07', 'E08', 'E09', 'E10', 'E11', 'E25', 'E26', 'E14', 'E12'),
    'phobos': ('E15', 'E29', 'E14', 'E16', 'E21'),
    'umbau': ('B04', 'E17', 'E18', 'E19', 'E30', 'E31', 'E32', 'E33', 'E20'),
    'sage-wolf': ('E04', 'S01', 'S02', 'S03'),
    'sage-hoelle': ('E04', 'S04', 'S05', 'S06'),
    'sagen': ('E04', 'S01', 'S02', 'S03', 'S04', 'S05', 'S06'),
}
_FAMILY = re.compile(r'\b(familie|vater|mutter|eltern|kindheit)\b')


def packet(query, persona, lore, engrams=None):
    """Ordered engram IDs for a long story about ``query`` ([]: free story).

    The best match picks its packet; a family wish next to a saga adds the
    childhood frame (E01). Entries the persona may not see are dropped."""
    if lore not in ('light', 'full'):
        return []
    engrams = load() if engrams is None else engrams
    found = search(query, persona, lore, personal_question=True, engrams=engrams,
                   limit=3, budget=10 ** 6)
    if not found:
        return []
    known = by_id(engrams)
    # E04 (the storytelling frame) belongs to every saga; the first saga or
    # episode match decides which story it is.
    lead = next((e for e in found if e['id'] != 'E04'), found[0])
    name = lead.get('paket')
    ids = list(PACKETS.get(name, (lead['id'],)))
    if name in ('sage-wolf', 'sage-hoelle', 'sagen') and _FAMILY.search(fold(query)):
        ids = ['E01'] + ids
    for engram in found:            # explicit matches outside the packet
        if engram['id'] not in ids:
            ids.append(engram['id'])
    return [i for i in ids if i in known and visible(known[i], persona, lore)]
