"""Known people: list, context menu, voice + passphrase authentication.

Menu "Personen" lists the voice profiles on the memory stick. E on a person
opens ACTIONS; every action first authenticates that person:

- After a beep the person speaks the passphrase (PHRASE_SECONDS). Silence
  around it is cut off, then the server returns a voiceprint and a
  transcript of the same take.
- Not recognized: one more try (ATTEMPTS). Every try is logged as an
  'auth' event (scores only, never the passphrase) for the journal.
- The voice must match the selected profile (cosine >= speaker.THRESHOLD)
  and the transcript the stored passphrase (fuzzy, Vosk spells differently
  and splits or joins words; extra words around the phrase are ignored).
- Passphrase right but the voice only weakly recognized (>= WEAK_VOICE):
  accepted, with the advice to retrain; the action list then offers
  "Nachtrainieren" first.
- Without a passphrase yet the voice alone counts once; the person is asked
  to set one.

The passphrase is kept in plain text in the profile on the stick (fuzzy
matching needs the text). Flows run in a thread like enroll.Session and use
the same io (say, beep, record, transcribe, voiceprint, publish).
"""
import difflib
import json
import re
import threading
import time
from pathlib import Path

ACTIONS = ('refine', 'details', 'passphrase', 'delete', 'back')
ACTION_LABELS = {'refine': 'Nachtrainieren', 'details': 'Details anzeigen',
                 'passphrase': 'Passphrase festlegen', 'delete': 'Löschen', 'back': 'Zurück'}
PHRASE_SECONDS = 6
ATTEMPTS = 2
PHRASE_MATCH = 0.75     # difflib ratio between spoken and stored passphrase
WEAK_VOICE = 0.3        # below speaker.THRESHOLD but still plausibly the same person
DELETE_CONFIRM_SECONDS = 20.0

AUTH_PROMPT = "Authentifizierung. Nach dem Signalton die Passphrase sprechen."
AUTH_VOICE_ONLY = "Authentifizierung. Nach dem Signalton einen Satz sprechen."
AUTH_FAILED = "Authentifizierung fehlgeschlagen."
AUTH_RETRY = "Nicht erkannt. Nach dem Signalton noch einmal."
AUTH_WEAK = "Passphrase korrekt. Stimme nur unsicher erkannt. Nachtrainieren empfohlen."
AUTH_OK = "Authentifiziert."
SET_PHRASE_FIRST = "Bitte Passphrase festlegen."
PHRASE_PROMPT = "Neue Passphrase nach dem Signalton sprechen."
PHRASE_REPEAT = "Zur Bestätigung noch einmal."
PHRASE_MISMATCH = "Die beiden Passphrasen stimmen nicht überein. Nichts geändert."
PHRASE_SAVED = "Passphrase gespeichert."
DELETE_ASK = "Profil wirklich löschen? Bestätigen mit Taste E, abbrechen mit B."
DELETED = "Profil gelöscht."
NO_SERVER = "Server nicht erreichbar. Authentifizierung nicht möglich."


def normalize(text):
    return ' '.join(re.findall(r'[a-zäöüß0-9]+', str(text).lower()))


def phrase_score(spoken, stored):
    """Best similarity of the stored phrase with the spoken words or any run
    of them (filler words around it); spaces are ignored, since Vosk splits
    and joins words differently from take to take."""
    spoken, stored = normalize(spoken).split()[:30], normalize(stored).replace(' ', '')
    if not spoken or not stored:
        return 0.0
    runs = [''.join(spoken[i:j]) for i in range(len(spoken)) for j in range(i + 1, len(spoken) + 1)]
    return max(difflib.SequenceMatcher(None, run, stored).ratio() for run in runs)


def phrase_matches(spoken, stored):
    return phrase_score(spoken, stored) >= PHRASE_MATCH


