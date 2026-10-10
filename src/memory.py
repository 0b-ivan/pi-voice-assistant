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
PERSONAS = ('servitor', 'mensch')
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

    def remember_turn(self, question, answer, mood=None, persona=None):
        """``mood``: 'gereizt:0.62', the feeling of this answer (restart baseline).
        ``persona``: who answered, so a later style switch keeps facts, not style."""
        question, answer = clean_text(question), clean_text(answer)
        if not question or not answer:
            return False
        entry = dict(q=question, a=answer, at=int(self.clock()))
        if isinstance(mood, str) and len(mood) <= 24:
            entry['mood'] = mood
        if persona in PERSONAS:
            entry['p'] = persona

        def update(data):
            data['history'] = (data['history'] + [entry])[-MAX_HISTORY:]
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
        history = [dict(q=h['q'], a=h['a'], **({'p': h['p']} if h.get('p') in PERSONAS else {}))
                   for h in data['history'][-4:]]
        budget -= sum(len(h['q']) + len(h['a']) for h in history)
        facts = []
        for item in reversed(data['facts']):
            budget -= len(item['text'])
            if budget < 0:
                break
            facts.append(item['text'])
        context = dict(facts=facts[::-1], directives=directives, history=history,
                       total_facts=len(data['facts']))
        prints = self.voiceprints()
        if prints:
            context['voiceprints'] = prints
        return context

    # --- Voiceprint (written by the enrollment session) -------------------

    @property
    def voice_dir(self):
        return self.root / 'voice'

    def _profiles(self):
        """{name: data} of all voice profiles (voice/profiles/*.json, plus the
        single-profile file of the first version)."""
        found = {}
        paths = sorted((self.voice_dir / 'profiles').glob('*.json'))
        legacy = self.voice_dir / 'voiceprint.json'
        for path in ([legacy] if legacy.is_file() else []) + paths:
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get('print'), str):
                name = clean_text(data.get('name') or 'Bediener')[:40]
                found[name] = dict(data, name=name, path=str(path))
        return found

    def voiceprints(self):
        """[{'name', 'print'}] of every known person (base64 int8), or []."""
        return [dict(name=name, print=data['print']) for name, data in self._profiles().items()]

    def profile(self, name):
        """One person's profile (name, print, count, at, passphrase, ...) or None."""
        data = self._profiles().get(name)
        return {k: v for k, v in data.items() if k != 'path'} if data else None

    def update_profile(self, name, **fields):
        data = self._profiles().get(name)
        if data is None:
            return False
        path = Path(data.pop('path'))
        data.update(fields)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data), encoding='utf-8')
        temporary.replace(path)
        return True

    def delete_profile(self, name):
        data = self._profiles().get(name)
        if data is None:
            return False
        Path(data['path']).unlink(missing_ok=True)
        return True

    def people(self):
        """[(name, recordings)] of the known voices."""
        return [(name, int(data.get('count') or 0)) for name, data in self._profiles().items()]

    def save_voiceprint(self, name, print_, count):
        """Store a person's voiceprint; an existing profile of the same name is
        refined (weighted mean with the earlier recordings)."""
        from speaker import decode, encode, normalize
        name = clean_text(name)[:40] or 'Bediener'
        old = self._profiles().get(name)
        total = count
        if old:
            before, after = decode(old['print']), decode(print_)
            weight = int(old.get('count') or 1)
            if before and after and len(before) == len(after):
                print_ = encode(normalize([b * weight + a * count for b, a in zip(before, after)]))
                total = weight + count
        folder = self.voice_dir / 'profiles'
        folder.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'bediener'
        path = folder / f'{slug}.json'
        temporary = path.with_suffix('.tmp')
        keep = {k: old[k] for k in ('passphrase', 'last_score') if old and k in old}
        temporary.write_text(json.dumps(dict(keep, name=name, print=print_, count=total,
                                             at=int(self.clock()))), encoding='utf-8')
        temporary.replace(path)
        if old and old['path'] != str(path):
            Path(old['path']).unlink(missing_ok=True)  # moved from the first-version file
        return total


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


