# Stimm-Einlernen v2: Umsetzungsstand

Bezug: [KONZEPT.md](KONZEPT.md), Abschnitt 11. Zielbranch basiert auf `97151af` (= Analysebasis des Konzepts, keine Abweichung). Kein Deployment, keine Änderung produktiver Stick-Daten.

## Etappe 1 — Iststand und Fehlerfreigabe: umgesetzt

Iststand bestätigt: alle Befunde aus Abschnitt 2 lagen im Code vor (unveränderter Kontext bei fehlendem Modell/Ausnahme/fehlendem Embedding, Längenprüfung statt Sprachsekunden, `WEAK_VOICE`-Passphrase-Freigabe, Prozentanzeige, Namen im `auth`-Log, lokaler Rückfall ohne Sprecherprüfung, `guest_view()` mit allen Direktiven). Zusätzlich: Äußerungen ohne Embedding galten ausdrücklich als Bediener, und Gastanfragen landeten im Verlauf des Bedieners.

| Änderung | Datei |
|---|---|
| `speech_seconds()` zählt Sprachframes statt Dateilänge (Energieschätzung bis zum VAD-Pfad der Etappe 2) | `src/speaker.py` |
| `decide()` liefert `matched / unknown / ambiguous / unavailable` mit bester und zweitbester Ähnlichkeit; Abstand `DELTA = 0,05` bei mehreren Profilen; kein Ausweichen auf das zweitbeste Profil | `src/speaker.py` |
| Embedding-Validierung (endlich, nicht null, Dimension ≤ 1024) | `src/speaker.py` |
| `_identify()`: jeder Fehlerweg → Gastansicht mit `identity`-Grund; `speaker`-Ereignis mit `status`; Fehlerlog nur mit Ausnahmetyp, ohne freien Text | `server/servitor_server.py` |
| `/v1/voiceprint` lehnt Aufnahmen mit < 1 s Sprache ab | `server/servitor_server.py` |
| Pi schickt `enrolled` (Zahl der Profile); fehlen verwertbare Prints, bleibt der Server streng | `src/memory.py`, Server |
| `guest_view()` gibt nur installierte Module frei (`public_directive()`) | `src/memory.py` |
| Passphrase + Stimme 0,3–0,5 wird abgelehnt; `auth`-Log ohne Namen; Details ohne Prozent | `src/people.py` |
| Lokaler Rückfall mit eingelernter Person = Gast: keine Fakten/Termine/Änderungen/gelernten Zeilen/Verlauf, kein Sprach-Kennenlernen | `src/ptt.py` |
| Gastanfragen (Server meldet `known=false`) werden nicht in den Verlauf geschrieben | `src/ptt.py` |

Tests: `tests/test_enroll.py` (Sprachsekunden, `decide`, Server-Fehlerwege: kurze Sprache in langer PCM, Stille, Brummen, kein Modell, Ausnahme ohne Logtext, kaputtes Profil, `enrolled` ohne Print, zwei nahe Profile, ohne Einlernen unverändert), `tests/test_people.py` (schwache Stimme abgelehnt, keine Namen/Prozente), `tests/test_alarms.py` (lokaler Rückfall als Gast, Gastverlauf nicht gespeichert). Gesamtlauf: 874 Tests grün.

Bekannte Grenzen dieser Etappe:

- Die Energieschätzung unterscheidet lautes Dauerrauschen nicht von Sprache; erst WebRTC-VAD (Etappe 2) löst das.
- Nach dem Abschneiden der Stille misst die Personen-Authentifizierung auf bereits gekürztem Audio; sehr leise Passphrasen können dort als zu kurz gelten.
- Ausdrückliche lokale Freigabe persönlicher Aktionen im Rückfall (Taste E, Etappe 5) fehlt noch: bis dahin sind sie ohne Server bei eingelernter Person gesperrt.
- Kurze Befehle ohne 1 s Sprache sind jetzt Gastanfragen; Gedächtnis- und Kalenderfragen als ganzen Satz sprechen.

## Etappen 2–7: offen

Hardwareabnahme offen. Keine Messwerte am echten Gerät erhoben.
