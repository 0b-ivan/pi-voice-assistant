# Auftrag: Proximus und Billy natürlich sprechen lassen

Implementiere im aktiven Repository die erweiterte Lore und bessere sprachliche Varianz aus der beigefügten `LORE_PROXIMUS_BILLY.md`. Die umfangreiche Geschichte ist bereits ausgearbeitet. Erfinde nicht zunächst eine neue und schreibe sie nicht mehrfach um.

Die aktualisierte Vorlage enthält 33 Episoden sowie einen recherchierten Warhammer-Rahmen mit offiziellen Quellen in Abschnitten 13–15. Sie unterscheidet Weltwissen, eigene Projektszenen und gesperrtes Autorenwissen. Die Recherche nicht pauschal wiederholen; Quellen nur bei einem konkreten Widerspruch gezielt nachlesen.

Abschnitt 17 ergänzt sechs Sageneinträge S01–S06: klassisches Wolfenstein, The New Order und Doom als altes Familienerbe. Die Rahmenszenen erweitern E04. Persönliche Erinnerung, Familienüberlieferung und Sachwissen über die Spiele auseinanderhalten. Die klassische Familienwurzel bleibt erhalten; MachineGames liefert eine zusätzliche Erzählfassung, keinen neuen bestätigten Stammbaum.

Abschnitt 16 enthält die zusätzliche ausdrückliche Nutzerpräferenz: Lange Geschichten müssen auch ungefähr 10–20 Minuten gesprochen werden können. Für diesen Erzählauftrag sind längere Ausgabe, passende Kontextpakete und nötige automatische Fortsetzungsaufrufe erlaubt. Die kurze Alltagsantwort bleibt die Voreinstellung.

## Kontext, damit du gezielt starten kannst

Die geprüfte Projektkopie enthält:

- `docs/concepts/lore-blazkowicz.md`: bestehende Billy-Lore und verbindliche Entscheidungen.
- `docs/concepts/persoenlichkeit-und-protokoll.md`: Persona, Gefühle und geplante Funktionen. Einige Abschnitte sind Konzepte, keine bereits implementierten Fähigkeiten.
- `src/llm.py`: `SERVITOR_SYSTEM_PROMPT`, `BILLY_SYSTEM_PROMPT`, `LORE_PROMPTS`, `LORE_POOLS`, `lore_hint`, `system_prompt`, Cloud-/Lokalanfragen.
- `src/memory.py`: bereits vorhandene begrenzte Gesprächshistorie, Stick-Abhängigkeit und Zugriffsregeln.
- `src/mood.py`: Stimmungssteuerung, Durchbrüche und Gereiztheit bei Wiederholungen.
- `src/intents.py`, `src/system_status.py`, `src/alarms.py`, `src/memory.py`, `src/maintenance.py`: feste Antworten außerhalb des LLM.
- `server/servitor_server.py`, `server/sample-persona.py` sowie relevante vorhandene Tests: tatsächliche Aufrufwege und Dialogprüfung.

Prüfe kurz, ob der aktive Stand dieselben Stellen verwendet. Nutze dessen neuere bestätigte Festlegungen, falls die Projektkopie inzwischen veraltet ist. Keine alte Kopie parallel aktualisieren. Repository-Anweisungen beachten; `sources/` unverändert lassen.

## Ergebnis

