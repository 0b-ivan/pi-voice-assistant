# Pi Voice Assistant 🎙️

Ein AI-Sprachassistent auf einem Raspberry Pi Zero 2 W mit WM8960 Audio-HAT und PiSugar2. Der Pi dient als mobiler Audio-Client und kann deutsche Sprache mit Vosk lokal erkennen; die eigentliche KI-Antwort erfolgt zunächst über OpenRouter.

**Stand: 05.10.2026 — Raspberry Pi OS Lite 64-bit / Trixie läuft stabil; WM8960-Aufnahme und -Wiedergabe sind bestätigt. Push-to-Talk über GPIO17 funktioniert. OpenRouter-STT und Vosk wurden mit echten WM8960-Aufnahmen erfolgreich getestet; `STT_PROVIDER=openrouter|vosk|auto` ist implementiert. Für lokale deutsche Sprachausgabe ist Piper 1.8.0 mit `de_DE-thorsten-low` auf dem Pi Zero 2 W erfolgreich abgenommen. Die aktuelle TTS-Wiedergabe läuft als 16-kHz-Mono über das WM8960; Speaker ist auf 80 % / −19 dB gespeichert. Der nächste große Schritt ist die OpenRouter-LLM-Anbindung und anschließend der vollständige PTT → STT → LLM → TTS-Loop.**

**Betriebssystem:** Raspberry Pi OS Lite 64-bit (Trixie), Headless/SSH; Hostname `pi-assistent`.

## Erstes Ziel

Taste halten → Sprache aufnehmen → beim Loslassen transkribieren → über OpenRouter beantworten → deutsche Antwort über die beiden Lautsprecher ausgeben. Zunächst Halbduplex: Aufnahme, Verarbeitung und Wiedergabe laufen nacheinander.

## Dokumentation

- [Hardware und offene Prüfungen](docs/hardware.md)
- [Ethernet/USB und Button SHIM in Betrieb nehmen](docs/hardware-bring-up.md)
- [Architektur und MVP-Verhalten](docs/architecture.md)
- [Betriebssystem und Inbetriebnahme](docs/setup.md)
- [Roadmap und Aufgaben](docs/roadmap.md)
- [Push-to-Talk: Installation, Schnittstelle und Abnahme](docs/push-to-talk.md)
- [Speech-to-Text mit OpenRouter und Vosk](docs/speech-to-text.md)
- [Text-to-Speech mit Piper](docs/text-to-speech.md)
- [Entscheidung: Pi als Client](docs/decisions/0001-client-server.md)
- [Entscheidung: Raspberry Pi OS Lite 64-bit / Trixie](docs/decisions/0002-operating-system.md)
- [Entscheidung: Hybrides STT mit OpenRouter und Vosk](docs/decisions/0003-hybrid-stt.md)

## Repository

`docs/` enthält Planung und Anleitungen. `config/` enthält Konfigurationsbeispiele. `src/ptt.py` enthält den lokalen PTT-Recorder und die STT-Übergabe; `src/transcribe.py` kapselt OpenRouter- und Vosk-STT einschließlich Hybrid-Fallback; `src/speak.py` kapselt die lokale Piper-Synthese und WM8960-Wiedergabe. `deploy/` enthält die systemd-Unit, `scripts/` Diagnose- und Installationshilfen und `tests/` Hardware-unabhängige Tests.

```bash
bash scripts/inspect-pi.sh
```

Das Bestandsaufnahmeskript auf dem Pi ausführen. Fehlende Diagnoseprogramme werden übersprungen. Es verändert keine Einstellungen.

Für den aktuellen Sprachdienst:

```bash
sudo bash scripts/install-voice-service.sh
```

Vor dem Start `/etc/pi-voice-assistant.env` mit **Vim** bearbeiten. Für `openrouter` bzw. den Online-Pfad von `auto` den echten `OPENROUTER_API_KEY` setzen. Vosk wird optional mit `sudo bash scripts/install-vosk.sh` installiert. Piper TTS und die getestete deutsche Stimme werden mit `sudo bash scripts/install-piper.sh` installiert.

## Arbeitsweise

Konfigurationsdateien bearbeiten wir mit **Vim**; Anleitungen verwenden `vim` als Editor.

`main` enthält nachvollziehbare Projektstände. Änderungen erfolgen über kurze Feature-Branches und Pull Requests. Treiberänderungen werden erst nach Hardwaretests als funktionierend dokumentiert. Keine Zugangsdaten, Sprachaufnahmen oder Kamerabilder committen. Eine Lizenz ist noch nicht festgelegt.
