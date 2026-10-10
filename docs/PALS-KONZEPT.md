# PALS – Proximus Agonie-, Feedback- und Lernsystem

**Vollständiges technisches Konzept / Implementierungsspezifikation für die Weiterentwicklung mit Claude**

| Feld | Wert |
|---|---|
| Projekt | [0b-ivan/pi-voice-assistant](https://github.com/0b-ivan/pi-voice-assistant) |
| Dokument | `docs/PALS-KONZEPT.md` |
| Version | 1.0, Entwurf vom 10.10.2026 |
| Implementierungsstand | **Nicht implementiert**; dieses Dokument beschreibt Zielarchitektur und Abnahmekriterien |
| Zielgruppe | Projektentwickler, Claude Code, Reviewer und Betreiber |
| Charakter | Proximus / Servitor bzw. Billy |
| Priorität | Bestehende Sprach-/Abbruchfunktionen dürfen nicht regressieren |

> **Definition:** „Agonie“, „Bestrafung“ und „Schmerz“ sind in PALS **fiktive, simulierte Zustände** eines Sprachassistenten. Ein LLM leidet dadurch nicht. Eine negative Rückmeldung kann die künftigen Antworten durch Gedächtnisregeln beeinflussen oder später als Trainingsbeispiel dienen. **Ein Tastendruck ist kein Fine-Tuning und verändert keine Modellgewichte.**

## 0. Nutzung durch Claude

Dieses Dokument ist die **einzige fachliche Spezifikation** für das neue Feature. Die bestehende `README.md`, die tatsächlichen Repository-Dateien und Tests sind die Quelle für den implementierten Istzustand. Bei Abweichungen zwischen diesem Entwurf und dem Code: **erst Code prüfen, Unterschiede dokumentieren, keine vorhandenen Funktionen überschreiben**.

Die Schritte in **Abschnitt 16** sind als einzeln reviewbare Pull Requests gedacht. Nicht das gesamte Feature auf einmal implementieren. Ohne ausdrücklichen Auftrag **kein Merge in `main` und kein Deployment auf die Geräte**.

### Begriffe

- **Feedback:** Bewertung einer einzelnen LLM-Antwort (positiv/negativ); zunächst keine Verhaltensregel.
- **Korrektur:** Erklärung, was konkret falsch und was stattdessen gewünscht war.
- **Lernregel:** Vom berechtigten Bediener bestätigte, kontextabhängige Präferenz im Gedächtnis.
- **Agonie:** Simulierter, vorübergehender visueller/akustischer/emotionaler Zustand als Reaktion auf negatives Feedback.
- **SFT:** Supervised Fine-Tuning anhand bestätigter erwünschter Antworten.
- **DPO:** Direct Preference Optimization anhand `prompt`, `chosen`, `rejected`.
- **Adapter:** Kleine trainierte LoRA-Gewichte; nicht das vollständige Basismodell.
- **Kandidat:** Neu trainierte Modellversion, die **nicht** automatisch produktiv ist.

## 1. Zielbild und Nicht-Ziele

Proximus soll seine Rolle und seine Fähigkeiten behalten, sich aber nach und nach verlässlich auf den Bediener einstellen. Ein negativer Tastendruck kennzeichnet eine **konkrete Antwort** als unerwünscht. Erst eine genaue, bestätigte Korrektur darf daraus eine aktive Regel erzeugen. Eine separate, optional laufende Trainingsumgebung soll später **echtes Parameterlernen** für das lokale Qwen-Modell ermöglichen.

**MUSS-Anforderungen:**

1. **B stoppt sofort** – auch bei langem Drücken. Vorhandene Funktionen in Menü, Personenregistrierung, Wartung, Aufnahme, Sicherheitsrückfragen und Audio bleiben erhalten.
2. Jede Bewertung ist genau **einem** geeigneten Turn und einer tatsächlich generierten LLM-Antwort zugeordnet; keine Bewertung von STT, Alarmen, Status, Geräteaktionen oder noch nicht erzeugten Antworten.
3. Ein reines „schlecht“ erzeugt **keine erfundene globale Regel**. Neue Regeln brauchen eine konkrete, bestätigte Korrektur.
4. Speicherung folgt dem bestehenden Prinzip: **nur der Pi schreibt den USB-Gedächtnis-Stick** `PROXIMUS`; ohne Stick kein dauerhaftes Lernen.
5. Der zweite Proxmox ist **keine Laufzeitabhängigkeit**. Training ist auslagerbar, stoppbar und optional.
6. Lernregeln sind inspizierbar, deaktivierbar und widerrufbar. Modelländerungen brauchen Evaluation, explizite Freigabe und Rollback.
7. Datenexport für Training ist **standardmäßig aus** und nur nach Freigabe gestattet.
8. Sicherheits- und Bedienprioritäten haben Vorrang vor Persona, Stimmung und erlernten Präferenzen.

**SOLLTE-Anforderungen:** Nachvollziehbare Rückmeldung auf dem PiTFT, kurze simulierte Agonie, positive Bewertung per Sprache, Testfälle gegen Regression und Lernen aus ausdrücklichen Gegenbeispielen.

**Nicht-Ziele für MVP:** Vollständiges RLHF/Online-RL, dauerhaft laufende Trainingsprozesse auf dem Pi, direkter Gewichtsupdate nach einem Knopfdruck, Cloud-Zwang, neue Datenbank auf dem Pi, autonomes Überschreiben produktiver Modelle, starke biometrische Sicherheit allein durch kurze Stimmaufnahmen.

## 2. Verifizierter Ausgangspunkt (Repo-Stand 10.10.2026)

Vor Implementierung aktuelle Branches/Dateien erneut lesen.

| Komponente | Vorhanden / zu beachten |
|---|---|
| Hardware | Raspberry Pi Zero 2 W, WM8960, Pimoroni Button SHIM A–E, PiTFT, PiSugar 3 |
| PTT-Tasten | `src/ptt.py`: SHIM B verarbeitet bisher ein **Start-Ereignis** und ruft `cancel()` auf; Menü/Sonderabläufe haben Vorrang |
| Audio/Stimme | Piper/WM8960; produktive Ausgabe kann B bereits unterbrechen |
| Stimmung | `src/mood.py`: `Mood` mit vorhandenen Emotionen wie `besorgt`, `gereizt`, Abklingfunktion |
| USB-Gedächtnis | `src/memory.py`: `memory.json` im Ordner `proximus/`, atomare Schreibweise; Fakten, Direktiven, kurzer Verlauf |
| Kommunikationsprotokoll | `src/protocol.py`: SPX/1; `TYPES` enthält derzeit `hello`, `welcome`, `ping`, `ack`; **noch kein `feedback.batch`** |
| CT 107 | `server/servitor_server.py`: Vosk, Piper, `src/llm.py`; HTTP `/v1/turn`, `/v1/hello`, `/v1/message` |
| Lokales LLM | Qwen3-4B-Instruct-2507, quantisiertes GGUF Q4_K_M über `llama.cpp`, ca. 5 GB RAM/4 CPU-Kerne für CT 107 |
| Externes LLM | OpenRouter; Modellgewichte fremd, daher kein direktes Fine-Tuning durch uns |
| Dokumentationsstatus | [PR #81](https://github.com/0b-ivan/pi-voice-assistant/pull/81) konsolidiert bestehende Doku; [PR #82](https://github.com/0b-ivan/pi-voice-assistant/pull/82) wurde geschlossen, seine Idee in #81 aufgenommen |

**Wichtige technische Lücke:** Ein eigener `turn_id`-/Antwortkandidat-Lebenszyklus ist für PALS zu ergänzen. Aus dem existierenden `cancel()` darf kein Rückschluss auf eine bewertete Antwort gezogen werden. Ebenso dürfen in `src/protocol.py` nicht existierende Outbox-Features nicht als verfügbar angenommen werden.

## 3. Gesamtarchitektur

```text
+--------------------------------------------------------------+
| PI ZERO 2 W – produktiver Controller                          |
| WM8960 / PTT / SHIM B                                         |
|    |                                                         |
|    +--> Cancel (sofort, bestehende Funktion)                  |
|    |                                                         |
|    +--> FeedbackCapture (Press/Hold/Turn-Kontext)             |
|           |                                                  |
|           +--> FeedbackStore -> USB PROXIMUS                 |
|           +--> Mood/Agony -> PiTFT/LED/(opt.) kurzer Sound    |
|           +--> RuleManager -> bestätigte Präferenzen         |
|                               |                              |
+-------------------------------|------------------------------+
                                | validierter, kompakter Kontext
                                v
+--------------------------------------------------------------+
| CT 107 – produktiver Inference Node (Proxmox 1)               |
| Vosk / Intent-Router -> OpenRouter oder Qwen3-4B via llama.cpp|
| Piper -> Audio an Pi                                         |
| Training NICHT im Antwortpfad                                |
+-------------------------------|------------------------------+
                                | explizit freigegebener Export
                                v
+--------------------------------------------------------------+
| TRAINING NODE – Proxmox 2, i5 Gen7, 32 GB RAM, ohne dGPU     |
| Import -> Redaktion -> Dataset -> SFT/LoRA -> Evaluation      |
|                                    -> optional DPO           |
|                       Kandidat + Report + Checksum          |
+-------------------------------|------------------------------+
                                | Freigabe durch Betreiber
                                v
+--------------------------------------------------------------+
| CT 107 – Staging-/Kompatibilitätstest, Modellwechsel,         |
| Überwachung und sofortiger Rollback auf bekannte gute Version|
+--------------------------------------------------------------+
```

**Verantwortlichkeiten:** Pi ist der einzige Autor für persönliche Präferenzen; CT 107 benutzt nur die aktuell gültigen Regeln; Training Node kann nur **Kandidaten erzeugen**. Der Besitzer gibt Trainingsexport und Modellpromotion separat frei.

## 4. Taste B: exakte Bediensemantik

Die Belegung wird **nicht ersetzt**, sondern erweitert.

**Konfigurierbare Startwerte (Entscheidungsvorschlag):**

- `FEEDBACK_ENABLED=0` als initialer Feature-Flag bis reale Hardware-Abnahme.
- `FEEDBACK_B_HOLD_MS=800`
- `FEEDBACK_LAST_TURN_WINDOW_S=60`
- `FEEDBACK_AGONY_SOUND=0` (Display/LED ja, Sound opt-in)
- `FEEDBACK_REQUIRE_CONFIRMATION=1`

| Kontext **beim Press-Edge** | Kurzer Druck | Langer Druck (≥ 800 ms) |
|---|---|---|
| Eigene **LLM**-Antwort spielt | sofort stoppen | sofort stoppen; diese Antwort **einmal** negativ bewerten |
| Leerlauf, letzte bewertbare LLM-Antwort ≤ 60 s | heutige Abbruch-/Leerlauffunktion | letzte Antwort **einmal** bewerten |
| Aufnahme, STT, LLM denkt ohne fertige Antwort | abbrechen/verwerfen | abbrechen/verwerfen, **kein** Feedback |
| Menü/Picker/Enrolment/Wartung/offene Bestätigung | heutige Zurück-/Cancel-Semantik | **kein** Feedback |
| Alarm, E-Status, fixe Intent-Antwort, kritische Geräteansage | stoppen wie bisher | **kein** LLM-Feedback |
| Boot mit gedrücktem B oder I²C-Ausfall | sichere bestehende Behandlung | **kein** Feedback |

**Priorität:** Sicherheit / Gerätebestätigung > Registrierung und Wartung > Menü > Abbruch des laufenden Vorgangs > Feedback. **B + E gleichzeitig** darf weder bestätigen noch eine Lernregel aktivieren.

### 4.1 Press/Hold/Release-Zustandsautomat

1. **Press:** Genau einmal bestehende Cancel-Aktion sofort ausführen. **Vor** dem Cancel gegebenenfalls einen unveränderlichen `feedback_candidate` aus dem aktuellen LLM-Turn übernehmen. Falls im Leerlauf: nur unbewerteten Kandidaten innerhalb des Zeitfensters übernehmen.
2. **Hold:** Entprellte Tastensituation mit `time.monotonic()` prüfen. Beim Erreichen von 800 ms genau ein `feedback_recorded` an einen **nicht blockierenden Worker** melden, sofern Kandidat gültig und Fingerabdruck nicht schon verwendet.
3. **Release:** Tastenstatus zurücksetzen, kein zweites Event, keine erneute Cancel-Aktion.
4. **Neuer Turn beginnt:** Alten Leerlauf-Kandidaten invalidieren. Frühere Turn-Antwort nicht irrtümlich bewerten.
5. **Timeout:** Nach 60 s Kandidat verwerfen. Nach Dienstneustart **kein alter drückbarer Kandidat**.
6. **Fehlkontext:** Ein langer Druck erzeugt **keine** Strafe und keine Meldung „gelernt“.

Eine **fertig generierte, aber noch nicht vorgelesene** Antwort ist nur bewertbar, wenn eindeutig dieselbe Antwort gerade zur Wiedergabe vorgesehen oder begonnen wurde. Kein Feedback an einen unbekannten STT- oder LLM-In-Flight-Turn.

### 4.2 Zuordnung und Idempotenz

- Jeder Turn benötigt eine interne ID (`turn_id`: UUID o. ä.), Quelle (`llm` vs. `intent`/`alarm`/`status`), Zeitstempel, Frage, Antwort, Modell, aktive Persona.
- Zusätzlich `feedback_id` (eindeutig), `press_id` für Tastenereignis und `last_rated_turn_id`.
- Ein **Press** erzeugt maximal einen Datensatz; identische IDs dürfen auch nach wiederholten Worker-Versuchen nicht doppelt geschrieben werden.
- Ob nach Ende einer Antwort **mehrfach** bewertet werden darf: **MVP nein**; spätere erneute Bewertung nur nach bewusstem Dialog.
- Ein abgebrochener TTS-Stream muss sein vollständiges, bereits generiertes Antworttextfeld weiterhin als Kandidat verfügbar haben, soweit vorhanden; kein Speichern großer Audio- oder Rohausschnitte.

## 5. UX: negative Bewertung, Korrektur, positive Verstärkung

### 5.1 Negativer Ablauf

```text
Bediener: „Wie spät ist es?“
Proximus: „Gemäß den Chronometern des Omnissiah ist ...“
Bediener: hält B ≥ 800 ms.
[Audio stoppt sofort beim ersten Press]
[PiTFT: kurzer Glitch, „AGONIE / FEEDBACK ERFASST“]
[LED: kurzer Impuls; optional leiser synthetischer Glitch]
[USB: NEGATIVE zu turn_id gespeichert, noch KEINE Regel]

Bei nächster geeigneter Interaktion (maximal einmal pro Fall):
Proximus: „Korrektur registriert. Was soll ich ändern?“
Bediener: „Bei einfachen Uhrzeitfragen nur die Uhrzeit.“
Proximus: „Regelvorschlag: Zeitfragen kurz und direkt beantworten. Übernehmen?“
Bediener: „Bestätigt.“ / physisch E nach eindeutiger Anzeige
[USB: Regel ACTIVE; CT-107-Gedächtnis-Hash invalidiert]
Folgeturn: kurze Antwort, wenn keine explizite andere Anweisung vorliegt
```

**Wichtig:** Das **sofortige Stoppen** darf nicht durch ungefragte TTS-Wiedergabe („Aua!“) wieder aufgehoben werden. Sound nur kurz, gesondert und abschaltbar. Bei Stick-/Persistenzfehler stattdessen „NICHT GESPEICHERT“ anzeigen.

### 5.2 Sprachbefehle (neu; mit Vosk-Echtaufnahmen prüfen)

- „Korrigiere die letzte Antwort: …“ – öffnet Regelvorschlag für den letzten bewerteteten Turn.
- „Was hast du aus meinen Korrekturen gelernt?“ – zeigt/spricht aktive Regeln.
- „Vergiss die letzte Korrektur.“ – deaktiviert zugehörige Regel und bestätigt tatsächliche Änderung.
- „Lernregeln anzeigen.“ – zeigt ein kompaktes Menü.
- „Gut gemacht“, „So ist es richtig“ – optional später positives Feedback zur jüngsten Antwort.
- „Agonie aus“ / Menüschalter – schaltet **nur den Persona-Effekt**, nicht die Feedback-Speicherung aus.

**Designregel:** Bestätigungen immer auf den genau bezeichneten Regelvorschlag beziehen; keine unbestimmte „ja“-Äußerung aus anderem Gespräch als Freigabe interpretieren.

### 5.3 PiTFT/Menü

Unter bestehender Gruppe **Persönlichkeit** (oder **System**, je nach UI-Platz) optional:

- `Lernen`: AUS / AN
- `Agonie-Effekt`: AUS / AN
- `Offene Korrekturen`: Anzahl
- `Aktive Regeln`: Anzeigen / Löschen
- `Letzte Bewertung`: Rückgängig
- `Trainingsfreigaben`: standardmäßig AUS; Verwaltung später

Kein neues umfangreiches Untermenü im MVP zwingend, sofern Sprachbefehle und eine klare Anzeige vorhanden sind.

## 6. Datenmodell und Persistenz

**Source of Truth:** USB-Stick mit Label `PROXIMUS`, Pfad unter `/mnt/proximus-memory/proximus/` (wie bestehendes `memory.json`). Neue Datei **`feedback.json`**, kein direktes Vermischen mit historischen Fakten/alten Direktiven. Versionierte Schemas, beschränkte Größen, atomare Aktualisierung (temp-Datei + `fsync` + Rename + `fsync` des Ordners) analog `src/memory.py`.

### 6.1 Feedback-Record – Beispiel

```json
{
  "schema_version": 1,
  "id": "fb_01JEXAMPLE",
  "turn_id": "turn_42",
  "press_id": "press_19",
  "created_at": "2026-10-10T14:00:00+02:00",
  "origin": "button_b_long",
  "rating": "negative",
  "source": "llm",
  "model": "local/qwen3-4b",
  "persona": "servitor",
  "speaker_profile": "owner",
  "speaker_confidence": "unverified",
  "prompt_text": "Wie spät ist es?",
  "rejected_text": "Gemäß den Chronometern ...",
  "reason": null,
  "chosen_text": null,
  "scope_hint": null,
  "state": "recorded",
  "allow_training": false,
  "rule_id": null
}
```

**Hinweis:** Das Beispiel ist eine **vorgeschlagene** Struktur; es gibt noch kein solches API. `speaker_confidence` darf nicht als verlässliche Authentisierung verstanden werden. Freitext wird hinsichtlich Länge, Privatsphäre und Kontrollzeichen begrenzt. Bei Datenschutzbedenken statt Volltext nur ein kurzzeitig vorgehaltenes Snippet und eine nicht umkehrbare Prüfsumme ablegen; zum DPO-Training ist ein passendes Textpaar erst nach Einwilligung erforderlich.

### 6.2 Lernregel – Beispiel

```json
{
  "id": "rule_short_time_01",
  "source_feedback_ids": ["fb_01JEXAMPLE"],
  "version": 1,
  "status": "active",
  "owner_profile": "owner",
  "scope": {"intent": "time", "persona": "any"},
  "preference": {
    "kind": "conciseness",
    "instruction": "Bei einfachen Uhrzeitfragen direkt und kurz antworten."
  },
  "created_at": "2026-10-10T14:03:00+02:00",
  "confirmed_by": "physical_e_after_review",
  "allow_training": false
}
```

**Regelpriorität:** Sicherheits-/Systemvorgaben und exakte Nutzeranfrage > berechtigte, passende Lernregel > Persona/Lore/Stimmung. Eine Lernregel zu „kurz antworten“ darf z. B. die ausdrückliche Bitte um eine ausführliche Erklärung nicht überschreiben.

### 6.3 Aufbewahrung (konfigurierbare Startwerte)

- Höchstens **200 Feedback-Ereignisse** und **20 aktive Regeln** auf dem Stick.
- Nicht bestätigte Rohbewertungen nach **30 Tagen** löschen oder zur manuellen Prüfung markieren; bestätigte Regeln behalten, bis widerrufen.
- Kein automatisches Wegwerfen bestätigter Lernregeln bei Kapazitätsdruck; bei Vollzustand ehrliche Fehlermeldung statt stiller Datenverlust.
- Kein Roh-Audio im Feedback; nur für Training freigegebene und redigierte Textpaare exportieren.
- Speicherdatei bei unvollständigem oder beschädigtem JSON sicher isolieren, **nicht** fragwürdige Regeln ungeprüft aktivieren.
- Dateizugriff nur für Dienstbenutzer; keine personenbezogenen Freitexte im systemd-Journal.

### 6.4 Löschung und „Unlearning“

Das Entfernen eines Feedbacks oder einer Regel muss den künftigen **Prompt-/Gedächtniseffekt** entfernen, einschließlich CT-107-Sessions und Cache. Für bereits trainierte Modelladapter gilt ausdrücklich:

> Die Entfernung aus dem Datensatz entfernt **nicht automatisch** den gelernten Einfluss aus Modellparametern.

Zum verlässlichen Widerruf einer Trainingsfreigabe betroffene Adapter/Kandidaten sperren und ggf. aus bereinigtem Datensatz neu trainieren. Aufbewahrte Trainingsartefakte und Backups in Löschkonzept berücksichtigen.

## 7. Regel-Engine / direktes Lernen (MVP)

**Ein B-Druck ist nur ein negatives Beispiel.** Das System darf daraus ohne Erklärung keine Ursache erfinden. Regelvorschläge können mithilfe des LLM vorbereitet werden, aber **nur nach Bestätigung** aktiv werden.

**Kompakter Regelzustand:**

`recorded` → `explained` → `proposed` → `approved` → `active`, alternativ `discarded`, `revoked`.

- `recorded`: nur negative Bewertung, keine Regel.
- `explained`: Bediener nennt Grund und ggf. gewünschte Antwort.
- `proposed`: Parser/LLM erzeugt eine **konkrete** begrenzte Regel.
- `approved`: Eigentümer bestätigt eindeutigen Wortlaut und Geltungsbereich.
- `active`: Regel wird beim nächsten Turn berücksichtigt.
- `revoked`: Regel verliert sofort Wirkung; Datenhistorie gem. Löschrichtlinie behandeln.

**Technik zur Anwendung:**
1. Regeln nach Kontext (Intent/Schlüsselwörter, erkannte Persona, Bediener) filtern.
2. Maximal wenige relevante Regeln in das bestehende Speicher-/Promptkontextformat einfügen; **Grenzen in `src/memory.py`** beachten (`CONTEXT_BUDGET`, `HEADER_LIMIT`).
3. Änderungen müssen den stabilen SPX/1-Kern-Digest verändern bzw. die Sitzung invalidieren; `src/protocol.py`-Sitzungsmechanismus prüfen.
4. Regeltexte niemals ungeprüft als höherrangigen Systembefehl interpretieren. Speichern und einschleusen als untrusted, ausdrücklich gekennzeichnete Benutzervorlieben.
5. Unter OpenRouter- und lokalem Qwen-Modus gleichermaßen wirksam; niemals private komplette Fehlerarchive an externe Modelle senden.
6. Im rein lokalen Pi-Fallback ohne LLM bleiben deterministische Intents und Statusantworten unverändert; keine falsche Zusage, ein Qwen-Modell liefe auf dem Pi.

**Mehrbenutzer:** Phase 1 bevorzugt Hauptbediener und explizite Bestätigung am Gerät. Physische Taste B kann jeder Anwesende drücken, daher erzeugt sie **keine Administrationsberechtigung**. Später scoped Präferenzen je Stimmprofil; Stimmerkennung allein ist nicht als starke Authentisierung anzusehen.

## 8. Emotionen und Agonie: simulierte Zustände

**Bestehende `Mood`-Klasse weiterverwenden**, keine parallele Emotionsimplementierung. Die Bewertungsfunktion darf weiter funktionieren, wenn Gefühle vollständig ausgeschaltet sind.

Vorgeschlagenes Modell:

- `agony_level` in `[0, 1]`, Start `0`.
- Nach einem gültigen negativen Feedback **ein kurzer Impuls**, z. B. `+0.4`, gedeckelt auf `1.0`.
- Zeitliches Abklingen unabhängig vom aktiven Lernregelspeicher; kein dauerhafter Zustand „bestraft“.
- Optional `Mood.feel("besorgt", ...)`, jedoch keine dauerhafte Gereiztheit oder unkontrollierte Eskalation.
- Servitor: kurzer technischer Feedback-Fehler / gestörtes „Engramm“ auf PiTFT, rote/violette LED, optionaler DSP-Glitch.
- Billy: andere künstlerische Darstellung bei gleichem Logikzustand.
- Stille nach B-Cancel bleibt Priorität. Keine Notfallmeldung, Hilfe, Wartung oder konkrete Antwort wird durch den Stimmungseffekt blockiert.

**Trennung:** `agony_level` wird **nicht** als numerischer RL-Reward für Modelltraining verwendet. Ein „hoher Schmerz“ ist nur UI/Persona, kein Qualitätsmaß.

## 9. Trainingsdatensätze: von Bewertungen zu echtem Modelllernen

Training ist eine **zweite Entwicklungsetappe**. Die Feedback-Engine muss vorher ohne Training stabil funktionieren.

### 9.1 Quellen und Gütesicherung

Nur **ausdrücklich freigegebene** Beispiele werden exportiert. Kriterien:

- Identische Frage und relevanter Kontext für abgelehnte/bevorzugte Antwort.
- Bisherige Antwort (`rejected`) wirklich vom Modell generiert; gewünschte Antwort (`chosen`) vom Bediener bestätigt oder manuell geprüft.
- Kein Secrets-Leak, private Termine/Passphrasen/medizinische Daten, keine fremden personenbezogenen Aussagen ohne Freigabe.
- Dubletten entfernen, Korrekturen nach Fehlertyp klassifizieren (Fakt, Stil, Länge, Ton, Lore, Mehrsprachigkeit).
- Unveränderlicher Holdout-Testsatz; kein gleicher oder nahezu identischer Beispielsatz gleichzeitig in Train und Test.
- Ergebnisse nach ursprünglicher Modellversion und Datensatzversion dokumentieren.

Ein unkorrigiertes `rating=negative` ist **kein DPO-Datensatz**.

### 9.2 SFT-Format

Für erste CPU-Experimente mit bestätigten guten Antworten:

```json
{"prompt": "Erkläre Docker kurz.", "completion": "Docker verpackt Anwendungen mit ihren Abhängigkeiten in Container."}
```

### 9.3 DPO-Format

Erst bei echten Präferenzpaaren:

```json
{
  "prompt": "Erkläre Docker kurz.",
  "chosen": "Docker verpackt Anwendungen und Abhängigkeiten in Container.",
  "rejected": "Im Namen des Omnissiah verkünde ich eine lange Litanei ..."
}
```

DPO optimiert das Modell zugunsten der bevorzugten Antwort gegenüber der abgelehnten Antwort. **Es setzt ein sauber zugeordnetes Paar voraus.** Optionales DPO erst nach einem stabilen SFT/LoRA-Basispfad.

### 9.4 Qualitätsindikatoren

Zur operativen Entscheidung dienen: Anteil bestätigter Korrekturen, Anteil zurückgenommener Regeln, Korrekturtrefferquote bei wiederkehrendem Intent, Duplikat-/PII-Quote und später Eval-Scores des Kandidaten gegen die Baseline. **Nicht**: Anzahl negativer Knopfdrücke als alleinige Optimierungsmetrik.

## 10. Zweiter Proxmox als Training Node (ohne dedizierte GPU)

**Bekannt:** Zweiter Proxmox mit **32 GB RAM, Intel i5 der 7. Generation und keiner externen GPU**. Exaktes i5-Modell, CPU-Kerne, SSD, Temperaturen und Hostlast sind vor Planungsfreigabe zu prüfen.

**Vorgeschlagene, isolierte VM:**

| Ressource | Wert / Entscheidung |
|---|---|
| VM | `proximus-training-01` |
| OS | Ubuntu Server LTS (z. B. 24.04) |
| CPU | `host`, 2–4 vCPU, nur soweit tatsächliche Kerne/Hostreserve erlauben |
| RAM | Start bei 24 GB, ca. 8 GB Reserve für Proxmox/Host |
| SSD | Mindestens 100–150 GB Arbeitsbereich für Modellgewichte/Checkpoints/Exports |
| Netzwerk | Privates LAN, nicht öffentlich; SSH beschränkt |
| Laufzeit | Nach Bedarf; keine Auswirkungen auf den produktiven CT 107 |
| Werkzeuge | Python, PyTorch, Transformers, PEFT, TRL, Datasets, `llama.cpp` (exakt gepinnte Versionen) |

**Wahrheitsgetreue Machbarkeit:** Ohne dedizierte GPU ist CPU-LoRA **grundsätzlich** möglich, kann aber deutlich länger dauern. 32 GB RAM allein bedeuten nicht, dass Qwen3-4B praktisch oder überhaupt mit der konkret gewählten LoRA-/Quantisierungs-Pipeline trainierbar ist. QLoRA-/4-Bit-Backends und DPO auf x86-CPU müssen je Version **real getestet** werden. Keine erfundenen Trainingstaktzahlen.

**Messplan vor großem Training:**

1. Kleines Modell wie **Qwen3-0.6B** mit klassischem LoRA/SFT auf CPU, kurze Sequenzen, Batch 1, 20 Schritte.
2. Peak-RAM, Schritte pro Sekunde, Swap-Nutzung, CPU-Temperatur und Speicherbedarf messen; bei Swap/OOM abbrechen.
3. Optional 1.7B probieren; dann mit **Qwen3-4B-Instruct-2507** Hochrechnung und kurzer Smoke-Test, nur sofern kompatibel.
4. Für Training geeignete **Original-Modellgewichte** laden, nicht versuchen, die vorhandene Q4_K_M-GGUF-Inferenzdatei direkt mit einer nicht unterstützten Pipeline zu „trainieren“.
5. Wenn zu langsam, Proxmox 2 weiterhin als **Dataset-/Evaluationsserver** betreiben. Training kann optional auf Mac mit Apple Silicon/MLX oder temporärer GPU erfolgen. **Kein Cloud-Zwang.**

### Trainingsjob-Metadaten

Jeder Job speichert **außerhalb des öffentlichen Repos**: `job_id`, Code-Commit, Basismodell/Revision/Tokenizerversion, Dataset-Hash, Hyperparameter, Trainingsframeworkversionen, CPU/RAM-Limits, Dauer, Peak-RAM, Checkpoint und Logs ohne private Volltexte. Ein Job darf keine Produktivkonfiguration ändern.

## 11. Transport und Schnittstellen

**MVP:** Keine neue Netz-API notwendig. Pi erfasst B lokal; aktive Regeln werden mit dem bestehenden Gedächtniskern/Sitzungsmechanismus an CT 107 übermittelt. Die SPX/1-Grenzen (insbesondere vorhandene Header-/Bodylimits) bleiben einzuhalten.

**Exportstufe (später):** Ein administrativ gestarteter und explizit erlaubter Export erstellt ein lokales `jsonl`-Paket samt Manifest/Prüfsumme. Übertragung im **privaten LAN**, abgesichert durch bestehende Authentisierung/SSH oder gesondert geprüfte Lösung. Trainingsserver erhält **kein** direktes Schreibrecht auf den Pi.

Eine zukünftige SPX/1-Nachricht `feedback.batch` wäre optional; sie ist **nicht vorhanden**. Vor Nutzung muss sie mit Bodylimit, Version, ACK, Idempotenz und Retry im Protokoll implementiert werden. Die bestehende `protocol.TYPES`-Allowlist respektieren. Kein sofortiger Ausbau zu WebSockets oder eigenständigem Message-Bus erforderlich.

**API-Verträge – mögliche interne Interfaces (Entwurf, keine vorhandenen Funktionen):**

```python
FeedbackCapture.on_b_press(now_monotonic, context) -> None
FeedbackCapture.on_b_hold(now_monotonic) -> FeedbackEvent | None
FeedbackCapture.on_b_release(now_monotonic) -> None
FeedbackStore.append(event: FeedbackEvent) -> bool
FeedbackStore.mark_explained(feedback_id, reason, chosen=None) -> bool
RuleManager.propose(feedback_id) -> RuleProposal
RuleManager.approve(proposal_id, actor) -> ActiveRule
RuleManager.revoke(rule_id, actor) -> bool
RuleManager.relevant(turn_context) -> list[ActiveRule]
```

Exakte Signaturen nach Code-Review entscheiden. Speicherzugriffe dürfen weder Tastenumfrage noch Audioausgabe blockieren.

## 12. Trainingsergebnis, Modellwechsel und Rollback

Produktive Inferenz läuft auf CT 107 mit `llama.cpp` und einem quantisierten Qwen3-4B-Modell. Nach Training entstehen **zunächst Adaptergewichte** und ein Evaluationsbericht.

**Deployment-Lebenszyklus:**

1. Trainingsjob liefert **Kandidatenadapter**, Basismodellreferenz, SHA-256-Prüfsummen, Dataset-ID, Evaluationsreport.
2. Adapter technisch prüfen: kompatibler LoRA-Adapterpfad oder **Merge in Basisgewichte → GGUF-Konvertierung → Quantisierung**. Exportkompatibilität mit der tatsächlich eingesetzten `llama.cpp`-Version testen.
3. Kandidat vor Aktivierung isoliert prüfen (separate Datei, Service/Port oder Wartungsfenster) – **CT 107 hat nur ca. 5 GB RAM**, also keine Zusage, zwei große Modelle parallel halten zu können.
4. Unveränderlichen Holdout-Test durchführen: kurze und lange Antworten, faktische Genauigkeit, technische Sprache, deutsche Aussprache, Persona/Lore, Kontexthandling, Sicherheitsbefehle und bisher unbekannte Fragen.
5. Latenz und Arbeitsspeicher im echten CT-107-Betrieb messen; keine Verschlechterung über zuvor festgelegte Toleranzen.
6. **Nur nach ausdrücklicher Betreiberfreigabe** atomare Modellreferenz wechseln, Dienst neu starten/Health-Check, Smoke-Test.
7. Bei fehlender Readiness, OOM, Fehlern oder schlechterem Verhalten sofort auf **vorherige Modellversion plus zugehörige Konfiguration** zurückwechseln.

**Nie:** Automatische Produktion nach „100 Strafen“ oder direktes Überschreiben von `current.gguf` ohne getestete Rückkehrmöglichkeit.

## 13. Zuverlässigkeit, Fehlermodi, Sicherheitsmodell

| Fehlermodus | Erwartetes Verhalten |
|---|---|
| USB-Stick fehlt / voll | B-Abbruch bleibt sofort; Bewertung maximal flüchtig, Status ehrlich „nicht gespeichert“ |
| CT 107 offline | B/Agonie/lokale Speicherung funktionieren; direkte Pi-Intents unverändert |
| Proxmox 2 aus | Kein Einfluss auf produktiven Sprachassistenten |
| OpenRouter-Fehler | Existierender lokaler LLM-Fallback auf CT 107; keine eigene Trainingsabhängigkeit |
| Doppeltes Hold/Prellen | maximal **eine** Bewertung pro Press und Antwort |
| Firmware-Reboot bei gedrücktem B | keine Phantom-Bewertung |
| Fremder drückt B | darf bewerten, aber **keine** globale, unbestätigte Regel aktivieren |
| Manipulierter Feedback-Text | als untrusted Daten behandeln, nicht als höherrangige Systemanweisung |
| Fehltrainierter Adapter | Fail closed: nicht promoten; bereits aktivierter Kandidat rollback |
| Regel gelöscht | sofort kein Prompt-Effekt mehr; bereits trainiertes Modell separat behandeln |
| Personen-/Geheimdaten im Training | Export verweigern/Redaktion, keine Secrets im Log oder öffentlichem Git |

**Bedrohungen:** Prompt Injection über gespeicherte Feedbacktexte, Feedback Poisoning durch andere Personen, Sprecherverwechslung, private Daten in exportierten Paaren, Replay/duplizierte Events, Training-Set-Leakage, Modellregression, falsche Erfolgsanzeigen und Storage-Korruption.

Die fiktive Agonie darf **niemals** zu echter Gerätebeschädigung, Sicherheitsdeaktivierung, absichtlicher Falschinformation oder Eingriffen in Notfallfunktionen führen.

## 14. Tests und prüfbare Akzeptanzkriterien

### 14.1 Unit-Tests

- B-Press: Cancel wird **vor** jedem Hold-Feedback ausgeführt.
- Hold < 800 ms → **kein** Feedback; ≥ 800 ms → genau **ein** Feedback.
- Hold bei Menüzustand, Wartung, Enrollment, Bestätigung, STT, in-flight-LLM ohne Antwort und Alarm → **kein** Feedback.
- Während LLM-Audio → exakt richtiges `turn_id`; nach Antwort innerhalb von 60 s gültig, danach nicht.
- Neuer Turn, Reboot mit gehaltener Taste, kurzzeitiger I²C-Ausfall, B/E-Gleichzeitigkeit, mehrfacher Worker-Retry.
- JSON-Validierung, Schema-Migration, Datensatzlimit, atomare Schreibweise, Stromausfall-Simulation, Entfernen/erneutes Stecken des Sticks.
- Regel ohne bestätigte Korrektur nie aktiv. Bestätigte scoped Regel wirkt nur bei passenden Anfragen. Undo entfernt die Wirkung aus lokalem und CT-107-Kontext.
- Agonie-Effekt aus → normales Feedback weiterhin aktiv; keine neue TTS nach Cancel.

### 14.2 Integrations- und Hardware-Abnahme

- Reales SHIM B mit `pi-ptt.service`: Tastenreaktion, entprellte 800 ms und Audio-Stopp **messen**; Display-/LED-Ausgabe darf I²C-Polling nicht blockieren.
- WM8960-Ausgabe, Bluetooth-Ausgabe, lokale Piper-Ausgabe und CT-107-WAV prüfen.
- Spracherkennung für Korrekturbefehle mit echter Stimme, keine synthetischen Audiodateien als alleiniger Beleg.
- Session-Digest invalidiert bei neuen/gelöschten Regeln; OpenRouter-/lokaler Qwen-Pfad liefern konsistente Kontexte.
- Offline-Tests: nur Pi, Pi + CT 107 ohne Internet, ohne Trainingsserver, Gedächtnisstick absent/reinsert.

### 14.3 Modell-Evaluation (spätere Phase)

**Vor** jedem Training Evaluationssuite mit stabilem Testkatalog definieren. Vergleich: bisheriges Basismodell vs. Kandidat bei möglichst gleichen Einstellungen. Prüfen: Befolgung korrigierter Präferenzen **und** Regression außerhalb des Trainingsgebiets, Faktentreue, Sicherheit, Persona, Sprechtextqualität, CPU-Latenz/RAM. Kein Erfolgsversprechen anhand kleiner Datensätze.

**Release-Kriterien:** Keine kritischen Sicherheits-/Datenschutzregressionen, keine OOM, messbare Verbesserung auf vorher festgelegtem Zielkriterium, Regressionen innerhalb vorab vereinbarter Toleranzen, manueller Freigabeschritt und getesteter Rollback.

## 15. Observability und Betrieb

Strukturierte, personenbezogen sparsame Ereignisse:

- `feedback_candidate_ready`
- `feedback_recorded` / `feedback_not_persisted`
- `feedback_explained` / `rule_proposed`
- `rule_approved` / `rule_revoked`
- `agony_simulated`
- `training_export_created`
- `training_run_started` / `training_run_finished`
- `candidate_evaluated` / `model_promoted` / `model_rolled_back`

Logs enthalten **IDs, Zeitstempel, Status und Fehlercode**, aber standardmäßig **keinen vollständigen persönlichen Gesprächsinhalt**. Akzeptanztests dürfen synthetische Fragen/Antworten verwenden.

Geplante Diagnosefragen: „Wie viele Korrekturen hast du gelernt?“, „Welche Regeln sind aktiv?“, „Ist der Trainingsserver verfügbar?“; nicht behaupten, der zweite Server würde kontinuierlich laufen.

## 16. Umsetzung in kleinen PRs (Claude-Arbeitsplan)

| Phase | PR-Inhalt | Fertig, wenn ... |
|---|---|---|
| **P0 – Baseline** | Repos/Branchzustand prüfen, Tests für B-Abbruch, Menü, STT/LLM/Status und SHIM-Simulation sichern | bestehende Tests grün, Regression reproduzierbar |
| **P1 – Feedback Capture** | Turn-IDs/Kandidaten und B-Long-Press-Logik, Feature-Flag, strukturiertes Event | B stoppt sofort; exakt eine richtige Bewertung |
| **P2 – Persistenz** | Versioniertes `feedback.json`, CRUD/Undo, Stick-Ausfälle, Limits | über Neustart stabil; fehlender Stick ist ehrlich sichtbar |
| **P3 – Korrektur & Regeln** | Erklärung, Regelvorschlag, Bestätigung, Scoped-Regeln, Prompt-Anwendung, Widerruf | nächster passender Turn reagiert; kein Lernen ohne Bestätigung |
| **P4 – Agonie-UX** | `Mood`-Integration, PiTFT/LED, optionaler Sound, Menü-Schalter | Effekt ist abschaltbar, bricht Cancel nicht |
| **P5 – Dataset** | Positiv-/Negativpaare, Freigabe/Redaktion/Export, Testdataset | keine unfreigegebenen Privatdaten im Export |
| **P6 – CPU-Pilot** | Zweiter Proxmox, VM, isoliertes SFT/LoRA mit kleinem Modell, Benchmark | RAM/Zeit reproduzierbar ermittelt; keine Produktionsabhängigkeit |
| **P7 – Kandidat** | optional Qwen3-4B SFT/LoRA bzw. DPO nach Messungen; Eval, GGUF, Registry, Rollback | Modell nur manuell und sicher promotbar |

**Abhängigkeitsregeln:** P4 darf nicht Voraussetzung für P1–P3 sein. P5 erst nach verlässlicher Einwilligungslogik. P7 erst, wenn CPU-Benchmark und Datenqualität P6 tragfähig zeigen. Keine riesigen PRs und keine Doku-Dopplungen.

### 16.1 Vorschlag für neue/angepasste Dateien

**Bestehende Dateien gezielt ändern:** `src/ptt.py`, `src/memory.py`, `src/mood.py`, `src/llm.py` sowie passende vorhandene Tests. Die genaue Einbindung von Statusdisplay/LED anhand aktueller Implementierung überprüfen.

**Neu, nur wenn sinnvoll:**
- `src/feedback.py`: schlanke testbare Kernlogik für Kandidat, Langdruck, Feedback-Record, Regeln.
- `tests/test_feedback.py`: deterministische Tests (Fake Clock, Fake Memory, Fake Buttons).
- `server/training/` erst in **P6**: Skripte für redigierte Exporte, Trainingspipeline, Eval und Modellmanifest.

**Nicht** schon im MVP: neue externe Datenbank, zweite unabhängige Gedächtnisimplementierung, WebSocket-Komplettumbau, zusätzlicher Container für jeden kleinen Teil, unnötige Kopie der gesamten Projekt-Dokumentation.

## 17. Offene Produktentscheidungen

Diese Startwerte sind **Vorschläge**, keine vorausgesetzten Nutzerentscheidungen:

1. **Agonie-Sound:** standardmäßig aus, optional kurzer synthetischer Glitch – ohne erneute TTS-Ansage.
2. **Korrekturfrage:** maximal einmal bei nächster geeigneter Interaktion oder nur nach explizitem Sprachbefehl? MVP bevorzugt einmalige Nachfrage.
3. **B-Haltezeit:** initial 800 ms; nach realem Hardwaretest konfigurierbar.
4. **Bewertungsfenster:** initial 60 s; kein Kandidat nach neuem Turn.
5. **Autorisierung:** zunächst ein Hauptbediener mit sichtbarer Bestätigung am Gerät, später echte Mehrbenutzer-Geltungsbereiche; Stimmprofil nicht als alleinige Sicherheitsschranke.
6. **Feedback-Limits und Aufbewahrung:** 200 Ereignisse, 20 aktive Regeln, 30 Tage für unbestätigte Bewertungen als Startwert.
7. **Training Node:** exakte i5-Variante, physische Kerne, SSD und Temperatur vor Hardware-/Laufzeitzusagen messen.
8. **Trainingsexport:** getrennte Zustimmung je Datensatz/Eintrag, ausdrücklich deaktiviert als Default.

## 18. Claude-Startauftrag (kopierfertig)

> Du arbeitest am Repository `0b-ivan/pi-voice-assistant`. Lies zuerst die aktuelle README, die tatsächlichen Python-Dateien, Tests und dieses Konzept `docs/PALS-KONZEPT.md`. Beachte, dass PR #81 die Dokumentation konsolidiert; ändere niemals blind veraltete Pfade oder die produktive Installation. Implementiere **ausschließlich P0 und P1** aus Abschnitt 16: sichere zunächst die bisherige B-Abbruch- und Menüfunktion durch Tests ab und ergänze einen konfigurierbaren B-Langdruck ab 800 ms, der nach sofortigem Cancel eine zuvor eindeutig identifizierte LLM-Antwort höchstens einmal negativ bewertet. Füge nötige Turn-IDs und Fake-Clock-Tests hinzu, aber **noch keine Trainingspipeline, keine neuen Netzwerkendpunkte, keine automatische Lernregel, keine Persona-Animation**. Sei bei Menü, Wartung, Enrollment, Sicherheitsbestätigung, STT ohne Ergebnis und Status/Alarm strikt rückwärtskompatibel. Erstelle einen kleinen Pull Request mit Testnachweisen, überprüften Dateiänderungen, dokumentierten Risiken und Hardware-Abnahmeplan. Kein automatischer Merge/Deploy.

## 19. Technische Referenzen

- Projektrepository: https://github.com/0b-ivan/pi-voice-assistant
- Bestehende Konsolidierung: https://github.com/0b-ivan/pi-voice-assistant/pull/81
- Frühere Feedback-Idee: https://github.com/0b-ivan/pi-voice-assistant/pull/82
- Qwen3-4B-Instruct-2507: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- Hugging Face Transformers/PEFT: https://huggingface.co/docs/transformers/peft
- TRL SFT/DPO-Datenformate: https://huggingface.co/docs/trl/dataset_formats
- TRL DPO: https://huggingface.co/docs/trl/dpo_trainer
- llama.cpp Modelle und GGUF: https://github.com/ggml-org/llama.cpp/blob/master/docs/models.md

---

**Entscheidungsregel:** Erst muss Proximus **zuverlässig stoppen und konkrete, widerrufbare Präferenzen speichern**. Erst danach lohnt sich echtes Fine-Tuning. Eine dramatische Agonie-Animation ist optional; Lernqualität, Datenschutz, Offline-Fähigkeit und kontrollierbarer Rollback haben Vorrang.