1. Die 33 Episoden und sechs Sageneinträge als feste, konsistente Projekt-Lore integrieren. Ausführliche Autorenfassung erhalten; für die Runtime kompakte, thematisch abrufbare Engramme erstellen. Bestehenden Kern nicht überschreiben. Fakten und Unsicherheiten aus der Vorlage übernehmen. Neue Mikrodetails dürfen auf Anfrage eine Szene ausgestalten, aber keine neuen biografischen Eckdaten, Schicksale oder Fähigkeiten als Kanon etablieren. Die Sichtbarkeiten aus Abschnitten 15 und 17 anwenden. Proximus bekommt die redigierten Servitor-Fassungen der Sagen; Billy erinnert deren Erzähler, nicht eigene Einsätze in Doom oder Wolfenstein. Phobos IX und die Marsmonde sowie Fergus-/Wyatt-Zweige auseinanderhalten. Kaels spezielle servitorische Konstruktion nicht zur üblichen Bauweise jedes Servoschädels erklären.
2. Beide Stimmen verbessern: Billy menschlich, trocken, kompetent und manchmal geduldig oder nachdenklich; Proximus mechanisch und kontrolliert, mit flüssigem abwechslungsreichem Deutsch. Gemeinsamer Ursprung, unterscheidbare Zugänge. Gedankensprünge, unklare Bezüge und bedeutungsschwere Sätze ohne konkrete Aussage vermeiden; Antworten sollen beim einmaligen Hören verständlich sein. Keine verpflichtende Anrede, Quittung, Pointe, Anrufung oder Lore-Beimischung. Widersprüchliche alte Regeln und Beispiele ersetzen statt neue Regeln nur anzuhängen.
3. Gesprächskontext wiederverwenden, wo vorhanden. Keine ständige Wiederholung von Einstiegen und Anekdoten. Ausdrücklich erbetene Wiederholung erlauben. Rückfragen richtig auflösen. Reale Nutzerfakten müssen bei Personawechsel konsistent bleiben; alter Stil darf den neuen nicht überlagern.
4. Eine kleine deterministische Lore-Suche bauen, wenn sie fehlt: Persona/Lore-Sichtbarkeit vor Auswahl prüfen. Null bis drei passende Einträge sind eine sparsame Alltagsvoreinstellung, keine starre Inhaltsgrenze. Persönliche Fragen erhalten alle nötigen passenden Einträge; lange Geschichten zusammenhängende Episoden- oder Sagenpakete mit eigenem Kontextbudget. Vorhandenes Format mit Herkunft und Erzählart ergänzen, keine neue Archivarchitektur. Sachfragen über ein genanntes Spiel dürfen auch bei `off` Sachantworten erhalten, ohne Familien-Lore zu injizieren. Keine Vektordatenbank und kein eigener LLM-Aufruf zur Klassifikation. A01 bleibt getrenntes Autorenwissen und gelangt niemals unredigiert in den generativen Kontext. Billy darf Kael verdächtigen, aber dessen Schließbefehl nicht als gesicherte eigene Erinnerung nennen.
5. Proximus' Engramm-Durchbrüche nur mit eingeschalteten Gefühlen, passendem Anlass und bestehender Stärkevorgabe; kurz, selten und nicht in aufeinanderfolgenden Antworten. Gelöschte Kameradennamen dadurch nicht dauerhaft freischalten. Keine neue Audioinszenierung in diesem Auftrag.
6. Häufige lokale Standardsätze ebenfalls variieren, soweit sinnvoll. Kleine Variantenpools mit zuletzt verwendeten Varianten-IDs statt neuem Inhaltsgedächtnis. Alle Varianten behalten Zahlen, Status und notwendige Bestätigung. Bestehendes vorab erzeugtes Alarm-Audio beachten; Text und abgespielte Variante müssen übereinstimmen. Kritische Meldungen dürfen fest bleiben.
7. `off/light/full`, Gefühle aus, beide Personas, Cloud-/Lokalfallback und Bedienerfunktionen erhalten. Grundregeln anhand der echten Laufzeitfähigkeiten formulieren: verfügbare Daten und Werkzeuge nutzen, keine ausgeführten Aktionen oder aktuelle Messwerte erfinden. Für lange Geschichten erforderliche begrenzte Änderungen an Textgenerierung, Transport, Audioqueue und Abbruch sind ausdrücklich erlaubt. Keine neue Stimme, keine DSP-Neuentwicklung oder sachfremde Hardwareänderung.
8. Ausdrücklich lange Geschichten gemäß Abschnitt 16 umsetzen: eine gewünschte Hörzeit bis mindestens 20 Minuten unterstützen; ohne Dauer ungefähr 10 Minuten als konfigurierbaren Startwert verwenden. Automatisch in zusammenhängenden Abschnitten weitererzählen, ohne dauernd nach Fortsetzung zu fragen. Frühe Sprachausgabe, begrenzte Puffer und zuverlässigen Abbruch ermöglichen. Standard-Tokenlimits, Textkürzung vor TTS, Transportgrenzen und Timeouts im tatsächlichen Code prüfen. Die geprüfte Kopie benutzt standardmäßig 180 Cloud-/120 lokale Ausgabetokens und 1.200 Zeichen für die Synthese; eine bloße Promptänderung genügt nicht. Ganzen Monolog nicht bei jedem Fortsetzungsaufruf neu mitsenden. Keine stille Kürzung, kein Neustart von vorn nach einem Fehler und kein automatischer Wiederbeginn nach Gerätestart. Angeforderte Länge darf mehr Tokens benötigen; keine künstlichen Wiederholungen zum Auffüllen.

