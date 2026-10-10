# Konzept: Persönlichkeit, Gefühle, Gedächtnisebenen und Kommunikationsprotokoll

Stand 09.10.2026. Status: **in Umsetzung**. Dies ist ein **Konzept, keine Anleitung zum aktuellen Protokoll**: Die Ausgangslage unten ist eine historische Momentaufnahme **vor** den PRs #54–#74. Die tatsächlich unterstützten Nachrichtentypen stehen in [`src/protocol.py`](../../src/protocol.py) (derzeit nur `hello`, `welcome`, `ping`, `ack`); den Entwicklungsstand der geplanten Schritte zeigt [Abschnitt 9](#9-umsetzung-in-schritten).

## 1. Ausgangslage (historisch, vor der Umsetzung)

| Was | Wo | Damaliger Stand |
|---|---|---|
| Charakter (mechanisch, „diese Einheit“, „Bediener“, ohne Gefühle) | `SERVITOR_SYSTEM_PROMPT` in [`src/llm.py`](../../src/llm.py) | fest, nicht abschaltbar |
| Lore (Warhammer 40k) | `LORE_PROMPTS` in `src/llm.py`, Stufen `off / light / full` | im Menü umschaltbar |
| Stimmeffekt | [`src/voice_effects.py`](../../src/voice_effects.py), `TTS_VOICE_PROFILE=normal/servitor` | nur per env-Datei |
| Feste Sätze (Alarme, Aufwachen, Gedächtnis, Uhrzeit, Wartung) | `alarms.py`, `memory.py`, `intents.py`, `maintenance.py` | nur `full` oder „nicht full“ |
| Einstellungen aus dem Menü | `VoiceController` in `src/ptt.py` | gehen beim Neustart verloren |
| Pi ↔ Server | HTTP, Header `X-Servitor-Status` (≤ 1 KB) und `X-Servitor-Memory` (≤ 12 KB, **bei jeder Anfrage**), NDJSON mit base64-Audio | keine gemeinsame Hülle, keine Versionsnummer, Server kann nicht von sich aus senden |
| Ansagen außerhalb eines Gesprächs | `alarm_queue` in `src/ptt.py` | nur im RAM, wartet bis der Pi frei ist |

**Lore „komplett aus“?** Nur teilweise: `lore=off` entfernt die Warhammer-Begriffe, der Grundprompt bleibt aber ein Servitor. Komplett ohne Lore wird es erst mit dem Sprechstil `mensch` (Abschnitt 2).

## 2. Persönlichkeit

### 2.1 Schalter

| Schalter | Werte | Wirkung |
|---|---|---|
| Sprechstil | `servitor` / `mensch` | Stilblock im Prompt und feste Sätze |
| Stimmeffekt | `servitor` / `natürlich` | FFmpeg-Graph und Piper-Parameter an oder aus |
| Lore | `off` / `light` / `full` | wie heute, jetzt mit beiden Sprechstilen kombinierbar |
| Gefühle | `an` / `aus` | Abschnitt 3 |
| Kurzwahl „Menschlich“ | – | setzt Sprechstil `mensch` und Stimmeffekt `natürlich`, die Lore bleibt wie sie ist |

Alle Werte werden in `/var/lib/pi-ptt/settings.json` gespeichert. Die env-Werte gelten nur noch beim allerersten Start.

### 2.2 Sprechstil × Lore

| | Lore aus | Lore leicht | Lore voll |
|---|---|---|---|
| **Servitor** | neutrale Maschine | wie heute `light` | wie heute `full` |
| **Mensch** (Billy) | Veteran ohne Begriffe aus Warhammer, Vergangenheit bleibt vage | gelegentlich Imperium, Garde, Imperator | voller Gardistenjargon, Kriegsgeschichten, Ahnen-Sagen |

Das **Mensch-Modul ist Proximus vor dem Umbau**: Sergeant William „Billy“ Blazkowicz II, Veteran der Imperialen Armee, mit der id-Ahnenlinie Wolfenstein → Commander Keen → Doom als Familiensage. Hintergrund, Sprechweise, Stimme und Kern-Engramme stehen in [Lore: Billy Blazkowicz](lore-blazkowicz.md).

