"""Conversation threads per person on the memory core (stage 1, not wired yet).

Concept: docs/concepts/proximus-context/KONZEPT.md. This module stores the
original question/answer turns of each person (owner) in up to three threads,
a small working state whose fields quote the person's own words, and selects
the context for one model request. It lives inside the existing memory.json
(field ``conversations``) and writes only through ``MemoryCore._change``.

Not active in the voice path yet: personal context needs a voice identity
checked BEFORE the context is chosen (stage 2, ticket identify). Until then
nothing imports this module at runtime.

- owner: a stable profile id (``memory.profile_ids``) or DEVICE_OPERATOR when
  no voice is enrolled. Names, personas and sessions are never owners.
- Quotas are retention limits per owner; one owner never evicts another.
- A pause (12 h) only stops automatic use of old turns; nothing is deleted.
- "Neues Gespräch" archives, "Vergiss dieses Gespräch" deletes.
"""
import copy
import json
import re
import uuid

VERSION = 2
DEVICE_OPERATOR = 'device-operator'
MAX_OWNERS = 5
MAX_THREADS = 3
MAX_TURNS = 48
MAX_TEXT = 2048
MAX_SLOTS = 8
MAX_QUOTE = 400
MAX_SEEN = 128
MAX_QUESTIONS = 3       # open questions (stage 3 fills them)
MAX_TIME_REFS = 4
MAX_TITLE = 60
OWNER_BYTES = 128 * 1024
IDLE_SECONDS = 12 * 3600
RECENT_PAIRS = 4
EVIDENCE = 2            # older matching turns besides the first request
PERSONAS = ('servitor', 'mensch')
DELIVERY = ('pending', 'played', 'interrupted', 'failed')


class StaleTurn(ValueError):
    """The turn was prepared against a state that changed since (forget,
    thread switch, other stick): it must not be written."""


class QuotaError(ValueError):
    pass


class UnsupportedSchema(ValueError):
    """An unknown conversations version: left untouched, never overwritten."""


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def clean(text):
    return re.sub(r'\s+', ' ', text).strip() if isinstance(text, str) else ''


def _ident(value):
    return isinstance(value, str) and 0 < len(value) <= 80


def _count(value):
    return type(value) is int and value >= 0


def new_id():
    return uuid.uuid4().hex


# --- one thread ---------------------------------------------------------------

def fresh(owner, now):
    if not _ident(owner):
        raise ValueError('stable owner id required')
    if not _count(now):
        raise ValueError('invalid timestamp')
    return dict(version=VERSION, id=new_id(), owner=owner, title=None, updated_at=now,
                epoch=0, revision=0, discarded=0, turns=[], slots={}, seen=[], questions=[])


def validate(state):
    """A checked deep copy of one thread; ValueError if anything is off."""
    if not isinstance(state, dict) or state.get('version') != VERSION:
        raise ValueError('unsupported thread version')
    if not _ident(state.get('owner')) or not _ident(state.get('id')):
        raise ValueError('invalid scope')
    for key in ('updated_at', 'epoch', 'revision', 'discarded'):
        if not _count(state.get(key)):
            raise ValueError(f'invalid {key}')
    title = state.get('title')
    if title is not None and (not isinstance(title, str) or not 0 < len(title) <= MAX_TITLE):
        raise ValueError('invalid title')
    for key, limit in (('turns', MAX_TURNS), ('seen', MAX_SEEN), ('questions', MAX_QUESTIONS)):
        if not isinstance(state.get(key), list) or len(state[key]) > limit:
            raise ValueError(f'invalid {key}')
    if any(not _ident(i) for i in state['seen']) or len(set(state['seen'])) != len(state['seen']):
        raise ValueError('invalid receipts')
    ids = set()
    for turn in state['turns']:
        if not isinstance(turn, dict) or not _ident(turn.get('id')) or turn['id'] in ids:
            raise ValueError('invalid turn id')
        ids.add(turn['id'])
        if not _count(turn.get('at')) or turn.get('p') not in PERSONAS \
                or turn.get('delivery') not in DELIVERY:
            raise ValueError('invalid turn metadata')
        for key in ('q', 'a'):
            if not isinstance(turn.get(key), str) or not 0 < len(turn[key]) <= MAX_TEXT:
                raise ValueError('invalid turn text')
        refs = turn.get('context_ids')
        if not isinstance(refs, list) or len(refs) > MAX_TURNS or any(not _ident(r) for r in refs):
            raise ValueError('invalid dependencies')
        times = turn.get('time_refs', [])
        if not isinstance(times, list) or len(times) > MAX_TIME_REFS:
            raise ValueError('invalid time references')
    if not isinstance(state.get('slots'), dict) or len(state['slots']) > MAX_SLOTS:
        raise ValueError('invalid working state')
    turns = {t['id']: t for t in state['turns']}
    for key, slot in state['slots'].items():
        _check_slot(key, slot, turns)
    for question in state['questions']:
        if not isinstance(question, dict) or question.get('source_id') not in turns:
            raise ValueError('question without source')
    return copy.deepcopy(state)


