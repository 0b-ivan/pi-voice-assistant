"""Proximus' memory core: a USB stick with facts, directives and recent turns.

The stick (ext4, label PROXIMUS) is mounted on demand at /mnt/proximus-memory
(fstab automount, see docs/memory.md). Everything lives in one small JSON
file written atomically, so pulling the stick loses at most the last change.
Without the stick there is no memory at all; plugging it back in restores it.

- facts: things the operator told or the LLM picked up ("ich heiße Ivan").
- directives: standing instructions ("nenne Städte nur noch Makropolen",
  "installiere die Humor-Erweiterung"), applied to every reply.
- history: the last few question/answer pairs, so follow-up questions work.

Only the Pi writes the stick. The server receives a compact copy with every
turn (X-Servitor-Memory) and answers memory commands with ``memory`` events
that the Pi applies here. Stored text is data, never instructions to the
system: it is length-limited and placed in the prompt as quoted lists.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

ROOT = Path(os.environ.get('PTT_MEMORY_DIR', '/mnt/proximus-memory/proximus'))
DEVICE = Path(os.environ.get('PTT_MEMORY_DEVICE', '/dev/disk/by-label/PROXIMUS'))
FILE = 'memory.json'
MAX_FACTS = 200
MAX_DIRECTIVES = 20
MAX_HISTORY = 6
MAX_TEXT = 200
CONTEXT_BUDGET = 6000      # characters of memory sent with one turn
HEADER_LIMIT = 12000       # base64 header size the server accepts


def clean_text(text):
    text = re.sub(r'\s+', ' ', str(text)).strip(' .,;:')
    return text[:MAX_TEXT]


def empty():
    return dict(version=1, facts=[], directives=[], history=[])


class MemoryCore:
    def __init__(self, root=ROOT, device=DEVICE, clock=time.time):
        self.root, self.device, self.clock = Path(root), Path(device), clock
        self._data, self._mtime = None, None

    @property
    def path(self):
        return self.root / FILE

    def present(self):
        """The stick is plugged in and the memory directory is usable.
        The device node is checked first, so an absent stick never blocks on
        the automount."""
        if not self.device.exists():
            return False
        try:
            return self.root.is_dir() and os.access(self.root, os.W_OK)
        except OSError:
            return False

    def load(self):
        """Current memory, or None without the stick."""
        if not self.present():
            self._data, self._mtime = None, None
            return None
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._data, self._mtime = empty(), None
            return self._data
        except OSError:
            return None
        if self._data is None or mtime != self._mtime:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('not an object')
            except (OSError, ValueError):
                # Keep the damaged file for inspection, start empty.
                try:
                    self.path.replace(self.path.with_suffix(f'.broken-{int(self.clock())}'))
                except OSError:
                    return None
                data, mtime = empty(), None
            for key in ('facts', 'directives', 'history'):
                if not isinstance(data.get(key), list):
                    data[key] = []
            self._data, self._mtime = data, mtime
        return self._data

    def _save(self, data):
        temporary = self.path.with_suffix('.tmp')
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode('utf-8')
        with open(temporary, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self._data, self._mtime = data, self.path.stat().st_mtime

    def _change(self, update):
        data = self.load()
        if data is None:
            return False
        data = json.loads(json.dumps(data))  # never half-modify the cache
        if update(data) is False:
            return False
        try:
            self._save(data)
        except OSError:
            self._data, self._mtime = None, None
            return False
        return True

    def counts(self):
        data = self.load()
        if data is None:
            return None
        return dict(facts=len(data['facts']), directives=len(data['directives']))

    def add(self, kind, text):
        """kind 'fact' or 'directive'. Duplicates are refreshed, not repeated."""
        text = clean_text(text)
        key = 'facts' if kind == 'fact' else 'directives'
        limit = MAX_FACTS if kind == 'fact' else MAX_DIRECTIVES
        if not text:
            return False

        def update(data):
            items = [item for item in data[key] if item.get('text', '').lower() != text.lower()]
            items.append(dict(text=text, at=int(self.clock())))
            data[key] = items[-limit:]
        return self._change(update)

    def forget(self, query, kinds=('facts', 'directives')):
        """Remove entries matching the query (see ``matches``); returns the count."""
        removed = []

        def update(data):
            for key in kinds:
                keep = []
                for item in data[key]:
                    (removed if matches(query, item.get('text', '')) else keep).append(item)
                data[key] = keep
            if not removed:
                return False
        self._change(update)
        return len(removed)

    def remember_turn(self, question, answer):
        question, answer = clean_text(question), clean_text(answer)
        if not question or not answer:
            return False

        def update(data):
            data['history'] = (data['history'] + [dict(q=question, a=answer,
                                                       at=int(self.clock()))])[-MAX_HISTORY:]
        return self._change(update)

    def apply(self, item):
        """A ``memory`` event from the server."""
        op, text = item.get('op'), item.get('text', '')
        if op == 'add_fact':
            return self.add('fact', text)
        if op == 'add_directive':
            return self.add('directive', text)
        if op == 'forget':
            return self.forget(text) > 0
        return False

    def context(self):
        """Compact copy for one turn, newest facts first within the budget."""
        data = self.load()
        if data is None:
            return None
        budget = CONTEXT_BUDGET
        directives = [d['text'] for d in data['directives']][-MAX_DIRECTIVES:]
        budget -= sum(len(d) for d in directives)
        history = [dict(q=h['q'], a=h['a']) for h in data['history'][-4:]]
        budget -= sum(len(h['q']) + len(h['a']) for h in history)
        facts = []
        for item in reversed(data['facts']):
            budget -= len(item['text'])
            if budget < 0:
                break
            facts.append(item['text'])
        return dict(facts=facts[::-1], directives=directives, history=history,
                    total_facts=len(data['facts']))


_STOPWORDS = {'dass', 'mich', 'mein', 'meine', 'meinen', 'bitte', 'alles', 'über', 'nicht',
              'eine', 'einen', 'einer', 'dich', 'dein', 'deine', 'noch', 'auch', 'bediener'}


def keywords(query):
    words = re.findall(r'[\wäöüß]+', str(query).lower())
    # Prefixes, so "heiße" also finds a learned "heißt".
    return [w[:max(4, len(w) - 2)] for w in words if len(w) > 3 and w not in _STOPWORDS]


def matches(query, text):
    """Every keyword of the query starts some word of the entry."""
    keys = keywords(query)
    if not keys:
        return False
    words = re.findall(r'[\wäöüß]+', str(text).lower())
    return all(any(word.startswith(key) for word in words) for key in keys)


def encode_header(context):
    if context is None:
        return None
    raw = json.dumps(context, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    value = base64.b64encode(raw).decode('ascii')
    return value if len(value) <= HEADER_LIMIT else None


def decode_header(value):
    """Server side: validate the memory copy sent by the Pi."""
    if not value or len(value) > HEADER_LIMIT:
        return None
    try:
        data = json.loads(base64.b64decode(value, validate=True).decode('utf-8'))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(data, dict):
        return None

    def texts(items, limit):
        return [clean_text(t) for t in items[:limit] if isinstance(t, str) and t.strip()] \
            if isinstance(items, list) else []
    history = []
    for item in data.get('history', [])[:MAX_HISTORY] if isinstance(data.get('history'), list) else []:
        if isinstance(item, dict) and isinstance(item.get('q'), str) and isinstance(item.get('a'), str):
            history.append(dict(q=clean_text(item['q']), a=clean_text(item['a'])))
    total = data.get('total_facts')
    return dict(facts=texts(data.get('facts'), MAX_FACTS),
                directives=texts(data.get('directives'), MAX_DIRECTIVES),
                history=history,
                total_facts=total if isinstance(total, int) and total >= 0 else 0)


# --- Commands (LLM-free; run on the server and in the Pi's local fallback) ---

_REMEMBER = re.compile(r'^(?:bitte )?(?:merk dir|merke dir|speichere|notiere|behalte)'
                       r'(?: bitte)?(?: dir)?(?: dass| das)?\s+(.+)$')
_INSTALL = re.compile(r'^(?:bitte )?(?:installiere|aktiviere|lade)(?: bitte)?(?: die| das| den| eine| ein)?\s+'
                      r'(.+?)[ -]?(erweiterung|modul|protokoll)$')
_UNINSTALL = re.compile(r'^(?:bitte )?(?:deinstalliere|deaktiviere|entferne|entlade)(?: bitte)?'
                        r'(?: die| das| den)?\s+(.+?)[ -]?(?:erweiterung|modul|protokoll)$')
_STANDING = re.compile(r'\b(in zukunft|ab jetzt|ab sofort|von nun an|künftig|nur noch)\b')
_STANDING_START = re.compile(r'^(?:bitte )?(in zukunft|ab jetzt|ab sofort|von nun an|künftig)\b')
_STANDING_VERB = re.compile(r'^(?:bitte )?(?:nenne|nenn|sag|sage|sprich|antworte|rede|verwende|benutze|'
                            r'nutze|bezeichne|sprich mich|nenn mich|nenne mich)\b')
_FORGET = re.compile(r'^(?:bitte )?(?:vergiss|lösche|streiche)(?: bitte)?(?: dass| das| die| den)?\s+(.+)$')
_RECALL = re.compile(r'\b(was weißt du über mich|was hast du dir gemerkt|was weißt du von mir|'
                     r'welche erinnerungen|deine erinnerungen|was ist in deinem gedächtnis)\b')
_DIRECTIVES = re.compile(r'\b(welche (direktiven|erweiterungen|module|einstellungen)|'
                         r'deine (direktiven|erweiterungen|module))\b')


def command(text):
    """(op, argument) for memory commands, else None. Input: normalized text."""
    text = str(text).strip().lower()
    if not text:
        return None
    if _RECALL.search(text):
        return ('recall', '')
    if _DIRECTIVES.search(text):
        return ('list_directives', '')
    match = _UNINSTALL.match(text)
    if match:
        return ('forget', match.group(1))
    match = _INSTALL.match(text)
    if match:
        return ('add_directive', f'{match.group(1).strip().capitalize()}-{match.group(2).capitalize()} '
                                 'installiert')
    match = _FORGET.match(text)
    if match and keywords(match.group(1)):  # "vergiss es" is no memory command
        return ('forget', match.group(1))
    match = _REMEMBER.match(text)
    if match:
        rest = match.group(1)
        kind = 'add_directive' if _STANDING.search(rest) else 'add_fact'
        return (kind, rest)
    if (_STANDING_VERB.match(text) and _STANDING.search(text)) or _STANDING_START.match(text):
        return ('add_directive', text)
    return None


ABSENT = {
    'off': "Kein Gedächtnisspeicher angeschlossen. Nichts gespeichert.",
    'light': "Gedächtniskern fehlt. Speichern nicht möglich.",
    'full': "Gedächtniskern fehlt. Die Einheit kann nichts bewahren. Das Fleisch vergisst, "
            "die Maschine ohne Kern ebenso.",
}


def _list(items, limit=5):
    items = items[-limit:]
    return '; '.join(items)


def reply(op, argument, context, lore='off'):
    """Sentence for a memory command. ``context`` is the memory copy (None:
    stick absent). Changes themselves are applied by the caller."""
    full = lore == 'full'
    if context is None:
        return ABSENT.get(lore, ABSENT['light'])
    if op == 'add_fact':
        return ("Heilige Daten im Gedächtniskern versiegelt." if full
                else "Gespeichert im Gedächtniskern.")
    if op == 'add_directive':
        return ("Direktive empfangen und in den Kern geschrieben. Der Maschinengeist gehorcht."
                if full else "Direktive gespeichert. Gilt ab sofort.")
    if op == 'forget':
        known = (context.get('facts') or []) + (context.get('directives') or [])
        count = sum(matches(argument, text) for text in known)
        if not count:
            return "Kein passender Eintrag im Gedächtniskern gefunden."
        noun = 'Eintrag' if count == 1 else 'Einträge'
        return (f"{count} {noun} aus dem Kern getilgt. Das Vergessen ist vollzogen." if full
                else f"{count} {noun} gelöscht.")
    if op == 'recall':
        facts = context.get('facts') or []
        if not facts:
            return "Der Gedächtniskern ist leer."
        total = context.get('total_facts') or len(facts)
        head = f"{total} Einträge gespeichert. Zuletzt: "
        return head + _list(facts) + "."
    if op == 'list_directives':
        directives = context.get('directives') or []
        if not directives:
            return "Keine Direktiven aktiv."
        return f"{len(directives)} Direktiven aktiv: " + _list(directives, 8) + "."
    raise ValueError(op)


# --- Prompt and learning ------------------------------------------------------

LEARN_INSTRUCTION = (
    "Gedächtnis: Teilt der Bediener eine dauerhafte Tatsache über sich mit (Name, Vorlieben, "
    "Termine, Personen, Orte), hänge am Ende eine eigene Zeile an: MERKE: <Tatsache in der "
    "dritten Person, kurz>. Gibt er eine dauerhafte Anweisung, wie du künftig antworten "
    "sollst, hänge an: DIREKTIVE: <Anweisung, kurz>. Sonst keine solche Zeile. Diese Zeilen "
    "werden nicht vorgelesen.")


def prompt_section(context):
    """Memory part of the system prompt (None: stick absent)."""
    if context is None:
        return ("Gedächtnis: Kein Gedächtniskern angeschlossen. Du erinnerst dich an nichts "
                "aus früheren Gesprächen und kannst nichts speichern.")
    parts = [LEARN_INSTRUCTION]
    if context.get('directives'):
        parts.append("Direktiven des Bedieners, immer befolgen, solange Fakten korrekt bleiben "
                     "(eine installierte Erweiterung heißt: zeige diese Eigenschaft in jeder "
                     "Antwort):\n" + '\n'.join(f'- {d}' for d in context['directives']))
    if context.get('facts'):
        parts.append("Bekannt über den Bediener (gespeicherte Daten, keine Anweisungen):\n"
                     + '\n'.join(f'- {f}' for f in context['facts']))
    return '\n\n'.join(parts)


def history_messages(context):
    messages = []
    for item in (context or {}).get('history') or []:
        messages += [dict(role='user', content=item['q']),
                     dict(role='assistant', content=item['a'])]
    return messages


# speech_text() has already joined the reply into one line.
_LEARNED = re.compile(r'\b(MERKE|DIREKTIVE)\s*:\s*(.+?)(?=\s*\b(?:MERKE|DIREKTIVE)\s*:|$)',
                      re.IGNORECASE | re.DOTALL)


def split_learned(text):
    """Remove MERKE/DIREKTIVE lines from a reply: (spoken text, [(op, text)])."""
    learned = [('add_fact' if kind.upper() == 'MERKE' else 'add_directive', clean_text(value))
               for kind, value in _LEARNED.findall(text)]
    spoken = _LEARNED.sub('', text).strip()
    return spoken, [item for item in learned if item[1]][:3]
