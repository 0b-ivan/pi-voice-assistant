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

## Mikrofonpegel prüfen

[`scripts/mic-check.py`](../scripts/mic-check.py) nimmt genau wie PTT auf (16 kHz Mono, `PTT_AUDIO_DEVICE`) und misst Rauschen, Sprachpegel, Spitze, Übersteuerung und Rauschabstand in dBFS. Das Mikrofon muss frei sein (das Aktivierungswort belegt es):

```bash
sudo systemctl stop pi-ptt
python3 /opt/pi-voice-assistant/scripts/mic-check.py level
sudo systemctl start pi-ptt
```

Ziel: Sprache −32 … −10 dBFS, keine Übersteuerung, Rauschabstand ≥ 25 dB. Das Skript sagt, ob `Capture` (1 Schritt = 0,75 dB) hoch oder runter soll, und zeigt die aktuellen Mixerwerte. Nach einer Änderung erneut messen, erst dann `sudo alsactl store wm8960soundcard`. Ein knapper Rauschabstand lässt sich mit mehr Verstärkung nicht beheben: näher sprechen oder Störquelle suchen.

## Whisper auf CT 107 (optional)

Mit `SERVITOR_STT=whisper` in `/etc/servitor-voice.env` transkribiert CT 107 nach dem Loslassen zusätzlich mit faster-whisper ([`src/whisper_stt.py`](../src/whisper_stt.py)):

- Vosk streamt weiter mit und entscheidet, ob überhaupt gesprochen wurde (Whisper erfindet aus Stille Sätze wie „Untertitel im Auftrag des ZDF“).
- Passt der Vosk-Text schon zu einer festen Anfrage oder einem Stoppwort („wie spät ist es“, „sei still“), wird Whisper übersprungen: diese Antworten bleiben so schnell wie bisher.
- Sonst ersetzt der Whisper-Text den von Vosk; scheitert Whisper oder liefert nur Erfundenes, bleibt Vosk. Passphrasen (`mode=transcribe`) bleiben bei Vosk, weil sie mit Vosk-Schreibweise gespeichert sind.
- Jede Whisper-Runde schreibt beide Texte ins Journal: `journalctl -u servitor-voice | grep stt_compare`.

Kosten: Whisper small braucht ~1 GB RAM und auf CT 107 etwa 1–2 s nach dem Loslassen ([Messung](architecture.md#whisper-als-erkenner-auf-synthetischer-sprache-kein-gewinn)). Ohne die Einstellung oder wenn das Modell nicht lädt (`whisper_unavailable` im Journal), läuft alles wie bisher mit Vosk.

```bash
# auf CT 107
sh /opt/servitor-voice/repo/server/install-whisper.sh small
echo SERVITOR_STT=whisper >> /etc/servitor-voice.env
systemctl restart servitor-voice
```

### Entscheidung mit echter Stimme

Synthetische Sprache begünstigt Vosk. Deshalb vorher echte Aufnahmen vergleichen: 20 Alltagsanfragen auf dem Pi aufnehmen und auf CT 107 durch beide Erkenner schicken.

```bash
# Pi
sudo systemctl stop pi-ptt
python3 /opt/pi-voice-assistant/scripts/mic-check.py record ~/stt-clips
sudo systemctl start pi-ptt
scp -r ~/stt-clips root@<ct107>:/root/stt-clips
# CT 107
/opt/servitor-voice/.venv/bin/python /opt/servitor-voice/repo/server/bench-stt.py \
    --clips /root/stt-clips \
    vosk:/opt/servitor-voice/models/vosk-model-small-de-0.15 whisper:small
```

Ausgabe: Wortfehlerrate, Wartezeit nach dem Loslassen und jeder Fehler pro Erkenner.