def _check_slot(key, slot, turns):
    if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', key) \
            or not isinstance(slot, dict) or set(slot) != {'source_id', 'start', 'end', 'quote'}:
        raise ValueError('invalid slot')
    source = turns.get(slot['source_id'])
    start, end = slot['start'], slot['end']
    if source is None or type(start) is not int or type(end) is not int \
            or not 0 <= start < end <= len(source['q']) or end - start > MAX_QUOTE:
        raise ValueError('slot without user source')
    if slot['quote'] != source['q'][start:end]:
        raise ValueError('quote differs from the user source')


def snapshot(state, stick, write_epoch=0):
    """What a turn was prepared against; checked again before its commit."""
    return dict(owner=state['owner'], id=state['id'], epoch=state['epoch'],
                revision=state['revision'], stick=stick, write_epoch=write_epoch)


def _evict_turn(state):
    pinned = {s['source_id'] for s in state['slots'].values()}
    pinned |= {q['source_id'] for q in state['questions']}
    # Unpinned old turns go first; the quota may still force a pinned one.
    index = next((i for i, t in enumerate(state['turns'][:-1]) if t['id'] not in pinned), 0)
    state['turns'].pop(index)
    state['discarded'] += 1
    _drop_orphans(state)


def _drop_orphans(state):
    ids = {t['id'] for t in state['turns']}
    state['slots'] = {k: s for k, s in state['slots'].items() if s['source_id'] in ids}
    state['questions'] = [q for q in state['questions'] if q['source_id'] in ids]
    for turn in state['turns']:
        turn['context_ids'] = [i for i in turn['context_ids'] if i in ids]


def commit_turn(state, *, expected, stick, turn_id, question, answer, persona, now,
                context_ids=(), updates=None):
    """The thread with this turn added (a new copy), or the same state for a
    duplicate turn id. Raises StaleTurn/ValueError; never truncates text.

    ``context_ids``: turns actually exported to the model for this answer
    (dependencies for a later targeted forget). ``updates``: working-state
    selectors {field: {source_id, start, end}} quoting a user text."""
    state = validate(state)
    keys = ('owner', 'id', 'epoch', 'stick')
    actual = snapshot(state, stick)
    if not isinstance(expected, dict) or any(expected.get(k) != actual[k] for k in keys):
        raise StaleTurn('thread changed since the turn started')
    if turn_id in state['seen']:
        return state                        # retry of an already stored turn
    if expected.get('revision') != state['revision']:
        raise StaleTurn('thread changed since the turn started')
    if not _ident(turn_id) or persona not in PERSONAS or not _count(now):
        raise ValueError('invalid turn metadata')
    q, a = clean(question), clean(answer)
    if not q or not a or len(q) > MAX_TEXT or len(a) > MAX_TEXT:
        raise ValueError('empty or oversized text: route it explicitly, never cut it')
    ids = {t['id'] for t in state['turns']}
    context_ids = list(dict.fromkeys(context_ids))
    if len(context_ids) > MAX_TURNS or any(i not in ids for i in context_ids):
        raise ValueError('unknown dependency')
    state['turns'].append(dict(id=turn_id, q=q, a=a, p=persona, at=now, delivery='pending',
                               context_ids=context_ids))
    state['seen'] = (state['seen'] + [turn_id])[-MAX_SEEN:]
    if updates is not None:
        _apply_updates(state, updates, set(context_ids) | {turn_id})
    state['updated_at'] = now
    state['revision'] += 1
    while len(state['turns']) > MAX_TURNS:
        _evict_turn(state)
    return state


