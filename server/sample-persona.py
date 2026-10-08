#!/usr/bin/env python3
"""Ask OpenRouter and the offline model the same questions with the Servitor
persona, to judge the character. Run on CT 107 with the service environment:

  set -a; . /etc/servitor-voice.env; set +a
  /opt/servitor-voice/.venv/bin/python server/sample-persona.py [openrouter|local|both] [off|light|full]
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from llm import LLMError, generate_local_reply, generate_reply  # noqa: E402

QUESTIONS = (
    'Wie hoch ist der Eiffelturm?',
    'Was ist die Hauptstadt von Australien?',
    'Erkläre kurz, was ein Raspberry Pi ist.',
    'Wie spät ist es in Tokio, wenn es in Berlin zwölf Uhr ist?',
    'Danke dir!',
    'Wie geht es dir heute?',
    'Erzähl mir einen Witz.',
    'Mach das Licht im Wohnzimmer an.',
    'Wer hat die Relativitätstheorie entwickelt?',
    'Was hältst du von Pizza?',
)


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'
    lore = sys.argv[2] if len(sys.argv) > 2 else None
    backends = []
    if which in ('openrouter', 'both'):
        backends.append(('OpenRouter', generate_reply))
    if which in ('local', 'both'):
        backends.append(('Lokal', generate_local_reply))
    for name, ask in backends:
        print(f'===== {name} (Lore: {lore or "Standard"})', flush=True)
        for question in QUESTIONS:
            started = time.monotonic()
            try:
                answer, _model = ask(question, lore=lore)
            except LLMError as exc:
                answer = f'FEHLER: {exc}'
            print(f'{time.monotonic() - started:5.1f}s  {question}\n       -> {answer}', flush=True)


if __name__ == '__main__':
    main()
