# Sprache: Vosk, OpenRouter und Piper

Im **Normalbetrieb** verarbeitet **CT 107** Audio mit Vosk, beantwortet direkte Fragen oder ruft OpenRouter auf und erzeugt die Antwort mit Piper/Servitor-DSP. Auf dem Pi gibt es Vosk und Piper als Rückfallweg; **Qwen3-4B läuft nur auf CT 107**, nicht auf dem Pi.

## Lokalen Fallback einmalig einrichten

Aus dem geprüften Checkout **auf dem Pi**:

```bash
sudo bash scripts/install-vosk.sh
sudo bash scripts/install-piper.sh
sudo bash scripts/install-voice-service.sh
```

Konfiguration auf dem Pi: `/etc/pi-voice-assistant.env`. Der Server-Token und seine URL kommen aus [Betrieb](../operation.md#konfiguration).

| Funktion | Einstellung / Ort |
|---|---|
| Offline-STT | `STT_PROVIDER=vosk`; `VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15` |
| Vosk-Python | `VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor` |
| Piper | `PIPER_VENV=/opt/pi-voice-assistant/.venv`; Modelle unter `tts/` |
| Stimmeffekt | `TTS_VOICE_PROFILE=servitor`, Thorsten Emotional Speaker 4 + DSP |
| Audio | `plughw:CARD=wm8960soundcard,DEV=0` |

Die Aufnahme erfolgt mit **16 kHz Mono S16_LE**. Während PTT wird sie zum Server gestreamt. Ohne CT 107 nutzt der Pi inkrementelles Vosk; WAV-Fallback unterstützt PCM16 bei 16/48 kHz. OpenRouter erhält nur Text, kein Mikrofon-Audio.

## Kurzer Test

**Vor direktem Mikrofontest Dienst stoppen, danach wieder starten:**

```bash
sudo systemctl stop pi-ptt
arecord -D plughw:CARD=wm8960soundcard,DEV=0 -f S16_LE -r 16000 -c 1 -d 5 /tmp/stt-test.wav
set -a; . /etc/pi-voice-assistant.env; set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/transcribe.py /tmp/stt-test.wav
sudo systemctl start pi-ptt
```

Der Servitor-DSP nutzt FFmpeg für Pitch, Resonanzen, Chorus, Maschinen-Aura und Limiter. Aussprachekorrekturen stehen in [`src/pronounce.py`](../../src/pronounce.py). Nach Änderungen Alarm-Clips neu erzeugen ([Betrieb](../operation.md#aktualisieren)).

**Historische Details statt doppelter Live-Anleitungen:** [Vosk-Messungen](speech.md), [Piper-Performance](../history/piper-resources-2026-10-06.md), [alter TTS-DSP-Stand](speech.md). Die echten Vosk/Whisper-Vergleichstests stehen noch aus ([Roadmap](../roadmap.md)).
