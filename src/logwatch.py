"""Self-test: Proximus reads his own logs, repairs small faults, reports the rest.

Every 30 min a daemon thread reads the last 24 h of the journal: all lines of
the own units (JSON events such as ``stt_error``, systemd's "Failed with
result" for a crash) and, system-wide, only warnings and worse (kernel:
undervoltage, I/O errors, OOM). Fixed rules turn lines into findings, each
with a threshold, so a single dropped connection or a known harmless driver
message never reaches the operator. The result is a small dict of codes and
counts that travels in the status snapshot; the morning litany names at most
three, the most severe first, and stays silent when nothing is notable.

Small faults are repaired through the root maintenance worker (the voice
services have no root rights): a unit systemd gave up on is restarted, a
stalled log copy to the stick is triggered again. At most every 30 min and
three times a day per repair, and each repair is checked afterwards.

Reading the system journal needs the group ``systemd-journal`` (see the
units). Without it journalctl only shows the service's own lines; the
self-test then says so instead of reporting a clean system.
"""
import datetime
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

WINDOW = 24 * 3600.0          # how far back one check reads
INTERVAL = 30 * 60.0          # seconds between checks
FIRST_DELAY = 180.0           # after start: let the boot settle first
MAX_LINES = 50000             # per journal query
REQUESTS = Path('/run/proximus-maintenance/requests')
LOGSYNC_STATE = Path('/run/proximus-maintenance/logsync.json')
LOGSYNC_STALE = 20 * 60.0     # stick present, last copy older than this: stalled
REPAIR_COOLDOWN = 30 * 60.0
REPAIRS_PER_DAY = 3
REPAIR_SETTLE = 20.0          # seconds before a repair is checked
BRIEFING_ITEMS = 3            # findings named in the morning litany
SELFTEST_ITEMS = 5            # ... and on "Selbsttest"
# Root worker actions (deploy/maintenance/proximus-maintenance).
REPAIR_ACTIONS = ('restart-display', 'restart-llm', 'logsync')

# code: (severity 0 = critical, 1 = fault, 2 = notice; short label for logs/docs)
CODES = {
    'undervoltage': (0, 'Unterspannung'),
    'oom': (0, 'Arbeitsspeicher erschöpft'),
    'disk_io': (0, 'Datenträgerfehler'),
    'display_down': (0, 'Anzeige ausgefallen, Neustart erfolglos'),
    'llm_down': (0, 'Lokaler Sprachkern ausgefallen, Neustart erfolglos'),
    'crash_ptt': (1, 'Sprachdienst abgestürzt'),
    'crash_display': (1, 'Anzeige abgestürzt'),
    'crash_voice': (1, 'Sprachdienst abgestürzt'),
    'crash_llm': (1, 'Lokaler Sprachkern abgestürzt'),
    'load': (1, 'Modelle nicht geladen'),
    'local_llm': (1, 'Lokaler Sprachkern nicht bereit'),
    'logsync': (1, 'Log-Ablage auf dem Stick gestört'),
    'loop': (1, 'interner Fehler'),
    'stt': (1, 'Spracherkennung gestört'),
    'tts': (1, 'Sprachausgabe gestört'),
    'llm': (1, 'Sprachkern gestört'),
    'turn': (1, 'Anfragen fehlgeschlagen'),
    'usb': (1, 'USB-Störung'),
    'buttons': (2, 'Tastenleiste gestört'),
    'wake': (2, 'Weckwort gestört'),
    'server_link': (2, 'Serververbindung abgebrochen'),
    'openrouter': (2, 'OpenRouter ausgefallen'),
    'speaker': (2, 'Stimmerkennung gestört'),
    'errors': (2, 'Fehlermeldungen im Systemprotokoll'),
    'limited': (2, 'Systemprotokoll nicht lesbar'),
}


@dataclass(frozen=True)
class Rule:
    code: str
    threshold: int = 1
    events: tuple = ()     # JSON event names written by the own units
    pattern: str = None    # regex over the message text
    kernel: bool = False   # pattern only applies to kernel messages


