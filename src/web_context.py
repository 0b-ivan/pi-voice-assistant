"""Flüchtiger Webchat auf Basis der bestehenden Conversation-Auswahl.

Keine Pi-Identität: "web-guest" ist ausschließlich ein Platzhalter im
flüchtigen Thread-Schema, niemals eine Personen-/Profil-ID. Kein USB-Stick,
kein memory.json und keine Übertragung in persönliche Pi-Konversationen.
Alle Zugriffe sind an die jeweilige authentifizierte Browser-Capability
gebunden; ein anderer Browser kann nicht über eine Gesprächs-ID mitlesen.
Nur Kennung, Status und Befund der Diagnoseaufträge überdauern einen
Neustart, über das optionale Archiv in src/web_jobs.py.
"""
import re
import secrets
import threading
import time

import conversation

CHAT_TTL = 12 * 3600
MAX_CHATS = 128
MAX_JOBS = 8
MAX_EVENTS = 100
FINAL = frozenset({'abgeschlossen', 'fehlgeschlagen', 'abgebrochen'})

# Ein Modell hat keinen Zugriff auf Aufträge. Nennt seine Antwort trotzdem eine
# Auftragskennung oder eine Aktion an einem Auftrag, ist sie erfunden.
JOB_ID = re.compile(r'\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,24}\b', re.IGNORECASE)
JOB_ACTION = re.compile(
    r'\b(?:prüf)?(?:auftrag|aufträge|auftrags\w*|diagnose\w*|job|jobs|prüfung)\b'
    r'[^.!?\n]{0,80}\b(?:markiert|erledigt|abgebrochen|storniert|gelöscht|entfernt|'
    r'beendet|geschlossen|archiviert|zurückgesetzt|neu gestartet|abgeschlossen)\b',
    re.IGNORECASE)
JOB_ACTION_TEXT = ('Diagnoseaufträge kann ich nur starten und lesen, nicht markieren, '
                   'abbrechen oder löschen. Den belegten Stand nennt „Prüfe Auftrag: '
                   '<Kennung>“; „Neues Gespräch“ beendet noch laufende Prüfungen.')


def claims_job_action(text):
    """True, wenn eine Modellantwort einen Auftrag oder dessen Zustand behauptet."""
    return bool(JOB_ID.search(text or '') or JOB_ACTION.search(text or ''))


class MissingChat(ValueError):
    pass


