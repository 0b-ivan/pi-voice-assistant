"""Mouth shapes for the face while a reply is spoken.

The Pi knows the reply text and the loudness of the audio (50 ms steps).
Syllables are the peaks of the loudness; each one gets the next vowel of the
text, in order. The result is one character per step for the display:

  '.' mouth closed (pause, quiet consonant)
  'a' / 'A'  open (a, au, ai): half / wide
  'e' / 'E'  spread, teeth showing (e, i, ei, ä)
  'o' / 'O'  round (o, u, ö, ü, eu)

Upper case is the louder half. Only these codes leave ptt.py, never text.
"""
import re

QUIET = 15          # loudness (0..100) below which the mouth closes
LOUD = 55           # from here the mouth opens wide
MIN_GAP = 3         # steps between two syllable peaks (150 ms)
HOLD = 2            # steps a shape is held at least, against flicker
CODES = '.aAeEoO'

_VOWEL = re.compile(r'aa|ah|au|ai|ay|ei|ey|eu|äu|ie|ee|eh|oo|oh|uh|[aeiouyäöü]')
_CLASS = {'aa': 'a', 'ah': 'a', 'au': 'a', 'ai': 'a', 'ay': 'a', 'a': 'a',
          'ei': 'e', 'ey': 'e', 'ie': 'e', 'ee': 'e', 'eh': 'e', 'e': 'e', 'i': 'e',
          'y': 'e', 'ä': 'e',
          'eu': 'o', 'äu': 'o', 'oo': 'o', 'oh': 'o', 'uh': 'o', 'o': 'o', 'u': 'o',
          'ö': 'o', 'ü': 'o'}
# Digits are spoken as words: their vowels, roughly.
_DIGITS = {'0': 'u', '1': 'ei', '2': 'ei', '3': 'ei', '4': 'ie', '5': 'ü', '6': 'e',
           '7': 'ie e', '8': 'a', '9': 'eu'}


def vowels(text):
    """Vowel classes of a German text, one per syllable: 'a', 'e' or 'o'."""
    text = str(text or '').lower()
    text = re.sub(r'\d', lambda m: f' {_DIGITS[m.group()]} ', text)
    return [_CLASS[v] for v in _VOWEL.findall(text)]


def _peaks(levels):
    """Indexes of syllable peaks: local maxima above QUIET, MIN_GAP apart."""
    peaks = []
    for i, level in enumerate(levels):
        if level < QUIET * 1.5:
            continue
        left = levels[i - 1] if i else 0
        right = levels[i + 1] if i + 1 < len(levels) else 0
        if level >= left and level >= right:
            if peaks and i - peaks[-1] < MIN_GAP:
                if level > levels[peaks[-1]]:
                    peaks[-1] = i
                continue
            peaks.append(i)
    return peaks


def track(levels, text):
    """One mouth code per loudness step (see the module doc)."""
    classes = vowels(text) or ['a']
    peaks = _peaks(levels)
    shapes = ['.'] * len(levels)
    if not peaks:
        return ''.join(shapes)
    # Peak k gets the vowel at the same relative position in the text.
    vowel_at = {}
    for k, peak in enumerate(peaks):
        index = round(k * (len(classes) - 1) / (len(peaks) - 1)) if len(peaks) > 1 else 0
        vowel_at[peak] = classes[index]
    for i, level in enumerate(levels):
        if level >= QUIET:
            shapes[i] = vowel_at[min(peaks, key=lambda p: abs(p - i))]
    # Hold every shape for at least HOLD steps: 10 frames per second flicker.
    held, last, count = [], None, 0
    for shape in shapes:
        if last is not None and shape != last and count < HOLD:
            held.append(last)
            count += 1
            continue
        count = count + 1 if shape == last else 1
        last = shape
        held.append(shape)
    # Wide open on the loud steps of a vowel.
    return ''.join(shape.upper() if shape != '.' and level >= LOUD else shape
                   for shape, level in zip(held, levels))
