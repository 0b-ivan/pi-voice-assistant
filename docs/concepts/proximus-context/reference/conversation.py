"""Original offline reference, not imported by the voice runtime.

Patterns: owner/thread scope, source-backed working state, bounded retrieval,
optimistic commit and independent playback status. No third-party code copied.
Authenticate BEFORE selecting an owner. The adapter needs a mount-generation
provider; it does not implement authentication or kernel mount verification.
"""
import base64
import copy
import json
import re
import uuid

VERSION = 2
MAX_TURNS = 48
MAX_TEXT = 2048
MAX_SLOTS = 8
MAX_QUOTE = 400
MAX_IDS = 128
OWNER_BYTES = 128 * 1024
MAX_OWNERS = 5
MAX_THREADS = 3
IDLE_SECONDS = 12 * 3600
CHAR_LIMIT = 6000
HEADER_LIMIT = 12000


class StaleTurn(ValueError):
    pass


class BudgetError(ValueError):
    pass


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def clean(value):
    return re.sub(r'\s+', ' ', value).strip() if isinstance(value, str) else ''


def ident(value):
    return isinstance(value, str) and 0 < len(value) <= 80


def timestamp(now):
    if type(now) is not int or now < 0:
        raise ValueError('invalid timestamp')
    return now


def fresh(owner, now):
    if not ident(owner):
        raise ValueError('stable owner id required')
    return dict(version=VERSION, id=uuid.uuid4().hex, owner=owner, updated_at=timestamp(now),
                epoch=0, revision=0, discarded=0, turns=[], slots={}, seen=[])


def validate(state):
    if not isinstance(state, dict) or type(state.get('version')) is not int or state['version'] != VERSION:
        raise ValueError('unsupported conversation version')
    if not ident(state.get('owner')) or not ident(state.get('id')):
        raise ValueError('invalid scope')
    for key in ('updated_at', 'epoch', 'revision', 'discarded'):
        timestamp(state.get(key))
    for key, maximum in (('turns', MAX_TURNS), ('seen', MAX_IDS)):
        if not isinstance(state.get(key), list) or len(state[key]) > maximum:
            raise ValueError('invalid collection')
    if any(not ident(i) for i in state['seen']) or len(set(state['seen'])) != len(state['seen']):
        raise ValueError('invalid receipt ids')
    ids = set()
    for t in state['turns']:
        if not isinstance(t, dict) or not ident(t.get('id')) or t['id'] in ids:
            raise ValueError('invalid turn id')
        ids.add(t['id'])
        timestamp(t.get('at'))
        if t.get('p') not in ('servitor', 'mensch') or t.get('delivery') not in ('pending', 'played', 'interrupted', 'failed'):
            raise ValueError('invalid turn metadata')
        for key in ('q', 'a'):
            if not isinstance(t.get(key), str) or not 0 < len(t[key]) <= MAX_TEXT:
                raise ValueError('invalid turn text')
        refs = t.get('context_ids')
        if not isinstance(refs, list) or len(refs) > MAX_TURNS or any(not ident(i) for i in refs):
            raise ValueError('invalid dependencies')
    if not isinstance(state.get('slots'), dict) or len(state['slots']) > MAX_SLOTS:
        raise ValueError('invalid working state')
    turns = {t['id']: t for t in state['turns']}
    for key, slot in state['slots'].items():
        _check_slot(key, slot, turns)
    return copy.deepcopy(state)


def _check_slot(key, slot, turns):
    if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', key) or not isinstance(slot, dict):
        raise ValueError('invalid slot')
    if not ident(slot.get('source_id')) or set(slot) != {'source_id', 'start', 'end', 'quote'}:
        raise ValueError('invalid source selector')
    source = turns.get(slot['source_id'])
    start, end = slot.get('start'), slot.get('end')
    if (source is None or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= len(source['q']) or end - start > MAX_QUOTE):
        raise ValueError('unbacked slot')
    if slot.get('quote') != source['q'][start:end]:
        raise ValueError('quote differs from user source')


