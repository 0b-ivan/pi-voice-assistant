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

## Konzept: Proximus Adaptive Learning System (PALS)

**Stand: 10.10.2026 · Status: Entwurf, nicht implementiert.** Diese Spezifikation erweitert die in PR #81 bestehende Roadmap und ersetzt die bisherige Konzeptskizze aus [PR #82](https://github.com/0b-ivan/pi-voice-assistant/pull/82) (geschlossen, nicht gemergt). Keine zusätzliche Dokumentdatei.

### 1. Ziel, Grenzen, Architekturprinzipien

Proximus soll anhand **bewerteter Antworten und bestätigter Korrekturen** sein Verhalten nachhaltig anpassen. Das „Agonie“-Erlebnis ist ein **simulierter** Persona-/Displayzustand, kein tatsächliches Schmerzempfinden. Ein Tastendruck verändert keine Modellgewichte; echtes Fine-Tuning ist ein separater, optionaler und zu prüfender Schritt.

| Stufe | Effekt | Modellgewichte verändert? | Zweiter Proxmox nötig? |
|---|---|---|---|
| Bewertung | Konkrete Antwort erhält positives/negatives Signal | Nein | Nein |
| Verhaltensgedächtnis | Bestätigte Regel verändert künftige Antworten | Nein | Nein |
| Simulierte Stimmung / Agonie | Anzeige, Ton und kurzlebige Persona-Reaktion | Nein | Nein |
| Fine-Tuning | Neuer LoRA-Adapter / neue lokale Modellversion | **Ja** | Geplant: ja |

**Nicht-Ziele:** Kein autonomes Ändern von Sicherheitsregeln, kein Training nach jedem Knopfdruck, keine Vergeltung/Hilfsverweigerung, keine selbständigen Produktiv-Rollouts, kein Umtrainieren von OpenRouter-Modellen, keine Audioaufzeichnung als Standard.

**Entwurfsgrundsätze:** Offline-first, keine zusätzliche Latenz im Sprachantwort-Pfad, sparsame personenbezogene Daten, jede Lernregel sichtbar und widerrufbar, reproduzierbare Trainingsexperimente, fail-safe Rückfall auf die bisherige Modellversion.

### 2. Komponenten und Datenfluss

```text
                   Raspberry Pi Zero 2 W
 Button SHIM B ──> Bewertungslogik ──> Gedächtnis auf USB "PROXIMUS"
       │                    │                    │
       └─ sofort abbrechen  └─ Mood / Agonie      └─ freigegebene Regeln
                               PiTFT / LED             │
                                                       ▼
                                  CT 107 / PROXMOX 1 (PRODUKTIV)
                              Vosk → Intents / Qwen3-4B / OpenRouter
                                             → Piper → Pi
                                                       │
                              nur explizit freigegebene Trainingsbeispiele
                                        (asynchron, nicht im Hot Path)
                                                       ▼
                                  PROXMOX 2 / TRAINING NODE
                                  Datenprüfung → SFT / LoRA → Tests
                                                  │
                                  optional DPO / größeres Modell
                                                  │
                                   geprüfter Kandidat + Manifest
                                                  │
                                  manuelle Promotion + Rollback
                                                  ▼
                                             CT 107
```

**Bereits vorhanden:** B-Cancel/Zurück in `src/ptt.py`, `Mood` in `src/mood.py`, USB-Gedächtnis (`src/memory.py`), SPX/1-Sitzung, CT 107 mit Qwen3-4B-Instruct-2507/Q4_K_M über `llama.cpp`, OpenRouter-Fallback. **Neu:** Turn-Feedback-Zuordnung, bestätigte Korrekturen, Regeln, Export/Training, Modell-Registry.

**Systemgrenze:** Nur der Pi schreibt persönliche Erinnerungen auf den USB-Stick. Der Trainingsserver hat keine Schreibrechte auf den Pi oder das produktive Modell. Proximus arbeitet weiter, wenn der Trainingsserver abgeschaltet ist.

