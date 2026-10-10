"""Explicitly long stories: about 10 to 20 minutes of speech, told in sections.

The operator asked for this: "Erzähl mir eine lange Geschichte" or "... zehn
Minuten lang" starts a story with its own output plan instead of the short
everyday answer. The story is generated section by section; every section is
one model call that only gets the stable character core, the engrams this
section needs and a compact story state (target, what was told, the last
words), never the whole monologue. Audio is cut at sentence boundaries into
small parts, so speech starts early and a stop takes effect at once.

The state lives in RAM only (the Pi's StoryRun, and inside the requests to
the server): no story survives a restart or is resumed automatically, and
nothing of it is written to the memory stick.
"""
from collections import deque
import math
import os
from pathlib import Path
import re
import time

# Planning assumption until real audio durations are known. Measured on the
# Pi with thorsten-high on 10.10.2026: 276 words in 90 s, about 185 a minute.
WORDS_PER_MINUTE = 180
FIRST_WORDS = 140          # short first section: the story starts speaking soon
SECTION_WORDS = 330        # following sections (about 2.5 minutes each)
TAIL_CHARS = 600           # end of the last section, for a seamless continuation
MAX_TOLD = 12              # progress notes kept in the state
LOOKAHEAD_MS = 75_000      # Pi: fetch the next section when less audio is queued
MAX_PARTS = 24             # Pi: never queue more parts than this

_NUMBERS = {
    'eine': 1, 'einer': 1, 'ein': 1, 'zwei': 2, 'drei': 3, 'vier': 4, 'fünf': 5, 'sechs': 6,
    'sieben': 7, 'acht': 8, 'neun': 9, 'zehn': 10, 'elf': 11, 'zwölf': 12, 'dreizehn': 13,
    'vierzehn': 14, 'fünfzehn': 15, 'sechzehn': 16, 'siebzehn': 17, 'achtzehn': 18,
    'neunzehn': 19, 'zwanzig': 20, 'fünfundzwanzig': 25, 'dreißig': 30, 'vierzig': 40,
    'fünfundvierzig': 45, 'sechzig': 60,
}
_STORY = re.compile(r'\b(erzähl\w*|geschichte\w*|sage|sagen|märchen|vortrag|lies mir)\b')
_LONG = re.compile(r'\b(lange|langen|langes|lang|längere\w*|ausführlich\w*|ausgiebig\w*|'
                   r'richtig viel)\b')
_MINUTES = re.compile(r'\b(\d{1,3}|' + '|'.join(sorted(_NUMBERS, key=len, reverse=True)) +
                      r')\s*minuten\b')
_HALF_HOUR = re.compile(r'\b(halbe stunde|halben stunde)\b')
_HOUR = re.compile(r'\b(eine stunde|einer stunde|stundenlang)\b')
_EXPLAIN = re.compile(r'\b(erklär\w*|beschreib\w*)\b')


