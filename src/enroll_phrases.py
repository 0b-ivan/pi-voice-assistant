"""Sentences for the voice part of enrollment: plan, challenge words, WER.

Concept stimm-einlernen v2, section 4. A local generator (no LLM, no cloud)
builds every session's sentences anew from checked patterns and word lists.
Each word carries curated sound classes (LEXICON); a plan is accepted only
when its sentences cover the whole INVENTORY, spelling is never taken as
proof. Production randomness comes from ``secrets``; tests pass a seeded
``random.Random``.

Plan: 24 sentences, eight per condition (near, far, quiet), two short (4-7
words), four middle (8-12), two long (13-20). Six of them are held-out
checks, two per condition, built from *other* patterns than the anchors; the
last two (quiet) are challenges with at least three freshly drawn words in
the middle and at the end. Every text is a recording task only: it is never
stored as a fact and never dispatched as a command.

Content check: WER = (substitutions + deletions + insertions) / words of the
target, after Unicode, case, punctuation and number normalization. Normal
sentences pass with WER <= 0.20; long anchors with WER <= 0.30; challenges
need WER <= 0.15 and every drawn
word in order, answers of up to five words an exact match. WER checks the
content, it does not detect synthetic speech.
"""
import re
import secrets
import unicodedata

CONDITIONS = ('near', 'far', 'quiet')
CONDITION_HINTS = {
    'near': "Beginne an deinem gewöhnlichen Platz. Verstelle deine Stimme nicht.",
    'far': "Gehe auf deinen üblichen Abstand. Sprich gewöhnlich. Rufe nicht.",
    'quiet': "Jetzt leiser, mit hörbarer Stimme. Flüstern ist nicht erforderlich.",
}
CONDITION_LABELS = {'near': 'nah', 'far': 'fern', 'quiet': 'leise'}
# Recording length per sentence class (arecord takes whole seconds).
RECORD_SECONDS = {'short': 5, 'mid': 7, 'long': 10, 'challenge': 7}
WER_MAX, LONG_ANCHOR_WER_MAX, CHALLENGE_WER_MAX, EXACT_WORDS = 0.20, 0.30, 0.15, 5

# Sound classes (Standard German inventory, fixed in advance).
INVENTORY = {
    'long_vowel': 'lange Vokale', 'short_vowel': 'kurze Vokale', 'ü': 'ü', 'ö': 'ö',
    'ä': 'ä', 'diphthong': 'Diphthonge', 'schwa': 'Schwa', 'ich': 'ich-Laut',
    'ach': 'ach-Laut', 's': 's', 'z': 'stimmhaftes s', 'sch': 'sch', 'affricate': 'Affrikaten',
    'plosive': 'Plosive', 'nasal': 'Nasale', 'ng': 'ng', 'l': 'l', 'r': 'r',
    'zh': 'Lehnlaut zh (Garage)', 'dzh': 'Lehnlaut dsch (Jeans, Dschungel)',
}
_CODES = dict(L='long_vowel', K='short_vowel', U='ü', O='ö', A='ä', D='diphthong',
              E='schwa', C='ich', X='ach', S='s', Z='z', H='sch', T='affricate',
              P='plosive', N='nasal', G='ng', l='l', r='r', J='zh', Q='dzh',
              f='fricative', v='fricative', h='h')


def _lexicon(text):
    entries = {}
    for line in text.strip().splitlines():
        word, codes = line.split()
        entries[word] = frozenset(_CODES[c] for c in codes)
    return entries


