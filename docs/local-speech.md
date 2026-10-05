# Lokale Sprachausgabe und Performance

## Stand: Pi und Repository unterscheiden

Auf `pi-assistent` ist Piper **1.8.0** mit **`de_DE-thorsten-low`** erfolgreich getestet. Ausgabe: S16_LE, 16 kHz, Mono über `plughw:CARD=wm8960soundcard,DEV=0`; Speaker zuletzt beide Kanäle **80 % / −19 dB**, gespeichert.

Wrapper `src/speak.py` und Installer `scripts/install-piper.sh` liegen im Repo. Der Dienstinstaller deployt den Wrapper; Piper und die Stimme werden separat installiert. SHIM E meldet auf dem Pi erfolgreichen Prozessabschluss; automatische LLM-Antworten fehlen weiterhin.

Installation, Modellpfade und Konfiguration: [Piper-TTS einrichten](text-to-speech.md).

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