# Pi: pi-ptt and pi-display. One dropped server turn or a single STT
# failure is normal operation; only repetitions count.
PI_UNITS = {'pi-ptt.service': 'crash_ptt', 'pi-display.service': 'crash_display'}
PI_RULES = (
    Rule('undervoltage', pattern=r'under-?voltage', kernel=True),
    Rule('oom', pattern=r'out of memory|oom-kill|invoked oom-killer'),
    Rule('disk_io', pattern=r'I/O error|EXT4-fs error|Buffer I/O error|mmc\d+: .*timeout',
         kernel=True),
    Rule('usb', 3, pattern=r'device descriptor read|device not accepting address|'
                           r'unable to enumerate|over-current', kernel=True),
    Rule('loop', 3, events=('error',)),
    Rule('stt', 3, events=('stt_error', 'stt_live_error')),
    Rule('tts', 2, events=('speech_error', 'tts_error')),
    Rule('llm', 3, events=('llm_error',)),
    Rule('server_link', 5, events=('remote_error',)),
    Rule('buttons', 3, events=('shim_error',)),
    Rule('wake', 3, events=('wake_error',)),
)
SERVER_UNITS = {'servitor-voice.service': 'crash_voice', 'servitor-llm.service': 'crash_llm'}
SERVER_RULES = (
    Rule('oom', pattern=r'out of memory|oom-kill|invoked oom-killer'),
    Rule('disk_io', pattern=r'I/O error|EXT4-fs error|Buffer I/O error', kernel=True),
    Rule('load', events=('load_failed',)),
    Rule('local_llm', events=('local_llm_warmup_failed',)),
    Rule('openrouter', 3, events=('llm_fallback',)),
    Rule('speaker', 3, events=('speaker_error',)),
    Rule('turn', 3, events=('turn_error',)),
)
ERRORS_THRESHOLD = 5   # other err/crit/alert/emerg lines before they are named
# Known harmless lines (Pi Zero 2 W on Trixie). Extra patterns: LOGWATCH_IGNORE
# in the environment, separated by "||".
IGNORE = (
    r'brcmfmac', r'cfg80211', r'bluetoothd', r'wireplumber', r'pipewire',
    r'Bluetooth: hci0', r'vc4-drm', r'dbus-daemon.*Activation via systemd failed',
    r'systemd-journald', r'sudo: .*pam_unix',
    r'USB disconnect',   # pulling the memory stick
    # wpa_supplicant with the brcmfmac driver, Debian's ALSA udev rule at boot
    r'nl80211: kernel reports', r'bgscan simple', r'alsa-restore\.rules',
)
# systemd results that mean a crash; 'timeout' only follows a slow stop.
_FAILED = re.compile(r"Failed with result '(exit-code|signal|core-dump|oom-kill|watchdog)'")
# Exit 75 (EX_TEMPFAIL): the display restarts itself on new code (display.py).
_RESTART = re.compile(r'Main process exited, code=exited, status=75/')
# A pulled USB stick: the kernel reports "device offline" for it, then lost
# writes and an aborted ext4 journal. Its lines within this window are no fault;
# a failing medium reports read/write errors without "device offline".
_OFFLINE = re.compile(r'device offline error, dev (sd[a-z])\b')
_USB_DISK = re.compile(r'\b(sd[a-z])\d*\b')
UNPLUG_WINDOW = 5.0
BENIGN_CODES = ('no_speech', 'too_short', 'too_large', 'bad_request')
_BENIGN = re.compile(r'no transcript|no speech|too short', re.IGNORECASE)
_LIMITED = re.compile(r'insufficient permissions|not seeing messages from other users',
                      re.IGNORECASE)


def _text(value):
    if isinstance(value, list):  # journald sends non-UTF-8 messages as byte arrays
        try:
            return bytes(value).decode('utf-8', 'replace')
        except (TypeError, ValueError):
            return ''
    return value if isinstance(value, str) else ''