# Curated sound classes per word (lower case, as normalize() writes them).
LEXICON = _lexicon('''
der LPr
die LP
das KPS
den LPN
dem LPN
ein DN
ich KC
und KNP
im KN
am KN
in KN
auf DPf
vor Lfr
unter KNPr
neben LPNE
hinter KCNPr
zwischen TKHNE
alte KlPE
alten KlPEN
müde ULPE
müden ULPEN
grüne UPrLNE
grünen UPrLNE
stille HPKlE
stillen HPKlEN
helle KlE
hellen KlEN
kleine PlDNE
kleinen PlDNE
rote rLPE
roten rLPEN
graue PrDE
grauen PrDEN
schwere HvLrE
schweren HvLrEN
junge KGE
jungen KGEN
fröhliche OfrLlCE
fröhlichen OfrLlCEN
flinke flKGPE
flinken flKGPEN
fuchs PfKXS
vogel fLPlE
sänger ZAGEr
nachbar NKXPr
katze PKTE
wache vKXE
ente KNPE
kind PKNP
mädchen NAPCEN
pferd TKrP
schlüssel HlUSEl
brief PrLf
teller PKlEr
becher PKCEr
lampe lKNPE
tasse PKSE
uhr Lr
kiste PKSPE
birne PKrNE
fenster fKNSPEr
glas PlLS
buch PLX
kabel PLPEl
brötchen PrOPCEN
jeans QLNS
schläft HlAfP
wartet vKrPEP
singt ZKGP
lacht lKXP
träumt PrDNP
sitzt ZKTP
übt ULPP
ruht rLP
zählt TAlP
sucht ZLXP
suche ZLXE
prüft PrUfP
prüfe PrUfE
holt hLlP
hole hLlE
findet fKNPEP
finde fKNPE
trägt PrAPP
trage PrLPE
versteckt fArHPKP
verstecke fArHPKE
zeichnet TDCNEP
zeichne TDCNE
heute hDPE
wieder vLPEr
leise lDZE
draußen PrDSEN
ruhig rLCK
kurz PKrT
gemütlich PEUPlKC
morgens NKrPENS
schrank HrKGP
garten PKrPEN
tisch PKH
küche PUCE
tür PUr
zaun TDN
bach PKX
brücke PrUPE
flur flLr
zweigen TvDPEN
garage PKrLJE
dschungel QKGEl
regen rLPEN
sonne ZKNE
wind vKNP
warum vKrKN
wann vKN
wo vL
wie vL
lange lKGE
wenn vKN
morgen NKrPEN
früh frU
scheint HDNP
sobald ZLPKlP
kühle PUlE
endlich KNPlKC
aufhört DfhOrP
bevor PEfLr
licht lKCP
ausgeht DSPLP
weht vLP
leiser lDZEr
zweige TvDPE
während vArNP
danach PKNLX
bitte PKPE
melde NKlPE
durch PKrX
langen lKGEN
kühlen PUlEN
''')

# Slots. Noun phrases are built with the right article and adjective ending.
_ADJECTIVES = ('alt', 'müd', 'grün', 'still', 'hell', 'klein', 'rot', 'grau', 'schwer',
               'jung', 'fröhlich', 'flink')
_SUBJECTS = (('m', 'Fuchs'), ('m', 'Vogel'), ('m', 'Sänger'), ('m', 'Nachbar'),
             ('f', 'Katze'), ('f', 'Wache'), ('f', 'Ente'), ('n', 'Kind'), ('n', 'Mädchen'),
             ('n', 'Pferd'))
_OBJECTS = (('m', 'Schlüssel'), ('m', 'Brief'), ('m', 'Teller'), ('m', 'Becher'),
            ('f', 'Lampe'), ('f', 'Tasse'), ('f', 'Uhr'), ('f', 'Kiste'), ('f', 'Birne'),
            ('n', 'Fenster'), ('n', 'Glas'), ('n', 'Buch'), ('n', 'Kabel'), ('n', 'Brötchen'),
            ('p', 'Jeans'))
_INTRANSITIVE = ('schläft', 'wartet', 'singt', 'lacht', 'träumt', 'sitzt', 'übt', 'ruht',
                 'zählt')
_TRANSITIVE = (('sucht', 'suche'), ('prüft', 'prüfe'), ('holt', 'hole'), ('findet', 'finde'),
               ('trägt', 'trage'), ('versteckt', 'verstecke'), ('zeichnet', 'zeichne'))
