"""Offline reference, NOT imported by the runtime.

Storage adapter deliberately reuses MemoryCore's atomic transaction. Integration
must serialize all memory writers and authenticate before exporting or saving.
"""
import base64
import copy
import json
import re
import uuid

VERSION = 1
MAX_TURNS = 6
MAX_TEXT = 800
MAX_NOTES = 6
MAX_NOTE = 240
MAX_IDS = 32
IDLE_SECONDS = 12 * 3600
CHAR_LIMIT = 6000
HEADER_LIMIT = 12000


def text(value, limit=MAX_TEXT):
    return re.sub(r'\s+', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def fresh(owner, now):
    if not text(owner, 80):
        raise ValueError('owner required')
    return dict(version=VERSION, id=uuid.uuid4().hex, owner=text(owner, 80),
                updated_at=now, revision=0, turns=[], notes=[], seen=[])


def validate(state):
    if not isinstance(state, dict) or state.get('version') != VERSION:
        raise ValueError('unsupported conversation version')
    if not isinstance(state.get('owner'), str) or not state['owner']:
        raise ValueError('invalid owner')
    for key in ('id',):
        if not isinstance(state.get(key), str) or not state[key] or len(state[key]) > 80:
            raise ValueError('invalid id')
    for key in ('updated_at', 'revision'):
        if type(state.get(key)) is not int or state[key] < 0:
            raise ValueError('invalid counter')
    for key, limit in (('turns', MAX_TURNS), ('notes', MAX_NOTES), ('seen', MAX_IDS)):
        if not isinstance(state.get(key), list) or len(state[key]) > limit:
            raise ValueError('invalid collection')
    if any(not isinstance(i, str) or not i or len(i) > 80 for i in state['seen']):
        raise ValueError('invalid seen id')
    for item in state['turns'] + state['notes']:
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or not item['id'] or len(item['id']) > 80):
            raise ValueError('invalid entry')
        if type(item.get('at')) is not int or item['at'] < 0:
            raise ValueError('invalid timestamp')
    for turn in state['turns']:
        if turn.get('p') not in ('servitor', 'mensch'):
            raise ValueError('invalid persona')
        for key in ('q', 'a'):
            if not isinstance(turn.get(key), str) or not turn[key] or len(turn[key]) > MAX_TEXT:
                raise ValueError('invalid turn text')
    for note in state['notes']:
        if not isinstance(note.get('q'), str) or not note['q'] or len(note['q']) > MAX_NOTE:
            raise ValueError('invalid note')
    return copy.deepcopy(state)


def commit_turn(state, *, turn_id, question, answer, persona, owner, now):
    state = validate(state)
    if state['owner'] != owner:
        raise PermissionError('speaker does not own conversation')
    if not isinstance(turn_id, str) or not turn_id or len(turn_id) > 80:
        raise ValueError('invalid turn id')
    if turn_id in state['seen']:
        return state  # one logical turn despite retransmission
    if persona not in ('servitor', 'mensch') or type(now) is not int or now < 0:
        raise ValueError('invalid metadata')
    q, a = text(question), text(answer)
    if not q or not a:
        raise ValueError('empty turn')
    # Call only after terminal success; summary is verbatim user excerpts,
    # not LLM-generated facts and never standing instructions.
    state['turns'].append(dict(id=turn_id, q=q, a=a, p=persona, at=now))
    if len(state['turns']) > MAX_TURNS:
        old = state['turns'].pop(0)
        state['notes'] = (state['notes'] + [dict(id=old['id'], q=old['q'][:MAX_NOTE],
                                               at=old['at'])])[-MAX_NOTES:]
    state['seen'] = (state['seen'] + [turn_id])[-MAX_IDS:]
    state['updated_at'] = now
    state['revision'] += 1
    return state


def reset(state, owner, now):
    state = validate(state)
    if state['owner'] != owner:
        raise PermissionError('speaker does not own conversation')
    return fresh(owner, now)


def forget_context(state, owner, now):
    # Conservative MVP: clear all conversation material so paraphrases cannot
    # resurrect forgotten facts. MemoryCore.forget must also remove facts.
    return reset(state, owner, now)


def available(state, owner, now, *, resume=False):
    state = validate(state)
    return state['owner'] == owner and (resume or 0 <= now - state['updated_at'] <= IDLE_SECONDS)


def pack(base, state, owner, now, *, resume=False, char_limit=CHAR_LIMIT,
         header_limit=HEADER_LIMIT):
    """Bound final JSON and base64, including Unicode and voiceprints.

    On overflow remove lowest-priority data; never return partial turn pairs.
    Raises if non-droppable base data alone exceeds the transport limit.
    PRECONDITION: base has already passed guest/owner filtering; this function
    filters conversation only, not existing global facts/directives.
    """
    payload = copy.deepcopy(base)
    payload['history'] = []
    payload.pop('conversation', None)
    if available(state, owner, now, resume=resume):
        payload['conversation'] = dict(version=VERSION, id=state['id'],
            revision=state['revision'], notes=copy.deepcopy(state['notes']))
        payload['history'] = [dict(q=t['q'], a=t['a'], p=t['p']) for t in state['turns'][-4:]]
    while True:
        raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        encoded = base64.b64encode(raw.encode('utf-8')).decode('ascii')
        if len(raw) <= char_limit and len(encoded) <= header_limit:
            return payload, encoded
        if payload.get('facts'):
            payload['facts'].pop(0)  # base facts oldest -> newest
        elif payload.get('conversation', {}).get('notes'):
            payload['conversation']['notes'].pop(0)
        elif len(payload['history']) > 1:
            payload['history'].pop(0)
        elif payload['history']:
            payload['history'].pop()
        elif 'conversation' in payload:
            del payload['conversation']
        else:
            raise ValueError('directives/voiceprints exceed budget')


class ConversationAdapter:
    """Prototype storage bridge; no second file, writer, or server database."""
    def __init__(self, core):
        self.core = core

    def append(self, owner, now, **turn):
        def update(data):
            state = data.get('conversation') or fresh(owner, now)
            if state['owner'] != owner:
                raise PermissionError('speaker does not own conversation')
            if not available(state, owner, now):
                state = reset(state, owner, now)
            updated = commit_turn(state, owner=owner, now=now, **turn)
            if updated == state:
                return False
            data['conversation'] = updated
        return self.core._change(update)

    def clear(self, owner, now):
        def update(data):
            state = data.get('conversation')
            if state:
                data['conversation'] = reset(state, owner, now)
            data['history'] = []  # legacy context must not restore old contents
        return self.core._change(update)
