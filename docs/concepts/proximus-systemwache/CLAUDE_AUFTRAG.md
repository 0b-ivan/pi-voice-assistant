# Auftrag: Systemwache umsetzen

**Noch nicht freigegeben.** Dieser Auftrag wird neu gefasst, sobald der Bediener die Entscheidungen aus Abschnitt 12 von [KONZEPT.md](KONZEPT.md) getroffen hat. Die erste Fassung (fest verdrahteter Sammler auf dem Hetzner-Server) ist überholt; ihr Bericht-Modus lebt als Zugriffsstufe R im Konzept weiter.

## Bereits feststehend

- Umsetzung in Stufen nach Abschnitt 10 des Konzepts, Stufe 1 und 2 zuerst.
- Vorbilder im Code: `src/logwatch.py` (Regeln, Codes, Schwellen, Snapshot-Felder, Thread), `server/servitor_server.py` (Status, Morgenbericht, Web-Client-Anmeldung), `deploy/maintenance/` (Root-Skripte, Units, `install.sh`), `src/intents.py`, `src/alarms.py`.
- Grenzen: kein Sprachmodell erzeugt Befehle; Geheimnisse nur auf CT 107; keine Updates, kein `pull`/`prune`/`compose down`; keine kostenpflichtigen API-Aufrufe; kein Deployment und kein PR ohne Aufforderung.
- Prüfung: Offline-Tests mit aufgezeichneten Sondenausgaben und `/metrics`-Beispielen, darunter der Vorfall vom 15.10.2026 (verwaistes Netz, fehlende Image-Tags, Platte 97 %, Swap 14/15 GiB, OOM-Kill). Echtbetrieb nur als geprüft melden, wenn tatsächlich ausgeführt.
