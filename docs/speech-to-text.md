# Speech-to-Text mit OpenRouter

Stand 05.10.2026: Der separate STT-Adapter wurde auf `pi-assistent` mit einer WM8960-WAV-Datei erfolgreich getestet. Der Testsatz wurde als „Hallo, das ist ein Test.“ transkribiert. Die Verdrahtung mit dem PTT-Dienst liegt in diesem Feature-Stand vor und muss nach Deployment noch auf Hardware abgenommen werden.

## Datenfluss

```text
GPIO17 gedrückt
    ↓
WM8960 / arecord
    ↓
/run/pi-ptt/capture.wav
    ↓
src/transcribe.py
    ↓
OpenRouter /api/v1/audio/transcriptions
    ↓
deutscher Text
```

Auf dem Pi läuft kein lokales Whisper-Modell. `src/transcribe.py` verwendet ausschließlich die Python-Standardbibliothek und sendet das begrenzte WAV als Base64 an OpenRouter. Standardmodell ist `openai/whisper-large-v3-turbo`, Sprache `de`.

## Konfiguration

Der echte Schlüssel bleibt außerhalb von Git:

```bash
if [ ! -e /etc/pi-voice-assistant.env ]; then
  sudo install -o root -g obivan -m 0640 \
    config/openrouter.env.example /etc/pi-voice-assistant.env
fi
sudo chown root:obivan /etc/pi-voice-assistant.env
sudo chmod 0640 /etc/pi-voice-assistant.env
sudo vim /etc/pi-voice-assistant.env
```

Mindestens setzen:

```text
OPENROUTER_API_KEY=sk-or-v1-...
```

Optionale Werte:

```text
OPENROUTER_STT_MODEL=openai/whisper-large-v3-turbo
OPENROUTER_STT_LANGUAGE=de
OPENROUTER_STT_TIMEOUT_SECONDS=30
```

Die systemd-Unit lädt `/etc/pi-ptt.env` und `/etc/pi-voice-assistant.env`. Die Secret-Datei gehört `root:obivan` und hat Modus `0640`: root kann sie verwalten, der Dienstbenutzer `obivan` kann sie für manuelle Tests lesen, andere Benutzer nicht. Schlüssel, WAV-Dateien und Transkripte werden nicht ins Repository geschrieben.

## Standalone-Test

Vor der PTT-Integration kann eine vorhandene WAV-Datei direkt geprüft werden:

```bash
set -a
. /etc/pi-voice-assistant.env
set +a
/usr/bin/python3 src/transcribe.py /tmp/stt-test.wav
```

Erwartet wird nur der erkannte Text. Fehler beginnen mit `STT_ERROR:` und liefern Exitstatus 1.

## Installation des integrierten Dienstes

Im Checkout:

```bash
sudo bash scripts/install-voice-service.sh
sudo vim /etc/pi-voice-assistant.env
sudo systemctl enable --now pi-ptt.service
journalctl -u pi-ptt.service -f
```

Das Installationsskript überschreibt vorhandene `/etc/pi-ptt.env` und `/etc/pi-voice-assistant.env` nicht.

Nach dem Loslassen sind diese Ereignisse zu erwarten:

```text
capture_ready
processing
transcript
ERKANNT: ...
```

Bei Netz-/API-Fehlern erscheint `stt_error`; der PTT-Prozess bleibt aktiv und kann beim nächsten Tastendruck erneut verwendet werden.

Während der synchronen STT-Anfrage wird keine neue Aufnahme begonnen. Nach der Verarbeitung wird der aktuelle GPIO-Zustand neu eingelesen. Ist die Taste noch gedrückt, muss sie erst losgelassen werden, bevor eine neue Aufnahme starten kann.

## Tests

Hardware-unabhängig:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/ptt.py src/transcribe.py
```

Die STT-Tests mocken den HTTP-Aufruf; CI benötigt weder Internetzugang noch einen OpenRouter-Schlüssel. Der reale API-Test bleibt eine Hardware-/Integrationsprüfung auf dem Pi.
