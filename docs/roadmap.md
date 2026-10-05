# Roadmap und Aufgaben ✅

## Phase 0 — Grundlage

- [x] Repositorystruktur und erste Dokumentation erstellen.
- [x] Bestätigten Hardwarebestand und offene Punkte festhalten.
- [x] Client/Server-Entscheidung dokumentieren.
- [x] GitHub-Repository anlegen und initiale Dokumentation hochladen.
- [x] OS-Entscheidung dokumentieren: Raspberry Pi OS Lite 64-bit / Trixie.
- [x] Display anhand des Fotos identifizieren: Adafruit mini PiTFT 1,3″, 240 × 240.
- [ ] Kamerasensor und PiSugar2-Revision feststellen.

## Phase 1 — Hardware in Betrieb nehmen

- [x] Trixie 64-bit mit Imager auf SD-Karte schreiben; [Screenshots dokumentiert](setup.md).
- [x] Ersten Boot, Netzwerkerreichbarkeit und SSH-Anmeldung bestätigen.
- [x] Systemupdate durchführen; Git, ALSA- und I²C-Werkzeuge bereitstellen.
- [x] SSH und Netzwerkerreichbarkeit nach dem angestoßenen Neustart bestätigen.
- [ ] WLAN-Schnittstelle und Verbindung explizit erfassen.
- [x] Laufenden Kernel nach Neustart und ALSA-Bestandsaufnahme dokumentieren.
- [x] Vorhandenes WM8960-Kernelmodul und Overlay aktivieren; Aufnahme-/Wiedergabegerät nach Neustart bestätigt (kein zusätzlicher Treibercommit).
- [x] Lautsprecherwiedergabe testen und passende Lautstärke bestätigen; ALSA-Zustand speichern.
- [ ] Mikrofone testen und gespeicherte Audioeinstellungen nach Neustart prüfen.
- [ ] Tasten-GPIO, Polarität und Entprellung prüfen.
- [ ] Akkuversorgung und sauberes Herunterfahren testen.

Abnahme: Aufnahme und Wiedergabe funktionieren nach Neustart.

## Phase 2 — Sprach-MVP

- [ ] Homelab-Ziel und STT/LLM/TTS-Komponenten auswählen.
- [ ] API-Vertrag, Authentifizierung und Audioformat festlegen.
- [ ] Pi-Client mit Taste, Zeitlimit und Zustandssteuerung implementieren.
- [ ] Timeout- und Fehlerbehandlung implementieren.
- [ ] Durchgehende deutsche Sprachinteraktion testen; Latenzen messen.

Abnahme: Taste → Frage → hörbare Antwort; Wiederherstellung nach Netzwerkausfall.

## Phase 3 — Zuverlässiger Betrieb

- [ ] systemd-Dienst und Konfiguration erstellen.
- [ ] Speicherverbrauch, Startzeit, Akkulaufzeit und Temperatur messen.
- [ ] Wiederholte Interaktionen und Dienstneustart testen.
- [ ] Installationsanleitung mit tatsächlich getesteten Versionen vervollständigen.

## Phase 4 — Erweiterungen

- [ ] Display mit Zustands- und Akkuanzeige.
- [ ] Kamera und explizit ausgelöste Bildanfragen.
- [ ] Wake Word und Unterbrechen der Wiedergabe evaluieren.
- [ ] Gehäuse und mobile Bedienung verbessern.

Priorität: Erst Audio auf echter Hardware, dann ein vollständiger Sprachdurchlauf.