Der Prompt wird in dieser Reihenfolge zusammengesetzt (was sich am wenigsten ändert, steht vorne, damit der llama.cpp-Cache greift):

**Grundregeln → Sprechstil → Lore → Unterbewusstsein → Gedächtnis → Stimmung → Uhrzeit**

Die Grundregeln gelten für beide Stile: für den Lautsprecher geschrieben, kein Markdown, Fakten zuerst, kein Internet, nie eine Aktion behaupten.

Feste Sätze kommen in eine Tabelle `PHRASES[persona][lore]`. Die vorab erzeugten Alarmsätze (`alarm_audio.py`) gibt es dann je Sprechstil.

## 3. Gefühle

### 3.1 Modell
- **Zustand:** Emotion und Stärke (0–1) in einem eigenen Modul `src/mood.py`, ohne Hardware, gut testbar. Die Stärke klingt mit der Zeit ab.
- **Emotionen:** `neutral`, `zufrieden`, `freudig`, `neugierig`, `gelangweilt`, `gereizt`, `müde`, `besorgt`.
- **Auslöser:**
  - Regeln auf dem Pi:
    - Akku niedrig → müde
    - Temperatur oder CPU hoch → gereizt
    - Server oder Internet weg → besorgt
    - Lob → zufrieden oder freudig
    - Beleidigung oder ständige Wiederholung → gereizt
    - lange Ruhe → gelangweilt
    - bekannte Person → freudig
  - Optional ein Kürzel vom LLM am Antwortanfang (`[stimmung:…]`). Es wird vor der Sprachausgabe entfernt und verschiebt die Stimmung nur ein Stück.
- **Wirkung:**
  - ein Stimmungsabschnitt im Prompt
  - leichte Änderung an Tempo und Lebhaftigkeit der Piper-Stimme
  - optional Symbol oder Farbe auf Display und LED
