# Piper-TTS einrichten

Lokale deutsche Sprachausgabe auf Pi Zero 2 W / Trixie ist mit **Piper 1.8.0** und **`de_DE-thorsten-low`** getestet. Ausgabe: S16_LE, 16 kHz, Mono über WM8960. Das ist Standalone-TTS bzw. SHIM-Statusausgabe; LLM und automatische Antwortwiedergabe fehlen weiterhin.

## Installation

Im aktuellen Repo-Checkout auf dem Pi:

```bash
sudo bash scripts/install-piper.sh
sudo bash scripts/install-voice-service.sh
/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py "Hallo Ivan, ich kann lokal sprechen."
```

Der erste Installer richtet Piper in `/opt/pi-voice-assistant/.venv` ein und lädt Modell plus passende `.onnx.json` nach `/opt/pi-voice-assistant/tts/`. Der Dienstinstaller deployt `speak.py` und aktualisiert die übrigen Dienstmodule; vorhandene Konfigurationen bleiben erhalten. Paket-/Modelldownload benötigt Netzwerk, spätere Synthese nicht.

`/opt/pi-voice-assistant`, `src/` und `scripts/` bleiben root-verwaltet; nur `.venv/` und `tts/` sind im Piper-Installer für `obivan` beschreibbar. Kein rekursives `chown` des gesamten Anwendungsverzeichnisses. Dienst läuft mit `ProtectHome=yes`, daher Modell/Programme nicht unter `~/...` ablegen.

## Konfiguration und SHIM E

| Variable | Standard |
|---|---|
| `PIPER_MODEL` | `/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx` |
| `PIPER_PYTHON` | `/opt/pi-voice-assistant/.venv/bin/python` |
| `TTS_AUDIO_DEVICE` | `plughw:CARD=wm8960soundcard,DEV=0` |

Der Standalone-Wrapper kann mit System-Python gestartet werden; sein Piper-Unterprozess nutzt den venv-Interpreter. Der PTT-Dienst lädt Piper dagegen einmal aus diesem venv und hält `PiperVoice` resident. Dazu gelten zusätzlich:

| Variable | Standard |
|---|---|
| `PIPER_VENV` | `/opt/pi-voice-assistant/.venv` |
| `PIPER_MODEL` | `/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx` |
| `TTS_AUDIO_DEVICE` | `plughw:CARD=wm8960soundcard,DEV=0` |

Eigene Werte in `/etc/pi-voice-assistant.env` eintragen und Dienst neu starten. `PTT_SPEAK_COMMAND` bleibt nur als Kompatibilitäts-Fallback aktiv, falls der residente Import oder das Modellladen fehlschlägt.

[Button-Steuerung](button-controls.md) beschreibt die Aktivierung und Abnahme. Der Dienst meldet beim Start `tts_loading` und danach entweder `tts_ready` oder `tts_error` mit Fallback.

## Wiedergabe und Grenzen

Der residente Dienst erzeugt pro Ansage ein temporäres WAV unter `PTT_RUNTIME_DIR`, spielt es mit einem eigenen `aplay`-Prozess ab und entfernt es anschließend. Synthese läuft außerhalb des Button-Control-Loops; Cancel stoppt Wiedergabe sofort und verwirft eine noch laufende native Piper-Synthese nach deren Rückkehr. Der Standalone-Wrapper behält sein bisheriges Verhalten. Letzter bestätigter analoger Speaker-Pegel: beide Kanäle **80 % / −19 dB**; C/D ändern den separaten digitalen Playback-Pegel. [Audio-Setup](setup.md#3-audio-testen).

Fehlendes Phonem und ONNX-Telemetrie-Warnung waren beim protokollierten Aufruf nicht blockierend (Exitcode 0). [Troubleshooting](troubleshooting.md#sprachausgabe).

Bekannter offener Wrapper-Befund: `--` wird derzeit als Teil des Sprachtexts an Piper übergeben. Die entsprechende Benchmarkkorrektur hat diesen Wrapper nicht geändert. [Textpfad und Performance](local-speech.md#performance).

Pi-Messungen und Speichergrenzen stehen unter [TTS-Performance](local-speech.md) und [Ressourcenbericht](piper-resources.md). Cache und Streaming sind noch nicht implementiert; resident Piper ist im PTT-Dienst implementiert.
