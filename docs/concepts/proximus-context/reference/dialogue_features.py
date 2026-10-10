"""Six optional dialogue features for the offline reference, never runtime code.

The caller already authenticates the owner, supplies trusted capture time/zone,
and serializes writes. Natural-language classification and real audio are NOT
implemented here. Ambiguity leads to clarification rather than guessed data.
"""
import copy
from datetime import datetime, timedelta, timezone
import re
import uuid
from zoneinfo import ZoneInfo

import conversation as c

MAX_QUESTIONS = 3
MAX_TIME_REFS = 4
HOURS = dict(zip(('null', 'eins', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun', 'zehn', 'elf', 'zwölf'), range(13)))
FACT_KEYS = {'name', 'home_city', 'occupation'}


class ClarificationNeeded(ValueError):
    pass


class RecordingPaused(PermissionError):
    pass


def command(utterance, thread_names=()):
    """Run before memory.command; quoted examples/general text are not commands."""
    raw = c.clean(utterance).strip(' .!?')
    lower = raw.casefold()
    exact = {
        'pausiere das gedächtnis': ('pause', 'owner'),
        'pausiere gedächtnis': ('pause', 'owner'),
        'speichere dieses gespräch nicht': ('pause', 'thread'),
        'speichere das gespräch nicht': ('pause', 'thread'),
        'speichere wieder': ('record', 'owner'),
        'aktiviere das gedächtnis wieder': ('record', 'owner'),
        'welche gespräche hast du gespeichert': ('list_threads', ''),
        'welche fragen sind noch offen': ('list_questions', ''),
    }
    if lower in exact:
        return exact[lower]
    for pattern, op in [
        (r'(?:nenne dieses gespräch|speichere (?:dieses gespräch|das) als) (.+)', 'name_thread'),
        (r'öffne das gespräch (.+)', 'open_thread'),
        (r'setze das gespräch (.+) fort', 'open_thread'),
        (r'korrigiere meinen wohnort (?:auf|zu) (.+)', 'correct_home_city'),
        (r'woher weißt du (?:meinen|den) wohnort', 'explain_home_city'),
        (r'woher weißt du (?:mein|das) reiseziel', 'explain_destination'),
    ]:
        found = re.fullmatch(pattern, raw, re.IGNORECASE)
        if found:
            return op, found.group(1) if found.lastindex else ''
    if lower.startswith('öffne '):
        wanted = raw[6:]
        if any(c.clean(n).casefold() == wanted.casefold() for n in thread_names):
            return 'open_thread', wanted
    return None


def _source(state, source_id):
    return next((t for t in state['turns'] if t['id'] == source_id), None)


def validate_questions(state):
    questions = state.get('questions', [])
    if not isinstance(questions, list) or len(questions) > MAX_QUESTIONS:
        raise ValueError('invalid pending-question collection')
    seen = set()
    for item in questions:
        if not isinstance(item, dict) or not c.ident(item.get('id')) or item['id'] in seen:
            raise ValueError('invalid pending question')
        seen.add(item['id'])
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', str(item.get('field', ''))):
            raise ValueError('invalid pending field')
        source = _source(state, item.get('source_id'))
        start, end = item.get('start'), item.get('end')
        if (source is None or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(source['a']) or end - start > c.MAX_QUOTE
                or item.get('quote') != source['a'][start:end]):
            raise ValueError('question is not backed by assistant text')
        if item.get('status') not in ('open', 'answered', 'dismissed'):
            raise ValueError('invalid question status')
        if item['status'] == 'answered' and _source(state, item.get('answer_id')) is None:
            raise ValueError('answer source missing')


def apply_questions(state, *, open_items=(), resolve_items=(), dismiss_items=(), current_id):
    """Trusted structured proposal from same response; ranges are validated.

    Resolving a question establishes a dialogue link, not a durable personal fact.
    Semantic classification of whether it answers the field remains a model test.
    """
    state = copy.deepcopy(state)
    if len(open_items) > MAX_QUESTIONS or len(resolve_items) > MAX_QUESTIONS or len(dismiss_items) > MAX_QUESTIONS:
        raise ValueError('too many question updates')
    questions = state.setdefault('questions', [])
    for proposal in open_items:
        source = _source(state, current_id)
        if not isinstance(proposal, dict) or source is None:
            raise ValueError('invalid question update')
        start, end = proposal.get('start'), proposal.get('end')
        if type(start) is not int or type(end) is not int:
            raise ValueError('invalid question range')
        field = proposal.get('field')
        item = dict(id=uuid.uuid4().hex, field=field, source_id=current_id,
                    start=start, end=end, quote=source['a'][start:end], status='open', answer_id=None)
        # Do not silently replace or discard a still-open unrelated question.
        if any(q['field'] == field and q['status'] == 'open' for q in questions):
            raise ClarificationNeeded('question for field already open')
        questions[:] = [q for q in questions if q['status'] == 'open']
        questions.append(item)
    for question_id in resolve_items:
        found = next((q for q in questions if q['id'] == question_id and q['status'] == 'open'), None)
        if found is None or found['source_id'] == current_id or _source(state, current_id) is None:
            raise ValueError('resolution needs an existing question and new user turn')
        found.update(status='answered', answer_id=current_id)
    for question_id in dismiss_items:
        found = next((q for q in questions if q['id'] == question_id and q['status'] == 'open'), None)
        if found is None:
            raise ValueError('unknown open question to dismiss')
        found.update(status='dismissed', answer_id=None)
    validate_questions(state)
    return state


def pending_context(state):
    validate_questions(state)
    return [dict(copy.deepcopy(q), delivery=_source(state, q['source_id'])['delivery'])
            for q in state.get('questions', []) if q['status'] == 'open']


def resolve_time(expression, captured_at, zone_name):
    """Narrow MVP grammar: heute/morgen/übermorgen [um HH[:MM] [Uhr]].

    No full natural-language date parser or scheduling side effects. Ambiguous or
    missing civil time at DST transitions requires explicit clarification.
    """
    c.timestamp(captured_at)
    zone = ZoneInfo(zone_name)  # missing timezone data is an error, never host fallback
    phrase = c.clean(expression)
    normalized = phrase.casefold()
    for word, number in HOURS.items():
        normalized = re.sub(r'(?<=um )' + word + r'\b', str(number), normalized)
    match = re.fullmatch(r'(heute|morgen|übermorgen)(?: um (\d{1,2})(?::(\d{2}))?(?: uhr)?)?', normalized)
    if match is None:
        raise ClarificationNeeded('unsupported time phrase')
    captured = datetime.fromtimestamp(captured_at, timezone.utc).astimezone(zone)
    date = captured.date() + timedelta(days={'heute': 0, 'morgen': 1, 'übermorgen': 2}[match[1]])
    result = dict(raw=phrase, captured_at=captured_at, timezone=zone_name, date=date.isoformat())
    if match[2] is None:
        return dict(result, precision='date', local=None, utc=None)
    hour, minute = int(match[2]), int(match[3] or 0)
    if hour > 23 or minute > 59:
        raise ClarificationNeeded('invalid clock time')
    naive = datetime(date.year, date.month, date.day, hour, minute)
    candidates = {}
    for fold in (0, 1):
        local = naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[utc.isoformat()] = local.isoformat()
    if len(candidates) != 1:
        raise ClarificationNeeded('nonexistent/ambiguous local time')
    utc, local = next(iter(candidates.items()))
    return dict(result, precision='minute', utc=utc, local=local)


def make_time_ref(question, start, end, captured_at, zone_name):
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(question):
        raise ValueError('invalid time source range')
    return dict(start=start, end=end, **resolve_time(question[start:end], captured_at, zone_name))


def validate_times(refs, question):
    if not isinstance(refs, list) or len(refs) > MAX_TIME_REFS:
        raise ValueError('invalid time-reference collection')
    for item in refs:
        if not isinstance(item, dict):
            raise ValueError('invalid time reference')
        expected = make_time_ref(question, item.get('start'), item.get('end'), item.get('captured_at'), item.get('timezone'))
        if item != expected:
            raise ValueError('resolved time changed or differs from source')


class FeatureAdapter(c.ConversationAdapter):
    """Atomic metadata/fact actions; owner is already verified by the caller."""
    def request_snapshot(self, owner):
        state = self.read(owner)
        if state is None:
            return None
        own = self._book(copy.deepcopy(self.core.load()))['owners'][owner]
        return dict(c.snapshot(state, owner, self.generation()), write_epoch=own.get('write_epoch', 0))

    def set_recording(self, owner, *, enabled, scope='owner'):
        if type(enabled) is not bool or scope not in ('owner', 'thread'):
            raise ValueError('invalid recording policy')
        if not self.generation():
            return False
        def update(data):
            own = self._book(data)['owners'][owner]
            if scope == 'thread' and not enabled:
                own['private_thread'] = own['active']
            else:
                own['recording'] = enabled
                if enabled:
                    own['private_thread'] = None
            own['write_epoch'] = own.get('write_epoch', 0) + 1
            self._quota(own)
        return self.core._change(update)

    @staticmethod
    def _writable(own):
        if not own.get('recording', True) or own.get('private_thread') == own['active']:
            raise RecordingPaused('content storage is paused')

    def threads(self, owner):
        data = self.core.load()
        if data is None or not self.generation():
            return []
        own = self._book(copy.deepcopy(data))['owners'].get(owner)
        if own is None:
            return []
        return [dict(id=t['id'], title=t.get('title') or 'Unbenanntes Gespräch',
                     active=t['id'] == own['active'], updated_at=t['updated_at'], turns=len(t['turns']))
                for t in own['threads']]

    def name_thread(self, owner, title):
        title = c.clean(title)
        if not 0 < len(title) <= 60:
            raise ValueError('title must be 1..60 characters')
        if not self.generation():
            return False
        def update(data):
            own = self._book(data)['owners'][owner]
            self._writable(own)
            if any(t['id'] != own['active'] and t.get('title', '').casefold() == title.casefold() for t in own['threads']):
                raise ClarificationNeeded('title already used by another thread')
            next(t for t in own['threads'] if t['id'] == own['active'])['title'] = title
            self._quota(own)
        return self.core._change(update)

    def open_thread(self, owner, name_or_id):
        if not self.generation():
            return False
        def update(data):
            own = self._book(data)['owners'][owner]
            wanted = c.clean(name_or_id).casefold()
            if not wanted:
                raise ClarificationNeeded('conversation name required')
            exact = [t for t in own['threads'] if t['id'] == name_or_id or t.get('title', '').casefold() == wanted]
            candidates = exact or [t for t in own['threads'] if wanted in t.get('title', '').casefold()]
            if len(candidates) != 1:
                raise ClarificationNeeded('missing or ambiguous conversation')
            if own.get('private_thread') == own['active']:
                raise RecordingPaused('end private conversation before switching')
            own['active'] = candidates[0]['id']
            own['write_epoch'] = own.get('write_epoch', 0) + 1  # switch away/back invalidates old results
            self._quota(own)
        return self.core._change(update)

    def correct_fact(self, owner, *, key, source_thread, source_turn, start, end, expected_revision):
        if key not in FACT_KEYS:
            raise ClarificationNeeded('unsupported or multi-valued fact key')
        if not self.generation():
            return False
        def update(data):
            own = self._book(data)['owners'][owner]
            self._writable(own)
            if own.get('fact_revision', 0) != expected_revision:
                raise c.StaleTurn('fact state changed')
            state = next((t for t in own['threads'] if t['id'] == source_thread), None)
            if state is None:
                raise ValueError('fact source does not belong to owner')
            c.authorize(c.validate(state), owner)
            source = _source(state, source_turn)
            if (source is None or type(start) is not int or type(end) is not int
                    or not 0 <= start < end <= len(source['q']) or end - start > 200):
                raise ValueError('invalid user fact source')
            value = source['q'][start:end]
            own.setdefault('facts', {})[key] = dict(id=uuid.uuid4().hex, value=value,
                source=dict(conversation_id=source_thread, turn_id=source_turn, at=source['at'],
                            role='user', quote=value, start=start, end=end))
            own['fact_revision'] = expected_revision + 1
            # In-flight turns must not reintroduce the old fact after correction.
            own['write_epoch'] = own.get('write_epoch', 0) + 1
            self._quota(own)
        return self.core._change(update)

    def explain_fact(self, owner, key):
        data = self.core.load()
        if data is None or not self.generation():
            return None
        own = self._book(copy.deepcopy(data))['owners'].get(owner)
        fact = (own or {}).get('facts', {}).get(key)
        return copy.deepcopy(fact) if fact else None

    def explain_slot(self, owner, key):
        state = self.read(owner)
        if state is None:
            return None
        slot = state['slots'].get(key)
        if slot is None:
            return None
        source = _source(state, slot['source_id'])
        return dict(value=slot['quote'], source_id=source['id'], quote=source['q'],
                    at=source['at'], role='user')
