"""Maintenance mode: updates and reboots of the Pi and CT 107, confirmed by button.

The voice services never run apt or reboot themselves. They drop an empty
request file into /run/proximus-maintenance/requests; a root path unit
(deploy/maintenance) runs the fixed action and writes status.json next to
it. The Pi asks CT 107 through POST /v1/maintenance, which the server only
accepts on the LAN path, not through the Cloudflare tunnel.

Every action needs the maintenance mode (menu "Wartung" or "Wartungsmodus")
and a confirmation with button E (B cancels) within CONFIRM_SECONDS.
"""
import json
import re
import time
from pathlib import Path

DIR = Path('/run/proximus-maintenance')
ACTIONS = ('update', 'reboot')
TARGETS = ('pi', 'server')
CONFIRM_SECONDS = 20.0
IDLE_EXIT_SECONDS = 600.0     # leave maintenance mode after 10 min without input
SERVER_MIN_INTERVAL = 300.0   # server side: at most one request per 5 min
# Maintenance list on the display, in order.
ITEMS = ('update_pi', 'update_server', 'reboot_pi', 'reboot_server', 'exit')
LABELS = {
    'update_pi': 'Pi aktualisieren',
    'update_server': 'Server aktualisieren',
    'reboot_pi': 'Pi neu starten',
    'reboot_server': 'Server neu starten',
    'exit': 'Wartung beenden',
}
STATES = ('running', 'done', 'failed', 'rebooting')


def split(item):
    """'update_pi' -> ('update', 'pi')."""
    action, _, target = item.partition('_')
    return action, target


def request(action, directory=DIR):
    """Ask the root worker on this host to run ``action``."""
    if action not in ACTIONS:
        raise ValueError(action)
    (Path(directory) / 'requests' / action).touch()


def status(directory=DIR):
    """Last worker status of this host, sanitized, or None."""
    try:
        data = json.loads((Path(directory) / 'status.json').read_text())
    except (OSError, ValueError):
        return None
    return sanitize_status(data)


def sanitize_status(data):
    if not isinstance(data, dict) or data.get('state') not in STATES:
        return None
    clean = dict(state=data['state'], action=data.get('action') if data.get('action') in ACTIONS
                 else None)
    for key in ('at', 'upgraded', 'code'):
        if isinstance(data.get(key), int) and not isinstance(data.get(key), bool):
            clean[key] = data[key]
    clean['reboot'] = data.get('reboot') is True
    return clean


def installed(directory=DIR):
    return (Path(directory) / 'requests').is_dir()


# --- Voice commands -----------------------------------------------------------

_TARGET_SERVER = re.compile(r'\b(server|kogitator|ct ?107|container)\b')
_UPDATE = re.compile(r'\b(aktualisier\w*|update\w*|installiere (die )?(updates|aktualisierungen|'
                     r'sicherheitsupdates))\b')
_REBOOT = re.compile(r'\b(neu ?start\w*|starte\b.*\bneu|reboot\w*)\b')
_ENTER = re.compile(r'\b(wartungsmodus|wartungs modus|wartung (starten|beginnen|aktivieren))\b')
_EXIT = re.compile(r'\b(wartung(smodus)? (beenden|verlassen|aus)|beende (die )?wartung\w*)\b')


def command(text):
    """'enter', 'exit', 'update_pi', 'update_server', 'reboot_pi', 'reboot_server' or None."""
    text = str(text).lower().strip()
    if not text or len(text.split()) > 10:
        return None
    if _EXIT.search(text):
        return 'exit'
    target = 'server' if _TARGET_SERVER.search(text) else 'pi'
    if _REBOOT.search(text):
        return f'reboot_{target}'
    if _UPDATE.search(text) and not re.search(r'\b(gibt es|welche|wie viele|sind)\b', text):
        return f'update_{target}'
    if _ENTER.search(text):
        return 'enter'
    return None


def confirm_prompt(item, snapshot=None):
    """Spoken before an action; the operator confirms with E."""
    snapshot = snapshot or {}
    action, target = split(item)
    name = 'Pi' if target == 'pi' else 'Server'
    if action == 'update':
        key = 'updates' if target == 'pi' else 'server_updates'
        pending = snapshot.get(key)
        what = (f"{name} aktualisieren: {pending} Pakete." if pending
                else f"{name} aktualisieren.")
    else:
        what = f"{name} neu starten."
    return f"{what} Bestätigen mit Taste E, abbrechen mit B."


