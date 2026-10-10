# Ergänzung: sechs Funktionen für den täglichen Dialog

Verbindliche Ergänzung zu `KONZEPT.md` und zum Claude-Auftrag, 11.10.2026. Referenz in `reference/dialogue_features.py` plus Erweiterungen von `conversation.py`; keine aktive Pi-Funktion. Basis ist nun `main` `0e5bd33`, einschließlich Hardware-/Kogitator-Korrekturen aus PR #91 und #93.

## 1. Offene Rückfragen behalten und gezielt abschließen

Problem: Proximus fragt „Von welchem Bahnhof?“, danach folgen Nebenfragen. Die offene Frage kann aus den letzten vier Paaren verschwinden. Ihr Text stammt von Proximus und kann daher nicht in die bisherigen ausschließlich nutzerbelegten Arbeitsfelder übernommen werden.

Neue separate `questions`-Liste pro Thread, maximal drei Einträge. Eintrag enthält eigene ID, fachliches Feld (`start_station`), Quell-Turn, exakten Bereich in **Assistant-Text**, Zitat, Status und gegebenenfalls Antwort-Turn. Er ist Gesprächszustand und niemals persönlicher Nutzerfakt. Der normale Antwortaufruf darf ein geprüftes `open/resolve/dismiss`-Update vorschlagen. Keinen Zusatzaufruf zum Klassifizieren erzeugen.

- `open`: Eine tatsächlich in der Antwort enthaltene nötige Rückfrage wird eröffnet. Nicht jeden Fragesatz als Rückfrage klassifizieren; rhetorische und erzählte Fragen bleiben draußen.
- `resolve`: Eine neue Nutzeraussage beantwortet genau die ausgewählte Frage. „Von Berlin“ kann `start_station` erfüllen; eine CPU-Nebenfrage schließt sie nicht. Der Link beweist Zuordnung, keine erfolgreiche Aktion.
- `dismiss`: „Überspring diese Frage“ schließt ohne erfundene Antwort. Bei mehreren offenen Fragen konkret nachfragen, welche gemeint ist.
- `answered/dismissed` nicht wieder als offen injizieren. Verwandte Folgerunden bleiben Originalverlauf.

Quellen offener Fragen bei der Speicherquote bevorzugen. Bei Löschen/Verdrängen der Quelle auch Frage entfernen; keine Frage ohne Herkunft bestehen lassen. Nach Personawechsel dieselbe fachliche Frage, aktueller Stil. Thread-/Personenwechsel nimmt sie nicht mit. Bei Neustart/Speicherpause keine automatische Wiederholungsansage.

Nur eine als `played` markierte Frage darf als erfolgreich vorgelesen bezeichnet werden. `pending/failed/interrupted` im Prompt kenntlich machen; gegebenenfalls knapp erneut fragen. Auch erfolgreiche Wiedergabe beweist kein menschliches Zuhören.

**Beispiel:** Reise nach Hamburg → „Von welchem Bahnhof?“ → CPU-Nebenfrage → „Von Berlin“. Startbahnhof = Berlin, Ziel = Hamburg; kein Neustart der gesamten Reiseplanung.

## 2. Relative Zeit mit festem Bezug und Zeitzone speichern

„Morgen um neun“ bezieht sich auf den Zeitpunkt der Äußerung, nicht auf das spätere Wiederladen. Zu jeder erkannten Zeitreferenz gehören Nutzerquelle/Range, Originalwortlaut, vertrauenswürdiger Aufnahmezeitpunkt, IANA-Zeitzone, konkretes lokales Datum, Präzision und bei genauer Uhrzeit UTC-Zeit plus lokaler Offset.

Referenzparser deckt bewusst nur `heute/morgen/übermorgen`, optional `um HH[:MM] Uhr` und Stundenwörter null bis zwölf ab. Andere Angaben wie „nächsten Freitag“, „morgen früh“ oder „um neun“ ohne Tag sind noch keine geprüfte Parserfähigkeit. Nicht raten; kurze Rückfrage oder vorhandenen geprüften Zeitparser verwenden. Die Quellen-Selektoren gehören zum tatsächlich normalisierten gespeicherten Nutzertext.