def _apply_updates(state, updates, exported):
    if not isinstance(updates, dict) or len(updates) > MAX_SLOTS:
        raise ValueError('invalid update')
    turns = {t['id']: t for t in state['turns']}
    order = {t['id']: i for i, t in enumerate(state['turns'])}
    for key, selector in updates.items():
        if not isinstance(selector, dict) or set(selector) != {'source_id', 'start', 'end'}:
            raise ValueError('invalid selector')
        source_id = selector['source_id']
        if source_id not in exported or source_id not in turns:
            raise ValueError('source was not exported for this turn')
        start, end = selector['start'], selector['end']
        if type(start) is not int or type(end) is not int:
            raise ValueError('invalid selector range')
        slot = dict(selector, quote=turns[source_id]['q'][start:end])
        _check_slot(key, slot, turns)
        before = state['slots'].get(key)
        if before and order[source_id] < order[before['source_id']]:
            raise ValueError('an older source cannot replace a newer value')
        state['slots'][key] = slot          # the earlier turn stays as history
    if len(state['slots']) > MAX_SLOTS:
        raise ValueError('too many working fields')


def set_delivery(state, turn_id, status):
    """Playback result of a stored answer; not a new turn or revision."""
    state = validate(state)
    if status not in DELIVERY[1:]:
        raise ValueError('invalid delivery')
    for turn in state['turns']:
        if turn['id'] == turn_id:
            if turn['delivery'] not in ('pending', status):
                raise StaleTurn('final delivery cannot change')
            turn['delivery'] = status
            return state
    raise StaleTurn('turn was deleted or evicted')


def last_answer(state):
    """(text, delivery) of the newest answer for "Was hast du gesagt?": the
    newest played one, else the newest at all (the caller says it was not
    confirmed). None for an empty thread."""
    turns = state['turns']
    played = [t for t in turns if t['delivery'] == 'played']
    turn = played[-1] if played else (turns[-1] if turns else None)
    return None if turn is None else (turn['a'], turn['delivery'])


_STOP = {'ich', 'der', 'die', 'das', 'und', 'ist', 'mit', 'bitte', 'was', 'wie', 'mir', 'ein',
         'eine', 'nach', 'dann', 'den', 'dem', 'des', 'zu', 'auf', 'für', 'von', 'wir', 'du',
         'sie', 'es', 'nicht', 'auch', 'noch', 'mal', 'aber', 'oder', 'wann', 'wo', 'wer'}


def words(text):
    return {w for w in re.findall(r'\w+', text.casefold()) if len(w) > 2 and w not in _STOP}


def select(state, query, now, resume=False):
    """Context for one model request: the last pairs, the first request of
    the thread, up to two older matching turns and the working state.

    After IDLE_SECONDS nothing is attached unless the thread was explicitly
    resumed; the stored turns stay untouched either way."""
    if not resume and not 0 <= now - state['updated_at'] <= IDLE_SECONDS:
        return dict(history=[], conversation=dict(id=state['id'], stale=bool(state['turns']),
                                                  slots={}, evidence=[]))
    recent = state['turns'][-RECENT_PAIRS:]
    older = state['turns'][:-RECENT_PAIRS]
    asked = words(query)
    matching = [t for t in older if asked & words(t['q'] + ' ' + t['a'])]
    matching.sort(key=lambda t: (len(asked & words(t['q'] + ' ' + t['a'])), t['at']),
                  reverse=True)
    chosen = {t['id']: t for t in older[:1] + matching[:EVIDENCE]}
    return dict(
        history=[dict(id=t['id'], q=t['q'], a=t['a'], p=t['p'], at=t['at'],
                      delivery=t['delivery']) for t in recent],
        conversation=dict(id=state['id'], stale=False, slots=copy.deepcopy(state['slots']),
                          evidence=[dict(id=t['id'], q=t['q'], at=t['at'])
                                    for t in chosen.values()]))


def matching_ids(state, query):
    """Turns whose question or answer contain every word of the query."""
    asked = words(query)
    if not asked:
        return []
    return [t['id'] for t in state['turns'] if asked <= words(t['q'] + ' ' + t['a'])]


def forget_ids(state, ids):
    """(thread, direct, follow-ups): removes the turns, every turn that was
    answered with them in its context, and the working fields they back."""
    state = validate(state)
    direct = set(ids) & {t['id'] for t in state['turns']}
    remove = set(direct)
    while True:
        extra = {t['id'] for t in state['turns'] if remove & set(t['context_ids'])}
        if extra <= remove:
            break
        remove |= extra
    if remove:
        state['turns'] = [t for t in state['turns'] if t['id'] not in remove]
        _drop_orphans(state)
        state['epoch'] += 1                  # late replies may not bring it back
        state['revision'] += 1
    return state, len(direct), len(remove) - len(direct)


