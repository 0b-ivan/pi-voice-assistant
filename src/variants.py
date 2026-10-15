"""Small pools of wordings for frequent fixed sentences (no LLM involved).

Every variant of one key carries the same facts: numbers, status, target and
any confirmation the operator must give stay identical, only the wording
changes. Only the IDs of the last variants are kept, in RAM, so the same
sentence does not come twice in a row; nothing about the conversation is
stored. Critical warnings and confirmation prompts are not varied.
"""
from collections import deque
import random

_recent = {}


def pick(key, options, rng=None, avoid=1, **values):
    """One of ``options`` (strings with ``{name}`` placeholders), never one of
    the last ``avoid`` choices for ``key``; formatted with ``values``."""
    options = tuple(options)
    if not options:
        raise ValueError('no options')
    rng = rng or random
    used = _recent.setdefault(key, deque(maxlen=max(1, min(avoid, len(options) - 1))))
    free = [i for i in range(len(options)) if i not in used] or list(range(len(options)))
    index = rng.choice(free)
    used.append(index)
    return options[index].format(**values) if values else options[index]


def reset():
    _recent.clear()