def parse_entry(line):
    """One ``journalctl -o json`` line as a small dict, or None."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    text = _text(data.get('MESSAGE'))
    try:
        priority = int(data.get('PRIORITY', 6))
    except (TypeError, ValueError):
        priority = 6
    try:
        at = int(data.get('__REALTIME_TIMESTAMP', 0)) / 1e6
    except (TypeError, ValueError):
        at = 0.0
    name = None
    if text.startswith('{'):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get('event'), str):
            name = payload['event']
            # Silence and too short presses are reported as errors too; they
            # are the operator's, not a fault.
            if payload.get('code') in BENIGN_CODES or _BENIGN.search(
                    _text(payload.get('message'))):
                name = 'benign'
    return dict(at=at, unit=_text(data.get('UNIT')) or _text(data.get('_SYSTEMD_UNIT')),
                priority=priority, text=text, event=name,
                kernel=data.get('_TRANSPORT') == 'kernel')


def compile_ignore(extra=None):
    extra = os.environ.get('LOGWATCH_IGNORE', '') if extra is None else extra
    patterns = list(IGNORE) + [p.strip() for p in extra.split('||') if p.strip()]
    return re.compile('|'.join(f'(?:{p})' for p in patterns), re.IGNORECASE)


def classify(entry, rules, units, ignore):
    """Finding code of one journal entry, or None."""
    text, unit = entry['text'], entry['unit']
    if unit in units:
        # Own units: only systemd's verdict and event names count. Their text
        # holds transcripts and answers ("out of memory" in a reply is no OOM).
        if _FAILED.search(text):
            return units[unit]
        for rule in rules:
            if entry['event'] is not None and entry['event'] in rule.events:
                return rule.code
        return None
    if entry['event'] is not None or ignore.search(text):
        return None
    for rule in rules:
        if rule.pattern and (entry['kernel'] or not rule.kernel) \
                and re.search(rule.pattern, text, re.IGNORECASE):
            return rule.code
    if entry['priority'] <= 3:
        return 'errors'
    return None


def _unplugged(entry, offline):
    """True for a kernel line about a USB disk shortly after it went offline."""
    match = entry['kernel'] and _USB_DISK.search(entry['text'])
    return bool(match) and any(0 <= entry['at'] - at <= UNPLUG_WINDOW
                               for at in offline.get(match.group(1), ()))


def analyse(entries, rules, units, ignore=None, last=None):
    """Counts of the findings that reach their threshold: {code: count}.
    ``last``, a dict, receives the time each code was last seen."""
    ignore = compile_ignore() if ignore is None else ignore
    offline = {}
    for entry in entries:
        match = entry['kernel'] and _OFFLINE.search(entry['text'])
        if match:
            offline.setdefault(match.group(1), []).append(entry['at'])
    counts = {}
    restarting = set()
    for entry in entries:
        if entry['unit'] in units and _RESTART.search(entry['text']):
            restarting.add(entry['unit'])
            continue
        if entry['unit'] in restarting and _FAILED.search(entry['text']):
            restarting.discard(entry['unit'])
            continue
        if _unplugged(entry, offline):
            continue
        code = classify(entry, rules, units, ignore)
        if code:
            counts[code] = counts.get(code, 0) + 1
            if last is not None:
                last[code] = max(last.get(code, 0.0), entry['at'])
    thresholds = {rule.code: rule.threshold for rule in rules}
    thresholds.update({code: 1 for code in units.values()})
    thresholds['errors'] = ERRORS_THRESHOLD
    return {code: n for code, n in counts.items() if n >= thresholds.get(code, 1)}


def _journal(args, timeout=120):
    """(entries, limited) of one journalctl query, read as a stream: a day of
    service logs does not have to fit into memory at once."""
    command = ['nice', '-n', '15', 'journalctl', '-o', 'json', '--no-pager', '-q',
               '--output-fields=MESSAGE,PRIORITY,UNIT,_SYSTEMD_UNIT,_TRANSPORT',
               '-n', str(MAX_LINES)] + args
    entries = []
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          errors='replace', env=dict(os.environ, LC_ALL='C')) as proc:
        timer = threading.Timer(timeout, proc.kill)
        timer.start()
        try:
            for line in proc.stdout:
                entry = parse_entry(line)
                if entry is not None:
                    entries.append(entry)
            hint = proc.stderr.read()
        finally:
            timer.cancel()
    if proc.returncode not in (0, None) and not entries:
        raise OSError(f'journalctl exited with {proc.returncode}')
    return entries, bool(_LIMITED.search(hint or ''))


def read_journal(since, units):
    """Own units completely, everything else from warning upwards.
    Returns (entries, limited) or raises OSError."""
    since_arg = f'--since=@{int(since)}'
    own, limited = _journal([since_arg] + [arg for unit in units for arg in ('-u', unit)])
    system, more = _journal([since_arg, '-p', '0..4'])
    # The own units' warnings are already in ``own``.
    return own + [entry for entry in system if entry['unit'] not in units], limited or more


def unit_failed(unit, run=subprocess.run):
    """True when systemd gave up on ``unit`` (start limit hit)."""
    try:
        return run(['systemctl', 'is-failed', '--quiet', unit], timeout=10,
                   check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def request(action, directory=REQUESTS):
    """Ask the root worker for a repair; False if it is not installed."""
    if action not in REPAIR_ACTIONS:
        raise ValueError(action)
    try:
        (Path(directory) / action).touch()
    except OSError:
        return False
    return True


def logsync_stalled(path=LOGSYNC_STATE, now=None):
    """True when the copy to the stick is not running (timer dead, worker
    missing) or failed with the stick plugged in. Without the stick the
    journal stays in RAM on purpose: no fault (deploy/logsync/proximus-logsync)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    at = data.get('at')
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return True
    if (now or time.time()) - at > LOGSYNC_STALE:
        return True
    return data.get('stick') is True and data.get('ok') is not True