# --- all owners in memory.json --------------------------------------------------

def _owner_size(own):
    return len(compact(own).encode('utf-8'))


class Conversations:
    """Owner index and threads inside MemoryCore's JSON file.

    One serialized writer (the PTT controller). ``generation()`` names the
    mounted medium (filesystem id plus a per-mount nonce) or None; a turn
    prepared on another medium is never written."""

    def __init__(self, core, generation, clock=None):
        self.core, self.generation = core, generation
        self.clock = clock or core.clock

    def _now(self):
        return int(self.clock())

    @staticmethod
    def _book(data, create=True):
        book = data.get('conversations')
        if book is None:
            if not create:
                return None
            book = data['conversations'] = dict(version=VERSION, owners={})
        if not isinstance(book, dict) or book.get('version') != VERSION \
                or not isinstance(book.get('owners'), dict):
            raise UnsupportedSchema('unknown conversations schema')
        if len(book['owners']) > MAX_OWNERS:
            raise UnsupportedSchema('too many owners')
        return book

    @staticmethod
    def _active(own):
        return next(t for t in own['threads'] if t['id'] == own['active'])

    def _change(self, owner, update):
        """Run update(own, data) for an existing owner in one transaction.
        Returns update's result, or None when the stick is absent/failing."""
        if not self.generation():
            return None
        result = []

        def apply(data):
            own = self._book(data)['owners'].get(owner)
            if own is None:
                raise StaleTurn('owner not set up')
            result.append(update(own, data))
            return False if result[0] is False else None
        saved = self.core._change(apply)
        if not result:
            return None                     # stick gone while loading
        if result[0] is False:
            return False
        return result[0] if saved else None

    def read(self, owner):
        """(owner index copy, active thread) or (None, None)."""
        data = self.core.load()
        if data is None or not self.generation():
            return None, None
        book = self._book(copy.deepcopy(data), create=False)
        own = book and book['owners'].get(owner)
        if not own:
            return None, None
        return own, validate(self._active(own))

    def ensure(self, owner, legacy=False):
        """Set up an owner (idempotent). ``legacy``: move the old global
        history into this owner's first thread; only for an unambiguous owner
        (no or exactly one voice profile), never into several owners."""
        if not self.generation():
            return False
        now = self._now()

        def update(data):
            book = self._book(data)
            if owner in book['owners']:
                return False
            if len(book['owners']) >= MAX_OWNERS:
                raise QuotaError('owner limit reached; nobody else is evicted')
            state = fresh(owner, now)
            if legacy:
                state['turns'] = _migrated(data.get('history'), now)
                state['updated_at'] = max([t['at'] for t in state['turns']] or [now])
                data['history'] = []   # one writer only: the old field stays empty
            book['owners'][owner] = dict(active=state['id'], threads=[state], resume=None,
                                         write_epoch=0)
        return self.core._change(update)

    def append(self, owner, expected, **turn):
        """Store a generated answer once. True stored, False duplicate or
        refused, None stick absent. Raises StaleTurn for an outdated turn."""
        stick = self.generation()
        if not stick:
            return None

        def update(own, data):
            if expected.get('write_epoch') != own['write_epoch']:
                raise StaleTurn('thread switched since the turn started')
            for i, state in enumerate(own['threads']):
                if state['id'] == own['active']:
                    updated = commit_turn(state, expected=expected, stick=stick, **turn)
                    if updated == state:
                        return False
                    own['threads'][i] = updated
                    own['resume'] = None    # resumed until the next stored turn
                    _quota(own)
                    return True
            raise StaleTurn('active thread missing')
        return self._change(owner, update)

    def prepare(self, owner, query):
        """(snapshot, context) for one request of a known owner, or (None, None)."""
        stick = self.generation()
        own, state = self.read(owner)
        if state is None or not stick:
            return None, None
        resume = own.get('resume') == state['id']
        return (snapshot(state, stick, own['write_epoch']),
                select(state, query, self._now(), resume=resume))

    def delivered(self, owner, turn_id, status):
        def update(own, data):
            for i, state in enumerate(own['threads']):
                if any(t['id'] == turn_id for t in state['turns']):
                    own['threads'][i] = set_delivery(state, turn_id, status)
                    return True
            return False                    # forgotten meanwhile: ignore late events
        return self._change(owner, update)

    # --- commands --------------------------------------------------------------

    def new_thread(self, owner):
        """Archive the active thread (if it has turns) and start an empty one."""
        now = self._now()

        def update(own, data):
            active = self._active(own)
            if not active['turns'] and active['title'] is None:
                return 'empty'
            own['threads'].append(fresh(owner, now))
            own['active'] = own['threads'][-1]['id']
            archived = len(own['threads']) > MAX_THREADS
            own['threads'] = own['threads'][-MAX_THREADS:]
            _switched(own)
            _quota(own)
            return 'dropped_oldest' if archived else 'archived'
        return self._change(owner, update)

    def forget_thread(self, owner):
        """Delete the active thread; returns the number of deleted turns."""
        now = self._now()

        def update(own, data):
            active = self._active(own)
            own['threads'] = [t for t in own['threads'] if t['id'] != active['id']]
            own['threads'].append(fresh(owner, now))
            own['active'] = own['threads'][-1]['id']
            _switched(own)
            return len(active['turns'])
        return self._change(owner, update)

    def forget_all(self, owner):
        """Delete every thread of this owner (facts stay, see docs)."""
        now = self._now()

        def update(own, data):
            count = sum(len(t['turns']) for t in own['threads'])
            own['threads'] = [fresh(owner, now)]
            own['active'] = own['threads'][0]['id']
            _switched(own)
            return count
        return self._change(owner, update)

    def forget_matching(self, owner, query):
        """(direct, follow-ups) removed from this owner's threads for "Vergiss
        Hamburg". Word match only; no match deletes nothing."""
        if not words(query):
            return (0, 0)

        def update(own, data):
            direct = extra = 0
            for i, state in enumerate(own['threads']):
                ids = matching_ids(state, query)
                if ids:
                    own['threads'][i], d, e = forget_ids(state, ids)
                    direct, extra = direct + d, extra + e
            if not direct:
                return (0, 0)
            own['write_epoch'] += 1
            return (direct, extra)
        return self._change(owner, update)

    def resume(self, owner, title=None):
        """Reactivate a thread for automatic context until the next stored
        turn: the named one, else the active one if it has turns, else the
        newest archived. Returns the thread summary or 'ambiguous'/None."""
        def update(own, data):
            if title:
                found = _find(own, title)
                if not isinstance(found, dict):
                    return found
                target = found
            else:
                active = self._active(own)
                others = sorted((t for t in own['threads'] if t['turns'] and t is not active),
                                key=lambda t: t['updated_at'])
                target = active if active['turns'] else (others[-1] if others else None)
                if target is None:
                    return None
            if target['id'] != own['active']:
                own['active'] = target['id']
                _switched(own)
            own['resume'] = target['id']
            return summary(target, own)
        return self._change(owner, update)

    def name(self, owner, title):
        title = clean(title).strip(' .!?')[:MAX_TITLE]
        if not title:
            return None

        def update(own, data):
            if any(t['title'] and t['title'].casefold() == title.casefold()
                   and t['id'] != own['active'] for t in own['threads']):
                return 'duplicate'
            self._active(own)['title'] = title
            return title
        return self._change(owner, update)

    def threads(self, owner):
        own, _ = self.read(owner)
        if own is None:
            return []
        return [summary(t, own) for t in sorted(own['threads'], key=lambda t: t['updated_at'],
                                                reverse=True)]

    def titles(self, owner):
        return [t['title'] for t in self.threads(owner) if t['title']]


