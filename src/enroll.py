"""Getting to know the operator: menu items, commands, wake-word takes.

The voice part, the facts and the confirmation live in voice_enroll
(enrollment v2, concept stimm-einlernen). This module keeps what is shared:

- command(): "Lerne mich kennen" / "Stimme nachtrainieren". A voice command
  only opens the menu at "Stimme kennenlernen"; button E starts, so no spoken
  sentence can overwrite an identity.
- QUESTIONS and name_from() for the optional facts module.
- Session: menu "Wakeword aufnehmen", WAKE_COUNT takes of "Proximus" (2 s,
  with hints) for the wake-word training in proximus/voice/wake/. They never
  enter a voice profile. Every take goes through voice_quality: bad formats
  and takes without speech are deleted, the numbers are logged as
  'enroll_clip' events (no content).
- Server calls of the Pi's io: transcribe(), voiceprint() (v1, people
  authentication) and clip() (POST /v2/enroll/clip).

Everything stays on the stick; button B cancels at any time. Sessions run in
their own thread and talk to the hardware through small callables, so they
can be tested without a microphone.
"""
import json
import re
import threading
import time
import wave
from pathlib import Path

import voice_quality

WAKE_COUNT = 20
WAKE_SECONDS = 2
ANSWER_SECONDS = 6
WAKE_HINTS = {
    0: "Sage nach jedem Signalton Proximus.",
    5: "Jetzt etwas leiser.",
    10: "Jetzt aus etwa zwei Metern Abstand.",
    15: "Jetzt so, wie du es im Alltag sagen würdest.",
}
# key, question, how the answer is stored ('fact' template or 'directive').
QUESTIONS = (
    ('name', "Wie soll diese Einheit dich nennen?", 'fact', "Name des Bedieners: {}"),
    ('home', "Wo lebst du?", 'fact', "Wohnort des Bedieners: {}"),
    ('work', "Womit verbringst du deine Arbeitstage?", 'fact', "Tätigkeit des Bedieners: {}"),
    ('interests', "Welche Themen interessieren dich besonders?", 'fact',
     "Interessen des Bedieners: {}"),
    ('people', "Welche Menschen sind dir wichtig?", 'fact', "Wichtige Personen des Bedieners: {}"),
    ('style', "Wie soll diese Einheit mit dir sprechen, knapp oder ausführlich?", 'directive',
     "Antwortstil nach Wunsch des Bedieners: {}"),
    ('more', "Was soll diese Einheit sonst noch über dich wissen?", 'fact',
     "Über den Bediener: {}"),
)
ANNOUNCE = "Kennenlern-Sitzung startet."
CANCELLED = "Kennenlern-Sitzung abgebrochen."
NEED_STICK = "Kennenlernen braucht den Gedächtniskern. Bitte Stick anschließen."
NEED_SERVER = "Kennenlernen braucht den Server. Verbindung prüfen."
FAILED = "Kennenlern-Sitzung abgebrochen. Mikrofon nicht verfügbar."
WAKE_INTRO = "Aktivierungswort aufnehmen. Sage nach jedem Signalton Proximus. Abbrechen mit Taste B."
MENU_HINT = "Kennenlernen bereit. Taste E startet, Taste B bricht ab."
CHOOSE_PERSON = "Mehrere Personen bekannt. Bitte im Menü Personen auswählen."
NO_PROFILE = "Noch kein Stimmprofil vorhanden. Erst Stimme kennenlernen."
# Problems that keep a clip out of a voiceprint or the wake-word samples.
VOICE_PROBLEMS = {'too_short', 'clipping', 'too_quiet', 'noisy', 'pauses'}
WAKE_PROBLEMS = {'too_short', 'clipping', 'too_quiet', 'noisy'}
WAKE_SPEECH_MIN = 0.3   # one "Proximus"
_REFINE = re.compile(r'\b(stimm(e|profil) (nachtrainieren|nach trainieren|verbessern|verfeinern|'
                     r'trainieren)|nachtrainieren|stimmtraining)\b')
_COMMAND = re.compile(r'\b(lerne? mich kennen|kennenlernen|kennen lernen|trainingsmodus|'
                      r'stimmprofil (anlegen|erstellen)|lerne? meine stimme|'
                      r'meine stimme (lernen|anlernen|einlernen|kennenlernen))\b')
_NAME_PREFIX = re.compile(r'^(?:du kannst mich |nenn(?:e)? mich |ich heiße |mein name ist |'
                          r'ich bin |sag |einfach )+')


def command(text):
    """'refine' (more samples for an existing voice), 'enroll' (new person) or None."""
    text = str(text).lower()
    if _REFINE.search(text):
        return 'refine'
    return 'enroll' if _COMMAND.search(text) else None


def wake_done_text(count):
    return f"Aktivierungswort aufgenommen. {count} Proben gespeichert."



def phrases():
    """Fixed sentences, for prerecorded clips."""
    import voice_enroll
    return [ANNOUNCE, CANCELLED, FAILED, WAKE_INTRO, MENU_HINT, CHOOSE_PERSON, NO_PROFILE,
            NEED_STICK, NEED_SERVER, wake_done_text(WAKE_COUNT), *WAKE_HINTS.values(),
            *(q for _, q, _, _ in QUESTIONS), *voice_enroll.phrases()]


def name_from(answer):
    """'nenn mich ivan' -> 'Ivan'."""
    words = _NAME_PREFIX.sub('', str(answer).lower().strip()).split()
    return ' '.join(w.capitalize() for w in words[:3]) or 'Bediener'


