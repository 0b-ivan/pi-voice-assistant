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

# code: (severity 0 = critical, 1 = fault, 2 = notice; spoken label)
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
REPAIRS = {
    'display': 'Anzeige neu gestartet',
    'llm': 'Lokaler Sprachkern neu gestartet',
    'logsync': 'Log-Ablage neu angestoßen',
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
)
# systemd results that mean a crash; 'timeout' only follows a slow stop.
_FAILED = re.compile(r"Failed with result '(exit-code|signal|core-dump|oom-kill|watchdog)'")
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


def analyse(entries, rules, units, ignore=None):
    """Counts of the findings that reach their threshold: {code: count}."""
    ignore = compile_ignore() if ignore is None else ignore
    counts = {}
    for entry in entries:
        code = classify(entry, rules, units, ignore)
        if code:
            counts[code] = counts.get(code, 0) + 1
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

    def _may_repair(self, code, now):
        times = [t for t in self.attempts.get(code, []) if now - t < WINDOW]
        self.attempts[code] = times
        return len(times) < REPAIRS_PER_DAY and (not times or now - times[-1] >= REPAIR_COOLDOWN)

    def _repair(self, findings, now):
        pending = []
        for repair in self.repairs:
            try:
                broken = repair.broken()
            except Exception:  # a broken probe must never stop the self-test
                broken = False
            if not broken:
                continue
            if not self._may_repair(repair.code, now):
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

    def check(self):
        """One self-test round (blocking: journal read and repairs)."""
        with self._lock:
            now = self.clock()
            try:
                entries, limited = self.reader(now - WINDOW, list(self.units))
            except (OSError, subprocess.SubprocessError):
                entries, limited = [], True
            findings = analyse(entries, self.rules, self.units, self.ignore)
            if limited:
                findings['limited'] = 1
            for check in self.checks:
                try:
                    code = check()
                except Exception:
                    code = None
                if code:
                    findings[code] = max(findings.get(code, 0), 1)
            self._repair(findings, now)
            self.done = [(t, code) for t, code in self.done if now - t < WINDOW]
            repairs = sorted({code for _, code in self.done})
            previous = self.result
            self.result = dict(at=now, findings=findings, repairs=repairs)
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

    def refresh(self):
        threading.Thread(target=self.check, name='logwatch-now', daemon=True).start()

    def snapshot_fields(self, prefix='log'):
        """Status-snapshot fields of the last result (none before the first check)."""
        result = self.result
        if result is None:
            return {}
        return {f'{prefix}_findings': dict(result['findings']),
                f'{prefix}_repairs': list(result['repairs'])}


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


def _times(n):
    return {1: 'einmal', 2: 'zweimal', 3: 'dreimal'}.get(n, f'{n} mal')


def _items(findings):
    """(severity, -count, text) per finding, most severe first."""
    out = []
    for code, n in (findings or {}).items():
        severity, label = CODES[code]
        once = code in ('limited', 'display_down', 'llm_down', 'logsync')
        text = (label if once
                else f"{n} {label}" if code == 'errors' else f"{label}, {_times(n)}")
        out.append((severity, -n, text))
    return sorted(out)


def _parts(snapshot):
    """Spoken items of Pi and server, ranked: [(severity, -count, text)]."""
    parts = []
    for prefix, name in (('log', 'Pi'), ('server_log', 'Server')):
        findings = clean_findings(snapshot.get(f'{prefix}_findings')) or {}
        repairs = clean_repairs(snapshot.get(f'{prefix}_repairs')) or []
        for severity, count, text in _items(findings):
            parts.append((severity, count, f"{name}: {text}"))
        for code in repairs:
            parts.append((3, 0, f"{name}: {REPAIRS[code]}"))
    return sorted(parts)


def _prefix(lore):
    if lore in ('billy', 'billy_full'):
        return "In den Logs:"
    if lore == 'full':
        return "Auspex der Protokolle:"
    return "Selbsttest:"


def briefing_sentence(snapshot, lore='off'):
    """At most three notable findings for the morning litany, or None."""
    parts = _parts(snapshot)
    if not parts:
        return None
    named = [text for _, _, text in parts[:BRIEFING_ITEMS]]
    rest = len(parts) - len(named)
    text = f"{_prefix(lore)} {'. '.join(named)}."
    if rest:
        text += f" Und {rest} {'weiterer Punkt' if rest == 1 else 'weitere Punkte'}."
    return text


def selftest_text(snapshot, lore='off'):
    """Answer to "Selbsttest": what the last check found, or that all is clean."""
    if 'log_findings' not in snapshot and 'server_log_findings' not in snapshot:
        if lore in ('billy', 'billy_full'):
            return "Hab die Logs noch nicht durch, frag gleich nochmal."
        return "Selbsttest läuft. Ergebnis folgt in wenigen Minuten."
    parts = _parts(snapshot)
    if not parts:
        if lore in ('billy', 'billy_full'):
            return "Logs sind sauber, Boss. Nichts Auffälliges in den letzten 24 Stunden."
        clean = "Selbsttest abgeschlossen. Keine Auffälligkeiten in den letzten 24 Stunden."
        return clean + " Der Maschinengeist ist zufrieden." if lore == 'full' else clean
    named = [text for _, _, text in parts[:SELFTEST_ITEMS]]
    rest = len(parts) - len(named)
    text = f"{_prefix(lore)} {'. '.join(named)}."
    if rest:
        text += f" Und {rest} {'weiterer Punkt' if rest == 1 else 'weitere Punkte'}."
    return text