def summary(state, own):
    first = state['turns'][0]['q'] if state['turns'] else None
    return dict(id=state['id'], title=state['title'], updated_at=state['updated_at'],
                turns=len(state['turns']), discarded=state['discarded'],
                active=state['id'] == own['active'], first=first,
                slots={k: s['quote'] for k, s in state['slots'].items()})


def _find(own, title):
    """A thread of this owner by title: exact before partial; 'ambiguous' or None."""
    wanted = clean(title).casefold()
    named = [t for t in own['threads'] if t['title']]
    exact = [t for t in named if t['title'].casefold() == wanted]
    if exact:
        return exact[0]
    partial = [t for t in named if wanted in t['title'].casefold()
               or t['title'].casefold() in wanted]
    if len(partial) > 1:
        return 'ambiguous'
    return partial[0] if partial else None


def _switched(own):
    own['resume'] = None
    own['write_epoch'] += 1     # an answer to the old thread can no longer be stored


def _quota(own):
    """Keep this owner within OWNER_BYTES: archived threads first, then the
    oldest turns of the active one (visible in ``discarded``)."""
    active = next(t for t in own['threads'] if t['id'] == own['active'])
    while _owner_size(own) > OWNER_BYTES:
        archived = sorted((t for t in own['threads'] if t is not active),
                          key=lambda t: t['updated_at'])
        if archived:
            own['threads'].remove(archived[0])
            active['discarded'] += len(archived[0]['turns'])
        elif len(active['turns']) > 1:
            _evict_turn(active)
        else:
            raise QuotaError('one turn exceeds the owner quota')


