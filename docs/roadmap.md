# Roadmap: offene Aufgaben

**Stand: 10.10.2026 · Arbeitsvorschlag zur Priorisierung.** Diese Datei listet **nur offene** Arbeiten. Implementierte Funktionen und ihre Grenzen stehen in der [README](../README.md) und [Architektur](architecture.md); frühere Änderungen in [history/](history/). Ein grüner Unittest bedeutet **nicht** automatisch, dass die Funktion am echten Pi abgenommen wurde.

## P0 · Stabilität und Geräte-Abnahme

- **Neuerungen vom 09.–10.10. am Pi testen** ([Sitzungsprotokoll](history/session-2026-10-09.md)):
  - Sprachbefehle „WLAN aus/an“, „Geh schlafen“, „Starte dich neu“, „Fahr dich herunter“ und „Bestätigt“ mit echter Stimme; `rfkill`, polkit und Wartungsworker. **Herunterfahren zuletzt testen.**
  - Stimmerkennung bei kurzen Befehlen: `speaker`-Werte auf CT 107 prüfen, Schwelle nur auf Basis echter Messwerte anpassen.
  - Quittungston und Aussprache von „Omnissiah“ anhören; Morgenbericht mit **echter** Nextcloud-Verbindung.
  - Wetterfrage offline, `weather.json` auf dem Gedächtnis-Stick, animierte Fünf-Tage-Ansicht auf dem PiTFT (Lesbarkeit/Bildrate).
  - PiTFT-Menü, „Display aus“, Status-LED, C/D-Wiederholung und E im/außerhalb des Menüs prüfen.
- **Wake Word „Hey Jarvis“ abnehmen** (nach [#77](https://github.com/0b-ivan/pi-voice-assistant/pull/77)): echte Sprache, Trefferquote, Fehlaktivierungen über einen Tag, Pausenerkennung und Rückkehr nach Wiedergabe/Abbruch. Das Wake Word ist **implementiert**, nicht „später“.
- **Fehlerwege reproduzierbar prüfen:** CT 107 nicht erreichbar; Internet weg bei erreichbarem CT 107; beide weg; Wiederverbindung; OpenRouter-Timeout/fehlende Credits. Regelbasierte Pi-Antworten müssen ohne Netz weitergehen; ein freies Offline-LLM existiert **nicht** auf dem Pi.
- **PTT-Grenzfälle und Betrieb:** Entprellung, Aufnahme <100 ms, Boot mit gedrückter Taste, Abbruch während STT/Wiedergabe, Reboot mit SHIM/Display. PiSugar-3-Kapazität, Laufzeit und kontrollierte Abschaltung messen.
- **Selbsttest/Logs** ([PR #79](https://github.com/0b-ivan/pi-voice-assistant/pull/79)): zum Stand dieser Roadmap **offener PR**, nicht Teil von `main`. Nach Review und Merge den Logsync auf dem Stick, journal-RAM-Konfiguration, Reparaturgrenzen und echten Pi-/CT-Betrieb abnehmen.

## P1 · Wartbarkeit und Qualität

- **STT-Vergleich mit echter Stimme:** ungefähr zehn WM8960-Aufnahmen für Vosk small vs. Whisper small auf CT 107; Experimente auf `feat/servitor-whisper-stt`; Audiodateien danach löschen. Messungen mit synthetischer Sprache sind kein Ersatz.
- **Zielgerichtetes Refactoring:** `src/ptt.py` weiter aufteilen (Konfiguration bereits `src/ptt_config.py`), ohne die getesteten Zustandsübergänge zu verändern. Tests für Geräte- und Server-Fallback ergänzen.
- **Dienstrechte:** dedizierten Pi-Dienstbenutzer statt `obivan` prüfen; Installer, Secret-Gruppen, systemd-Units und Schreibpfade gemeinsam migrieren.
- **CI und Doku:** Links/Anker, Konfigurationsbeispiele, Installer-Syntax und dokumentierte Defaults automatisiert prüfen; zeitabhängigen Testflaky-Fall reproduzieren.
- **Kommunikation SPX/1:** Sitzung und `ping/ack` sind vorhanden ([PR #74](https://github.com/0b-ivan/pi-voice-assistant/pull/74)); Outbox, Dead-Letter, Zustellgarantien und Server→Pi-Kanal stehen noch aus ([Konzept](concepts/persoenlichkeit-und-protokoll.md#9-umsetzung-in-schritten)).
- **Netz und Hardware:** LAN-SSH, Router-Zugriff und alle drei USB-Ports des Hubs einzeln prüfen; anschließend Gehäuse/Montage.

## P2 · Nützliche Funktionen (noch nicht umgesetzt)

- **Timer, Wecker und Erinnerungen:** Timer/Wecker lokal auf dem Pi, Erinnerungen auf dem Stick; bei Ausfall von Server/Internet verfügbar. Benötigt Priorisierung von Ansagen und Geräte-Kommandos.
- **Wiederhole und Lautstärke per Sprache** ohne LLM; für vom Server erkannte Kommandos einen bestätigten Rückkanal auf den Pi vorsehen.
- **Nachrichten je Person:** Postfach auf dem Stick; ausgeben nur an erkannte Person. Mit Stimmprofilen, Datenschutz und Prioritäten abstimmen.
- **Durchsagen vom Server:** etwa für Home Assistant/Uptime Kuma; erst nach sicherem Rückkanal. `POST /v1/announce` ist ein Vorschlag, kein vorhandener Endpoint.
- **Morgenbericht automatisch beim ersten Tastendruck** als abschaltbare Option.
- **Charakter und Gedächtnis:** echtes Gesprächsfeedback für Servitor/Billy; verbleibende Konzeptschritte (Lore-Archiv, Logbuch/„Was habe ich verpasst?“, Gesprächserkennung, Inbox, Unterbewusstsein) separat planen.

## P3 · Optionale Erweiterungen (Entscheidung ausstehend)

- **Home Assistant** anbinden und **Proxmox-Monitoring** über einen Token mit reinen Leserechten evaluieren; Sicherheits- und Bestätigungsmodell festlegen.
- **Eigenes „Hey Servitor“** trainieren und gegen „Hey Jarvis“ vergleichen, erst nach Wake-Word-Abnahme.
- **Android als mobiles Offline-LLM-Backend ohne Termux** evaluieren: native App, lokaler API-Endpunkt, Hotspot/WLAN, Modellleistung und Energieverbrauch prüfen. **Nicht implementiert, noch keine Architekturentscheidung.**
- **Kamera/Vision:** Sensor identifizieren, Bildqualität und Datenschutz prüfen; dann erst API/Modellwahl.
- **Optimierungen:** Opus erst bei schnellerer Dekodierung auf dem Pi; satzweises LLM-/TTS-Streaming nur nach Latenzmessungen. WebSocket nur bei nachgewiesenem Mehrwert gegenüber HTTP/SPX/1.

**Prioritäten und Produktentscheidungen sind Vorschläge zur Abstimmung im Dokumentations-PR.** Nach Bestätigung die Phasen festlegen. Keine abgeschlossenen Funktionen mit ~~Durchstreichung~~ wieder in diese Roadmap aufnehmen.
