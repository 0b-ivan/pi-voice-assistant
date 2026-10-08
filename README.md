# Pi Voice Assistant

Sprachprojekt für **Raspberry Pi Zero 2 W**, WM8960-HAT mit zwei eingebauten Mikrofonen, zwei Lautsprechern und vorhandener PTT-Taste sowie PiSugar 3 (Akku).

**Stand 06.10.2026:** Der Sprachloop ist integriert: Taste halten → lokale Vosk-Erkennung → nicht-streamender OpenRouter-LLM-Aufruf → lokale Piper/Thorsten-Servitor-Ausgabe. Die Hardware-Abnahme dieses vollständigen Loops auf dem Pi steht noch aus.

| Bereich | Aktueller Stand |
|---|---|
| System | Raspberry Pi OS Lite 64-bit / Debian 13 Trixie, `pi-assistent`, Benutzer `obivan` |
| Audio | WM8960-Aufnahme und Wiedergabe samt Neustart bestätigt; vorhandenes Kernelmodul/Overlay, kein zusätzlicher Waveshare-Treiber |
| PTT und STT | GPIO17, optional Button SHIM A–E/RGB; STT ist ausschließlich lokale Live-Vosk-Erkennung mit `STT_PROVIDER=vosk`. OpenRouter erhält kein Mikrofon-Audio. |
| Lokale Sprachausgabe auf dem Pi | Piper 1.8.0 resident; `normal` nutzt `de_DE-thorsten-low`, `servitor` nutzt `de_DE-thorsten_emotional-medium` (Speaker 4) plus gestreamten FFmpeg-Live-DSP. SHIM E spricht kompakte dynamische Telemetrie; Verarbeitung blinkt Rot↔Gelb, Sprachpausen sind Türkis und Sprachsegmente Orange. |
| Performance | [Piper auf dem Pi gemessen](docs/piper-resources.md): frischer Prozess 17–22 s, resident 1,12–1,20 s. Kontrollierter Vosk/Piper-Wechsel stabilisierte sich bei 5,89 s STT / 1,26 s TTS; kombinierter Peak-RSS 254,7 MiB, zram physisch ~57 MiB, kein Writeback-I/O |
| LLM | `src/llm.py` kapselt OpenRouter vollständig getrennt von GPIO/Audio; API-Key nur aus Environment, nicht-streamend, mit Timeout und Fehlerbehandlung. |
| Display | Adafruit mini PiTFT 1,3″: [animierte Arbeitsschritte](docs/display-work-steps.md#interface-preview) für Aufnahme, Erkennung, Denken, Synthese, Rendering und Ausgabe. |
| Noch offen | Hardware-Abnahme des vollständigen Antwortloops und reale Latenzmessung; Akku/Abschaltung und Kamera. |

## Einrichten und betreiben

1. [Setup](docs/setup.md): OS, funktionierendes WM8960-Audio, Dienst und Offline-STT installieren.
2. [Betrieb](docs/operation.md): starten, konfigurieren, aktualisieren und Logs prüfen.
3. [Troubleshooting](docs/troubleshooting.md): GPIO belegt, I²C fehlt, leere Transkripte oder TTS-Probleme.

Vertiefung: [Hardware und Fotos](docs/hardware.md), [Ethernet/USB/SHIM-Test](docs/hardware-bring-up.md), [PTT-Verhalten](docs/push-to-talk.md), [STT und Messwerte](docs/speech-to-text.md), [Button-Bedienung](docs/button-controls.md), [Piper-TTS einrichten](docs/text-to-speech.md), [TTS-Performance](docs/local-speech.md), [PiTFT-Display](docs/display.md), [Architektur](docs/architecture.md), [nächste Aufgaben](docs/roadmap.md), [Projekt-/PR-Prüfung](docs/project-review.md).

Die Installationsvorlage setzt jetzt bewusst **`STT_PROVIDER=vosk`**. Vosk und Piper werden weiterhin separat installiert. OpenRouter wird im normalen Assistentenpfad ausschließlich für das LLM verwendet; der echte API-Key gehört nur nach `/etc/pi-voice-assistant.env`.
