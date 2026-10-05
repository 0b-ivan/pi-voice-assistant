# Architektur 🧠

## Verantwortlichkeiten

| Komponente | Aufgabe |
|---|---|
| Pi-Client | GPIO17 lesen, Aufnahme begrenzen/validieren, STT anstoßen, später LLM/TTS orchestrieren und Audio abspielen |
| OpenRouter STT | Begrenzte WAV-Aufnahme in deutschen Text transkribieren |
| OpenRouter LLM | Nächster Schritt: erkannten Text beantworten |
| TTS | Danach: deutschen Antworttext in Wiedergabeaudio umwandeln; Provider noch offen |
| PiSugar2-Integration | Später Akkustatus und kontrolliertes Herunterfahren |
| Kamera/Display | Spätere optionale Erweiterung |

Auf dem Pi Zero 2 W findet für das MVP keine lokale Modellinferenz statt. Der Client bleibt dünn; Audioaufnahme und Geräte-I/O laufen lokal, rechenintensive Verarbeitung extern.

## MVP-Ablauf

1. Client wartet auf Tastendruck.
2. Gedrückt halten startet die Aufnahme; Loslassen stoppt sie. Zusätzlich gilt ein konfigurierbares Zeitlimit (PTT-Standard 30 Sekunden).
3. Der Recorder validiert das WAV und veröffentlicht atomar `capture.wav`.
4. Der STT-Adapter sendet die Aufnahme an OpenRouter und liefert deutschen Text.
5. Als nächster Baustein wird der Text an ein OpenRouter-LLM gesendet.
6. Danach synthetisiert TTS eine deutsche Antwort.
7. Client spielt die Antwort ab und kehrt in den Wartezustand zurück.

Während der synchronen Verarbeitung startet keine neue Aufnahme. Nach STT wird GPIO17 resynchronisiert; eine während der Verarbeitung gehaltene Taste muss zuerst losgelassen werden. Wake Word, Unterbrechen der Sprachausgabe und Echounterdrückung gehören nicht zum ersten MVP.

## Implementierter Vertrag: PTT → STT

Der lokale [Push-to-Talk-Dienst](push-to-talk.md) liest GPIO17 mit libgpiod v2 und erzeugt geprüftes Stereo-WAV (48 kHz, S16_LE) unter `/run/pi-ptt`. Nach `capture_ready` ruft er [`src/transcribe.py`](speech-to-text.md) synchron auf.

Der STT-Adapter benötigt `OPENROUTER_API_KEY`, verwendet standardmäßig `openai/whisper-large-v3-turbo`, Sprachhinweis `de` und einen 30-Sekunden-HTTP-Timeout. Er verwendet keine zusätzliche Python-Abhängigkeit. Erfolgreiche Verarbeitung erzeugt die Ereignisse `processing` und `transcript`; erwartete Netzwerk-/API-Fehler werden als `stt_error` gemeldet und beenden PTT nicht.

## Betrieb und Fehler

- Begrenzte Aufnahme und HTTP-Timeout; keine unbegrenzten Audio-Uploads.
- Nach STT-Fehlern Rückkehr in den Wartezustand, keine Endlosschleife.
- Zustände: bereit, Aufnahme, Verarbeitung; Wiedergabe und LLM-Antwort folgen.
- Zugangsdaten ausschließlich außerhalb von Git in `/etc/pi-voice-assistant.env`.
- Audio bleibt im flüchtigen Runtime-Verzeichnis; keine dauerhafte Speicherung als Standard.
- Der OpenRouter-Schlüssel wird nicht im Journal ausgegeben.
- Hardware-unabhängige Tests mocken den HTTP-Aufruf und benötigen keinen Schlüssel.

## Erfolgskriterien

Bereits bestätigt: GPIO17 startet zuverlässig eine Aufnahme; WM8960-WAV ist verständlich; ein deutscher Test wurde über OpenRouter-STT korrekt transkribiert.

Noch offen für den vollständigen MVP: integrierten PTT→STT-Ablauf auf Hardware abnehmen, LLM-Antwort anbinden, deutsche TTS anbinden, hörbare Antwort ausgeben und Wiederherstellung nach Netzwerkausfall prüfen. Latenz und Speicherverbrauch werden anschließend gemessen.
