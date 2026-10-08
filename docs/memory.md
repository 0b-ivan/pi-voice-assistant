# Gedächtnis, Systemwartung und Netzwerk

## Gedächtniskern (USB-Stick)

Proximus speichert sein Gedächtnis nur auf einem USB-Stick mit ext4 und dem Label `PROXIMUS` ([`src/memory.py`](../src/memory.py)). Ohne Stick hat er kein Gedächtnis; steckt der Stick wieder, sind alle Erinnerungen zurück. Ab- und Anstecken sagt er an („Gedächtniskern entfernt …“ / „Gedächtniskern verbunden. N Einträge geladen.“); im Display zeigt eine Raute neben PROXIMUS den Zustand (gefüllt: verbunden, grau umrandet: fehlt), die Info-Seite eine Zeile „Gedächtnis“.

| Inhalt | Beispiel | Grenze |
|---|---|---|
| Fakten | „Merk dir, dass ich Ivan heiße.“ oder vom Modell gelernt | 200 Einträge |
| Direktiven | „Nenne Städte in Zukunft nur noch Makropolen.“, „Installiere die Humor-Erweiterung.“, „Ab jetzt antworte kürzer.“ | 20 |
| Verlauf | die letzten 6 Fragen und Antworten (4 gehen an das Modell) | 6 |

Befehle ohne Sprachmodell (auch offline): „Merk dir …“, „Installiere/Aktiviere die X-Erweiterung“, „Deinstalliere/Deaktiviere die X-Erweiterung“, „Vergiss …“, „Was weißt du über mich?“, „Welche Direktiven/Erweiterungen hast du?“.

**Lernen:** Mit Stick bekommt das Sprachmodell die Anweisung, dauerhafte Tatsachen und Anweisungen als eigene Zeile `MERKE: …` bzw. `DIREKTIVE: …` anzuhängen. Der Server schneidet sie ab (sie werden nie gesprochen) und schickt sie als `memory`-Ereignis an den Pi, der sie speichert.

**Ablauf:** Nur der Pi schreibt den Stick. Er schickt mit jedem Turn eine kompakte Kopie (Direktiven, neueste Fakten bis 6000 Zeichen, letzte 4 Runden) base64-kodiert im Header `X-Servitor-Memory`; im Statusheader steht `memory: on|off`. Der Server prüft und kürzt alles (`decode_header`) und baut daraus den Systemprompt: Persona, Lore, Gedächtnis, Uhrzeit. Gespeicherter Text gilt dort als Daten, nicht als Anweisung an das System. Ältere Pis ohne Gedächtnis-Unterstützung bleiben unverändert.

**Datenschutz:** Die Kopie geht mit jeder Frage an CT 107 und im Sprachkern-Modus AUTO an OpenRouter, wie die gesprochene Frage selbst. Im Modus LOKAL bleibt sie im Haus.

**Robustheit:** eine JSON-Datei `proximus/memory.json`, atomar geschrieben (temporäre Datei, fsync, rename, fsync des Ordners). Abziehen während des Schreibens verliert höchstens die letzte Änderung; eine beschädigte Datei wird als `memory.broken-<zeit>` beiseitegelegt. Ob der Stick steckt, prüft der Pi am Gerät `/dev/disk/by-label/PROXIMUS`, bevor er den Automount anfasst.

### Einrichtung (einmalig, löscht den Stick)

```sh
ssh obivan@172.22.9.128 'lsblk -o NAME,SIZE,TRAN,MOUNTPOINTS'   # Stick prüfen, hier /dev/sda1
ssh -t obivan@172.22.9.128 'sudo mkfs.ext4 -F -L PROXIMUS /dev/sda1 \
  && sudo mkdir -p /mnt/proximus-memory \
  && echo "LABEL=PROXIMUS /mnt/proximus-memory ext4 nofail,noatime,x-systemd.automount,x-systemd.device-timeout=3s 0 2" | sudo tee -a /etc/fstab \
  && sudo systemctl daemon-reload \
  && sudo systemctl start "$(systemd-escape -p --suffix=automount /mnt/proximus-memory)" \
  && sudo install -d -o obivan -g obivan -m 0700 /mnt/proximus-memory/proximus \
  && sudo install -D -m 0644 ~/pi-voice-setup/pi-ptt-memory.conf /etc/systemd/system/pi-ptt.service.d/memory.conf \
  && sudo install -m 0644 ~/pi-voice-setup/20proximus-update-lists /etc/apt/apt.conf.d/ \
  && sudo systemctl daemon-reload && sudo systemctl restart pi-ptt'
```

