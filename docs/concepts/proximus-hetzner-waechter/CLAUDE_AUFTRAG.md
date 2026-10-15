# Auftrag: Hetzner-Wächter für Proximus umsetzen

Setze die Stufen 1 und 2 aus [KONZEPT.md](KONZEPT.md) um. Stufe 3 (Reparatur) nur, wenn der Bediener sie ausdrücklich freigegeben hat (Abschnitt 5 des Konzepts). Hintergrund und echte Messwerte: [VORFALL_2026-10-15.md](VORFALL_2026-10-15.md).

## Kontext

- `src/logwatch.py`: Vorbild für Regeln, Schwellen, Codes, `ADVICE`/`ADVICE_LORE`, Snapshot-Felder, Thread mit Intervall.
- `server/servitor_server.py`: startet `logwatch.server_watch()`; Snapshot für `briefing`/`selftest` (um Zeile 1292).
- `src/protocol.py`, `src/system_status.py`: erlaubte Snapshot-Felder und deren Prüfung.
- `src/intents.py`, `src/alarms.py`: Intent-Erkennung (Vosk-small-Schreibweisen beachten) und Alarme.
- `deploy/maintenance/`: Muster für root-Skripte, Path-/Timer-Units und `install.sh`.
- `docs/logs.md`, `docs/maintenance.md`: Ton und Struktur der Doku.

## Ergebnis

1. **Sammler** `deploy/hetzner-watch/proximus-watch-collect` mit `proximus-watch.service`, `proximus-watch.timer`, `proximus-watch.conf.example` und `install.sh` (legt Nutzer `proximus-watch`, Verzeichnis, Timer an; schreibt `authorized_keys` nur, wenn ein Schlüssel übergeben wird). Erhebt die Felder aus Konzept 3.1, schreibt atomar, Schema-Version im JSON. Erkennung verwaister Namespaces über `NetworkMode container:<id>` und `StartedAt`. Ohne Zusatzpakete auf Debian lauffähig.
2. **`src/hostwatch.py`** auf CT 107: Abruf per `ssh -i … -o BatchMode=yes -o StrictHostKeyChecking=yes`, Timeout, Größenlimit, Schemaprüfung; Regeln und Codes aus Konzept 3.3; externe HTTP-Proben aus `SERVITOR_HOSTWATCH_PROBES`. Abschaltbar mit `SERVITOR_HOSTWATCH=0`, standardmäßig aus, solange kein Ziel konfiguriert ist.
3. **Anbindung:** Snapshot-Felder `host_findings`, `host_last`, `host_checked_at` (Pi lässt nur bekannte Codes durch), Morgenbericht (höchstens zwei aktuelle Befunde), Intent `hostwatch`, Alarm für Schwere 0 mit Erholungsmeldung. Alle drei Lore-Stufen und Billy.
4. **Doku:** `docs/hetzner-watch.md` (Einrichtung auf beiden Seiten, Prüfbefehle), Verweis in README-Tabelle und Roadmap.

## Grenzen

- Kein Docker-Socket, keine Shell und kein root für CT 107. Kein `pull`, `prune`, `compose down`, keine Updates.
- Keine Geheimnisse, Host-Adressen oder Schlüssel im Repository.
- Keine kostenpflichtigen API-Aufrufe; kein LLM in der Auswertung.
- Kein Deployment, kein PR ohne Aufforderung.

## Prüfen

- Offline-Tests mit festen `status.json`-Beispielen: jede Regel, Schwellen, Ignorierliste, Lazytainer-Schlafphase, verwaister Namespace (Werte aus dem Vorfall: Kind gestartet 4 Wochen vor dem Elternteil), veralteter Bericht, kaputtes JSON, Übergröße, SSH-Fehler.
- Sammler mit gefälschten `docker`/`df`/`journalctl`-Ausgaben testen (Befehle per Umgebungsvariable austauschbar).
- Vorhandene Tests weiter grün. Echtbetrieb auf dem Hetzner-Server nur als geprüft melden, wenn tatsächlich ausgeführt.

Abschluss knapp: geänderte Dateien, Einrichtungsbefehle für Hetzner und CT 107, tatsächliche Prüfungen, offene Punkte, zwei Beispielansagen.
