# Arbeitsauftrag für Claude: dauerhafter Gesprächskontext

Implementiere im aktiven `pi-voice-assistant`-Repository das beigefügte `KONZEPT.md`. Lies es einmal vollständig und prüfe danach gezielt die genannten Aufrufstellen. Das Konzept wurde gegen main `b366b1f` am 10.10.2026 erarbeitet; neuer bestätigter Code hat Vorrang. `reference/conversation.py` und `reference/test_conversation.py` sind ausführbarer Beispielcode, keine bereits aktive Funktion. Nicht einfach unverändert in die Runtime kopieren: Controller-Lifecycle, Sprecherberechtigung, Stickidentität, Migration und Sessiontransport fehlen dort absichtlich als klar beschriebene Integrationsaufgaben.

## Gewünschtes Ergebnis

Proximus versteht Folgefragen, hält sechs begrenzte Runden und sechs ältere Nutzertext-Auszüge auf dem vorhandenen Erinnerungskern und lädt passenden Kontext für Cloud und lokale Antwortbildung. Neuer Kontext ersetzt keine expliziten Fakten/Direktiven. Beide Personas behalten denselben freigegebenen Gesprächsinhalt; alte Persona bestimmt nicht die neue Sprache.

Baue auf `MemoryCore`, `dialog`, `_chat`, Pi-Controller und SPX-Sitzungen auf. Keine zweite Datenbank, keine Serverpersistenz, kein SD-Fallback ohne Stick, keine Hintergrund-Zusammenfassungsaufrufe und keine Vektorsuche. Standardfall weiterhin ein LLM-Aufruf. Kennenlern-/Passphrasen-/Wartungsdaten aus diesem Speicher fernhalten.

## Verbindliche Umsetzung

- Eigenes optionales versioniertes Conversation-Feld in derselben atomaren Datei; alte Fakten/Direktiven erhalten; Migration von Legacy-History einmalig und validiert. Unbekannte Version nicht überschreiben.
- Turntext separat auf 800 Zeichen begrenzen. Sechs Runden speichern, vier senden, verdrängte Nutzertexte als maximal sechs 240-Zeichen-Auszüge mit Quell-ID/Zeit führen. Keine Modellantwort als gelerntes Nutzerfaktum behandeln.
- Gesamte exportierte JSON-Kopie auf 6000 Zeichen und fertigen Base64-Header auf 12000 Zeichen prüfen. Paarweise Kürzung, expliziter Fallback bei unpassenden festen Daten. `sanitize()` und Legacy-Reinigung aktualisieren.
- Erfolgreiche Runden einmalig über Pi-generierte Turn-ID committen; Retries dieselbe ID. Owner, Revision, Conversation-ID und Stick-Generation aus dem Snapshot prüfen; abgebrochene, veraltete oder fehlerhafte Ergebnisse nicht committen. Aktuelle remote Speicherung bei `reply` auf überprüften terminalen Lifecycle umstellen. Delivery-Status für unterbrochene Wiedergabe berücksichtigen.
- „Neues Gespräch“, „Vergiss das Gespräch“, „Lösche den Gesprächsverlauf“, „Setze unser Gespräch fort“, „Was war unser Thema?“ ohne LLM routen. Neue Befehle vor generischem Forget erkennen. Reset löscht auch Legacy-History und invalidiert SPX-Cache. „Vergiss X“ entfernt passende normale Einträge und konservativ alle Gesprächsinhalte; Ansage nennt das.
- Nach 12 h Inaktivität alten Kontext erst auf ausdrückliche Fortsetzung nutzen; neue gewöhnliche Frage ersetzt den alten Gesprächszustand. Kein automatisches Sprechen oder Aktionen beim Neustart.
- Gäste/unsichere Stimmen bekommen und schreiben keinen persönlichen Conversation-Kontext. Mit registriertem Profil lokalen Fallback ohne Stimmerkennung sperren. Mehrere Profile im MVP fail-closed für Conversation behandeln; bestehende globale Fakten nicht als bereits isoliert behaupten. Persona ist keine Sprecher-ID.
- Notizen als markierte historische Daten injizieren, aktuelle Aussage bevorzugen, Widersprüche nicht heimlich zu Gewissheit glätten. Werkzeuge/Intents bleiben für Aktionen maßgeblich. Fahrplan/Wetter/Status neu prüfen.
- SPX-Digest und Capability prüfen. Für den MVP ist volle Kopie bei verändertem Conversation-Kern in Ordnung. Alten Server mit Legacy-Export bedienen; keine einseitige Slim-Payload-Erweiterung.
- Speichererfolg ausschließlich nach lokalem Commit bestätigen. Bei Fehler nicht behaupten, gelöscht/gespeichert zu haben. Nur Metadaten loggen, keine vollständigen Kontexte oder Passphrasen.

## Prüfung und Abschluss

Referenztests und passende vorhandene Memory-/Protocol-/Controller-/Server-/Dialogtests ausführen. Ergänze vor allem Stickwechsel während Turn, Neustart/TTL/Resume, Vergessen plus Sessioncache, zwei Personas, Gast/mehrere Profile/lokaler Fallback, Duplikate, stale Reply nach Reset, read-only/voller Stick, Unicodebudget und alte Server. Prüfe bestehende Story-/Abbruch-/Enrollment-Pfade auf Regressionen. Für Routineänderungen keine überflüssigen neuen Tests, für diese persistenten Zustandswechsel sind gezielte Tests notwendig.

Qualitative Modellprüfung und Pi-Audiolatenz nur als bestätigt melden, wenn tatsächlich ausgeführt und freigegeben. Keine kostenpflichtigen API-Aufrufe, kein Deployment oder Merge ohne gesonderten Auftrag. Keine Subagents oder sachfremden Refactorings. Repository-Anweisungen beachten; `sources/` bleibt read-only.

Liefere einen nachvollziehbaren Runtime-Diff mit Tests und aktualisierter `docs/memory.md`. Abschluss kurz: umgesetztes Verhalten, Prüfresultate, verbleibende Hardware-/Modelltests und illustrative Folgefragen. Nicht das Konzept komplett in den Abschluss kopieren.
