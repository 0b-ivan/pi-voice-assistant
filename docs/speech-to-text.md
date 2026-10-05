# Speech-to-Text: OpenRouter + Vosk

Stand 05.10.2026: OpenRouter-STT und Vosk-STT sind auf `pi-assistent` mit echten WM8960-WAV-Dateien bestätigt. Vosk 0.3.45 läuft unter Python 3.13 auf aarch64; der integrierte PTT→Vosk-Pfad ist auf dem Raspberry Pi Zero 2 W hardwareseitig abgenommen. `pi-assistent` wird aktuell bewusst mit `STT_PROVIDER=vosk` offline betrieben. Der `auto`-Fallback ist implementiert, aber noch nicht als realer OpenRouter→Vosk-Ausfalltest abgenommen.

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
Piper TTS lokal
```

Damit bleiben Aufnahme und Spracherkennung im `vosk`-Modus vollständig offline. Auch die Sprachausgabe ist inzwischen lokal mit [Piper](text-to-speech.md) bestätigt. Für die eigentliche KI-Antwort ist im aktuellen Projektstand weiterhin OpenRouter vorgesehen.

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

## Hardware-Abnahme auf dem Pi Zero 2 W

Getestet am 05.10.2026 auf `pi-assistent` mit Raspberry Pi OS Lite 64-bit / Trixie und WM8960-HAT:

- `vosk==0.3.45` ließ sich als `manylinux2014_aarch64`-Wheel unter Python 3.13 installieren und importieren.
- `vosk-model-small-de-0.15` wurde erfolgreich geladen und wiederholt im laufenden PTT-Dienst verwendet.
- Ein 6-Sekunden-Testclip benötigte beim ersten Modell-Load 15,59 s; weitere Durchläufe im selben Prozess benötigten 6,75 s bzw. 6,78 s.
- Der laufende `pi-ptt.service` belegte nach geladenem Vosk-Modell 145728 kB RSS (rund 142 MiB).
- Zum Messzeitpunkt zeigte das Gesamtsystem 415 MiB RAM, 120 MiB verfügbar und 105 MiB belegten Swap. Der Swap-Wert beschreibt den beobachteten Systemzustand und wird nicht allein Vosk zugerechnet.
- Die Erkennungsqualität des kleinen deutschen Modells ist brauchbar, aber deutlich schwächer als OpenRouter Whisper bei freier Sprache und teilweise schwach bei kurzen Kommandos.
- Ein identischer WM8960-Clip wurde von OpenRouter nahezu vollständig erkannt; damit ist das Mikrofon kein Hauptverdächtiger für die Vosk-Fehler.
- Linker und rechter Mikrofonkanal lieferten mit Vosk sehr ähnliche Ergebnisse; der rechte Kanal war nur geringfügig besser.
- Eine hochwertige 48-kHz→16-kHz-Konvertierung mit SoX lieferte praktisch dasselbe Vosk-Ergebnis wie die interne Konvertierung. Es gibt daher aktuell keinen Hinweis, dass der einfache interne Resampler die Hauptursache der Erkennungsfehler ist.

Fazit: Vosk ist auf dem Pi Zero 2 W als vollständig lokale STT funktionsfähig. Für den aktuellen Offline-Betrieb bleibt `STT_PROVIDER=vosk` gesetzt. Die nächsten Qualitätsverbesserungen sollten beim Erkennungsmodell bzw. bei einer optionalen Command-Grammar ansetzen, nicht bei Mikrofon oder Resampling.

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

CI benötigt weder einen OpenRouter-Key noch ein installiertes Vosk-Modell. Die reale Vosk-Integration ist auf dem Pi Zero 2 W bestätigt; offen bleibt insbesondere die Hardware-Abnahme des `auto`-Fallbacks.