def authorize(state, owner):
    if state['owner'] != owner:
        raise PermissionError('conversation owner mismatch')


def snapshot(state, owner, stick):
    state = validate(state)
    authorize(state, owner)
    if not ident(stick):
        raise ValueError('mount generation required')
    return dict(owner=owner, id=state['id'], epoch=state['epoch'], revision=state['revision'], stick=stick)


def _check_expected(state, owner, expected, stick, *, duplicate=False):
    actual = snapshot(state, owner, stick)
    keys = ('owner', 'id', 'epoch', 'stick') if duplicate else tuple(actual)
    if not isinstance(expected, dict) or any(expected.get(k) != actual[k] for k in keys):
        raise StaleTurn('turn snapshot no longer valid')


def _evict_turn(state):
    pinned = {s['source_id'] for s in state['slots'].values()}
    # Prefer removing unpinned evidence. Quota can still force source removal.
    index = next((i for i, t in enumerate(state['turns'][:-1]) if t['id'] not in pinned), 0)
    state['turns'].pop(index)
    state['discarded'] += 1


def _prune(state):
    while len(state['turns']) > MAX_TURNS:
        _evict_turn(state)
    ids = {t['id'] for t in state['turns']}
    state['slots'] = {k: s for k, s in state['slots'].items() if s['source_id'] in ids}
    state['seen'] = state['seen'][-MAX_IDS:]
    return state


def commit_turn(state, *, expected, stick, turn_id, question, answer, persona, owner, now,
                context_ids=(), updates=None):
    state = validate(state)
    authorize(state, owner)
    duplicate = turn_id in state['seen']
    _check_expected(state, owner, expected, stick, duplicate=duplicate)
    if duplicate:
        return state
    if not ident(turn_id) or persona not in ('servitor', 'mensch'):
        raise ValueError('invalid turn metadata')
    q, a = clean(question), clean(answer)
    if not q or not a or len(q) > MAX_TEXT or len(a) > MAX_TEXT:
        raise ValueError('empty/oversized text: route explicitly, do not silently truncate')
    ids = {t['id'] for t in state['turns']}
    if len(context_ids) > MAX_TURNS or any(i not in ids for i in context_ids):
        raise ValueError('unknown dependency')
    state['turns'].append(dict(id=turn_id, q=q, a=a, p=persona, at=timestamp(now),
                               delivery='pending', context_ids=list(dict.fromkeys(context_ids))))
    state['seen'].append(turn_id)
    # Optional extraction comes from the SAME reply call. Caller handles invalid
    # updates separately; the core fails transactionally rather than saving guesses.
    turns = {t['id']: t for t in state['turns']}
    if updates is not None:
        if not isinstance(updates, dict) or len(updates) > MAX_SLOTS:
            raise ValueError('invalid update packet')
        for key, selector in updates.items():
            if not isinstance(selector, dict):
                raise ValueError('invalid selector')
            if not ident(selector.get('source_id')) or set(selector) != {'source_id', 'start', 'end'}:
                raise ValueError('invalid source selector')
            source = turns.get(selector['source_id'])
            if source is None or selector.get('source_id') not in set(context_ids) | {turn_id}:
                raise ValueError('source was not exported for this turn')
            slot = dict(selector)
            start, end = slot.get('start'), slot.get('end')
            if type(start) is not int or type(end) is not int:
                raise ValueError('invalid selector range')
            slot['quote'] = source['q'][start:end]
            _check_slot(key, slot, turns)
            before = state['slots'].get(key)
            order = {t['id']: i for i, t in enumerate(state['turns'])}
            if before and order[slot['source_id']] < order[before['source_id']]:
                raise ValueError('older source cannot overwrite newer working state')
            state['slots'][key] = slot  # replaces current value, earlier turn remains history
        if len(state['slots']) > MAX_SLOTS:
            raise ValueError('too many slots')
    state['updated_at'] = now
    state['revision'] += 1
    return _prune(state)


