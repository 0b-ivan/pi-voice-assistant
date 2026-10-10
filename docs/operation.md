# Betriebsleitfaden

Die Unit auf dem Pi heißt `pi-ptt.service`. Im **Normalbetrieb** verarbeitet CT 107 Vosk, LLM und TTS; der Pi übernimmt Aufnahme, Tasten, Display und Wiedergabe. Ohne Server übernimmt der Pi Vosk und Piper selbst, freie Fragen erfordern dann OpenRouter und Internet. Siehe [Architektur](architecture.md#servitor-server-ct-107-mit-lokalem-fallback).

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

Die Logs enthalten Transkripte, LLM-Antworten und Statusmeldungen, auch wenn WAVs flüchtig gespeichert werden. Zusätzlich werden `latency`-Events für `stt`, `llm` und `tts` ausgegeben; der residente Servitor-Pfad meldet weiterhin `tts_first_chunk` und `tts_playback_start`. Logs vor Weitergabe auf private Inhalte prüfen.

## Konfiguration

| Datei | Zweck |
|---|---|
| `/etc/pi-ptt.env` | GPIO, 40 ms Entprellung, 30 s Aufnahmelimit, Audiogerät, optionaler SHIM und Status-Sprachbefehl |
| `/etc/pi-voice-assistant.env` | Vosk/STT, OpenRouter-LLM, Piper-/Servitor-Modell und DSP-/Sprechparameter, Servitor-Server (`ASSISTANT_*`); echte Schlüssel nur hier |
| `/boot/firmware/config.txt` | Bestehendes WM8960-Overlay, I²C/I²S |
| `/etc/modules-load.d/pi-voice-i2c.conf` | Bei aktiviertem SHIM `i2c-dev` beim Boot laden |

**Servitor-Server (CT 107):** Die Werte aus [`config/client.env.example`](../config/client.env.example) gehören in `/etc/pi-voice-assistant.env` auf dem Pi. `ASSISTANT_TOKEN` muss mit `SERVITOR_API_TOKEN` in `/etc/servitor-voice.env` auf CT 107 übereinstimmen. Beide Dateien nur mit administrativen Rechten bearbeiten (z. B. `sudoedit`), das Token **nicht** in ein Shell-Kommando, die History oder ein Git-Commit kopieren. Nach Änderungen den Pi-Dienst neu starten. Es gibt hier bewusst kein nur teilweise ausführbares Token-Übertragungsskript.

Leeres `ASSISTANT_BASE_URL` schaltet auf rein lokalen Betrieb zurück. Beim Start meldet das Journal `remote_ready` mit Host und Format; Fallbacks erscheinen als `remote_error`/`remote_fallback`. Siehe [Architektur](architecture.md#servitor-server-ct-107-mit-lokalem-fallback) und [Serverpfad und Grenzen](architecture.md#servitor-server-ct-107-mit-lokalem-fallback).

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

Der Quittungston lässt sich mit `PTT_CUE=0` in `/etc/pi-ptt.env` abschalten (auch im Menü).

`PTT_RUNTIME_DIR=/run/pi-ptt` ist unter der mitgelieferten Unit fest vorgegeben: systemd erstellt/erlaubt genau dieses Verzeichnis. Ein anderer Pfad ist nur bei manuellem Vordergrundbetrieb oder passender eigener Unit möglich.

## Dateien und Rechte

- Dienstcode: `/opt/pi-voice-assistant/src`; die Installation erfolgt über einen root-gesteuerten Installer, Eigentümer vorhandener Verzeichnisse können abweichen. **Die Pi-Dienste laufen als `obivan`** mit den benötigten Gruppen (`audio/gpio/i2c` beim Sprachdienst). `src/DEPLOYED` nennt den eingespielten Commit. Kein dedizierter Dienstbenutzer implementiert.
- Aufnahme: ein Slot `/run/pi-ptt/capture.wav`, privat, flüchtig. Neue Aufnahme, Dienststop oder Reboot entfernt die vorherige Datei. Vor Ctrl-C/Stop abhören, falls die Testaufnahme benötigt wird.
- Vosk: root-verwaltete `vendor/`- und `models/`-Verzeichnisse; Modell einmal bei Bedarf laden, im Dienst wiederverwenden.
- Piper/Servitor, separat: `.venv/` und `tts/`; FFmpeg kommt über den Piper-Installer. Der residente Servitor-Pfad streamt Piper-PCM direkt über FFmpeg nach ALSA und benötigt keine TTS-WAV. Siehe [Piper-Installation](setup.md#lokalen-fallback-einmalig-einrichten).
- `ProtectHome=yes`, `ProtectSystem=strict`, `PrivateTmp=yes`: Dienstdateien nicht aus `~/...` laden. Manuelle TTS-WAVs in `/tmp` sind nicht automatisch im privaten Dienst-`/tmp` sichtbar.

Bei anderem Loginbenutzer müssen Unit, Installer und Dateigruppen gemeinsam angepasst werden; nur `User=` zu ändern reicht nicht. Die Isolation eines dedizierten Dienstbenutzers bleibt eine offene Verbesserung.

## Aktualisieren

**Bevorzugt für das bestehende Gerät:** gepushten Git-Commit mit dem [root-eigenen Deploy-Wrapper](operation.md#aus-einem-git-commit-einspielen-aktuelle-praxis) installieren. Die folgenden manuellen Installer-Schritte dienen einem lokalen Checkout oder einer Erstinstallation.

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

Auf dem Pi ist `/opt/pi-voice-assistant` kein Git-Checkout. Eingespielt wird ein gepushter Commit mit dem root-eigenen Wrapper [`deploy/pi-voice-install`](../deploy/pi-voice-install): Er nimmt nur einen vollständigen 40-stelligen Commit-Hash, lädt genau diesen Commit von GitHub, führt dessen `scripts/install-voice-service.sh` aus (Module, Unit, Neustart von `pi-ptt` und `pi-display`) und schreibt den Kurz-Hash nach `/opt/pi-voice-assistant/src/DEPLOYED`. Die sudo-Regel erlaubt `obivan` nur diesen Wrapper, nichts für `obivan` Beschreibbares läuft als root.

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

Den vollständigen Ablauf prüfen: `recording → processing → transcript → llm_start → llm_response → speech_started → speech_finished`. Dazwischen müssen `latency`-Events für STT, LLM und TTS erscheinen. Mit SHIM zusätzlich A, B während Aufnahme/STT/LLM/Ansage, C/D-Pegel und E-Ansage prüfen. Nach einem Reboot I²C, Audio und Dienst prüfen. Einzelne frühere Tests ersetzen keine Abnahme einer neu installierten Version. Aktuelle offene Tests stehen in [Roadmap](roadmap.md) und [Button-Abnahme](user-guide.md).


## Wartungsmodus

Updates des Pi und Neustarts von Pi und CT 107 löst Proximus nur im Wartungsmodus aus, und jede Aktion muss mit **Taste E** bestätigt werden (B bricht ab, 20 s Frist). **Den Server aktualisiert Proximus nie** (Entscheidung des Bedieners, 08.10.2026): Menü, Sprache und Server lehnen das ab, auf CT 107 ist der Update-Dienst gar nicht installiert, und wartende Server-Updates zeigt oder sagt Proximus nicht an – der Server wird manuell gewartet.

**Einschalten:** Menü „Wartung“ oder „Wartungsmodus“ sagen. Das Display zeigt dann WARTUNG: wartende Updates des Pi (Sicherheitsupdates rot), darunter die Aktionen „Pi aktualisieren“, „Pi neu starten“, „Server neu starten“, „Wartung beenden“. PiTFT-Tasten wählen, E wählt aus; danach steht unten rot „…? E = Ja · B = Nein“ und Proximus sagt an, was passiert („Pi aktualisieren: 12 Pakete. Bestätigen mit Taste E, abbrechen mit B.“). Per Sprache geht dasselbe: „Aktualisiere den Pi“, „Starte den Server neu“, „Wartung beenden“ („Aktualisiere den Server“ wird abgelehnt) – die Bestätigung bleibt bei der Taste. Nach 10 min ohne Eingabe endet der Modus (nicht während einer laufenden Aktion); solange er aktiv ist, geht Proximus nicht in Ruhe oder Schlaf.

**Ausführung:** Die Sprachdienste bekommen keine Root-Rechte. Sie legen nur eine leere Datei in `/run/proximus-maintenance/requests/` ab (`update` oder `reboot`); eine systemd-Path-Unit startet dann als root [`proximus-maintenance`](../deploy/maintenance/proximus-maintenance), das genau zwei feste Aktionen kennt:

- `update`: `apt-get update`, dann `apt-get -y upgrade` (keine neuen Pakete, keine Entfernungen, bestehende Konfigurationsdateien bleiben). Danach `status.json` mit Anzahl, Ergebnis und ob ein Neustart nötig ist (`/run/reboot-required` oder ein neuerer Kernel als der laufende).
- `reboot`: `systemctl reboot` nach 3 s.

Den Server-Neustart stößt der Pi über `POST /v1/maintenance` an (nur `reboot`, `update` gibt 403; Token, nur über die LAN-Adresse – über den Cloudflare-Tunnel antwortet der Server mit 403 –, höchstens alle 5 min). Fortschritt und Ergebnis liest der Pi alle 10 s (`status.json` lokal, `GET /v1/status` vom Server) und sagt das Ergebnis an, auch wenn Alarme stumm sind: „Pi: Aktualisierung abgeschlossen, 12 Pakete installiert. Neustart empfohlen.“ Danach zählt er die wartenden Updates neu.

Der Proxmox-Host wird bewusst nicht über Proximus gewartet.

### Einrichtung des Wartungsworkers

Auf jedem Host einmal als root ([`deploy/maintenance/install.sh`](../deploy/maintenance/install.sh)); es installiert Skript, Units, `tmpfiles.d`-Eintrag (Anforderungsordner gehört der Gruppe des Dienstnutzers) und ein Drop-in, das dem Dienst trotz `ProtectSystem=strict` das Schreiben in den Anforderungsordner erlaubt:

```sh
sh deploy/maintenance/install.sh obivan pi-ptt             # Pi
sh deploy/maintenance/install.sh servitor servitor-voice reboot-only   # CT 107: nur Neustart
```


## Gedächtnis: Speicherung und Datenschutz

**Lernen:** Mit Stick bekommt das Sprachmodell die Anweisung, dauerhafte Tatsachen und Anweisungen als eigene Zeile `MERKE: …` bzw. `DIREKTIVE: …` anzuhängen. Der Server schneidet sie ab (sie werden nie gesprochen) und schickt sie als `memory`-Ereignis an den Pi, der sie speichert.

**Ablauf:** Nur der Pi schreibt den Stick. Er schickt mit jedem Turn eine kompakte Kopie (Direktiven, neueste Fakten bis 6000 Zeichen, letzte 4 Runden) base64-kodiert im Header `X-Servitor-Memory`; im Statusheader steht `memory: on|off`. Der Server prüft und kürzt alles (`decode_header`) und baut daraus den Systemprompt: Persona, Lore, Gedächtnis, Uhrzeit. Gespeicherter Text gilt dort als Daten, nicht als Anweisung an das System. Ältere Pis ohne Gedächtnis-Unterstützung bleiben unverändert.

**Sitzung (SPX/1, [`src/protocol.py`](../src/protocol.py)):** Vor dem ersten Turn meldet sich der Pi mit `POST /v1/hello` und bekommt eine Sitzungs-ID, die er bei jedem Turn im Header `X-Servitor-Session` mitschickt. Der stabile Teil der Kopie (Fakten, Direktiven, Stimmabdrücke) geht dann nur einmal mit; der Server bestätigt ihn mit dem Ereignis `{"event": "session", "core": "<Prüfsumme>"}`. Danach enthält `X-Servitor-Memory` nur noch die Prüfsumme und die letzten Runden, bis sich der stabile Teil ändert. Kennt der Server die Sitzung nicht mehr (Neustart, nach 24 h ohne Nutzung, mehr als 16 Sitzungen), antwortet er diesen einen Turn ohne Gedächtnis und meldet `{"event": "session", "state": "unknown"}`. Der Pi meldet sich dann neu an und schickt die Kopie wieder vollständig. Ein älterer Server ohne `/v1/hello` (404) wird wie bisher mit der vollen Kopie bedient.

**Datenschutz:** Die Kopie geht mit jeder Frage an CT 107 und im Sprachkern-Modus AUTO an OpenRouter, wie die gesprochene Frage selbst. Im Modus LOKAL bleibt sie im Haus.

**Robustheit:** eine JSON-Datei `proximus/memory.json`, atomar geschrieben (temporäre Datei, fsync, rename, fsync des Ordners). Abziehen während des Schreibens verliert höchstens die letzte Änderung; eine beschädigte Datei wird als `memory.broken-<zeit>` beiseitegelegt. Ob der Stick steckt, prüft der Pi am Gerät `/dev/disk/by-label/PROXIMUS`, bevor er den Automount anfasst.


## Systemwartung

[`src/sysmon.py`](../src/sysmon.py) zählt alle 6 h (erstmals 2 min nach dem Start) wartende Pakete mit `apt-get -s upgrade` (Simulation, ohne root, mit `nice`), davon die aus einer `-security`-Quelle. Nur der Pi wird überwacht; den Server wartet der Bediener selbst, Proximus fragt dessen Updates nicht ab. Proximus sagt neue Updates an und erinnert höchstens einmal am Tag, nie im Schlaf; die Frage „Gibt es Updates?“ und der Statusbericht nennen sie auch. Installiert wird nur im [Wartungsmodus](#wartungsmodus).

Die Zahlen sind nur so aktuell wie die Paketlisten. `apt-daily` aktualisiert sie nur mit `APT::Periodic::Update-Package-Lists "1"` ([`deploy/20proximus-update-lists`](../deploy/20proximus-update-lists)); auf CT 107 ist das seit 08.10.2026 gesetzt, auf dem Pi über die [Stick-Einrichtung](setup.md#gedächtnis-stick-einrichten). Sind die Listen älter als 7 Tage, sagt Proximus das dazu.

## Netzwerk

Alle 30 s misst der Pi WLAN-Signal (`/proc/net/wireless`), die TCP-Verbindungszeit zu 1.1.1.1:443 und zum Server im LAN sowie die DNS-Auflösung von openrouter.ai. „Wie ist das Netzwerk?“ liest das vor. Zusätzlich zu den bestehenden Alarmen (Netz, Internet, Server weg) warnt er, wenn ein Problem eine Minute anhält, mit Entwarnung:

| Alarm | an | aus |
|---|---|---|
| WLAN-Signal schwach | ≤ −80 dBm | > −72 dBm |
| Netz langsam | ≥ 400 ms | < 200 ms |
| DNS gestört | Auflösung schlägt fehl | Auflösung klappt |

Bei abgeschaltetem oder sich verbindendem WLAN schweigen diese Alarme.


## Mikrofon und Vosk testen

**Vor direktem Mikrofontest Dienst stoppen, danach wieder starten:**

```bash
sudo systemctl stop pi-ptt
arecord -D plughw:CARD=wm8960soundcard,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/stt-test.wav
set -a; . /etc/pi-voice-assistant.env; set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/transcribe.py /tmp/stt-test.wav
sudo systemctl start pi-ptt
```

Der Servitor-DSP nutzt FFmpeg für Pitch, Resonanzen, Chorus, Maschinen-Aura und Limiter. Aussprachekorrekturen stehen in [`src/pronounce.py`](../src/pronounce.py). Nach Änderungen Alarm-Clips neu erzeugen ([Betrieb](operation.md#aktualisieren)).


## Fehlersuche

Zuerst `systemctl status pi-ptt.service --no-pager` und `journalctl -u pi-ptt.service -n 40 --no-pager` ansehen. Nicht gleichzeitig mehrere Recorder oder SHIM-/LED-Programme starten. Nach manuellen Tests den Dienst wieder starten.

### Audio

**WM8960 fehlt:** `aplay -l`, `arecord -l`, vorhandenes Overlay und `/boot/firmware/config.txt` prüfen. Auf dem bestätigten Kernel reicht `wm8960-soundcard`; kein zusätzlicher Waveshare-Treiber erforderlich. Nach Kernelupdate zuerst Modul/Overlay-Verfügbarkeit prüfen.

**Test läuft, aber stumm:** Mixer mit `amixer -c wm8960soundcard scontents` lesen. `Playback` und `Speaker` dämpfen nacheinander; Prozentwerte sind keine lineare Lautstärkeskala. Der frühere Test mit 70 % Speaker und 80 % Playback war zu stark gedämpft. Output-Mixer-Routing, beide Kanäle und Verdrahtung prüfen, leise beginnen. Aktuell bestätigt: Speaker 80 % / −19 dB. Nicht pauschal sämtliche Regler hochdrehen.

**Aufnahme leise/verrauscht:** erst eine 48-kHz-Stereoaufnahme manuell hören. Bestätigt sind L/R Input Mixer Boost, LINPUT1/RINPUT1=3 und ADC-Hochpass. Hörprüfung beweist keine Clippingfreiheit; bei Verzerrung Pegel reduzieren. Die zwei eingebauten Mikrofone benötigen kein zusätzliches USB-Mikrofon.

**Kein `capture.wav`:** nächste Aufnahme oder Dienststop löscht den Slot. Während `recording` ist er noch nicht veröffentlicht. `error` im Journal kann auf fehlgeschlagene bzw. zu kurze Aufnahme hinweisen. Vor Stop und nächstem Tastendruck abhören.

### GPIO und Tasten

**`Device or resource busy`:** meist läuft noch `pi-ptt.service` oder eine zweite Probe. Dienst stoppen, mit `gpioinfo` Consumer prüfen, erst dann Probe starten. Keine Leitung mit Force übernehmen.

**`waiting_for_release`:** normaler Startzustand. Alle Tasten loslassen; eine beim Start oder nach Zeitlimit/STT gehaltene Taste muss erst losgelassen und erneut gedrückt werden.

**B stoppt STT scheinbar nicht:** B verwirft das Ergebnis. Der native Vosk-Aufruf läuft zu Ende; solange bleibt der Aufnahmeslot belegt und die Verarbeitungsanzeige bleibt aktiv. Das ist implementiertes Verhalten.

### I²C / Button SHIM

**`shim_error` / `/dev/i2c-1` fehlt:**

```bash
sudo modprobe i2c-dev
i2cdetect -l
ls -l /dev/i2c-1
id obivan
sudo systemctl restart pi-ptt.service
```

Erwartet: `shim_ready`, Bus 1, Adresse `0x3f`. Bei Bedarf `i2c-dev` in `/etc/modules-load.d/pi-voice-i2c.conf` eintragen; nach Reboot prüfen. I²C muss im Boot-Config aktiviert bleiben. Ein unmittelbar nach `modprobe` gesehener `root:root 0600`-Zustand belegt nicht die endgültigen udev-Rechte. Bei Zugriffsfehlern Gruppe `i2c` und Geräterecht prüfen; keine pauschale Freigabe für alle.

Nach I²C-Ausfall bleibt GPIO17 verfügbar; SHIM wird erst beim Dienstneustart erneut geöffnet. Ein antwortendes `0x3f` bestätigt noch keine Tasten/LED. Für Einzeltests Dienst stoppen; nie parallel auf den Expander schreiben.

### STT

**Vosk fehlt/Modellfehler:** `STT_PROVIDER`, `VOSK_PYTHON_PATH` und `VOSK_MODEL_PATH` prüfen. Dienstinstaller installiert Vosk nicht; `scripts/install-vosk.sh` separat ausführen. Beschädigtes Modell erzeugt `stt_error`, kein erfolgreiches Transcript.

**`Vosk returned no transcript`:** besonders kurze Clips können gültiges Audio, aber keinen erkennbaren Satz enthalten. Mehrere Sekunden klar sprechen, WAV vor Stop abhören. Das ist kein Beleg für einen kaputten Audiotreiber. Zwei Clips um 0,13/0,38 s blieben leer; spätere Clips wurden erkannt.

**Erkennung schlecht:** das kleine deutsche Modell ist nach den bisherigen Vergleichsclips das wahrscheinliche Hauptlimit. L/R-Kanalwahl und SoX verbesserten diese Beispiele kaum. Das schließt Pegel-, Abstands- oder Umgebungsprobleme bei anderen Aufnahmen nicht aus. Optionale Command-Grammar ist noch nicht implementiert; sie wäre nur für begrenzte Befehle sinnvoll.

**`STT_PROVIDER must be vosk`:** eine alte Konfiguration enthält noch `openrouter`, `auto` oder einen anderen Wert. In `/etc/pi-voice-assistant.env` auf `STT_PROVIDER=vosk` korrigieren und den Dienst neu starten. STT führt keine API-Aufrufe mehr aus.

### LLM

**`llm_error` / OpenRouter nicht erreichbar:** Netzwerk prüfen und `OPENROUTER_API_KEY`, `OPENROUTER_LLM_MODEL` sowie `OPENROUTER_LLM_TIMEOUT_SECONDS` kontrollieren. Ein LLM-Fehler darf den Dienst nicht beenden; nach dem Fehler muss die nächste PTT-Aufnahme wieder möglich sein. API-Fehler gehören zum LLM-Schritt und sind kein STT-Fehler.

### Sprachausgabe

**`speak.py` fehlt:** aktuellen Dienstinstaller ausführen; er deployt den Wrapper nach `/opt/pi-voice-assistant/src/`. [Piper-Installation](setup.md#lokalen-fallback-einmalig-einrichten) beschreibt die separate Paket-/Modellinstallation.

**`No module named piper`:** Piper liegt in `/opt/pi-voice-assistant/.venv`. Der mitgelieferte Wrapper startet diesen Interpreter. Ein älteres lokales `speak.py` verwendete fälschlich `/usr/bin/python3 -m piper`. GitHub-Version installieren und `PIPER_PYTHON` prüfen; keine globale Piper-Installation als Umweg.

**`speech_error`:** Exitcode allein erklärt den Fehler nicht; unmittelbar vorangehende stderr-Zeilen ansehen. Pfade, Modell plus `.onnx.json`, Dienstrechte und `TTS_AUDIO_DEVICE` prüfen.

**Langsame Ansage:** zuerst Remote- oder Pi-Fallback und `PTT_MEMORY_MODE`/`TTS_DSP_MODE` prüfen. Auf CT 107 sind Modelle resident; auf dem Pi konkurrieren Vosk/Piper um RAM. `hybrid` hält Piper resident und trennt Vosk, `isolated` lädt getrennt mit höherer Startlatenz. Latenz-Events und RSS/Swap messen, keine historischen Einzelmessungen als Sollwert verwenden.

**Piper-Warnungen:** fehlendes Phonem und ONNX-Telemetrie-Meldung traten bei einem Aufruf mit Exitcode 0 auf. Sie waren dort nicht blockierend; das ist keine allgemeine Garantie für jede Stimme oder jeden Text.

### Dienst oder Installation

`PTT_RUNTIME_DIR` unter der Standardunit auf `/run/pi-ptt` belassen. Nach fehlgeschlagenem Installer prüfen, ob der Dienst gestoppt blieb. `python3-smbus` und `i2c-tools` vorher installieren; der aktuelle Installer prüft `i2cdetect` noch nicht selbst.


## Paralleländerung PR #79

[PR #79](https://github.com/0b-ivan/pi-voice-assistant/pull/79) ist zum Dokumentationsstand 10.10.2026 offen und hier nicht integriert. Er ergänzt `docs/logs.md`, Logwatch, RAM-Journal (16 MB) und Logsync zum Stick (5 min, 90 Tage/1 GB). Reparaturen laufen über den eingeschränkten Worker: nur ausgefallene Anzeige bzw. lokales Server-LLM neu starten, Logsync anfordern; höchstens drei Versuche täglich und automatisch 30 Minuten Abstand. Das sind Angaben zum Vorschlag, keine Abnahme dieses Branches.

Bei Zusammenführung die vollständige Logs-Anleitung aus #79 hier in den Betriebsleitfaden übernehmen, ihre Einrichtungs-/Prüfschritte erhalten und anschließend `docs/logs.md` entfernen. Dessen Verweise aus README, Betrieb, Wartung, Gedächtnis, Architektur und Roadmap an die sechs Dateien anpassen. Keine Logsync-Kommandos auf diesem Branch ausführen: dessen Code fehlt hier. Beide PRs ändern/entfernen dieselben Dokumente; ein sauberer Einzel-PR-Mergestatus beweist keine Konfliktfreiheit zwischen #79 und #81.

## Alte Pfadverweise im Code

Kommentare in `src/memory.py` und `src/sysmon.py` nennen noch `docs/memory.md`: gemeint sind [Stick-Einrichtung](setup.md#gedächtnis-stick-einrichten) und dieser Leitfaden. `deploy/maintenance/proximus-maintenance` nennt `docs/maintenance.md`: siehe [Wartungsmodus](#wartungsmodus). `src/llm.py` nennt die alte Billy-Lore: siehe Git-Historie in [Architektur](architecture.md#entscheidungen-und-historie). Die Kommentare bleiben im reinen Dokumentations-PR unverändert.

Bei lokalen Speicherproblemen `resident` (Code-Default), `isolated` (Beispielkonfiguration) und `hybrid` unterscheiden. `isolated` beendet Vosk vor Piper; `TTS_DSP_MODE=buffered` rendert vor Wiedergabe. `hybrid` benötigt das externalisierte Piper-Modell. Nicht ungeprüft zwischen Modi wechseln. Die frühere Pi-Diagnose zeigte, dass `MemoryMax` auf dem getesteten Kernel ohne Memory-cgroup keine wirksame Grenze bot; RSS/Swap und Kernel-OOM prüfen.

Referenz-Klangparameter für den Pi-Fallback (in `/etc/pi-voice-assistant.env`, nicht automatisch einspielen):

```text
TTS_VOICE_PROFILE=servitor
TTS_SERVITOR_MODEL=/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx
TTS_SERVITOR_AURA=reference
TTS_PIPER_SPEAKER_ID=4
TTS_PIPER_LENGTH_SCALE=1.10
TTS_PIPER_NOISE_SCALE=0.30
TTS_PIPER_NOISE_W_SCALE=0.25
TTS_PIPER_SENTENCE_SILENCE=0.32
OPENBLAS_NUM_THREADS=1
```