def _migrated(history, now):
    """Old global history pairs as turns: new ids, playback never confirmed."""
    turns = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, dict):
            continue
        q, a = clean(item.get('q')), clean(item.get('a'))
        if not q or not a:
            continue
        at = item.get('at') if _count(item.get('at')) else now
        turns.append(dict(id=new_id(), q=q[:MAX_TEXT], a=a[:MAX_TEXT],
                          p=item.get('p') if item.get('p') in PERSONAS else 'servitor',
                          at=at, delivery='pending', context_ids=[]))
    return turns[-MAX_TURNS:]


def legacy_owner(profile_ids):
    """Who may inherit the old global history: DEVICE_OPERATOR without voice
    profiles, the single profile with one, nobody (None) with several."""
    ids = list(profile_ids)
    if not ids:
        return DEVICE_OPERATOR
    return ids[0] if len(ids) == 1 else None


# --- voice commands ---------------------------------------------------------------

_NEW = re.compile(r'^(?:bitte )?(?:neues (?:gespräch|thema)|(?:lass uns |wir )?(?:ein )?neues '
                  r'(?:gespräch|thema) (?:anfangen|beginnen|starten)|fang(?:e)? (?:ein )?neues '
                  r'(?:gespräch|thema) an)$')
_FORGET_THIS = re.compile(r'^(?:bitte )?(?:vergiss|lösche) (?:bitte )?(?:dieses|das|unser) '
                          r'(?:gespräch|thema|gesprächsverlauf)$|^(?:bitte )?lösche (?:diesen|den) '
                          r'gesprächsverlauf$')
_FORGET_ALL = re.compile(r'^(?:bitte )?(?:vergiss|lösche) (?:bitte )?alle (?:unsere |meine )?'
                         r'gespräche$')
_RESUME = re.compile(r'^(?:bitte )?(?:setze?|mach(?:e)?) (?:unser(?:e)?|das) '
                     r'(?:gespräch|unterhaltung) (?:fort|weiter)$|^(?:lass uns )?weiter(?:machen| reden)'
                     r' wo wir (?:aufgehört|stehen geblieben) (?:haben|sind)$')
_TOPIC = re.compile(r'^(?:was war|worüber (?:haben|hatten) wir (?:gesprochen|geredet)|was ist) '
                    r'(?:unser )?(?:thema)?$|^was war unser thema$|^worum ging es (?:gerade|zuletzt)$')
_LIST = re.compile(r'\b(welche gespräche (?:hast du )?(?:gespeichert|kennst du)|'
                   r'liste (?:unsere |meine )?gespräche)\b')
_NAME = re.compile(r'^(?:bitte )?(?:nenne|nenn|benenne) (?:dieses|das|unser) gespräch '
                   r'(?:in )?(.+)$|^(?:bitte )?speichere (?:das|dieses gespräch) als (.+)$')
_OPEN = re.compile(r'^(?:bitte )?(?:öffne|lade) (?:das )?gespräch (.+)$')
_OPEN_SHORT = re.compile(r'^(?:bitte )?(?:öffne|lade) (?:das |die |den )?(.+)$')


