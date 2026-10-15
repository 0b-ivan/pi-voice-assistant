"""Read-only, allowlisted diagnostic journal inspection for the web chat."""
import datetime
import json
import os
import re
import selectors
import subprocess
import time

import logwatch

UNITS = ('servitor-voice.service', 'servitor-llm.service')
WINDOW = 7200
MAX_LINES = 120
MAX_BYTES = 65536
TIMEOUT = 8


def _capture(command):
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = bytearray()
    error = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, output)
    selector.register(proc.stderr, selectors.EVENT_READ, error)
    deadline = time.monotonic() + TIMEOUT
    limited = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                limited = True
                break
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = key.data
                cap = MAX_BYTES if buffer is output else 2048
                if len(buffer) + len(chunk) > cap:
                    limited = True
                    buffer.extend(chunk[:max(0, cap - len(buffer))])
                    break
                buffer.extend(chunk)
            if limited:
                break
    finally:
        selector.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=1)
    return bytes(output), proc.returncode, limited, bool(error)


def read(now=None, capture=_capture):
    now = time.time() if now is None else now
    since = int(now - WINDOW)
    command = ['journalctl', '--no-pager', '-q', '-o', 'json',
               '--output-fields=MESSAGE,PRIORITY,UNIT,_SYSTEMD_UNIT,_TRANSPORT,'
               '__REALTIME_TIMESTAMP', '-n', str(MAX_LINES),
               '--since=@' + str(since), '--until=@' + str(int(now)),
               '-u', UNITS[0], '-u', UNITS[1]]
    try:
        raw, status, limited, warning = capture(command)
    except (OSError, subprocess.SubprocessError):
        return [], dict(read=0, discarded=0, since=since, until=int(now),
                        limited=True, error='Journal nicht erreichbar')
    entries = []
    discarded = 0
    lines = raw.decode('utf-8', 'replace').splitlines()
    for line in lines[:MAX_LINES]:
        item = logwatch.parse_entry(line)
        if item is None or item['unit'] not in UNITS or not since <= item['at'] <= now:
            discarded += 1
        else:
            entries.append(item)
    discarded += max(0, len(lines) - MAX_LINES)
    return entries, dict(read=len(entries), discarded=discarded, since=since,
                         until=int(now), limited=bool(limited or warning or status),
                         error='Journalabfrage fehlgeschlagen oder unvollständig'
                         if status or limited else None)


def evidence(item):
    """No raw transcripts, free text or arbitrary model instructions."""
    event = item.get('event')
    if event:
        try:
            data = json.loads(item['text'])
        except ValueError:
            data = {}
        if isinstance(data, dict):
            return ', '.join(
                key + '=' + str(data[key])[:40]
                for key in ('event', 'stage', 'code')
                if isinstance(data.get(key), str)
                and re.fullmatch(r'[a-zA-Z0-9_.-]{1,40}', data[key]))
    message = item['text']
    if re.match(r'^(?:[\\w@.\\-]+: )?(?:Main process exited|Failed with result|'
                r'Failed to start|Start request repeated)', message, re.I):
        return re.sub(r'(?i)\\b(?:Bearer|token|password|secret)\\b\\s*[:=]\\s*\\S+',
                      '[entfernt]', message[:140])
    return 'Freitext nicht freigegeben'


def analyse(entries, coverage):
    entries = sorted((e for e in entries if e['unit'] in UNITS), key=lambda e: e['at'])
    last, recovered = {}, {}
    findings = logwatch.analyse(entries, logwatch.SERVER_RULES, logwatch.SERVER_UNITS,
                                last=last, recovered=recovered)
    groups = []
    for code, count in sorted(findings.items()):
        affected = [e for e in entries if logwatch.classify(
            e, logwatch.SERVER_RULES, logwatch.SERVER_UNITS, logwatch.compile_ignore()) == code]
        last_at = last.get(code)
        proven = bool(recovered.get(code, 0) > (last_at or 0))
        groups.append(dict(code=code, category=logwatch.CODES.get(code, (2, code))[1],
                           count=count, units=sorted({e['unit'] for e in affected}),
                           first=affected[0]['at'] if affected else None, last=last_at,
                           recovery='belegt' if proven else 'nicht belegt',
                           evidence=[dict(at=e['at'], unit=e['unit'], text=evidence(e))
                                     for e in affected[-3:]]))
    return dict(coverage=coverage, services=list(UNITS), findings=groups,
                excerpts=[dict(at=e['at'], unit=e['unit'], text=evidence(e))
                          for e in entries if e['priority'] <= 3][-8:])


def describe(report):
    c = report['coverage']
    a = datetime.datetime.fromtimestamp(c['since']).strftime('%d.%m. %H:%M')
    b = datetime.datetime.fromtimestamp(c['until']).strftime('%d.%m. %H:%M')
    parts = [f"Lesende Diagnose von servitor-voice.service und servitor-llm.service, {a}–{b}.",
             f"{c['read']} Einträge gelesen, {c['discarded']} verworfen."]
    if c['limited']:
        parts.append('Die Journalabfrage ist unvollständig; Ursache und Status bleiben offen.')
    elif not c['read']:
        parts.append('Keine Protokolle im Zeitraum lesbar; keine Entwarnung möglich.')
    for entry in report['findings']:
        when = datetime.datetime.fromtimestamp(entry['last']).strftime('%d.%m. %H:%M')
        parts.append(f"{entry['category']} bei {', '.join(entry['units'])}: "
                     f"{entry['count']} Ereignisse, zuletzt {when}; "
                     f"Wiederherstellung {entry['recovery']}. "
                     "Die eigentliche Ursache ist damit nicht bewiesen.")
    if not report['findings'] and c['read']:
        parts.append('Keine bekannte Fehlerkategorie über ihrer Erkennungsschwelle. '
                     'Unbekannte Ursachen sind nicht ausgeschlossen.')
    if report['excerpts']:
        entry = report['excerpts'][-1]
        parts.append(f"Auszug ({entry['unit']}): {entry['text']}.")
    parts.append('Nächster Schritt: betroffenen Dienst und konkretes Ereignis gezielt prüfen.')
    return ' '.join(parts)
