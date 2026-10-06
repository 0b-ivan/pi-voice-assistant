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

Die Logs enthalten Transkripte, LLM-Antworten und Statusmeldungen, auch wenn WAVs flüchtig gespeichert werden. Zusätzlich werden `latency`-Events für `stt`, `llm` und `tts` ausgegeben; der residente Servitor-Pfad meldet weiterhin `tts_first_chunk` und `tts_playback_start`. Logs vor Weitergabe auf private Inhalte prüfen.

## Konfiguration

| Datei | Zweck |
|---|---|
| `/etc/pi-ptt.env` | GPIO, 40 ms Entprellung, 30 s Aufnahmelimit, Audiogerät, optionaler SHIM und Status-Sprachbefehl |
| `/etc/pi-voice-assistant.env` | Vosk/STT, OpenRouter-LLM, Piper-/Servitor-Modell und DSP-/Sprechparameter; echter API-Key nur hier |
| `/boot/firmware/config.txt` | Bestehendes WM8960-Overlay, I²C/I²S |
| `/etc/modules-load.d/pi-voice-i2c.conf` | Bei aktiviertem SHIM `i2c-dev` beim Boot laden |

`config/client.env.example` ist ein ungenutzter früherer Backend-Entwurf und wird vom Dienst nicht geladen.

Für den integrierten Assistentenpfad mindestens:

```text
STT_PROVIDER=vosk
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_LLM_MODEL=openai/gpt-5.4-mini
OPENROUTER_LLM_TIMEOUT_SECONDS=15
OPENROUTER_LLM_MAX_TOKENS=180
TTS_VOICE_PROFILE=servitor
```

Den echten Key niemals in Git oder eine Beispielkonfiguration schreiben. `deploy/pi-ptt.service` lädt `/etc/pi-voice-assistant.env` bereits über `EnvironmentFile=`.

Nach Konfigurationsänderungen `sudo systemctl restart pi-ptt.service`. Für Overlay-Änderungen ist ein Pi-Neustart nötig. Mixeränderungen nur nach Hörprüfung mit `sudo alsactl store wm8960soundcard` dauerhaft speichern.

`PTT_RUNTIME_DIR=/run/pi-ptt` ist unter der mitgelieferten Unit fest vorgegeben: systemd erstellt/erlaubt genau dieses Verzeichnis. Ein anderer Pfad ist nur bei manuellem Vordergrundbetrieb oder passender eigener Unit möglich.

## Dateien und Rechte

- Dienstcode: `/opt/pi-voice-assistant/src`, root-verwaltet. Der Dienst läuft tatsächlich als **obivan**, mit `audio`, `gpio`, `i2c`; kein dedizierter Dienstbenutzer implementiert.
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

## Abnahme nach Änderungen

Den vollständigen Ablauf prüfen: `recording → processing → transcript → llm_start → llm_response → speech_started → speech_finished`. Dazwischen müssen `latency`-Events für STT, LLM und TTS erscheinen. Mit SHIM zusätzlich A, B während Aufnahme/STT/LLM/Ansage, C/D-Pegel und E-Ansage prüfen. Nach einem Reboot I²C, Audio und Dienst prüfen. Einzelne frühere Tests ersetzen keine Abnahme einer neu installierten Version. Aktuelle offene Tests stehen in [Roadmap](roadmap.md) und [Button-Abnahme](button-controls.md).