@dataclass(frozen=True)
class Repair:
    """``broken()`` -> True: ask the worker for ``action``; ``down`` is the
    finding when the fault is still there afterwards."""
    code: str
    action: str
    broken: object
    down: str


class LogWatch:
    """Periodic self-test. ``result``: dict(at, findings, repairs, limited)."""

    def __init__(self, rules, units, repairs=(), checks=(), reader=read_journal,
                 clock=time.time, sleep=time.sleep, requester=request, report=None):
        self.rules, self.units, self.repairs, self.checks = rules, units, repairs, checks
        self.reader, self.clock, self.sleep, self.requester = reader, clock, sleep, requester
        self.report = report            # called with the result when it changed
        self.ignore = compile_ignore()
        self.result = None
        self.done = []                  # (time, repair code) of the last 24 h
        self.attempts = {}              # repair code -> [request times]
        self._lock = threading.Lock()

    def _may_repair(self, code, now, force=False):
        times = [t for t in self.attempts.get(code, []) if now - t < WINDOW]
        self.attempts[code] = times
        return len(times) < REPAIRS_PER_DAY and (force or not times
                                                 or now - times[-1] >= REPAIR_COOLDOWN)

    def _repair(self, findings, now, force=False):
        pending = []
        for repair in self.repairs:
            try:
                broken = repair.broken()
            except Exception:  # a broken probe must never stop the self-test
                broken = False
            if not broken:
                continue
            if not self._may_repair(repair.code, now, force):
                findings[repair.down] = max(findings.get(repair.down, 0), 1)
                continue
            if self.requester(repair.action):
                self.attempts[repair.code].append(now)
                pending.append(repair)
            else:
                findings[repair.down] = max(findings.get(repair.down, 0), 1)
        if pending:
            self.sleep(REPAIR_SETTLE)
        for repair in pending:
            try:
                fixed = not repair.broken()
            except Exception:
                fixed = False
            if fixed:
                self.done.append((now, repair.code))
            else:
                findings[repair.down] = max(findings.get(repair.down, 0), 1)

    def check(self, force=False):
        """One self-test round (blocking: journal read and repairs). ``force``:
        asked for by the operator, repairs skip the cooldown."""
        with self._lock:
            now = self.clock()
            try:
                entries, limited = self.reader(now - WINDOW, list(self.units))
            except (OSError, subprocess.SubprocessError):
                entries, limited = [], True
            last = {}
            findings = analyse(entries, self.rules, self.units, self.ignore, last)
            if limited:
                findings['limited'] = 1
            for check in self.checks:
                try:
                    code = check()
                except Exception:
                    code = None
                if code:
                    findings[code] = max(findings.get(code, 0), 1)
            self._repair(findings, now, force)
            self.done = [(t, code) for t, code in self.done if now - t < WINDOW]
            repairs = sorted({code for _, code in self.done})
            previous = self.result
            # Checks, repairs and an unreadable journal are current by nature.
            last = {code: last.get(code) or now for code in findings}
            self.result = dict(at=now, findings=findings, repairs=repairs, last=last)
            if self.report and (previous is None or previous['findings'] != findings
                                or previous['repairs'] != repairs):
                self.report(self.result)
            return self.result

    def start(self, interval=INTERVAL, first_delay=FIRST_DELAY):
        def run():
            self.sleep(first_delay)
            while True:
                try:
                    self.check()
                except Exception:  # never take the assistant down
                    pass
                self.sleep(interval)
        threading.Thread(target=run, name='logwatch', daemon=True).start()
        return self

    def refresh(self, force=False):
        threading.Thread(target=self.check, kwargs=dict(force=force), name='logwatch-now',
                         daemon=True).start()

    def snapshot_fields(self, prefix='log'):
        """Status-snapshot fields of the last result (none before the first check)."""
        result = self.result
        if result is None:
            return {}
        return {f'{prefix}_findings': dict(result['findings']),
                f'{prefix}_repairs': list(result['repairs']),
                f'{prefix}_last': dict(result.get('last', {}))}


def pi_watch(memory_present, report=None, requests=REQUESTS, state=LOGSYNC_STATE):
    """The Pi's self-test: pi-ptt/pi-display, kernel, the log copy to the stick."""
    def stalled():
        # Just plugged in: pi-ptt already asked for a copy, the state follows.
        return memory_present() and logsync_stalled(state)

    repairs = (
        Repair('display', 'restart-display', lambda: unit_failed('pi-display.service'),
               'display_down'),
        Repair('logsync', 'logsync', stalled, 'logsync'),
    )
    return LogWatch(PI_RULES, PI_UNITS, repairs=repairs, report=report,
                    requester=lambda action: request(action, requests))


