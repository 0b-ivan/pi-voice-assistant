# Tatsächlich ausgeführte Validierung

Stand: 11.10.2026, Draft-PR #92, einschließlich der sechs zusätzlichen Dialogfunktionen. Basis `main` bei `0e5bd33`, einschließlich PR #90, #91 und #93. Referenz wird nicht von der Runtime importiert. Abschlussprüfung gemeinsam in einem Testlauf mit Python **3.13.7**.

| Prüfung | Ergebnis |
|---|---:|
| `reference/test_conversation.py` | 33 bestanden |
| `reference/test_dialogue_features.py` | 37 bestanden |
| `tests/test_memory.py` | 9 bestanden |
| `tests/test_protocol.py` | 8 bestanden |
| `tests/test_llm.py` | 16 bestanden |
| `tests/test_intents.py` | 18 bestanden |
| `tests/test_lore.py` einschließlich Dialog-Hinweisen | 24 bestanden |
| `tests/test_transcribe.py` einschließlich Kogitator-Korrekturen | 10 bestanden |
| Insgesamt | **155 bestanden** |
| `git diff --check` | keine Fehler |

Zusätzliche Dialogprüfungen: offene Rückfragen über Nebenfragen/48-Rundenfenster, gezieltes Antworten/Überspringen, unbestätigte Audiowiedergabe, relative Datumsauflösung vor/nach Mitternacht und tatsächliches MemoryCore-Wiederladen, DST-Lücke und doppelte lokale Zeit, Quellmanipulation, Owner-Faktkorrektur plus Revision, Pause/Privatmodus über Reload, late Reply nach Pause/Resume und Korrektur, benannte/mehrdeutige/eigene Archive, tatsächliche Quellenauskunft und Command-Abgrenzung zu Gerätebefehlen/quotierten Sätzen.

Weiterhin geprüft: Originalziel und gezielte alte Quellen, aktuelle Arbeitswerte, Ownertrennung, Snapshot-/Mount-/Epoch-Barrieren, Delivery-Status, gezielte Abhängigkeitslöschung, Owner-Bytequoten, Unicode-Header und Modellbudget-Callback. Vorhandene Memory-/LLM-/Lore-/STT-Tests verwenden ihre bisherigen Mocks.

Eine erste Zusatzprüfung des bestehenden STT-Moduls wurde in einer Tool-Umgebung mit Python 3.9 gestartet und scheiterte an dessen bereits vorhandenen Python-3.10+-Typannotationen. Mit der passenden Python-3.13-Version wurde sie erfolgreich wiederholt; der abschließende gemeinsame Lauf enthält alle 155 Prüfungen. Kein Produktcode wurde dafür verändert.

Keine echten Cloud-/lokalen Modellaufrufe. Budget-Callback simuliert, kein realer Tokenizer. Speicherprüfung benutzt temporäre Verzeichnisse und vorhandenen MemoryCore, keine Pi-/USB-Hardware oder echten Kernel-Mountwechsel.

**Noch nicht fertig integriert/geprüft:** echte Sprecherzuordnung und semantische Rückfrageklassifikation, Tickettransport/SPX-Capabilities, Legacy-/Faktmigration und ownerweite Löschtransaktion, Controller-Resume, dir-fd-/Kernel-Mountraces, echte Audio-Callbacks, breite Zeitinterpretation und Uhrvertrauen, Modell-Updatequalität, vollständiges privates Logging/Audio und bestehende MERKE-/DIREKTIVE-Pfade, realer Tokenrequest sowie Pi-Latenz/RAM. Die Referenz sperrt ihre eigenen Speicherungsmethoden, nicht bereits alle bisherigen globalen Runtime-Wege.

Keine fertige Runtime-Funktion, kein Deployment und kein Merge. Die Runtime-Abnahme steht in Konzept, `DIALOGFUNKTIONEN.md` und Claude-Auftrag.
