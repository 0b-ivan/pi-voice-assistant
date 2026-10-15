"""Flüchtiger Webchat auf Basis der bestehenden Conversation-Auswahl.

Keine Pi-Identität: "web-guest" ist ausschließlich ein Platzhalter im
flüchtigen Thread-Schema, niemals eine Personen-/Profil-ID. Kein USB-Stick,
kein memory.json und keine Übertragung in persönliche Pi-Konversationen.
Alle Zugriffe sind an die jeweilige authentifizierte Browser-Capability
gebunden; ein anderer Browser kann nicht über eine Gesprächs-ID mitlesen.
"""
import secrets
import threading
import time

import conversation

CHAT_TTL = 12 * 3600
MAX_CHATS = 128
MAX_JOBS = 8
MAX_EVENTS = 100
FINAL = frozenset({'abgeschlossen', 'fehlgeschlagen', 'abgebrochen'})


class MissingChat(ValueError):
    pass


class WebConversations:
    def __init__(self, clock=time.time):
        self.clock = clock
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
                jobs=[], events=[], seq=0)
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

    def job_start(self, chat_id, principal, period):
        with self.lock:
            chat = self._get(chat_id, principal)
            for job in reversed(chat['jobs']):
                if job['status'] not in FINAL:
                    return dict(job), False   # singleflight: double click / retries
                if self.clock() - (job['finished'] or self.clock()) < 10:
                    return dict(job), False   # short retry window after completion
                break
            job = dict(id=secrets.token_hex(12), status='gestartet', period=period,
                       created=self.clock(), started=None, finished=None, report=None)
            chat['jobs'].append(job)
            chat['jobs'] = chat['jobs'][-MAX_JOBS:]
            self._event(chat, job, 'diagnose_start', period=period)
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
            if status == 'läuft' and job['started'] is None:
                job['started'] = self.clock()
            if status in FINAL:
                job['finished'] = self.clock()
            if 'report' in fields:
                job['report'] = fields.pop('report')
            # Every event is scoped, monotonic and replayable. The browser
            # deduplicates by sequence across reconnections.
            self._event(chat, job, stage, status=status, **fields)
            return True

    def latest(self, chat_id, principal):
        with self.lock:
            jobs = self._get(chat_id, principal)['jobs']
            return dict(jobs[-1]) if jobs else None

    def events(self, chat_id, principal, after=0):
        with self.lock:
            chat = self._get(chat_id, principal)
            after = max(0, int(after))
            events = [dict(e) for e in chat['events'] if e['seq'] > after]
            return dict(seq=chat['seq'], events=events,
                        jobs=[dict(j) for j in chat['jobs'][-MAX_JOBS:]],
                        gap=bool(chat['events'] and after and
                                 after < chat['events'][0]['seq'] - 1))

    def cancel(self, chat_id, principal):
        with self.lock:
            chat = self._get(chat_id, principal)
            for job in chat['jobs']:
                if job['status'] not in FINAL:
                    job['status'] = 'abgebrochen'
                    job['finished'] = self.clock()
                    self._event(chat, job, 'diagnose_cancel', status='abgebrochen')
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
