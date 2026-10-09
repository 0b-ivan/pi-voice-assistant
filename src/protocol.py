"""SPX/1: the message envelope between Pi and Servitor server.

One module for both sides (the server imports ``src``). Every message is a
small JSON object:

  {"v": 1, "id": "b7f3...", "type": "ping", "ts": 1760000000,
   "ttl": 60, "prio": 2, "ack": true, "body": {...}}

Only types listed in TYPES are accepted, each with a size limit for its body:
that list is the brake against the protocol growing wild. A message seen
twice (same id) is handled once. Audio never travels in an envelope; it stays
binary in /v1/turn and /v1/speak.

Session (HTTP): the Pi sends ``hello`` to ``POST /v1/hello`` and gets
``welcome`` with a session id. With that id the stable part of the memory
core (facts, directives, voiceprints) is sent once and then only named by its
hash; every turn still carries the short recent history.
"""
import collections
import hashlib
import json
import threading
import time
import uuid

VERSION = 1
# Type -> largest body in bytes (compact JSON). Later steps add their types here.
TYPES = {
    'hello': 2048,
    'welcome': 2048,
    'ping': 256,
    'ack': 256,
}
PRIO_ALARM, PRIO_HIGH, PRIO_NORMAL, PRIO_LOW = 0, 1, 2, 3
MAX_MESSAGE = 16 * 1024      # whole envelope, any type
MAX_TTL = 7 * 24 * 3600
CLOCK_SKEW = 300             # a message from up to 5 min in the future is fine


class ProtocolError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def digest(value):
    """Short fingerprint of a JSON value, the same on Pi and server."""
    return hashlib.sha256(_compact(value).encode('utf-8')).hexdigest()[:16]


def message(kind, body=None, ttl=None, prio=PRIO_NORMAL, ack=False, now=None):
    """A new envelope; raises ProtocolError for unknown types or large bodies."""
    item = dict(v=VERSION, id=uuid.uuid4().hex, type=kind,
                ts=int(time.time() if now is None else now), prio=prio, ack=bool(ack),
                body=body or {})
    if ttl is not None:
        item['ttl'] = int(ttl)
    return validate(item, now)


def encode(item):
    return _compact(item).encode('utf-8')


def validate(item, now=None):
    """Check an envelope (dict); returns it, raises ProtocolError."""
    if not isinstance(item, dict):
        raise ProtocolError('format', 'message is not an object')
    if item.get('v') != VERSION:
        raise ProtocolError('version', f'unsupported version {item.get("v")!r}')
    kind = item.get('type')
    if kind not in TYPES:
        raise ProtocolError('type', f'unknown type {kind!r}')
    ident = item.get('id')
    if not isinstance(ident, str) or not 8 <= len(ident) <= 64 or not ident.isalnum():
        raise ProtocolError('format', 'id must be 8 to 64 letters or digits')
    ts, ttl, prio = item.get('ts'), item.get('ttl'), item.get('prio', PRIO_NORMAL)
    if not isinstance(ts, int) or isinstance(ts, bool):
        raise ProtocolError('format', 'ts must be an integer')
    if ttl is not None and (not isinstance(ttl, int) or isinstance(ttl, bool)
                            or not 0 < ttl <= MAX_TTL):
        raise ProtocolError('format', f'ttl must be 1 to {MAX_TTL} seconds')
    if prio not in (PRIO_ALARM, PRIO_HIGH, PRIO_NORMAL, PRIO_LOW):
        raise ProtocolError('format', 'prio must be 0 to 3')
    if not isinstance(item.get('ack', False), bool):
        raise ProtocolError('format', 'ack must be true or false')
    body = item.get('body', {})
    if not isinstance(body, dict):
        raise ProtocolError('format', 'body must be an object')
    if len(_compact(body).encode('utf-8')) > TYPES[kind]:
        raise ProtocolError('size', f'{kind} body larger than {TYPES[kind]} bytes')
    now = time.time() if now is None else now
    if ts > now + CLOCK_SKEW:
        raise ProtocolError('time', 'message from the future')
    if ttl is not None and ts + ttl < now:
        raise ProtocolError('expired', 'message expired')
    return item


def decode(raw, now=None):
    """Bytes from the wire -> validated envelope."""
    if len(raw) > MAX_MESSAGE:
        raise ProtocolError('size', f'message larger than {MAX_MESSAGE} bytes')
    try:
        item = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError('format', 'not JSON') from exc
    return validate(item, now)


class Seen:
    """Ids of recently handled messages: a resent message is handled once."""

    def __init__(self, size=512):
        self.size = size
        self.ids = collections.OrderedDict()
        self.lock = threading.Lock()

    def first(self, ident):
        """True the first time an id is seen."""
        with self.lock:
            if ident in self.ids:
                self.ids.move_to_end(ident)
                return False
            self.ids[ident] = True
            while len(self.ids) > self.size:
                self.ids.popitem(last=False)
            return True


# --- memory core in a session --------------------------------------------------

def split_memory(context):
    """(stable core, recent history) of a memory context (memory.Memory.context)."""
    core = {key: value for key, value in context.items() if key != 'history'}
    return core, context.get('history', [])


class ClientSession:
    """The Pi's side of a session: id and the memory core the server holds."""

    def __init__(self):
        self.lock = threading.Lock()
        self.id = None
        self.core = None           # digest of the core the server confirmed
        self.supported = True      # False after an old server answered 404

    def start(self, ident):
        with self.lock:
            self.id, self.core = ident, None

    def reset(self):
        with self.lock:
            self.id, self.core = None, None

    def confirmed(self, core_digest):
        with self.lock:
            if self.id is not None:
                self.core = core_digest

    def memory_payload(self, context):
        """What goes into X-Servitor-Memory for this turn: the full context, or
        only the history plus the digest of a core the server already has."""
        if context is None:
            return None
        core, history = split_memory(context)
        with self.lock:
            known = self.id is not None and self.core == digest(core)
        if known:
            return dict(core=digest(core), history=history)
        return context


class Sessions:
    """The server's sessions: id -> memory core; bounded, idle ones expire."""

    def __init__(self, limit=16, idle=24 * 3600, clock=time.monotonic):
        self.limit, self.idle, self.clock = limit, idle, clock
        self.items = collections.OrderedDict()
        self.lock = threading.Lock()

    def open(self, device=''):
        ident = uuid.uuid4().hex
        with self.lock:
            self._expire()
            self.items[ident] = dict(device=str(device)[:40], core=None, digest=None,
                                     seen=self.clock())
            while len(self.items) > self.limit:
                self.items.popitem(last=False)
        return ident

    def _expire(self):
        now = self.clock()
        for ident in [i for i, s in self.items.items() if now - s['seen'] > self.idle]:
            del self.items[ident]

    def known(self, ident):
        with self.lock:
            self._expire()
            session = self.items.get(ident)
            if session is not None:
                session['seen'] = self.clock()
                self.items.move_to_end(ident)
            return session is not None

    def remember(self, ident, context):
        """Keep the core of a full memory context; returns its digest or None."""
        core, _ = split_memory(context)
        with self.lock:
            session = self.items.get(ident)
            if session is None:
                return None
            session['core'], session['digest'] = core, digest(core)
            return session['digest']

    def restore(self, ident, payload):
        """Full context from a slim payload (core digest + history), or None
        when this session does not hold that core (new server, other core)."""
        with self.lock:
            session = self.items.get(ident)
            if session is None or session['digest'] != payload.get('core'):
                return None
            return dict(session['core'], history=payload.get('history', []))
