"""Observable execution events, never prompts, private memories or model reasoning.

A request-local sink keeps concurrent requests separate. Outside a server turn
this module is silent; diagnostic failures must never interrupt an answer.
"""
from contextvars import ContextVar
from functools import wraps
import inspect
import secrets
import time

_SINK = ContextVar('processing_trace', default=None)


def record(step, **fields):
    sink = _SINK.get()
    if sink is not None:
        try:
            sink(dict(step=step, **fields))
        except Exception:
            pass


def turn(function):
    signature = inspect.signature(function)

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        import json
        ident = secrets.token_hex(4)
        started = time.monotonic()

        def sink(event):
            self.journal.add(json.dumps(dict(event='processing', turn=ident, **event)),
                             unit='servitor-voice',
                             priority=3 if event.get('state') == 'failed' else 6)

        token = _SINK.set(sink)
        try:
            bound = signature.bind(self, *args, **kwargs)
            emit = bound.arguments['emit']

            def observed(event):
                name = event.get('event')
                if name == 'stage':
                    record('stage', name=event.get('stage'))
                elif name == 'reply':
                    record('answer', model=str(event.get('model', ''))[:80])
                return emit(event)

            bound.arguments['emit'] = observed
            record('turn', state='start', channel='text' if bound.arguments.get('typed') is not None
                   else 'story' if function.__name__ == 'run_story'
                   else 'speak' if bound.arguments.get('text') is not None else 'voice')
            result = function(*bound.args, **bound.kwargs)
            record('turn', state='done', ms=round((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            record('turn', state='failed', error=type(exc).__name__)
            raise
        finally:
            _SINK.reset(token)

    return wrapped
