# Pi Voice Assistant

Sprachprojekt für **Raspberry Pi Zero 2 W**, WM8960-HAT mit zwei eingebauten Mikrofonen, zwei Lautsprechern und vorhandener PTT-Taste sowie PiSugar2.

**Stand 06.10.2026:** Taste halten → aufnehmen → loslassen → deutscher Text mit Vosk funktioniert lokal. Der vollständige Frage-Antwort-Assistent ist noch nicht implementiert.

| Bereich | Aktueller Stand |
|---|---|
| System | Raspberry Pi OS Lite 64-bit / Debian 13 Trixie, `pi-assistent`, Benutzer `obivan` |
| Audio | WM8960-Aufnahme und Wiedergabe samt Neustart bestätigt; vorhandenes Kernelmodul/Overlay, kein zusätzlicher Waveshare-Treiber |
| PTT und STT auf `main` | GPIO17, optional Button SHIM A–E/RGB, OpenRouter- und Vosk-STT implementiert; Pi läuft bewusst mit `STT_PROVIDER=vosk` |
| Lokale Sprachausgabe auf dem Pi | Piper 1.8.0 resident; `normal` nutzt `de_DE-thorsten-low`, `servitor` nutzt `de_DE-thorsten_emotional-medium` (Speaker 4) plus gestreamten FFmpeg-Live-DSP. SHIM E spricht kompakte dynamische Telemetrie; Verarbeitung blinkt Rot↔Gelb, Sprachpausen sind Türkis und Sprachsegmente Orange. |
| Performance | [Piper auf dem Pi gemessen](docs/piper-resources.md): frischer Prozess 17–22 s, resident 1,12–1,20 s. Kontrollierter Vosk/Piper-Wechsel stabilisierte sich bei 5,89 s STT / 1,26 s TTS; kombinierter Peak-RSS 254,7 MiB, zram physisch ~57 MiB, kein Writeback-I/O |
| Display | Adafruit mini PiTFT 1,3″: SPI0, ST7789-Farbtest und eigener Boot-/Statusdienst bestätigt; Live-Voice-Zustände noch offen |
| Noch offen | LLM-Anbindung und automatische Antwortwiedergabe; reale `auto`-Fallback-Abnahme; Akku/Abschaltung und Kamera |

## Einrichten und betreiben

1. [Setup](docs/setup.md): OS, funktionierendes WM8960-Audio, Dienst und Offline-STT installieren.
2. [Betrieb](docs/operation.md): starten, konfigurieren, aktualisieren und Logs prüfen.
3. [Troubleshooting](docs/troubleshooting.md): GPIO belegt, I²C fehlt, leere Transkripte oder TTS-Probleme.

Vertiefung: [Hardware und Fotos](docs/hardware.md), [Ethernet/USB/SHIM-Test](docs/hardware-bring-up.md), [PTT-Verhalten](docs/push-to-talk.md), [STT und Messwerte](docs/speech-to-text.md), [Button-Bedienung](docs/button-controls.md), [Piper-TTS einrichten](docs/text-to-speech.md), [TTS-Performance](docs/local-speech.md), [PiTFT-Display](docs/display.md), [Architektur](docs/architecture.md), [nächste Aufgaben](docs/roadmap.md), [Projekt-/PR-Prüfung](docs/project-review.md).

Die Installationsvorlage setzt weiterhin `openrouter`, weil der normale Dienstinstaller Vosk nicht mitinstalliert. Für den dokumentierten Offline-Betrieb Vosk separat installieren und **explizit `STT_PROVIDER=vosk` setzen**. Installation und Providerwahl stehen zusammen im Setup.
