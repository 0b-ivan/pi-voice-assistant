# Pi Voice Assistant 🎙️

Ein AI-Sprachassistent auf einem Raspberry Pi Zero 2 W mit WM8960 Audio-HAT und PiSugar2. Der Pi dient als mobiler Audio-Client; Sprachverarbeitung läuft auf einem stärkeren Gerät im Homelab.

**Stand: 05.10.2026 — Planung und Dokumentation. Es ist noch keine Assistenten-Software implementiert oder auf der Hardware getestet.**

## Erstes Ziel

Taste drücken → Sprache aufnehmen → im Homelab transkribieren und beantworten → Antwort über die beiden Lautsprecher ausgeben. Zunächst Halbduplex: Aufnahme und Wiedergabe laufen nacheinander.

## Dokumentation

- [Hardware und offene Prüfungen](docs/hardware.md)
- [Architektur und MVP-Verhalten](docs/architecture.md)
- [Betriebssystem und Inbetriebnahme](docs/setup.md)
- [Roadmap und Aufgaben](docs/roadmap.md)
- [Entscheidung: Pi als Client](docs/decisions/0001-client-server.md)

## Repository

`docs/` enthält Planung und Anleitungen. `config/` enthält Konfigurationsbeispiele. `scripts/` enthält eine lesende Bestandsaufnahme. Quellcode und Deployment-Dateien folgen mit der Implementierung.

```bash
bash scripts/inspect-pi.sh
```

Das Skript auf dem Pi ausführen. Fehlende Diagnoseprogramme werden übersprungen. Es verändert keine Einstellungen.

## Arbeitsweise

`main` enthält nachvollziehbare Projektstände. Änderungen erfolgen über kurze Feature-Branches und Pull Requests. Treiberänderungen werden erst nach Hardwaretests als funktionierend dokumentiert. Keine Zugangsdaten, Sprachaufnahmen oder Kamerabilder committen. Eine Lizenz ist noch nicht festgelegt.