_ADVERBS = ('heute', 'wieder', 'leise', 'draußen', 'ruhig', 'kurz', 'gemütlich', 'morgens')
_PLACES = ('im stillen Schrank', 'im grünen Garten', 'unter dem Tisch', 'neben der Küche',
           'vor der Tür', 'hinter dem Zaun', 'am kühlen Bach', 'auf der Brücke',
           'im langen Flur', 'zwischen zwei Zweigen', 'in der Garage', 'im Dschungel',
           'neben dem Fenster')
_QUESTIONS = ('Warum', 'Wann', 'Wo', 'Wie lange')
_WHEN = ('Wenn morgen früh die Sonne scheint', 'Sobald der kühle Regen endlich aufhört',
         'Bevor das helle Licht ausgeht', 'Wenn der Wind leise durch Zweige weht')
_ARTICLES = dict(nom=dict(m='der', f='die', n='das', p='die'),
                 acc=dict(m='den', f='die', n='das', p='die'))

# Challenge words: frequent, easy for Vosk, independent draws.
CHALLENGE_NOUNS = ('Birke', 'Fenster', 'Lampe', 'Brücke', 'Kessel', 'Mantel', 'Garten',
                   'Hafen', 'Tunnel', 'Faden')
CHALLENGE_MATERIALS = ('Kupfer', 'Silber', 'Eisen', 'Messing', 'blau', 'grün', 'rot', 'golden')
CHALLENGE_NUMBERS = ('zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun', 'zehn',
                     'elf', 'zwölf')
CHALLENGE_TARGETS = ('Fenster', 'Wagen', 'Leiter', 'Anker', 'Spiegel', 'Kerze', 'Feder',
                     'Schalter')
COLORS = ('blau', 'grün', 'rot', 'gelb', 'weiß')

# Optional baseline part: own lines, Proximus speaks first, the human answers.
# None in the answer: drawn fresh per attempt (pairs 2, 8, 10, 11).
BASELINE = (
    ("Kern bereit. Leitung offen.", "Kern bereit. Leitung offen."),
    (None, None),
    ("Was bleibt im Speicher?", "Nur das helle Signal."),
    ("Wer hält die Wache?", "Der Kern hält die Wache."),
    ("Druck fällt. Licht bleibt.", "Druck fällt. Licht bleibt."),
    ("Kein Echo. Eine Stimme.", "Kein Echo. Eine Stimme."),
    ("Wo endet der Draht?", "Am stillen Rand des Raums."),
    (None, None),
    ("Was öffnet die Klappe?", "Ein grünes Zeichen öffnet sie."),
    (None, None),
    (None, None),
    ("Ist die Prüfung beendet?", "Die Einheit wartet auf Bestätigung."),
)


def system_random():
    return secrets.SystemRandom()


def _np(rng, items, case):
    gender, noun = rng.choice(items)
    if gender == 'p':
        return f"{_ARTICLES[case]['p']} {rng.choice(_ADJECTIVES)}en {noun}"
    ending = 'en' if case == 'acc' and gender == 'm' else 'e'
    return f"{_ARTICLES[case][gender]} {rng.choice(_ADJECTIVES)}{ending} {noun}"


def _cap(text):
    return text[:1].upper() + text[1:]


