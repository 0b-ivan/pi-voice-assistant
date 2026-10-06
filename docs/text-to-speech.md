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
| `TTS_VOICE_PROFILE` | `normal` (`servitor` optional) |
| `TTS_SOX_BIN` | `/usr/bin/sox` |

Der Standalone-Wrapper kann mit System-Python gestartet werden; sein Piper-Unterprozess nutzt den venv-Interpreter. Der PTT-Dienst lädt Piper dagegen einmal aus diesem venv und hält `PiperVoice` resident. Dazu gelten zusätzlich:

| Variable | Standard |
|---|---|
| `PIPER_VENV` | `/opt/pi-voice-assistant/.venv` |
| `PIPER_MODEL` | `/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx` |
| `TTS_AUDIO_DEVICE` | `plughw:CARD=wm8960soundcard,DEV=0` |
| `TTS_VOICE_PROFILE` | `normal` (`servitor` optional) |
| `TTS_SOX_BIN` | `/usr/bin/sox` |

Eigene Werte in `/etc/pi-voice-assistant.env` eintragen und Dienst neu starten. `PTT_SPEAK_COMMAND` bleibt nur als Kompatibilitäts-Fallback aktiv, falls der residente Import oder das Modellladen fehlschlägt.

[Button-Steuerung](button-controls.md) beschreibt die Aktivierung und Abnahme. Der Dienst meldet beim Start `tts_loading` und danach entweder `tts_ready` oder `tts_error` mit Fallback.

## Servitor-Profil

`TTS_VOICE_PROFILE=servitor` legt nach der Piper-Synthese eine leichte
SoX-Effektkette über das WAV. Sie ist für den Pi Zero 2 W bewusst ohne weiteres
KI-/Voice-Conversion-Modell gebaut: Pitch-Absenkung, 180–4000-Hz-Bandbegrenzung,
Kompression, leichte Sättigung, eine 60-Hz-Tremolo/Ringmod-Anmutung und kurzer
Hall. Das Preset-Konzept orientiert sich an
[marmalade-tts](https://github.com/maxwhipw/marmalade-tts), bleibt aber auf den
bestehenden Piper-/WM8960-Pfad dieses Projekts zugeschnitten.

Aktivieren und mit der Statusabfrage auf Button SHIM **E** prüfen:

```bash
sudo vim /etc/pi-voice-assistant.env
# TTS_VOICE_PROFILE=servitor
sudo systemctl restart pi-ptt.service
journalctl -u pi-ptt.service -n 30 --no-pager
```

Beim Dienststart muss `tts_ready` zusätzlich `"profile":"servitor"` melden.
Der Installer installiert SoX zusammen mit Piper. `normal` umgeht die
Effektverarbeitung vollständig.

## Wiedergabe und Grenzen

Der residente Dienst erzeugt pro Ansage ein temporäres WAV unter `PTT_RUNTIME_DIR`, spielt es mit einem eigenen `aplay`-Prozess ab und entfernt es anschließend. Synthese läuft außerhalb des Button-Control-Loops; Cancel stoppt Wiedergabe sofort und verwirft eine noch laufende native Piper-Synthese nach deren Rückkehr. Der Standalone-Wrapper behält sein bisheriges Verhalten. Letzter bestätigter analoger Speaker-Pegel: beide Kanäle **80 % / −19 dB**; C/D ändern den separaten digitalen Playback-Pegel. [Audio-Setup](setup.md#3-audio-testen).

Fehlendes Phonem und ONNX-Telemetrie-Warnung waren beim protokollierten Aufruf nicht blockierend (Exitcode 0). [Troubleshooting](troubleshooting.md#sprachausgabe).

Bekannter offener Wrapper-Befund: `--` wird derzeit als Teil des Sprachtexts an Piper übergeben. Die entsprechende Benchmarkkorrektur hat diesen Wrapper nicht geändert. [Textpfad und Performance](local-speech.md#performance).

Pi-Messungen und Speichergrenzen stehen unter [TTS-Performance](local-speech.md) und [Ressourcenbericht](piper-resources.md). Cache und Streaming sind noch nicht implementiert; resident Piper ist im PTT-Dienst implementiert.
