"""Lesende Diagnose: eigene CT-107-Dienste und aggregierte lokale Systemfehler.

Keine LogWatch-Reparaturen, keine fremden Rohmeldungen im Bericht oder Modell.
"""
import datetime
import json
import os
import re
import selectors
import socket
import subprocess
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import logwatch

UNITS = ('servitor-voice.service', 'servitor-llm.service')
SYSTEM_SOURCE = 'ct107_system'
SYSTEM_LABEL = 'CT 107 (Systemjournal)'
WINDOW = 7200          # Standard; SERVITOR_DIAGNOSE_HOURS oder die Frage ändern ihn
MAX_WINDOW = 25 * 3600  # ein Kalendertag kann wegen der Zeitumstellung 25 Stunden haben
# Zeilenbudget je Quelle wächst mit dem Zeitraum: CT 107 schreibt im Normalbetrieb
# etwa 40 eigene Zeilen pro Stunde, 100 lassen Spielraum für Fehlerhäufungen.
MIN_LINES = 120
LINES_PER_HOUR = 100
MAX_LINES = 2500
BYTES_PER_LINE = 640   # eine JSON-Zeile mit den festen Feldern, großzügig
TIMEOUT = 8

# LogWatch-Regeln für Kernel-/Systemfehler auf CT 107. Andere Dienste
# werden ausschließlich als ANZAHL kritischer Meldungen ausgewertet.
# Eigene Modell-/Sprachfehler verwenden separat SERVER_RULES.
SYSTEM_RULES = tuple(
    rule for rule in logwatch.PI_RULES
    if rule.code in ('undervoltage', 'oom', 'disk_io', 'usb')
)


