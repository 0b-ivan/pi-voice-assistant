# Logs und Selbsttest

## Wo die Logs liegen

**Pi:** Das Journal liegt nur noch im RAM (`Storage=volatile`, `/run/log/journal`, höchstens 16 MB) und geht bei einem Neustart verloren. Steckt der Gedächtnis-Stick, kopiert [`proximus-logsync`](../deploy/logsync/proximus-logsync) es als Text nach `/mnt/proximus-memory/logs/JJJJ-MM-TT.log`:

- alle 5 Minuten (`proximus-logsync.timer`),
- sofort beim Anstecken des Sticks (pi-ptt legt dann eine Anforderung ab),
- einmal beim Herunterfahren oder Neustart (`proximus-logsync-shutdown.service`).

Ist der Stick abgezogen, bleibt das Journal nur temporär im RAM. Beim nächsten Anstecken wird nachgeholt, was seit der letzten Kopie dazugekommen ist (Cursor je Bootvorgang auf dem Stick), solange es noch im RAM liegt. Ältere Tage werden mit gzip gepackt, nach 90 Tagen oder über 1 GB fallen die ältesten weg. Der Ordner gehört root (0750); die Logs enthalten Transkripte und Antworten, wie das Gedächtnis selbst. rsyslog wird abgeschaltet, sonst schriebe es eine zweite Kopie auf die SD-Karte. apt und dpkg schreiben weiter ihre eigenen kleinen Dateien unter `/var/log`.

Auf dem Stick lesen: `zcat /mnt/proximus-memory/logs/2026-10-09.log.gz | grep pi-ptt` (als root). Live wie bisher: `journalctl -u pi-ptt.service -f`.

**CT 107:** unverändert im persistenten Journal des Containers.

## Selbsttest

[`src/logwatch.py`](../src/logwatch.py) läuft auf dem Pi (in pi-ptt) und auf CT 107 (in servitor-voice): 3 Minuten nach dem Start, danach alle 30 Minuten. Er liest das Journal der letzten 24 Stunden – die eigenen Dienste vollständig, das übrige System nur ab Priorität „warning“ – und ordnet die Zeilen festen Regeln zu:

| Befund | Quelle | ab |
|---|---|---|
| Unterspannung, Arbeitsspeicher erschöpft (OOM), Datenträgerfehler (I/O, ext4) | Kernel | 1 |
| Dienst abgestürzt (pi-ptt, pi-display; servitor-voice, servitor-llm) | systemd „Failed with result“ | 1 |
| Modelle nicht geladen, lokaler Sprachkern nicht bereit | Server-Events | 1 |
| Tick-Fehler, Spracherkennung, Sprachkern, Anfragen fehlgeschlagen (Server) | Events | 3 |
| Sprachausgabe | Events | 2 |
| USB-Störung (Enumerierung, Überstrom) | Kernel | 3 |
| Tastenleiste, Weckwort, OpenRouter-Ausfall, Stimmerkennung | Events | 3 |
| Serververbindung abgebrochen | Events | 5 |
| sonstige Fehlermeldungen (err und schlimmer) | System | 5 |

**Kein Rauschen:** Einzelne Aussetzer liegen unter der Schwelle. Stille oder ein zu kurzer Tastendruck (`no_speech`, `too_short`) gelten nicht als Fehler. Text der eigenen Dienste wird nie nach Mustern durchsucht (ein Transkript mit „out of memory“ ist kein OOM), dort zählen nur Event-Namen. Bekannt harmlose Zeilen (brcmfmac, bluetoothd, PipeWire, „USB disconnect“ beim Abziehen des Sticks …) werden übergangen; eigene Muster kommen mit `LOGWATCH_IGNORE` dazu (reguläre Ausdrücke, getrennt durch `||`). Ein Stopp-Timeout beim Deploy zählt nicht als Absturz. Ins Journal schreibt der Selbsttest ein `selftest`-Event nur, wenn sich das Ergebnis ändert.

