# Lokale Sprachausgabe und Performance

## Stand: Pi und Repository unterscheiden

Auf `pi-assistent` ist Piper **1.8.0** mit **`de_DE-thorsten-low`** erfolgreich getestet. Ausgabe: S16_LE, 16 kHz, Mono über `plughw:CARD=wm8960soundcard,DEV=0`; Speaker zuletzt beide Kanäle **80 % / −19 dB**, gespeichert.

Wrapper `src/speak.py` und Installer `scripts/install-piper.sh` befinden sich in **offenem [PR #14](https://github.com/0b-ivan/pi-voice-assistant/pull/14)**, geprüfter Head `ae3c927`. Sie fehlen auf `main`. Der Pi wurde laut Chat aus diesem Feature-Stand aktualisiert. SHIM E meldet danach erfolgreichen Prozessabschluss; automatische LLM-Antworten fehlen weiterhin.

## Feature-Version verwenden

Die folgende Installation gilt ausschließlich für einen Checkout von PR #14, in dem beide Skripte tatsächlich vorhanden sind. Bei bestehendem Checkout erst lokale Änderungen prüfen; keinen fremden oder älteren Checkout übergehen. Noch offener Textübergabe-Befund steht unten.

```bash
git status --short
git rev-parse --short HEAD
ls scripts/install-piper.sh src/speak.py
sudo bash scripts/install-piper.sh
sudo bash scripts/install-voice-service.sh
/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py "Hallo Ivan, ich kann lokal sprechen."
```

Piper liegt in `/opt/pi-voice-assistant/.venv`, Stimme als `.onnx` plus passende `.onnx.json` unter `/opt/pi-voice-assistant/tts/`. Wrapper startet den venv-Interpreter, erzeugt temporäres WAV und ruft danach `aplay` auf. Wrapper kann mit System-Python gestartet werden; Piper-Unterprozess muss aus dem venv kommen.

Optionale Umgebungswerte des Wrappers: `PIPER_PYTHON`, `PIPER_MODEL`, `TTS_AUDIO_DEVICE`. Unter systemd vorhandene Konfiguration ergänzen und Dienst neu starten. Modelle/venv müssen unter erlaubtem Pfad liegen (`ProtectHome=yes`); `/opt`, `src/` und `scripts/` bleiben root-verwaltet. Nur venv und TTS-Datenverzeichnis gehören im Feature-Installer `obivan`. Historisches rekursives `chown` auf dem Pi ist kein nachgewiesener korrekter aktueller Rechtezustand.

Die Warnungen `Missing phoneme from id map` und `Failed to persist telemetry device ID` waren beim protokollierten Statusaufruf nicht blockierend (Exitcode 0). Das ersetzt keine Qualitätsprüfung verschiedener Texte.

## Performance

Das Journal zeigt am 05.10.2026 CEST `status` 21:43:32 und den nächsten Piper-Prozess um 21:43:34; für den vorherigen Statusaufruf 21:43:05 → `speech_finished` 21:43:29 etwa 24 s Gesamtdauer. Dies umfasst Prozessstart, Modell-Laden, Synthese **und Wiedergabe**; eine isolierte Synthesezeit oder RSS-Messung ist daraus nicht ableitbar.

Der Wrapper startet pro Satz einen neuen Piper-Prozess und wartet auf das gesamte WAV, bevor er abspielt. Modell-Laden ist deshalb ein plausibler Teil der Verzögerung. Wie viel es ausmacht, muss gemessen werden. Vosk bleibt im PTT-Prozess geladen; Piper dauerhaft zusätzlich zu laden ist noch keine beschlossene Optimierung auf dem 512-MB-Pi.

[PR #16](https://github.com/0b-ivan/pi-voice-assistant/pull/16), Head `87ca1ba`, ergänzt einen Vergleich frischer CLI-Aufrufe mit einer einmal geladenen Stimme. **Noch keine realen Vergleichsergebnisse und zwei offene Reviewfehler:**

1. CLI erhält `--` als Teil des Texts, API nicht. Der Vergleich nutzt somit verschiedene Eingaben.
2. Signalhandler sind vor dem geschützten Report-Block aktiv; frühes SIGTERM/SIGHUP/Ctrl-C kann ohne versprochenen Teilbericht abbrechen.

Daher vor Auswertung diese Fehler beheben. Derselbe CLI-Textfehler steckt auch im Wrapper aus PR #14: `parse_known_args()` in [Piper 1.8.0](https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/src/piper/__main__.py) übernimmt unbekannte Argumente inklusive `--` in den Sprachtext. Unterstützte stdin-Übergabe ist die geeignete Korrektur; Argumenttests allein beweisen nicht die gesprochene Eingabe.

Nach Korrektur: im PTT-Dienst einmal mit Vosk transkribieren (Modell laden), Abschluss abwarten. Im Benchmark-Checkout `python3 scripts/profile-piper.py --output /tmp/pi-piper-resources.json` ausführen; währenddessen keine Tasten drücken. Vergleich erzeugt WAVs ohne Wiedergabe, startet keine permanente TTS-Komponente und ändert keine Mixerwerte. Das in einem Chat genannte `/opt/pi-voice-assistant/scripts/speak.sh` existiert in den geprüften Repo-Ständen nicht.

Auswerten: Ladezeit, warme Synthesezeit, CPU-Zeit, Audiolänge/RTF, aktueller und maximaler RSS, verfügbares System-RAM, Swap-Zähler und Temperatur/Throttling. Belegter Swap des Gesamtsystems ist nicht automatisch Piper-Verbrauch. Erst danach geladenes Modell, vorbereitete Status-WAVs oder Streaming beurteilen. Aktuell gibt es keinen dauerhaften Piper-Dienst, Audio-Cache oder Streaming-Pfad.
