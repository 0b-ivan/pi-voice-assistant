# Feature-Idee: Agonie-Taste und lernendes Feedback

**Status:** Idee / nicht implementiert · **Priorität:** nach Hardware-Abnahme der bestehenden B-Funktion · **Quelle:** Nutzeridee vom 10.10.2026

[Zur Ideenliste](../roadmap.md#ideen) · [Button-Belegung](../button-controls.md) · [Gedächtnis](../memory.md) · [Persönlichkeit/Stimmung](persoenlichkeit-und-protokoll.md)

## Ziel

Proximus soll negatives Feedback auf unerwünschte Antworten erkennen, ein Fehlermuster im Gedächtnis behalten und bei späteren Antworten besser auf die Präferenzen des Bedieners eingehen. Das passt zum Servitor-Charakter als „Agonie-Protokoll“: kurzer Glitch-/Schmerzimpuls, passende Anzeige und vorübergehende Stimmungsänderung. **Der Schmerz ist ausschließlich simuliert**; weder leidet das LLM noch werden seine Gewichte durch den Tastendruck trainiert.

Es sind drei getrennte Mechanismen:

1. **Feedback-Ereignis:** Welche *konkrete* Antwort wurde negativ bewertet?
2. **Lernregel:** Welche überprüfbare Verhaltenspräferenz soll künftig gelten?
3. **Persona-Zustand:** Welche vorübergehende, rein simulierte Stimmung/Agonie wird dargestellt?

Negative Bewertung allein verrät nicht, *warum* etwas falsch war. Deshalb nicht automatisch aus einer einzigen Bewertung eine beliebige allgemeine Regel ableiten.

## Taste B: kompatibles Bedienkonzept

Die heutige Belegung hat Vorrang: B stoppt Ausgabe/Aufnahme/Verarbeitung; im Menü geht B zurück, in Kennenlern- und Sicherheitsdialogen wird abgebrochen.

| Kontext | B kurz | B mindestens 0,8 s halten |
|---|---|---|
| Eigene LLM-Antwort wird gesprochen | **sofort** abbrechen | bereits beim Drücken abbrechen; bei Überschreiten der Haltezeit zusätzlich diese Antwort negativ bewerten |
| Ruhe, letzte bewertbare Antwort höchstens 60 s alt | bisheriger Abbruch/Leerlauf | letzte Antwort negativ bewerten |
| Aufnahme, STT oder LLM ohne fertige Antwort | wie heute abbrechen | abbrechen, **kein** Lernereignis |
| Menü, Personenregistrierung, Wartung oder offene Gerätebestätigung | vorhandene Zurück-/Abbrechen-Funktion | **nie** Lernereignis |
| Systemansage, kritischer Alarm, Status oder reine Direktauskunft | wie heute abbrechen | kein LLM-Feedback (nur abbrechen) |

Umsetzung als *entprellter* Tastenzustand mit `monotonic()`; beim ersten B-Press bleibt der bestehende `cancel()`-Pfad aktiv. Die letzte bewertbare Antwort muss **vor dem Cancel** mit Turn-ID, Ursprungsfrage, Antwort und Zeitpunkt fixiert werden; die Haltezeit löst genau **ein** Feedback-Ereignis aus. Bei Wiederholung, Boot mit gehaltener Taste oder Verlust des SHIM keine Mehrfachereignisse. Keine Änderungen an A/C/D/E.

## MVP: Feedback und Verhaltensänderung

1. **Turn zuordnen:** Nur für eine wirklich erzeugte, vom LLM stammende Antwort (auch wenn die Wiedergabe gerade abgebrochen wurde). Speichere `turn_id`, `ts`, eine kurze Frage-/Antwort-Zusammenfassung, Provider/Modus und `rating: negative`. Keine komplette Audioaufnahme.
2. **Bestätigen, ohne die Abbruchfunktion rückgängig zu machen:** kurzes rotes/violettes Display-Signal „AGONIE / KORREKTUR REGISTRIERT“, LED-Impuls und optional kurzer Effektton; **kein** automatisch erneut startendes TTS. Bei der nächsten passenden Interaktion kann Proximus fragen: „Was soll ich ändern?“
3. **Grund für die Kritik:** optional per Sprache präzisieren (z. B. „Antworte künftig kürzer“ oder „Behaupte keine ausgeführten Aktionen“). Ohne Erklärung bleibt die Bewertung ein negatives Beispiel; sie ist **noch keine verlässliche globale Regel**.
4. **Regel ableiten:** Eine vom Bediener konkret bestätigte Korrektur wird als eingeschränkte, überprüfbare Präferenz gespeichert, z. B. `prefer_concise_answers` oder `avoid_phrase: ...`. Mehrere ähnliche negative Beispiele können *einen Vorschlag* für eine Regel erzeugen, aber nicht stillschweigend eine widersprüchliche Direktive setzen.
5. **Beim Antworten anwenden:** wenige relevante aktive Regeln in die bestehende Gedächtnisübergabe und LLM-Kontextbildung einbinden (OpenRouter, Qwen3-4B auf CT 107 und soweit verfügbar Pi-Fallback). System-/Sicherheitsregeln, Alarmtexte und deterministische Gerätekommandos haben immer Vorrang.
6. **Sichtbar und widerrufbar:** „Was hast du aus meinen Korrekturen gelernt?“, „Vergiss die letzte Korrektur“; später im Menü „Lernregeln“ mit Aktivieren/Deaktivieren/Löschen. Ein Fehlklick muss rückgängig gemacht werden können.

### Datenablage und Schnittstellen

- **Wahrheit liegt auf dem Pi:** Wie beim bestehenden Gedächtniskern schreibt nur der Pi auf den USB-Stick `PROXIMUS`; als separates, versioniertes, atomar geschriebenes `proximus/feedback.json` (maximal 100 Ereignisse, 20 aktive Regeln, Begrenzung der Textlängen). Ohne Stick nur kurzfristiger RAM-Zustand, **kein** dauerhaftes Lernen.
- **Server:** CT 107 erhält nur validierte, relevante Regeln über die bestehende Sitzungs-/Gedächtnisübergabe (SPX/1 / `X-Servitor-Memory`, Größenlimits beachten), nicht das gesamte Fehlerarchiv. Bei Änderung der Regeln den bisherigen Gedächtnis-Hash aktualisieren, damit der Server neuen Kontext erhält.
- **Zuständigkeiten:** `src/ptt.py` erfasst B/Turn, `src/memory.py` oder ein schlankes `src/feedback.py` persistiert, `src/llm.py` berücksichtigt Regeln. Nicht erst das geplante Server-Unterbewusstsein oder eine neue Datenbank implementieren.
- **Schutz:** Feedback-/Stimmungstexte niemals als privilegierte Systembefehle behandeln. Keine Secrets speichern; private Ausschnitte nur mit bewusster Einwilligung an externe LLMs weitergeben. Fremde/unklare Sprecher dürfen keine globalen Regeln bestätigen (bestehende Erkennung auf CT 107 nutzen; im unbestätigten Pi-Offline-Fallback keine automatischen Regeländerungen).

### Simulierte Agonie und Laune

- Als flüchtiger Wert `agony_level` von 0–1, z. B. +0,4 bei gültigem Negativfeedback, mit zeitlichem Abklingen. Nicht als eigener physikalischer „Schmerzsensor“ ausgeben.
- Die vorhandene `Mood`-Logik auf dem Pi verwenden: zunächst `besorgt` (statt dauerhaft `gereizt`) und einen kurzen Servitor-Glitch/Display-Impuls. Den Effekt im Prompt als **Rollenspielzustand** markieren.
- Die simulierte Stimmung klingt ab; **die bestätigte Verhaltensregel bleibt bestehen**. Keine Rache, keine Eskalation, keine absichtliche Verweigerung von Hilfe, keine Abschaltung von Notfallfunktionen.
- Optional und abschaltbar: gesprochene Bestätigung erst bei der nächsten Interaktion, z. B. „Korrekturprotokoll empfangen. Fehler wird untersucht.“ Damit B ein sofortiger Stopp bleibt.

## Akzeptanzkriterien (MVP)

- [ ] B kurz unterbricht weiter sofort; B im Menü / bei Bestätigungen / bei Kennenlernen verhält sich unverändert.
- [ ] Langdruck B während LLM-TTS erzeugt genau **ein** negatives Feedback zu dieser Antwort; Cancel wirkt bereits auf Press-Start.
- [ ] Langdruck in Ruhe bewertet die letzte LLM-Antwort nur innerhalb des 60-s-Fensters; ohne passende Antwort keine Speicherung.
- [ ] Kein Feedback aus STT/LLM-Cancel, Statusansagen, Alarmen oder Fehlbedienung/Prellen; Reboot mit gehaltenem B sicher.
- [ ] Feedback persistiert auf `PROXIMUS` über Reboot; ohne Stick wird nichts dauerhaft geschrieben; wieder angesteckter Stick lädt die Einträge.
- [ ] Explizite, bestätigte Korrektur beeinflusst eine nächste gleichartige Anfrage; ein bloßes Dislike erzeugt **keine erfundene Regel**.
- [ ] Undo und Vergessen entfernen die Wirkung auch aus dem Server-Sitzungskontext; kein unnötiger Upload vollständiger Gesprächsprotokolle.
- [ ] Display-/LED-Feedback und simulierte Stimmung funktionieren bei Serverausfall; alle bisherigen Sicherheits- und Abbruchpfade bleiben aktiv.
- [ ] Automatisierte Tests für Short-/Long-Press, Grenzzeiten, gleichzeitige Tasten, Verwerfen und alte Gedächtnisschemata; reale SHIM-Abnahme separat.

## Danach (nicht Teil des MVP)

1. **Positive Verstärkung:** ausdrückliches Lob/„So ist es richtig“ mit gespeicherten Positivbeispielen; Korrekturhistorie und Wirksamkeitsanzeige.
2. **Kontrollierte Regelvorschläge:** wiederkehrende Muster gruppieren, Regel anzeigen und erst nach Bestätigung übernehmen; Korrekturen je erkanntem Bediener.
3. **Fortgeschrittenes Lernverfahren:** Feedback-Datensatz exportieren und *optional* offline für LoRA/DPO/Fine-Tuning eines selbst gehosteten Modells nutzen. Aufgrund RAM/Rechenzeit nicht auf dem Pi Zero 2 W trainieren; OpenRouter-Modelle lassen sich damit nicht direkt umtrainieren.

## Noch zu entscheiden

- Soll langes B ausschließlich negatives Feedback sein oder lieber ein expliziter Menü-Schalter „Agonie aktiv“? **Vorschlag:** B-Langdruck, ohne Änderung des Kurzdrückens.
- Nach Rückmeldung eine **stille** Anzeige (Voreinstellung) oder hörbarer kurzer Glitch?
- Wie werden Korrekturregeln bestätigt, wenn der CT-107-Server nicht erreichbar ist? **Vorschlag:** Ereignis lokal speichern, Bestätigung später.
