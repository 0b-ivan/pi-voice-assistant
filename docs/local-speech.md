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

Der Wrapper startet pro Satz einen neuen Piper-Prozess und wartet auf das gesamte
WAV. Die [Pi-Messungen](piper-resources.md) mit Version `d056897` bestätigen den
Ladeaufwand: neben Vosk 18,01 s zum Modellladen, 20,10–27,92 s für neue Prozesse,
aber nur 1,26–1,67 s für Erzeugungen mit geladenem Modell.

Der korrigierte Benchmark aus [PR #16](https://github.com/0b-ivan/pi-voice-assistant/pull/16)
verwendet denselben Text über CLI-stdin und API, speichert Teilberichte auch bei
frühen Signalen und spielt keinen Ton ab. Die ursprünglichen Reviewbefunde wurden
mit `d056897` behoben; 56 Tests bestanden unter Python 3.13 auf GitHub.

Neben Vosk lagert das System während des gesamten Vergleichs 318,74 MiB aus.
Der PTT-Dienst fällt von 198,65 auf 7,48 MiB RSS und liegt nachher mit 185,80 MiB
im Swap. Deshalb vorerst kein dauerhaft geladenes Piper aktivieren. Feste
Statusansagen als WAV-Cache sind der nächste Schritt; Cache, Streaming und
permanente TTS-Komponente sind weiterhin nicht implementiert. Die detaillierten
Werte und Grenzen stehen ausschließlich im [Messbericht](piper-resources.md).

Der gleiche `--`-Textbefund betraf den geprüften Wrapperstand `ae3c927` aus PR #14:
[Piper 1.8.0](https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/src/piper/__main__.py)
übernimmt unbekannte Argumente inklusive `--` in den Sprachtext. Die Korrektur
des Benchmarks ändert den installierten Wrapper nicht; dessen Textpfad separat
prüfen/korrigieren. Das in einem Chat genannte `speak.sh` ist kein Repo-Skript.
