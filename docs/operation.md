# Betrieb

Die Unit heißt `pi-ptt.service`, auch mit STT, OpenRouter-LLM, lokaler TTS und Button SHIM. Der Assistent nutzt Vosk lokal; nur der Textschritt zum LLM benötigt OpenRouter.

## Start, Stop und Logs

```bash
sudo systemctl enable --now pi-ptt.service
systemctl status pi-ptt.service --no-pager
journalctl -u pi-ptt.service -n 40 --no-pager
journalctl -u pi-ptt.service -f
```

Ctrl-C beendet die Loganzeige. Vor GPIO-/SHIM-Proben oder manuellen Aufnahmen den Dienst stoppen. **Nach dem Test wieder starten:**

```bash
sudo systemctl stop pi-ptt.service
# Test ausführen, Vordergrundprogramm danach mit Ctrl-C beenden
sudo systemctl start pi-ptt.service
```

Das Journal liegt auf dem Pi nur im RAM und wird bei steckendem Gedächtnis-Stick alle 5 Minuten nach `/mnt/proximus-memory/logs/` kopiert; der Selbsttest wertet es alle 30 Minuten aus ([Logs und Selbsttest](logs.md)). Die Logs enthalten Transkripte, LLM-Antworten und Statusmeldungen, auch wenn WAVs flüchtig gespeichert werden. Zusätzlich werden `latency`-Events für `stt`, `llm` und `tts` ausgegeben; der residente Servitor-Pfad meldet weiterhin `tts_first_chunk` und `tts_playback_start`. Logs vor Weitergabe auf private Inhalte prüfen.

## Konfiguration

| Datei | Zweck |
|---|---|
| `/etc/pi-ptt.env` | GPIO, 40 ms Entprellung, 30 s Aufnahmelimit, Audiogerät, optionaler SHIM und Status-Sprachbefehl |
| `/etc/pi-voice-assistant.env` | Vosk/STT, OpenRouter-LLM, Piper-/Servitor-Modell und DSP-/Sprechparameter, Servitor-Server (`ASSISTANT_*`); echte Schlüssel nur hier |
| `/boot/firmware/config.txt` | Bestehendes WM8960-Overlay, I²C/I²S |
| `/etc/modules-load.d/pi-voice-i2c.conf` | Bei aktiviertem SHIM `i2c-dev` beim Boot laden |

**Servitor-Server (CT 107):** Die Werte aus [`config/client.env.example`](../config/client.env.example) werden an `/etc/pi-voice-assistant.env` angehängt. `ASSISTANT_TOKEN` ist derselbe Wert wie `SERVITOR_API_TOKEN` in `/etc/servitor-voice.env` auf CT 107; ihn direkt übertragen, ohne ihn anzuzeigen, etwa:

```sh
ssh root@172.22.2.11 "pct exec 107 -- sed -n 's/^SERVITOR_API_TOKEN=//p' /etc/servitor-voice.env" \
  | ssh obivan@172.22.9.128 'read -r T; …'   # in eine Temp-Datei schreiben, dann per sudo tee übernehmen
```