def server_watch(report=None, requests=REQUESTS):
    """CT 107: servitor-voice, the local LLM, kernel."""
    repairs = (Repair('llm', 'restart-llm', lambda: unit_failed('servitor-llm.service'),
                      'llm_down'),)
    return LogWatch(SERVER_RULES, SERVER_UNITS, repairs=repairs, report=report,
                    requester=lambda action: request(action, requests))


# --- Snapshot fields and spoken sentences ---------------------------------
#
# Proximus speaks about himself: the Pi is his body ("ich", "meine Anzeige"),
# CT 107 his server ("mein Server", in full lore "mein Kogitator"). A finding
# last seen within ONGOING is current and gets a cause and a remedy; an older
# one only says when it happened and that it has been quiet since.

ONGOING = 60 * 60.0           # last seen this recently: still a problem

# code: (what happened, remedy). {bei}/{Bei}: where (Pi or server), {log}: whose
# system journal, {n}: count. Plain style, also used by Billy.
ADVICE = {
    'undervoltage': ("Meine Stromversorgung ist eingebrochen",
                     "Netzteil oder Kabel tauschen, es braucht mindestens 2,5 Ampere"),
    'oom': ("{Bei} ist der Arbeitsspeicher ausgegangen",
            "Kommt das wieder, ein kleineres Modell wählen oder neu starten"),
    'disk_io': ("{Bei} gab es Schreib- oder Lesefehler auf einem Datenträger",
                "Gedächtniskern und Speicherkarte prüfen, im Zweifel tauschen"),
    'display_down': ("Meine Anzeige ist ausgefallen, und mein Neustart hat nicht geholfen",
                     "Displaykabel prüfen und mich neu starten"),
    'llm_down': ("Mein lokaler Sprachkern ist ausgefallen und startet nicht wieder",
                 "Den Dienst servitor-llm auf meinem Server prüfen"),
    'crash_ptt': ("Mein Sprachdienst ist abgestürzt",
                  "Er lief danach von selbst wieder an. Kommt das wieder, das Protokoll "
                  "auf dem Gedächtniskern prüfen lassen"),
    'crash_display': ("Meine Anzeige ist abgestürzt",
                      "Sie lief danach von selbst wieder an. Kommt das wieder, das Protokoll "
                      "prüfen lassen"),
    'crash_voice': ("Mein Sprachdienst auf dem Server ist abgestürzt",
                    "Er lief danach von selbst wieder an. Kommt das wieder, das Serverprotokoll "
                    "prüfen lassen"),
    'crash_llm': ("Mein lokaler Sprachkern ist abgestürzt",
                  "Er lief danach von selbst wieder an"),
    'load': ("Auf meinem Server ließen sich Modelle nicht laden",
             "Speicherplatz und Modelldateien auf dem Server prüfen"),
    'local_llm': ("Mein lokaler Sprachkern wurde nicht bereit",
                  "Ohne Netz kann ich dann nicht nachdenken. Den Dienst servitor-llm prüfen"),
    'logsync': ("Ich kann meine Protokolle nicht auf den Gedächtniskern schreiben",
                "Den Gedächtniskern einmal ab- und wieder anstecken"),
    'loop': ("In meiner Steuerung sind wiederholt interne Fehler aufgetreten",
             "Das Protokoll auf dem Gedächtniskern prüfen lassen"),
    'stt': ("Meine Spracherkennung ist wiederholt ausgefallen",
            "Mikrofon und Serververbindung prüfen"),
    'tts': ("Meine Sprachausgabe ist wiederholt ausgefallen",
            "Lautsprecher prüfen, sonst mich neu starten"),
    'llm': ("Mein Sprachkern hat wiederholt nicht geantwortet", "Die Netzverbindung prüfen"),
    'turn': ("Auf meinem Server sind wiederholt Anfragen fehlgeschlagen",
             "Das Serverprotokoll prüfen lassen"),
    'usb': ("An meinem USB-Anschluss gibt es Störungen",
            "Den Gedächtniskern fest einstecken und den Anschluss prüfen"),
    'buttons': ("Meine Tastenleiste hat wiederholt nicht geantwortet",
                "Die Steckverbindung der Tastenleiste prüfen"),
    'wake': ("Mein Weckwort-Lauscher ist wiederholt ausgefallen",
             "Mich neu starten. Bis dahin funktioniert die Sprechtaste"),
    'server_link': ("Die Verbindung zu meinem Server ist wiederholt abgerissen",
                    "Den WLAN-Empfang am Standort prüfen"),
    'openrouter': ("OpenRouter war wiederholt nicht erreichbar, ich bin auf meinen lokalen "
                   "Sprachkern ausgewichen", "Nichts zu tun, solange es nicht anhält"),
    'speaker': ("Meine Stimmerkennung ist wiederholt gescheitert",
                "Die Stimme im Menü neu einlernen"),
    'errors': ("{Bei} stehen {n} weitere Fehlermeldungen im Systemprotokoll",
               "Nichts Dringendes. Bei Gelegenheit das Protokoll auf dem Gedächtniskern ansehen"),
    'limited': ("Ich darf {log} nicht lesen",
                "Den Dienst neu einspielen, ihm fehlt die Gruppe systemd-journal"),
}
# Full lore: the same, in the words of the Adeptus Mechanicus.
ADVICE_LORE = {
    'undervoltage': ("Der Energiefluss meines Leibes stockt",
                     "Das Netzteil ist zu ersetzen, mindestens 2,5 Ampere, so verlangt es der Ritus"),
    'oom': ("{Bei} ist der Speicher der Kogitation erschöpft",
            "Die Last mindern oder den Ritus des Neustarts vollziehen"),
    'disk_io': ("{Bei} verweigert ein Datenträger den Dienst",
                "Gedächtniskern und Datenkarte prüfen. Entweihter Speicher ist zu ersetzen"),
    'display_down': ("Mein Okular ist erloschen, auch der Ritus des Neustarts half nicht",
                     "Die Leitung des Okulars prüfen und mich neu erwecken"),
    'llm_down': ("Mein lokaler Sprachkern schweigt und lässt sich nicht erwecken",
                 "Den Maschinengeist von servitor-llm in meinem Kogitator besänftigen"),
    'crash_ptt': ("Mein Sprachgeist ist gefallen",
                  "Er erwachte von selbst neu. Kehrt das Übel wieder, sind die Protokolle "
                  "zu deuten"),
    'crash_display': ("Mein Okular ist gefallen",
                      "Es erwachte von selbst neu. Kehrt das Übel wieder, sind die Protokolle "
                      "zu deuten"),
    'crash_voice': ("Der Sprachgeist meines Kogitators ist gefallen",
                    "Er erwachte von selbst neu"),
    'crash_llm': ("Mein lokaler Sprachkern ist gefallen", "Er erwachte von selbst neu"),
    'load': ("Mein Kogitator konnte die heiligen Modelle nicht laden",
             "Speicher und Modelle im Kogitator prüfen"),
    'local_llm': ("Mein lokaler Sprachkern erwachte nicht",
                  "Ohne Noosphäre bliebe ich stumm. Den Dienst servitor-llm prüfen"),
    'logsync': ("Meine Protokolle erreichen den Gedächtniskern nicht",
                "Den Gedächtniskern einmal lösen und neu einsetzen"),
    'loop': ("Meine Steuerlitanei stockt wiederholt",
             "Die Protokolle im Gedächtniskern deuten lassen"),
    'stt': ("Mein Gehör versagt wiederholt",
            "Das Mikrofon prüfen und die Verbindung zum Kogitator"),
    'tts': ("Meine Stimme versagt wiederholt",
            "Den Lautsprecher prüfen, sonst den Ritus des Neustarts"),
    'llm': ("Mein Sprachkern schweigt wiederholt", "Die Verbindung zur Noosphäre prüfen"),
    'turn': ("Mein Kogitator wies wiederholt Anfragen ab", "Die Protokolle des Kogitators deuten"),
    'usb': ("Mein USB-Port zeigt Zeichen der Entweihung",
            "Den Gedächtniskern fest einsetzen und den Port prüfen"),
    'buttons': ("Meine Tastenleiste gehorcht nicht zuverlässig",
                "Die Steckverbindung prüfen und mit heiligem Öl salben"),
    'wake': ("Mein Lauschgeist für das Weckwort ist wiederholt verstummt",
             "Den Ritus des Neustarts vollziehen. Bis dahin gehorche ich der Sprechtaste"),
    'server_link': ("Die Verbindung zu meinem Kogitator riss wiederholt ab",
                    "Die Noosphäre am Standort prüfen"),
    'openrouter': ("Die ferne Noosphäre schwieg wiederholt, ich wich auf meinen lokalen "
                   "Kogitator aus", "Kein Eingriff nötig, solange es nicht anhält"),
    'speaker': ("Meine Stimmerkennung irrte wiederholt",
                "Die Stimme des Bedieners im Menü neu einprägen"),
    'errors': ("{Bei} verzeichnet das Protokoll {n} weitere Klagen der Maschinengeister",
               "Nichts Dringendes. Bei Gelegenheit die Protokolle im Gedächtniskern deuten"),
    'limited': ("Mir ist der Blick in {log} verwehrt",
                "Den Dienst neu einspielen, ihm fehlt die Gruppe systemd-journal"),
}
# Findings the self-test repairs on its own (LogWatch repairs, on "Selbsttest" at once).
SELF_REPAIR = ('logsync', 'display_down', 'llm_down')
REPAIR_DONE = {
    'display': ("Meine Anzeige habe ich neu gestartet",
                "Mein Okular habe ich neu erweckt"),
    'llm': ("Meinen lokalen Sprachkern habe ich neu gestartet",
            "Meinen lokalen Sprachkern habe ich neu erweckt"),
    'logsync': ("Die Ablage meiner Protokolle habe ich neu angestoßen",
                "Die Überführung meiner Protokolle habe ich neu angestoßen"),
}
_PLACES = {  # side: plain {Bei}, lore {Bei}, plain {log}, lore {log}
    'log': ("Bei mir", "In meinem Leib", "mein Systemprotokoll", "mein Systemprotokoll"),
    'server_log': ("Auf meinem Server", "In meinem Kogitator",
                   "das Systemprotokoll meines Servers", "das Protokoll meines Kogitators"),
}
REPAIRS = {code: plain for code, (plain, _) in REPAIR_DONE.items()}