def phrases():
    """Fixed sentences, for prerecorded clips."""
    return [AUTH_PROMPT, AUTH_VOICE_ONLY, AUTH_FAILED, AUTH_RETRY, AUTH_WEAK, AUTH_OK, SET_PHRASE_FIRST,
            PHRASE_PROMPT, PHRASE_REPEAT, PHRASE_MISMATCH, PHRASE_SAVED, DELETE_ASK, DELETED,
            NO_SERVER]


def details_text(person):
    when = time.strftime('%d.%m.%Y', time.localtime(person.get('at') or 0))
    phrase = 'gesetzt' if person.get('passphrase') else 'nicht gesetzt'
    score = person.get('last_score')
    voice = f" Stimme zuletzt mit {round(score * 100)} Prozent erkannt." if score else ""
    return (f"{person['name']}: {person.get('count', 0)} Aufnahmen, zuletzt geändert am "
            f"{when}. Passphrase {phrase}.{voice}")


class Browser:
    """List and context-menu state for the display and the buttons."""

    def __init__(self):
        self.active = False
        self.page = 'list'        # 'list', 'actions', 'details'
        self.index = 0
        self.names = []
        self.person = None
        self.details = None
        self.weak = False         # last authentication: voice only weakly recognized
        self.delete_until = None

    def open(self, names):
        self.active, self.page, self.index = True, 'list', 0
        self.names, self.person, self.details = list(names) + ['Zurück'], None, None

    def close(self):
        self.active, self.page, self.person, self.delete_until = False, 'list', None, None

    @property
    def items(self):
        return self.names if self.page == 'list' else list(ACTIONS) if self.page == 'actions' else []

    def move(self, step):
        if self.items:
            self.index = (self.index + step) % len(self.items)

    def select(self):
        """E: returns ('person', name), ('action', action), ('close', None) or None."""
        if self.page == 'list':
            name = self.names[self.index]
            if name == 'Zurück':
                return ('close', None)
            self.person, self.page, self.index = name, 'actions', 0
            return ('person', name)
        if self.page == 'actions':
            action = ACTIONS[self.index]
            if action == 'back':
                self.page, self.index = 'list', 0
                return None
            return ('action', action)
        self.page, self.index = 'actions', 0  # details -> back to the actions
        return None

    def back(self):
        """B: one level up; True when the browser closed."""
        if self.page in ('actions', 'details'):
            self.page, self.index, self.details = ('list', 0, None) if self.page == 'actions' \
                else ('actions', 0, None)
            return False
        self.close()
        return True

    def view(self):
        """Plain data for the display (written to display-people.json)."""
        data = dict(page=self.page, index=self.index, person=self.person)
        if self.page == 'list':
            data['items'] = [n[:24] for n in self.names][:12]
        elif self.page == 'actions':
            labels = [ACTION_LABELS[a] for a in ACTIONS]
            if self.weak:
                labels[0] = 'Nachtrainieren!'  # recommended after a weak recognition
            data['items'] = labels
        else:
            data['details'] = {k: v for k, v in (self.details or {}).items()
                               if k in ('count', 'at', 'passphrase', 'last_score')}
        data['confirm_delete'] = self.delete_until is not None
        return data