def parse_header(value):
    """Server side: the JSON object in the header, not yet checked, or None."""
    if not value or len(value) > HEADER_LIMIT:
        return None
    try:
        data = json.loads(base64.b64decode(value, validate=True).decode('utf-8'))
    except (ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def decode_header(value):
    """Server side: validate the memory copy sent by the Pi."""
    data = parse_header(value)
    return None if data is None else sanitize(data)


def sanitize(data):
    """Only the known fields of a memory copy, cleaned and capped."""
    def texts(items, limit):
        return [clean_text(t) for t in items[:limit] if isinstance(t, str) and t.strip()] \
            if isinstance(items, list) else []
    history = []
    for item in data.get('history', [])[:MAX_HISTORY] if isinstance(data.get('history'), list) else []:
        if isinstance(item, dict) and isinstance(item.get('q'), str) and isinstance(item.get('a'), str):
            entry = dict(q=clean_text(item['q']), a=clean_text(item['a']))
            if item.get('p') in PERSONAS:
                entry['p'] = item['p']
            history.append(entry)
    total = data.get('total_facts')
    prints = []
    for item in data.get('voiceprints', [])[:5] if isinstance(data.get('voiceprints'), list) else []:
        if (isinstance(item, dict) and isinstance(item.get('print'), str)
                and len(item['print']) <= 1500 and isinstance(item.get('name'), str)):
            prints.append(dict(name=clean_text(item['name'])[:40], print=item['print']))
    result = dict(facts=texts(data.get('facts'), MAX_FACTS),
                  directives=texts(data.get('directives'), MAX_DIRECTIVES),
                  history=history,
                  total_facts=total if isinstance(total, int) and total >= 0 else 0)
    if prints:
        result['voiceprints'] = prints
    return result


def unknown_speaker(context):
    """An operator is enrolled and the server did not recognize this voice."""
    return bool(context) and context.get('speaker') == 'unknown'


def guest_view(context):
    """What an unrecognized voice may use: directives only, nothing personal."""
    return dict(facts=[], directives=context.get('directives', []), history=[],
                total_facts=0, speaker='unknown')


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
_PEOPLE = re.compile(r'\b(wen kennst du|welche (personen|menschen|stimmen|leute) kennst du|'
                     r'(bekannte|gespeicherte) (personen|stimmen|stimmprofile)|stimmprofile)\b')
_DIRECTIVES = re.compile(r'\b(welche (direktiven|erweiterungen|module|einstellungen)|'
                         r'deine (direktiven|erweiterungen|module))\b')


def command(text):
    """(op, argument) for memory commands, else None. Input: normalized text."""
    text = str(text).strip().lower()
    if not text:
        return None
    if _RECALL.search(text):
        return ('recall', '')
    if _PEOPLE.search(text) and not re.search(r'nachtrain|verbesser|verfeiner', text):
        return ('list_people', '')
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
    'billy': "Kein Gedächtnis-Stick drin. Ich kann mir gerade nichts merken.",
    'billy_full': "Kein Gedächtnis-Stick drin. Ohne den vergesse ich alles.",
}
# Billy (persona "mensch"): variants per memory command; every variant says
# the same thing (saved / applies from now on / how many were forgotten).
BILLY_REPLIES = {
    'add_fact': ("Gemerkt.", "Ist notiert.", "Gut, das merk ich mir."),
    'add_directive': ("Verstanden. Mach ich ab jetzt so.", "Alles klar, gilt ab jetzt.",
                      "In Ordnung, ab jetzt halte ich mich daran."),
    'forget': ("{count} {noun} vergessen.", "Erledigt, {count} {noun} gelöscht.",
               "{count} {noun} sind weg."),
}
SERVITOR_REPLIES = {
    'add_fact': ("Gespeichert im Gedächtniskern.", "Im Gedächtniskern abgelegt.",
                 "Eintrag gespeichert."),
    'add_directive': ("Direktive gespeichert. Gilt ab sofort.",
                      "Direktive im Kern abgelegt. Sie gilt ab sofort."),
    'forget': ("{count} {noun} gelöscht.", "{count} {noun} aus dem Kern entfernt."),
}


