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

Der Wrapper kann mit System-Python gestartet werden; sein Piper-Unterprozess nutzt den venv-Interpreter. Eigene Werte in `/etc/pi-voice-assistant.env` eintragen und Dienst neu starten. SHIM-Statusansagen verwenden in `/etc/pi-ptt.env`:

```text
PTT_SPEAK_COMMAND="/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py"
```

[Button-Steuerung](button-controls.md) beschreibt die Aktivierung und Abnahme. Ohne installiertes Piper/Modell meldet E `speech_error`.

## Wiedergabe und Grenzen

Wrapper erzeugt ein temporäres WAV, spielt es mit `aplay` ab und entfernt es anschließend. Letzter bestätigter analoger Speaker-Pegel: beide Kanäle **80 % / −19 dB**; C/D ändern den separaten digitalen Playback-Pegel. [Audio-Setup](setup.md#3-audio-testen).

Fehlendes Phonem und ONNX-Telemetrie-Warnung waren beim protokollierten Aufruf nicht blockierend (Exitcode 0). [Troubleshooting](troubleshooting.md#sprachausgabe).

Bekannter offener Wrapper-Befund: `--` wird derzeit als Teil des Sprachtexts an Piper übergeben. Die entsprechende Benchmarkkorrektur hat diesen Wrapper nicht geändert. [Textpfad und Performance](local-speech.md#performance).

Pi-Messungen und Speichergrenzen stehen unter [TTS-Performance](local-speech.md) und [Ressourcenbericht](piper-resources.md). Cache, Streaming und dauerhafter Piper-Prozess sind noch nicht implementiert.