## Wirtschaftlich umsetzen

Lies die Lore einmal vollständig und danach nur benötigte Abschnitte. Suche gezielt und lade keine ganzen Repositorys, Logs oder alten Chatverläufe in den Kontext. Arbeite direkt, ohne lange Planungsrunde, Subagents oder sachfremde Refactorings. Keine Deployment-, PR- oder Veröffentlichungsaktion in diesem Auftrag.

Die vollständige Geschichte bleibt außerhalb des normalen Systemprompts. Kurze Charakterkerne plus relevante Engramme reichen. Stabile Promptteile zuerst, dynamische Daten danach; vorhandenes Caching beachten. Möglichst ein LLM-Aufruf pro gewöhnlicher Antwort; für ausdrücklich lange Geschichten sind notwendige begrenzte Fortsetzungsaufrufe erlaubt. Keine automatische Umschreibungsrunde und keine unbestellten Hintergrundgeschichten, die neue Ereignisse erzeugen. Ohne Stick keine neue dauerhafte oder inhaltliche Gesprächserinnerung einführen; zulässigen bestehenden RAM-Kontext gegebenenfalls nutzen. Varianten-IDs und der Zustand eines gerade beauftragten langen Vortrags dürfen flüchtig gehalten werden.

Sampling nur ändern, wenn das Modell die Parameter unterstützt und eine Prüfung den Nutzen zeigt. Mehr Temperatur ist keine Implementierung von Natürlichkeit.

## Prüfen und abschließen

Gezielte Offline-Tests für Lore-Filter einschließlich A01, Budgetgrenzen, Namensvarianten, Personawechsel, Rückfragen, fehlenden Stick und Archiv-Fallback; passende vorhandene Tests ausführen. Standardsatzvarianten auf gleiche Fakten und Bestätigungen prüfen. Bei Tests mit bislang festem Wortlaut die inhaltliche Absicherung behalten.

Kleine qualitative Dialogfolge aus Abschnitt 12 der Lore vorbereiten und die Prüfungen aus Abschnitten 15.4, 16.5 und 17.5 ergänzen. Lange Ausgabe und Abbruch mit simulierten 10-/15-/20-Minuten-Audiolängen offline testen. Echte hörbare Dauer, Sprachunterbrechung und Leistung auf der Zielhardware nur als verifiziert melden, wenn tatsächlich geprüft. Falls bereits ein nutzbares Modell samt Freigabe für Aufrufe vorhanden ist, eine begrenzte Vorher-/Nachher-Stichprobe durchführen. Sonst offline validieren und die echte qualitative Modellprüfung als offen melden. Keine kostenpflichtigen API-Aufrufe ohne bestehende Freigabe. Handgeschriebene Beispiele sind keine gemessene Modellverbesserung.

Nicht nach jeder Routineentscheidung rückfragen. Nur wenn eine notwendige Information fehlt und weder Code noch Unterlagen sie beantworten, eine konkrete kurze Frage stellen. Nach relevanten bestandenen Prüfungen fertigstellen.

Abschluss knapp: geänderte Dateien, wichtigste Verbesserungen, tatsächliche Prüfungen, offene Einschränkungen und je zwei kurze illustrative Beispielantworten. Keine vollständigen Dateien oder die ganze Lore im Abschluss erneut ausgeben.
