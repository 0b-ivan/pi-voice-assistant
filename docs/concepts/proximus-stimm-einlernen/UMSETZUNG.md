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

## Etappe 3 — Profile und Kalibrierung: umgesetzt (ohne Laufzeit-Anbindung)

Iststand: v1-Profile liegen als `voice/profiles/<namens-slug>.json` (plus erste Fassung `voice/voiceprint.json`), adressiert über den Namen, ohne ID, mit int8-Mittelwert und Lebenszeit-Zähler `count`. Verschiedene Namen mit gleichem Slug landen in derselben Datei.

| Änderung | Datei |
|---|---|
| V2-Schema (`schema_version` 2, stabile `profile_id` als UUID, Anzeigename, Modell + SHA-256, Vorverarbeitungs-Version, Dimension, Codec `f32le-b64`, Status `legacy/provisional/calibrated/validated`, drei Zentren `near/far/quiet`, ≤ 18 Anker, 6 zurückgehaltene Prüfproben, ≤ 24 Ergänzungen, Kalibrierungsergebnis, Revision) mit strikter Validierung (NaN, Nullnorm, Dimension, Größenlimits) | `src/voice_profile.py` |
| Vergleich `s_p(x) = max` über die Zentren; `decide()` liefert `matched/unknown/ambiguous/unverified/unavailable`, nur für kalibrierte Profile mit eigener Schwelle, `delta` 0,05, kein Ausweichen auf das zweitbeste Profil, inkompatible Modelle/Vorverarbeitung/Dimension werden übersprungen | `src/voice_profile.py` |
| `loo_scores()`: jeder Anker gegen Zentren aus allen *anderen* Ankern, mit dem vollständigen Vergleich | `src/voice_profile.py` |
| `calibrate()`: auf gespeicherten und wieder geladenen Vektoren; `tau = max(Fremd) + 0,03`; `calibrated` nur mit ≥ 20 Fremdproben von ≥ 5 Quellen, ≥ 95 % LOO-Treffern (bei 18 = 18/18) und allen 6 Prüfproben ≥ `tau` ohne Nachstellen; sonst `provisional` mit Grund (`too_few_impostors`, `not_separable`, `impostor_too_close`, `checks_failed`, `checks_missing`) | `src/voice_profile.py` |
| `cross_check()`: ganzer Entscheidungsweg über mehrere Personen (richtig, falscher Name, abgewiesen, Fremder angenommen) | `src/voice_profile.py` |
| `ProfileStore`: eine Datei je `profile_id` unter `voice/profiles-v2/`, atomares Schreiben mit fsync, vorige Revision als `<id>.prev.json`, Rückfall darauf bei beschädigter Datei, Umbenennen ohne ID-Wechsel, Löschen inkl. Vorrevision | `src/voice_profile.py` |
| Migration v1 → v2 nur in eine Kopie: alter Print wird **ein** Legacy-Zentrum (keine drei Kopien), v1-Daten bleiben unverändert, vorhandene ID bleibt, fehlende ID wird einmalig vergeben und vor dem Profil atomar in `voice/profile-ids.json` gesichert; Bericht ohne Namen, mit Slug-Kollisionen. Status `legacy`: ohne bekanntes Modell nie automatisch verglichen | `scripts/voice-profile-migrate.py` |

Die laufenden v1-Profile und `MemoryCore.save_voiceprint()` bleiben unverändert; V2-Dateien liegen in einem eigenen Verzeichnis, der v1-Lebenszeit-Mittelwert kann sie nicht verändern. Server und Pi nutzen V2 noch nicht: Übertragung und Anbindung folgen mit dem Core-Body-Pfad (Etappe 4).

Migration ausprobieren (Kopie des Sticks, nie der Stick selbst):

```bash
cp -a /mnt/proximus-memory/proximus /tmp/stick-kopie
python3 scripts/voice-profile-migrate.py /tmp/stick-kopie /tmp/stick-v2
```

Tests: `tests/test_voice_profile.py` (Codec und Ablehnungen, LOO schließt den Prüfclip aus und baut Zentren neu, getrennte/überlappende/vertauschte Verteilungen, fehlende oder zu wenige Fremdquellen, Prüfproben ohne Nachstellen, Kalibrierung auf gespeicherten Vektoren, `score == tau`, kein Ausweichen auf niedrigere Zweitschwelle, nahe Profile uneindeutig, Modell-/Vorverarbeitungs-/Dimensionsfehler und Nullnorm, Mehrpersonen-Prüfung, doppelte Anzeigenamen, Umbenennen, unterbrochener und beschädigter Schreibvorgang, Limits, Legacy-Rundlauf, Migrationsskript lässt die Quelle byte-gleich und hält IDs stabil). Gesamtlauf: 915 Tests grün, 1 übersprungen.

Befund aus den Tests: Mit synthetischen Stimmen nahm ein Profil, das gegen 20 Fremdproben kalibriert war, einen neuen Fremden an. Das bestätigt Abschnitt 9: 20 Fremdproben garantieren nichts für neue Stimmen; `cross_check()` macht solche Fälle nur sichtbar.

## Etappe 4 — Generator und Einlern-Dialog: umgesetzt

