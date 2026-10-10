# Recherche und erneute Architekturprüfung

Überarbeitung für PR #92, 11.10.2026. Primärquellen auf GitHub und bei den jeweiligen Projekten; abgerufen während dieser Überarbeitung. Verlinkte Hauptbranches können sich verändern. Keine Projektmarketing-Benchmarks als Beweis für Proximus-Leistung übernommen. Kein fremder Quellcode kopiert; die Referenz implementiert übernommene Muster eigenständig.

## Vergleichbare Lösungen und sinnvolle Übernahme

| Lösung / Primärquelle | Beobachtetes Muster | Eigene Anpassung für Proximus |
|---|---|---|
| [LangGraph: Memory-Konzept](https://docs.langchain.com/oss/python/concepts/memory) | Trennung zwischen Gesprächszustand und längerfristigem Gedächtnis; JSON-Einträge in Namespaces mit eigenen Schlüsseln | Stabile Profil-ID plus Gesprächs-ID; separate Fakten, Arbeitsstand und Verlauf |
| [LangChain: Short-term memory](https://docs.langchain.com/oss/python/langchain/short-term-memory) | Persistierter Threadzustand, Trimmen vor Modellaufruf, Zusammenfassung und Tokenbudget als unterschiedliche Strategien | Auf dem Stick mehr speichern als im Prompt; letzte Paare plus relevante ältere Quellen; Modellbudget zusätzlich zur Headergrenze |
| [Home Assistant: Conversation ChatLog](https://github.com/home-assistant/core/blob/dev/homeassistant/components/conversation/chat_log.py) | `conversation_id` bindet einen Chatlog an eine konkrete Unterhaltung; einzelne Inhalte und Lebenszyklusereignisse sind getrennt | Gesprächs-ID unabhängig von Netzwerk-Sitzung und Persona; Generierung und Audioauslieferung getrennt erfassen |
| [Mem0: Add](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/add.mdx), [Delete](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-operations/delete.mdx), [Implementierung](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py) | Rohaufnahme ohne Inferenz (`infer=False`), nach Nutzer-/Run-ID eingeschränkte Operationen, Löschung bestimmter IDs | Originaltexte behalten; alle Zugriffe nach Profil begrenzen; gezielte Quelllöschung mit abhängigen Ableitungen |
| [Letta: Memory Architecture](https://github.com/letta-ai/skills/blob/main/letta/agent-development/references/memory-architecture.md) | Begrenzter stets sichtbarer Kern, durchsuchbares Archiv und Gesprächshistorie erfüllen unterschiedliche Aufgaben | Kleiner Arbeitsstand im Kontext; ältere Runden lokal durchsuchen, nicht vollständig mitsenden |
| [Graphiti: README und Architektur](https://github.com/getzep/graphiti/blob/main/README.md) | Herkunft aus Episoden und zeitliche Gültigkeit; neue Werte können alte Angaben ablösen | Aktueller Arbeitsstand ersetzt belegte alte Werte; ursprüngliche Runde bleibt historische Quelle. Löschung entfernt Quelle und Ableitungen |

Diese Übernahmen sind eigene Architekturentscheidungen. Keines der Projekte belegt, dass exakt unsere Grenzen, Sprecherprüfung oder Auswahl auf einem Pi Zero 2 gut funktionieren. Das muss die Integration prüfen.

## Entscheidungen aus der ersten Fassung, die ersetzt werden

| Erste Fassung | Problem bei genauer Prüfung | Neue Entscheidung |
|---|---|---|
| Sechs ältere Auszüge à 240 Zeichen als „Zusammenfassung“ | Keine eigentliche Zusammenfassung; Ziel verschwindet nach kurzer Zeit, Details gehen durch Kürzen verloren | Bis 48 Originalrunden je Thread, maximal 3 Threads je Profil; kleines Arbeitsblatt mit belegten Quellen und deterministische Auswahl |
| Nach 12 Stunden neue normale Frage setzt den Bestand zurück | Zeitgrenze vernichtet Kontext unabhängig von Nutzerabsicht | Zeit steuert nur den automatischen Abruf. Fortsetzung/konkrete alte Themen bleiben möglich; keine implizite Löschung |
| „Neues Gespräch“ und „Vergiss Gespräch“ identisch | Gesprächswechsel und Löschung haben unterschiedliche Bedeutung | Neues Gespräch archiviert; Vergessen löscht. Archiv nur auf passenden ausdrücklichen Bezug abrufen |
| „Vergiss Hamburg“ setzt alle Gespräche zurück | Zerstört unabhängig gespeicherte Inhalte | Passende Quellen identifizieren; abhängige Runden/Arbeitsstand entfernen. Ohne Treffer nichts löschen; keine semantische Vollständigkeit vortäuschen |
| Mehrere bekannte Profile deaktivieren die Funktion pauschal | Vermeidet das eigentliche Zugriffskonzept und macht ein vorhandenes Feature unbrauchbar | Eigene Namespaces mit stabilen Profil-IDs und Erkennung vor Auswahl. Nur uneindeutige/ungeprüfte Identität sperren |
| Älteste Fakten immer zuerst aus dem Export entfernen | Ein alter Name oder aktuelles Reiseziel kann wichtiger sein als neue Nebensachen | Fakten vor Auswahl nach Relevanz sortieren; getrennte Prioritäten, aktuelle Runde und Arbeitsstand schützen |
| Headergröße als ausreichendes Kontextbudget | Transport kann passen, während das konkrete Modell überfüllt wird | Vollständige Modellnachrichten einschließlich Persona, Lore, Werkzeugdaten und Ausgabereserve zählen |
| Commit erst bei Server-`done` und Audio nicht klar getrennt | Ein TTS-Fehler verliert eine gültige Antwort; Netzwerkereignis beweist kein Hören | Generierte Antwort committen, danach `pending/played/interrupted/failed` erfassen; passende Wiederholungsregel |
| Duplicate-ID-Fenster als Hauptschutz | Späte Antworten nach Reset/Löschen können Daten erneut erzeugen | Snapshot mit Profil, Thread, Epoch, Revision und Mount-Generation plus Duplicate-Erkennung |

## Bewusst nicht übernommene Komponenten

- **Komplette LangGraph-/Letta-/Mem0-Runtime:** Unsere vorhandenen Python-Module können die benötigten Muster ohne neue Abhängigkeiten nutzen. Der Funktionsumfang rechtfertigt keinen parallelen Agentenstack.
- **Graphdatenbank/Embedding-Suche:** Für höchstens 48 Runden pro Thread ist lineare Wortsuche zunächst nachvollziehbar und begrenzt. Sie löst keine Synonyme automatisch; bei schlechten realen Dialogtests kann später serverseitige Suche ergänzt werden.
- **Zusammenfassungsaufruf nach jeder Runde:** Zusätzliche Latenz, Kosten und möglicher Fehlerpfad. Optionaler Arbeitsstand wird im selben normalen Antwortaufruf als validierter Quellen-Selektor geliefert. Ohne Unterstützung bleibt die Auswahl aus Originaltexten verfügbar.
- **SQLite/FTS als Pflicht:** Für diesen begrenzten Bestand zunächst vorhandene atomare JSON-Datei verwenden. Das ist eine Abwägung, keine generelle Empfehlung gegen SQLite. Falls Messung der ganzen JSON-Neuschreibung die Turn-Latenz beeinflusst, wäre eine auf dem Stick liegende SQLite-Datei mit klarer Migration der nächste überprüfbare Schritt.
- **Unbegrenzte Archivierung:** Speichergrenzen sind sichtbar dokumentiert; Quoten gelten pro Profil. Ein Profil darf nicht heimlich den Verlauf eines anderen verdrängen.

## Noch offene Qualitätsfragen

Quellenprüfung beweist, dass eine Notiz auf echten Nutzertext verweist, aber nicht, dass das Modell diesen korrekt als Ziel/Termin interpretiert. Die optionale Extraktion braucht echte Dialogtests und bleibt bei Fehlern abgeschaltet. Wortsuche findet exakte Begriffe gut, keine beliebigen Paraphrasen. Die erste Anfrage als Rückfallanker kann nach echtem Themenwechsel irrelevant werden; daher neue Threads ausdrücklich oder nach klarer eindeutiger Themensteuerung eröffnen, nicht aufgrund einer ungetesteten Heuristik.

Sprachidentifikation ist keine unfehlbare Authentifizierung. Für mehrere Profile braucht das bestehende einphasige Uploadprotokoll einen Identifizierungsschritt vor dem persönlichen Kontextabruf; die konkrete Integrationslösung und ihre zusätzliche LAN-Latenz stehen im Konzept. Alte globale Fakten sind dadurch noch nicht automatisch auf Personen verteilt.

Erfolgskriterien sind gemessene korrekt aufgelöste Folgefragen, korrektes Ersetzen alter Angaben, zuverlässiges Löschen und Zugriffstrennung. Höhere Testzahl allein beweist keine bessere Gesprächsqualität.
