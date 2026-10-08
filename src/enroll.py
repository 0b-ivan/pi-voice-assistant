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
_COMMAND = re.compile(r'\b(lerne? mich kennen|kennenlernen|kennen lernen|trainingsmodus|'
                      r'stimmtraining|stimmprofil (anlegen|erstellen))\b')
_NAME_PREFIX = re.compile(r'^(?:du kannst mich |nenn(?:e)? mich |ich heiße |mein name ist |'
                          r'ich bin |sag |einfach )+')


def command(text):
    return 'enroll' if _COMMAND.search(str(text).lower()) else None


def done_text(facts, voiceprint):
    voice = " Stimmprofil gespeichert." if voiceprint else ""
    return f"Kennenlernen abgeschlossen. {facts} Erinnerungen angelegt.{voice}"


def phrases():
    """Fixed sentences, for prerecorded clips."""
    texts = [ANNOUNCE, INTRO, QUESTIONS_INTRO, CANCELLED, FAILED, NEED_STICK, NEED_SERVER, NO_VOICEPRINT,
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
    with wave.open(str(path), 'rb') as clip:
        return clip.readframes(clip.getnframes())


class Session:
    """Runs the steps; ``io`` provides say(text), beep(), record(path, seconds),
    transcribe(pcm) -> text|None, voiceprint(pcm) -> base64|None, publish(**state)."""

    def __init__(self, core, io, clock=time.time):
        self.core, self.io, self.clock = core, io, clock
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
        self.io.say(INTRO)
        for index in range(WAKE_COUNT):
            self._check()
            if index in WAKE_HINTS:
                self.io.publish(stage='wake', step=index, total=WAKE_COUNT, rec=False)
                self.io.say(WAKE_HINTS[index])
            self.io.beep()
            self.io.publish(stage='wake', step=index + 1, total=WAKE_COUNT, rec=True)
            if self.io.record(wake_dir / f'{stamp}-{index + 1:02d}.wav', WAKE_SECONDS) is False:
                self._check()
                raise RuntimeError('microphone unavailable')
            self.io.publish(stage='wake', step=index + 1, total=WAKE_COUNT, rec=False)
        self._check()
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
            pcm = pcm_of(path)
            answer = self.io.transcribe(pcm)
            if answer:
                if key == 'name':
                    name = name_from(answer)
                    answer = name
                if self.core.add(kind, template.format(answer)):
                    stored += 1
            print_ = self.io.voiceprint(pcm)
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