def _sentence(pattern, rng):
    subject = _np(rng, _SUBJECTS, 'nom')
    obj = _np(rng, _OBJECTS, 'acc')
    other = _np(rng, _SUBJECTS, 'nom')
    verb_i, verb_t = rng.choice(_INTRANSITIVE), rng.choice(_TRANSITIVE)
    adverb, place = rng.choice(_ADVERBS), rng.choice(_PLACES)
    if pattern == 'S1':
        return f"{_cap(subject)} {verb_i} {adverb}."
    if pattern == 'S2':
        return f"{_cap(subject)} {verb_i} {place}."
    if pattern == 'M1':
        return f"{_cap(subject)} {verb_t[0]} {obj} {place}."
    if pattern == 'M2':
        return f"{rng.choice(_QUESTIONS)} {verb_i} {subject} {adverb} {place}?"
    if pattern == 'L1':
        return f"{rng.choice(_WHEN)}, {verb_t[1]} ich {obj} und {other} {verb_i} {place}."
    if pattern == 'L2':
        return f"{_cap(subject)} {verb_t[0]} {obj} {place}, während {other} {adverb} {verb_i}."
    if pattern == 'C1':     # check: object first
        return f"{_cap(obj)} {verb_t[0]} {subject} {place}."
    if pattern == 'C2':     # check: place first, then a second clause
        return f"{_cap(place)} {verb_i} {subject}, und danach {verb_t[1]} ich {obj} {adverb}."
    raise ValueError(pattern)


def challenge(rng):
    """A challenge sentence with four independently drawn words, placed in
    the middle and at the end."""
    words = [rng.choice(CHALLENGE_NOUNS), rng.choice(CHALLENGE_MATERIALS),
             rng.choice(CHALLENGE_NUMBERS), rng.choice(CHALLENGE_TARGETS)]
    text = f"Prüfe bitte {words[0]} {words[1]} {words[2]} und melde danach {words[3]}."
    return text, words


# (role, length, pattern) per condition; the quiet checks are the challenges.
_LAYOUT = {
    'near': (('anchor', 'short', 'S1'), ('anchor', 'mid', 'M1'), ('anchor', 'short', 'S2'),
             ('check', 'mid', 'C1'), ('anchor', 'mid', 'M2'), ('anchor', 'mid', 'M1'),
             ('check', 'long', 'C2'), ('anchor', 'long', 'L1')),
    'far': (('anchor', 'short', 'S2'), ('anchor', 'mid', 'M2'), ('anchor', 'short', 'S1'),
            ('check', 'mid', 'C1'), ('anchor', 'mid', 'M1'), ('anchor', 'mid', 'M2'),
            ('check', 'long', 'C2'), ('anchor', 'long', 'L2')),
    'quiet': (('anchor', 'short', 'S1'), ('anchor', 'mid', 'M1'), ('anchor', 'short', 'S2'),
              ('anchor', 'long', 'L1'), ('anchor', 'mid', 'M2'), ('anchor', 'long', 'L2'),
              ('check', 'challenge', 'CH'), ('check', 'challenge', 'CH')),
}


def task(condition, role, length, pattern, rng, avoid=()):
    """One recording task {condition, role, length, pattern, text, words}."""
    for _ in range(50):
        if pattern == 'CH':
            text, words = challenge(rng)
        else:
            text, words = _sentence(pattern, rng), []
        if text not in avoid:
            return dict(condition=condition, role=role, length=length, pattern=pattern,
                        text=text, words=words)
    raise RuntimeError('no new sentence found')


def coverage(texts):
    """Sound classes covered by the texts (curated lexicon only)."""
    found = set()
    for text in texts:
        for word in normalize(text).split():
            found |= LEXICON.get(word, frozenset())
    return found


def unknown_words(text):
    """Words of a generated text that the lexicon does not describe."""
    return [w for w in normalize(text).split() if w not in LEXICON
            and w not in _CHALLENGE_WORDS]


def plan(rng=None, tries=200):
    """24 tasks covering the whole sound inventory, no text twice."""
    rng = rng or system_random()
    for _ in range(tries):
        tasks, seen = [], set()
        for condition in CONDITIONS:
            for role, length, pattern in _LAYOUT[condition]:
                item = task(condition, role, length, pattern, rng, seen)
                seen.add(item['text'])
                tasks.append(item)
        if coverage(t['text'] for t in tasks) >= set(INVENTORY):
            for index, item in enumerate(tasks):
                item['index'] = index
            return tasks
    raise RuntimeError('no plan covers the sound inventory')


