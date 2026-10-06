# Speech-to-Text: Vosk

Auf `pi-assistent` ist Speech-to-Text bewusst **lokal-only**. Der Dienst akzeptiert ausschließlich `STT_PROVIDER=vosk`; OpenRouter erhält kein Mikrofon-Audio und wird erst nach der Transkription als LLM verwendet.

PTT → Vosk mit echten WM8960-Aufnahmen ist seit 05.10.2026 bestätigt. Vosk 0.3.45 funktioniert unter Python 3.13/aarch64 auf Trixie.

## Installation und Konfiguration

[Setup](setup.md#4-repository-und-dienst-installieren) enthält die vollständige Installation. Der Vosk-Installer wird einmal benötigt:

```bash
sudo bash scripts/install-vosk.sh
```

Paket und Modell liegen außerhalb des Repositories:

- Python-Paket: `/opt/pi-voice-assistant/vendor`
- deutsches Modell: `/opt/pi-voice-assistant/models/vosk-model-small-de-0.15`

Konfiguration in `/etc/pi-voice-assistant.env`:

```text
STT_PROVIDER=vosk
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor
```

`openrouter`, `auto` und andere Werte werden absichtlich abgewiesen. Der OpenRouter-Key gehört zum getrennten LLM-Schritt, nicht zur STT.

## Live-Pfad

Bei gedrückter PTT-Taste nimmt der Dienst direkt in **16 kHz / Mono / S16_LE** auf und speist die PCM-Daten gleichzeitig in einen inkrementellen Vosk-Recognizer. Das Modell wird beim Dienststart vorgewärmt. Beim Loslassen muss Vosk im Normalfall nur noch `FinalResult()` liefern.

```text
PTT halten
  -> arecord 16 kHz / Mono
  -> LiveVoskRecognizer.accept_pcm(...)
PTT loslassen
  -> FinalResult()
  -> transcript
  -> OpenRouter LLM
```

Der erzeugte WAV-Slot bleibt als validierter Fallback erhalten. Wenn der Live-Recognizer während der Aufnahme ausfällt, wird die WAV-Datei lokal über denselben Vosk-Adapter transkribiert. Es gibt weiterhin nur einen Capture-/Processing-Slot.

B verwirft eine laufende Aufnahme bzw. das Ergebnis. STT-Fehler erzeugen `stt_error` und dürfen den Dienst nicht beenden.

## Unterstützte WAV-Formate im Fallback

Der Vosk-Adapter akzeptiert unkomprimierte 16-Bit-PCM-WAVs mit:

- 16 kHz Mono oder Stereo
- 48 kHz Mono oder Stereo

Stereo wird gemittelt; 48 kHz wird auf 16 kHz heruntergerechnet. Für den normalen Live-PTT-Pfad ist diese Konvertierung nicht nötig.

## Hardware-Messwerte

Einzelmessungen vom 05.10.2026, kein allgemeines Leistungsversprechen:

| Messung | Ergebnis |
|---|---|
| 6-s-Clip, erster Modellaufruf | 15,59 s |
| Gleicher Clip, Modell schon geladen | 6,75 / 6,78 s |
| Laufender PTT-Dienst mit geladenem Vosk | 145728 kB RSS ≈ 142 MiB |
| Gesamtsystem zum Messzeitpunkt | 415 MiB RAM, 120 MiB verfügbar, 105 MiB Swap belegt |

Diese älteren Clip-Messungen stammen noch aus dem WAV-Nachtranskriptionspfad. Der aktuelle Live-Pfad beginnt die Erkennung bereits während PTT gehalten wird; deshalb ist für die neue End-to-End-Kette vor allem die Release→Transcript-Latenz relevant und muss auf dem Pi neu gemessen werden.

## Standalone-Test und Logs

Eine eigene WAV testen:

```bash
set -a
. /etc/pi-voice-assistant.env
set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/transcribe.py /tmp/stt-test.wav
```

stdout enthält den Text, stderr `STT_PROVIDER_USED=vosk`. Fehler erscheinen als `STT_ERROR:` mit Exitstatus 1.

Im PTT-Journal erscheinen beim Start `stt_loading`/`stt_ready`, während Aufnahme `recording` mit `stt=vosk-live`, danach `capture_ready`, `processing`, `transcript` und ein `latency`-Event mit `stage=stt`. Erst danach beginnt `llm_start`.

Audiodaten sind flüchtig; Transkripte werden protokolliert. Siehe [Troubleshooting](troubleshooting.md#stt) und [Betrieb](operation.md).
