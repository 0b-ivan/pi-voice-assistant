# Architektur 🧠

## Verantwortlichkeiten

| Komponente | Aufgabe |
|---|---|
| Pi-Client | GPIO17 lesen, Aufnahme begrenzen/validieren, STT auswählen, später LLM/TTS orchestrieren und Audio abspielen |
| Vosk STT | Optionale lokale deutsche Spracherkennung ohne Internet |
| OpenRouter STT | Online-STT mit Whisper; im Hybridmodus primärer Provider |
| OpenRouter LLM | Nächster Schritt: erkannten Text beantworten |
| TTS | Danach: deutschen Antworttext in Wiedergabeaudio umwandeln; Provider noch offen |
| PiSugar2-Integration | Später Akkustatus und kontrolliertes Herunterfahren |
| Kamera/Display | Spätere optionale Erweiterung |

Der Pi Zero 2 W übernimmt Geräte-I/O und kann STT lokal mit Vosk ausführen. Auf `pi-assistent` ist derzeit bewusst `STT_PROVIDER=vosk` gesetzt, sodass Aufnahme und Spracherkennung vollständig offline laufen. Die eigentliche LLM-Antwort bleibt im bisherigen Architekturplan zunächst extern bei OpenRouter; sie ist noch nicht angebunden.

## MVP-Ablauf

1. Client wartet auf Tastendruck.
2. Gedrückt halten startet die Aufnahme; Loslassen stoppt sie. Zusätzlich gilt ein konfigurierbares Zeitlimit (PTT-Standard 30 Sekunden).
3. Der Recorder validiert das WAV und veröffentlicht atomar `capture.wav`.
4. Der STT-Adapter verwendet `STT_PROVIDER=openrouter|vosk|auto`.
5. `auto` versucht zuerst OpenRouter und verwendet bei STT-/Netzfehler Vosk lokal.
6. Als nächster Baustein wird der erkannte Text an ein OpenRouter-LLM gesendet.
7. Danach synthetisiert TTS eine deutsche Antwort.
8. Client spielt die Antwort ab und kehrt in den Wartezustand zurück.

Während der synchronen Verarbeitung startet keine neue Aufnahme. Nach STT wird GPIO17 resynchronisiert; eine während der Verarbeitung gehaltene Taste muss zuerst losgelassen werden. Wake Word, Unterbrechen der Sprachausgabe und Echounterdrückung gehören nicht zum ersten MVP.

## Implementierter Vertrag: PTT → STT

Der lokale [Push-to-Talk-Dienst](push-to-talk.md) liest GPIO17 mit libgpiod v2 und erzeugt geprüftes Stereo-WAV (48 kHz, S16_LE) unter `/run/pi-ptt`. Nach `capture_ready` ruft er [`src/transcribe.py`](speech-to-text.md) synchron auf.

Der STT-Adapter unterstützt drei Modi. `openrouter` entspricht dem bisherigen Verhalten. `vosk` verarbeitet das WAV lokal; dafür wird das bestätigte 48-kHz-Stereoformat intern in 16-kHz-Mono überführt. `auto` bevorzugt OpenRouter und fällt bei einem fehlgeschlagenen Online-STT-Aufruf auf Vosk zurück.

Erfolgreiche Verarbeitung erzeugt `processing` und `transcript`; das Transcript-Ereignis nennt den tatsächlich verwendeten Provider. Erwartete Provider-, Netzwerk- und API-Fehler werden als `stt_error` gemeldet und beenden PTT nicht.

## Betrieb und Fehler

- Begrenzte Aufnahme und HTTP-Timeout; keine unbegrenzten Audio-Uploads.
- Der lokale Vosk-Pfad benötigt kein Netzwerk.
- Der Dienst wartet beim Start nicht auf `network-online.target`.
- Das Vosk-Modell wird lazy geladen und innerhalb des Prozesses wiederverwendet.
- Nach STT-Fehlern Rückkehr in den Wartezustand, keine Endlosschleife.
- Zustände: bereit, Aufnahme, Verarbeitung; Wiedergabe und LLM-Antwort folgen.
- Zugangsdaten ausschließlich außerhalb von Git in `/etc/pi-voice-assistant.env`.
- Audio bleibt im flüchtigen Runtime-Verzeichnis; keine dauerhafte Speicherung als Standard.
- Der OpenRouter-Schlüssel wird nicht im Journal ausgegeben.
- Hardware-unabhängige Tests mocken externe STT-Aufrufe und benötigen keinen Schlüssel bzw. kein Vosk-Modell.

## Erfolgskriterien

Bereits bestätigt: GPIO17 startet zuverlässig eine Aufnahme; WM8960-WAV ist verständlich; OpenRouter-STT und Vosk-STT transkribieren echte Aufnahmen. Der integrierte PTT→Vosk-Pfad läuft auf dem Pi Zero 2 W. Im Hardwaretest benötigte Vosk für einen 6-Sekunden-Clip nach geladenem Modell rund 6,75–6,78 s; der laufende Dienst belegte rund 142 MiB RSS. Ein Vergleich mit beiden Mikrofonkanälen und hochwertigem SoX-Resampling zeigte keinen wesentlichen Qualitätsgewinn, sodass das kleine deutsche Vosk-Modell derzeit als Hauptlimit der Erkennungsqualität gilt.

Noch offen: den `auto`-Fallback als realen OpenRouter→Vosk-Ausfalltest abnehmen, Offline-Erkennungsqualität für kurze Kommandos verbessern und danach LLM/TTS sowie die durchgehende Sprachinteraktion testen.