def replacement(item, rng=None, avoid=()):
    """A new sentence for a failed take: same condition, role, length and
    pattern, a different text."""
    rng = rng or system_random()
    fresh = task(item['condition'], item['role'], item['length'], item['pattern'], rng,
                 set(avoid) | {item['text']})
    return dict(fresh, index=item.get('index'))


def baseline(rng=None):
    """The twelve baseline pairs, variable ones drawn fresh: [(prompt, answer, words)]."""
    rng = rng or system_random()
    pairs = []
    for number, (prompt, answer) in enumerate(BASELINE, 1):
        words = []
        if number == 2:
            a, b = rng.sample(CHALLENGE_NUMBERS, 2)
            prompt = answer = f"{_cap(a)} Impulse. {_cap(b)} Zeichen."
            words = [a, b]
        elif number == 8:
            words = [rng.choice(CHALLENGE_NOUNS), rng.choice(CHALLENGE_MATERIALS),
                     rng.choice(CHALLENGE_NUMBERS)]
            answer = '. '.join(_cap(w) for w in words) + '.'
            prompt = f"Zähle: {answer}"
        elif number == 10:
            words = [rng.choice(CHALLENGE_NUMBERS), rng.choice(COLORS)]
            prompt = answer = f"Leitung {words[0]}. Lampe {words[1]}."
        elif number == 11:
            words = [rng.choice(CHALLENGE_TARGETS), rng.choice(CHALLENGE_MATERIALS),
                     rng.choice(CHALLENGE_NUMBERS)]
            answer = '. '.join(_cap(w) for w in words) + '.'
            prompt = f"Melde: {answer}"
        pairs.append((prompt, answer, words))
    return pairs


_CHALLENGE_WORDS = {w.lower() for w in CHALLENGE_NOUNS + CHALLENGE_MATERIALS
                    + CHALLENGE_NUMBERS + CHALLENGE_TARGETS + COLORS}

# --- normalization and WER -------------------------------------------------

_NUMBERS = {'0': 'null', '1': 'eins', '2': 'zwei', '3': 'drei', '4': 'vier', '5': 'fünf',
            '6': 'sechs', '7': 'sieben', '8': 'acht', '9': 'neun', '10': 'zehn', '11': 'elf',
            '12': 'zwölf'}


def normalize(text):
    """NFC, lower case, no punctuation, digits as words (fixed mapping)."""
    text = unicodedata.normalize('NFC', str(text)).lower()
    words = re.findall(r'[a-zäöüß0-9]+', text)
    return ' '.join(_NUMBERS.get(w, w) for w in words)


def wer(target, heard):
    """Word error rate of ``heard`` against ``target`` (Levenshtein on words)."""
    ref, hyp = normalize(target).split(), normalize(heard).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / len(ref)


def in_order(words, heard):
    """Every drawn word appears in ``heard``, in this order."""
    heard_words = normalize(heard).split()
    position = 0
    for word in (normalize(w) for w in words):
        try:
            position = heard_words.index(word, position) + 1
        except ValueError:
            return False
    return True


def check(item, heard):
    """{wer, ok, words_ok} of a transcript against a task (or an answer dict
    with 'text' and 'words'). Challenges: stricter WER and every drawn word."""
    heard = heard or ''
    value = wer(item['text'], heard)
    words = item.get('words') or []
    strict = bool(words)
    if strict and len(normalize(item['text']).split()) <= EXACT_WORDS:
        ok = normalize(item['text']) == normalize(heard)
    else:
        # Long anchor sentences are for voice characteristics, not for
        # validating a secret. Small German ASR mistakes must not discard
        # good, long speech recordings. All held-out checks retain 0.20;
        # freshly drawn challenge words still require 0.15 + exact order.
        limit = (CHALLENGE_WER_MAX if strict else
                 LONG_ANCHOR_WER_MAX if item.get('role') == 'anchor'
                 and item.get('length') == 'long' else WER_MAX)
        ok = value <= limit
    words_ok = in_order(words, heard) if words else True
    return dict(wer=round(value, 3), ok=ok and words_ok, words_ok=words_ok)