class Flow:
    """Authenticate, then run one action; same interface as enroll.Session."""

    def __init__(self, core, io, name, action, done=None):
        self.core, self.io, self.name, self.action = core, io, name, action
        self.done = done              # callback(result) in the flow thread
        self.cancelled = threading.Event()
        self.thread = None
        self.result = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, name='people', daemon=True)
        self.thread.start()

    def cancel(self):
        self.cancelled.set()
        abort = getattr(self.io, 'abort', None)
        if abort is not None:
            abort()

    def _run(self):
        try:
            self.result = self.run()
        except Exception as exc:
            self.result = dict(error=str(exc))
        finally:
            self.io.publish(stage=None)
            if self.done is not None:
                self.done(self.result)

    def _take(self, prompt, path):
        self.io.say(prompt)
        if self.cancelled.is_set():
            return None
        self.io.beep()
        self.io.publish(stage='auth', rec=True)
        ok = self.io.record(path, PHRASE_SECONDS)
        self.io.publish(stage='auth', rec=False)
        if ok is False or self.cancelled.is_set() or not Path(path).is_file():
            return None
        from enroll import pcm_of
        return pcm_of(path)

    def authenticate(self):
        """'ok', 'weak' or None (failed)."""
        from speaker import THRESHOLD, cosine, decode, trim_silence
        person = self.core.profile(self.name)
        if person is None:
            return None
        stored = person.get('passphrase')
        path = self.core.voice_dir / 'auth.wav'
        prompt = AUTH_PROMPT if stored else AUTH_VOICE_ONLY
        best = None
        for attempt in range(1, ATTEMPTS + 1):
            pcm = self._take(prompt, path)
            Path(path).unlink(missing_ok=True)   # nothing of the passphrase stays on disk
            if pcm is None:
                return None
            pcm = trim_silence(pcm)
            print_ = self.io.voiceprint(pcm)
            if print_ is None:
                self.io.say(NO_SERVER)
                return None
            score = cosine(decode(print_), decode(person['print'])) or 0.0
            best = score if best is None else max(best, score)
            phrase = phrase_score(self.io.transcribe(pcm) or '', stored) if stored else None
            phrase_ok = phrase is None or phrase >= PHRASE_MATCH
            if phrase_ok and score >= THRESHOLD:
                state = 'ok'
            elif phrase_ok and stored and score >= WEAK_VOICE:
                state = 'weak'
            else:
                state = None
            print(json.dumps(dict(version=1, event='auth', person=self.name, attempt=attempt,
                                  voice=round(score, 3), threshold=THRESHOLD,
                                  phrase=None if phrase is None else round(phrase, 2),
                                  seconds=round(len(pcm) / 32000, 1), result=state or 'failed')),
                  flush=True)
            if state is not None or self.cancelled.is_set():
                break
            if attempt < ATTEMPTS:
                prompt = AUTH_RETRY
        self.core.update_profile(self.name, last_score=round(best, 3))
        self.io.say({'ok': AUTH_OK, 'weak': AUTH_WEAK}.get(state, AUTH_FAILED))
        return state

    def run(self):
        self.io.publish(stage='auth', rec=False)
        state = self.authenticate()
        if state is None:
            return dict(auth=False)
        result = dict(auth=True, weak=state == 'weak')
        person = self.core.profile(self.name)
        if not person.get('passphrase') and self.action != 'passphrase':
            self.io.say(SET_PHRASE_FIRST)
            result['action'] = 'passphrase'
            result['passphrase'] = self.set_passphrase()
            return result
        result['action'] = self.action
        if self.action == 'details':
            result['details'] = self.core.profile(self.name)
            self.io.say(details_text(result['details']))
        elif self.action == 'passphrase':
            result['passphrase'] = self.set_passphrase()
        elif self.action == 'delete':
            result['delete_pending'] = True   # the controller asks for E
            self.io.say(DELETE_ASK)
        elif self.action == 'refine':
            result['refine'] = True           # the controller starts the session
        return result

    def set_passphrase(self):
        first = self._phrase(PHRASE_PROMPT)
        second = self._phrase(PHRASE_REPEAT) if first else None
        if not first or not second or not phrase_matches(first, second):
            self.io.say(PHRASE_MISMATCH)
            return False
        self.core.update_profile(self.name, passphrase=normalize(first))
        self.io.say(PHRASE_SAVED)
        return True

    def _phrase(self, prompt):
        path = self.core.voice_dir / 'phrase.wav'
        pcm = self._take(prompt, path)
        Path(path).unlink(missing_ok=True)
        return self.io.transcribe(pcm) if pcm else None


def write_view(path, view):
    temporary = Path(path).with_suffix('.tmp')
    temporary.write_text(json.dumps(view, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
