#!/usr/bin/env python3
"""Time the offline LLM fallback with typical spoken questions.

Runs inside CT 107 against the loopback llama.cpp server, using the same
Servitor system prompt and request as the voice service. Prints the answer,
wall time and llama.cpp's own prompt/generation timings per question.

  /opt/servitor-voice/.venv/bin/python server/bench-local-llm.py
"""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from llm import DEFAULT_LOCAL_LLM_URL, SERVITOR_SYSTEM_PROMPT  # noqa: E402

QUESTIONS = (
    'Wie hoch ist der Eiffelturm?',
    'Wie spät ist es in Tokio, wenn es in Berlin zwölf Uhr ist?',
    'Was ist die Hauptstadt von Australien?',
    'Erkläre kurz, was ein Raspberry Pi ist.',
    'Wie viele Minuten hat ein Tag?',
)


def ask(url, question, max_tokens):
    body = json.dumps({
        'model': 'local', 'stream': False, 'max_tokens': max_tokens,
        'messages': [{'role': 'system', 'content': SERVITOR_SYSTEM_PROMPT},
                     {'role': 'user', 'content': question}],
    }).encode()
    request = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    return payload, time.monotonic() - started


def main():
    url = os.environ.get('LOCAL_LLM_URL', DEFAULT_LOCAL_LLM_URL)
    max_tokens = int(os.environ.get('LOCAL_LLM_MAX_TOKENS', '120'))
    walls = []
    for question in QUESTIONS:
        payload, wall = ask(url, question, max_tokens)
        walls.append(wall)
        answer = payload['choices'][0]['message']['content'].strip().replace('\n', ' ')
        t = payload.get('timings', {})
        print(f'{wall:5.2f}s  prompt {t.get("prompt_n", "?")} tok '
              f'{t.get("prompt_per_second", 0):5.1f}/s  gen {t.get("predicted_n", "?")} tok '
              f'{t.get("predicted_per_second", 0):5.1f}/s  | {question} -> {answer}', flush=True)
    print(f'median {sorted(walls)[len(walls) // 2]:.2f}s  max {max(walls):.2f}s')


if __name__ == '__main__':
    main()
