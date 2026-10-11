"""Whisper as the recognizer on CT 107 (optional: SERVITOR_STT=whisper).

Vosk keeps streaming while the button is held. It decides whether anything
was said at all (Whisper invents sentences from silence, "Untertitel im
Auftrag des ZDF") and answers short fixed requests at once: when its text
already matches an intent ("wie spät ist es"), Whisper is skipped. Everything
else is transcribed again by faster-whisper after release, on the whole take;
if Whisper fails or returns nothing usable, the Vosk text stays.

Every Whisper turn logs both texts (event stt_compare), so the two can be
compared on real use. Installation: server/install-whisper.sh.
"""
import json
import os
import re
import time

from transcribe import LiveVoskRecognizer

MODEL = os.environ.get('SERVITOR_WHISPER_MODEL', 'small')
MODEL_DIR = os.environ.get('SERVITOR_WHISPER_DIR', '/opt/servitor-voice/models/whisper')
# Spelling hints for names Whisper cannot know; kept short (a long prompt
# makes it repeat the prompt on unclear audio).
PROMPT = os.environ.get('SERVITOR_WHISPER_PROMPT',
                        'Proximus, Servitor, Omnissiah, Morgenbericht, WLAN.')
# Whisper's known German inventions from its subtitle training data.
HALLUCINATIONS = re.compile(
    r'untertitel|vielen dank fürs zuschauen|danke fürs zuschauen|amara\.org|'
    r'copyright|im auftrag des|bis zum nächsten mal', re.IGNORECASE)

_model = None


def enabled(env=None):
    env = os.environ if env is None else env
    return env.get('SERVITOR_STT', 'vosk').strip().lower() == 'whisper'


def load():
    """Load and warm the model (the first transcription is slow otherwise)."""
    global _model
    from faster_whisper import WhisperModel
    _model = WhisperModel(MODEL, device='cpu', compute_type='int8',
                          cpu_threads=os.cpu_count() or 4, download_root=MODEL_DIR)
    transcribe_pcm(b'\0\0' * 16000)


def loaded():
    return _model is not None


def _words(text):
    return re.findall(r'[\wäöüß]+', text.lower())


def usable(text):
    """Whisper's text, or '' when it is an invention or just the prompt."""
    text = ' '.join(text.split())
    words = _words(text)
    if not words or HALLUCINATIONS.search(text):
        return ''
    if PROMPT and set(words) <= set(_words(PROMPT)):
        return ''
    return text


def transcribe_pcm(pcm, model=None):
    """16 kHz mono int16 PCM -> text ('' when nothing usable)."""
    import numpy
    model = model or _model
    audio = numpy.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype=numpy.int16)
    audio = audio.astype(numpy.float32) / 32768
    segments, _info = model.transcribe(
        audio, language='de', beam_size=1, condition_on_previous_text=False,
        initial_prompt=PROMPT or None, without_timestamps=True)
    return usable(' '.join(s.text.strip() for s in segments if s.no_speech_prob < 0.6))


class HybridRecognizer:
    """Same interface as LiveVoskRecognizer (accept_pcm, finish).

    ``quick(text)``: True when the Vosk text can be used as it is (a matched
    intent); ``whisper=False`` keeps Vosk only (passphrases were enrolled
    with Vosk spelling)."""

    def __init__(self, quick=None, vosk=None, transcribe=None, log=print):
        self.vosk = vosk or LiveVoskRecognizer()
        self.audio = bytearray()
        self.quick = quick
        self.whisper = True
        self.transcribe = transcribe or transcribe_pcm
        self.log = log

    def accept_pcm(self, pcm):
        self.vosk.accept_pcm(pcm)
        self.audio += pcm

    def finish(self):
        text = self.vosk.finish()          # NoSpeechError: nothing said, no Whisper
        if not self.whisper or (self.quick is not None and self.quick(text)):
            return text
        started = time.monotonic()
        try:
            better = self.transcribe(bytes(self.audio))
        except Exception as exc:  # never lose a turn over Whisper
            self.log(json.dumps(dict(event='whisper_error', message=str(exc))), flush=True)
            return text
        self.log(json.dumps(dict(event='stt_compare', vosk=text, whisper=better,
                                 seconds=round(time.monotonic() - started, 2)),
                            ensure_ascii=False), flush=True)
        return better or text