def command(text, titles=()):
    """(op, argument) for conversation commands, else None. Checked before
    memory.command, so "Vergiss dieses Gespräch" is not a fact search.
    ``titles``: known thread names of the speaker; only they make the short
    form "Öffne Reiseplanung" a command ("Öffne das Fenster" stays a device
    command)."""
    text = clean(str(text)).lower().strip(' .!?')
    if not text:
        return None
    if _NEW.match(text):
        return ('new_thread', '')
    if _FORGET_ALL.match(text):
        return ('forget_all', '')
    if _FORGET_THIS.match(text):
        return ('forget_thread', '')
    if _RESUME.match(text):
        return ('resume', '')
    if _TOPIC.match(text):
        return ('topic', '')
    if _LIST.search(text):
        return ('list_threads', '')
    match = _NAME.match(text)
    if match:
        return ('name_thread', (match.group(1) or match.group(2)).strip())
    match = _OPEN.match(text)
    if match:
        return ('open_thread', match.group(1).strip())
    match = _OPEN_SHORT.match(text)
    if match and any(match.group(1).strip() == t.casefold() for t in titles):
        return ('open_thread', match.group(1).strip())
    return None


def _when(at, now):
    hours = max(0, now - at) // 3600
    if hours < 1:
        return 'eben'
    if hours < 24:
        return f'vor {hours} Stunde' + ('' if hours == 1 else 'n')
    days = hours // 24
    return f'vor {days} Tag' + ('' if days == 1 else 'en')


def reply(op, result, now=0):
    """Spoken sentence for a command result (the store's return value).
    None from the store means the stick is absent or failed."""
    if result is None and op not in ('resume', 'open_thread', 'topic', 'name_thread'):
        return "Gedächtniskern nicht verfügbar. Nichts geändert."
    if op == 'new_thread':
        if result == 'empty':
            return "Das Gespräch ist schon leer. Neues Thema, bitte."
        if result == 'dropped_oldest':
            return ("Neues Gespräch begonnen. Das bisherige ist archiviert, das älteste "
                    "Archiv wurde dafür entfernt.")
        return "Neues Gespräch begonnen. Das bisherige ist archiviert."
    if op == 'forget_thread':
        return "Dieses Gespräch ist gelöscht." if result else "Dieses Gespräch war noch leer."
    if op == 'forget_all':
        return ("Alle unsere Gespräche sind gelöscht. Gespeicherte Fakten bleiben, die löscht "
                "„Vergiss …“." if result else "Es waren keine Gespräche gespeichert.")
    if op == 'forget_matching':
        direct, extra = result
        if not direct:
            return "Dazu habe ich in unseren Gesprächen nichts gefunden. Nichts gelöscht."
        text = f"{direct} Gesprächsrunde{'n' if direct != 1 else ''} gelöscht"
        if extra:
            text += f", dazu {extra} darauf aufbauende"
        return text + ". Gesucht habe ich nach den Wörtern, nicht nach jeder Umschreibung."
    if op in ('resume', 'open_thread'):
        if result == 'ambiguous':
            return "Mehrere Gespräche passen. Welches genau?"
        if result is None:
            return ("Kein früheres Gespräch gefunden." if op == 'resume'
                    else "Ein Gespräch mit diesem Namen kenne ich nicht.")
        return "Wir machen weiter. " + _topic_text(result, now)
    if op == 'topic':
        return _topic_text(result, now) if result else "Wir haben noch kein Thema."
    if op == 'name_thread':
        if result == 'duplicate':
            return "Ein anderes Gespräch heißt schon so. Bitte einen anderen Namen."
        return f"Gespräch heißt jetzt {result}." if result else "Welcher Name?"
    if op == 'list_threads':
        if not result or not any(t['turns'] for t in result):
            return "Keine Gespräche gespeichert."
        parts = []
        for t in result:
            if not t['turns'] and not t['active']:
                continue
            name = t['title'] or ('das aktuelle' if t['active'] else 'ein unbenanntes')
            parts.append(f"{name}, {_when(t['updated_at'], now)}"
                         + (", aktiv" if t['active'] and t['title'] else ""))
        return f"{len(parts)} Gespräch{'e' if len(parts) != 1 else ''}: " + '; '.join(parts) + "."
    raise ValueError(op)


def _topic_text(info, now):
    if not info['turns']:
        return "Das Gespräch ist noch leer."
    head = f"Thema {info['title']}" if info['title'] else "Begonnen hat es mit"
    text = f"{head}: {info['first']}" if not info['title'] else head
    if info['slots']:
        text += ". Stand: " + ', '.join(info['slots'].values())
    if now - info['updated_at'] > IDLE_SECONDS:
        text += f". Das war {_when(info['updated_at'], now)}"
    return text.rstrip('.') + "."
