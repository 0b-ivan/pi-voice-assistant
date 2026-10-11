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

Bekannte Grenzen dieser Etappe (die ersten beiden mit Etappe 2 erledigt bzw. gemildert):

- Die Energieschätzung unterscheidet lautes Dauerrauschen nicht von Sprache; WebRTC-VAD ist seit Etappe 2 vorgesehen.
- ~~Die Personen-Authentifizierung maß auf bereits gekürztem Audio.~~ Seit Etappe 2 misst und schneidet der Server selbst.
- Ausdrückliche lokale Freigabe persönlicher Aktionen im Rückfall (Taste E, Etappe 5) fehlt noch: bis dahin sind sie ohne Server bei eingelernter Person gesperrt.
- Kurze Befehle ohne 1 s Sprache sind jetzt Gastanfragen; Gedächtnis- und Kalenderfragen als ganzen Satz sprechen.

## Etappe 2 — Audioqualität und Schnittstellengleichheit: umgesetzt

Iststand: Der Server hält eingehendes Turn- und Einlern-Audio bereits nur im RAM (`bytearray`/Request-Body); temporäre WAVs unter `SERVITOR_WORKDIR` entstehen nur für die eigene Sprachausgabe und werden gelöscht. `pcm_of()` las WAVs ohne Format-Prüfung; Turn und Einlernen schnitten zwar mit derselben Funktion, maßen aber Sprache nicht gleich (Etappe 1 hatte die Prüfung nur im Turn).

| Änderung | Datei |
|---|---|
| Neues Modul für den gemeinsamen Pfad: `read_wav()` (mono, 16 Bit, 8–48 kHz; Stereo gemischt, echtes Resampling mit Tiefpass und Interpolation, numpy oder reines Python), `speech_mask()` (20-ms-Frames, WebRTC-VAD wenn installiert, sonst Energieschätzung), `trim()` (150 ms Rand, ohne Sprache leer), `analyze()` (Zahlen: Sprachsekunden, Sprachanteil, längste Pause, Clipping-Anteil und -Lauf, RMS-dBFS der Sprache, SNR mit Gültigkeitsflag), `problems()` (Codes, dominanter zuerst) und `HINTS` | `src/voice_quality.py` |
| `speaker.speech_seconds()`/`trim_silence()` delegieren dorthin: Turn-Prüfung, `/v1/voiceprint`, Einlernen und Personen-Auth messen und schneiden gleich | `src/speaker.py` |
| `/v1/voiceprint` antwortet zusätzlich mit `quality` (nur Zahlen), auch bei Ablehnung | `server/servitor_server.py` |
| Einlernen: Format-Prüfung je Clip; `enroll_clip`-Ereignis nur mit Zahlen/Codes; Clips ohne Sprache oder mit falschem Format werden gelöscht; Antworten mit `too_short`, `clipping`, `too_quiet`, `noisy`, `pauses` geben weiter ihre Transkription, aber keinen Stimmabdruck; Wakeword-Proben beim Nachtrainieren mit 0,3 s Mindestsprache | `src/enroll.py` |
| Personen-Auth: unbearbeitete Aufnahme an den Server (der schneidet selbst); zu wenig Sprache zählt als Fehlversuch statt „Server nicht erreichbar“ | `src/people.py` |
| Nach dem Signalton 300 ms Abklingpause vor der Aufnahme | `src/ptt.py` (`EnrollIO.beep`) |
| WebRTC-VAD (`webrtcvad-wheels`) optional auf CT 107; `voice_quality.py` im Pi-Installer | `server/install-ct.sh`, `scripts/install-voice-service.sh` |

Tests: `tests/test_voice_quality.py` (int16-LE, Resampling mit und ohne numpy inkl. Alias-Unterdrückung, Stereo/48 kHz/falsche Formate, Stille, Pausen, Clipping, niedriger Pegel, fehlende und schlechte SNR, VAD-Auswahl, int8-Rundlauf, Modell-Selbstvergleich nur mit installiertem Modell), `tests/test_enroll.py` (gleiche PCM über Turn und `/v1/voiceprint` → identische Modell-Eingabe und Zahlen; abgelehnte Stimme hinterlässt keine Server-Dateien; schlechte Clips ohne Stimmabdruck, Log ohne Inhalt; stille/kaputte Clips gelöscht), `tests/test_people.py`. Gesamtlauf: 896 Tests grün, 1 übersprungen (Modell nicht installiert).

Gemessen (Software): Der gespeicherte int8-Abdruck verändert ein zufälliges 512-Werte-Embedding um bis zu ~0,0014 Kosinus. Kalibrierung (Etappe 3) muss deshalb den gespeicherten und wieder geladenen Vektor verwenden.

Bekannte Grenzen:

- Ohne installiertes `webrtcvad` bleibt es bei der Energieschätzung (unterscheidet lautes Dauerrauschen nicht von Sprache). Auf dem Pi ist WebRTC-VAD nicht installiert; dort laufen nur Einlern-Analyse und Auth-Vorprüfung, die Entscheidung trifft der Server.
- Die Grenzwerte sind Startwerte des Konzepts, nicht am echten Mikrofon geprüft. Besonders `too_quiet` (−35 dBFS) und `pauses` können leise oder zögernde Antworten aus dem Stimmabdruck nehmen; die Zahlen stehen im Journal (`journalctl -u pi-ptt | grep enroll_clip`).
- Eine eigene Raumgeräusch-Messung fehlt im alten Ablauf; fehlende SNR wird protokolliert, hält einen Clip aber erst ab Etappe 4 zurück.
- Der Modell-Selbstvergleich läuft nur, wo das CAM++-Modell installiert ist (CT 107), nicht in CI.

## Etappen 3–7: offen

Hardwareabnahme offen. Keine Messwerte am echten Gerät erhoben.
