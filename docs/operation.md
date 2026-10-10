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

Auf **CT 107** ist `/opt/servitor-voice/repo` ein Git-Checkout: `git fetch` und `git checkout --detach <commit>`, dann `systemctl restart servitor-voice`; Unit/Env bzw. Offline-LLM mit `server/install-ct.sh` bzw. `server/install-llm.sh`; `install-ct.sh` startet einen installierten `servitor-rvc` (Billys RVC-Stimme, `server/install-rvc.sh`) mit neu. `/opt/servitor-voice/DEPLOYED` nennt den Commit.

## Abnahme nach Änderungen

Den vollständigen Ablauf prüfen: `recording → processing → transcript → llm_start → llm_response → speech_started → speech_finished`. Dazwischen müssen `latency`-Events für STT, LLM und TTS erscheinen. Mit SHIM zusätzlich A, B während Aufnahme/STT/LLM/Ansage, C/D-Pegel und E-Ansage prüfen. Nach einem Reboot I²C, Audio und Dienst prüfen. Einzelne frühere Tests ersetzen keine Abnahme einer neu installierten Version. Aktuelle offene Tests stehen in [Roadmap](roadmap.md) und [Button-Abnahme](button-controls.md).