[`deploy/pi-ptt-memory.conf`](../deploy/pi-ptt-memory.conf) gibt dem abgeschotteten Dienst (`ProtectSystem=strict`) Schreibrecht auf `/mnt/proximus-memory`. Ein anderer Stick wird zum Gedächtniskern, sobald er so formatiert ist und den Ordner `proximus` (Besitzer `obivan`) hat.

## Kennenlernen und Stimmerkennung

Menü „Kennenlernen“ oder „Lerne mich kennen“ startet eine Sitzung ([`src/enroll.py`](../src/enroll.py)); B bricht jederzeit ab. Voraussetzung: Stick steckt, Server erreichbar.

1. **Stimmproben:** nach jedem Signalton „Proximus“ sagen, 20-mal à 2 s (normal, leiser, aus ~2 m, wie im Alltag). Die Clips landen in `proximus/voice/wake/` und sind Trainingsdaten für das Aktivierungswort (`train_wakeword.py real --real <ordner>`, dann `train`: die Hälfte der Positivbeispiele kommt aus diesen Aufnahmen, ausgewählt wird nach der Trefferquote auf zurückgehaltenen Clips).
2. **Fragen:** Name, Wohnort, Tätigkeit, Interessen, wichtige Menschen, gewünschter Antwortstil, Sonstiges; Antwort jeweils 6 s nach dem Signalton. Der Server transkribiert nur (`POST /v1/turn?mode=transcribe`), der Pi speichert Fakten („Name des Bedieners: Ivan“) bzw. den Stil als Direktive. Clips in `proximus/voice/answers/`.
3. **Stimmprofil:** Der Server bildet aus jeder Antwort einen Stimmabdruck (`POST /v1/voiceprint`, sherpa-onnx mit CAM++/VoxCeleb, 512 Werte); ihr Mittelwert wird als `proximus/voice/voiceprint.json` gespeichert (int8, ~700 Zeichen) und mit jeder Anfrage im Gedächtnis-Header mitgeschickt.

**Bei jeder Anfrage** vergleicht der Server die Stimme mit dem Profil (Kosinus ≥ 0,5, `SERVITOR_SPEAKER_THRESHOLD`; ~40 ms). Erkannt: das Modell erfährt den Namen und spricht persönlich. Fremd: es bekommt nur die Direktiven, keine Fakten und keinen Verlauf, Gedächtnisbefehle werden abgelehnt („Stimme nicht als Bediener erkannt …“), gelernte Zeilen verworfen, und eine neue Kennenlern-Sitzung startet nicht. Ohne Profil ändert sich nichts; Äußerungen unter 1 s gelten mangels sicherer Entscheidung als Bediener. Auf dem Pi (lokaler Rückfall) gibt es keine Stimmerkennung.

## Systemwartung

[`src/sysmon.py`](../src/sysmon.py) zählt alle 6 h (erstmals 2 min nach dem Start) wartende Pakete mit `apt-get -s upgrade` (Simulation, ohne root, mit `nice`), davon die aus einer `-security`-Quelle. Nur der Pi wird überwacht; den Server wartet der Bediener selbst, Proximus fragt dessen Updates nicht ab. Proximus sagt neue Updates an und erinnert höchstens einmal am Tag, nie im Schlaf; die Frage „Gibt es Updates?“ und der Statusbericht nennen sie auch. Installiert wird nur im [Wartungsmodus](maintenance.md).

Die Zahlen sind nur so aktuell wie die Paketlisten. `apt-daily` aktualisiert sie nur mit `APT::Periodic::Update-Package-Lists "1"` ([`deploy/20proximus-update-lists`](../deploy/20proximus-update-lists)); auf CT 107 ist das seit 08.10.2026 gesetzt, auf dem Pi mit dem Befehl oben. Sind die Listen älter als 7 Tage, sagt Proximus das dazu.

## Netzwerk

Alle 30 s misst der Pi WLAN-Signal (`/proc/net/wireless`), die TCP-Verbindungszeit zu 1.1.1.1:443 und zum Server im LAN sowie die DNS-Auflösung von openrouter.ai. „Wie ist das Netzwerk?“ liest das vor. Zusätzlich zu den bestehenden Alarmen (Netz, Internet, Server weg) warnt er, wenn ein Problem eine Minute anhält, mit Entwarnung:

| Alarm | an | aus |
|---|---|---|
| WLAN-Signal schwach | ≤ −80 dBm | > −72 dBm |
| Netz langsam | ≥ 400 ms | < 200 ms |
| DNS gestört | Auflösung schlägt fehl | Auflösung klappt |

Bei abgeschaltetem oder sich verbindendem WLAN schweigen diese Alarme.
