"""Text prepared for Piper right before it speaks: what the German espeak
phonemizer behind Piper would read or stress wrongly.

Only the spoken text changes; the display, logs and memory keep the real
spelling. Check a candidate with the phonemizer from Piper's venv:

    python -c "from piper.phonemize_espeak import EspeakPhonemizer as P; \
print(P().phonemize('de', 'Lob dem Omnissi-ah.'))"

Three steps, in this order:

1. Abbreviations, units, dates, decimals: espeak spells "z. B." letter by
   letter and makes a sentence of each dot, reads "10. Oktober" as "zehn.
   Oktober" (with a sentence pause), "3.5" as "drei Punkt fünf", "°C" as
   "Grad C" and "Phobos IX" as "Phobos römisch neun".
2. Respellings (Omnissiah).
3. Names espeak stresses wrongly ("pro-XI-mus", "A-deptus", "ME-chanicus")
   go to Piper as raw phonemes ``[[ ... ]]`` (Piper >= 1.3; 1.8.0 here).
   Without that support the word stays as it is.
"""
import re
import unicodedata

RULES = (
    # espeak: 'ˈɔmnɪsˌiːɑː' (OM-nissi-a); lore: om-NISS-i-ah -> 'ɔmnˈɪsiːˈɑː'
    (re.compile(r'\bOmnissiahs?\b', re.IGNORECASE), 'Omnissi-ah'),
)

# Word -> IPA as espeak writes it (checked against Piper's phoneme map).
PHONEMES = (
    ('Adeptus Mechanicus', 'adˈɛptʊs meçˈaːnikʊs'),
    ('Proximus', 'prˈɔksimʊs'),          # espeak: proːksˈiːmʊs
    ('Adeptus', 'adˈɛptʊs'),             # ˈɑdɛptˌʊs
    ('Mechanicus', 'meçˈaːnikʊs'),       # mˈɛçanˌiːkʊs
    ('Noosphäre', 'noːɔsfˈɛːrə'),        # noːsfˈɛːrə
    ('Astartes', 'astˈaɾtəs'),           # ˈaʃtaɾtəs
    ('Lho-Stäbchen', 'lˈoːʃtɛːpçən'),    # ɛl ha o Stäbchen
    ('Leman', 'lˈeːmən'),                # leːmˈɑːn
    ('Blazkowicz', 'blaskˈoːvɪts'),      # blˈatskoːvˌɪkts
    ('Milwaukee', 'mɪlvˈɔːkiː'),         # mˈɪlvaʊkˌeː
    ('Secundus', 'zeːkˈʊndʊs'),          # zˈeːkʊndˌʊs
)
# Punctuation right after the word goes into the block: Piper drops it when
# the text after a raw block starts with it ("Mechanicus. Diese" ran together).
_PHONEME_RULES = tuple(
    (re.compile(rf'(?<![\w-]){re.escape(word)}(?![\w-])([.,;:!?]*)', re.IGNORECASE),
     unicodedata.normalize('NFD', ipa))
    for word, ipa in PHONEMES)

# Abbreviation -> words. Sentence-final ones keep their full stop.
ABBREVIATIONS = (
    (r'z\.\s?B\.', 'zum Beispiel'),
    (r'd\.\s?h\.', 'das heißt'),
    (r'u\.\s?a\.', 'unter anderem'),
    (r'o\.\s?ä\.', 'oder ähnlich'),
    (r'usw\.', 'und so weiter'),
    (r'bzw\.', 'beziehungsweise'),
    (r'evtl\.', 'eventuell'),
    (r'ggf\.', 'gegebenenfalls'),
    (r'ca\.', 'circa'),
    (r'inkl\.', 'inklusive'),
    (r'vgl\.', 'vergleiche'),
    (r'etc\.', 'et cetera'),
    (r'Nr\.(?=\s*\d)', 'Nummer'),
    (r'Dr\.', 'Doktor'),
    (r'Mio\.', 'Millionen'),
    (r'Mrd\.', 'Milliarden'),
)
_ABBREVIATIONS = tuple((re.compile(rf'(?<!\w){pattern}'), words)
                       for pattern, words in ABBREVIATIONS)

# Unit after a number -> words ("%" and "€" espeak already reads).
UNITS = (
    ('km/h', 'Kilometer pro Stunde'),
    ('m/s', 'Meter pro Sekunde'),
    ('kWh', 'Kilowattstunden'),
    ('°C', 'Grad'),
    ('° C', 'Grad'),
    ('°', 'Grad'),
    ('hPa', 'Hektopascal'),
    ('km', 'Kilometer'),
    ('cm', 'Zentimeter'),
    ('mm', 'Millimeter'),
    ('kg', 'Kilogramm'),
    ('GB', 'Gigabyte'),
    ('MB', 'Megabyte'),
)
_UNITS = re.compile(r'(\d)\s*(' + '|'.join(re.escape(u) for u, _ in UNITS) + r')(?![\w/])')
_UNIT_WORDS = dict(UNITS)

MONTHS = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August',
          'September', 'Oktober', 'November', 'Dezember')