def set_delivery(state, owner, turn_id, status):
    state = validate(state)
    authorize(state, owner)
    if status not in ('played', 'interrupted', 'failed'):
        raise ValueError('invalid delivery')
    for turn in state['turns']:
        if turn['id'] == turn_id:
            if turn['delivery'] not in ('pending', status):
                raise StaleTurn('terminal delivery cannot change')
            turn['delivery'] = status
            return state  # separate status update, not another generated turn
    raise StaleTurn('turn was deleted/evicted')


def repeat_answer(state, owner):
    state = validate(state)
    authorize(state, owner)
    return next((t['a'] for t in reversed(state['turns']) if t['delivery'] == 'played'), None)


def tokens(text):
    stop = {'ich', 'der', 'die', 'das', 'und', 'ist', 'mit', 'bitte', 'was', 'wie', 'mir', 'ein', 'eine', 'nach', 'dann'}
    return {w for w in re.findall(r'\w+', text.casefold()) if len(w) > 2 and w not in stop}


def context(state, owner, query, now, *, resume=False):
    state = validate(state)
    authorize(state, owner)
    # Expiry changes READ policy only. It never resets or deletes the stored thread.
    active = resume or 0 <= timestamp(now) - state['updated_at'] <= IDLE_SECONDS
    if not active:
        return dict(history=[], conversation=dict(version=VERSION, stale=True, slots={}, evidence=[]))
    recent = state['turns'][-4:]
    recent_ids = {t['id'] for t in recent}
    older = state['turns'][:-4]
    query_words = tokens(query)
    ranked = sorted(older, key=lambda t: (len(query_words & tokens(t['q'] + ' ' + t['a'])), t['at']), reverse=True)
    relevant = [t for t in ranked if query_words & tokens(t['q'] + ' ' + t['a'])][:2]
    # First request remains a fallback anchor only while explicit thread is active.
    anchors = state['turns'][:1]
    chosen = {t['id']: t for t in anchors + relevant if t['id'] not in recent_ids}
    evidence = [dict(id=t['id'], q=t['q'], at=t['at']) for t in chosen.values()]
    return dict(history=[dict(q=t['q'], a=t['a'], p=t['p'], id=t['id'], delivery=t['delivery']) for t in recent],
                conversation=dict(version=VERSION, id=state['id'], revision=state['revision'],
                                  stale=False, slots=copy.deepcopy(state['slots']), evidence=evidence))


def forget_ids(state, owner, ids):
    state = validate(state)
    authorize(state, owner)
    remove = set(ids)
    if not remove or not remove <= {t['id'] for t in state['turns']}:
        raise ValueError('select existing turn ids')
    # All actually exported sources are conservative dependencies; deletion may
    # remove follow-ups as well. Other independent turns/threads remain intact.
    while True:
        extra = {t['id'] for t in state['turns'] if remove.intersection(t['context_ids'])}
        if extra <= remove:
            break
        remove |= extra
    state['turns'] = [t for t in state['turns'] if t['id'] not in remove]
    state['slots'] = {k: v for k, v in state['slots'].items() if v['source_id'] not in remove}
    state['epoch'] += 1  # stale retries may never recreate forgotten data
    state['revision'] += 1
    return state


def matching_ids(state, owner, query):
    state = validate(state)
    authorize(state, owner)
    words = tokens(query)
    if not words:
        return []
    return [t['id'] for t in state['turns'] if words <= tokens(t['q'] + ' ' + t['a'])]