def clean_findings(value):
    """Known codes with plausible counts (the snapshot comes over the network)."""
    if not isinstance(value, dict):
        return None
    return {code: int(n) for code, n in value.items()
            if code in CODES and isinstance(n, int) and not isinstance(n, bool)
            and 1 <= n <= 99999}


def clean_repairs(value):
    if not isinstance(value, list):
        return None
    return sorted({code for code in value if code in REPAIRS})


def clean_last(value):
    """{code: epoch seconds} of known codes."""
    if not isinstance(value, dict):
        return {}
    return {code: float(at) for code, at in value.items()
            if code in CODES and isinstance(at, (int, float)) and not isinstance(at, bool)
            and 1e9 < at < 1e10}


def _times(n):
    return {1: 'einmal', 2: 'zweimal', 3: 'dreimal'}.get(n, f'{n} mal')


def _clock(at, now):
    """'um 8 Uhr 51', 'gestern um 19 Uhr 20' (now: datetime, naive = local)."""
    when = datetime.datetime.fromtimestamp(at, now.tzinfo)
    clock = f"{when.hour} Uhr" if when.minute == 0 else f"{when.hour} Uhr {when.minute}"
    return f"gestern um {clock}" if when.date() < now.date() else f"um {clock}"


def _findings(snapshot, now):
    """Pi and server findings, current ones first, each most severe first."""
    out = []
    for side in ('log', 'server_log'):
        findings = clean_findings(snapshot.get(f'{side}_findings')) or {}
        last = clean_last(snapshot.get(f'{side}_last'))
        for code, n in findings.items():
            at = last.get(code)
            current = at is None or now.timestamp() - at < ONGOING
            out.append(dict(side=side, code=code, n=n, at=at, current=current,
                            severity=CODES[code][0]))
    out.sort(key=lambda f: (not f['current'], f['severity'], -f['n']))
    return out