**Selbst beheben:** Kleine Fehler behebt der Selbsttest über den root-Wartungsdienst, denn die Sprachdienste haben keine Root-Rechte ([Wartungsmodus](maintenance.md)):

- Pi: `pi-display` hat systemd aufgegeben (Startlimit erreicht) → `restart-display` (reset-failed, restart).
- Pi: Stick steckt, aber die Log-Kopie läuft nicht (Timer steht oder Kopie fehlgeschlagen) → `logsync`.
- CT 107: `servitor-llm` aufgegeben → `restart-llm`.

Der Worker startet nur eine Unit im Zustand „failed“ neu, eine Anforderung kann also nie einen laufenden Dienst durchstarten. Je Reparatur höchstens alle 30 Minuten und dreimal am Tag; 20 Sekunden danach wird geprüft. Hat es geholfen, steht „Anzeige neu gestartet“ im nächsten Bericht, sonst „Anzeige ausgefallen, Neustart erfolglos“. Abstürze von pi-ptt selbst fängt systemd (`Restart=on-failure`) ab, der Selbsttest meldet sie.

## Im Morgenbericht und auf Nachfrage

Der Morgenbericht nennt nach den Updates höchstens drei Befunde, die schwersten zuerst, Pi und Server gemeinsam; der Rest wird nur gezählt. Ohne Befund kommt kein Satz. Beispiel: „Selbsttest: Pi: Unterspannung, zweimal. Pi: Spracherkennung gestört, 4 mal. Server: Lokaler Sprachkern abgestürzt, einmal. Und 1 weiterer Punkt.“ (Lore VOLL: „Auspex der Protokolle: …“, Billy: „In den Logs: …“).

„Selbsttest“, „Systemdiagnose“, „Prüfe deine Logs“ oder „Logs auswerten“ liest bis zu fünf Punkte vor, oder „Selbsttest abgeschlossen. Keine Auffälligkeiten in den letzten 24 Stunden.“

Die Befunde des Pi reisen als Codes mit Anzahl im Status-Snapshot (`log_findings`, `log_repairs`); der Server lässt nur bekannte Codes durch und ergänzt seine eigenen. Fehlt die Leseberechtigung für das Journal, meldet der Selbsttest „Systemprotokoll nicht lesbar“ statt eines sauberen Systems.

Abschalten: `PTT_LOGWATCH=0` in `/etc/pi-ptt.env` bzw. `SERVITOR_LOGWATCH=0` in `/etc/servitor-voice.env`.

## Einrichtung

Beide Dienste brauchen Lesezugriff aufs Journal: `SupplementaryGroups=… systemd-journal` steht in [`deploy/pi-ptt.service`](../deploy/pi-ptt.service) und [`server/servitor-voice.service`](../server/servitor-voice.service) (nur lesen; damit sehen sie auch die Logs anderer Dienste des Hosts). Danach einmal als root:

```sh
# Pi: Wartungs-Worker (jetzt mit restart-display), dann Logs auf den Stick
sudo sh deploy/maintenance/install.sh obivan pi-ptt
sudo sh deploy/logsync/install.sh
sudo -n /usr/local/sbin/pi-voice-install <commit>   # neue Unit mit systemd-journal

# CT 107: Worker mit restart-llm, Unit neu einspielen
sh deploy/maintenance/install.sh servitor servitor-voice reboot-only
sh server/install-ct.sh
```

`deploy/logsync/install.sh` stellt journald auf RAM um, schaltet rsyslog ab und archiviert ein altes Journal von der SD-Karte (`/var/log/journal`) als `sd-journal-<datum>.log.gz` auf den Stick, bevor es gelöscht wird; ohne Stick bleibt es liegen, bis das Skript mit Stick erneut läuft.

Prüfen:

```sh
systemctl list-timers proximus-logsync.timer
cat /run/proximus-maintenance/logsync.json      # {"at":…,"stick":true,"ok":true,…}
sudo ls -l /mnt/proximus-memory/logs
journalctl -u pi-ptt.service | grep '"selftest"' | tail -n 1
```