def result_text(target, data, lore='off'):
    """Spoken when a worker finished."""
    name = 'Pi' if target == 'pi' else 'Server'
    if data.get('state') == 'failed':
        return f"{name}: Aktualisierung fehlgeschlagen. Protokoll prüfen."
    count = data.get('upgraded', 0)
    text = f"{name}: Aktualisierung abgeschlossen, {count} Pakete installiert."
    if data.get('reboot'):
        text += " Neustart empfohlen."
    if lore == 'full':
        text += " Die Riten der Wartung sind vollzogen."
    return text


class Mode:
    """Maintenance state on the Pi: list selection and pending confirmation."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.active = False
        self.index = 0
        self.pending = None          # item waiting for E
        self.pending_until = 0.0

    def enter(self):
        self.active, self.index, self.pending = True, 0, None
        self.touched = self.clock()

    def idle_too_long(self):
        return self.active and self.clock() - getattr(self, 'touched', 0) > IDLE_EXIT_SECONDS

    def exit(self):
        self.active, self.pending = False, None

    def move(self, step):
        self.index = (self.index + step) % len(ITEMS)
        self.pending = None
        self.touched = self.clock()

    def ask(self, item):
        self.pending, self.pending_until = item, self.clock() + CONFIRM_SECONDS
        self.touched = self.clock()

    def take_confirmed(self):
        """E pressed: the pending item if still valid."""
        item, self.pending = self.pending, None
        if item is not None and self.clock() <= self.pending_until:
            return item
        return None

    def expired(self):
        if self.pending is not None and self.clock() > self.pending_until:
            self.pending = None
            return True
        return False


def _lan_url(env):
    for url in env.get('ASSISTANT_BASE_URL', '').split(','):
        url = url.strip().rstrip('/')
        if url.startswith('http://'):
            return url
    return None


def server_call(path, env=None, body=None, opener=None):
    """Authenticated call to CT 107 over the LAN URL; (status, data) or None."""
    import os
    import urllib.error
    import urllib.request
    env = os.environ if env is None else env
    url, token = _lan_url(env), env.get('ASSISTANT_TOKEN', '').strip()
    if not url or not token:
        return None
    from remote_turn import USER_AGENT
    headers = {'Authorization': f'Bearer {token}', 'User-Agent': USER_AGENT}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    request_ = urllib.request.Request(url + path, data=data, headers=headers,
                                      method='POST' if body is not None else 'GET')
    try:
        with (opener or urllib.request.urlopen)(request_, timeout=5) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (OSError, ValueError):
        return None


def request_server(action, env=None, opener=None):
    """'accepted', 'too_soon', 'not_installed' or 'unreachable'."""
    result = server_call('/v1/maintenance', env, dict(action=action), opener)
    if result is None:
        return 'unreachable'
    return {202: 'accepted', 429: 'too_soon', 503: 'not_installed'}.get(result[0], 'unreachable')


def server_status(env=None, opener=None):
    result = server_call('/v1/status', env, None, opener)
    if result is None or result[0] != 200 or not isinstance(result[1], dict):
        return None
    return sanitize_status(result[1].get('maintenance'))


ENTER_TEXT = "Wartungsmodus aktiv. Aktion wählen, Bestätigung mit Taste E."
EXIT_TEXT = "Wartungsmodus beendet."
NEED_MODE_TEXT = "Erst Wartungsmodus aktivieren."
CANCEL_TEXT = "Abgebrochen."


def phrases():
    """Every fixed maintenance sentence, for prerecorded clips."""
    texts = [ENTER_TEXT, EXIT_TEXT, NEED_MODE_TEXT, CANCEL_TEXT,
             *START_TEXT.values(), *FAIL_TEXT.values()]
    for item in ITEMS[:-1]:
        texts += [confirm_prompt(item, dict(updates=2, server_updates=2)), confirm_prompt(item)]
    for target in TARGETS:
        for lore in ('off', 'full'):
            texts += [result_text(target, dict(state='done', upgraded=2, reboot=reboot), lore)
                      for reboot in (True, False)]
        texts.append(result_text(target, dict(state='failed')))
    return texts


START_TEXT = {
    ('update', 'pi'): "Aktualisierung des Pi gestartet. Das dauert einige Minuten.",
    ('update', 'server'): "Aktualisierung des Servers gestartet.",
    ('reboot', 'pi'): "Pi startet neu. Bis gleich.",
    ('reboot', 'server'): "Server startet neu. Antworten kommen so lange lokal.",
}
FAIL_TEXT = {
    'not_installed': "Wartungsdienst nicht eingerichtet.",
    'too_soon': "Server-Wartung erst in einigen Minuten wieder möglich.",
    'unreachable': "Server für Wartung nicht erreichbar. Nur im lokalen Netz möglich.",
}