### 3. Taste B als negatives Feedback

**Vorgeschlagen:** Langdruck ab **0,8 Sekunden**, gültiger Rückblick auf letzte LLM-Antwort höchstens **60 Sekunden**. Bestehende Funktion muss in allen Fällen erhalten bleiben: **B stoppt bereits beim ersten Drücken**, unabhängig davon, wie lange gehalten wird.

| Zustand beim Drücken | B kurz | B ≥ 0,8 Sekunden |
|---|---|---|
| Eigene LLM-Antwort spricht | Wiedergabe sofort stoppen | Sofort stoppen **und** die betroffene Antwort einmal negativ bewerten |
| Leerlauf, letzte LLM-Antwort ≤ 60 s alt und noch bewertbar | B wie bisher | Letzte Antwort einmal negativ bewerten |
| Audioaufnahme, STT, LLM ohne fertige Antwort | Abbrechen | Abbrechen, keine Bewertung |
| Menü, Kennenlernen, Wartung, Gerätebestätigung | Zurück / abbrechen | Zurück / abbrechen, keine Bewertung |
| Status, kritischer Alarm, deterministische Antwort | Stoppen wie bisher | Stoppen, keine LLM-Bewertung |

**Technik:** Vor `cancel()` ein unveränderliches Kandidatenobjekt mit `turn_id`, Zeitpunkt, Frage, erzeugter Antwort und Modell/Provider sichern. `monotonic()` und entprellte Press/Hold/Release-Ereignisse; höchstens **ein** Bewertungsereignis je Turn/Press. Beim Start einer neuen Anfrage veraltet der vorherige Kandidat. Boot mit gehaltenem B, zwei gleichzeitige Tasten, I²C-Ausfall, Prellen und Cancel während der Wiedergabe gezielt testen. Keine Änderungen an A/C/D/E.

**Positive Verstärkung:** später optional „Gut gemacht“ / „So ist es richtig“ für die letzte Antwort per Sprache; eigene Taste dafür nicht erforderlich.

### 4. Lernprozess: Feedback ist noch keine Regel

Ein B-Druck verrät nur: „Diese Antwort passt nicht.“ Ob Fakten, Länge, Sprache, Lore oder Tonfall falsch waren, bleibt offen. Deshalb ein expliziter Zustandsablauf:

`recorded` (erfasst) → `explained` (Grund/Verbesserung bekannt) → `approved` (bestätigt) → `active` (wirksam), alternativ `dismissed` oder `revoked`.

**Benutzerfluss:**
1. Während Proximus spricht, B halten: Audio stoppt **sofort**. Kurze Displaymeldung „AGONIE / FEEDBACK ERFASST“ ohne erneute TTS.
2. Bei nächster geeigneter Interaktion optional einmal fragen: „Was soll ich künftig ändern?“ Alternativ direkt „Korrigiere die letzte Antwort: ...“.
3. Nutzer: „Bei Uhrzeitfragen nur die Uhrzeit nennen.“
4. Proximus formuliert **eine überprüfbare, thematisch begrenzte Regel**: „Einfache Uhrzeitfragen beantworte ich ohne Einleitung.“
5. Besitzer bestätigt mit E oder „Bestätigt“. Erst jetzt wird die Regel aktiv, promptwirksam und später optional trainingstauglich.
6. „Was hast du gelernt?“ zeigt aktive Regeln; „Vergiss die letzte Korrektur“ entfernt die zuletzt aktivierte Regel samt aktualisiertem Serverkontext.

Wiederholte negative Bewertungen können später **Vorschläge** auslösen; sie dürfen keine ungeprüften globalen Direktiven erzeugen. Präferenzen brauchen Scope/Quelle/Zeit, z. B. nur für Uhrzeitfragen, nur für bestimmte Person/Persona. Keine Regel darf Notfallausgaben, sichere Gerätebestätigungen oder Faktentreue aushebeln. Nicht eindeutig autorisierte Stimmen erzeugen höchstens eine **pending**-Bewertung, keine neue globale Direktive. Kurze Stimmproben reichen nicht allein als sichere Freigabe.

