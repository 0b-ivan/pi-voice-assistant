"""Respellings applied to text right before Piper speaks it.

The German espeak phonemizer behind Piper stresses some lore words wrongly.
Only the spoken text changes; the display, logs and memory keep the real
spelling. Check a candidate with the phonemizer from Piper's venv:

    python -c "from piper.phonemize_espeak import EspeakPhonemizer as P; \
print(P().phonemize('de', 'Lob dem Omnissi-ah.'))"
"""
import re

RULES = (
    # espeak: 'ˈɔmnɪsˌiːɑː' (OM-nissi-a); lore: om-NISS-i-ah -> 'ɔmnˈɪsiːˈɑː'
    (re.compile(r'\bOmnissiahs?\b', re.IGNORECASE), 'Omnissi-ah'),
)


def spoken(text):
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    return text
