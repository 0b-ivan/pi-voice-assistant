"""Getting to know the operator: wake-word samples, questions, voiceprint.

One session (menu "Kennenlernen" or "Lerne mich kennen"):

1. After a beep the operator says "Proximus" WAKE_COUNT times (2 s each,
   with hints: quieter, from further away ...). The clips go to the memory
   stick (proximus/voice/wake/) for retraining the wake word.
2. Proximus asks QUESTIONS; each answer (6 s after a beep) is transcribed by
   the server (POST /v1/turn?mode=transcribe) and stored as a fact or, for
   the speaking style, as a directive. Clips: proximus/voice/answers/.
3. The server turns the answer clips into voice embeddings
   (POST /v1/voiceprint); their mean becomes the operator's voiceprint on
   the stick, used to recognize the operator in later turns.

Every clip goes through voice_quality (the path the server uses for turns):
the WAV format is checked, then its numbers are logged as an 'enroll_clip'
event (no content). Clips without speech or with a bad format are deleted;
clips that fail the enrollment limits (too little speech, clipping, too
quiet, noisy, too many pauses) still give their transcript but no voice
embedding. Until the room noise is measured on its own (Etappe 4), a missing
SNR estimate is logged, not held against the clip.

Everything stays on the stick; button B cancels at any time. The session
runs in its own thread and talks to the hardware through small callables,
so it can be tested without a microphone.
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
INTRO = "Kennenlern-Sitzung beginnt. Abbrechen jederzeit mit Taste B."
QUESTIONS_INTRO = "Stimmproben erfasst. Nun einige Fragen. Antworte nach dem Signalton."
CANCELLED = "Kennenlern-Sitzung abgebrochen."
NEED_STICK = "Kennenlernen braucht den Gedächtniskern. Bitte Stick anschließen."
NEED_SERVER = "Kennenlernen braucht den Server. Verbindung prüfen."
NO_VOICEPRINT = "Stimmprofil konnte nicht erstellt werden."
FAILED = "Kennenlern-Sitzung abgebrochen. Mikrofon nicht verfügbar."
REFINE_INTRO = "Stimmprofil nachtrainieren. Sage nach jedem Signalton Proximus. Abbrechen mit Taste B."
NO_PROFILE = "Noch kein Stimmprofil vorhanden. Erst Kennenlernen starten."
NO_MATCH = "Stimme keinem bekannten Profil zuzuordnen. Für eine neue Person Kennenlernen starten."
REFINE_MATCH = 0.4      # lower than for recognition: we already expect a known voice
# Problems that keep an enrollment clip out of the voiceprint.
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


def refined_text(name, total):
    return f"Stimmprofil {name} verfeinert. {total} Aufnahmen."



def done_text(facts, voiceprint):
    voice = " Stimmprofil gespeichert." if voiceprint else ""
    return f"Kennenlernen abgeschlossen. {facts} Erinnerungen angelegt.{voice}"


def phrases():
    """Fixed sentences, for prerecorded clips."""
    texts = [ANNOUNCE, INTRO, QUESTIONS_INTRO, CANCELLED, FAILED, REFINE_INTRO, NO_PROFILE,
             NO_MATCH, refined_text('Ivan', 2), NEED_STICK, NEED_SERVER, NO_VOICEPRINT,
             *WAKE_HINTS.values(), *(q for _, q, _, _ in QUESTIONS)]
    texts += [done_text(n, v) for n in (2,) for v in (True, False)]
    return texts


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
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self.clock()))
        wake_dir = self.core.voice_dir / 'wake'
        answer_dir = self.core.voice_dir / 'answers'
        wake_dir.mkdir(parents=True, exist_ok=True)
        answer_dir.mkdir(parents=True, exist_ok=True)
        self.io.publish(stage='intro')
        self.io.say(REFINE_INTRO if self.mode == 'refine' else INTRO)
        takes = []
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
            takes.append(take)
            self.io.publish(stage='wake', step=index + 1, total=WAKE_COUNT, rec=False)
        self._check()
        if self.mode == 'refine':
            return self._refine(takes)
        self.io.say(QUESTIONS_INTRO)
        stored, prints, name = 0, [], None
        for number, (key, question, kind, template) in enumerate(QUESTIONS):
            self._check()
            self.io.publish(stage='ask', step=number, total=len(QUESTIONS), rec=False)
            self.io.say(question)
            self.io.beep()
            path = answer_dir / f'{stamp}-{key}.wav'
            self.io.publish(stage='ask', step=number, total=len(QUESTIONS), rec=True)
            if self.io.record(path, ANSWER_SECONDS) is False:
                self._check()
                raise RuntimeError('microphone unavailable')
            self.io.publish(stage='ask', step=number, total=len(QUESTIONS), rec=False)
            self._check()
            pcm = _read(path, 'answer', number)
            if pcm is None:
                continue
            quality, rejected = clip_check(pcm, 'answer', number)
            if not quality['speech_seconds']:
                path.unlink(missing_ok=True)   # nothing said: nothing to keep
                continue
            answer = self.io.transcribe(pcm)
            if answer:
                if key == 'name':
                    name = name_from(answer)
                    answer = name
                if self.core.add(kind, template.format(answer)):
                    stored += 1
            print_ = self.io.voiceprint(pcm) if not rejected else None
            if print_:
                prints.append(print_)
        self.io.publish(stage='process')
        voiceprint = False
        if prints:
            from speaker import average, decode, encode
            vector = average([decode(p) for p in prints])
            if vector:
                self.core.save_voiceprint(name or 'Bediener', encode(vector), len(prints))
                voiceprint = True
        self.io.publish(stage='done')
        self.io.say(done_text(stored, voiceprint) if prints else
                    done_text(stored, False) + " " + NO_VOICEPRINT)
        self.io.publish(stage=None)
        return dict(facts=stored, voiceprint=voiceprint, name=name)


    def _refine(self, takes):
        """Five takes at a time (~10 s) give one embedding; their mean is merged
        into the profile it matches best."""
        from speaker import average, decode, encode, identify, trim_silence
        self.io.publish(stage='process')
        prints = []
        for start in range(0, len(takes), 5):
            # Each 2 s take holds ~0.7 s of "Proximus"; without its pauses the
            # five words are speech only, like the server's trimmed turns.
            parts = []
            for index, path in enumerate(takes[start:start + 5], start):
                pcm = _read(path, 'wake', index) if path.is_file() else None
                if pcm is None:
                    continue
                _, rejected = clip_check(pcm, 'wake', index, WAKE_PROBLEMS,
                                         WAKE_SPEECH_MIN, voice_quality.SPEECH_MAX)
                if not rejected:
                    parts.append(trim_silence(pcm))
            pcm = b''.join(parts)
            print_ = self.io.voiceprint(pcm) if pcm else None
            if print_:
                prints.append(print_)
        known = [dict(name=p['name'], vector=decode(p['print'])) for p in self.core.voiceprints()]
        vector = average([decode(p) for p in prints])
        if self.target and vector and any(p['name'] == self.target for p in known):
            name, score = self.target, None
        else:
            name, score = identify(vector, known, REFINE_MATCH) if vector else (None, None)
        self.io.publish(stage='done')
        if not known:
            text, total = NO_PROFILE, None
        elif name is None:
            text, total = NO_MATCH, None
        else:
            total = self.core.save_voiceprint(name, encode(vector), len(prints))
            text = refined_text(name, total)
        self.io.say(text)
        self.io.publish(stage=None)
        return dict(refined=name, score=None if score is None else round(score, 3), count=total)


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


def voiceprint(pcm, env=None):
    raw = _post('/v1/voiceprint', pcm, env)
    try:
        return json.loads(raw)['print'] if raw else None
    except (ValueError, KeyError, TypeError):
        return None
