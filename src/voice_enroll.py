"""Enrollment v2: the voice part, optional baseline and facts, one draft.

Concept stimm-einlernen v2, sections 3-6 (Etappe 4). One session runs in its
own thread and talks to the hardware through ``io`` (say, beep, record,
abort, clip, transcribe, publish), so it can be tested with a fake clock:

1. Introduction and technical check: three seconds of room noise (the SNR of
   every take is measured against it).
2. Voice part: 24 sentences from enroll_phrases.plan(), eight per condition
   (near, far, quiet); 18 anchors build the three centers, 6 held-out
   checks (the last two are challenges) stay outside. Every take is judged
   at once: voice_quality limits, then the server's transcript against the
   sentence (WER) only for held-out checks, then the embedding. Technical
   quality protects every clip. Every failed check may be repeated twice;
   a third failure pauses without throwing away previous accepted samples.
   At least MIN_SPEECH seconds of speech in all.
3. Optional baseline (twelve call-and-answer pairs): numbers only, never in
   the profile.
4. Optional facts (the seven questions): kept as a draft.
5. Profile: centers, leave-one-out and impostor calibration
   (voice_profile.calibrate) against the reference samples on the stick, if
   any; without them the profile stays 'provisional'.
6. Confirmation: button E stores the draft (v2 profile, compatibility v1
   print, facts, accepted audio); B or the deadline pauses the draft for up
   to 24 hours; the previous profile stays until final E.

Hard limit DEADLINE seconds *per session* including the confirmation;
optional parts are not started when the rest would not fit. B cancels in every phase, also
while a server call is running (calls run in a helper thread, a late answer
is dropped). Logs carry numbers and codes only, never text or names.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

import enroll_phrases
import voice_profile
import voice_quality

DEADLINE = 420.0
MIN_SPEECH = 45.0  # 24 short samples, >=2 s speech each
RETRIES_PER_SENTENCE = 2  # retries are local to a single task, never global
RESUME_SECONDS = 24 * 60 * 60
CONFIRM_SECONDS = 30.0
NOISE_SECONDS = 3
BASELINE_SECONDS = 4
ANSWER_SECONDS = 6
BASELINE_BUDGET = 65.0
FACTS_BUDGET = 70.0
CLOSE_BUDGET = 20.0          # profile, confirmation, storing
TAKE_OVERHEAD = 4.0          # beep, prompt, server per take (budget, not measured)
AUDIO_LIMIT = 20 * 1024 * 1024
REFERENCE_FILE = 'reference-v2.json'

INTRO = "Diese Einheit lernt deine Stimme. Sprich wie im Alltag. Taste B pausiert das Kennenlernen."
NOISE_PROMPT = "Bitte kurz still sein. Die Einheit misst den Raum."
BASELINE_INTRO = ("Rhythmusprüfung. Höre zu. Wiederhole die angezeigte Antwort. "
                  "Deine gewöhnliche Stimme genügt.")
FACTS_INTRO = "Stimmteil beendet. Nun kannst du persönliche Angaben ergänzen."
CHECKING = "Die Einheit prüft die Zuordnung."
CONFIRM = "Stimmprofil geprüft. Taste E übernimmt, Taste B verwirft."
CONFIRM_FACTS = "Angaben erfasst. Taste E übernimmt, Taste B verwirft."
EXTRA_CONFIRMATION = "Zusatzbestätigung erforderlich."
DONE = "Stimmprofil gespeichert. Die Erkennung wird im Alltag geprüft."
DONE_FACTS = "Angaben gespeichert."
CANCELLED = "Vorgang abgebrochen. Das bisherige Profil bleibt erhalten."
UNSUITABLE = "Aufnahmebedingungen ungeeignet. Das bisherige Profil bleibt erhalten."
TOO_LITTLE = "Zu wenig Sprache erfasst. Das bisherige Profil bleibt erhalten."
TIMEOUT = "Zeit abgelaufen. Das bisherige Profil bleibt erhalten."
NO_SERVER = "Server nicht erreichbar. Das bisherige Profil bleibt erhalten."
CONTENT_HINT = "Die Wörter wurden nicht sicher verstanden. Lies den neuen Satz vollständig."
PAUSED = "Kennenlernen pausiert. Deine Aufnahmen bleiben einen Tag gespeichert."


def phrases():
    """Fixed sentences, for prerecorded clips."""
    return [INTRO, NOISE_PROMPT, BASELINE_INTRO, FACTS_INTRO, CHECKING, CONFIRM, CONFIRM_FACTS,
            EXTRA_CONFIRMATION, DONE, DONE_FACTS, CANCELLED, UNSUITABLE, TOO_LITTLE, TIMEOUT,
            NO_SERVER, CONTENT_HINT, PAUSED, *voice_quality.HINTS.values(),
            *enroll_phrases.CONDITION_HINTS.values()]


class _Cancelled(Exception):
    pass


class _Stop(Exception):
    """Ends the session without storing; ``text`` is said, ``code`` returned."""

    def __init__(self, code, text):
        super().__init__(code)
        self.code, self.text = code, text


def log(**fields):
    print(json.dumps(dict(version=1, **fields)), flush=True)


class VoiceSession:
    """mode 'new' (a new person), 'add' (record the voice part again for
    ``target``) or 'facts' (questions only). ``target``: {name, profile_id}."""

    def __init__(self, core, io, mode='new', target=None, facts=None, baseline=False,
                 clock=time.monotonic, rng=None, wall=time.time):
        self.core, self.io, self.mode = core, io, mode
        self.target = dict(target or {})
        self.voice = mode in ('new', 'add')
        self.facts = (mode == 'new') if facts is None else facts
        if mode == 'facts':
            self.facts = True
        self.baseline = baseline and self.voice
        self.clock, self.wall = clock, wall
        self.rng = rng or enroll_phrases.system_random()
        self.cancelled = threading.Event()
        self.pressed = threading.Event()
        self.thread = None
        self.result = None
        self.started = None
        self._resume_state = None
        self._complete = False
        self._draft_created = None

    # --- control from the controller thread ---------------------------------

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        self.thread = threading.Thread(target=self._run, name='voice-enroll', daemon=True)
        self.thread.start()

    def cancel(self):
        """B: stop at once; the display switches before any cleanup runs."""
        self.cancelled.set()
        self.io.publish(stage=None)
        abort = getattr(self.io, 'abort', None)
        if abort is not None:
            abort()

    def press(self, button):
        if button == 'E':
            self.pressed.set()

    # --- helpers --------------------------------------------------------------

    def _check(self):
        if self.cancelled.is_set():
            raise _Cancelled()

    def elapsed(self):
        return self.clock() - self.started

    def remaining(self):
        return DEADLINE - self.elapsed()

    def _fits(self, seconds):
        if self.remaining() < seconds:
            raise _Stop('timeout', TIMEOUT)

    def _publish(self, **state):
        if not self.cancelled.is_set():
            self.io.publish(**state)

    def _say(self, text):
        self._check()
        self.io.say(text)
        self._check()

    def _call(self, function, *args):
        """Run a (network) call in a helper thread; B or the deadline end the
        wait at once and a late answer is dropped."""
        box = {}

        def work():
            try:
                box['value'] = function(*args)
            except Exception as exc:  # reported as "no answer"
                box['error'] = type(exc).__name__

        worker = threading.Thread(target=work, name='voice-enroll-call', daemon=True)
        worker.start()
        while worker.is_alive():
            self._check()
            if self.remaining() <= 0:
                raise _Stop('timeout', TIMEOUT)
            worker.join(0.05)
        self._check()
        return box.get('value')

    def _record(self, path, seconds):
        self._check()
        self.io.beep()
        self._check()
        self._publish(rec=True)
        ok = self.io.record(path, seconds)
        self._publish(rec=False)
        self._check()
        if ok is False or not Path(path).is_file():
            raise _Stop('microphone', UNSUITABLE)
        try:
            return voice_quality.read_wav(path)
        except voice_quality.AudioFormatError:
            Path(path).unlink(missing_ok=True)
            return None

    # --- draft checkpoint / restart (USB stick, no server-side identity) ----

    def _prepare_draft(self, stamp):
        """Find an unexpired draft for the selected mode and profile.
        Expired drafts are removed without touching committed profiles."""
        base = self.core.voice_dir / 'drafts'
        base.mkdir(parents=True, exist_ok=True)
        now = self.wall()
        if self.voice:
            for folder in sorted(base.iterdir(), reverse=True):
                if not folder.is_dir():
                    continue
                path = folder / 'progress.json'
                try:
                    state = json.loads(path.read_text(encoding='utf-8'))
                    created = float(state['created'])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                if now - created > RESUME_SECONDS or created > now + 60:
                    shutil.rmtree(folder, ignore_errors=True)
                    continue
                if (state.get('version') != 1 or state.get('mode') != self.mode
                        or state.get('target_id') != self.target.get('profile_id')
                        or not isinstance(state.get('tasks'), list)
                        or len(state['tasks']) != 24
                        or not isinstance(state.get('accepted'), list)):
                    continue
                self._resume_state = state
                self._draft_created = created
                self.draft = folder
                log(event='voice_enroll', result='resumed',
                    accepted=len(state['accepted']))
                return folder.name
        self._draft_created = now
        self.draft = base / stamp
        self.draft.mkdir(mode=0o700, parents=True, exist_ok=True)
        return stamp

    def _save_progress(self, tasks, accepted):
        """Atomic private USB checkpoint; profile still requires button E."""
        state = dict(version=1, mode=self.mode,
                     target_id=self.target.get('profile_id'),
                     created=self._draft_created, tasks=tasks,
                     model=self.model,
                     accepted=[dict(index=a['index'], vector=a['vector'],
                                    quality=a['quality'], wer=a.get('wer'))
                               for a in accepted])
        target = self.draft / 'progress.json'
        temporary = target.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as output:
            json.dump(state, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)

    def _restore_accepted(self, tasks):
        accepted = []
        state = self._resume_state
        if not state:
            return accepted
        try:
            self.model = tuple(state['model']) if state['model'] else None
            for data in state['accepted']:
                index = data['index']
                if not isinstance(index, int) or not 0 <= index < len(tasks):
                    continue
                item = tasks[index]
                # Never trust persisted paths; reconstruct from the plan.
                path = self.draft / (
                    f"{index + 1:02d}-{item['condition']}-{item['role']}.wav")
                if not path.is_file() or any(a['index'] == index for a in accepted):
                    continue
                if not isinstance(data['quality'], dict) or not data['vector']:
                    continue
                accepted.append(dict(item, vector=data['vector'],
                                     quality=data['quality'], path=path,
                                     wer=data.get('wer')))
        except (KeyError, ValueError, TypeError, IndexError):
            return []
        return accepted

    # --- run ------------------------------------------------------------------

    def _run(self):
        try:
            self.result = self.run()
        except Exception as exc:  # never take the voice service down
            log(event='voice_enroll', result='error', error=type(exc).__name__)
            self.io.publish(stage=None)
            self.result = dict(error=type(exc).__name__)

    def run(self):
        self.started = self.clock()
        stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(self.wall()))
        stamp = self._prepare_draft(stamp)
        try:
            return self._session(stamp)
        except _Cancelled:
            self.io.publish(stage=None)
            if self.voice and (self.draft / 'progress.json').is_file():
                log(event='voice_enroll', result='paused')
                self._quiet_say(PAUSED)
            else:
                self._quiet_say(CANCELLED)
            return dict(cancelled=True)
        except _Stop as stop:
            self.io.publish(stage=None)
            log(event='voice_enroll', result=stop.code, elapsed=round(self.elapsed(), 1))
            self._quiet_say(stop.text)
            return dict(stored=False, reason=stop.code)
        finally:
            # Kept takes were moved out on commit; everything else goes.
            if self._complete or not (self.draft / 'progress.json').is_file():
                shutil.rmtree(self.draft, ignore_errors=True)
            self.io.publish(stage=None)

    def _quiet_say(self, text):
        try:
            self.io.say(text)
        except Exception:
            pass

    def _session(self, stamp):
        self._publish(stage='intro')
        self._say(INTRO if self.voice else FACTS_INTRO)
        accepted, noise, profile, calibration = [], None, None, None
        if self.voice:
            self._publish(stage='check')
            self._say(NOISE_PROMPT)
            noise = self._record(self.draft / 'noise.wav', NOISE_SECONDS)
            (self.draft / 'noise.wav').unlink(missing_ok=True)
            if noise is None:
                raise _Stop('noise_invalid', UNSUITABLE)
            room = voice_quality.noise_profile(noise)
            # Only technical metrics, never raw audio or spoken content.
            log(event='enroll_noise', seconds=NOISE_SECONDS,
                usable_seconds=room['noise_seconds'], noise_dbfs=room['noise_dbfs'],
                peak_dbfs=room['noise_peak_dbfs'], spikes=room['noise_spikes'],
                valid=room['valid'])
            if not room['valid']:
                raise _Stop('noise_invalid', UNSUITABLE)
            accepted = self._voice_part(noise)
            if self.baseline and self.remaining() > (BASELINE_BUDGET + CLOSE_BUDGET
                                                     + (FACTS_BUDGET if self.facts else 0)):
                self._baseline_part(noise)
        drafts = []
        if self.facts and self.remaining() > FACTS_BUDGET + CLOSE_BUDGET:
            if self.voice:
                self._say(FACTS_INTRO)
            drafts = self._facts_part()
        if self.voice:
            self._publish(stage='process')
            self._say(CHECKING)
            profile, calibration = self._build(accepted)
        provisional = profile is not None and profile['status'] != 'calibrated'
        self.pressed.clear()        # only a press from now on counts
        self._publish(stage='confirm')
        text = CONFIRM if self.voice else CONFIRM_FACTS
        self._say(text + (" " + EXTRA_CONFIRMATION if provisional else ""))
        self._wait_confirm()
        stored = self._commit(stamp, accepted, profile, drafts)
        self._complete = True
        self._publish(stage='done')
        self._say(DONE if self.voice else DONE_FACTS)
        result = dict(stored=True, facts=stored['facts'], profile_id=stored.get('profile_id'),
                      status=profile['status'] if profile else None,
                      reason=(calibration or {}).get('reason'),
                      speech=round(sum(a['quality']['speech_seconds'] for a in accepted), 1),
                      elapsed=round(self.elapsed(), 1))
        log(event='voice_enroll', result='stored', status=result['status'],
            calibration=result['reason'], speech=result['speech'], elapsed=result['elapsed'],
            anchors=sum(a['role'] == 'anchor' for a in accepted),
            checks=sum(a['role'] == 'check' for a in accepted))
        return result

    # --- voice part --------------------------------------------------------------

    def _voice_part(self, noise):
        tasks = (self._resume_state['tasks'] if self._resume_state
                 else enroll_phrases.plan(self.rng))
        used = {t['text'] for t in tasks}
        self.model = None
        accepted = self._restore_accepted(tasks)
        recorded = {item['index'] for item in accepted}
        self._save_progress(tasks, accepted)
        condition = None
        for item in tasks:
            if item['condition'] != condition:
                condition = item['condition']
                self._publish(stage='voice', step=item['index'] + 1, total=len(tasks),
                              condition=condition, text=None)
                self._say(enroll_phrases.CONDITION_HINTS[condition])
            if item['index'] in recorded:
                continue
            take = self._take_with_repeat(item, len(tasks), noise, used)
            accepted.append(take)
            recorded.add(take['index'])
            tasks[take['index']] = {
                k: take[k] for k in ('index', 'condition', 'role',
                                    'length', 'pattern', 'text', 'words')}
            self._save_progress(tasks, accepted)
        if sum(a['quality']['speech_seconds'] for a in accepted) < MIN_SPEECH:
            raise _Stop('paused', TOO_LITTLE)
        return accepted

    def _take_with_repeat(self, item, total, noise, used):
        for attempt in range(RETRIES_PER_SENTENCE + 1):
            self._fits(enroll_phrases.RECORD_SECONDS[item['length']] + TAKE_OVERHEAD
                       + CLOSE_BUDGET)
            take, problem = self._take(item, total, noise)
            if take is not None:
                return take
            if attempt >= RETRIES_PER_SENTENCE:
                raise _Stop('paused', PAUSED)
            self._say(voice_quality.HINTS.get(problem, CONTENT_HINT))
            item = enroll_phrases.replacement(item, self.rng, used)
            used.add(item['text'])

    def _take(self, item, total, noise):
        """(accepted take, None) or (None, problem code)."""
        index = item['index']
        self._publish(stage='voice', step=index + 1, total=total, condition=item['condition'],
                      text=item['text'])
        path = self.draft / f"{index + 1:02d}-{item['condition']}-{item['role']}.wav"
        pcm = self._record(path, enroll_phrases.RECORD_SECONDS[item['length']])
        if pcm is None:
            return self._reject(item, path, 'format', None)
        quality = voice_quality.analyze(pcm, noise=noise)
        problems = voice_quality.problems(quality, speech_ratio_min=0.50)
        if problems:
            return self._reject(item, path, problems[0], quality)
        self._publish(stage='voice', step=index + 1, total=total, condition=item['condition'],
                      text=item['text'], rec=False, checking=True)
        reply = self._call(self.io.clip, pcm)
        if not isinstance(reply, dict):
            raise _Stop('server', NO_SERVER)
        content = enroll_phrases.check(item, reply.get('transcript'))
        # Anchors learn vocal characteristics, not transcripts: tolerate
        # Vosk word errors when audio quality and the embedding are valid.
        # Held-out checks and challenge words remain strictly verified.
        if not content['ok'] and item['role'] == 'check':
            return self._reject(item, path, 'content', quality, content['wer'])
        try:
            vector = voice_profile.decode(reply.get('vector') or '')
        except voice_profile.ProfileError:
            return self._reject(item, path, 'too_short', quality, content['wer'])
        model = (reply.get('model'), reply.get('preprocessing'), len(vector))
        if self.model is None:
            self.model = model
        elif model != self.model:
            raise _Stop('model_changed', NO_SERVER)   # never mix models in one profile
        log(event='enroll_clip', kind='voice', index=index, condition=item['condition'],
            role=item['role'], result='accepted', text_ok=content['ok'],
            wer=content['wer'], **quality)
        return dict(item, vector=vector, quality=quality, path=path, wer=content['wer']), None

    def _reject(self, item, path, problem, quality, wer=None):
        Path(path).unlink(missing_ok=True)          # rejected takes never stay
        log(event='enroll_clip', kind='voice', index=item['index'], condition=item['condition'],
            role=item['role'], result=problem, wer=wer, **(quality or {}))
        return None, problem

    # --- baseline and facts ---------------------------------------------------

    def _baseline_part(self, noise):
        self._publish(stage='baseline', step=0, total=len(enroll_phrases.BASELINE), text=None)
        self._say(BASELINE_INTRO)
        for number, (prompt, answer, words) in enumerate(enroll_phrases.baseline(self.rng), 1):
            if self.remaining() < BASELINE_SECONDS + TAKE_OVERHEAD + CLOSE_BUDGET:
                break
            self._publish(stage='baseline', step=number, total=len(enroll_phrases.BASELINE),
                          text=answer)
            self._say(prompt)
            path = self.draft / f'baseline-{number:02d}.wav'
            pcm = self._record(path, BASELINE_SECONDS)
            Path(path).unlink(missing_ok=True)      # baseline takes never enter the profile
            if pcm is None:
                continue
            quality = voice_quality.analyze(pcm, noise=noise)
            mask, _ = voice_quality.speech_mask(pcm)
            onset = next((i for i, flag in enumerate(mask) if flag), None)
            reply = self._call(self.io.clip, pcm) if quality['speech_seconds'] else None
            content = enroll_phrases.check(dict(text=answer, words=words),
                                           (reply or {}).get('transcript'))
            log(event='enroll_clip', kind='baseline', index=number, wer=content['wer'],
                result='ok' if content['ok'] else 'content',
                onset=None if onset is None else round(onset * voice_quality.FRAME_SECONDS, 2),
                words_per_second=(round(len(answer.split()) / quality['speech_seconds'], 2)
                                  if quality['speech_seconds'] else None), **quality)

    def _facts_part(self):
        import enroll
        drafts = []
        for number, (key, question, kind, template) in enumerate(enroll.QUESTIONS):
            if self.remaining() < ANSWER_SECONDS + TAKE_OVERHEAD + CLOSE_BUDGET:
                break
            self._publish(stage='ask', step=number, total=len(enroll.QUESTIONS))
            self._say(question)
            path = self.draft / f'answer-{key}.wav'
            pcm = self._record(path, ANSWER_SECONDS)
            Path(path).unlink(missing_ok=True)      # answers are kept as text only
            if pcm is None or not voice_quality.speech_seconds(pcm):
                continue                            # nothing said: question skipped
            answer = self._call(self.io.transcribe, pcm)
            if not answer:
                continue
            if key == 'name':
                answer = enroll.name_from(answer)
                self.target.setdefault('name', answer)
            drafts.append((kind, template.format(answer)))
        return drafts

    # --- profile, confirmation, commit -----------------------------------------

    def _store(self):
        return voice_profile.ProfileStore(self.core.voice_dir / 'profiles-v2')

    def _build(self, accepted):
        anchors = [dict(condition=a['condition'], vector=a['vector'])
                   for a in accepted if a['role'] == 'anchor']
        checks = [dict(condition=a['condition'], vector=a['vector'])
                  for a in accepted if a['role'] == 'check']
        model, _, dim = self.model
        calibration = voice_profile.calibrate(anchors, checks, self._impostors(model, dim))
        store = self._store()
        current = store.load(self.target['profile_id']) if self.target.get('profile_id') else None
        name = self.target.get('name') or (current or {}).get('name') or 'Bediener'
        fresh = voice_profile.new_profile(name, anchors, checks, model=model, dim=dim)
        if current is not None:
            # The same person again: keep id and name, new anchors and centers.
            fresh = dict(fresh, profile_id=current['profile_id'], name=current['name'])
        elif self.target.get('profile_id'):
            fresh['profile_id'] = self.target['profile_id']     # the v1 profile's id
        profile = voice_profile.apply_calibration(fresh, calibration)
        log(event='voice_profile', status=profile['status'], reason=calibration['reason'],
            tau=calibration['tau'], impostors=calibration['impostor_count'],
            genuine_min=min(calibration['genuine']) if calibration['genuine'] else None)
        return profile, calibration

    def _impostors(self, model, dim):
        """Reference samples of other people on the stick (voice/reference-v2.json:
        [{source, vector, model_sha256}]), only of the same model."""
        path = self.core.voice_dir / REFERENCE_FILE
        try:
            items = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        found = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict) or not isinstance(model, dict):
                continue
            if item.get('model_sha256') != model.get('sha256') or not item.get('source'):
                continue
            try:
                found.append(dict(source=str(item['source']),
                                  vector=voice_profile.decode(item['vector'], dim)))
            except (voice_profile.ProfileError, KeyError):
                continue
        return found

    def _wait_confirm(self):
        """Button E within CONFIRM_SECONDS and before the deadline."""
        limit = min(CONFIRM_SECONDS, self.remaining())
        if limit <= 0:
            raise _Stop('timeout', TIMEOUT)
        until = self.clock() + limit
        while not self.pressed.is_set():
            self._check()
            if self.clock() >= until:
                raise _Stop('timeout' if self.remaining() <= 0 else 'not_confirmed', TIMEOUT)
            self.pressed.wait(0.05)
        self._check()

    def _commit(self, stamp, accepted, profile, drafts):
        stored = dict(facts=0)
        if profile is not None:
            saved = self._store().save(profile)
            stored['profile_id'] = saved['profile_id']
            self._compatibility_print(saved, accepted)
            self._keep_audio(saved['profile_id'], stamp, accepted)
        for kind, text in drafts:
            if self.core.add(kind, text):
                stored['facts'] += 1
        return stored

    def _compatibility_print(self, profile, accepted):
        """The v1 print (used until the profile is calibrated) replaced by the
        mean of the new anchors, never averaged with the old lifetime mean."""
        import speaker
        vectors = [a['vector'] for a in accepted if a['role'] == 'anchor']
        mean = voice_profile.mean(vectors)
        if mean is None:
            return
        done = self.core.replace_voiceprint(profile['name'], speaker.encode(mean), len(vectors),
                                            profile['profile_id'])
        if not done:
            log(event='voice_profile', v1='name_taken')

    def _keep_audio(self, ident, stamp, accepted):
        """Accepted core takes for a later recomputation, AUDIO_LIMIT per person
        (oldest sessions go first)."""
        folder = self.core.voice_dir / 'v2-audio' / ident
        target = folder / stamp
        target.mkdir(parents=True, exist_ok=True)
        for take in accepted:
            path = Path(take['path'])
            if path.is_file():
                path.replace(target / path.name)
        sessions = sorted(p for p in folder.iterdir() if p.is_dir())
        while len(sessions) > 1 and sum(f.stat().st_size for s in sessions
                                        for f in s.glob('*.wav')) > AUDIO_LIMIT:
            shutil.rmtree(sessions.pop(0), ignore_errors=True)
