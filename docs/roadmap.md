# Roadmap und Aufgaben ✅

## Phase 0 — Grundlage

- [x] Repositorystruktur und erste Dokumentation erstellen.
- [x] Bestätigten Hardwarebestand und offene Punkte festhalten.
- [x] Client/Server-Entscheidung dokumentieren.
- [x] GitHub-Repository anlegen und initiale Dokumentation hochladen.
- [ ] OS-Release und Architektur aus vorheriger Entscheidung bestätigen.
- [ ] Display, Kamera und PiSugar2-Variante identifizieren.

## Phase 1 — Hardware in Betrieb nehmen

- [ ] SD-Karte vorbereiten; SSH und WLAN prüfen.
- [ ] OS-/Kernelstand und WM8960-Treibercommit dokumentieren.
- [ ] Lautsprecher und Mikrofone testen.
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