_ONES = ('', 'ein', 'zwei', 'drei', 'vier', 'fünf', 'sechs', 'sieben', 'acht', 'neun',
         'zehn', 'elf', 'zwölf', 'dreizehn', 'vierzehn', 'fünfzehn', 'sechzehn',
         'siebzehn', 'achtzehn', 'neunzehn')
_ORDINAL_STEMS = {1: 'erst', 3: 'dritt', 7: 'siebt', 8: 'acht'}
# Before these words a date takes "-en" (am zehnten), after der/die/das "-e".
_DATIVE = {'am', 'vom', 'zum', 'dem', 'den', 'beim', 'im', 'ab', 'bis', 'seit', 'für'}
_NOMINATIVE = {'der', 'die', 'das'}
_WEEKDAYS = {'montag', 'dienstag', 'mittwoch', 'donnerstag', 'freitag', 'samstag', 'sonntag'}


def _ordinal_stem(number):
    if number in _ORDINAL_STEMS:
        return _ORDINAL_STEMS[number]
    if number < 20:
        return _ONES[number] + 't'
    tens = {2: 'zwanzig', 3: 'dreißig'}[number // 10]
    ones = number % 10
    return (f'{_ONES[ones]}und{tens}' if ones else tens) + 'st'


def _ordinal(number, before):
    word = before.lower()
    ending = 'en' if word in _DATIVE else 'e' if word in _NOMINATIVE else 'er'
    return _ordinal_stem(number) + ending


_MONTH_PATTERN = '|'.join(MONTHS)
# "10.10.2026", "10.10." (not a time like 10.10 Uhr), "10. Oktober"
_NUMERIC_DATE = re.compile(r'(?:(\w+)\s+)?\b(\d{1,2})\.(\d{1,2})\.(\d{4})?(?!\d)')
_NAMED_DATE = re.compile(rf'(?:(\w+)\s+)?\b(\d{{1,2}})\.\s*({_MONTH_PATTERN})\b')
_DOT_TIME = re.compile(r'\b(\d{1,2})\.(\d{2})(?=\s*Uhr\b)')
_DECIMAL = re.compile(r'(?<![\d.])(\d+)\.(\d{1,2})(?![\d.])')
_ROMAN = re.compile(r'\bPhobos\s+IX\b')


def _numeric_date(match):
    before, day, month, year = match.group(1) or '', int(match.group(2)), int(match.group(3)), match.group(4)
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return match.group(0)
    if not year and before.lower() not in _DATIVE | _NOMINATIVE | _WEEKDAYS:
        return match.group(0)   # "3.5." could be a decimal at the end of a sentence
    lead = f'{before} ' if before else ''
    return f'{lead}{_ordinal(day, before)} {MONTHS[month - 1]}' + (f' {year}' if year else '')


def _named_date(match):
    before, day = match.group(1) or '', int(match.group(2))
    if not 1 <= day <= 31:
        return match.group(0)
    lead = f'{before} ' if before else ''
    return f'{lead}{_ordinal(day, before)} {match.group(3)}'


def _abbreviation(words):
    def replace(match):
        rest = match.string[match.end():].lstrip()
        # Sentence-final ("... und so weiter.") keeps its full stop.
        if not rest or (rest[0].isupper() and words in ('und so weiter', 'et cetera')):
            return words + '.'
        return words
    return replace


def normalized(text):
    """Step 1: what espeak would spell out or read as punctuation."""
    for pattern, words in _ABBREVIATIONS:
        text = pattern.sub(_abbreviation(words), text)
    text = _NUMERIC_DATE.sub(_numeric_date, text)
    text = _NAMED_DATE.sub(_named_date, text)
    text = _DOT_TIME.sub(r'\1 Uhr \2', text)
    text = re.sub(r'(\d+ Uhr \d{2})\s*Uhr\b', r'\1', text)
    text = _DECIMAL.sub(r'\1,\2', text)
    text = _UNITS.sub(lambda m: f'{m.group(1)} {_UNIT_WORDS[m.group(2)]}', text)
    text = _ROMAN.sub('Phobos Neun', text)
    return text


_RAW_PHONEMES = None


def raw_phonemes_supported():
    """Piper splits ``[[ ... ]]`` off as raw phonemes (piper1-gpl >= 1.3)."""
    global _RAW_PHONEMES
    if _RAW_PHONEMES is None:
        try:
            import piper.voice
            _RAW_PHONEMES = hasattr(piper.voice, '_PHONEME_BLOCK_PATTERN')
        except Exception:
            _RAW_PHONEMES = False
    return _RAW_PHONEMES


def spoken(text, raw_phonemes=None):
    text = normalized(text)
    for pattern, replacement in RULES:
        text = pattern.sub(replacement, text)
    if raw_phonemes is None:
        raw_phonemes = raw_phonemes_supported()
    if raw_phonemes:
        for pattern, ipa in _PHONEME_RULES:
            text = pattern.sub(lambda m, ipa=ipa: f'[[ {ipa}{m.group(1)} ]]', text)
    return text