- „Morgen“ ohne Uhrzeit bleibt ein **Datum**. Keine erfundene Mitternacht und kein automatischer Timer.
- Nach Neustart feste gespeicherte Auflösung verwenden; neue Äußerungen erhalten ihren eigenen neuen Aufnahmebezug.
- Systemuhr und Zone müssen brauchbar sein. Bei ungeklärter Uhr/NTP oder fehlender Zeitzonendatenbank keine angeblich genaue Auflösung speichern. Host-Zeitzone nicht heimlich übernehmen.
- Bei Sommerzeitwechsel können lokale Uhrzeiten fehlen oder doppelt vorkommen. Nicht automatisch die erste zweite Ausführung auswählen, sondern gezielt klären. Das zugrunde liegende `fold`-/IANA-Verhalten ist in der [Python-ZoneInfo-Dokumentation](https://docs.python.org/3/library/zoneinfo.html) beschrieben; die konkrete Roundtrip-Prüfung ist eigene Referenzlogik.
- Zeitzonenregel-Updates dürfen gespeicherte Auflösungen nicht still verändern. Widerspruch zur aktuellen Regelbasis ausdrücklich als Prüfbedarf behandeln.

Maximal vier Zeitreferenzen pro Turn; aufbewahren, exportieren und mit Quelle löschen. Relative Aussage allein ist kein Auftrag zum Wecker-/Kalendereintrag. Deren bestehende Berechtigung, Rückfrage und Ausführungsstatus bleiben separat.

**Beispiel:** Äußerung am 11.10.2026, lokale Zeit 23:00: „morgen um neun“ → 12.10.2026, 09:00 Europe/Berlin. Wiederladen am 12.10. verschiebt es nicht auf den 13.10.

## 3. Dauerhafte Fakten korrigieren, ohne Widersprüche anzusammeln

Aktuelle Gesprächsziele können bereits aktualisiert werden. Für dauerhafte persönliche Fakten braucht es zusätzlich eine Operation mit fachlichem Schlüssel, aktuellem Wert, bestätigter Nutzerquelle, eigener `fact_revision` und Personenbindung.

Im Referenzausbau zunächst eindeutige Einzelfelder: `name`, `home_city`, `occupation`. Keine beliebige Liste von Orten in „Wohnort“ umdeuten, keine zweite Präferenz heimlich durch die erste ersetzen. „Korrigiere meinen Wohnort auf Leipzig“ ist eindeutig. „Nein, ich wohne inzwischen in Leipzig“ darf nur als Korrektur gelten, wenn vorhandener Dialog/Quelle diese Zuordnung sicher machen; sonst kurz klären.

`correct_fact` ersetzt genau den aktuellen Wert dieses Owner-Schlüssels in einer atomaren Transaktion. Frühere Äußerungen können historische Runden bleiben, dürfen im Faktenteil nicht als gleichzeitige aktuelle Werte erscheinen. Andere Fakten/Personen bleiben erhalten. Quelle mindestens als explizit belegter kurzer Auszug und ID/Zeit erhalten; keinen ganzen privaten Turn als unbegrenzten Faktbeleg kopieren.

- Der Quellenbereich muss aus Nutzertext der gleichen Person stammen; Assistant-Vermutung genügt nicht.
- Veraltete `fact_revision` zurückweisen. Korrektur erhöht `write_epoch`, damit eine laufende Antwort mit altem Faktstand nichts zurückspeichern kann.
- Doppelte Legacy-Textfakten vor Umstellung des jeweiligen Schlüssels abgleichen. Globale Alt-Fakten nicht blind Personen zuordnen. Ohne sichere Zuordnung oder bei widersprüchlichen Altwerten einmal konkret klären.
- Im Modellkontext aktuellen Owner-Fakt vor alter Aussage behandeln. Herkunft und historischer Status müssen klar sein.
- „Vergiss Leipzig“ entfernt auch den aktuellen passenden Owner-Fakt und dessen Belegkopie. Conversation-Löschung bewahrt ausdrücklich gespeicherte Fakten wie bereits festgelegt; Ansage darf das nicht als Löschung sämtlicher persönlichen Daten darstellen.

Referenz zeigt aktuelle Fakten im Owner-Index. Der Runtime-PR muss den existierenden Faktenpfad darauf abbilden, statt parallel globalen und persönlichen aktuellen Wohnort zu verwenden. Profil-Fakten zählen zur vorhandenen Owner-Bytequote und werden bei Gesprächsverdrängung nicht still entfernt.

## 4. Speicherpause und privates Gespräch vollständig umsetzen

Zwei klare Sprachbefehle mit unterschiedlicher Reichweite:

| Befehl | Reichweite | Ende |
|---|---|---|
| „Pausiere das Gedächtnis“ | gesamte weitere persönliche Inhaltsspeicherung dieses Owners | ausdrücklich „Speichere wieder“; Neustart/Threadwechsel ändern nichts |
| „Speichere dieses Gespräch nicht“ | aktive Unterhaltung privat | ausdrücklich neues Gespräch oder „Speichere wieder“ |

Das bedeutet eine **Schreibpause**. Vorhandene freigegebene Erinnerungen dürfen weiterhin zur Antwort genutzt werden. Für völliges Abschalten von Lesen plus Schreiben wäre ein separat benannter Modus erforderlich; diesen nicht aus der Schreibpause ableiten.

Folgende Wege gemeinsam sperren: Turn-/Arbeitsstand-/Fragen-/Zeitreferenz-Commit, automatische `MERKE`/`DIREKTIVE`-Ereignisse remote und lokal, Fact-Correction, neue Gesprächstitel und sonstige contenthaltige Speicheroperationen. Explizites „Merk dir …“ während Pause führt zu einer verständlichen Meldung und speichert nicht still. Steuer-Metadaten für die Pause dürfen atomar auf dem Stick liegen; kein privater Inhalt darin.

`recording`, `private_thread`, `write_epoch` liegen im Owner-Index und überstehen Neustart. Jeder Request hält seinen Policy-Snapshot. Pause/Resume erhöhen Epoch; eine private oder vor Änderung gestartete Runde darf nach Resume nicht verspätet geschrieben werden. Ein privater RAM-Puffer wird verworfen und niemals später rückwirkend gespeichert. Fehlender Stick: Status ehrlich melden; keine behauptete dauerhaft gespeicherte Pause ohne Commit. Flüchtige Sperre kann bis Neustart wirken, muss so bezeichnet werden.

**Bestehende Logs beachten:** Der aktuelle Code schreibt Transkript-/Antworttext ins Journal und sendet `MERKE` separat. Nur `remember_turn()` zu sperren wäre unzureichend. Für den Modus private Inhalte aus Pi-/Server-Logs, temporären Audio-Dateien, Diagnoseaufzeichnungen und automatische Faktenwege fernhalten bzw. deren Lebensdauer ausdrücklich begrenzen. Bestehende unabhängige Betriebs-/Auth-Metadaten bleiben möglich. Der Befehl wirkt ab Erkennung und kann bereits vorher verarbeitete/versendete Äußerungen nicht rückwirkend ungeschehen machen.

„Nicht speichern“ stoppt auch nicht automatisch Cloudverarbeitung. AUTO verwendet weiterhin den konfigurierten Sprachkern; beim Aktivieren kurz verständlich sagen: „Ich speichere ab jetzt keinen neuen Gesprächsinhalt. Der eingestellte Sprachkern bleibt aktiv.“ Lokal bleibt die vorhandene lokale Betriebswahl. Keine absolute Nichtaufbewahrungszusage über externe Anbieter geben.

Die Referenz sperrt neue Turns und eigene Fact-/Titeloperationen. Sperren der bestehenden globalen `_learn()/MemoryCore.apply()`-/Server-/Logging-/Audio-Pfade sind ausdrückliche Runtime-Integrationsaufgaben, noch nicht erledigt.

## 5. Gesprächsarchiv benennen, anzeigen und eindeutig auswählen

- „Nenne dieses Gespräch Reiseplanung“ / „Speichere das als Reiseplanung“ benennt den aktiven Thread, erstellt keinen zusätzlichen persönlichen Fakt.
- „Welche Gespräche hast du gespeichert?“ listet bis drei eigene Threads mit Name, letztem Zeitpunkt und aktivem Zustand. Nicht alle Inhalte vorlesen.
- „Öffne das Gespräch Reiseplanung“ wählt den vorhandenen eigenen Thread und stellt dessen Arbeitsstand/offene Fragen bereit. „Öffne Reiseplanung“ nur als Kurzform, wenn der Name einem bekannten eigenen Gespräch entspricht; „Öffne das Fenster“ bleibt im vorhandenen Gerätepfad.
- Exakte ID/Name vor Teiltreffer. Mehrere passende Namen → eine kurze Auswahlfrage; kein beliebiges zuletzt gefundenes Gespräch. Titel maximal 60 Zeichen, doppelte Namen pro Person vermeiden.
- Gelöschte/verdrängte Gespräche nicht wiederherstellen oder unter ähnlichem Namen automatisch neu anlegen. Profilwechsel bietet nur dessen eigene Liste.

Öffnen erhöht `write_epoch`. Damit wird auch Wechsel A → B → A sicher: eine verspätete Antwort aus der ersten A-Nutzung darf nicht nach Rückkehr in A geschrieben werden. Explizites Öffnen gilt als Fortsetzung auch nach Ablauf der Idle-Grenze; Controller hält Abruffreigabe bis zum nächsten Erfolgs-Turn. Referenz implementiert Auswahl, nicht den kompletten Controller-Resume-Lifecycle.

Bei privatem aktivem Gespräch zunächst den privaten Zustand durch ausdrücklichen neuen Thread/Resume beenden; nicht versehentlich private RAM-Antworten in ein geöffnetes Archiv übernehmen. Benennen während Pause speichert keinen privaten Titel. Bestehende Speicherquoten gelten weiter; ein Name macht einen Thread nicht unbegrenzt haltbar.

## 6. „Woher weißt du das?“ mit echten Quellen beantworten

Proximus kann gespeicherte Fakten und Arbeitsfelder anhand ihrer Herkunft erläutern. Kein Einblick in verborgenes Modell-Denken und keine erfundene Begründung.

- „Woher weißt du meinen Wohnort?“ → aktuellen Owner-Fakt und belegten kurzen Nutzertext mit Gesprächszeit nennen.
- „Woher weißt du mein Reiseziel?“ → aktuelle `destination`-Quelle im aktiven Thread.
- „Warum glaubst du, dass ich nach Hamburg möchte?“ → prüfen, ob aktuelle Zielangabe wirklich Hamburg ist; ansonsten die Prämisse korrigieren. „Woher weißt du das?“ ohne klaren Bezug → nach dem gemeinten Punkt fragen.
- Quellenloser Legacy-Eintrag: ehrlich „Das steht im alten Speicher, eine ursprüngliche Aussage ist nicht hinterlegt.“ Keine nachträgliche Quellen-ID erfinden.
- Zeitangabe als aufgelösten historischen Bezug erklären; persönliche Erinnerung nicht mit aktueller Fahrplanauskunft vermischen.

Erklärung nur für berechtigte Person, aus derselben gefilterten Auswahl. Quelle entfernt oder unbekannter Schlüssel → ehrlich keine belegte Erinnerung. Der typische Antwortsatz bleibt kurz und für einmaliges Hören verständlich. Vorhandene Herkunft ist ein Beleg für frühere Aussage, kein Beweis ihres Wahrheitsgehalts.

Referenz liefert `explain_fact` und `explain_slot` als strukturierte Daten. Claude integriert Intents und verständliche deutsche Varianten; keine neue LLM-Runde für die einfache Quelle nötig. Pflichtprüfung: gespeicherte Quelle statt erfundener Modellbegründung und keine fremde Biografie offenlegen.

## Gemeinsames Routing, Datenmodell und Abnahme

Neue explizite Commands vor den breiten `_REMEMBER/_RECALL/_FORGET`-Mustern routen. Erzählte/quotierte Beispiele sind keine Aktion. Benennung, Pause, Facts und Quellenfragen nicht als gewöhnliche zusätzliche Runde speichern. Für Archive-Kurzformen bekannte Namen nutzen, um vorhandene Gerätebefehle nicht zu verdrängen. Spracheingaben brauchen dieselbe bestätigte Owner-ID wie jede andere persönliche Funktion.

Version 2 erhält optionale rückwärtskompatible Felder im **noch nicht aktivierten** Entwurf: `questions` im Thread, `time_refs` pro Turn, `title`, Owner-`facts/fact_revision`, `recording/private_thread/write_epoch`. Produktionsmigration/Capability für diese Felder zusammen abstimmen. Neue Fakten, Fragen, Zeiten und Steuerzustände sind in JSON-/Header-/Modellbudgets einzurechnen. Beim Löschen Fragen und Zeiten über Quellenabhängigkeiten entfernen, Faktbelege über Faktoperationen, Policy-/Cachebarrieren beachten.

Abnahmefälle für alle sechs Funktionen:

1. Offene Bahnhofsfrage → viele Nebenfragen → Antwort „Von Berlin“, beide Personas und Neustart. Andere offene Frage bleibt offen; Überspringen erfindet keine Antwort.
2. „Morgen um neun“ vor/nach Mitternacht, Neustart, andere Zeitzone, März-/Oktober-Zeitumstellung und unbrauchbare Uhr. Keine Datumverschiebung, kein erfundener Timer.
3. Wohnortkorrektur Leipzig ersetzt alten aktuellen Wert, anderer Owner bleibt unverändert; alte Antworten oder MERKE-Ereignisse holen ihn nicht zurück.
4. Pause/Privatmodus remote/lokal, automatische Lernzeilen, verspätete Antworten, Neustart und Resume. Kein privater Inhalt auf Stick/SD/Serverlogs durch diese Modi; echte Anbietergrenzen transparent.
5. Benennen/List/Open, doppelte/ähnliche Namen, Ownerwechsel, A → B → A, gelöschtes Archiv und Befehle zum echten Gerät.
6. Herkunftsfragen liefern tatsächliche kurze Quelle, korrigieren falsche Prämisse und akzeptieren fehlende Legacy-Herkunft. Keine fremde Quelle, kein vorgespiegeltes Modell-Denken.

Referenztests prüfen deterministische Teile. Klassifikation „beantwortet das die Frage?“, realer Sprachdialog, komplette Privacy-Integration, echte Tokenizer/Audio und Pi-Leistung bleiben gesonderte Runtime-Abnahme. Keine zusätzliche Agentenruntime oder allgemeine Kalender-/Wissensplattform für diese Ergänzung.