Leeres `ASSISTANT_BASE_URL` schaltet auf rein lokalen Betrieb zurück. Beim Start meldet das Journal `remote_ready` mit Host und Format; Fallbacks erscheinen als `remote_error`/`remote_fallback`. Siehe [Architektur](architecture.md#servitor-server-ct-107-mit-lokalem-fallback) und [ADR 0004](decisions/0004-servitor-server.md).

Für den integrierten Assistentenpfad mindestens:

```text
STT_PROVIDER=vosk
OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY_HERE
OPENROUTER_LLM_MODEL=openai/gpt-5.4-mini
OPENROUTER_LLM_TIMEOUT_SECONDS=15
OPENROUTER_LLM_MAX_TOKENS=180
TTS_VOICE_PROFILE=servitor
```

Den echten Key niemals in Git oder eine Beispielkonfiguration schreiben. `deploy/pi-ptt.service` lädt `/etc/pi-voice-assistant.env` bereits über `EnvironmentFile=`.

Nach Konfigurationsänderungen `sudo systemctl restart pi-ptt.service`. Für Overlay-Änderungen ist ein Pi-Neustart nötig. Mixeränderungen nur nach Hörprüfung mit `sudo alsactl store wm8960soundcard` dauerhaft speichern.

**Wetter für Morgenlitanei und Wetterfragen:** `WEATHER_LAT` und `WEATHER_LON` (Dezimalgrad) in `/etc/servitor-voice.env` auf CT 107 setzen und den Dienst neu starten; für den Offline-Fallback dieselben Werte auch in `/etc/pi-voice-assistant.env`. Ohne sie antwortet Proximus ohne Wetter („Wetterdaten nicht verfügbar.“). 

**Termine aus Nextcloud:** In Nextcloud unter Einstellungen → Sicherheit ein App-Passwort für „Proximus“ anlegen. Die Kalender-URL steht in der Kalender-App unter „Kalender-Einstellungen“ → „Primäre CalDAV-Adresse kopieren“ bzw. beim einzelnen Kalender „Link kopieren“ (Form `https://…/remote.php/dav/calendars/BENUTZER/KALENDER/`). Einfacher: nur `https://…/remote.php/dav` eintragen, dann sucht der Pi alle Terminkalender des Kontos selbst (Aufgabenlisten werden übersprungen). Dann in `/etc/pi-voice-assistant.env` auf dem Pi `CALDAV_URLS` (mehrere Kalender mit Komma), `CALDAV_USER` und `CALDAV_PASSWORD` setzen (Vorlage in [`config/client.env.example`](../config/client.env.example)) und `pi-ptt` neu starten. Das Journal meldet `agenda_ready`; prüfen mit „Was steht heute an?“. Der Server braucht dafür nichts. Das App-Passwort nie ins Repository.

„Was steht morgen an?“ nutzt die im Hintergrund geladenen Termine für morgen. Dafür Pi und Server zusammen aktualisieren: Der Pi übermittelt morgen in `X-Servitor-Agenda-Tomorrow`; fehlt dieser Header bei älteren Pis, sagt der neue Server „Kalenderdaten für morgen nicht verfügbar.“ Daten außerhalb heute/morgen werden nicht geraten. Fremde Sprecher bekommen keine persönlichen Kalenderdaten und keine Bediener-Anreden wie „Kommandant“. Die Sprechererkennung selbst braucht weiterhin eine Abnahme mit echten Stimmen.

Akku- und eigene Messwertfragen werden auch bei längeren Formulierungen direkt aus dem Gerätestatus beantwortet, einschließlich Sprachkern LOKAL. Fehlt ein Akkumesswert, wird das gesagt. „Welches Sprachmodell bist du?“ hat eine feste Antwort als Proximus beziehungsweise Billy. Ab 18 Uhr verwendet der Abendbericht die Wettervorhersage für morgen; fehlt sie, meldet er das ausdrücklich.

**Fahrten (Straßenbahn, Zug, Erinnerung, Kalender):** `TRANSIT_HOME` und `TRANSIT_CITY_STOP` in `/etc/pi-voice-assistant.env` setzen; Kalendereinträge nutzen die `CALDAV_*`-Werte und brauchen Schreibrecht. Einzelheiten und offene Voraussetzungen: [Fahrten](journeys.md).

Der Quittungston lässt sich mit `PTT_CUE=0` in `/etc/pi-ptt.env` abschalten (auch im Menü).

`PTT_RUNTIME_DIR=/run/pi-ptt` ist unter der mitgelieferten Unit fest vorgegeben: systemd erstellt/erlaubt genau dieses Verzeichnis. Ein anderer Pfad ist nur bei manuellem Vordergrundbetrieb oder passender eigener Unit möglich.

## Dateien und Rechte

- Dienstcode: `/opt/pi-voice-assistant/src`, gehört `obivan` (beide Dienste laufen als **obivan** mit `audio`, `gpio`, `i2c`); `src/DEPLOYED` nennt den eingespielten Commit. Kein dedizierter Dienstbenutzer implementiert.
- Aufnahme: ein Slot `/run/pi-ptt/capture.wav`, privat, flüchtig. Neue Aufnahme, Dienststop oder Reboot entfernt die vorherige Datei. Vor Ctrl-C/Stop abhören, falls die Testaufnahme benötigt wird.
- Vosk: root-verwaltete `vendor/`- und `models/`-Verzeichnisse; Modell einmal bei Bedarf laden, im Dienst wiederverwenden.
- Piper/Servitor, separat: `.venv/` und `tts/`; FFmpeg kommt über den Piper-Installer. Der residente Servitor-Pfad streamt Piper-PCM direkt über FFmpeg nach ALSA und benötigt keine TTS-WAV. Siehe [TTS-Setup](text-to-speech.md).
- `ProtectHome=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`: Dienstdateien nicht aus `~/...` laden. Manuelle TTS-WAVs in `/tmp` sind nicht automatisch im privaten Dienst-`/tmp` sichtbar.

Bei anderem Loginbenutzer müssen Unit, Installer und Dateigruppen gemeinsam angepasst werden; nur `User=` zu ändern reicht nicht. Die Isolation eines dedizierten Dienstbenutzers bleibt eine offene Verbesserung.

## Aktualisieren

Checkout auf sauberen Zustand und gewünschten Branch/Commit prüfen. Dienstinstaller aufrufen; vorhandene Konfigurationen bleiben erhalten, `speak.py` wird aus dem Checkout aktualisiert. Ein vorher laufender Dienst wird gewöhnlich wieder gestartet. Frisch angelegte STT-Konfiguration lässt ihn bis zur Prüfung gestoppt.

```bash
git status --short
git branch --show-current
git rev-parse --short HEAD
sudo bash scripts/install-voice-service.sh
sudo systemctl restart pi-ptt.service
systemctl status pi-ptt.service --no-pager
```

Vosk/Piper nicht bei jedem Codeupdate neu installieren. **Nach erstmaligem Wechsel auf das Servitor-Profil** `sudo bash scripts/install-piper.sh` einmal ausführen, damit Emotional-Modell und FFmpeg vorhanden sind. Installer verändern `/opt` und die systemd-Unit; ein Wechsel des Git-Checkouts allein verändert den installierten Dienst nicht. Für Rücknahme einen bekannten Commit in einem sauberen Checkout wählen und dessen Installer ausführen, Konfiguration separat prüfen.

### Aus einem Git-Commit einspielen (aktuelle Praxis)

Auf dem Pi ist `/opt/pi-voice-assistant` kein Git-Checkout. Eingespielt wird ein gepushter Commit mit dem root-eigenen Wrapper [`deploy/pi-voice-install`](../deploy/pi-voice-install): Er nimmt nur einen vollständigen 40-stelligen Commit-Hash, extrahiert **ausschließlich diesen unveränderlichen Git-Commit** in ein root-eigenes Verzeichnis, führt dessen `scripts/install-voice-service.sh` aus (Module, Unit, Neustart von `pi-ptt` und `pi-display`) und schreibt den Kurz-Hash nach `/opt/pi-voice-assistant/src/DEPLOYED`. Ein geänderter, nicht committeter Arbeitsbaum wird dabei niemals als root ausgeführt.

**Privates GitHub-Repository:** Anonymer Download über `codeload.github.com` endet mit HTTP 404, selbst wenn der SHA existiert. Deshalb muss der bereits eingerichtete Benutzer-Checkout `/home/obivan/pi-voice-assistant` den Commit kennen. Einmal vor dem Update **als `obivan`, nicht mit `sudo`**, abrufen:

```bash
cd ~/pi-voice-assistant
git fetch origin main
git cat-file -t <vollständiger-40-stelliger-commit-sha>  # erwartet: commit
sudo /usr/local/sbin/pi-voice-install <vollständiger-40-stelliger-commit-sha>
```

Der Installer benötigt selbst **keinen GitHub-Token** und liest aus dem Checkout nur das Git-Objekt mit exakt dem angegebenen SHA. Ist der Commit noch nicht lokal vorhanden, versucht der Wrapper für öffentliche Repositories den anonymen Download; bei einem privaten Repository gibt er eine klare Anweisung zum vorherigen `git fetch` aus. Die sudo-Regel erlaubt `obivan` nur den Wrapper, keine unkontrollierte privilegierte Shell.

**Einmaliges Upgrade von einem alten Wrapper mit `curl`-404:** Nach einem erfolgreichen `git fetch origin main` den geprüften neuen Wrapper aus dem festen Commit installieren und anschließend erneut ausführen (die Datei wird root-eigen/0755):

```bash
cd ~/pi-voice-assistant
git show origin/main:deploy/pi-voice-install | sudo tee /usr/local/sbin/pi-voice-install >/dev/null
sudo chmod 0755 /usr/local/sbin/pi-voice-install
```

Beim ersten Update sollte der Zugriff über eine funktionierende Ethernet-/SSH-Verbindung erfolgen.

```bash
C=$(git rev-parse origin/main)          # vollständiger Hash, nicht abgekürzt
ssh obivan@pi "sudo -n /usr/local/sbin/pi-voice-install $C"
ssh obivan@pi 'cat /opt/pi-voice-assistant/src/DEPLOYED; systemctl is-active pi-ptt pi-display'
```

Einmalig einrichten (als root auf dem Pi, aus einem geprüften Checkout):

```bash
install -o root -g root -m 0755 deploy/pi-voice-install /usr/local/sbin/pi-voice-install
install -o root -g root -m 0440 deploy/pi-voice-install.sudoers /etc/sudoers.d/pi-voice-install
visudo -c
```

Für Neustart und Env-Datei gibt es zusätzlich eine eng begrenzte sudo-Regel (`/etc/sudoers.d/pi-voice-deploy`: nur `pi-ptt`/`pi-display` neu starten und `tee /etc/pi-voice-assistant.env`). Neue Env-Werte (z. B. `CALDAV_*`, `WEATHER_*`) danach mit `sudo -n systemctl restart pi-ptt.service pi-display.service` übernehmen.

Nach Änderungen an Alarmtexten oder an der Aussprache ([`src/pronounce.py`](../src/pronounce.py)) die vorgefertigten Ansagen neu bauen; es werden nur geänderte Clips über CT 107 gerendert:

```bash
set -a; . /etc/pi-voice-assistant.env; set +a
python3 /opt/pi-voice-assistant/src/alarm_audio.py build --prune
```

**Notweg ohne Internet auf dem Pi:** nur `src/` aus einem gepushten Commit kopieren (Unit und Installer-Schritte bleiben dann aus):

```bash
C=$(git rev-parse HEAD)
git archive $C src | ssh obivan@pi 'D=$(mktemp -d); tar -x -C $D; \
  python3 -m py_compile $D/src/*.py && install -m 0644 $D/src/*.py /opt/pi-voice-assistant/src/; \
  echo '$C' > /opt/pi-voice-assistant/src/DEPLOYED; rm -rf $D'
ssh obivan@pi 'sudo -n systemctl restart pi-ptt.service pi-display.service'
```

Auf **CT 107** ist `/opt/servitor-voice/repo` ein Git-Checkout: `git fetch` und `git checkout --detach <commit>`, dann `systemctl restart servitor-voice`; Unit/Env bzw. Offline-LLM mit `server/install-ct.sh` bzw. `server/install-llm.sh`. `/opt/servitor-voice/DEPLOYED` nennt den Commit.

## Abnahme nach Änderungen

Den vollständigen Ablauf prüfen: `recording → processing → transcript → llm_start → llm_response → speech_started → speech_finished`. Dazwischen müssen `latency`-Events für STT, LLM und TTS erscheinen. Mit SHIM zusätzlich A, B während Aufnahme/STT/LLM/Ansage, C/D-Pegel und E-Ansage prüfen. Nach einem Reboot I²C, Audio und Dienst prüfen. Einzelne frühere Tests ersetzen keine Abnahme einer neu installierten Version. Aktuelle offene Tests stehen in [Roadmap](roadmap.md) und [Button-Abnahme](button-controls.md).

### Persona, Lore und lange Geschichten

Offline (ohne Modellaufrufe) zeigt `python3 server/sample-persona.py dialog dry light` für die Abnahme-Dialogfolge ([Lore, Abschnitte 12, 15.4, 16.5, 17.5](concepts/proximus-billy-lore/LORE_PROXIMUS_BILLY.md#12-abnahme)), welche Engramme und Gesprächshinweise jede Frage bekommt. Die echte Stichprobe läuft auf CT 107 mit der Dienstumgebung, je einmal vor und nach einem Update, und kostet OpenRouter-Aufrufe (etwa 90 kurze Antworten):

```sh
set -a; . /etc/servitor-voice.env; set +a
/opt/servitor-voice/.venv/bin/python server/sample-persona.py dialog openrouter light
/opt/servitor-voice/.venv/bin/python server/sample-persona.py dialog local light
```

Bewerten: Aufgabe gelöst, beim einmaligen Hören verständlich, Charakter, Lore-Konsistenz (A01 nie als eigene Erinnerung, keine Kameradennamen beim Servitor, Phobos IX nicht der Marsmond), keine gleichen Einstiege, kein „Boss“ in jeder Antwort, keine Pointe über den fehlenden Körper.

Lange Geschichten am Gerät prüfen: „Erzähl mir zehn Minuten über deinen Trupp“, „… fünfzehn Minuten von Phobos“, „Erzähl die alte Höllenläufer-Sage zwanzig Minuten lang“. Im Journal erscheinen `story_start`, `story_part`, `story_fetch`, `story_section` und am Ende `story_finished` mit `played_ms`; Taste B oder die Sprechtaste ergibt `story_stopped`. Zu messen: Zeit bis zum ersten Ton, Lücken zwischen den Abschnitten, tatsächliche Dauer gegenüber dem Ziel (`STORY_WORDS_PER_MINUTE` danach anpassen), Speicher in `/run/pi-ptt` während der Wiedergabe.


## Energieverwaltung: PiSugar 3

Proximus besitzt bereits einen read-only I²C-Akku-Monitor in `src/power.py`.
Zusätzlich liest er direkt am PiSugar 3 die Statusbits für Soft-Poweroff,
Ladeschutz, Watchdog und automatisches Einschalten bei Netzanschluss sowie den
Ausgangsstrom (Register `0x26/0x27`, in Milliampere) aus. Der Ausgangsstrom
ist **nicht** gleich dem Batteriestrom und erlaubt ohne kalibrierte Akkukapazität
und weitere Annahmen keine verlässliche Restlaufzeit.
**Hardwarebefund 14.10.2026:** Bei laufendem Pi Zero 2 W lieferten die
Register `0x26/0x27` mehrfach `0 mA`, sowohl mit Netzteil als auch auf
Akku. Dieser Wert ist für ein eingeschaltetes Gerät **nicht als gemessener
Nullverbrauch plausibel**; daher wird ein Rohwert von Null im Proximus
als `Ausgangsstrom nicht verfügbar` behandelt und nicht als Verbrauch
ausgesprochen. Die Ursache ist noch offen (Firmware-/Messpfad).
Diese **reinen Lesezugriffe** funktionieren ohne den Herstellerdienst; ein
Fehler in einem der Zusatzregister verhindert die Akkumessung nicht.
Die Akkustandsanzeige und das Herunterfahren bei Energieknappheit sind **lokal**,
auch ohne OpenRouter oder Kogitator, verfügbar. **Die bestehende Schutzlogik
bleibt unverändert:** Warnungen bei 15/10/6 % im Akkubetrieb; ab 6 % wird
nach 60 Sekunden `systemctl poweroff` angefordert. Anschluss des Netzteils
hebt die ausstehende automatische Abschaltung auf. Die Schwellen sind noch
nicht auf den konkreten Akku kalibriert; die Hardware muss abschließend
geprüft werden.

Im Display: **Energie → Akku-Diagnose / PiSugar Konfig /
Stromhinweise / Sicher ausschalten**. `Sicher ausschalten` benutzt dieselbe
20-Sekunden-Bestätigungslogik wie „Fahr dich herunter“ (Taste E oder eine
autorisierte Sprachbestätigung; Taste B bricht ab). Es ist **kein** sofortiges
Abschalten des Akkus. Die zusätzliche Konfigurationsansicht ist ausschließlich
lesend. Direkt per I²C bekannte Werte erscheinen auch ohne Herstellerdienst;
Firmware, Ladefenster und RTC-Alarme können als „nicht verfügbar“ gelten,
solange kein PiSugar-Power-Manager-Socket erreichbar ist.

Lokale Sprachbeispiele (ohne LLM-Hardwarezugriff):

- „Wie hoch ist der Akkustand?“ → Prozent und Ladezustand.
- „Wie hoch ist die Batteriespannung?“ / „Was meldet PiSugar?“ →
  Batteriespannung, Ausgangsstrom, Stromquelle und PiSugar-**Chip**temperatur,
  soweit bekannt.
- „Wie viel Strom verbrauchst du?“ → nur der gemessene Ausgangsstrom,
  nicht als Batteriestrom oder Laufzeit ausgeben.
- „Wie lange hält der Akku noch?“ → ehrlich **keine** Zeitangabe ohne
  gemessenen Verbrauch; keine erfundenen Stunden.
- „Schalte die Energiehinweise an“ / „Schalte die Stromhinweise aus“ →
  normale Ansagen bei Wechsel zwischen Netzteil und Akku einschalten/
  unterdrücken. Standardmäßig sind sie **aus**, der Wert liegt in
  `/var/lib/pi-ptt/settings.json`.
- „Fahr dich herunter“ → verlangt Bestätigung, wie zuvor.

`Stromhinweise AUS` unterdrückt **nur routinemäßige** Netz-/Akkuwechsel,
nicht die kritischen Akkustufen, die Meldung einer gerade abgebrochenen
Notabschaltung oder den Poweroff selbst. Der allgemeine Menüpunkt
`Gerät → Alarme` beeinflusst weiterhin die Sprachausgabe der Alarme, **nicht**
die automatische Notabschaltung. Bei I²C-Leseausfall wird kein Akkustand
erfunden; der Linux-Poweroff-Befehl allein garantiert **keine** elektrische
Abtrennung durch den PiSugar.

### Opt-in: Software-Standby über die PiSugar-3-Funktionstaste

**Hardwaretest vom 14.10.2026:** Ein kurzer Druck auf die separate
**Funktionstaste** lieferte über I²C (`0x57`, Register `0x08`) den
Wert `1` (zuvor `0`). Die PiSugar-Power-Taste bleibt unverändert.

Die Implementierung ist **standardmäßig deaktiviert**. Nach einem separaten
Test der Quittierung kann `/etc/pi-voice-assistant.env` enthalten:

```text
PISUGAR_STANDBY_BUTTON=on
```

Nach Installation des PR und anschließendem Neustart von `pi-ptt.service`
bewirkt **ein kurzer Druck** Software-Standby, der nächste Druck weckt
Proximus wieder. Doppelte und lange Funktionstastendrücke werden nur
quittiert, nicht als Umschaltung interpretiert. Beim Dienststart wird ein
möglicherweise noch gespeichertes Ereignis ohne Standby-Aktion gelöscht.

Software-Standby: Display und Status-LED werden dunkel, Wakeword-Listener
sowie normale Audio-/Tastenbedienung sind pausiert. **Linux, I²C, SSH,
WLAN und der 5-V-Ausgang bleiben an** (auch bei konfigurierter automatischer
WLAN-Abschaltung im normalen Idle-Sleep). Kritische Alarme wecken Proximus
auf; die vorhandene lokale Notabschaltung bleibt unabhängig davon aktiv.
Während Aufnahme, Sprachausgabe, laufenden Wartungsaktionen und noch
bestätigungsbedürftigen Shutdowns wird ein Standby-Tap abgewiesen.
Aufwachen per Taste ist absichtlich **lautlos**. Ein Dienstneustart führt
zur normalen aktiven Betriebsart zurück.

**Hardware-Sicherheitsgrenze:** PiSugar-3-Tap-Ereignisse sind gespeichert.
Um einen erfolgreichen kurzen Druck zu quittieren, liest der Treiber
`0x08`, setzt kurz Schreibfreigabe `0x0B=0x29`, schreibt die
unteren zwei Bits von `0x08` auf null und setzt `0x0B=0x00`.
Das ist die Sequenz des offiziellen `pisugar3.rs`-Treibers.
**Keine** Ein-/Ausschalt-, Lade-, RTC- oder Watchdog-Register werden
geschrieben. Fehlende Quittierung darf nie mehrfach Standby auslösen.
Bei einem einzelnen I²C-Fehler wie `[Errno 121] Remote I/O error` bleibt
die Integration aktiv, wiederholt die Abfrage mit exponentiellem Backoff
(1, 2, 4 ... maximal 30 Sekunden) und protokolliert Erholung über
`pisugar_button_recovered`. Unquittierte Ereignisse lösen **keine**
Standby-Umschaltung aus. Nach **drei aufeinanderfolgenden** Fehlern
**im manuellen Standby** wird Proximus vorsorglich wieder aufgeweckt,
damit PTT/GPIO17 und SHIM bedienbar sind; die PiSugar-Abfrage läuft mit
reduzierter Frequenz weiter. Beim Start wird ein altes Ereignis weiterhin
erst nach erfolgreichem ACK verworfen. Einen konkurrierenden offiziellen
PiSugar-Tastenhandler nicht gleichzeitig betreiben.

**Hardwaretests am 14.10.2026:** ACK und Standby-/Aufwach-Umschaltung
waren erfolgreich. Später trat ein transienter I²C-Fehler `Errno 121`
auf und die ursprüngliche Version deaktivierte den Button dauerhaft.
Der Retry-/Failsafe-Fix ist durch Unit-Tests abgedeckt; sein Verhalten
auf der echten Hardware bleibt bis zum erneuten Dauertest unbestätigt.
Software-Standby ist kein echter suspend-to-RAM-Modus
und keine elektrische Stromabschaltung.

### Akkusparprofil im manuellen PiSugar-Standby (separates Opt-in)

Die PiSugar-Funktionstaste und die Akku-/Unterspannungsüberwachung laufen im
unprivilegierten `pi-ptt.service` weiter. Die CPU-Taktung und Funkgeräte werden
**nicht** direkt von diesem Prozess manipuliert. Ein separater, root-eigener
`proximus-battery-standby.service` setzt die optionalen Änderungen erst dann,
wenn **alle** Bedingungen erfüllt sind:

1. `PISUGAR_BATTERY_SAVE=on` in `/etc/pi-voice-assistant.env`;
2. manueller Standby per PiSugar-Funktionstaste ist aktiv;
3. PiSugar meldet **bestätigten Akkubetrieb** (Register `0x02`, Bit 7 = 0);
4. keine aktive kritische Warnung und keine bereits eingeleitete Abschaltung.

Bei Netzbetrieb oder unbekannter Stromquelle werden keine Optimierungen
angewandt. Der Wechsel zurück auf Netzstrom (Überwachung im 10-Sekunden-Takt),
Aufwachen, kritische Warnungen sowie Dienststop/-neustart lösen die
Wiederherstellung aus. Der Helfer speichert die **vorherigen** CPU-Governors
und WLAN-/Bluetooth-Softblock-Zustände atomar unter `/run`; `ExecStop` und
`ExecStopPost` stellen sie wieder her. `BindsTo=pi-ptt.service` koppelt die
Lebensdauer des Root-Helfers an die Voice-Unit. Nur `start` und `stop`
dieser **einen** Unit werden für `obivan` via Polkit freigegeben. Das
verhindert umfassende Root-Rechte im Sprachdienst.

**Ersttest mit Netzverbindung (kein Funkverlust):**

```bash
sudo vim /etc/pi-voice-assistant.env
# Ergänzen: PISUGAR_BATTERY_SAVE=on
sudo vim /etc/proximus-battery-standby.env
# PROXIMUS_SAVE_CPU=powersave
# PROXIMUS_SAVE_WLAN=keep
# PROXIMUS_SAVE_BLUETOOTH=keep
sudo systemctl restart pi-ptt.service
```

Beim Pi Zero 2 W gibt es keine fest programmierte Frequenz: Der Helfer
prüft den **tatsächlich angebotenen** Kernel-Governor `powersave` unter
`/sys/devices/system/cpu/cpufreq/policy*/scaling_available_governors`
und sichert den bestehenden Wert von `scaling_governor`. Ist der Governor
nicht verfügbar, bleibt die Policy unverändert. Der Governor wählt die
niedrigste Frequenz im vom Kernel erlaubten Bereich; Echtzeit-Ersparnis und
Wiedergabelatenzen müssen am Gerät gemessen werden.

**Erst nach erfolgreichem CPU- und I²C-Dauertest** können in der zweiten Datei
einzeln `PROXIMUS_SAVE_BLUETOOTH=off` und schließlich
`PROXIMUS_SAVE_WLAN=off` aktiviert werden. Diese Datei ist root-eigen und
enthält **keine Tokens/Passwörter**. `off` ist beim WLAN ein Softblock via
`rfkill` und bedeutet: **kein SSH, kein Webinterface, kein Remote-LLM über
WLAN bis zum Aufwachen per physischer PiSugar-Funktionstaste**. Auch das
Bluetooth-Audio kann beim Abschalten abbrechen und nach dem Wake etwas Zeit
zum Wiederverbinden benötigen. Während eines aktiven Setup-Hotspots lässt
der Helfer WLAN eingeschaltet. Bei mehreren gleichartigen Funkadaptern
verändert er diesen Funktyp nicht, damit eine korrekte Rücksetzung möglich
bleibt. Ein Hardwarefehler, der das Aufwachen verhindert, erfordert eine
alternative Verbindung (z. B. USB-Ethernet) oder einen Service-Neustart;
es gibt **keine** garantierte Fernrettung bei deaktiviertem WLAN.

Beobachtung und Kontrolle:

```bash
journalctl -u pi-ptt.service -f -o cat | grep -E 'pisugar|battery_standby|power'
systemctl is-active proximus-battery-standby.service
cat /sys/devices/system/cpu/cpufreq/policy*/scaling_governor
rfkill list
```

**Rollback, am besten VOR dem WLAN-Off-Test über eine lokale Verbindung
oder bei normal eingeschaltetem WLAN:**

```bash
sudo systemctl stop proximus-battery-standby.service
sudo vim /etc/pi-voice-assistant.env
# PISUGAR_BATTERY_SAVE=off
sudo systemctl restart pi-ptt.service
```

`proximus-battery-standby.service` niemals manuell aktivieren (`enable`);
sie wird bei Bedarf vom Sprachdienst ausgelöst. Kein echter Suspend-to-RAM,
kein PiSugar-Hard-Poweroff, keine erfundenen mA- oder Laufzeitangaben.
Aktivierungs-/Wiederherstellungslogik benötigt weiterhin einen echten
Hardwaretest unter Akkubetrieb, bevor WLAN-Abschaltung freigegeben wird.

### CPU-Profil selbst wählen und optional übertakten (Pi Zero 2 W)

**Sofort umschaltbar:** Unter `Energie → CPU-Profil` auf dem 240×240
PiTFT zwischen **ECO** (`powersave`), **NORMAL** (`ondemand`) und
**POWER** (`performance`) durchschalten. Entsprechende lokale Sprachbefehle
sind z. B. „Stell den CPU-Modus auf Eco“, „Stell den CPU-Modus auf
Balanced“ und „Stell den CPU-Modus auf Power“. Keine andere Anfrage
(z. B. „Übertakten“, „Overvoltage“) darf Boot- oder Spannungseinstellungen
ändern. Der ausgewählte Governor wird in `/var/lib/pi-ptt/settings.json`
gespeichert und nach einem regulären Dienststart wiederhergestellt. Eine
fehlgeschlagene Umschaltung wird protokolliert und nicht gespeichert.
Der Voice-Prozess hat kein Recht auf `/sys`: Eine sehr enge Polkit-Regel
autorisiert ausschließlich die systemd-Instanzen
`proximus-cpu-mode@eco/balanced/power.service`, und das root-owned
Helper-Script ändert nur `scaling_governor`. **Der Power-Governor
übertaktet nicht.** Er nutzt höchstens die gegenwärtig vom
Kernel/Firmware erlaubte maximale Frequenz. Der Akku-Standby-Helper darf
ihn vorübergehend mit `powersave` übersteuern und danach wieder
zurücksetzen.

Sofortige Kontrolle (einfaches Nachsehen ohne Neustart):

```bash
cat /sys/devices/system/cpu/cpufreq/policy*/scaling_governor
cat /sys/devices/system/cpu/cpufreq/policy*/cpuinfo_max_freq
vcgencmd measure_clock arm
vcgencmd get_throttled
vcgencmd measure_temp
```

**Echtes Übertakten und höhere Kernspannung** sind eine separate,
**niemals** durch Sprachbefehl oder Menü ausgelöste Administratoraktion.
Der Pi Zero 2 W hat ab Werk 1000 MHz; `arm_freq=1200` und
`over_voltage=2` (+50 mV am oberen Firmware-Spannungsfenster)
sind ein **experimentelles** Testprofil, **keine garantierte stabile
Frequenz**. Bootloader/Firmware übernehmen diese Werte erst nach Reboot.
Insbesondere `force_turbo=1` wird vermieden: Im Zusammenspiel mit
Overvoltage kann das einen dauerhaften OC-Marker im SoC setzen.
Zusätzliche GPU-/SDRAM-Übertaktung wird nicht vorgenommen.

**Voraussetzungen:** volle PiSugar-Versorgung, ausreichende Kühlung,
lokale physische Zugriffsmöglichkeit auf die microSD bei Bootfehlern,
Backup des Systems, keine vorhandenen `arm_freq`-/`over_voltage`-
Konflikte, Temperatur/Undervoltage vorab messen. Für den ersten
Overclock-Test sollte **Netzbetrieb** verwendet werden, nicht ein Akku
mit gleichzeitig ausgeschaltetem WLAN.

```bash
cd ~/pi-voice-energy-test
python3 scripts/configure-overclock.py preview
# Nur wenn du dieses Risiko bewusst akzeptierst:
sudo python3 scripts/configure-overclock.py enable --accept-risk
# Script sichert zuvor config.txt unter /boot/firmware/
# Es rebootet NICHT automatisch!
sudo reboot
```

Nach dem Reboot Frequenz, Temperatur, `get_throttled` und mehrere reale
TTS/STT-Transaktionen prüfen. Bei Instabilität (Audio-Aussetzer,
I²C-Fehler, Unterspannung, Temperatur-Throttling) sofort auf Stock
zurückgehen. Solange der Pi noch bootet:

```bash
sudo python3 scripts/configure-overclock.py disable --accept-risk
sudo reboot
```

Startet er nicht mehr, `config.txt` auf der microSD von einem anderen
Rechner öffnen und nur den Block zwischen
`# BEGIN PROXIMUS ZERO 2 W OVERCLOCK` und
`# END PROXIMUS ZERO 2 W OVERCLOCK` entfernen. Ein vom Script
angelegtes datiertes Backup von `config.txt` bleibt erhalten. Die
Übertaktung ist unabhängig vom CPU-Governor wählbar: Wird der OC-Block
entfernt, bleibt der Governor `power` innerhalb der Standardtaktgrenze.
**Ohne Hardwaretests keine automatische OC-Freigabe oder Merge.**

Ob die Übertaktung wirklich greift, zeigt das PiTFT: Erst wenn der Kernel
nach dem Reboot mehr als 1000 MHz erlaubt (und keine Unterspannung oder
Drosselung anliegt), erscheint `OC` in der Kopfzeile, das goldene Servitor-Auge bzw.
Billys Gottmodus ([Display](display.md#cpu-profil-auf-dem-display)).

### Optional: Einstellungen des offiziellen Power Managers auslesen

`src/pisugar_client.py` kann `get`-Kommandos des [offiziellen
PiSugar Power Managers](https://github.com/PiSugar/pisugar-power-manager-rs)
über **einen lokalen Unix-Socket** lesen: Firmware, Ladefenster,
Soft-Poweroff-Status, RTC-Alarm-Status und Safe-Shutdown-Konfiguration.
Alle Werte werden geprüft; es gibt **keine** `set`-Befehle,
kein direktes Schreiben von I²C-Konfigurationsregistern und keine externen
Aktionen durch das LLM. Der Hintergrund-Poller liest höchstens alle zwei
Minuten und blockiert den Sprachpfad nicht.

Aufgrund von `PrivateTmp=yes` in `pi-ptt.service` ist das übliche
`/tmp/pisugar-server.sock` der Herstellerinstallation **nicht** sichtbar.
Erst nach gesicherter Installation des Herstellerdienstes und Kontrolle
von dessen Besitzrechten einen gemeinsamen Socket unter `/run/` mit
**minimalen Berechtigungen** bereitstellen, z. B. Pfad
`/run/pisugar-server.sock`, Modus `0660` und Gruppe `i2c` (die Pi-PTT-Unit
ist Mitglied). Den Zugriffsmodus `0666` ausdrücklich **nicht** übernehmen:
die Herstellerschnittstelle akzeptiert auch schreibende Befehle.
**Auch ein UDS mit Modus `0660` ist nicht technisch read-only.** Nur
vertrauenswürdigen Benutzern Zugriff geben; langfristig ist ein ausschließlich
lesender Broker statt direktem Zugriff durch den Voice-Prozess vorzuziehen. Die
Datei `/etc/pi-voice-assistant.env` bekommt **erst dann**:

```text
PISUGAR_SOCKET=/run/pisugar-server.sock
```

Danach `sudo systemctl restart pi-ptt.service`. Ohne Socket läuft alles
ohne Herstellerdaemon (I²C-Akkudaten, die zusätzlichen Statusbits und
lokale kritische Schutzfunktion);
keine Abhängigkeit von einem zusätzlichen Daemon. Hersteller-TCP/HTTP,
falls aktiviert, nur lokal binden oder abschalten, niemals offen als
administrative Netzwerk-API bereitstellen.

**Vor produktiver Freigabe auf Hardware prüfen:** Erkennung von PiSugar 3,
I²C-Bus, abgezogenes Netzteil, Ladezustandswechsel, kurzzeitige
Spannungsschwankungen, Offline-Dialog, 15/10/6-%-Simulation, Abbruch durch
Laden, Backup der SD-Karte, `systemctl poweroff` und die
**tatsächliche elektrische Abschaltung** durch `pisugar-poweroff`/
Soft-Poweroff. Kein Live-Test mit absichtlich tiefentladenem Akku.
PiSugar-Watchdog, automatische Wiederanschaltung, Ladegrenzen ändern und
RTC-Weckzeiten **noch nicht** aktiviert: dafür braucht es einen separaten
privilegierten Schreibpfad, explizite Autorisierung, Rollback und
Hardware-Abnahmetests. Bestehenden Hersteller-Manager nicht durch einen
zweiten konkurrierenden I²C-Schreibdienst ersetzen.