### 5. Speicher, Ereignismodell und Datenschutz

**Pi = Source of Truth:** Kompaktes, versioniertes `proximus/feedback.json` auf dem Stick `PROXIMUS`; atomar geschrieben (wie das bestehende Gedächtnis), restriktive Dateirechte, maximale Größe und Schema-Migration. Startwerte: bis **200 Feedback-Ereignisse**, höchstens **20 aktive Regeln**. Bei fehlendem Stick nur kleiner flüchtiger Puffer, ausdrücklich **kein dauerhaftes Lernen**.

**Beispiel (konzeptionell, kein bestehendes Protokoll):**

```json
{
  "schema": 1,
  "id": "fb-0001",
  "turn_id": "turn-0092",
  "at": "2026-10-10T14:00:00+02:00",
  "actor": {"profile_id": "owner", "verified": true},
  "source": "button_b_long",
  "rating": "negative",
  "provider": "local/qwen3-4b",
  "prompt": "Wie spät ist es?",
  "rejected": "Gemäß den heiligen Chronometern...",
  "reason": "Uhrzeitantwort war unnötig lang",
  "chosen": "Es ist 14 Uhr.",
  "scope": "time_query",
  "state": "approved",
  "allow_training": false
}
```

Beim Tastendruck sind `reason` und `chosen` zunächst **nicht vorhanden**. Eine bevorzugte Antwort muss konkret vorgeschlagen und bestätigt werden; nicht still per Modell erfinden.

**Speicherprinzip:** Kein Roh-Audio, keine Passphrasen, Keys, vollständigen Gesprächstranskripte oder Gesundheitsinformationen als Standard. Textfelder begrenzen und Secrets/PII beim Export redigieren. Nutzer kann Bewertungen und Regeln ansehen, rückgängig machen, löschen; aktive Regeln aus Session-/Gedächtniscache invalidieren. Rohfeedback nicht an OpenRouter weiterreichen; gewöhnliche OpenRouter-Anfragen unterliegen weiterhin den dokumentierten bisherigen Kontext-/Privatsphäre-Einstellungen.

**Trainingseinwilligung getrennt:** `allow_training` standardmäßig `false`; Trainingsexport nur für überprüfte und bewusst freigegebene Beispiele. Ein trainiertes Modell kann Inhalte memorisieren: **Löschen eines Feedback-Eintrags entfernt nicht automatisch dessen Einfluss aus einem bereits trainierten Modell.** Solche Modelle/Backups müssten ausgemustert und bei Bedarf aus bereinigten Daten neu trainiert werden.

### 6. Agonie und Persönlichkeit

Die vorhandene `Mood`-Logik wird erweitert, nicht ersetzt. Der **flüchtige** Wert `agony_level` (0–1) steigt bei negativem Feedback kurz und klingt zeitbasiert ab. Stimmung z. B. temporär `besorgt`; nach Abklingen wieder normal. PiTFT: Servo-Schädel-Glitch und Text; Status-LED kurzer Impuls, optional kurzer synthetischer Effektton (standardmäßig **aus**). Im Servitor-Stil ein mechanisches „Korrekturimpuls“-Signal; in Billys Persönlichkeit passende sprachliche Variation.

Agonie ist **Rollenspiel und Rückmeldung**, kein numerischer Trainingsreward, kein echter Schmerz. Kein dauerhaftes Grollen, keine Bestrafung des Nutzers, keine absichtlichen falschen Antworten oder Verweigerung kritischer Hilfe. Stimmungsfunktion abschaltbar; Bewertungen und Lernregeln arbeiten unabhängig weiter.

### 7. Zweiter Proxmox als Lern- und Trainingsknoten