def budget(seconds):
    """Zeilen je Quelle für einen Zeitraum; Bytes ergeben sich daraus."""
    return max(MIN_LINES, min(MAX_LINES, -(-int(seconds) * LINES_PER_HOUR // 3600)))


def _capture(command, max_bytes=MIN_LINES * BYTES_PER_LINE):
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
                cap = max_bytes if buffer is output else 2048
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
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)
                limited = True
    return bytes(output), proc.returncode, limited, bool(error)


def _scope(command, source, since, now, capture, hostname, max_lines=MIN_LINES):
    """Eine begrenzte Journalabfrage. Fremder Freitext verlässt diese Ebene nicht."""
    try:
        raw, status, limited, warning = capture(command)
    except (OSError, subprocess.SubprocessError):
        return [], dict(read=0, discarded=0, limited=True,
                        error='Journal nicht erreichbar')
    entries, discarded = [], 0
    lines = raw.decode('utf-8', 'replace').splitlines()
    if len(lines) > max_lines:
        limited = True
        discarded += len(lines) - max_lines
        lines = lines[-max_lines:]
    for line in lines:
        try:
            fields = json.loads(line)
        except ValueError:
            fields = None
        item = logwatch.parse_entry(line)
        if item is None or not since <= item['at'] <= now:
            discarded += 1
            continue
        if source == 'own':
            if item['unit'] not in UNITS:
                discarded += 1
                continue
        else:
            # --system liest das lokale Journal, die Host-Prüfung verwirft
            # eventuell weitergeleitete Einträge anderer Rechner vollständig.
            if (not isinstance(fields, dict)
                    or fields.get('_HOSTNAME') != hostname):
                discarded += 1
                limited = True  # Herkunft nicht sicher: keine Entwarnung!
                continue
            if item['unit'] in UNITS or item['priority'] > 4:
                discarded += 1
                continue
            if not item['kernel']:
                # LogWatch darf nur die Priorität fremder Dienste sehen.
                # Keine fremden Nachrichtentexte, selbst nicht im internen
                # Bericht / im LLM: generische Fehlerzählung ab Schwellwert.
                item['text'] = ''
                item['event'] = None
        item['source'] = source
        entries.append(item)
    incomplete = bool(status or limited or warning)
    return entries, dict(read=len(entries), discarded=discarded,
                         limited=incomplete,
                         error='Journalabfrage fehlgeschlagen oder unvollständig'
                         if incomplete else None)


def read(now=None, capture=_capture, since=None, until=None, window=None):
    """Nur zwei festgelegte Quellen. Optionaler Tageszeitraum wird serverseitig erstellt."""
    now = time.time() if now is None else now
    end = int(now if until is None else until)
    since = int(end - (window or WINDOW) if since is None else since)
    if not 0 < end - since <= MAX_WINDOW or end > now + 60:
        raise ValueError('Diagnosezeitraum außerhalb des erlaubten Bereichs')
    lines = budget(end - since)
    if capture is _capture:
        def capture(command, _run=_capture):
            return _run(command, lines * BYTES_PER_LINE)
    args = ['--no-pager', '-q', '-o', 'json',
            '--output-fields=MESSAGE,PRIORITY,UNIT,_SYSTEMD_UNIT,_TRANSPORT,'
            '__REALTIME_TIMESTAMP,_HOSTNAME',
            '-n', str(lines + 1),
            '--since=@' + str(since), '--until=@' + str(end)]
    own_command = ['journalctl', *args, '-u', UNITS[0], '-u', UNITS[1]]
    # Nur CT 107, nur Priority 0..4. Kein Journal anderer Proxmox-Knoten,
    # kein journalctl --machine/--directory, keine fremde Logausgabe.
    system_command = ['journalctl', '--system', *args, '-p', '0..4']
    own, own_coverage = _scope(own_command, 'own', since, end, capture,
                               socket.gethostname(), lines)
    system, system_coverage = _scope(system_command, SYSTEM_SOURCE, since, end,
                                     capture, socket.gethostname(), lines)
    coverage = dict(
        read=own_coverage['read'] + system_coverage['read'],
        discarded=own_coverage['discarded'] + system_coverage['discarded'],
        since=since, until=end,
        limited=own_coverage['limited'] or system_coverage['limited'],
        error=own_coverage['error'] or system_coverage['error'],
        sources={'own': own_coverage, SYSTEM_SOURCE: system_coverage},
    )
    return own + system, coverage


def evidence(item):
    """Nur freigegebene Ereignis-Metadaten eigener Dienste, keine Prompts."""
    if item.get('source') == SYSTEM_SOURCE:
        return 'Systemmeldung nicht freigegeben'
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
    if re.match(r'^(?:[\w@.\-]+: )?(?:Main process exited|Failed with result|'
                r'Failed to start|Start request repeated)', message, re.I):
        return re.sub(r'(?i)\b(?:Bearer|token|password|secret)\b\s*[:=]\s*\S+',
                      '[entfernt]', message[:140])
    return 'Freitext nicht freigegeben'


def analyse(entries, coverage):
    own = sorted((e for e in entries
                  if e['unit'] in UNITS and e.get('source') != SYSTEM_SOURCE),
                 key=lambda e: e['at'])
    system = sorted((e for e in entries if e.get('source') == SYSTEM_SOURCE),
                    key=lambda e: e['at'])
    last, recovered = {}, {}
    findings = logwatch.analyse(own, logwatch.SERVER_RULES, logwatch.SERVER_UNITS,
                                last=last, recovered=recovered)
    groups = []
    unknown_count = 0
    ignore = logwatch.compile_ignore()
    for e in own:
        if e['priority'] <= 3 and logwatch.classify(
                e, logwatch.SERVER_RULES, logwatch.SERVER_UNITS, ignore) is None:
            unknown_count += 1
    for code, count in sorted(findings.items()):
        affected = [e for e in own if logwatch.classify(
            e, logwatch.SERVER_RULES, logwatch.SERVER_UNITS, ignore) == code]
        last_at = last.get(code)
        proven = bool(recovered.get(code, 0) > (last_at or 0))
        groups.append(dict(code=code, category=logwatch.CODES.get(code, (2, code))[1],
                           count=count, units=sorted({e['unit'] for e in affected}),
                           first=affected[0]['at'] if affected else None, last=last_at,
                           recovery='belegt' if proven else 'nicht belegt',
                           evidence=[dict(at=e['at'], unit=e['unit'], text=evidence(e))
                                     for e in affected[-3:]]))
    system_last = {}
    system_findings = logwatch.analyse(
        system, SYSTEM_RULES, {}, last=system_last)
    for code, count in sorted(system_findings.items()):
        groups.append(dict(
            code=code, category=logwatch.CODES.get(code, (2, code))[1],
            count=count, units=[SYSTEM_LABEL],
            first=None, last=system_last[code], recovery='nicht belegt',
            evidence=[], scope='system_aggregiert',
        ))
    # Fremde Systemmeldungen unterhalb der LogWatch-Schwelle bleiben nicht
    # sichtbar, werden aber als nicht ausgeschlossene Fehler transparent.
    unclassified_system = sum(
        1 for e in system if not e['kernel'] and e['priority'] <= 3)
    return dict(
        coverage=coverage, services=list(UNITS),
        system_scope=SYSTEM_LABEL, findings=groups,
        unknown_count=unknown_count,
        system_error_count=unclassified_system,
        excerpts=[dict(at=e['at'], unit=e['unit'], text=evidence(e))
                  for e in own if e['priority'] <= 3][-8:],
    )


def describe(report, timezone='Europe/Berlin'):
    # CT 107 läuft in UTC; Bedienerzeit und benannte Zeitzone sind notwendig.
    try:
        tz = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = datetime.timezone.utc
        timezone = 'UTC'
    c = report['coverage']
    a = datetime.datetime.fromtimestamp(c['since'], tz).strftime('%d.%m. %H:%M')
    b = datetime.datetime.fromtimestamp(c['until'], tz).strftime('%d.%m. %H:%M')
    scopes = c.get('sources', {})
    own_count = scopes.get('own', {}).get('read', c['read'])
    system_count = scopes.get(SYSTEM_SOURCE, {}).get('read', 0)
    parts = [f"Lesende Diagnose von servitor-voice.service und servitor-llm.service "
             f"sowie aggregierten Systemfehlern auf CT 107, {a}–{b} ({timezone}).",
             f"{own_count} eigene und {system_count} lokale Systemeinträge ausgewertet; "
             f"{c['discarded']} verworfen. Fremde Rohlogs werden nicht angezeigt."]
    if c['limited']:
        partial = [name for name, meta in scopes.items() if meta['limited']]
        parts.append('Die Journalabfrage ist unvollständig'
                     + (f" ({', '.join(partial)})" if partial else '')
                     + '; Ursache und Status bleiben offen.')
    elif not c['read']:
        parts.append('Keine Protokolle im Zeitraum lesbar; keine Entwarnung möglich.')
    for item in report['findings']:
        when = datetime.datetime.fromtimestamp(item['last'], tz).strftime('%d.%m. %H:%M')
        parts.append(f"{item['category']} bei {', '.join(item['units'])}: "
                     f"{item['count']} Ereignisse, zuletzt {when}; "
                     f"Wiederherstellung {item['recovery']}. "
                     "Die eigentliche Ursache ist damit nicht bewiesen.")
    if not report['findings'] and c['read']:
        parts.append('Keine bekannte Fehlerkategorie über ihrer Erkennungsschwelle. '
                     'Unbekannte Ursachen sind nicht ausgeschlossen.')
    if report['unknown_count']:
        parts.append(f"{report['unknown_count']} unklassifizierte Fehl-/Warnmeldungen "
                     'in eigenen Diensten, Ursache und Dringlichkeit ungeklärt.')
    if (report['system_error_count'] and
            not any(item['code'] == 'errors' for item in report['findings'])):
        parts.append('Weitere System-Fehlmeldungen liegen unter der LogWatch-Schwelle '
                     'oder sind noch nicht klassifiziert.')
    if report['excerpts']:
        item = report['excerpts'][-1]
        parts.append(f"Auszug ({item['unit']}): {item['text']}.")
    parts.append('Nächster Schritt: betroffenen Dienst und konkretes Ereignis gezielt prüfen.')
    return ' '.join(parts)
