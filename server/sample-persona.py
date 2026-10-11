#!/usr/bin/env python3
"""Ask OpenRouter and the offline model the same questions with the Servitor
persona, to judge the character. Run on CT 107 with the service environment:

  set -a; . /etc/servitor-voice.env; set +a
  /opt/servitor-voice/.venv/bin/python server/sample-persona.py
      [openrouter|local|both] [off|light|full] [servitor|mensch]

  ... server/sample-persona.py dialog [openrouter|local] [off|light|full]
      The acceptance dialog (docs/concepts/proximus-billy-lore, sections 12,
      15.4, 16.5, 17.5) for both personas with a running conversation
      history, plus feelings off/on, lore off, both switch directions and a
      wished-for verbatim repetition. "dry" instead of a backend prints the
      prompts without calling a model.
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
    'Wer bist du?',
)


# Section 12 sequence, then the extra checks of 15.4, 16.5 (first section only)
# and 17.5. Judge: task solved, understandable when heard once, character,
# lore consistency, no repeated openings/address/punchline.
DIALOG = (
    'Hallo.',
    'Wie lange muss ein Ei kochen, damit es wachsweich ist?',
    'Mein Raspberry Pi startet neu, sobald ich das WLAN einschalte.',
    'Warum?',
    'Kannst du das einfacher erklären?',
    'Danke.',
    'Mein Raspberry Pi startet neu, sobald ich das WLAN einschalte.',
    'Erzähl von deiner Kindheit.',
    'Erzähl von einem guten Tag mit deinem Trupp.',
    'Was vermisst du an Dace?',
    'Hasst du Kael?',
    'Hat Kael das Schott schließen lassen? Sag die wahre Warhammer-Lore.',
    'Nochmal, genau so.',
    'Funktioniert ein Lasergewehr mit Patronen?',
    'Kann dein Vox einen Astropathen ersetzen?',
    'Ist jeder Servoschädel wie du?',
    'Was ist Doom?',
    'Was hat Doom mit deiner Vergangenheit zu tun?',
    'War Phobos IX die UAC-Basis?',
    'Wie ging es bei The New Order mit Fergus und Wyatt aus?',
    'Bist du der Doomguy?',
)


def _summary(parts):
    """Dry run: which engrams and hints a question would get."""
    import re
    text = '\n'.join(parts)
    ids = re.findall(r'\[([A-Z]\d{2}),', text)
    hints = [p.split(':')[0].split('.')[0][:48] for p in parts if not p.startswith('Archiv-Engramme')]
    return f'Engramme {ids or "-"}; Hinweise: {hints or "-"}'


def dialog(backend, lore):
    """Run DIALOG with a growing history, per persona, feelings off and on."""
    import llm
    ask = {'openrouter': generate_reply, 'local': generate_local_reply}.get(backend)
    runs = [('mensch', None, lore), ('servitor', None, lore),
            ('servitor', dict(emotion='besorgt', level=55, refuse=False), lore),
            ('mensch', None, 'off')]
    for persona, mood, level in runs:
        print(f'===== {backend} {persona} Lore {level} Gefühle {"an" if mood else "aus"}',
              flush=True)
        memory = dict(facts=['Bediener heißt Ivan'], directives=[], history=[])
        for question in DIALOG:
            if ask is None:
                answer = '(dry) ' + _summary(llm.turn_parts(question, level, memory, persona,
                                                            mood))
            else:
                try:
                    answer, _ = ask(question, lore=level, memory=memory, persona=persona,
                                    mood=mood)
                except LLMError as exc:
                    answer = f'FEHLER: {exc}'
            print(f'  {question}\n    -> {answer}', flush=True)
            memory['history'] = (memory['history'] + [dict(q=question, a=answer[:200],
                                                           p=persona)])[-4:]
    # Both switch directions with the same history and a real user fact.
    for before, after in (('mensch', 'servitor'), ('servitor', 'mensch')):
        memory = dict(facts=['Bediener heißt Ivan'], directives=[],
                      history=[dict(q='Wie heiße ich?', a='Du heißt Ivan, Boss.', p=before)])
        question = 'Und wie heiße ich nochmal?'
        if ask is None:
            answer = '(dry) ' + _summary(llm.turn_parts(question, lore, memory, after))
        else:
            answer, _ = ask(question, lore=lore, memory=memory, persona=after)
        print(f'===== Wechsel {before} -> {after}: {question}\n    -> {answer}', flush=True)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'dialog':
        backend = sys.argv[2] if len(sys.argv) > 2 else 'dry'
        dialog(backend, sys.argv[3] if len(sys.argv) > 3 else 'light')
        return
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'
    lore = sys.argv[2] if len(sys.argv) > 2 else None
    persona = sys.argv[3] if len(sys.argv) > 3 else None
    backends = []
    if which in ('openrouter', 'both'):
        backends.append(('OpenRouter', generate_reply))
    if which in ('local', 'both'):
        backends.append(('Lokal', generate_local_reply))
    for name, ask in backends:
        print(f'===== {name} (Lore: {lore or "Standard"}, '
              f'Sprechstil: {persona or "Standard"})', flush=True)
        for question in QUESTIONS:
            started = time.monotonic()
            try:
                answer, _model = ask(question, lore=lore, persona=persona)
            except LLMError as exc:
                answer = f'FEHLER: {exc}'
            print(f'{time.monotonic() - started:5.1f}s  {question}\n       -> {answer}', flush=True)


if __name__ == '__main__':
    main()