**Bekannt:** Intel i5 7. Generation, **32 GB RAM**, **keine dedizierte GPU**. Exaktes CPU-Modell/Kernzahl und Laufwerk noch unbestätigt. GPU ist keine logische Voraussetzung für CPU-LoRA, aber praktisch ein großer Beschleuniger: Ob **Qwen3-4B** auf dieser CPU innerhalb sinnvoller Zeit trainierbar ist, muss ein Benchmark zeigen.

**VM-Vorschlag (nicht installiert):**

| Ressource | Ausgangswert |
|---|---|
| VM | `proximus-training-01`, Ubuntu Server LTS |
| CPU | `host`, 2–4 vCPU **abhängig von realen physischen Kernen** |
| RAM | **24 GB** für VM, ca. 8 GB Reserve für Proxmox |
| SSD | **100–150 GB** für Originalmodelle, Datensätze, Adapter, Checkpoints |
| Netzwerk | nur privates LAN; kein öffentlicher Trainingsendpunkt |
| Software | Python, PyTorch, Transformers, PEFT, TRL, Datasets, llama.cpp |
| Betrieb | nach Bedarf; kein Einfluss auf Produktivantworten |

**Benchmark- und Größenstrategie:**
1. `Qwen3-0.6B` (oder ähnlich klein) als **CPU-SFT + LoRA**-Smoke-Test, Batch 1, kurze Sequenzen, 20 Trainingsschritte; Sekunden/Schritt, Peak-RAM, Datenträger und OOM messen.
2. Optional `Qwen3-1.7B`, wenn das Basistraining reproduzierbar ist.
3. `Qwen3-4B-Instruct-2507` nur nach Hochrechnung und RAM-Prüfung; Training mit passenden ursprünglichen Hugging-Face-Gewichten, **nicht** direkt mit der produktiven GGUF-Q4-Datei.
4. **QLoRA** auf CPU und **DPO** erst nach konkreter Kompatibilitätsprüfung der Backend-Versionen; beides ist nicht automatisch effizient oder auf jeder CPU-Konfiguration unterstützt. DPO braucht für denselben Prompt eine bessere **und** eine schlechtere Antwort und verursacht zusätzliche Rechenarbeit.
5. Wenn CPU-Training zu lange dauert, übernimmt Proxmox 2 weiterhin lokale **Datenerfassung, Bereinigung, Evaluation und Modellverwaltung**. Trainiert werden kann optional auf einem Mac mit geeignetem Apple Silicon/MLX oder einem temporären GPU-Rechner; das Konzept setzt **keinen Cloud-Dienst voraus**.

**Abbruchbedingung:** Zeit/RAM nach Pilotmessung für 4B nicht akzeptabel → kein aufwendiges tagelanges Experiment ohne Nutzen. Eine VM allein beschleunigt die CPU nicht.

### 8. Datensatz- und Trainingspipeline

Nur freigegebene, redigierte, eindeutige Fälle bilden einen Trainingsdatensatz. Duplikate zusammenführen; Kontext und Modellversion mitführen; Testfragen und Trainingsfragen thematisch trennen. Die Datenqualität entscheidet stärker als die Zahl der B-Drücke.

**SFT (erste Ausbaustufe):** `{"prompt":"Wie spät ist es?","completion":"Es ist 14 Uhr."}` – bevorzugte Antwort muss bestätigt sein. Beginnt mit kleinem Modell und CPU-LoRA.

**DPO (später):** `{"prompt":"Wie spät ist es?","chosen":"Es ist 14 Uhr.","rejected":"Gemäß den Chronometern ..."}` – nur gültig, wenn Antworten sich auf die **gleiche** Frage/den **gleichen** Kontext beziehen. Ein bloßer Dislike hat noch keine `chosen`-Antwort und ist **kein** DPO-Paar.

Erste Testdatensätze mit etwa 50–200 hochwertigen SFT-Beispielen können für **Machbarkeits- und Stiltests** dienen, ohne versprochenen Qualitätsgewinn; DPO braucht erfahrungsgemäß weitere hochwertige Präferenzpaare. Die Zahl ist ein Planungswert, kein festes magisches Trainingslimit.

