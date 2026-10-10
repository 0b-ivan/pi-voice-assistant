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

**Kein Rauschen:** Einzelne Aussetzer liegen unter der Schwelle. Stille oder ein zu kurzer Tastendruck (`no_speech`, `too_short`) gelten nicht als Fehler. Text der eigenen Dienste wird nie nach Mustern durchsucht (ein Transkript mit „out of memory“ ist kein OOM), dort zählen nur Event-Namen. Bekannt harmlose Zeilen (brcmfmac, bluetoothd, PipeWire, „USB disconnect“ beim Abziehen des Sticks …) werden übergangen; eigene Muster kommen mit `LOGWATCH_IGNORE` dazu (reguläre Ausdrücke, getrennt durch `||`). Ein Stopp-Timeout beim Deploy zählt nicht als Absturz, ebenso wenig der Neustart der Anzeige mit Status 75 nach neuem Code. Wird der Stick beim Schreiben gezogen, meldet der Kernel „device offline“ und danach verlorene Schreibzugriffe und ein abgebrochenes ext4-Journal; diese Zeilen zum selben Gerät in den 5 s danach sind kein Datenträgerfehler. Dieselben Fehler ohne „device offline“ oder später zählen weiter. Ins Journal schreibt der Selbsttest ein `selftest`-Event nur, wenn sich das Ergebnis ändert.

**Selbst beheben:** Kleine Fehler behebt der Selbsttest über den root-Wartungsdienst, denn die Sprachdienste haben keine Root-Rechte ([Wartungsmodus](maintenance.md)):

- Pi: `pi-display` hat systemd aufgegeben (Startlimit erreicht) → `restart-display` (reset-failed, restart).
- Pi: Stick steckt, aber die Log-Kopie läuft nicht (Timer steht oder Kopie fehlgeschlagen) → `logsync`.
- CT 107: `servitor-llm` aufgegeben → `restart-llm`.

Der Worker startet nur eine Unit im Zustand „failed“ neu, eine Anforderung kann also nie einen laufenden Dienst durchstarten. Je Reparatur höchstens alle 30 Minuten und dreimal am Tag; 20 Sekunden danach wird geprüft. Hat es geholfen, steht „Meine Anzeige habe ich neu gestartet“ im nächsten Bericht, sonst „Meine Anzeige ist ausgefallen, und mein Neustart hat nicht geholfen“. Auf „Selbsttest“ prüfen Pi und Server sofort neu und reparieren ohne die 30-Minuten-Pause (die drei Versuche am Tag bleiben). Abstürze von pi-ptt selbst fängt systemd (`Restart=on-failure`) ab, der Selbsttest meldet sie.

## Im Morgenbericht und auf Nachfrage

Proximus spricht von sich selbst: der Pi ist er („meine Anzeige“, „bei mir“), CT 107 sein Server (Lore VOLL: „mein Kogitator“). Ein Befund, der in der letzten Stunde noch auftrat, gilt als **aktuell**; ältere nennt er mit Uhrzeit und „Seitdem ist Ruhe“. Jeder Code hat in `ADVICE` (Lore VOLL: `ADVICE_LORE`) eine Beschreibung in eigenen Worten und einen Lösungsvorschlag.

Eine spätere passende Erfolgsmeldung kennzeichnet den Befund schon innerhalb dieser Stunde als vergangen: `local_llm_ready` nach erfolgreichem Aufwärmen oder Antworten des lokalen Sprachkerns, `openrouter_ready` nach einer Cloud-Antwort und `ready` nach geladenem Sprachdienst. Ein bloß gestarteter Sprachdienst beweist keinen reparierten lokalen Sprachkern. Der Selbsttest sagt dann „Danach wieder erfolgreich ausgeführt.“ und gibt keinen Reparaturvorschlag mehr; ein neuer Fehler macht den Befund wieder aktuell. Alte OOM-Einträge bleiben mit ihrer ursprünglichen Uhrzeit erhalten, statt die Logs pauschal zu löschen oder Speichermangel auszublenden.

Der Morgenbericht nennt nach den Updates höchstens drei **aktuelle** Befunde ohne Vorschlag, die schwersten zuerst; vergangene erwähnt er nicht. Beispiel: „Selbsttest meldet: Meine Stromversorgung ist eingebrochen, zweimal, zuletzt um 11 Uhr 20. Einzelheiten mit Selbsttest.“

„Selbsttest“, „Systemdiagnose“, „Systemcheck“, „Prüfe deine Logs“ oder „Logs auswerten“ (auch so, wie Vosk small sie hört: „selbst“, „selbst test“, „führer selbst das durch“) gibt erst ein Urteil, dann bis zu fünf Befunde, aktuelle zuerst, jeweils mit Vorschlag; was er selbst reparieren kann (Log-Ablage, Anzeige, lokaler Sprachkern), versucht er sofort. Beispiel: „Selbsttest abgeschlossen. Ich habe derzeit ein Problem. In den letzten 24 Stunden gab es eine Störung, die vorbei ist. Ich kann meine Protokolle nicht auf den Gedächtniskern schreiben, zuletzt um 11 Uhr 29. Ich versuche das jetzt selbst zu beheben. Hilft das nicht: Den Gedächtniskern einmal ab- und wieder anstecken. Mein Weckwort-Lauscher ist wiederholt ausgefallen, 8 mal, zuletzt um 8 Uhr 50. Seitdem ist Ruhe.“ Lore VOLL: „Auspex der Protokolle abgeschlossen. Ein Maschinengeist zürnt. … Empfohlener Ritus: …“; Billy: „Hab meine Logs durchgesehen, Boss. Gerade hakt's an einer Stelle. …“

Die Befunde des Pi reisen als Codes mit Anzahl im Status-Snapshot (`log_findings`, `log_repairs`, `log_last` mit dem Zeitpunkt des letzten Auftretens, optional `log_resolved` mit dem späteren Erfolg); der Server lässt nur bekannte Codes durch und ergänzt seine eigenen. Fehlt die Leseberechtigung für das Journal, meldet der Selbsttest „Systemprotokoll nicht lesbar“ statt eines sauberen Systems.

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