def _list(items, limit=5):
    items = items[-limit:]
    return '; '.join(items)


def reply(op, argument, context, lore='off'):
    """Sentence for a memory command. ``context`` is the memory copy (None:
    stick absent). Changes themselves are applied by the caller."""
    full = lore == 'full'
    billy = lore in ('billy', 'billy_full')
    if context is None:
        return ABSENT.get(lore, ABSENT['light'])
    if unknown_speaker(context):
        return GUEST_TEXT
    import variants
    if billy and op in ('add_fact', 'add_directive'):
        return variants.pick(f'memory.billy.{op}', BILLY_REPLIES[op])
    if op == 'add_fact':
        return ("Heilige Daten im Gedächtniskern versiegelt." if full
                else variants.pick('memory.add_fact', SERVITOR_REPLIES['add_fact']))
    if op == 'add_directive':
        return ("Direktive empfangen und in den Kern geschrieben. Sie gilt ab sofort."
                if full else variants.pick('memory.add_directive',
                                           SERVITOR_REPLIES['add_directive']))
    if op == 'forget':
        known = (context.get('facts') or []) + (context.get('directives') or [])
        count = sum(matches(argument, text) for text in known)
        if not count:
            return ("Dazu hab ich nichts gespeichert." if billy
                    else "Kein passender Eintrag im Gedächtniskern gefunden.")
        noun = 'Eintrag' if count == 1 else 'Einträge'
        if billy:
            return variants.pick('memory.billy.forget', BILLY_REPLIES['forget'],
                                 count=count, noun=noun)
        return (f"{count} {noun} aus dem Kern getilgt. Das Vergessen ist vollzogen." if full
                else variants.pick('memory.forget', SERVITOR_REPLIES['forget'],
                                   count=count, noun=noun))
    if op == 'recall':
        facts = context.get('facts') or []
        if not facts:
            return "Da ist noch nichts drin." if billy else "Der Gedächtniskern ist leer."
        total = context.get('total_facts') or len(facts)
        head = f"{total} Einträge gespeichert. Zuletzt: "
        return head + _list(facts) + "."
    if op == 'list_people':
        names = [p['name'] for p in context.get('voiceprints') or []]
        if not names:
            return "Noch keine Stimme bekannt. Kennenlernen im Menü starten."
        noun = 'Person' if len(names) == 1 else 'Personen'
        return f"{len(names)} {noun} an der Stimme bekannt: " + ", ".join(names) + "."
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


GUEST_TEXT = ("Stimme nicht als Bediener erkannt. Persönliche Daten nur für den Bediener.")


def prompt_section(context):
    """Memory part of the system prompt (None: stick absent)."""
    if context is None:
        return ("Gedächtnis: Kein Gedächtniskern angeschlossen. Du erinnerst dich an nichts "
                "aus früheren Gesprächen und kannst nichts speichern.")
    if unknown_speaker(context):
        parts = ["Sprecher: Die Stimme gehört nicht dem Bediener. Gib keine persönlichen "
                 "Daten über den Bediener preis und schreibe keine MERKE- oder "
                 "DIREKTIVE-Zeilen."]
        if context.get('directives'):
            parts.append("Direktiven des Bedieners, immer befolgen:\n"
                         + '\n'.join(f'- {d}' for d in context['directives']))
        parts.append("Der aktuelle Sprecher ist ein Gast. Anredewünsche des Bedieners gelten "
                     "nur für den erkannten Bediener. Sprich den Gast ohne Namen, Rang oder "
                     "Titel an, insbesondere nicht als Kommandant, Boss oder Bediener.")
        return '\n\n'.join(parts)
    parts = [LEARN_INSTRUCTION]
    if context.get('speaker'):
        parts.append(f"Sprecher: an der Stimme erkannt als {context['speaker']}, der Bediener. "
                     "Den Namen nur gelegentlich verwenden, nicht in jeder Antwort.")
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