**Zwei Lernebenen gleichzeitig:** bestätigte Regeln wirken sofort über Prompt/Gedächtnis; echte Gewichtsänderungen entstehen nur in einem offline durchgeführten, überprüften Train-Job. Nach Fine-Tuning den Nutzen bereits vorhandener Promptregeln neu evaluieren.

Referenzen: [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507), [PEFT/LoRA](https://huggingface.co/docs/peft/main/developer_guides/lora), [TRL/SFT](https://huggingface.co/docs/trl/sft_trainer), [TRL/DPO](https://huggingface.co/docs/trl/dpo_trainer).

### 9. Evaluation, Registry und Rollback

**Kein automatisches Deployment nach einer festen Anzahl Feedbacks.** Jeder Trainingslauf erzeugt einen **Kandidaten**. Dazu gehört ein Manifest aus Ausgangsmodell/-Revision, Datensatz-Hash, Hyperparametern, Trainingstools/-Versionen, CPU-Zeit, Peak-RAM und Evaluationsergebnissen.

**Promotion-Gate:**
1. Fixer Holdout-Testkatalog: Antwortkürze (nur wenn passend), Faktenkorrektheit, Warhammer-Lore/Persona, Mehrsprachigkeit, ausdrückliche lange Antworten, Datenschutz, Gerätebestätigungen und unbekannte Fragen.
2. Basismodell und Kandidat mit identischen Testfällen und vergleichbaren Generierungsparametern prüfen. Keine Trainings-/Testdatenlecks. Manuelle qualitative Bewertung plus messbare Fehler/Regressionsquote.
3. Modellkandidat auf **CT 107 mit 5 GB RAM** gesondert testen: Speicher, Latenz, Antwortqualität und alter llama.cpp-Version (derzeit `v0.5.0` im Installationsskript).
4. Adapter als kompatibles LoRA-GGUF nutzen **oder** Adapter in die Basisgewichte übernehmen und in GGUF umwandeln/quantisieren. Konverter-Kompatibilität mit der konkret gepinnten Toolchain muss getestet werden.
5. Kandidaten zunächst in separater Datei/Version bereitstellen, Smoke-Test; **erst nach manueller Freigabe** Produktionsreferenz umstellen. Bekannte funktionierende Modellversion und Promptkonfiguration bleiben als sofortiger Rollback.
6. Private Datensätze und Modellgewichte **nicht in das öffentliche Git-Repository** pushen; Skripte/Tests/Konfigurationsbeispiele dagegen versionieren.

**Nicht akzeptabel:** OOM, schlechtere Korrektheit oder Sicherheitsantworten, nicht nachvollziehbare Überanpassung, übermäßig höhere Antwortzeit, fehlende Lösch-/Rollback-Möglichkeit.

### 10. Protokoll, Betrieb und Fehlerzustände

**MVP ohne neue API:** Feedback lokal in `src/ptt.py` erfassen, einem schlanken Feedbackmodul übergeben, atomar speichern; nur **validierte aktive** Regeln in die bestehende Gedächtnis-/SPX/1-Übergabe zum LLM übernehmen. Gedächtnis-Hash und CT-107-Session nach Änderungen invalidieren. Jede neue Persistenzoperation außerhalb des Tasten-/Audio-Hot-Paths ausführen.

**Späterer Transport zum Trainingsserver:** Versionierter Nachrichtentyp `feedback.batch` mit Event-ID, ACK, Integritätsprüfung, Wiederholschutz und optionaler Freigabesignatur. SPX/1-Outbox ist laut Roadmap **noch nicht vollständig implementiert**. Bis dahin administrativer **LAN-Export** ohne direkten Schreibzugriff auf den Pi. Der Trainingsserver braucht keine Internetfreigabe.

**Logging ohne Privattext:** `feedback_recorded`, `feedback_approved`, `feedback_revoked`, `feedback_exported`, `training_started`, `training_finished`, `model_evaluated`, `model_promoted`, `model_rollback`. In Journal/Monitoring nur ID/Status, keine Frage-Antwort-Volltexte. Backups verschlüsselt/zugriffsbeschränkt, Retention und Löschprozess festlegen.

**Ausfälle:** Fehlender Stick/volle Datei/server offline: B funktioniert weiter als **Stop**; Anzeige muss deutlich zwischen „Bewertung erfasst“, „gespeichert“, „Regel aktiv“ und „nicht gespeichert“ unterscheiden. Keine verlorenen Feedbacks still als erfolgreich melden. Systemalarme/Notfalltexte und Tastenzuordnung dürfen nie von Training oder Netzwerk abhängen.

### 11. Phasen, Abnahme und offene Entscheidungen

| Phase | Aufgabe | Abnahme |
|---|---|---|
| **P0 – Grundlage** | B-Verhalten auf echtem SHIM messen, Cancel-Regressionstests | Kurz-/Langdruck unterbrechen sofort; Menü/Bestätigung unverändert |
| **P1 – Bewertungs-MVP** | Zuordnung einer LLM-Antwort, Event, USB, Undo, Display | Exakt ein Event; kein Feedback bei STT/Alarm; Reboot/Stickszenarien |
| **P2 – Lernregel** | Korrektur per Sprache, Bestätigung, thematischer Scope, Promptwirkung, Vergessen | Korrigierte Frage im Folgeturn besser; keine erfundene globale Regel |
| **P3 – Trainingsdaten** | Positiv-/Negativ-Paare, Exportfreigabe, Redaktion, Testkatalog | Nur zugelassene/qualitätsgeprüfte Daten verlassen den Pi |
| **P4 – CPU-Prototyp** | Proxmox-2-VM, SFT/LoRA 0.6B, Benchmark, Modelltest | Zeit/RAM/Qualität gemessen, reproduzierbarer Lauf |
| **P5 – Großmodell** | Falls praktikabel Qwen3-4B LoRA, optional DPO, GGUF-Konvertierung, Candidate-Vergleich | Messbarer Gewinn, keine Regressionsfehler, manueller Rollout und Rollback |
| **P6 – Optional** | Positive Verstärkung, Mustererkennung, Benutzerprofile, zeitgesteuerte Trainingsjobs | Änderbar, auditierbar, keine automatische Produktion |

**Akzeptanzkriterien des Gesamtsystems:**
- Bestehende Sprachausgabe lässt sich ohne Verzögerung abbrechen; mindestens ein Hardwaretest je relevantem B-Kontext.
- Bewertungsereignis immer eindeutig, nicht doppelt, nicht dem falschen Turn zugeordnet.
- Kein dauerhafter Regelwechsel ohne bestätigten Grund; Undo/Löschen deaktiviert ihn auch im CT-107-Kontext.
- Stimmung wird rein simuliert, klingt ab und beeinflusst keine sicheren Geräteaktionen.
- Ohne zweitem Proxmox, Netzwerk oder USB-Stick läuft Proximus weiterhin mit dokumentierten Fallbacks.
- Trainingsbeispiele nur nach Einwilligung; exportierte Daten überprüfbar/redigierbar.
- Neues lokales Modell wird **erst nach Evaluation und Freigabe** aktiviert; alte Version bleibt reproduzierbar.

**Noch zu entscheiden:** Glitch-Ton opt-in oder immer aus? Korrekturfrage automatisch einmal anbieten oder nur auf Sprachkommando? Ein Hauptbediener zunächst oder Personen-spezifische Regeln direkt? Exaktes i5-Modell, SSD, verfügbare Kerne und RAM unter Last auf Proxmox 2? Welche Aufbewahrungsfristen sollen für Trainingsexporte und Adapter gelten?

**Umsetzungsprinzip:** Erst Feedback-/Gedächtnis-MVP, dann Datenqualität, erst danach CPU-Fine-Tuning-Experiment. Dieses Konzept macht **keine** ungeprüften CPU-Leistungs- oder Trainingsdauerzusagen.
