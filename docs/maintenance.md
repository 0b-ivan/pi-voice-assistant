# Wartungsmodus

Updates des Pi und Neustarts von Pi und CT 107 löst Proximus nur im Wartungsmodus aus, und jede Aktion muss mit **Taste E** bestätigt werden (B bricht ab, 20 s Frist). **Den Server aktualisiert Proximus nie** (Entscheidung des Bedieners, 08.10.2026): Menü, Sprache und Server lehnen das ab, auf CT 107 ist der Update-Dienst gar nicht installiert, und wartende Server-Updates zeigt oder sagt Proximus nicht an – der Server wird manuell gewartet.

**Einschalten:** Menü „Wartung“ oder „Wartungsmodus“ sagen. Das Display zeigt dann WARTUNG: wartende Updates des Pi (Sicherheitsupdates rot), darunter die Aktionen „Pi aktualisieren“, „Pi neu starten“, „Server neu starten“, „Wartung beenden“. PiTFT-Tasten wählen, E wählt aus; danach steht unten rot „…? E = Ja · B = Nein“ und Proximus sagt an, was passiert („Pi aktualisieren: 12 Pakete. Bestätigen mit Taste E, abbrechen mit B.“). Per Sprache geht dasselbe: „Aktualisiere den Pi“, „Starte den Server neu“, „Wartung beenden“ („Aktualisiere den Server“ wird abgelehnt) – die Bestätigung bleibt bei der Taste. Nach 10 min ohne Eingabe endet der Modus (nicht während einer laufenden Aktion); solange er aktiv ist, geht Proximus nicht in Ruhe oder Schlaf.

**Ausführung:** Die Sprachdienste bekommen keine Root-Rechte. Sie legen nur eine leere Datei in `/run/proximus-maintenance/requests/` ab (`update` oder `reboot`); eine systemd-Path-Unit startet dann als root [`proximus-maintenance`](../deploy/maintenance/proximus-maintenance), das genau zwei feste Aktionen kennt:

- `update`: `apt-get update`, dann `apt-get -y upgrade` (keine neuen Pakete, keine Entfernungen, bestehende Konfigurationsdateien bleiben). Danach `status.json` mit Anzahl, Ergebnis und ob ein Neustart nötig ist (`/run/reboot-required` oder ein neuerer Kernel als der laufende).
- `reboot`: `systemctl reboot` nach 3 s.

Den Server-Neustart stößt der Pi über `POST /v1/maintenance` an (nur `reboot`, `update` gibt 403; Token, nur über die LAN-Adresse – über den Cloudflare-Tunnel antwortet der Server mit 403 –, höchstens alle 5 min). Fortschritt und Ergebnis liest der Pi alle 10 s (`status.json` lokal, `GET /v1/status` vom Server) und sagt das Ergebnis an, auch wenn Alarme stumm sind: „Pi: Aktualisierung abgeschlossen, 12 Pakete installiert. Neustart empfohlen.“ Danach zählt er die wartenden Updates neu.

Der Proxmox-Host wird bewusst nicht über Proximus gewartet.

**Reparaturen des Selbsttests** ([Logs und Selbsttest](logs.md)) laufen über denselben Worker, aber ohne Wartungsmodus und ohne `status.json`: `restart-display` (Pi) und `restart-llm` (CT 107) starten `pi-display` bzw. `servitor-llm` nur neu, wenn systemd die Unit aufgegeben hat (Zustand „failed“). Auslöser sind die Path-Units `proximus-repair-display.path` bzw. `proximus-repair-llm.path` mit der Vorlage `proximus-repair@.service`; `install.sh` richtet je nach Host die passende ein.

## Einrichtung

Auf jedem Host einmal als root ([`deploy/maintenance/install.sh`](../deploy/maintenance/install.sh)); es installiert Skript, Units, `tmpfiles.d`-Eintrag (Anforderungsordner gehört der Gruppe des Dienstnutzers) und ein Drop-in, das dem Dienst trotz `ProtectSystem=strict` das Schreiben in den Anforderungsordner erlaubt:

```sh
sh deploy/maintenance/install.sh obivan pi-ptt             # Pi
sh deploy/maintenance/install.sh servitor servitor-voice reboot-only   # CT 107: nur Neustart
```
