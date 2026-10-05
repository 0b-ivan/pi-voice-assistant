# Text-to-Speech mit Piper 🔊

Stand 05.10.2026: Lokale deutsche Sprachausgabe ist auf dem Raspberry Pi Zero 2 W erfolgreich getestet. Verwendet wird Piper 1.8.0 mit der Stimme `de_DE-thorsten-low`. Die Ausgabe erfolgt lokal über ALSA und das WM8960 Audio-HAT.

## Getesteter Pfad

```text
Antworttext
    ↓
Piper 1.8.0
    ↓
de_DE-thorsten-low
    ↓
16 kHz / Mono WAV
    ↓
aplay
    ↓
plughw:CARD=wm8960soundcard,DEV=0
    ↓
WM8960 / Lautsprecher
```

Die TTS-Kette benötigt für die Sprachsynthese kein Netzwerk. Im geplanten MVP bleibt nur die eigentliche LLM-Antwort über OpenRouter online.

## Installation

Piper läuft in einer eigenen virtuellen Umgebung unter `/opt/pi-voice-assistant/.venv`.

Manuell:

```bash
sudo apt update
sudo apt install -y python3-venv alsa-utils

sudo mkdir -p /opt/pi-voice-assistant/tts
sudo chown -R obivan:obivan /opt/pi-voice-assistant

python3 -m venv /opt/pi-voice-assistant/.venv
/opt/pi-voice-assistant/.venv/bin/pip install --upgrade pip
/opt/pi-voice-assistant/.venv/bin/pip install piper-tts==1.8.0

/opt/pi-voice-assistant/.venv/bin/python \
  -m piper.download_voices \
  --data-dir /opt/pi-voice-assistant/tts \
  de_DE-thorsten-low
```

Das Modell liegt danach außerhalb des Git-Repositories:

```text
/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx
/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx.json
```

Das getestete ONNX-Modell ist ungefähr 61 MB groß. Modell- und Audiodateien werden nicht eingecheckt.

## Standalone-Test

```bash
/opt/pi-voice-assistant/.venv/bin/python \
  -m piper \
  -m /opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx \
  -f /tmp/tts-test.wav \
  -- "Hallo Ivan. Ich bin dein lokaler Sprachassistent auf dem Raspberry Pi."

aplay -D plughw:CARD=wm8960soundcard,DEV=0 /tmp/tts-test.wav
```

Bestätigtes Ausgabeformat:

```text
Signed 16 bit Little Endian
16000 Hz
Mono
```

## `src/speak.py`

Der Wrapper erzeugt eine temporäre WAV-Datei, spielt sie über das WM8960 ab und entfernt sie anschließend wieder.

Nach Installation über das Projekt:

```bash
/opt/pi-voice-assistant/src/speak.py "Hallo Ivan, ich kann jetzt komplett lokal sprechen."
```

Optionale Umgebungsvariablen:

| Variable | Standard |
|---|---|
| `PIPER_MODEL` | `/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx` |
| `PIPER_PYTHON` | `/opt/pi-voice-assistant/.venv/bin/python` |
| `TTS_AUDIO_DEVICE` | `plughw:CARD=wm8960soundcard,DEV=0` |

## Lautstärke

Der zuvor dokumentierte Speaker-Wert von 95 % ist nicht mehr aktuell. Für die TTS-Wiedergabe wurde am 05.10.2026 folgender Pegel als angenehm bestätigt:

```text
Front Left:  Playback 102 [80%] [-19.00dB]
Front Right: Playback 102 [80%] [-19.00dB]
```

Gesetzt und gespeichert mit:

```bash
amixer -c wm8960soundcard sset 'Speaker' 80%
sudo alsactl store wm8960soundcard
```

## Bekannte Beobachtung

Bei einzelnen deutschen Texten meldet Piper:

```text
WARNING:piper.phoneme_ids:Missing phoneme from id map: ̧
```

Die Synthese und Wiedergabe wurden trotzdem erfolgreich abgeschlossen. Der Hinweis ist aktuell nicht blockierend, sollte aber bei späteren Stimmen- oder Qualitätsvergleichen erneut geprüft werden.

## Noch offen

- Piper-Latenz und RAM-Verbrauch auf dem Zero 2 W messen.
- TTS in den vollständigen PTT → STT → LLM → TTS-Ablauf integrieren.
- Während der Wiedergabe neue Aufnahme zuverlässig sperren.
- Weitere deutsche Piper-Stimmen bei Bedarf gegen `de_DE-thorsten-low` vergleichen.