def _float_env(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def default_minutes():
    return _float_env('STORY_DEFAULT_MINUTES', 10.0, 1.0, 60.0)


def max_minutes():
    return _float_env('STORY_MAX_MINUTES', 30.0, 5.0, 60.0)


def words_per_minute():
    return _float_env('STORY_WORDS_PER_MINUTE', WORDS_PER_MINUTE, 60.0, 220.0)


def normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


def request(text):
    """dict(minutes, topic) for an explicitly long story, else None.

    "Erzähl eine Geschichte" alone stays a normal, shorter answer; "erklär
    das ausführlich" is a factual request, not a story."""
    words = normalize(text)
    # "... zehn Minuten lang über deinen Trupp" is a story wish even when the
    # recognizer lost "erzähl" (heard as "etc" on 10.10.2026).
    talk = bool(_MINUTES.search(words) and re.search(r'\b(lang|über|von)\b', words))
    if not _STORY.search(words) and not talk:
        return None
    if _EXPLAIN.search(words) and not re.search(r'\berzähl', words):
        return None
    minutes = None
    found = _MINUTES.search(words)
    if found:
        value = found.group(1)
        minutes = float(value) if value.isdigit() else float(_NUMBERS[value])
    elif _HALF_HOUR.search(words):
        minutes = 30.0
    elif _HOUR.search(words):
        minutes = 60.0
    if minutes is None and not _LONG.search(words):
        return None
    if minutes is None:
        minutes = default_minutes()
    minutes = min(max_minutes(), max(1.0, minutes))
    return dict(minutes=minutes, topic=str(text).strip()[:300])


# --- State ------------------------------------------------------------------------

def start(req, persona, lore, ids):
    """Fresh state for a story request (``ids``: engram packet, may be empty)."""
    wpm = words_per_minute()
    target = round(req['minutes'] * wpm)
    planned = 1 + max(0, math.ceil((target - FIRST_WORDS) / SECTION_WORDS))
    return dict(v=1, persona=persona, lore=lore, topic=req['topic'], minutes=req['minutes'],
                wpm=wpm, target_ms=int(req['minutes'] * 60000), words=0, ms=0, seg=0,
                planned=planned, max_seg=planned + max(2, planned // 2), ids=list(ids)[:12],
                told=[], tail='', done=False)


def _int(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def sanitize(state):
    """A state that came over the network: known fields with sane values, or None."""
    if not isinstance(state, dict) or state.get('v') != 1:
        return None
    if state.get('persona') not in ('servitor', 'mensch'):
        return None
    if state.get('lore') not in ('off', 'light', 'full'):
        return None
    numbers = dict(target_ms=(60_000, 3_600_000), words=(0, 50_000), ms=(0, 4_000_000),
                   seg=(0, 200), planned=(1, 200), max_seg=(1, 300))
    for name, (low, high) in numbers.items():
        if not _int(state.get(name), low, high):
            return None
    minutes, wpm = state.get('minutes'), state.get('wpm')
    if not isinstance(minutes, (int, float)) or not 1 <= minutes <= 60:
        return None
    if not isinstance(wpm, (int, float)) or not 60 <= wpm <= 220:
        return None
    ids = [i for i in state.get('ids') or [] if isinstance(i, str) and re.fullmatch(r'[A-Z]\d{2}', i)]
    told = [str(t)[:160] for t in state.get('told') or [] if isinstance(t, str)][-MAX_TOLD:]
    return dict(v=1, persona=state['persona'], lore=state['lore'],
                topic=str(state.get('topic', ''))[:300], minutes=float(minutes), wpm=float(wpm),
                target_ms=state['target_ms'], words=state['words'], ms=state['ms'],
                seg=state['seg'], planned=state['planned'], max_seg=state['max_seg'],
                ids=ids[:12], told=told, tail=str(state.get('tail', ''))[-TAIL_CHARS:],
                done=bool(state.get('done')), short=bool(state.get('short')))


def elapsed_ms(state):
    """Spoken so far: measured audio when known, else estimated from words."""
    if state.get('ms'):
        return state['ms']
    return int(state['words'] * 60000 / state['wpm'])


def remaining_words(state):
    wpm = state['wpm']
    if state.get('ms', 0) >= 45_000 and state['words']:
        wpm = state['words'] * 60000 / state['ms']   # measured speaking rate of this voice
    return max(0, round((state['target_ms'] - elapsed_ms(state)) * wpm / 60000))


def plan(state):
    """dict(words, final) for the next section."""
    remaining = remaining_words(state)
    words = FIRST_WORDS if state['seg'] == 0 else SECTION_WORDS
    last_allowed = state['seg'] + 1 >= state['max_seg']
    final = remaining <= words * 1.35 or last_allowed
    if final:
        words = min(max(80, remaining), round(SECTION_WORDS * 1.35))
    # The section limit was reached long before the target: say so at the end.
    short = final and last_allowed and remaining > words * 1.5
    return dict(words=words, final=final, short=short)


def max_tokens(words, cap):
    """Output tokens for a section of ``words`` (German: about 2 tokens a word)."""
    return int(min(cap, max(256, words * 2.3 + 160)))


_NOTE = re.compile(r'\bNOTIZ\s*:\s*(.+)$', re.IGNORECASE | re.DOTALL)
_END = ('.', '!', '?', '…', '“', '"', '»', '«')


def split_note(text):
    """(spoken text, progress note) — the NOTIZ line is never spoken."""
    found = _NOTE.search(text or '')
    if not found:
        return (text or '').strip(), ''
    return text[:found.start()].strip(), found.group(1).strip()[:160]


def trim_incomplete(text):
    """Drop an unfinished last sentence (a section cut by the token limit);
    the next section continues from the last complete one."""
    text = text.strip()
    if not text or text.endswith(_END):
        return text
    cut = max(text.rfind(mark) for mark in ('. ', '! ', '? ', '… '))
    if cut >= len(text) // 2:
        return text[:cut + 1]
    return text


def advance(state, text, note):
    """State after a section was told."""
    step = plan(state)
    final = step['final']
    state = dict(state)
    if step['short']:
        state['short'] = True
    state['words'] += len(text.split())
    state['seg'] += 1
    summary = note or ' '.join(text.split()[:18])
    state['told'] = (state['told'] + [summary[:160]])[-MAX_TOLD:]
    tail = text[-TAIL_CHARS:]
    if len(text) > TAIL_CHARS:
        start = re.search(r'[.!?…]\s+', tail)
        tail = tail[start.end():] if start else tail
    state['tail'] = tail
    state['done'] = final
    return state


def split_parts(text, max_chars=1200, first_chars=320, size=650):
    """Speakable parts at sentence boundaries; the first one short so playback
    starts early. No part is longer than ``max_chars`` (the server's synthesis
    limit), so nothing is silently cut off."""
    sentences = [s for s in re.split(r'(?<=[.!?…])\s+', text.strip()) if s]
    pieces = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            cut = sentence.rfind(', ', 0, max_chars)
            if cut < max_chars // 3:
                cut = sentence.rfind(' ', 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(sentence[:cut + 1].strip())
            sentence = sentence[cut + 1:].strip()
        if sentence:
            pieces.append(sentence)
    parts, current = [], ''
    for piece in pieces:
        limit = first_chars if not parts else size
        if current and len(current) + 1 + len(piece) > min(limit, max_chars):
            parts.append(current)
            current = piece
        else:
            current = f'{current} {piece}'.strip()
    if current:
        parts.append(current)
    return parts


# --- Prompt --------------------------------------------------------------------------

RULES = """\
Auftrag: eine ausdrücklich gewünschte lange Geschichte zum Vorlesen, insgesamt etwa \
{minutes} Minuten. Für diesen Auftrag gilt die Vorgabe von 1 bis 3 Sätzen nicht.
- Die Geschichte entsteht in Abschnitten, die ohne Pause hintereinander vorgelesen werden. \
Keine Überschriften, keine Abschnittsnummern, keine Frage, ob du weitererzählen sollst, kein \
neuer Einstieg wie „Natürlich“ oder „Hier ist“.
- Ein zusammenhängender Bogen: Ausgangslage, Menschen und Ort, eine wachsende Schwierigkeit, \
Entscheidungen, Folgen und ein ruhiger Schluss. Länge entsteht aus Szenen, Gesprächen und \
Entwicklung, nie aus Wiederholung oder Zusammenfassungen.
- Bereits Erzähltes nicht noch einmal erzählen und nicht als neue Enthüllung darstellen.
- Archiv-Engramme sind feste Anker. Beschreibende Kleinigkeiten wie Geräusche, Gerüche oder \
Handgriffe darfst du ergänzen, aber keine neuen Lebensdaten, Schicksale, Geheimnisse oder \
Fähigkeiten und keine neue Lage, Datierung oder Zugehörigkeit von Orten und Einheiten \
(etwa in welchem Orbit oder Segmentum etwas liegt).
- Ist kein Archiv-Engramm angegeben, erzählst du frei zum gewünschten Thema; eine erfundene \
Geschichte ändert nichts an deiner eigenen Vergangenheit.
- Kein Markdown, keine Listen, keine Emojis."""

VOICE = {
    'mensch': ("Du erzählst als Billy in der Ich-Form, ruhig und anschaulich. Familiensagen "
               "erzählst du als Geschichten deiner Eltern, nicht als eigene Erinnerung; du "
               "warst weder der Wolfsjäger noch der Höllenläufer. Wer am Tor das Schott hinter "
               "dir schließen ließ, weißt du nicht; du kannst nur vermuten."),
    'servitor': ("Du erzählst als Proximus einen Archivbericht in flüssigem, abwechslungsreichem "
                 "Deutsch, ohne Ich-Form, ohne Personennamen aus dem gelöschten Bestand und ohne "
                 "menschliche Erinnerungsfragmente. Sagen erzählst du als überlieferte "
                 "Archivgeschichte mit Ereignissen, Figuren und Folgen."),
}


def _section_ids(state):
    """The engrams this section tells about: the packet spread over the plan."""
    ids = state['ids']
    if not ids:
        return []
    planned = max(1, state['planned'])
    seg = min(state['seg'], planned - 1)
    start = math.floor(seg * len(ids) / planned)
    end = max(start + 1, math.floor((seg + 1) * len(ids) / planned))
    return ids[start:end]


FREE = ("Diese Geschichte stammt nicht aus deinem Archiv: Erzähle sie als erfundene Geschichte "
        "mit eigenen Figuren, nicht als deine eigene Erinnerung und nicht als Ich-Erzählung über "
        "dein Leben. Sie verändert nichts an deiner Vergangenheit.")


def messages(state):
    """[system, user] for the next section; stable parts first."""
    import llm
    import lore
    persona, level = state['persona'], state['lore']
    minutes = f"{state['minutes']:g}".replace('.', ',')
    voice = VOICE[persona] if state['ids'] else FREE
    parts = [llm.PERSONA_PROMPTS[persona], llm.LORE_PROMPTS[persona][level],
             RULES.format(minutes=minutes), voice]
    known = lore.by_id()
    current = [known[i] for i in _section_ids(state)
               if i in known and lore.visible(known[i], persona, level)]
    if current:
        parts.append(lore.block(current, persona))
    upcoming = [known[i].get('thema', i) for i in state['ids'][state['ids'].index(current[-1]['id']) + 1:]
                if i in known] if current else []
    progress = [f"Gewünscht: {state['topic']}"]
    if state['told']:
        progress.append("Schon erzählt: " + ' / '.join(state['told']))
    if upcoming and not plan(state)['final']:
        progress.append("Später noch: " + ', '.join(upcoming[:6]))
    parts.append('Erzählstand:\n' + '\n'.join(progress))
    step = plan(state)
    if state['seg'] == 0:
        start_text = "Beginne die Geschichte."
    else:
        start_text = ("Setze nahtlos dort fort, wo der letzte Abschnitt endete, ohne ihn zu "
                      f"wiederholen. Er endete mit: „{state['tail']}“")
    end_text = ("Führe die Geschichte in diesem Abschnitt zu einem ruhigen, vollständigen Schluss."
                if step['final'] else
                "Höre an einer natürlichen Stelle mitten in der Geschichte auf, ohne Schlusswort.")
    user = (f"{start_text} Erzähle jetzt etwa {step['words']} Wörter. {end_text} Schreibe ganz "
            "am Ende in einer eigenen Zeile NOTIZ: und in höchstens 20 Wörtern, was in diesem "
            "Abschnitt geschah. Diese Zeile wird nicht vorgelesen.")
    return [dict(role='system', content='\n\n'.join(parts)), dict(role='user', content=user)]


# --- Pi side: the queue of a running story ------------------------------------------

class StoryRun:
    """One running story on the Pi: the state for the next request, the queue
    of parts to play (audio files from the server or texts for local speech)
    and the job fetching the next section. RAM only; ``cancel`` drops all."""

    def __init__(self, state, remote, clock=time.monotonic):
        self.state = state
        self.remote = remote
        self.clock = clock
        self.queue = deque()           # dict(path=..., text=..., ms=...)
        self.job = None
        self.failed = None             # reason once a section could not be fetched
        self.notice_given = False
        self.playing = None            # (started, ms) of the part being played
        self.current_path = None       # its audio file, removed once played
        self.played_ms = 0
        self.parts_played = 0

    def add(self, path=None, text=None, ms=None):
        if ms is None:
            wpm = (self.state or {}).get('wpm', WORDS_PER_MINUTE)
            ms = int(len((text or '').split()) * 60000 / wpm)
        self.queue.append(dict(path=path, text=text, ms=int(ms)))

    def queued_ms(self):
        left = 0
        if self.playing is not None:
            started, ms = self.playing
            left = max(0, ms - int((self.clock() - started) * 1000))
        return left + sum(item['ms'] for item in self.queue)

    def needs_more(self):
        return (self.state is not None and not self.state.get('done')
                and self.job is None and self.failed is None
                and len(self.queue) < MAX_PARTS and self.queued_ms() < LOOKAHEAD_MS)

    def next_part(self):
        if not self.queue:
            return None
        item = self.queue.popleft()
        self.playing = (self.clock(), item['ms'])
        return item

    def part_finished(self):
        if self.playing is not None:
            self.played_ms += self.playing[1]
            self.parts_played += 1
        self.playing = None

    @property
    def finished(self):
        """Nothing left to play or fetch."""
        return (not self.queue and self.playing is None and self.job is None
                and (self.state is None or self.state.get('done') or self.failed is not None))

    def cancel(self):
        job, self.job = self.job, None
        if job is not None:
            job.cancel()
        for path in [item.get('path') for item in self.queue] + [self.current_path]:
            if path:
                Path(path).unlink(missing_ok=True)
        self.queue.clear()
        self.playing = self.current_path = None


ABORTED = {
    'servitor': "Die Erzählung bricht hier ab. Der Sprachkern liefert keinen weiteren Abschnitt.",
    'mensch': "Hier muss ich abbrechen. Der nächste Teil der Geschichte kommt nicht durch.",
}
SHORTER = {
    'servitor': "Die Erzählung ist kürzer als angefordert. Die Abschnittsgrenze ist erreicht.",
    'mensch': "Die Geschichte ist kürzer geworden als gewünscht. Mehr Abschnitte gingen nicht.",
}
OLD_DEVICE = {
    'servitor': ("Lange Erzählungen benötigen eine aktuelle Gerätesoftware. Eine kurze Fassung "
                 "ist auf Anforderung möglich."),
    'mensch': ("Für eine lange Geschichte braucht das Gerät ein Update. Eine kurze Fassung kann "
               "ich dir aber erzählen."),
}