def pack(base, state, owner, query, now, *, model_fits, resume=False,
         char_limit=CHAR_LIMIT, header_limit=HEADER_LIMIT):
    """model_fits assembles/counts the FULL model request incl. output reserve.

    base must already be owner/guest filtered and relevance-ranked (best first).
    On overflow remove optional evidence, old pairs, low-ranked facts, then cards.
    Never silently remove the last pair. Caller handles BudgetError explicitly.
    """
    payload = copy.deepcopy(base)
    payload.update(context(state, owner, query, now, resume=resume))
    while True:
        raw = compact(payload)
        encoded = base64.b64encode(raw.encode('utf-8')).decode('ascii')
        if len(raw) <= char_limit and len(encoded) <= header_limit and model_fits(payload):
            conv = payload['conversation']
            source_ids = {h['id'] for h in payload['history']} | {e['id'] for e in conv['evidence']} | {s['source_id'] for s in conv['slots'].values()}
            return payload, encoded, sorted(source_ids)
        conv = payload['conversation']
        if conv['evidence']:
            conv['evidence'].pop()  # first anchor before optional old matches
        elif len(payload['history']) > 1:
            payload['history'].pop(0)
        elif payload.get('facts'):
            payload['facts'].pop()  # ranked best -> worst, never insertion-order priority
        elif conv['slots']:
            conv['slots'].pop(next(reversed(conv['slots'])))
        else:
            raise BudgetError('mandatory data / last pair / full model request exceed budget')


class ConversationAdapter:
    """Owner index and bounded threads inside MemoryCore's existing JSON file.

    Calls belong to ONE serialized writer. generation() must return filesystem
    UUID + fresh mount nonce, or None if absent; never the label alone.
    """
    def __init__(self, core, generation):
        self.core, self.generation = core, generation

    def _book(self, data):
        book = data.setdefault('conversations', dict(version=VERSION, owners={}))
        if not isinstance(book, dict) or book.get('version') != VERSION or not isinstance(book.get('owners'), dict):
            raise ValueError('unsupported owner index')
        if len(book['owners']) > MAX_OWNERS:
            raise ValueError('owner index exceeds bound')
        return book

    def ensure(self, owner, now):
        def update(data):
            book = self._book(data)
            if owner in book['owners']:
                return False
            if len(book['owners']) == MAX_OWNERS:
                raise BudgetError('owner limit; no eviction of another person')
            state = fresh(owner, now)
            book['owners'][owner] = dict(active=state['id'], threads=[state])
        return self.core._change(update)

    def read(self, owner):
        data = self.core.load()
        if data is None or not self.generation():
            return None
        book = self._book(copy.deepcopy(data))
        own = book['owners'].get(owner)
        if own is None:
            return None
        return next(validate(t) for t in own['threads'] if t['id'] == own['active'])

    def append(self, owner, now, **turn):
        current_mount = self.generation()
        if current_mount is None:
            return False
        def update(data):
            book = self._book(data)
            own = book['owners'].get(owner)
            if own is None:
                raise StaleTurn('owner must be initialized before request')
            for i, state in enumerate(own['threads']):
                if state['id'] == own['active']:
                    updated = commit_turn(state, owner=owner, now=now, stick=current_mount, **turn)
                    if updated == state:
                        return False
                    own['threads'][i] = updated
                    self._quota(own)
                    return
            raise StaleTurn('missing active thread')
        return self.core._change(update)

    @staticmethod
    def _quota(own):
        # Prune only this owner's material, with visible discard counts.
        active = next(t for t in own['threads'] if t['id'] == own['active'])
        while len(compact(own).encode('utf-8')) > OWNER_BYTES:
            inactive = next((t for t in own['threads'] if t['id'] != own['active']), None)
            if inactive:
                own['threads'].remove(inactive)
            elif len(active['turns']) > 1:
                _evict_turn(active)
                _prune(active)
            else:
                raise BudgetError('single turn exceeds owner quota')

    def new_thread(self, owner, now, *, delete=False):
        def update(data):
            own = self._book(data)['owners'].get(owner)
            if own is None:
                raise StaleTurn('owner not initialized')
            new = fresh(owner, now)
            # Explicit new thread archives; explicit deletion keeps no current copy.
            if delete:
                own['threads'] = [t for t in own['threads'] if t['id'] != own['active']]
            own['threads'] = (own['threads'] + [new])[-MAX_THREADS:]
            own['active'] = new['id']
            self._quota(own)
            data['history'] = []  # legacy global content cannot reappear
        return self.core._change(update)