| Änderung | Datei |
|---|---|
| Satzgenerator: Muster und Wortlisten mit korrekten Artikel-/Adjektivendungen, kuratiertes Lexikon mit Lautklassen, Plan aus 24 Sätzen (je Bedingung 2 kurz, 4 mittel, 2 lang; 6 Prüfproben mit eigenen Mustern, die letzten zwei als Challenge mit vier unabhängig gezogenen Wörtern), Abdeckung des ganzen Inventars, Ersatzsätze, zwölf Baseline-Paare mit gezogenen Werten, Normalisierung, WER, Inhaltsprüfung; Produktion mit `secrets`, Tests mit festem Seed | `src/enroll_phrases.py` |
| Einlern-Dialog als Zustandsfolge: Einführung, Raummessung, Stimmteil mit sofortiger Prüfung (Qualität → Transkript/WER → Embedding), höchstens eine Wiederholung je Satz und drei insgesamt, ≥ 90 s Sprache (kurze Anker werden durch lange ersetzt), optionale Baseline und Fakten nur bei genug Restzeit, Profilbildung und Kalibrierung gegen `reference-v2.json`, Bestätigung per E, harte Grenze 420 s; Serveraufrufe im Hilfsthread, B beendet sofort, späte Antworten werden verworfen; Entwurf im Stick-Verzeichnis `drafts/`, das bei jedem Ende gelöscht wird | `src/voice_enroll.py` |
| Commit: V2-Profil (ID und Name bei „ergänzen“ erhalten), v1-Abdruck derselben Person ersetzt (nicht verrechnet, nicht bei fremder ID), Fakten erst jetzt, angenommene Aufnahmen mit 20-MB-Grenze | `src/voice_enroll.py`, `src/memory.py` |
| Server: `POST /v2/enroll/clip` (Qualität, Transkript, Float32-Embedding, Modell-ID mit SHA-256; nur RAM, ein Modelllauf zur Zeit), `PUT /v2/session/<id>/core` (nur kalibrierte Profile, ≤ 5, ≤ 2 MiB, strikte Prüfung), `X-Servitor-Voice` in der Anfrage; ein kalibrierter V2-Kern entscheidet allein, ein fehlender oder fremder Kern gibt die Gastansicht und eine Aufforderung zum erneuten Senden; Feature `voice2` im Welcome | `server/servitor_server.py`, `src/protocol.py`, `src/voice_profile.py`, `src/speaker.py` |
| Pi: sendet den V2-Kern nur bei Änderung; bestätigt der Server ihn nicht (alter Server, verlorene Session), gehen nur eine Gastkopie und keine Termine hinaus | `src/remote_turn.py`, `src/ptt.py` |
| Bedienung: Menü „Personen“ mit „Stimme kennenlernen“, „Stimme ergänzen“, „Fakten kennenlernen“, „Wakeword aufnehmen“; Sprachbefehl öffnet nur das Menü; E bestätigt in der laufenden Sitzung; Display zeigt `Stimme n/24`, Bedingung, Satz, Fortschritt; altes `enroll.Session` nur noch für Wakeword-Aufnahmen, `_refine()` und `REFINE_MATCH` entfernt | `src/menu.py`, `src/ptt.py`, `src/display.py`, `src/enroll.py` |

Nebenbei gefunden und behoben: Nach einem Server-Neustart kam die erste Anfrage mit veralteter Session an; das Gedächtnis fiel korrekt weg, der Kalender-Header wurde aber weiter ausgewertet (auch im v1-Weg). Jetzt bleiben Termine bei unbekannter Session oder fehlendem Stimmkern ungenutzt.

Tests: `tests/test_voice_enroll.py` (Plan, Abdeckung, Längen, Challenge-Wörter, Ersatzsätze, Baseline, WER und Inhaltsprüfung; Sitzung mit falscher Uhr: vollständiger Durchlauf, Kalibrierung mit Referenzstimmen, Wiederholung mit neuem Satz, Wiederholungsgrenzen, keine Bestätigung, Deadline, B in jeder Phase, B während hängender Serveranfrage < 300 ms, zu wenig Sprache, Modellwechsel, „ergänzen“ behält ID und Namen, Baseline bleibt außerhalb, Testtexte erzeugen keine Fakten, nur Fakten, Server weg), `tests/test_voice_transport.py` (Kern einmal gesendet, kalibrierte Stimme erkannt, fremde/kurze Stimme Gast, verlorene Session ohne Rückfall und ohne Kalender, fremder Digest, alter Server, Endpunkt-Prüfungen inkl. NaN und Größe, Clip-Endpunkt, Kern vom Stick), `tests/test_people.py` (Menüeinträge, Sprachbefehl, E/B, Display). Gesamtlauf: 949 Tests grün, 1 übersprungen.

Bekannte Grenzen:

- Aufnahme mit fester Länge je Satzklasse (5/7/10 s, `arecord -d`); Endpunkterkennung nach 500 ms Stille fehlt noch.
- Die Inhaltsprüfung vergleicht auf dem Pi mit dem Satz, den der Pi selbst erzeugt hat; Challenge-Sitzungen mit Solltext aus der Server-Sitzung folgen in Etappe 5.
- Ohne Fremdproben bleibt jedes V2-Profil „vorläufig“; der Alltag läuft dann weiter über den (jetzt aus den neuen Ankern gebildeten) v1-Abdruck. Ein Weg zum Erfassen von Fremdproben im Testmodus fehlt noch (Etappe 7).
- Wählt eine neue Person in den Fragen denselben Namen wie ein bestehender v1-Eintrag ohne ID, wird dessen v1-Abdruck ersetzt.
- Satzlängen und Zeitbudgets sind nicht am Gerät gemessen; Vosk-Erkennbarkeit der Wortlisten ist ungeprüft.

## Etappen 5–7: offen

Hardwareabnahme offen. Keine Messwerte am echten Gerät erhoben.