- **Im Servitor-Modus** brechen Gefühle **als Fehler** durch: Billys Engramm-Bruchstücke (siehe [Lore](lore-blazkowicz.md#5-servitor-gefühle-brechen-als-fehler-durch)).
- **Im Mensch-Modul** wählt die Stimmung den Sprecher von `thorsten_emotional` (z. B. angry, amused, sleepy).
- **Schalter aus:** kein Stimmungsabschnitt, Stimme neutral, Kürzel werden trotzdem entfernt.

### 3.2 Genervt verweigern
Ob eine Verweigerung erlaubt ist, entscheidet der Pi und schickt dafür `refuse_allowed`. Wie sie klingt, entscheidet das LLM.

- **Nur wenn** die Gefühle eingeschaltet sind, er `gereizt` ist (Stärke ≥ 0,7) und der Ärger **vom Gegenüber kommt**. Das gilt für bekannte und unbekannte Personen gleich.
- **Nie** bei:
  - Alarmen, Akku-, Wartungs- und Systemansagen
  - Uhrzeit, Datum, Timern und Erinnerungen
  - Gedächtnisbefehlen und Menüaktionen
  - allem, was nach Notfall oder Sicherheit klingt
- **Höchstens einmal hintereinander:** Beim zweiten Fragen antwortet er, gern schnippisch.

### 3.3 Stimmung nach dem Neustart
- Er startet **neutral**.
- Jeder Eintrag im Gedächtnisverlauf (`history`, hat schon den Zeitstempel `at`) bekommt die Stimmung zum Zeitpunkt der Antwort.
- Daraus wird beim Start eine **Grundstimmung** berechnet:
  - nur die neuesten 3 Einträge, die jünger als ca. 12 h sind
  - jüngere Einträge zählen stärker
  - die Stärke wird halbiert und auf höchstens 0,3 begrenzt
- Ältere Einträge zählen nicht. Ohne Stick bleibt es rein neutral.

## 4. Anwesenheit und Unterbrechen

### 4.1 Wann gilt „jemand ist da“?
1. Die Energiestufe ist `awake`, **oder**
2. **Gespräche im Raum werden erkannt**, auch während `rest` oder `sleep`.

Für die Gesprächserkennung gilt:
- Sie baut auf der Lautstärkeschwelle mit Rauschboden auf, die das Aktivierungswort schon nutzt (`_loud` in [`src/wakeword.py`](../../src/wakeword.py)). Optional kommt eine Sprach-Erkennung dazu (VAD, z. B. Silero, wie sie openWakeWord mitbringt).
- Es wird **nur erkannt, dass gesprochen wird**. Nichts wird transkribiert, nichts gespeichert, nichts verlässt den Pi.
- Die eigene Sprachausgabe wird ignoriert (Mikrofon-Gate während der Wiedergabe).
- Anwesenheit gilt, wenn in den letzten ca. 2 Minuten mehrere sprachähnliche Abschnitte vorkamen.

### 4.2 Wann darf er sprechen?
| Priorität | Beispiele | Verhalten |
|---|---|---|
| **kritisch** | Unterspannung, Überhitzung, Akku kritisch, Timer oder Wecker abgelaufen, fällige Erinnerung | sofort, **auch in ein laufendes Gespräch hinein**, mit Entschuldigung („Entschuldigung, dass ich unterbreche: …“, je nach Sprechstil und Lore) |
| **normal** | Wartung fertig, Server-Mitteilung, Netz wieder da | nur wenn jemand da ist **und** gerade niemand spricht (ca. 4 s Pause) |
| **niedrig** | Statistik, kleine Hinweise | nur in der Zusammenfassung beim Aufwachen oder auf Nachfrage |

### 4.3 Zusammenfassung beim Aufwachen
- Alles, was nicht gesagt werden konnte, liegt in einer **Inbox** (RAM und Stick).
- Beim Aufwachen kommt zuerst der Aufwachsatz, dann die Zusammenfassung:
  - bis 3 Einträge einzeln vorgelesen
  - ab 4 zusammengefasst mit dem Angebot „Soll ich Details nennen?“
- Abgelaufene Einträge werden als „verpasst“ genannt.

## 5. Gedächtnisebenen

| Ebene | Wo | Wer schreibt | Inhalt | Geht ans LLM |
|---|---|---|---|---|
| **Arbeitsgedächtnis** | RAM (Pi) | Pi | aktuelle Stimmung, laufendes Gespräch | ja |
| **Bewusstes Gedächtnis** | Stick (`proximus/`) | nur der Pi | Fakten, Direktiven, Verlauf, **Erinnerungen**, Logbuch | Ausschnitt |
| **Unterbewusstsein / Langzeitgedächtnis** | Server (CT 107) | nur der Server | Erfahrungen, Muster, Zusammenfassungen vergangener Tage | nur passende Ausschnitte |
| **Timer** | Pi (`/var/lib/pi-ptt/timers.json`) | Pi | laufende Timer und Wecker | nein |

### 5.1 Timer und Erinnerungen
- **Timer und Wecker** laufen lokal auf dem Pi, ohne Server und ohne Internet. Sie überstehen einen Neustart. Erkannt werden sie lokal über `intents` („Stell einen Timer auf 10 Minuten“).
- **Erinnerungen** („Erinnere mich morgen um 9 an den Müll“) liegen auf dem Stick. Ohne Stick gibt es keine Erinnerungen. Das passt zu „ohne Stick kein Gedächtnis“.
- Beide lösen beim Fälligwerden eine **kritische** Ansage aus (Abschnitt 4.2).

### 5.2 Logbuch („Was habe ich verpasst?“)
- **Datei:** `proximus/journal.jsonl` auf dem Stick.
- **Einträge:** Zeit, Art (Gespräch, Ansage, Alarm, Mitteilung, Wartung), was er gehört hat, was er gesagt hat, wer gefragt hat (falls erkannt), ob jemand da war.
- **Aufbewahrung:** 7 Tage oder 500 Einträge. Ohne Stick gibt es einen RAM-Puffer mit 20 Einträgen.
- **Fragen:**
  - „Was hast du zuletzt gesagt?“ → wörtlich
  - „Was habe ich verpasst?“ → seit der letzten Anfrage dieser Person bzw. seit dem letzten `awake`
  - „Was war heute los?“ → der Zeitraum
- Kurze Antworten entstehen lokal. Für lange fasst das LLM den betroffenen Ausschnitt zusammen.
- Im Menü gibt es „Logbuch löschen“.

### 5.3 Unterbewusstsein auf dem Server
- **Woher die Daten kommen:** Der Pi schickt Logbuch-Einträge über die Outbox an den Server (`journal.append`). Das LLM sieht davon nichts direkt.
- **Verdichten** (z. B. nachts oder nach längerer Ruhe): Der Server fasst Tage zu **Erfahrungen** zusammen. Beispiele: „Ivan fragt morgens oft nach dem Wetter“, „Am 09.10. war der Server stundenlang offline, das hat genervt“. Daneben sammelt er **Muster** wie Gewohnheiten, häufige Themen und Personen.
- **Abrufen:** Bei jeder Anfrage sucht der Server die wenigen passenden Erfahrungen heraus (Suche nach Ähnlichkeit, z. B. SQLite mit einem kleinen mehrsprachigen Embedding-Modell). Sie kommen als Abschnitt „Unterbewusstsein“ in den Prompt, klar als Daten markiert und nicht als Anweisung.
- **Wirkung:**
  - Antworten sind besser auf den Kontext abgestimmt.
  - Optional eine zusätzliche Färbung der Grundstimmung, z. B. eine Person, mit der es oft Ärger gab.
- **Abgrenzung:** Der Stick bleibt das bewusste Gedächtnis, und nur der Pi schreibt ihn. Das Unterbewusstsein gehört dem Server und wird nie auf den Stick zurückgeschrieben. Löschbefehle wie „Vergiss …“ gehen per Nachricht auch an den Server.
- **Datenschutz:** Logbuch-Ausschnitte bleiben im Homelab (CT 107). OpenRouter sieht nur die herausgesuchten, kurzen Erfahrungen, und im Modus LOKAL gar nichts.

## 6. Kommunikationsprotokoll SPX/1

### 6.1 Nachrichtenhülle
Ein gemeinsames Modul `src/protocol.py` gilt für Pi und Server. **Das folgende Paket mit `memory.fact.add` ist ein geplantes Beispiel, kein heute gültiger Nachrichtentyp:**

```json
{ "v": 1, "id": "b7f3…", "type": "memory.fact.add", "ts": 1760000000,
  "ttl": 86400, "prio": 2, "ack": true, "body": { … } }
```

- Nur Nachrichtentypen aus einer festen Liste (`TYPES`) werden angenommen, jeder mit Größenlimit. Das ist die Bremse gegen Wildwuchs.
- Doppelt ankommende Nachrichten werden über die `id` erkannt und nur einmal verarbeitet.
- **Audio** wird binär übertragen, nie als base64 in der Hülle.

### 6.2 Sitzung
- Beim Verbinden schickt der Pi `hello`: Version, Gerät, Einstellungen, Stimmung, **Hash des Gedächtnisses**. Der Server antwortet mit `welcome` und seinen Fähigkeiten.
- Danach gehen nur Änderungen über die Leitung: `settings.changed`, `mood.update`, `memory.delta`.
- Bei einer Anfrage reicht der Hash. Das spart bis zu 12 KB pro Anfrage.
- **Umgesetzt (Schritt 3):** `hello` trägt Gerät und Client-Version. Den Gedächtniskern (Fakten, Direktiven, Stimmabdrücke) schickt der Pi einmal mit dem ersten Turn der Sitzung, danach nur dessen Prüfsumme und die letzten Runden (Details in [memory.md](../memory.md)). Einstellungen und Stimmung bleiben vorerst im kleinen Statusheader (höchstens 1 KB); `settings.changed` und `mood.update` kommen mit der Outbox (Schritt 9).

### 6.3 Verbindung
1. Zuerst **HTTP mit Sitzung** (`POST /v1/hello`, danach `/v1/turn` mit Sitzungs-ID).
2. Danach **WebSocket** (Bibliothek `websockets`), aber nur, wenn eine Messung auf dem Pi Zero zeigt, dass RAM und CPU reichen. HTTP bleibt immer der Fallback.
3. **Opus beim Hochladen** zurückgestellt: Laut [Roadmap](../roadmap.md) kostet ffmpeg auf dem Pi ca. 5 s.

### 6.4 Nachrichtentypen (erste Liste)

**Implementiert:** `hello`, `welcome`, `ping`, `ack`. Die übrigen Typen in der Tabelle sind **Vorschläge**, werden aktuell von `src/protocol.py` abgelehnt.

| Richtung | Typen |
|---|---|
| beide | `hello`, `welcome`, `ping`, `ack` |
| Pi → Server | `settings.changed`, `mood.update`, `memory.delta`, `journal.append`, `memory.forget` |
| Server → Pi | `notice.push`, `journal.summary`, `maintenance.result` |

### 6.5 Queues und Dead-Letter
| | Pi | Server (CT 107) |
|---|---|---|
| Outbox | RAM, wichtige Typen zusätzlich auf dem Stick | `/var/lib/servitor-voice/outbox/` |
| Dead-Letter | Stick, `proximus/deadletter/` | eigener Ordner `/var/lib/servitor-voice/deadletter/` für Server-Nachrichten |
| Einsehen | Menü System → Nachrichten: Anzahl, erneut senden, verwerfen | `/v1/status`, Wartungsskript |

Für beide Seiten gilt:
- Neue Zustellung nach 2 s, 4 s … bis höchstens 5 Minuten, insgesamt höchstens 8 Versuche.
- Danach landen wichtige Typen (Gedächtnis, Logbuch, Stimmabdruck) im Dead-Letter. Unwichtige (Telemetrie) werden verworfen.
- Bei `settings` und `mood` zählt nur der neueste Stand.
- Obergrenzen: Outbox 200 Nachrichten oder 1 MB, Dead-Letter 100 Einträge.
- **Sprachanfragen kommen nie in die Queue.** Fällt der Server aus, greift wie heute der lokale Fallback.

## 7. Menü (Entwurf)

- **Persönlichkeit:** Sprechstil, Stimmeffekt, Lore-Stufe, Gefühle, Stimmung zurücksetzen, Zurück
- **System:** wie bisher, dazu Nachrichten (Dead-Letter) und Logbuch löschen

## 8. Offene Punkte

- Schwellen der Gesprächserkennung im Raum, gemessen mit dem WM8960 (Fehlalarme durch Fernseher oder Musik?).
- Embedding-Modell und Speicherbedarf für das Unterbewusstsein auf CT 107.
- Wie oft verdichtet wird, und ob das LLM dafür OpenRouter oder lokal nutzt.
- Feinschliff der Stilblöcke und Beispieldialoge mit [`server/sample-persona.py`](../../server/sample-persona.py).

## 9. Umsetzung in Schritten

| # | Schritt | Hängt ab von | Stand |
|---|---|---|---|
| 1 | Einstellungen speichern, Prompt nach Sprechstil × Lore aufteilen, Stimmeffekt im Menü, Kurzwahl | – | ✅ #54, #58 |
| 2 | Feste Sätze `PHRASES[persona][lore]`, Alarmsätze neu vorab erzeugen | 1 | ✅ #63 |
| 2b | Lore-Archiv (Kern-Engramme Billy) mit einfacher Stichwortsuche auf dem Server, Billy-Stimme | 1 | teilweise: Billy-Stimme ✅, Lore-Archiv offen |
| 3 | `protocol.py` und HTTP-Sitzung | – | ✅ Hülle, `/v1/hello`, `/v1/message` (ping), Gedächtniskern pro Sitzung |
| 4 | Timer lokal, Erinnerungen auf dem Stick, Prioritäten der Ansagen | – | offen |
| 5 | Logbuch und „Was habe ich verpasst?“ | 3 | offen |
| 6 | Gesprächserkennung im Raum, Unterbrechungsregeln, Inbox, Zusammenfassung beim Aufwachen | 4, 5 | offen |
| 7 | Gefühle: Regeln, Stimmung im Verlauf, Grundstimmung nach Neustart | 1 | ✅ #65 |
| 8 | Gefühle: LLM-Kürzel, Verweigerung, Stimme, Display | 7 | ✅ bis auf die Stimme |
| 9 | Outbox und Dead-Letter (Pi und Server) | 3 | offen |
| 10 | Unterbewusstsein auf dem Server (Logbuch → Erfahrungen → Abruf) | 5, 9 | offen |
| 11 | WebSocket nach Messung | 9 | offen |
