# Roadmap: offene Aufgaben

**Stand: 10.10.2026 · Arbeitsvorschlag zur Priorisierung.** Diese Datei listet **nur offene** Arbeiten. Implementierte Funktionen und ihre Grenzen stehen in der [README](../README.md) und [Architektur](architecture.md); frühere Änderungen in [Git-Historie](architecture.md#entscheidungen-und-historie). Ein grüner Unittest bedeutet **nicht** automatisch, dass die Funktion am echten Pi abgenommen wurde.

## P0 · Stabilität und Geräte-Abnahme

- **Neuerungen vom 09.–10.10. am Pi testen**:
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
- **Kommunikation SPX/1:** Sitzung und `ping/ack` sind vorhanden ([PR #74](https://github.com/0b-ivan/pi-voice-assistant/pull/74)); Outbox, Dead-Letter, Zustellgarantien und Server→Pi-Kanal stehen noch aus ([Architektur](architecture.md#spx1-netzwerk-und-sicherheit)).
- **Netz und Hardware:** LAN-SSH, Router-Zugriff und alle drei USB-Ports des Hubs einzeln prüfen; anschließend Gehäuse/Montage.

## P2 · Nützliche Funktionen (noch nicht umgesetzt)

- **Timer, Wecker und Erinnerungen:** Timer/Wecker lokal auf dem Pi, Erinnerungen auf dem Stick; bei Ausfall von Server/Internet verfügbar. Benötigt Priorisierung von Ansagen und Geräte-Kommandos.
- **Wiederhole und Lautstärke per Sprache** ohne LLM; für vom Server erkannte Kommandos einen bestätigten Rückkanal auf den Pi vorsehen.
- **Nachrichten je Person:** Postfach auf dem Stick; ausgeben nur an erkannte Person. Mit Stimmprofilen, Datenschutz und Prioritäten abstimmen.
- **Durchsagen vom Server:** etwa für Home Assistant/Uptime Kuma; erst nach sicherem Rückkanal. `POST /v1/announce` ist ein Vorschlag, kein vorhandener Endpoint.
- **Morgenbericht automatisch beim ersten Tastendruck** als abschaltbare Option.
- **Agonie-Taste / adaptives Feedback:** B-Langdruck bewertet eine tatsächlich erzeugte LLM-Antwort negativ, während B-Kurzdruck weiterhin sofort stoppt/zurückgeht. Eine konkrete Korrektur kann als widerrufbare Präferenz gespeichert werden; simulierte Agonie/Stimmung ist ein Darstellungseffekt. **Nur Konzept, keine Implementierung** (siehe [Konzeptskizze](#agonie-taste-und-adaptives-feedback-konzeptskizze)).
- **Charakter und Gedächtnis:** echtes Gesprächsfeedback für Servitor/Billy; verbleibende Konzeptschritte (Lore-Archiv, Logbuch/„Was habe ich verpasst?“, Gesprächserkennung, Inbox, Unterbewusstsein) separat planen.

## P3 · Optionale Erweiterungen (Entscheidung ausstehend)

- **Home Assistant** anbinden und **Proxmox-Monitoring** über einen Token mit reinen Leserechten evaluieren; Sicherheits- und Bestätigungsmodell festlegen.
- **Eigenes „Hey Servitor“** trainieren und gegen „Hey Jarvis“ vergleichen, erst nach Wake-Word-Abnahme.
- **Android als mobiles Offline-LLM-Backend ohne Termux** evaluieren: native App, lokaler API-Endpunkt, Hotspot/WLAN, Modellleistung und Energieverbrauch prüfen. **Nicht implementiert, noch keine Architekturentscheidung.**
- **Kamera/Vision:** Sensor identifizieren, Bildqualität und Datenschutz prüfen; dann erst API/Modellwahl.
- **Optimierungen:** Opus erst bei schnellerer Dekodierung auf dem Pi; satzweises LLM-/TTS-Streaming nur nach Latenzmessungen. WebSocket nur bei nachgewiesenem Mehrwert gegenüber HTTP/SPX/1.

## Agonie-Taste und adaptives Feedback (Konzeptskizze)

**Status: Idee aus [PR #82](https://github.com/0b-ivan/pi-voice-assistant/pull/82), nicht implementiert.** Erst die vorhandene B-Abbruchfunktion am SHIM abnehmen. Ein Tastendruck ändert **keine LLM-Modellgewichte**; hier geht es um gespeicherte Präferenzen, nicht um Fine-Tuning. „Agonie“ ist ein **simulierter Persona-/Displayeffekt**, kein tatsächliches Schmerzempfinden.

### Vorgeschlagenes B-Verhalten

| Situation | B kurz | B ≥ 0,8 s |
|---|---|---|
| LLM-Antwort wird vorgelesen | Ausgabe **sofort** stoppen | Sofort stoppen **und** diese Antwort einmal negativ bewerten |
| Ruhe, letzte LLM-Antwort höchstens 60 s alt | bisheriges Verhalten | Letzte LLM-Antwort einmal negativ bewerten |
| Aufnahme, STT oder LLM arbeitet noch ohne Antwort | abbrechen | abbrechen, **kein** Feedback |
| Menü, Registrierung, Wartung, Sicherheits-/Gerätebestätigung | zurück/abbrechen | **kein** Feedback |
| Alarm, Status, direkte Regelantwort | wie bisher stoppen | **kein** LLM-Feedback |

Die Abbruchaktion erfolgt **bereits beim Drücken**, nicht erst beim Loslassen. Turn-ID und zugehörige Frage/Antwort müssen zuvor eindeutig feststehen. Entprellung, `monotonic()`, gedrückt gestartete Taste und gleichzeitige Tastendrücke dürfen niemals Doppelbewertungen erzeugen. A/C/D/E bleiben unverändert.

### Geplanter MVP

1. **Feedback erfassen:** Nur wirklich generierte LLM-Antworten erhalten ein Ereignis mit Turn-ID, Zeitpunkt, Provider/Modus und gekürztem Frage-/Antwortkontext; **keine Audioaufnahme**. Negatives Feedback allein erzeugt **keine erfundene allgemeine Regel**.
2. **Grund erfragen und bestätigen:** Optional bei der nächsten passenden Interaktion „Was soll ich ändern?“. Erst eine konkret bestätigte Korrektur (z. B. „Antworte kürzer“) wird zur aktiven, überprüfbaren Präferenz. Wiederholte Fehler dürfen höchstens Vorschläge erzeugen.
3. **Gedächtnis:** Der **Pi** verwaltet eine versionierte, atomar geschriebene `proximus/feedback.json` auf dem Stick `PROXIMUS` (Vorschlag: maximal 100 Ereignisse/20 Regeln); ohne Stick nur flüchtig, **keine dauerhafte Änderung**. An CT 107 nur relevante, validierte Regeln über die bestehende Gedächtnis-/Sitzungsschnittstelle übertragen, den Kern-Hash bei Änderungen aktualisieren.
4. **Anwenden und widerrufen:** Relevante Regeln in OpenRouter-/Qwen-Kontexte einbinden, **nicht** in Notfallansagen, feste Geräteaktionen oder höherrangige Sicherheitsvorgaben. „Was hast du gelernt?“ und „Vergiss die letzte Korrektur“ müssen funktionieren; ein Widerruf muss auch den Serverkontext aktualisieren.
5. **Darstellung:** Kurz „AGONIE / KORREKTUR REGISTRIERT“ auf Display/LED, optional Glitch-Ton; flüchtiger `agony_level` (0–1), vorübergehende `besorgt`-Stimmung, dann Abklingen. **Keine** Rache, Hilfsverweigerung oder dauerhafte Stimmungsstrafe.
6. **Berechtigungen und Datenschutz:** Fremde oder nicht sicher erkannte Stimmen dürfen keine globalen Regeln bestätigen. Beim reinen Pi-Offline-Fallback ohne gesicherte Identität keine automatische Regeländerung. Keine Secrets speichern; private Inhalte nicht ohne Einwilligung an externe LLMs senden.

**Abnahme vor Umsetzung:** Short-/Long-Press und Grenzzeit, B während LLM-TTS und bis 60 s danach, kein Feedback in allen anderen Zuständen, genau ein Event, Reboot/fehlender Stick, Rücknahme und Sitzungs-Invalidierung, Offline-/Serverausfall und reale SHIM-Bedienung. Erst danach können positive Verstärkung, Vorschläge je Person und ein optionaler Export der Feedback-Daten für separates **LoRA/DPO/Fine-Tuning auf geeigneter Hardware** evaluiert werden.

**Noch zu entscheiden:** Glitch-Ton oder nur Display/LED? Bei nächster Interaktion nach Fehlergrund fragen? Geltungsbereich pro Stimmprofil oder zunächst geräteweit? Im Offline-Fall nur Ereignis speichern und Bestätigung später nachholen (Vorschlag).

**Prioritäten und Produktentscheidungen sind Vorschläge zur Abstimmung im Dokumentations-PR.** Nach Bestätigung die Phasen festlegen. Keine abgeschlossenen Funktionen mit ~~Durchstreichung~~ wieder in diese Roadmap aufnehmen.

