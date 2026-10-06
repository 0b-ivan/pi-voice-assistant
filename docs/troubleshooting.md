# Troubleshooting

Zuerst `systemctl status pi-ptt.service --no-pager` und `journalctl -u pi-ptt.service -n 40 --no-pager` ansehen. Nicht gleichzeitig mehrere Recorder oder SHIM-/LED-Programme starten. Nach manuellen Tests den Dienst wieder starten.

## Audio

**WM8960 fehlt:** `aplay -l`, `arecord -l`, vorhandenes Overlay und `/boot/firmware/config.txt` prüfen. Auf dem bestätigten Kernel reicht `wm8960-soundcard`; kein zusätzlicher Waveshare-Treiber erforderlich. Nach Kernelupdate zuerst Modul/Overlay-Verfügbarkeit prüfen.

**Test läuft, aber stumm:** Mixer mit `amixer -c wm8960soundcard scontents` lesen. `Playback` und `Speaker` dämpfen nacheinander; Prozentwerte sind keine lineare Lautstärkeskala. Der frühere Test mit 70 % Speaker und 80 % Playback war zu stark gedämpft. Output-Mixer-Routing, beide Kanäle und Verdrahtung prüfen, leise beginnen. Aktuell bestätigt: Speaker 80 % / −19 dB. Nicht pauschal sämtliche Regler hochdrehen.

**Aufnahme leise/verrauscht:** erst eine 48-kHz-Stereoaufnahme manuell hören. Bestätigt sind L/R Input Mixer Boost, LINPUT1/RINPUT1=3 und ADC-Hochpass. Hörprüfung beweist keine Clippingfreiheit; bei Verzerrung Pegel reduzieren. Die zwei eingebauten Mikrofone benötigen kein zusätzliches USB-Mikrofon.

**Kein `capture.wav`:** nächste Aufnahme oder Dienststop löscht den Slot. Während `recording` ist er noch nicht veröffentlicht. `error` im Journal kann auf fehlgeschlagene bzw. zu kurze Aufnahme hinweisen. Vor Stop und nächstem Tastendruck abhören.

## GPIO und Tasten

**`Device or resource busy`:** meist läuft noch `pi-ptt.service` oder eine zweite Probe. Dienst stoppen, mit `gpioinfo` Consumer prüfen, erst dann Probe starten. Keine Leitung mit Force übernehmen.

**`waiting_for_release`:** normaler Startzustand. Alle Tasten loslassen; eine beim Start oder nach Zeitlimit/STT gehaltene Taste muss erst losgelassen und erneut gedrückt werden.

**B stoppt STT scheinbar nicht:** B verwirft das Ergebnis. Der native Vosk-Aufruf läuft zu Ende; solange bleibt der Aufnahmeslot belegt und die LED blau. Das ist implementiertes Verhalten.

## I²C / Button SHIM

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

## STT

**Vosk fehlt/Modellfehler:** `STT_PROVIDER`, `VOSK_PYTHON_PATH` und `VOSK_MODEL_PATH` prüfen. Dienstinstaller installiert Vosk nicht; `scripts/install-vosk.sh` separat ausführen. Beschädigtes Modell erzeugt `stt_error`, kein erfolgreiches Transcript.

**`Vosk returned no transcript`:** besonders kurze Clips können gültiges Audio, aber keinen erkennbaren Satz enthalten. Mehrere Sekunden klar sprechen, WAV vor Stop abhören. Das ist kein Beleg für einen kaputten Audiotreiber. Zwei Clips um 0,13/0,38 s blieben leer; spätere Clips wurden erkannt.

**Erkennung schlecht:** das kleine deutsche Modell ist nach den bisherigen Vergleichsclips das wahrscheinliche Hauptlimit. L/R-Kanalwahl und SoX verbesserten diese Beispiele kaum. Das schließt Pegel-, Abstands- oder Umgebungsprobleme bei anderen Aufnahmen nicht aus. Optionale Command-Grammar ist noch nicht implementiert; sie wäre nur für begrenzte Befehle sinnvoll.

**`STT_PROVIDER must be vosk`:** eine alte Konfiguration enthält noch `openrouter`, `auto` oder einen anderen Wert. In `/etc/pi-voice-assistant.env` auf `STT_PROVIDER=vosk` korrigieren und den Dienst neu starten. STT führt keine API-Aufrufe mehr aus.

## LLM

**`llm_error` / OpenRouter nicht erreichbar:** Netzwerk prüfen und `OPENROUTER_API_KEY`, `OPENROUTER_LLM_MODEL` sowie `OPENROUTER_LLM_TIMEOUT_SECONDS` kontrollieren. Ein LLM-Fehler darf den Dienst nicht beenden; nach dem Fehler muss die nächste PTT-Aufnahme wieder möglich sein. API-Fehler gehören zum LLM-Schritt und sind kein STT-Fehler.

## Sprachausgabe

**`speak.py` fehlt:** aktuellen Dienstinstaller ausführen; er deployt den Wrapper nach `/opt/pi-voice-assistant/src/`. [TTS-Setup](text-to-speech.md) beschreibt die separate Paket-/Modellinstallation.

**`No module named piper`:** Piper liegt in `/opt/pi-voice-assistant/.venv`. Der mitgelieferte Wrapper startet diesen Interpreter. Ein älteres lokales `speak.py` verwendete fälschlich `/usr/bin/python3 -m piper`. GitHub-Version installieren und `PIPER_PYTHON` prüfen; keine globale Piper-Installation als Umweg.

**`speech_error`:** Exitcode allein erklärt den Fehler nicht; unmittelbar vorangehende stderr-Zeilen ansehen. Pfade, Modell plus `.onnx.json`, Dienstrechte und `TTS_AUDIO_DEVICE` prüfen.

**Langsame Ansage:** der Wrapper startet Piper pro Satz und erzeugt das gesamte WAV vor `aplay`. Die [Pi-Messungen](piper-resources.md) zeigen deutlich schnellere Synthese mit geladenem Modell, aber Speicherdruck neben Vosk. Vorerst keinen dauerhaften Modellprozess aktivieren; der Status-WAV-Cache ist noch nicht implementiert. [Performance-Stand](local-speech.md#performance).

**Piper-Warnungen:** fehlendes Phonem und ONNX-Telemetrie-Meldung traten bei einem Aufruf mit Exitcode 0 auf. Sie waren dort nicht blockierend; das ist keine allgemeine Garantie für jede Stimme oder jeden Text.

## Dienst oder Installation

`PTT_RUNTIME_DIR` unter der Standardunit auf `/run/pi-ptt` belassen. Nach fehlgeschlagenem Installer prüfen, ob der Dienst gestoppt blieb. `python3-smbus` und `i2c-tools` vorher installieren; der aktuelle Installer prüft `i2cdetect` noch nicht selbst. Weitere offene Codepunkte: [Projektprüfung](project-review.md).