def _repairs(snapshot):
    return [code for side in ('log', 'server_log')
            for code in clean_repairs(snapshot.get(f'{side}_repairs')) or []]


def _what(finding, lore):
    full = lore == 'full'
    what, fix = (ADVICE_LORE if full else ADVICE)[finding['code']]
    place = _PLACES[finding['side']]
    bei, log = (place[1], place[3]) if full else (place[0], place[2])
    fill = dict(Bei=bei, bei=bei[0].lower() + bei[1:], log=log, n=finding['n'])
    return what.format(**fill), fix.format(**fill)


def _sentence(finding, lore, now, advise=True):
    """One finding as spoken text: what, how often/when, and the remedy if current."""
    full, code, n = lore == 'full', finding['code'], finding['n']
    what, fix = _what(finding, lore)
    once = code in ('limited', 'display_down', 'llm_down', 'logsync', 'errors')
    seen = [] if once or n == 1 else [_times(n)]
    if finding['at'] is not None and code not in ('limited', 'errors'):
        seen.append(f"zuletzt {_clock(finding['at'], now)}")
    text = what + (f", {', '.join(seen)}" if seen else '') + '.'
    if not finding['current']:
        return text + (" Seitdem herrscht Ruhe." if full else " Seitdem ist Ruhe.")
    if not advise:
        return text
    if code in SELF_REPAIR:
        text += (" Ich vollziehe den Ritus der Instandsetzung jetzt selbst." if full
                 else " Ich versuche das jetzt selbst zu beheben.")
        return f"{text} Hilft das nicht: {fix}."
    if fix.startswith(_NO_ACTION):
        return f"{text} {fix}."
    return text + (f" Empfohlener Ritus: {fix}." if full else f" Vorschlag: {fix}.")


