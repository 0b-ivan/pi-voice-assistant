# Tatsächlich ausgeführte Validierung

Stand der Überarbeitung: 11.10.2026, Draft-PR #92. Referenz wird nicht von der Runtime importiert. Basis `main` bei `aa6cba0`, einschließlich Hardware-Intents aus PR #90.

| Prüfung | Ergebnis |
|---|---:|
| `reference/test_conversation.py` | 33 bestanden |
| `tests/test_memory.py` | 9 bestanden |
| `tests/test_protocol.py` | 8 bestanden |
| `tests/test_llm.py` | 16 bestanden |
| `tests/test_intents.py` | 18 bestanden |
| `tests/test_lore.py` einschließlich Dialog-Hinweisen | 24 bestanden |
| Insgesamt | **108 bestanden** |
| `git diff --check` | keine Fehler |

Wesentliche neue Verhaltensprüfungen: ursprüngliches Reiseziel nach mehr als zwölf Runden; passende ältere Quellen; geändertes Ziel ersetzt altes und alte Quelle darf nicht zurücküberschreiben; Quellenzitate entsprechen Nutzertext; Inaktivität und zurückgestellte Uhr löschen nichts; Ownertrennung; Snapshot-/Mount-/Epoch-Barrieren; getrennte Wiedergabezustände; Löschung einschließlich abhängiger Runden/Karten; Bytequoten nur auf eigenen Bestand; Unicode-Header und Modellbudget-Callback.

Vorhandene Tests verwenden ihre bisherigen Mocks; kein echter Cloud-/lokaler Modellaufruf ausgeführt. Der Budget-Callback wurde simuliert, nicht durch einen realen Tokenizer ersetzt. Speicherprüfung benutzt temporäre Verzeichnisse und den bestehenden MemoryCore, keine Pi-SD-/USB-Hardware und keinen tatsächlichen Mountwechsel.

Nicht geprüft bzw. noch nicht implementiert: echte Sprecherzuordnung, Tickettransport und SPX-Capabilities, Migration und ownerweite Löschtransaktion, archivierte Threadfortsetzung, dir-fd-/Kernel-Mountraces, Audio-Callbacks, Modell-Updatequalität, realer vollständiger Tokenrequest und Pi-Latenz/RAM. Die dazu erforderliche Runtime-Abnahme steht im Konzept und im Claude-Auftrag. Keine fertige Runtime-Funktion, kein Deployment und kein Merge behauptet.