def beep_wav(path, freq=880.0, seconds=0.15, rate=16000):
    import math
    from array import array
    samples = array('h', (int(9000 * math.sin(2 * math.pi * freq * i / rate)
                              * min(1.0, i / 200, (seconds * rate - i) / 200))
                          for i in range(int(seconds * rate))))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples.tobytes())


def pcm_of(path):
    """Mono 16 kHz int16 PCM of a WAV clip (voice_quality.read_wav: format
    checked, stereo mixed, other rates resampled); AudioFormatError if not."""
    return voice_quality.read_wav(path)


def clip_check(pcm, kind, index, allowed=VOICE_PROBLEMS,
               speech_min=voice_quality.SPEECH_MIN, speech_max=voice_quality.SPEECH_MAX):
    """(quality numbers, problems that keep the clip out of the voiceprint).
    Logged as numbers and codes only."""
    quality = voice_quality.analyze(pcm)
    codes = voice_quality.problems(quality, speech_min, speech_max)
    print(json.dumps(dict(version=1, event='enroll_clip', kind=kind, index=index,
                          problems=codes, **quality)), flush=True)
    return quality, [code for code in codes if code in allowed]


def _read(path, kind, index):
    """PCM of a recorded clip, or None (bad format: logged, clip deleted)."""
    try:
        return pcm_of(path)
    except (voice_quality.AudioFormatError, OSError) as exc:
        print(json.dumps(dict(version=1, event='enroll_clip', kind=kind, index=index,
                              problems=['format'], error=type(exc).__name__)), flush=True)
        Path(path).unlink(missing_ok=True)
        return None


class Session:
    """Runs the steps; ``io`` provides say(text), beep(), record(path, seconds),
    transcribe(pcm) -> text|None, voiceprint(pcm) -> base64|None, publish(**state)."""

    def __init__(self, core, io, clock=time.time, mode='enroll', target=None):
        self.core, self.io, self.clock, self.mode = core, io, clock, mode
        self.target = target   # refine: this (authenticated) person, no matching
        self.cancelled = threading.Event()
        self.thread = None
        self.result = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, name='enroll', daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        abort = getattr(self.io, 'abort', None)
        if abort is not None:
            abort()  # stop a running recording or prompt right away

    def _check(self):
        if self.cancelled.is_set():
            raise _Cancelled()

    def _run(self):
        try:
            self.result = self.run()
        except _Cancelled:
            self.io.publish(stage=None)
            self.io.say(CANCELLED)
            self.result = dict(cancelled=True)
        except Exception as exc:  # never take the voice service down
            self.io.publish(stage=None)
            self.io.say(FAILED)
            self.result = dict(error=str(exc))

    def run(self):
        """Menu "Wakeword aufnehmen": WAKE_COUNT takes of "Proximus" for the
        wake-word training; they never enter a voice profile."""
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock()))
        wake_dir = self.core.voice_dir / 'wake'
        wake_dir.mkdir(parents=True, exist_ok=True)
        self.io.publish(stage='intro')
        self.io.say(WAKE_INTRO)
        kept = 0
        for index in range(WAKE_COUNT):
            self._check()
            if index in WAKE_HINTS:
                self.io.publish(stage='wake', step=index, total=WAKE_COUNT, rec=False)
                self.io.say(WAKE_HINTS[index])
            self.io.beep()
            self.io.publish(stage='wake', step=index + 1, total=WAKE_COUNT, rec=True)
            take = wake_dir / f'{stamp}-{index + 1:02d}.wav'
            if self.io.record(take, WAKE_SECONDS) is False:
                self._check()
                raise RuntimeError('microphone unavailable')
            self.io.publish(stage='wake', step=index + 1, total=WAKE_COUNT, rec=False)
            pcm = _read(take, 'wake', index)
            if pcm is None:
                continue
            _, rejected = clip_check(pcm, 'wake', index, WAKE_PROBLEMS, WAKE_SPEECH_MIN,
                                     voice_quality.SPEECH_MAX)
            if rejected and 'too_short' in rejected:
                take.unlink(missing_ok=True)        # nothing said: no training sample
                continue
            kept += 1
        self._check()
        self.io.publish(stage='done')
        self.io.say(wake_done_text(kept))
        self.io.publish(stage=None)
        return dict(wake=kept)


class _Cancelled(Exception):
    pass


# --- Server calls used by the Pi's io ------------------------------------------

def _post(path, pcm, env=None, timeout=30):
    import os
    import urllib.request
    env = os.environ if env is None else env
    urls = [u.strip().rstrip('/') for u in env.get('ASSISTANT_BASE_URL', '').split(',') if u.strip()]
    token = env.get('ASSISTANT_TOKEN', '').strip()
    if not urls or not token:
        return None
    from remote_turn import USER_AGENT
    for url in urls:
        request = urllib.request.Request(url + path, data=pcm, method='POST', headers={
            'Authorization': f'Bearer {token}', 'User-Agent': USER_AGENT,
            'Content-Type': 'application/octet-stream'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except OSError:
            continue
    return None


def transcribe(pcm, env=None):
    raw = _post('/v1/turn?mode=transcribe', pcm, env)
    for line in (raw or b'').splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get('event') == 'transcript':
            return str(item.get('text', '')).strip() or None
    return None


def clip(pcm, env=None):
    """POST /v2/enroll/clip: {quality, transcript, vector, model, ...} or None."""
    raw = _post('/v2/enroll/clip', pcm, env)
    try:
        reply = json.loads(raw) if raw else None
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


def voiceprint(pcm, env=None):
    raw = _post('/v1/voiceprint', pcm, env)
    try:
        return json.loads(raw)['print'] if raw else None
    except (ValueError, KeyError, TypeError):
        return None
