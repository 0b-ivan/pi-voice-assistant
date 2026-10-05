# Speech-to-Text: OpenRouter + Vosk

Stand 05.10.2026: OpenRouter-STT und das lokale Vosk-Backend sind auf `pi-assistent` mit echten WM8960-Aufnahmen bestätigt. Vosk läuft damit auf dem Raspberry Pi Zero 2 W vollständig offline. `auto` ist implementiert, wurde aber noch nicht separat als OpenRouter→Vosk-Fallback auf der Hardware abgenommen.

## Zielarchitektur

```text
Taste / GPIO17
    ↓
WM8960 / arecord
    ↓
48 kHz Stereo WAV
    ↓
src/transcribe.py
    ├── openrouter → OpenRouter Whisper
    ├── vosk       → Vosk lokal
    └── auto       → OpenRouter → bei Fehler Vosk
    ↓
deutscher Text
    ↓
OpenRouter LLM
    ↓
deutsche TTS
```

Damit bleiben Aufnahme und Spracherkennung im `vosk`-Modus vollständig offline. Für die eigentliche KI-Antwort ist im aktuellen Projektstand weiterhin OpenRouter vorgesehen.

## Provider

`STT_PROVIDER` akzeptiert genau drei Werte:

| Wert | Verhalten |
|---|---|
| `openrouter` | Bestehender Cloud-Pfad. Das ist der kompatible Standard. |
| `vosk` | Nur lokale Vosk-Erkennung. Kein Internet und kein OpenRouter-Key erforderlich. |
| `auto` | Zuerst OpenRouter. Bei Netzwerk-, API- oder STT-Fehlern automatische lokale Vosk-Erkennung. |

Bei `auto` wird das Vosk-Modell erst beim ersten benötigten Fallback geladen und anschließend im laufenden Dienst wiederverwendet. Solange OpenRouter funktioniert, entstehen dadurch keine Vosk-Modellkosten beim Dienststart.

## Audioformat

Der bereits hardwareseitig getestete Recorder bleibt unverändert bei:

```text
PCM S16_LE
48000 Hz
2 Kanäle
```

Für Vosk wird diese Aufnahme ausschließlich innerhalb des STT-Adapters auf 16 kHz Mono heruntergemischt. Dadurch wird für die lokale STT-Einführung nicht gleichzeitig der funktionierende WM8960-/ALSA-Pfad verändert.

## Konfiguration

`/etc/pi-voice-assistant.env` enthält Provider- und STT-Konfiguration.

Beispiel für den bisherigen Cloud-Modus:

```text
STT_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-v1-...
```

Rein lokal:

```text
STT_PROVIDER=vosk
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor
```

Hybridmodus:

```text
STT_PROVIDER=auto
OPENROUTER_API_KEY=sk-or-v1-...
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor
```

Mit Vim bearbeiten:

```bash
sudo vim /etc/pi-voice-assistant.env
```

## Vosk installieren

Die Vosk-Installation ist absichtlich vom normalen PTT-/OpenRouter-Deployment getrennt:

```bash
sudo bash scripts/install-vosk.sh
```

Das Skript installiert:

- `vosk==0.3.45` isoliert nach `/opt/pi-voice-assistant/vendor`
- `vosk-model-small-de-0.15` nach `/opt/pi-voice-assistant/models/`

Es verändert keine System-Python-Pakete des Projekts. Das Modell wird nicht ins Git-Repository eingecheckt.

Danach beispielsweise:

```bash
sudo vim /etc/pi-voice-assistant.env
# STT_PROVIDER=vosk
sudo systemctl restart pi-ptt.service
```

## Standalone-Test

Eine vorhandene WM8960-WAV kann direkt getestet werden:

```bash
set -a
. /etc/pi-voice-assistant.env
set +a

/usr/bin/python3 src/transcribe.py /tmp/stt-test.wav
```

Auf stdout erscheint der erkannte Text. Auf stderr wird zusätzlich der tatsächlich verwendete Provider ausgegeben:

```text
STT_PROVIDER_USED=vosk
```

Fehler beginnen mit `STT_ERROR:` und liefern Exitstatus 1.

## Hardware-Abnahme Vosk — 05.10.2026 ✅

Eine Stereoaufnahme der beiden WM8960-Kanäle wurde lokal mit `STT_PROVIDER=vosk` transkribiert. Beide Kanäle lieferten verständlichen deutschen Text; als tatsächlich verwendeter Provider wurde jeweils Vosk gemeldet:

```text
=== /tmp/left.wav ===
oder liegt es vielleicht doch noch mikro oder daran dass es nicht in der nähe
STT_PROVIDER_USED=vosk

=== /tmp/right.wav ===
oder liegt es vielleicht doch noch mikro oder daran dass ich es nicht in der nähe
STT_PROVIDER_USED=vosk
```

Damit ist die lokale Vosk-Erkennung mit echter WM8960-Aufnahme auf dem Zero 2 W bestätigt. Latenz, RAM-Verbrauch und der separate `auto`-Fallback-Test bleiben offen.

## PTT-Integration

Nach dem Loslassen sind im Journal unter anderem diese Ereignisse zu erwarten:

```text
capture_ready
processing
transcript
ERKANNT: ...
```

Das JSON-Ereignis `transcript` enthält zusätzlich `provider=openrouter` oder `provider=vosk`. Damit ist bei `auto` sichtbar, ob ein Fallback stattgefunden hat.

Wenn sowohl OpenRouter als auch Vosk scheitern, meldet `auto` beide Ursachen in einem `stt_error`. Der PTT-Prozess bleibt aktiv und wartet auf die nächste Aufnahme.

Während der synchronen STT-Verarbeitung wird keine neue Aufnahme begonnen. Nach der Verarbeitung wird GPIO17 neu eingelesen. Ist die Taste noch gedrückt, muss sie zuerst losgelassen werden.

## systemd und Offline-Betrieb

Der Dienst benötigt `sound.target`, aber kein `network-online.target` mehr. Dadurch kann der lokale Vosk-Pfad auch ohne Netzwerk normal starten. Ein späterer OpenRouter-Aufruf behandelt fehlendes Netzwerk als normalen STT-Fehler; im `auto`-Modus löst das den Vosk-Fallback aus.

## Tests

Hardware-unabhängig:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/ptt.py src/transcribe.py
```

Die Tests prüfen unter anderem:

- bisheriges OpenRouter-Verhalten als Standard
- expliziten Vosk-Modus
- OpenRouter-Präferenz in `auto`
- Vosk-Fallback bei OpenRouter-Fehler
- kombinierten Fehler, wenn beide Backends scheitern
- 48-kHz-Stereo → 16-kHz-Mono-Konvertierung

CI benötigt weder einen OpenRouter-Key noch ein installiertes Vosk-Modell. Der reale Vosk-Test bleibt eine Integrationsprüfung auf dem Pi Zero 2 W.
