# GPT-Auftrag: Stimm-Einlernen v2, Etappen 5–7

Setze [KONZEPT.md](KONZEPT.md) ab Etappe 5 fort. Etappen 1–4 sind umgesetzt und getestet; Stand, Befunde und Grenzen stehen in [UMSETZUNG.md](UMSETZUNG.md). Lies beide zuerst, dann gezielt die unten genannten Dateien. Übergabestand: Branch `claude/stoic-hypatia-pjaaep`, PR [0b-ivan/pi-voice-assistant#100](https://github.com/0b-ivan/pi-voice-assistant/pull/100), Basis main `97151af`, 11.10.2026; CI grün, 949 Tests lokal grün (1 übersprungen: Stimmmodell fehlt außerhalb von CT 107).

Weiterarbeiten auf demselben Branch (die PR aktualisiert sich) oder, falls #100 inzwischen gemergt ist, auf einem neuen Branch von main. Nie direkt auf main pushen. Kein Deployment, kein Neustart produktiver Dienste, keine Migration produktiver Stick-Daten.

## Was schon da ist (nicht neu bauen)

| Baustein | Datei | Kernfunktionen |
|---|---|---|
| Gemeinsamer Audio-Pfad | `src/voice_quality.py` | `read_wav`, `speech_mask` (WebRTC-VAD oder Energie), `speech_seconds`, `trim`, `analyze` (nur Zahlen), `problems`, `HINTS`, `VERSION` |
| v1-Entscheidung, gehärtet | `src/speaker.py` | `decide()` mit `matched/unknown/ambiguous/insufficient/unavailable`; `Embedder.model_info()` (SHA-256) |
| V2-Profile | `src/voice_profile.py` | Schema + `validate`, Codec f32, `centers_of`, `score`, `loo_scores`, `calibrate`, `decide` (nur kalibriert, eigene Schwelle, `delta`, kein Ausweichen), `cross_check`, `ProfileStore` (atomar, Vorrevision), `migrate_legacy`, `voice_core`/`read_core` (Transport) |
| Satzgenerator | `src/enroll_phrases.py` | `plan`, `replacement`, `challenge`, `baseline`, `normalize`, `wer`, `in_order`, `check` |
| Einlern-Dialog | `src/voice_enroll.py` | `VoiceSession` (Modi `new/add/facts`), Deadline 420 s, B/E, Entwurf, Commit |
| Server | `server/servitor_server.py` | `_identify` (v1) / `_identify_v2`, `enroll_clip` (`POST /v2/enroll/clip`), `do_PUT` (`/v2/session/<id>/core`), `_voice` (`X-Servitor-Voice`), Welcome-Feature `voice2` |
| Pi-Transport | `src/remote_turn.py`, `src/protocol.py` | `_send_voice`, Gastkopie ohne Termine bei unbestätigtem Kern; `Sessions.set_voice/voice` |
| Pi-Steuerung | `src/ptt.py` | `_start_enroll`, `_enroll_target`, `_offer_enroll`, `voice_profiles`, `voice_core`, `_voice_unchecked`, `_turn_memory`, `turn_guest`; `EnrollIO.clip/publish` |
| Gastansicht | `src/memory.py` | `guest_view(context, status)`, `public_directive`, `enrolled`, `replace_voiceprint` |
| Migration (nur Kopie) | `scripts/voice-profile-migrate.py` | v1 → v2 legacy, IDs stabil in `voice/profile-ids.json` |

Verbindliche Zustände, die erhalten bleiben müssen: Jeder Fehlerweg ist Gast; der lokale Pi-Rückfall mit eingelernter Person ist Gast; ein kalibrierter V2-Kern entscheidet allein, ein fehlender oder fremder Kern gibt die Gastansicht; ohne bestätigten Kern verlassen weder Fakten noch Termine den Pi; bei unbekannter Session nutzt der Server keine Termine.

## Etappe 5 — Challenge und Aktionsfreigabe

Ziel (Konzept Abschnitt 7 und 11): Erkannte Person, bestandener Challenge-Inhalt und Berechtigung sind getrennte Ergebnisse. Persönliche Inhalte (Erinnerungen, Kalender) und Änderungen (Fakten, Kalender, Stimmprofil) verlangen eine frische Challenge **und** eine lokale Bestätigung mit Taste E für genau diese Aktion.

Umsetzen:

1. Server: Challenge-Sitzungen im RAM (`POST /v2/enroll/session`, `POST /v2/enroll/session/{id}/challenge`, `POST /v2/enroll/session/{id}/clip?challenge_id=…`, `DELETE /v2/enroll/session/{id}`) bzw. für den Alltag eine Challenge an eine einmalige Aktions-ID gebunden. Solltext nur aus der Server-Sitzung (Text mit `enroll_phrases.challenge()` serverseitig erzeugen, Client liefert keinen „Beweis“). Challenge-ID an Sitzung, Zielprofil und Aktion binden, nach einem Versuch verbrauchen, TTL 30 s mit `time.monotonic()`, kurze TTL für verwaiste Sitzungen, alles bei `DELETE` sofort verwerfen.
2. Prüfung: `enroll_phrases.check()` (≤ 5 Wörter exakt, sonst WER ≤ 0,15 und alle gezogenen Wörter in Reihenfolge); TTS- oder Signalton-Ausgabe im Aufnahmefenster ablehnen.
3. Pi: Die Freigabe kommt nur aus der echten Geräte-UI (Taste E im Controller), nie aus einem Feld einer Sprachantwort. Bestehende E-Bestätigungen (Fahrten, Gerätesteuerung, Wartung in `src/ptt.py`, `src/journeys.py`, `src/device_control.py`, `src/maintenance.py`) als Vorbild nehmen. Gedächtnisbefehle, Kalender und Profiländerungen gehen über diesen Weg; `manual` (lokal gewähltes Profil) als eigener Zustand, nie „an der Stimme erkannt“.
4. `VoiceSession`: die zwei Challenge-Aufgaben am Ende des Stimmteils auf den Server-Solltext umstellen.

Tests (Pflicht): passende/unpassende Texte, Einfügungen/Löschungen, Zahlennormalisierung, korrektes WER bei falschem Prüfwort, alte ID, falsche Sitzung/Person/Aktion, abgelaufene Aufgabe, doppelte Nutzung, Ausgabe im Aufnahmefenster. Korrekte Challenge ohne lokale Bestätigung löst keine geschützte Aktion aus; hohe Sprecherähnlichkeit ebenso wenig. Abschluss: Ein Faktenspeicher-Versuch bleibt ohne Gerätefreigabe gesperrt, unabhängig vom CAM++-Score.

## Etappe 6 — Nachlernen und Datenhaltung

Ziel (Konzept Abschnitt 8 und 9): Ergänzungsproben nur nach Taste E, mit festen Grenzen gegen Profilverschiebung.

Umsetzen: Kandidaten nur aus eindeutig erkannten Anfragen mit ≥ 3 s Sprache und bestandener Qualität; Score gegen die **unveränderlichen Ankerzentren** ≥ `tau + 0,08`, Abstand zum Zweitbesten ≥ `delta + 0,05`; nichts aus Gast, unklar, zu kurz, eigener TTS-Ausgabe oder Challenges. Anzeige „Stimme ergänzen · E übernimmt · B verwirft“. Höchstens 2 pro Tag, 24 insgesamt (`extras` in `voice_profile`), Ersetzung pro Bedingung, Anker und Prüfproben bleiben; neue Zentren höchstens 20 % Gewicht aus Ergänzungen; vor Übernahme alle Prüf- und Fremdproben erneut auswerten (keine neue Fremdannahme, keine Verschlechterung), sonst verwerfen; Schwelle nie automatisch senken; atomare Revision mit Rückkehr. Automatische Übernahme standardmäßig aus. Numerische Messlogs nur auf dem Stick (30 Tage), Server-Caches bei Sitzungsende, Stick-Abmeldung und TTL löschen; Löschen einer Person entfernt v1-Datei, V2-Profil inkl. Vorrevision, `v2-audio/<id>/` und Einträge in `profile-ids.json` (heute löscht `MemoryCore.delete_profile` nur die v1-Datei).

Tests (Pflicht): kein Lernen aus Gast/unklar/zu kurz; Score gegen feste Anker statt adaptive Zentren; Tageslimit, Ersetzung, 20-%-Grenze, keine Schwellenabsenkung, neue Fremdannahme verhindert Commit, Rollback, Stickverlust, vollständige Profillöschung; keine Inhalte in Logs oder persistenten Server-Dateien (HTTP-/Transcript-Logs dieses Pfads einschließen).

## Etappe 7 — Vorbereitete Hardwareabnahme

Ziel (Konzept Abschnitt 9): lokal ausführbare Prüfanleitung und Berichtsvorlage; Abnahme nur, wenn echte Hardware und Testpersonen verfügbar sind, sonst ausdrücklich „Hardwareabnahme offen“ melden und keine Werte erfinden.

Umsetzen: ein Testmodus zum Erfassen von Fremdproben mit ausdrücklicher Herkunftsangabe nach `voice/reference-v2.json` (`[{source, vector, model_sha256}]`, wird von `voice_enroll._impostors` gelesen; Gastaufnahmen nie heimlich sammeln); ein Skript, das eingefrorenes Profil/Modell/VAD/Schwellen gegen die Abnahmeversuche laufen lässt und nur Zahlen ausgibt (29/30 echt, 0/20 fremd/TTS/Replay, Challenge 0/10 und 19/20, Kurzsprache und Fehlerfälle); Ressourcenmessung (p50/p95, CPU, RAM) auf CT 107. Optionales Zweitmodell nur als gesonderter Versuch.

## Offene Punkte aus Etappen 1–4

- Aufnahme mit fester Länge (5/7/10 s); Endpunkterkennung nach 500 ms Schlussstille fehlt (`EnrollIO.record`, `arecord -d`).
- WebRTC-VAD ist nur auf CT 107 vorgesehen (`webrtcvad-wheels` in `server/install-ct.sh`), sonst Energieschätzung.
- Grenzwerte (−35 dBFS, Pausen, SNR 12 dB, Satzlängen, Zeitbudgets) und die Vosk-Erkennbarkeit der Wortlisten sind nicht am Gerät geprüft.
- Neue Person mit demselben Namen wie ein v1-Eintrag ohne ID ersetzt dessen v1-Abdruck (`memory.replace_voiceprint`).
- Ohne Fremdproben bleibt jedes V2-Profil `provisional`; der Alltag läuft dann über den v1-Abdruck (Schwelle 0,5). Das ist eine bewusste Übergangsentscheidung, die mit dem Bediener geklärt werden sollte.

## Arbeitsweise im Repo

- Tests: `python3 -m unittest discover -s tests` (wie CI in `.github/workflows/ptt-tests.yml`), vor jedem Push grün.
- Stil wie im umgebenden Code: Python-Kommentare und Docstrings englisch, Doku und Ansagen deutsch, kurze Funktionen, kein neues großes Modell im Standardweg.
- Neue Module in `src/`, die `ptt.py` importiert, auch in `scripts/install-voice-service.sh` eintragen (`tests/test_installers.py` prüft das).
- Logs dieses Pfads: nur Zahlen und Codes, nie Namen, Profil-IDs, Transkripte, Sollsätze, Audio oder Vektoren.
- Ergebnis jeder Etappe in `UMSETZUNG.md` nachtragen (Änderungen, Tests, Befunde, Grenzen) und `README.md`/`docs/memory.md` aktualisieren.