class WebConversations:
    def __init__(self, clock=time.time, archive=None):
        self.clock, self.archive = clock, archive
        self.lock = threading.RLock()
        self.chats = {}

    def _get(self, chat_id, principal):
        chat = self.chats.get(chat_id)
        if not chat or not secrets.compare_digest(chat['principal'], principal):
            raise MissingChat('Gespräch nicht vorhanden oder nicht freigegeben')
        if self.clock() - chat['created'] > CHAT_TTL:
            self.chats.pop(chat_id, None)
            raise MissingChat('Gespräch abgelaufen')
        return chat

    def create(self, principal):
        if not isinstance(principal, str) or len(principal) < 16:
            raise ValueError('Browser-Kennung fehlt')
        with self.lock:
            for key, chat in list(self.chats.items()):
                if self.clock() - chat['created'] > CHAT_TTL:
                    self.chats.pop(key, None)
            if len(self.chats) >= MAX_CHATS:
                raise ValueError('Maximale Anzahl aktiver Gespräche erreicht')
            chat_id = secrets.token_urlsafe(24)
            self.chats[chat_id] = dict(
                principal=principal, created=self.clock(),
                thread=conversation.fresh('web-guest', int(self.clock())),
                jobs=[], events=[], seq=0, focus=None)
            return chat_id

    def exists(self, chat_id, principal):
        with self.lock:
            return self._get(chat_id, principal)['thread']['id']

    def history(self, chat_id, principal, question):
        with self.lock:
            state = self._get(chat_id, principal)['thread']
            selected = conversation.select(state, question, int(self.clock()))
            # Only the selected original turns, not a prior claim of a completed
            # diagnosis, are model history. Evidence is question-only by design.
            return [dict(q=t['q'], a=t['a']) for t in selected['history']]

    def append(self, chat_id, principal, question, answer, persona='servitor'):
        with self.lock:
            chat = self._get(chat_id, principal)
            state = chat['thread']
            if not question.strip() or not answer.strip():
                return False
            if len(question.strip()) > conversation.MAX_TEXT or len(answer.strip()) > conversation.MAX_TEXT:
                return False
            expected = conversation.snapshot(state, None)
            updated = conversation.commit_turn(
                state, expected=expected, stick=None, turn_id=conversation.new_id(),
                question=question, answer=answer, persona=persona
                if persona in conversation.PERSONAS else 'servitor',
                now=int(self.clock()), context_ids=[t['id'] for t in state['turns'][-4:]])
            chat['thread'] = updated
            return True

    def _event(self, chat, job, stage, **data):
        chat['seq'] += 1
        event = dict(seq=chat['seq'], id=job['id'], stage=stage, **data)
        chat['events'].append(event)
        chat['events'] = chat['events'][-MAX_EVENTS:]
        return event

    def _keep(self, job):
        if self.archive is not None:
            self.archive.record(job)

    def job_start(self, chat_id, principal, period):
        with self.lock:
            chat = self._get(chat_id, principal)
            for job in reversed(chat['jobs']):
                if job['period'] != period:
                    break  # ein gestriger Auftrag ist kein aktueller Selbsttest
                if job['status'] not in FINAL:
                    return dict(job), False   # singleflight: double click / retries
                if self.clock() - (job['finished'] or self.clock()) < 10:
                    return dict(job), False   # short retry window after completion
                break
            job = dict(id=secrets.token_hex(12), status='gestartet', period=period,
                       created=self.clock(), started=None, finished=None,
                       delivered_at=None, report=None)
            chat['jobs'].append(job)
            chat['jobs'] = chat['jobs'][-MAX_JOBS:]
            self._event(chat, job, 'diagnose_start', period=period)
            self._keep(job)
            chat['focus'] = job['id']
            return dict(job), True

    def job_update(self, chat_id, principal, job_id, status, stage, **fields):
        with self.lock:
            try:
                chat = self._get(chat_id, principal)
            except MissingChat:
                return False
            job = next((j for j in chat['jobs'] if j['id'] == job_id), None)
            if job is None or job['status'] in FINAL:
                return False
            if status not in {'gestartet', 'läuft'} | FINAL:
                raise ValueError('invalid job status')
            job['status'] = status
            changed = status in FINAL or status == 'läuft' and job['started'] is None
            if status == 'läuft' and job['started'] is None:
                job['started'] = self.clock()
            if status in FINAL:
                job['finished'] = self.clock()
            if 'report' in fields:
                job['report'] = fields.pop('report')
            if changed:  # nur Statuswechsel, nicht jeder Zwischenschritt
                self._keep(job)
            # Every event is scoped, monotonic and replayable. The browser
            # deduplicates by sequence across reconnections.
            self._event(chat, job, stage, status=status, **fields)
            return True

    def latest(self, chat_id, principal):
        with self.lock:
            jobs = self._get(chat_id, principal)['jobs']
            return dict(jobs[-1]) if jobs else None

    def set_focus(self, chat_id, principal, job_id):
        """Der zuletzt gestartete oder abgefragte Auftrag gilt für Rückfragen."""
        with self.lock:
            self._get(chat_id, principal)['focus'] = job_id

    def focus(self, chat_id, principal):
        with self.lock:
            return self._get(chat_id, principal).get('focus')

    def job(self, chat_id, principal, job_id):
        """Read-only lookup by full ID or unique prefix, never across browsers."""
        if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{8,24}', job_id):
            return None
        with self.lock:
            jobs = self._get(chat_id, principal)['jobs']
            found = [item for item in jobs if item['id'].startswith(job_id)]
            return dict(found[0]) if len(found) == 1 else None

    def events(self, chat_id, principal, after=0):
        with self.lock:
            chat = self._get(chat_id, principal)
            after = max(0, int(after))
            events = [dict(e) for e in chat['events'] if e['seq'] > after]
            return dict(seq=chat['seq'], events=events,
                        jobs=[dict(j) for j in chat['jobs'][-MAX_JOBS:]],
                        gap=bool(chat['events'] and after and
                                 after < chat['events'][0]['seq'] - 1))

    def acknowledge(self, chat_id, principal, job_id):
        """Idempotent browser receipt, never inferred from a background job."""
        with self.lock:
            chat = self._get(chat_id, principal)
            job = next((j for j in chat['jobs'] if j['id'] == job_id), None)
            if job is None or job['status'] not in FINAL:
                return False
            if job.get('delivered_at') is not None:
                return False
            job['delivered_at'] = self.clock()
            self._event(chat, job, 'diagnose_delivered', status=job['status'])
            return True

    def cancel(self, chat_id, principal):
        with self.lock:
            chat = self._get(chat_id, principal)
            for job in chat['jobs']:
                if job['status'] not in FINAL:
                    job['status'] = 'abgebrochen'
                    job['finished'] = self.clock()
                    self._event(chat, job, 'diagnose_cancel', status='abgebrochen')
                    self._keep(job)
            return True

    def close(self, chat_id, principal):
        with self.lock:
            self.cancel(chat_id, principal)
            self.chats.pop(chat_id, None)

    def close_principal(self, principal):
        with self.lock:
            for chat_id, chat in list(self.chats.items()):
                if secrets.compare_digest(chat['principal'], principal):
                    self.close(chat_id, principal)