# Remedies that are a statement, not a request ("Nichts zu tun ...").
_NO_ACTION = ('Nichts', 'Kein', 'Er ', 'Sie ', 'Es ')


def _count(n, one, many, a='ein'):
    """'ein Problem', 'eine Störung' (a='eine'), 'zwei Probleme'."""
    word = {1: a, 2: 'zwei', 3: 'drei', 4: 'vier', 5: 'fünf'}.get(n, str(n))
    return f"{word} {one if n == 1 else many}"


def _upper(text):
    return text[:1].upper() + text[1:]


def briefing_sentence(snapshot, lore='off', now=None):
    """Current problems for the morning litany (at most three), or None. Past
    ones are left to "Selbsttest"."""
    now = now or datetime.datetime.now()
    current = [f for f in _findings(snapshot, now) if f['current']]
    if not current:
        return None
    named = [_sentence(f, lore, now, advise=False) for f in current[:BRIEFING_ITEMS]]
    rest = len(current) - len(named)
    if lore in ('billy', 'billy_full'):
        head = "Bei mir hakt was:"
    elif lore == 'full':
        head = "Der Auspex meldet Störungen:"
    else:
        head = "Selbsttest meldet:"
    text = f"{head} {' '.join(named)}"
    if rest:
        text += f" Dazu {_count(rest, 'weiterer Punkt', 'weitere Punkte')}."
    return text + (" Einzelheiten mit Selbsttest." if lore != 'full'
                   else " Einzelheiten offenbart der Selbsttest.")


def selftest_text(snapshot, lore='off', now=None):
    """Answer to "Selbsttest": a verdict first, then each finding in plain words,
    with cause and remedy for current ones and what was repaired."""
    now = now or datetime.datetime.now()
    billy, full = lore in ('billy', 'billy_full'), lore == 'full'
    if 'log_findings' not in snapshot and 'server_log_findings' not in snapshot:
        if billy:
            return "Hab meine Logs noch nicht durch, frag gleich nochmal."
        if full:
            return "Der Auspex meiner Protokolle läuft noch. Ergebnis folgt in wenigen Minuten."
        return "Ich prüfe meine Protokolle noch. Ergebnis folgt in wenigen Minuten."
    findings = _findings(snapshot, now)
    repairs = [REPAIR_DONE[code][1 if full else 0] + '.' for code in _repairs(snapshot)]
    current = sum(f['current'] for f in findings)
    past = len(findings) - current
    if not findings:
        verdict = ("Hab meine Logs durchgesehen, Boss. Alles sauber in den letzten 24 Stunden."
                   if billy else
                   "Auspex der Protokolle abgeschlossen. Keine Störungen in den letzten "
                   "24 Stunden. Der Maschinengeist ist zufrieden." if full else
                   "Selbsttest abgeschlossen. Ich habe in den letzten 24 Stunden keine "
                   "Störungen.")
        return ' '.join([verdict] + repairs)
    if billy:
        verdict = ("Hab meine Logs durchgesehen, Boss. "
                   + ("Gerade läuft alles." if not current else
                      f"Gerade hakt's an {_count(current, 'Stelle', 'Stellen', 'einer')}."))
    elif full:
        verdict = ("Auspex der Protokolle abgeschlossen. "
                   + ("Die Maschinengeister sind derzeit ruhig." if not current else
                      _upper(f"{_count(current, 'Maschinengeist zürnt', 'Maschinengeister zürnen')}.")))
    else:
        verdict = ("Selbsttest abgeschlossen. "
                   + ("Derzeit läuft alles." if not current else
                      f"Ich habe derzeit {_count(current, 'Problem', 'Probleme')}."))
    if past:
        verdict += (f" In den letzten 24 Stunden gab es "
                    f"{_count(past, 'Störung, die vorbei ist', 'Störungen, die vorbei sind', 'eine')}.")
    named = [_sentence(f, lore, now) for f in findings[:SELFTEST_ITEMS]]
    rest = len(findings) - len(named)
    if rest:
        named.append(f"Dazu {_count(rest, 'weiterer Punkt', 'weitere Punkte')}.")
    return ' '.join([verdict] + named + repairs)
