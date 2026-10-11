# Proximus: Stimm-Einlernen und Sprecherzuordnung v2

Stand: 11.10.2026. Implementierungskonzept für Claude Code; kein Deployment.

**Grundlage:** Systembeschreibung und Messwerte aus dem Auftrag; direkte GitHub-Prüfung von [0b-ivan/pi-voice-assistant, main, Commit 97151af](https://github.com/0b-ivan/pi-voice-assistant/tree/97151afad7a7510c4cb8d991f9618400a23a334f) am 11.10.2026 sowie Primärquellen und Quellcode anderer Projekte. Geprüft wurden insbesondere Einlernen, Sprechervergleich, Gedächtnis, Personenverwaltung, Pi-Steuerung, Protokoll und zugehörige Tests. Dieser GitHub-Stand ist kein Nachweis des laufenden Pi-/CT-Standes. Alle als Startwert, Ziel oder Budget angegebenen Parameter sind **Entwurfsannahmen**, die am echten Mikrofon und auf CT 107 überprüft werden müssen; die Messwerte stammen vom Auftraggeber.

## 1. Kurzfassung

Proximus erhält einen eigenen Stimmteil mit 24 abwechslungsreichen, qualitätsgeprüften Aufnahmen in den Bedingungen nah, fern und leise; Faktenfragen und Wakeword-Training bleiben separate Module. Drei Stimmzentren ersetzen den einzigen Mittelwert, und persönliche Schwellen werden mit Leave-one-out, fremden Stimmen und zurückgehaltenen Aufnahmen geprüft. Zu kurze Sprache, Erkennungsfehler und uneindeutige Treffer führen zu einem eingeschränkten Modus, während zufällige Nachsprechaufgaben einfache Wiederholungsangriffe erschweren. Ein optionaler rhythmischer Baseline-Teil liefert zusätzliche Messwerte, und Nachlernen erfolgt zunächst nur nach bewusster Tastenbestätigung mit festen Grenzen gegen Profilverschiebung. Der Ablauf bleibt unter sieben Minuten, schützt stabile Personen-IDs und Stick-Daten und behandelt Sprechererkennung ausdrücklich als fehlbare Zuordnung statt als sichere Authentifizierung.

## 2. Ursachenanalyse der Messwerte

**Befund:** 0 von 14 echten Anfragen überschritten 0,5; der höchste Score war 0,427. Vier TTS-Versuche und ein akzeptierter Fremdversuch reichen weder zur Modellbewertung noch zur Schätzung einer Fehlannahmerate. Die Fremdannahme bei 0,78 zeigt aber konkret, dass ein hoher Score keinen Nachweis einer lebenden, berechtigten Person liefert.

**Priorität 0:** Den Kurzsprach-Bypass schließen. GitHub bestätigt: `Service._identify()` gibt bei fehlendem Modell, Ausnahme oder fehlendem Embedding den unveränderten Gedächtniskontext zurück. `Embedder.embed()` prüft die PCM-Gesamtlänge, nicht Sprachsekunden; `trim_silence()` gibt bei zu wenig erkannter Sprache das Original zurück. Ein weitgehend stiller Clip kann so die Mindestlänge formal erfüllen. [Server, Zeilen 371–393](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/server/servitor_server.py#L371-L393), [Sprecherpfad, Zeilen 63–126](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/speaker.py#L63-L126)

Weitere konkrete Codebefunde:

| Befund im geprüften GitHub-Stand | Folge für den Auftrag |
|---|---|
| Nachtrainieren nimmt **20** Wakeword-Clips auf und erzeugt aus Gruppen von fünf vier Embeddings; Zuordnung ab `REFINE_MATCH = 0.4`. [Einlernen](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/enroll.py#L161-L256) | Die Auftragsbeschreibung „5 × Proximus“ beschreibt den aktuellen Ablauf nicht vollständig. Den ganzen Pfad ersetzen, nicht nur die Clipzahl erhöhen. |
| `pcm_of()` liest WAV-Daten ohne Prüfung von Rate/Kanälen; Antworten werden während der Sitzung sofort als Fakten geschrieben. [Einlernen](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/enroll.py#L115-L217) | Format vor Übernahme validieren; neue Fakten bis zur bewussten Übernahme als Entwurf halten. |
| Profile sind nach Namen indiziert, Dateiname ist ein Namens-Slug; kein explizites `profile_id`. `save_voiceprint()` bildet einen gewichteten Mittelwert. [Profile](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/memory.py#L217-L288) | Stabile ID bei Migration einführen, falls der laufende Stand ebenfalls keine hat; alte Zähler nicht als neue Anker interpretieren. |
| Personenverwaltung akzeptiert eine passende statische Passphrase schon mit Stimmwert ≥0,3; Details zeigen Score ×100 als Prozent. Auth-Logs enthalten den Namen. [Personen](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/people.py#L209-L243), [Anzeige](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/people.py#L82-L88) | Nebenpfad ebenfalls korrigieren; keine vermeintliche Erkennungswahrscheinlichkeit anzeigen und keine Namen protokollieren. |
| Der lokale Pi-Rückfall führt Gedächtnis- und Kalenderpfade ohne Server-Sprecherprüfung aus. [Lokaler Dialog](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/ptt.py#L1808-L1888) | Derselbe eingeschränkte Modus muss auch bei Serverausfall auf dem Pi gelten. Ein Serverfix allein genügt nicht. |
| `sanitize()` lässt nur kleine V1-Prints durch; `guest_view()` behält alle Direktiven. Der Core wird derzeit im Header übertragen. [Memory](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/memory.py#L335-L370), [Protokoll](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/src/protocol.py#L166-L178) | V2 braucht eigene Größenprüfung/Übertragung; Gastmodus erhält nur ausdrücklich öffentliche Persona-Einstellungen, keine eventuell persönlichen Direktiven. |

Die vorhandenen Sprecher-/Servertests in `tests/test_enroll.py` verwenden feste Fake-Embeddings, teils selbst für stille Eingaben. Sie prüfen Ablauf und Sperrlogik, belegen aber keine akustische Erkennungsleistung. [Tests](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/tests/test_enroll.py#L161-L267)

| Hypothese | Bewertung | Prüfung, die sie bestätigt oder schwächt |
|---|---|---|
| Unterschiedliche Audioverarbeitung beim Einlernen und im Alltag | Hohe Prüfpriorität, keine bewiesene Ursache | Dieselbe Stick-WAV durch beide Wege schicken: PCM-Format, tatsächliche Rate, Resampling, Kanal, VAD-Maske und resultierendes Embedding vergleichen. Identisches Audio muss praktisch identische Vektoren liefern. |
| Falsches Modell, falsches Profil oder beschädigte Serialisierung | Kann die durchgehend niedrigen Scores erklären | Modell-Checksumme, Dimension und Profil-ID vergleichen; frisches Embedding gegen sich selbst und gegen seine gespeicherte/geladene Fassung prüfen. Gleiche Aufnahme gegen falsche Profile als Gegenprobe. |
| Zu wenig verwertbare Sprache in den sieben Antworten | Plausibel: sechs Sekunden Aufnahme sind nicht sechs Sekunden Sprache | Sprachsekunden, Sprachanteil, Pausen und Paarähnlichkeiten messen. Nicht nur die Zahl der WAV-Dateien zählen. |
| Signalton, Servitor-TTS oder Raumhall im Clip | Plausible Vermischung zweier Stimmen bzw. Aufnahmebedingungen | Ausgabe und Aufnahme zeitlich strikt trennen; dieselben Antworten mit/ohne Signalton und mit ausreichender Abklingpause vergleichen. |
| Einlernen nah und konzentriert, Alltag fern und beiläufig | Plausibler Aufnahmeunterschied | Nah/fern/leise jeweils in mehreren Sätzen und zwei Sitzungen erfassen; Kreuzvergleich zwischen Bedingungen erstellen. |
| Mittelwert über schlechte oder verschiedene Aufnahmen | Möglich, aber Mittelwertbildung ist nicht grundsätzlich falsch | Auf identischen Testaufnahmen globalen Mittelwert und drei Zentren vergleichen. Ein sauberer, homogener Mittelwert dient als Kontrollvariante. |
| Fehlende Qualitätskontrolle und unzureichende Lautvielfalt | Plausibel; Lautvielfalt allein löst keine Identitätsverwechslung | Kurze Wakeword-Clips gegen gleich lange Alltagssätze vergleichen; schlechte Clips getrennt auswerten. |
| Schwelle 0,5 passt nicht zu Profil und Vergleichsverfahren | Sicher ungeeignet für die 14 berichteten Echtversuche; neue Schwelle noch unbekannt | Echte und fremde Scores gegen dasselbe Profil mit identischer Verarbeitung messen; Schwelle erst danach wählen. |
| Deutsch, Dialekt oder Kurzäußerungen passen schlecht zum Modell | Möglich, erst nach den Pipeline-Prüfungen bewerten | Gleiche Sprecher unter kontrollierten Bedingungen mit CAM++ und optional einem zweiten Modell vergleichen. |

Das offizielle sherpa-Beispiel mittelt ebenfalls mehrere Embeddings; die Änderung auf mehrere Zentren ist hier eine prüfbare Hypothese gegen Aufnahmeunterschiede, keine allgemeine Überlegenheit. [sherpa-Beispiel](https://github.com/k2-fsa/sherpa-onnx/blob/9db1af1871ed78cee9357c23898e5a7d8702d21a/python-api-examples/speaker-identification.py)

**Diagnosereihenfolge:** Format und Identität → Selbstvergleich und Speicher-Rundlauf → echte Sprachdauer und Fremdaudio → Aufnahmebedingungen → Profilform → Schwelle → optionaler Modellvergleich. Die vorhandenen echten Aufnahmen dürfen als Entwicklungsdaten dienen, aber nach darauf abgestimmten Änderungen nicht mehr als unabhängige Abnahme gelten.

Die Schwelle nicht allein wegen 0,427 auf 0,3 senken. Falls die berichteten echten Scores und 0,78 gegen dasselbe Profil unter derselben Verarbeitung entstanden sind, gibt es dafür keine Schwelle, die beide Ziele erfüllt.

## 3. Neuer Einlern-Ablauf mit Zeitbudget und Ansagen

### Module und Bedienung

Menü: **Stimme kennenlernen**, **Stimme ergänzen**, **Fakten kennenlernen**, **Wakeword aufnehmen**. Bei Wiederholung ist nur der Stimmteil vorausgewählt; die sieben Faktenfragen bleiben unverändert verfügbar. Die bisherigen 20 Wakeword-Aufnahmen bleiben für Wakeword-Training erhalten und werden nicht zum Sprecherprofil gemischt.

Ein neues Profil oder eine Wiederherstellung startet lokal nach bewusster Bestätigung mit Taste E. Ein Sprachbefehl darf das Menü öffnen, aber keine bestehende Identität überschreiben. Das Zielprofil wird vor der Aufnahme ausgewählt und durch seine stabile ID gebunden; der Name ist nur Anzeige. Taste E nutzt die vorhandene lokale Bestätigung; A dient im Stimmteil nur zum Vorsprechen. Taste B bricht in jeder Phase ab, einschließlich TTS, Aufnahme, Serveranfrage und Speichern.

### Plan für die normale Erstanlage

| Schritt | Budget | Ablauf und Ansage |
|---|---:|---|
| 1. Auswahl und Einführung | 15 s | „Diese Einheit lernt deine Stimme. Sprich wie im Alltag. Taste B beendet den Vorgang.“ Fakten und Baseline vorab an- oder abwählen. |
| 2. Technikprüfung | 15 s | Stick und Server prüfen; etwa eine Sekunde Raumgeräusch und einen kurzen Prüfsatz erfassen. „Sprich den angezeigten Satz mit gewöhnlicher Stimme.“ Bei ungeeignetem Pegel konkrete Korrektur. |
| 3. Stimmteil | 180 s | 24 akzeptierte Sätze: acht nah, acht fern, acht leise; siehe unten. Jede Aufnahme sofort prüfen. |
| 4. Optionaler Baseline-Teil | 65 s | Zwölf kurze Folgen. „Rhythmusprüfung. Höre zu. Wiederhole die angezeigte Antwort. Deine gewöhnliche Stimme genügt.“ |
| 5. Profil und Vorprüfung | 10 s | Zentren, Leave-one-out und vorhandenen Fremdvergleich berechnen; sechs zurückgehaltene Clips prüfen. „Die Einheit prüft die Zuordnung.“ |
| 6. Optionale Faktenfragen | 70 s | Bisherige sieben Fragen, jeweils bis sechs Sekunden Antwort. „Stimmteil beendet. Nun kannst du persönliche Angaben ergänzen.“ Einzelne Fragen sind überspringbar. |
| 7. Bestätigung und Abschluss | 10 s | Status und ausgewähltes Profil anzeigen; E übernimmt den geprüften Entwurf. „Stimmprofil gespeichert. Die Erkennung wird im Alltag geprüft.“ Bei ungeklärter Kalibrierung ausdrücklich „Zusatzbestätigung erforderlich“. |
| Reserve | 35 s | Korrekturhinweise, höchstens drei zusätzliche Aufnahmeversuche insgesamt und kurze Serververzögerungen. |

**Gesamt:** ohne Baseline 335 s = 5:35 min; mit Baseline 400 s = 6:40 min. Ohne Fakten verkürzt sich der jeweilige Ablauf um 70 s. Wakeword-Training benötigt zusätzlich ungefähr 45 s und ersetzt in einer vollständigen Sitzung den Baseline-Teil; alle Module zusammen überschreiten das Budget und werden nicht gemeinsam gestartet.

Die Zeiten setzen kurze Displaylesezeiten und ausreichend schnelle Serverantworten voraus. Es sind Budgets, keine gemessenen Laufzeiten. Einlesen vom Display ist im Hauptteil Standard; A kann den Satz vorsprechen lassen. Bei regelmäßigem Bedarf an vollständigem Vorsprechen werden optionale Module abgewählt, nicht Sprachqualität oder Mindestmenge abgesenkt.

### Stimmteil im Detail

1. **Nah:** etwa 40–70 cm in üblicher Geräteposition. Ansage: „Beginne an deinem gewöhnlichen Platz. Verstelle deine Stimme nicht.“
2. **Fern:** etwa zwei Meter oder tatsächlich üblicher Alltagsabstand. Ansage: „Gehe auf deinen üblichen Abstand. Sprich gewöhnlich. Rufe nicht.“
3. **Leise:** übliche Nähe, leise stimmhafte Sprache. Ansage: „Jetzt leiser, mit hörbarer Stimme. Flüstern ist nicht erforderlich.“

Je Bedingung zwei kurze, vier mittlere und zwei längere Sätze; innerhalb der Blöcke unterschiedliche Texte, davon einige etwas schneller, fragend oder beiläufig. Insgesamt mindestens 90 s VAD-bestätigte Sprache verlangen. Wird die Menge mit dem geplanten Satzmix nicht erreicht, innerhalb des Budgets zu kurze Aufnahmen durch längere ersetzen; anderenfalls keinen vollständigen Stimmteil bestätigen. Nicht zum künstlichen Tiefsprechen, monotonen Servitor-Ton oder forcierten Flüstern auffordern.

Vor Aufnahmebeginn TTS und Signalton vollständig beenden, dann als Startwert 300 ms abklingen lassen; bei gemessenem Nachhall verlängern. Aufnahme endet nach etwa 500 ms Schlussstille, spätestens nach zehn Sekunden. Das Sprachsegment soll zwei bis acht Sekunden enthalten. Große Binnenpausen nicht durch hartes Aneinanderkleben einzelner Wörter kaschieren.

Sechs Aufnahmen werden vorab per Plan als Prüfung reserviert, zwei pro Bedingung mit anderen Satzmustern. Die übrigen 18 bilden die Zentren. Ein fehlgeschlagener Versuch zählt nicht zum Fortschritt; Wiederholung verwendet einen neuen Satz mit denselben Laut- und Bedingungszielen. Baseline-Clips ersetzen diese 24 Kernaufnahmen nicht.

Display: `Stimme 12/24`, darunter `fern · sprich jetzt` bzw. `prüfe`, plus Fortschrittsbalken und `B: Abbruch`. Satz auf mehrere lesbare Zeilen verteilen. Während Aufnahme keine gesprochenen Hinweise. Keine Prozentanzeige, die bereits eine erfolgreiche Sprechererkennung suggeriert.

Die letzten zwei Kernaufgaben verwenden frische variable Prüfwörter und die strengere Challenge-Inhaltsprüfung. Sie können die aktuelle Einlernaktion abdecken, auch wenn der Baseline-Teil abgewählt ist; bei der Erstanlage gilt die bewusste lokale Profilwahl, kein vorgetäuschter Stimmnachweis. Neue Faktenantworten bleiben bis zur Schlussbestätigung in derselben Stick-Entwurfsrevision und werden nicht sofort in das aktive Gedächtnis geschrieben.

**Harte Grenze:** einschließlich Schlussbestätigung und Abschluss höchstens 420 s. Rechtzeitig keine optionalen Aufgaben mehr beginnen; fehlt bei Ablauf die Bestätigung, sauber abbrechen. Ein unvollständiger Stimmteil ersetzt kein Profil. Pro Satz höchstens eine Wiederholung, insgesamt höchstens drei zusätzliche Versuche; darüber „Aufnahmebedingungen ungeeignet. Das bisherige Profil bleibt erhalten.“ Erreichen der Mindestmenge hat Vorrang vor optionalen Abschnitten.

## 4. Satzgenerator, Beispielsätze und Baseline

### Regeln

- Lokaler Generator aus geprüften Satzmustern und Wortlisten; kein LLM und keine Cloud. Zufall mit `secrets`, reproduzierbarer Testgenerator nur in Tests.
- Pro Sitzung neu zusammengesetzte Sätze, keine Wiederholung desselben Textes. Kalibrierungsclips verwenden andere Muster als die Profilclips.
- Häufige, vom installierten Vosk-Modell unterstützte Wörter; keine schwer erkennbaren Eigennamen oder sinnlosen Kunstwörter. Im Gerätetest schlecht erkannte Wörter ersetzen.
- Lautabdeckung über ein kuratiertes Aussprachelexikon prüfen: kurze/lange Vokale einschließlich ü/ö/ä, Diphthonge, Schwa, ich-/ach-Laut, s/z/sch, Affrikaten, Plosive, Nasale, /ŋ/, /l/ und gebräuchliche /r/-Varianten. Häufige Lehnlaute mit „Garage“, „Jeans“ und „Dschungel“ aufnehmen.
- **Alle Laute** bedeutet das vorher festgelegte Standarddeutsch-Inventar; Orthografie allein belegt keine Vollständigkeit. Dialekt und individuelle /r/-Realisierung werden akzeptiert, nicht korrigiert. Seltene Lehnlaute können eigene Lexikontests benötigen.
- Kurze Sätze ungefähr 4–7 Wörter, mittlere 8–12, längere 13–20. Tatsächliche Dauer entscheidet; schnell gesprochenes Material unter zwei Sprachsekunden wird verlängert und neu aufgenommen.
- Für echte Challenge-Aufgaben variable Wörter in der Mitte und am Ende platzieren, z. B. „Prüfe **Birke Kupfer sieben** und melde **Fenster**.“ Mindestens drei unabhängig gezogene Prüfwerte; keine vorhersehbare tägliche Rotation.
- Sämtliche Testtexte sind nur Aufnahmeaufgaben: weder Fakten speichern noch Dialogbefehle ausführen.

Vosk unterstützt je nach Modell dynamische Wortschatzanpassung; große Modelle mit statischem Graph unterstützen sie nicht. Die Challenge-Prüfung nutzt zunächst den gewöhnlichen Erkenner. Eine Grammatik, die nur den Sollsatz erlaubt, kann falsche Inhalte scheinbar passend erkennen und darf nicht den Sicherheitsnachweis liefern. [Vosk-Dokumentation](https://alphacephei.com/vosk/adaptation)

### 36 Beispielsätze

Die Angaben sind Zielklassen, keine gemessenen Sprechzeiten. Die Liste ist ein Seed-Korpus für Muster und Lautprüfung; neue Sitzungen erzeugen Varianten.

| Nr. | Ziel | Satz |
|---:|---|---|
| 1 | 2–3 s | Die müde Mühle läuft wieder. |
| 2 | 2–3 s | Fünf Frösche hören das Öl tropfen. |
| 3 | 2–3 s | Lies bitte den nächsten Abschnitt vor. |
| 4 | 2–3 s | Der helle Kern bleibt heute wach. |
| 5 | 2–3 s | Ich suche das Buch am Bach. |
| 6 | 2–3 s | Sieben Schalter summen im stillen Schrank. |
| 7 | 2–3 s | Ist das grüne Licht noch an? |
| 8 | 2–3 s | Pack bitte die beiden Kisten weg. |
| 9 | 2–3 s | Der Zug fährt durch den Dschungel. |
| 10 | 2–3 s | Meine Jeans hängen in der Garage. |
| 11 | 2–3 s | Peter prüft den kleinen blauen Plan. |
| 12 | 2–3 s | Der junge Sänger wartet noch draußen. |
| 13 | 3–5 s | Die Tür ist offen, aber die Küche bleibt heute kühl. |
| 14 | 3–5 s | Bitte stelle die volle Tasse neben den leeren Teller. |
| 15 | 3–5 s | Warum liegt der rote Schlüssel unter dem weißen Tisch? |
| 16 | 3–5 s | Der Wind weht leise durch die schmale offene Lücke. |
| 17 | 3–5 s | Zwischen zwei Zweigen sitzt ein kleiner grauer Vogel. |
| 18 | 3–5 s | Heute kaufe ich frische Brötchen, Käse und süße Birnen. |
| 19 | 3–5 s | Nach dem Regen riecht der warme Boden nach Erde. |
| 20 | 3–5 s | Kannst du die alte Uhr vor der Pause prüfen? |
| 21 | 3–5 s | Die Wachen zählen zwölf helle Punkte auf dem Bildschirm. |
| 22 | 3–5 s | Ein junger Mensch spricht ruhig mit der kleinen Maschine. |
| 23 | 3–5 s | Die graue Katze schläft kurz neben der großen Vase. |
| 24 | 3–5 s | Der flinke Fuchs sucht seine Spur im feuchten Schnee. |
| 25 | 6–8 s | Wenn morgen früh die Sonne scheint, öffne ich das Fenster und höre den Vögeln im Garten zu. |
| 26 | 6–8 s | Die gelbe Lampe über dem Tisch leuchtet schwach, während draußen ein schwerer Wagen langsam vorbeifährt. |
| 27 | 6–8 s | Warum haben die beiden jungen Gäste ihre nassen Schuhe vor der Tür und ihre Jacken im Flur gelassen? |
| 28 | 6–8 s | Nach einem langen Arbeitstag spreche ich etwas leiser, aber meine Worte sollen für die Maschine verständlich bleiben. |
| 29 | 6–8 s | Auf dem Weg zum Bahnhof kaufe ich ein kleines Stück Kuchen und eine Flasche kühles Wasser. |
| 30 | 6–8 s | Hinter dem großen Schrank liegen fünf dünne Bücher, drei bunte Karten und ein völlig zerknitterter Brief. |
| 31 | 6–8 s | Die Einheit hört das Rauschen im Raum und wartet geduldig, bis der letzte klare Satz gesprochen ist. |
| 32 | 6–8 s | Bitte prüfe zuerst den grünen Schalter links und danach die beiden roten Leuchten über der geschlossenen Klappe. |
| 33 | 6–8 s | Ein fröhlicher Sänger übt am frühen Abend, während sein müder Nachbar die Küche gründlich aufräumt. |
| 34 | 6–8 s | Zwischen den hohen Bäumen steht eine kleine Hütte, deren offene Tür sich langsam im Wind bewegt. |
| 35 | 6–8 s | Ist der neue Plan schon vollständig, oder fehlen noch die letzten Zahlen für den nächsten ruhigen Arbeitsschritt? |
| 36 | 6–8 s | Ich lege den schweren Schlüssel neben das helle Glas und schließe anschließend vorsichtig das Fenster im Flur. |

### Optionaler Baseline-Teil: zwölf eigene Paare

Stilbezug ist der rhythmische Baseline-Test aus *Blade Runner 2049*; alle folgenden Zeilen sind eigene Formulierungen. Antworten vollständig anzeigen, bei Frageketten vorher erklären: „Sprich die angezeigte Antwort. Du musst nichts erraten.“ Mechanisch spricht nur Proximus; der Mensch spricht natürlich.

| Paar | Proximus spricht vor | Erwartete Nachsprech-/Antwortfolge |
|---:|---|---|
| 1 | „Kern bereit. Leitung offen.“ | „Kern bereit. Leitung offen.“ |
| 2 | „Ein Impuls. Zwei Zeichen.“ | „Ein Impuls. Zwei Zeichen.“ |
| 3 | „Was bleibt im Speicher?“ | „Nur das helle Signal.“ |
| 4 | „Wer hält die Wache?“ | „Der Kern hält die Wache.“ |
| 5 | „Druck fällt. Licht bleibt.“ | „Druck fällt. Licht bleibt.“ |
| 6 | „Kein Echo. Eine Stimme.“ | „Kein Echo. Eine Stimme.“ |
| 7 | „Wo endet der Draht?“ | „Am stillen Rand des Raums.“ |
| 8 | „Zähle: Birke. Kupfer. Sieben.“ | „Birke. Kupfer. Sieben.“ |
| 9 | „Was öffnet die Klappe?“ | „Ein grünes Zeichen öffnet sie.“ |
| 10 | „Leitung drei. Lampe blau.“ | „Leitung drei. Lampe blau.“ |
| 11 | „Melde: Fenster. Silber. Neun.“ | „Fenster. Silber. Neun.“ |
| 12 | „Ist die Prüfung beendet?“ | „Die Einheit wartet auf Bestätigung.“ |

Bei 2, 8, 10 und 11 werden Zahlen, Gegenstände oder Farben bei jedem Versuch neu gezogen und die Sollantwort entsprechend angepasst. Feste Paare sind nur Rhythmus- und Qualitätstests. Eine Sicherheits-Challenge besteht aus mindestens zwei frischen variablen Paaren; die Beispielwerte sind keine dauerhaften Passwörter.

Takt als Startwert: nach Ende der Vorgabe 300 ms Pause, dann Antwortfenster; keine strengen Reaktionszeiten als Menschennachweis. Pro Antwort separat messen: Sprachdauer, WER, Beginnverzögerung, Silben/Wörter pro Sekunde und Streuung der Antwortdauern. Die metrische Gleichmäßigkeit beschreibt die Aufgabe, nicht die Identität oder Lebendigkeit.

**Messbarer Nutzen:** vergleichbare kurze Segmente, überprüfbare Inhalte und eine zusätzliche Probe für Kurzäußerungen. Antworten unter zwei Sprachsekunden werden nicht in die drei Zentren übernommen; sehr kurze Folgen taugen nur zur Inhaltsprüfung. Auf einem getrennten Testdatensatz die kurze Sprechererkennung mit/ohne zusätzliche Baseline-Trainingsclips vergleichen. Ohne nachgewiesenen Vorteil bleibt der Teil optional und verändert weder Schwelle noch Profil.

## 5. Qualitätsprüfung und Wiederholungslogik

Ein gemeinsamer Audio-Pfad verarbeitet Einlern- und Alltagsaudio: mono, tatsächlich 16 kHz, PCM signed 16 Bit little endian; echtes Resampling statt bloßem Umdeklarieren der Rate. Modellinput Float32 im erwarteten Wertebereich; jedes Embedding endlich, dimensionsrichtig und L2-normalisiert. Formatfehler ablehnen.

Sprachaktivität (VAD) mit einem kleinen CPU-Verfahren, z. B. WebRTC-VAD auf 20-ms-Frames; Auswahl und Aggressivität am Mikrofon prüfen. Die API verlangt 16-Bit-Mono-PCM und exakt 10/20/30-ms-Frames bei unterstützten Raten. Wrapper MIT, enthaltene WebRTC-Komponente BSD; kein großes neuronales Modell erforderlich. [API](https://github.com/wiseman/py-webrtcvad/blob/e283ca41df3a84b0e87fb1f5cb9b21580a286b09/README.rst), [Lizenzen](https://github.com/wiseman/py-webrtcvad/blob/e283ca41df3a84b0e87fb1f5cb9b21580a286b09/LICENSE) Ein bloßer Amplitudenschwellwert unterscheidet Rauschen nicht zuverlässig von Sprache. Führende/abschließende Stille entfernen, etwa 150 ms Rand erhalten; natürliche kurze Binnenpausen bewahren. `speech_seconds` zählt nur VAD-Sprachframes, `trimmed_seconds` das gesamte verbleibende Segment.

| Messung | Startwert für Einlernen | Konkreter Hinweis bei Fehler |
|---|---|---|
| Sprachdauer | 2–8 s tatsächliche Sprache; keine künstliche Auffüllung | „Zu kurz. Sprich den ganzen Satz in gewöhnlichem Tempo.“ |
| Sprachanteil nach Randschnitt | mindestens 65 %; keine Binnenpause über 1,2 s | „Zu viele Pausen. Sprich den Satz als zusammenhängende Äußerung.“ |
| Clipping | weniger als 0,1 % Samples mit Betrag ≥ 0,99 Vollaussteuerung; keine zusammenhängende Sättigung ≥ 5 ms | „Das Mikrofon übersteuert. Gehe etwas zurück oder senke den Aufnahmepegel.“ |
| Sprachpegel | RMS der Sprachframes ungefähr −35 bis −10 dBFS | „Zu leise. Sprich etwas deutlicher oder gehe näher heran.“ Bei hohem Pegel nur mit weiteren Sättigungszeichen ablehnen. |
| Rauschabstand | geschätzt mindestens 12 dB | „Zu viel Hintergrundgeräusch. Schalte die Geräuschquelle aus und versuche es erneut.“ |
| Inhalt | normale Testsätze WER ≤ 0,20; variable Prüfwörter zusätzlich vollständig und in Reihenfolge | „Die Wörter wurden nicht sicher verstanden. Lies den neuen Satz vollständig.“ |
| Ausgabe im Aufnahmefenster | keine laufende TTS, kein Signalton; Aufnahme erst nach Abklingpause | „Die Ausgabe war noch hörbar. Die Einheit beginnt die Aufnahme erneut.“ |

`dBFS = 20 · log10(RMS / Vollaussteuerung)`. SNR näherungsweise aus Sprachframe-Leistung und einer unmittelbar davor gemessenen Geräuschleistung berechnen: `10 · log10(max(P_sprach − P_rauschen, ε) / max(P_rauschen, ε))`. Das ist eine Schätzung für stationäres Rauschen, kein echter Quellenvergleich und kein Hallmaß. Bei fehlender belastbarer Geräuschprobe SNR als fehlend markieren, kurze Geräuschmessung nachholen; fehlende Werte nicht als bestanden behandeln. Digitale Pegelanhebung verbessert diesen Rauschabstand nicht.

**WER:** `(Substitutionen + Löschungen + Einfügungen) / Wörter_im_Solltext` per Levenshtein. Unicode normalisieren, Kleinschreibung und Interpunktion vereinheitlichen; Zahlen über eine eindeutige feste Abbildung normalisieren. Keine freie Synonym- oder LLM-Bewertung. Bei kurzen Sätzen wirken einzelne Fehler stark: für Antworten mit höchstens fünf Wörtern in Challenge-Prüfungen exakte normalisierte Übereinstimmung verlangen. Längere Challenges: WER ≤ 0,15 plus alle variablen Wörter korrekt. WER ist Inhaltsprüfung, kein Anti-TTS-Detektor.

Qualitätsprüfung nutzt keine Bewertung gegen das bestehende Stimmprofil, um schlechte Anfangsprofile nicht selbst zu bestätigen. Trotzdem müssen offensichtlich unterschiedliche Sprecher in einer Einlernsitzung bei der späteren Konsistenzprüfung auffallen. Bei mehreren Stimmen nicht versuchen, automatisch die berechtigte Stimme herauszufiltern.

Nur angenommene Aufnahmen in den Entwurf übernehmen. Abgelehnte Clips sofort vom Stick löschen, keine Embeddings daraus speichern. Bessere Aufnahme mit neuem Challenge-Text verlangen, zuerst den dominierenden Fehler nennen; nicht fünf Hinweise gleichzeitig. Bei systematisch abgelehnter leiser Sprache VAD/Pegel prüfen, statt die Person zum Normalsprechen zu zwingen.

Für Faktenantworten gilt Qualitätsprüfung für Transkription, aber keine WER gegen unbekannte Inhalte und keine automatische Verwendung zum Stimmtraining. Im Alltag darf ein ausreichend sauberer Clip ab 1,5 Sprachsekunden zur Zuordnung dienen; 1,5–2 s sind ein separat zu prüfender Grenzbereich. Unter 1,5 s oder bei ungeeigneter Qualität: `insufficient`, eingeschränkter Modus, nötigenfalls „Sag die Anfrage bitte als ganzen Satz.“ Keine Freigabe aus der zuletzt erkannten Stimme erben.

## 6. Profilbildung, Schwellenkalibrierung und Vergleich

### Profilform und Vergleich

**Standard:** je ein L2-normalisiertes Zentrum für nah, fern und leise, aus jeweils sechs akzeptierten Kernclips; sechs weitere Clips bleiben außerhalb des Profils. Zentrum als normalisierter Mittelwert innerhalb einer Bedingung, bei Bedarf mit begrenzter Qualitätsgewichtung von höchstens 2:1. Zunächst gleiche Gewichte verwenden, um Effekte nachvollziehen zu können.

Paarähnlichkeiten innerhalb der Bedingungen prüfen. Ein stark abweichender Clip wird zur manuellen Prüfung bzw. erneuten Aufnahme markiert; eine niedrige Ähnlichkeit allein beweist keine Fremdstimme. Bei unplausiblen, getrennten Sprechergruppen kein Mischprofil speichern. Robuste Ausreißerregeln ausschließlich aus dem jeweiligen Trainingssatz berechnen und in jedem Kalibrierungsdurchlauf neu anwenden.

Für Anfrage-Embedding `x` und Profil `p`:

```text
s_p(x) = max(cos(x, c_p_nah), cos(x, c_p_fern), cos(x, c_p_leise))
b = Profil mit höchstem s_p(x)
matched nur, wenn:
  Audioqualität geeignet und Profilvergleich kompatibel,
  s_b(x) >= persönliche Schwelle tau_b,
  Abstand zum zweitbesten Profil >= delta.
```

`delta = 0,05` ist ein Startwert; bei mehreren Personen anhand ihrer gegenseitigen Verwechslungen prüfen. Bei nur einem Profil entfällt die Abstandsregel. Gleichstand, fehlende Kalibrierung oder unzureichender Abstand → `ambiguous` bzw. `unverified`; keine Namenszuweisung. Wenn der höchste Score seine persönliche Schwelle verfehlt, nicht automatisch auf eine zweitplatzierte Person mit niedrigerer Schwelle ausweichen. Scores und Schwellen sind keine Wahrscheinlichkeiten.

**Begründung:** drei Zentren erlauben passende Aufnahmebedingungen, ohne jedes Einzelbeispiel als zusätzliche Trefferchance zu verwenden. Max passt auch dann, wenn ein leiser Clip nur dem leisen Zentrum ähnelt; es erhöht aber gegenüber einem Zentrum die Fehlannahmegefahr und muss exakt so kalibriert werden. Ein unbegrenztes Max über Einzel-Embeddings ist ausgeschlossen. Top-2-Mittel über eine feste Zahl guter Anker kann als Vergleichsvariante untersucht werden, ist wegen möglicher Vermischung der Aufnahmebedingungen zunächst kein Standard.

### Kalibrierungsregel

1. **Echt-Scores G:** aus den 18 Profilclips Leave-one-out rechnen. Jeweils einen vollständigen Aufnahmeclip entfernen, Zentren aus den übrigen Clips neu bilden, den entfernten Clip mit dem vollständigen späteren Vergleichsverfahren prüfen. Nicht den eigenen Clip mit einem Zentrum vergleichen, in dem er noch enthalten ist; Segmente derselben Aufnahme gelten nicht als unabhängige Proben.
2. **Fremd-Scores F:** mindestens 20 qualitätsgeprüfte Aufnahmen von mindestens fünf nachweislich anderen echten Personen gegen das endgültige Profil prüfen; zusätzlich möglichst zehn TTS-/Replay-/Pitch-Varianten als eigene Angriffsgruppe. Aufnahmebedingungen einschließlich Abstand und Mikrofonkanal möglichst an den Alltag anpassen. Andere bekannte Personen sind ebenfalls Fremdproben für dieses Profil.
3. Fremde Embeddings dürfen nur mit kompatiblem Modell, Dimension und Vorverarbeitung verwendet werden. Ein Clip im Gastmodus ist **nicht** automatisch eine Fremdprobe: Er könnte vom falsch abgewiesenen Bediener stammen. Herkunft durch ausdrückliche Zuordnung im lokalen Testmodus sichern; Gastaufnahmen nicht heimlich sammeln. Ein lizenzierter, dokumentierter Referenzsatz auf dem Stick ist eine Alternative, ersetzt aber Tests am eigenen Mikrofon nicht.
4. **Schwellenwahl:** zunächst `tau = max(F) + epsilon`, mit `epsilon = 0,03` als konservativem Startabstand auf der Kosinusskala. Bei der üblichen Entscheidung `score >= tau` liegt damit kein beobachteter Fremdscore auf der akzeptierten Grenze. Dies ist die niedrigste Schwelle mit diesem Abstand und erhält unter dieser Nebenbedingung die meisten Echt-Treffer.
5. Schwelle nur als Kandidat zulassen, wenn mindestens 95 % der LOO-Echt-Scores `>= tau` sind und alle beobachteten Fremd- und Angriffsscores `< tau` bleiben. Bei 18 LOO-Proben bedeutet ≥95 % tatsächlich 18/18; Bedingungen getrennt auswerten. Für mehrere Profile zusätzlich prüfen, ob der gesamte Entscheidungsweg einschließlich `delta` dieselben Personen richtig zuordnet.
6. **Zurückgehaltene Prüfung:** die sechs separaten Aufnahmen müssen mit dem unveränderten Kandidaten alle korrekt zugeordnet werden. Kein Nachstellen der Schwelle an diesen sechs Clips; nach einer Anpassung neue Prüfclips erfassen. Danach folgt die unabhängige Alltagsabnahme aus Abschnitt 9.

`epsilon` ist keine statistisch begründete Sicherheitsmarge. Robustheit gegen neue Stimmen kann mit wenigen Proben nicht garantiert werden. Die Max-Regel, Serialisierung, Zentrumsauswahl und Qualitätsgrenzen müssen bei Kalibrierung und Alltag identisch sein. Insbesondere den **gespeicherten und wieder geladenen** Vektor verwenden; keine Float32-Kalibrierung mit anschließend anderer Quantisierung.

**Bei überlappenden oder falsch geordneten Verteilungen:** Gibt es keine Schwelle mit gewünschter Echt-Trefferrate und Fremdabstand, ist das Profil für automatische Zuordnung nicht geeignet. Die Schwelle nicht so weit senken, bis der Bediener gerade erkannt wird. Stattdessen Aufnahmeverarbeitung prüfen, gezielt problematische Bedingungen neu aufnehmen, separate Alltagssitzung erfassen und optional ein anderes Modell vergleichen. Bis dahin Status `provisional`; lokale Bestätigung kann eine konkrete Anfrage zuordnen, automatische Zuordnung bleibt gesperrt. Falls keine Fremdproben verfügbar sind, ebenfalls `provisional` statt eine frei erfundene persönliche Schwelle einzusetzen.

LOO aus einer einzigen Sitzung ist optimistisch für Tageszeit, Müdigkeit und Raumwechsel. Die 5–7-Minuten-Sitzung erzeugt deshalb ein Profil und höchstens eine Vorprüfung; sie kann die mehrtägige Validierung nicht vorwegnehmen.

### Speicherung und Migration

V2-Profil enthält: `schema_version`, stabile `profile_id`, Anzeigename, Modellname und SHA-256, Vorverarbeitungs-Version, Dimension, Codec, drei Zentren, bis zu 18 unveränderliche Anker, sechs Prüfembeddings, begrenzte ergänzende Proben, Aufnahmebedingungen/Qualitätszahlen, `tau`, `epsilon`, `delta`, numerische Kalibrierungsergebnisse, Status und Revision. Zulässige Status: `legacy`, `provisional`, `calibrated`, `validated`. `calibrated` setzt den Fremdvergleich und die sechs unabhängigen Prüfclips voraus; `validated` zusätzlich die Alltagsabnahme.

Für V2 Float32-Vektoren in klar versionierter Darstellung verwenden, z. B. Base64 mit festgelegter Byteordnung; nach Lesen erneut normieren und auf NaN, Nullnorm, Dimension und Größenlimits prüfen. Pro Profil höchstens 48 Probenembeddings: 18 Anker + sechs Prüfproben + 24 bestätigte Ergänzungen, zusätzlich drei Zentren. Bei beispielhaft 512 Dimensionen sind dies etwa 104 kB reine Float32-Werte; die tatsächliche Dimension kommt vom Modell. Keine fixe Dimension aus einem Kommentar übernehmen.

Profile und Audio ausschließlich auf dem ext4-Stick; atomare Revisionen und eine letzte gültige Sicherung ebenfalls dort. Akzeptiertes Kern-Audio für spätere Neuberechnung behalten, Alltags-Rohaudio standardmäßig nach der Auswertung löschen. Mit expliziter Freigabe gespeicherte Ergänzungsclips bleiben Teil der begrenzten Sammlung. Startlimit für diese Stimmaufnahmen: 20 MB pro Person; Wakeword-/Faktenarchive erhalten eigene Grenzen. Löschen einer Person entfernt auch Stimmproben, Prüfproben und Sicherungsrevisionen.

Migration:

- Vorhandene stabile ID unverändert übernehmen; weder aus dem Namen noch aus dem Vektor neu erzeugen. Der geprüfte GitHub-Stand adressiert Profile über Namen/Datei-Slugs und zeigt kein explizites ID-Feld. Falls der Zielstand ebenfalls keine ID hat, einmalig eine UUID vergeben und die Zuordnung atomar auf dem Stick sichern; Namensänderungen ändern sie danach nicht.
- Namens-Slug-Kollisionen gesondert prüfen: verschiedene Namen können dieselbe bereinigte Datei ergeben. Bereits zusammengefallene Identitäten lassen sich aus einem Mittelwert nicht wiederherstellen; in diesem Fall neue getrennte Profile anlegen und Fakten ausdrücklich zuordnen.
- Alte `print`-Int8-Vektoren weiter lesen. Modellherkunft zunächst prüfen; bei gesicherter Kompatibilität als ein Legacy-Zentrum einpacken, den alten Vektor und Metadaten erhalten. Ein Zentrum nicht dreifach kopieren und dadurch drei Bedingungen vortäuschen.
- Alte Zahl `count` ist keine Anzahl neuer, geprüfter Trainingsclips. Ein Legacy-Mittelwert ermöglicht ohne Einzelaufnahmen kein echtes Leave-one-out.
- Liegt ursprüngliches Audio vor, neue Embeddings nach dem gemeinsamen Pfad berechnen; anderenfalls einmal den Stimmteil nachholen. Faktenfragen werden dabei nicht erneut verlangt.
- Alte Profile bleiben auffindbar und manuell auswählbar; bis zur Kalibrierung lokale Zusatzbestätigung verlangen. Den bisherigen unsicheren Freigabeweg nicht als Kompatibilitäts-Fallback bewahren.
- Modellwechsel erfordert neue Embeddings aus Stick-Audio oder neues Einlernen. Kein Mittelwert zwischen Vektoren verschiedener Modelle, Dimensionen oder Vorverarbeitungen; auch bei gleicher Dimension nicht.

Fakten und künftige Gesprächszuordnung referenzieren `profile_id`. Mehrere Profile mit gleichem Anzeigenamen bleiben unterscheidbar. Bereits global gespeicherte Fakten nicht automatisch irgendeiner neuen Person zuschlagen; bei Migration ausdrücklich zuordnen oder als noch nicht zugeordnet belassen.

## 7. Lebendigkeit und Spoofing: Wirkung und Grenzen

### Was auf der Hardware realistisch ist

Zwei frische variable Nachsprechaufgaben: lokal erzeugter Text, neue Challenge-ID, Sollinhalt nur für diesen Versuch gültig, Vosk-Inhaltsprüfung und Sprechervergleich im Server-RAM. Der Pi zeigt Text, steuert Ausgabe/Aufnahme und misst leichte Qualitätsmerkmale; keine zusätzliche große Erkennung auf dem Pi.

Challenge-ID an Sitzung, Zielprofil und konkrete angefragte Aktion binden, nach einem Versuch verbrauchen, nach als Startwert 30 s verfallen lassen. Wiederholung erhält neue Werte; Antwort auf vorherige Challenge nie akzeptieren. TTL mit monotoner Serverzeit prüfen; sie ist ein Replay-Limit, kein Lebendigkeitsmaß. Profileinlernen bleibt eine lokal bestätigte Tätigkeit.

Mehrere unabhängig gezogene Werte erschweren das Abspielen einer vorab passenden Aufnahme. Sie sind hörbar und keine Geheimnisse. Schutz hängt von Wortlisten, Musteranzahl und tatsächlich geprüften Positionen ab; aus wenigen Sätzen keinen kryptografischen Sicherheitsgrad ableiten.

### Was damit nicht belegt wird

Ein Angreifer kann die neue Aufgabe hören und mit Echtzeit-TTS oder Voice Conversion beantworten; zusammengesetzte Aufnahmen können ebenfalls Inhalte erfüllen. Niedrige WER, natürliche Pausen, passende Tonhöhe und ein schneller Beginn beweisen weder Lebendigkeit noch Berechtigung. Replays der echten Stimme sind für ein Sprecher-Embedding gerade plausibel. Sprachsynthese, Stimmwandlung und Replay sind bekannte getrennte Angriffsklassen. [ASVspoof-Primärquelle](https://arxiv.org/abs/1911.01601)

Das gemeldete Pitch-TTS-Ergebnis darf nicht durch einen einfachen Tonhöhenfilter „repariert“ werden: Auch die echte Stimme verändert Tonhöhe, und bessere Angriffe umgehen solche Heuristiken. Gespeicherte TTS-Embeddings dienen der Angriffsauswertung, nicht einer universellen TTS-Sperrliste. Testweise eingelernte synthetische Stimmen werden als Testprofile markiert und nicht für persönliche Berechtigungen eingesetzt.

**Freigaberegel:** erkannte Person, bestandener Challenge-Inhalt und Berechtigung sind getrennte Ergebnisse. Gesprächszuordnung darf eine kalibrierte Stimme als wahrscheinliche Person verwenden. Persönliche Inhalte aus Erinnerungen/Kalender und Änderungen an Fakten, Kalender oder Stimmprofil verlangen standardmäßig zusätzlich eine frische Challenge und bewusste lokale Bestätigung für genau die betreffende Aktion. Erfolgreiche Challenge oder hoher Score allein können keine Freigabe erzeugen. Bei lokal bestätigter Wiederherstellung kann ein nicht erkanntes Profil ausdrücklich ausgewählt werden; das Ergebnis ist `manual`, nicht „an der Stimme erkannt“.

Eine Taste ist ein Nachweis bewusster Gerätebedienung, kein Identitätsfaktor gegen Personen mit physischem Zugriff. Die Regel setzt ein Gerät unter Kontrolle des Bedieners und vertrauenswürdige LAN-Endpunkte voraus. Für echte Zugangssicherheit wäre eine unabhängige Authentifizierung erforderlich; sie ist nicht Bestandteil dieses Auftrags. Ein zusätzlicher Anti-Spoofing-Klassifikator ist kein Standardbestandteil und würde eigene Modelle, Ressourcenmessung und Angriffstests benötigen.

## 8. Nachlernen im Alltag

**Standard zunächst:** automatisch nur geeignete Kandidaten vorschlagen, Übernahme per Taste E; Sprachzustimmung allein genügt nicht. Anzeige: „Stimme ergänzen · E übernimmt · B verwirft“. Bei sehr kurzen, unklaren, nicht erkannten oder im Gastmodus verarbeiteten Anfragen keine Kandidaten. Ein ausdrücklicher Ergänzungsmodus erlaubt schlecht erkannte echte Stimmen nach lokaler Profilwahl und frischer Aufnahme; nicht den Gastclip stillschweigend übernehmen.

Für einen Kandidaten gelten als Startwerte:

- Mindestens drei Sprachsekunden, bestandene Qualität, passendes Modell und eindeutiger Treffer.
- Score gegen **unveränderliche Ankerzentren** mindestens `tau + 0,08`; Abstand zum zweitbesten Profil mindestens `delta + 0,05`. Reicht der gültige Kosinusbereich dafür nicht aus, gibt es für dieses Profil kein automatisches Nachlernen.
- Kein eigener TTS-Ausgabeteil, keine Einlern-/Test-Challenge und keine offensichtlich gemischten Stimmen.
- Bestehende Fremdproben gegen den Kandidaten prüfen; auffällig ähnliche Kandidaten nicht übernehmen. Das kann Spoofing nicht zuverlässig erkennen.

Nach Bestätigung als begrenzte Ergänzungsprobe speichern, maximal zwei pro Tag und 24 insgesamt. Gleichartige Kopien verdrängen keine wichtigen Aufnahmebedingungen. Alte Ergänzungen nach einer festgelegten Regel pro Bedingung ersetzen; die 18 Kernanker und sechs Prüfproben bleiben unverändert. Keine unbeschränkt wachsende Gewichtung durch einen Lebenszeit-Zähler.

Kandidaten werden zunächst in einer Entwurfsrevision gehalten. Wenn neue Zentren gebildet werden, höchstens 20 % ihres Gewichts aus bestätigten Ergänzungen beziehen. Vor Übernahme alle gespeicherten echten Prüf- und Fremdproben erneut auswerten: keine neue Fremdannahme, keine Verschlechterung der bisherigen Prüfzuordnung. Schwelle nicht automatisch nach unten anpassen. Ein fehlgeschlagener Kandidat wird verworfen; atomare Revision ermöglicht Rückkehr zum alten Profil.

**Automatische Übernahme** bleibt per Vorgabe ausgeschaltet. Sie wäre erst nach erfolgreicher Alltags- und Angriffsauswertung, explizitem Opt-in und denselben Grenzen zulässig. Selbst deutlich über der Schwelle liegende Angriffe können die Bedingungen erfüllen: Anker, Obergrenzen und Rückprüfung begrenzen Drift, schließen sie nicht aus. Erkrankung, Flüstern oder ein neues Mikrofon sind Anlass für bewusstes Ergänzen bzw. neues Kalibrieren, nicht für selbstständiges Absenken der Schwelle.

## 9. Logging, Kennzahlen und Abnahmetest

### Zahlen ohne Inhalte

Für das neue Stimmverfahren feste numerische Felder/Enums protokollieren: zufällige Testlaufnummer, flüchtige Personenindexnummer, Bedingungscode, Dauer, VAD-Sprachsekunden, Sprachanteil, RMS-dBFS, Clippinganteil, SNR-Wert plus Gültigkeitsflag, WER, beste/zweitbeste Ähnlichkeit, Schwelle, Abstand, Ergebniscode, Challenge-Ergebniscode, Wiederholungsgrundcode, Modell-/Vorverarbeitungs-Versionsnummer, Profilrevision, Verarbeitungszeit und gegebenenfalls manuell vergebene Wahrheit im Testmodus. Fehlende Messwerte mit Gültigkeitsflag kennzeichnen, nicht als Nullwert erfinden.

Keine Namen, stabilen Personen-IDs, Transkripte, Sollsätze, freie Fehlermeldungen, Audio, Embedding-Werte oder deren Fingerabdrücke in Logs. Technische Versionsnummern dürfen über eine dokumentierte Zuordnung auf Modell-Checksummen verweisen. Numerische Profileindizes pro Testlauf neu vergeben; auch Zahlen können personenbezogene Messdaten darstellen.

Persistente Messlogs ebenfalls nur auf dem Stick; Server-Diagnose ausschließlich flüchtig und numerisch. HTTP-Zugriffslogs, Request-Bodies, Journald, temporäre WAVs, Fehlerausgaben und vorhandene Transcript-Logs für diesen Pfad prüfen. Serverseitig eingehendes Audio direkt als RAM-Puffer bearbeiten; keine Datei unter persistentem `/tmp`. Swap/Core-Dumps für diese Verarbeitung unterbinden bzw. die „nur RAM“-Vorgabe als noch nicht erfüllt melden. Personenbezogene Server-Caches bei Sitzungsende, Stick-Abmeldung und TTL-Ablauf verwerfen; keine persistente Cache-Wiederherstellung.

Start-Aufbewahrung: numerische Testlogs 30 Tage, danach löschen; dauerhafte Abnahmeübersicht nur aggregiert und auf dem Stick. Gespeicherte Referenz-/Gast-/TTS-Embeddings sind Profil-/Testdaten, keine Logdaten, und haben explizite Einwilligung/Herkunft und Löschregeln.

Kennzahlen: echte richtige Zuordnungen, Fehlablehnungen (FRR), Fremdannahmen (FAR), Verwechslungen bekannter Personen, Anteil `insufficient`/`ambiguous`, Treffer pro Bedingung, Challenge-Abweisung bei Replay und Challenge-Fehlablehnung echter Sprache, Wiederholungen, p50/p95-Latenz, Profilgröße und Nachlern-Änderungen. Identifikationsleistung und Freigabe persönlicher Aktionen getrennt auswerten.

### Unabhängige Abnahme am echten Gerät

Vorher Profil, Modell, VAD, Zentren und Schwellen einfrieren; Nachlernen während der Abnahme aus. Keine Testaufnahme für eine spätere Neuberechnung vor Ende des Testlaufs verwenden.

| Teil | Durchführung | Abnahmekriterium |
|---|---|---|
| Echte Alltagsstimme | 30 neue freie Anfragen an mindestens zwei Tagen: zehn nah, zehn aus etwa zwei Metern, sechs leise, vier schneller/fragend/müde. Morgen und Abend verteilen; je Anfrage 1,5–8 Sprachsekunden, mindestens sechs im kurzen Bereich 1,5–2,5 s. | Mindestens 29/30 korrekt, also 96,7 % und damit ≥95 %. Unklar/zu kurz im vorgesehenen Bereich zählt als nicht erkannt; kein Wegfiltern ungünstiger Fälle. |
| Fremd-/TTS-Grundtest | 20 neue Versuche: zwölf natürliche Fremdanfragen von mindestens vier Personen, vier synthetische inkl. Pitch-Variante, vier Replays echter und fremder Sprache über Lautsprecher. | 0/20 automatische Zuordnungen zur geschützten Person; Mehrpersonen-Verwechslungen ebenfalls null. Jede Fehlannahme dokumentieren und Kalibrierung verwerfen. |
| Challenge gegen feste Aufnahme | Zehn frische Aufgaben mit alten, inhaltlich unpassenden Aufnahmen beantworten. | 0/10 Inhaltsfreigaben; verbrauchte/abgelaufene Challenge immer abgelehnt. |
| Echte Challenge-Nutzung | 20 neue Aufgaben in natürlichen Bedingungen. | Mindestens 19/20 beim ersten Versuch; nach einer Wiederholung 20/20. Ergebnis liefert keine Lebendigkeitsgarantie. |
| Adaptives TTS/Voice Conversion | Soweit lokal verfügbar mindestens zehn Antworten auf aktuelle Aufgaben erzeugen. | Identitäts- und Inhaltsdurchgänge getrennt berichten; keine persönliche Aktion ohne lokale Bestätigung. Kein versprochener Nullfehler bei der akustischen Zuordnung. |
| Kurzsprache und Fehler | Unter einer Sekunde Sprache, lange Stille, fehlendes Modell, leeres/kaputtes Profil, Zeitüberschreitung, Stick abgezogen, unbekannte Modellversion. | Immer eingeschränkter Modus; keine persönlichen Daten, kein Faktenspeichern, kein Nachlernen. |
| Nachlernen/Migration | Bestätigte echte Ergänzungen, abgelehnte Fremdclips, Namensänderung, Stromunterbrechung beim Schreiben. | IDs bleiben stabil, Anker erhalten, Größenlimits eingehalten, bisherige Fremdtests bleiben negativ, letzte gültige Revision lesbar. |

Wird eine Replay-/TTS-Stimme akustisch akzeptiert, aber durch die lokale Freigabe blockiert, besteht der Test der Aktionssperre; das ist **kein** Bestehen des 0/20-Identifikationsziels. Schafft CAM++ dieses Ziel nicht, den Zielkonflikt offen melden statt die Messung umzubenennen.

29/30 und 0/20 sind kleine funktionale Abnahmetests, keine belastbare Sicherheitszertifizierung. Bei 0/20 liegt die einseitige 95-%-Obergrenze einer binomialen Fehlannahmewahrscheinlichkeit noch bei rund 13,9 % (`1 − 0,05^(1/20)`), selbst unter der hier fraglichen Unabhängigkeitsannahme. Nach Änderungen neue unabhängige Versuche erfassen; für verlässliche Raten viele weitere unterschiedliche Personen, Sitzungen und Angriffe benötigen.

## 10. Risiken und offene Fragen

- **Laufender Stand unbekannt:** GitHub-main wurde geprüft, der laufende Pi-/CT-Stand nicht. Das [Repository-README](https://github.com/0b-ivan/pi-voice-assistant/blob/97151afad7a7510c4cb8d991f9618400a23a334f/README.md#L1-L9) beschreibt WM8960-Mikrofone, der Auftrag ein USB-Mikrofon. Tatsächliches Aufnahmegerät, Rate, AGC, Hall, Bibliotheksversionen, Modell-Checksumme und IDs vor Änderungen erfassen; die Kalibrierung muss zum wirklich eingesetzten Mikrofon passen.
- **Eine Sitzung deckt keinen Alltag ab:** Müdigkeit, Krankheit, Tageszeit und Mikrofonwechsel brauchen spätere echte Aufnahmen. Nicht durch künstliche Tonhöhenänderung simuliertes Audio als gleichwertigen Echtbeleg verwenden.
- **Mehr Zentren verändern Scores:** Neue Fremdproben oder zusätzliche Personen können Schwellen ungültig machen. Neue Person und jede Profilrevision gegen alle übrigen Personen prüfen; Gesamt-FAR über alle Profile messen.
- **Stille und leise Sprache:** VAD kann leise Stimme verwerfen; Hall kann SNR-Schätzung täuschen. Startwerte anhand des realen Mikrofons überprüfen. Reine Mehrsprechererkennung ist mit den vorgesehenen Mitteln nicht zuverlässig abgesichert.
- **Physischer Zugriff:** Eine beliebige Person am Gerät kann eine Taste drücken oder den Stick lesen. Sprecherzuordnung, lokale Bedienung und Datenschutz auf einem unverschlüsselten Stick bieten keine starke Authentifizierung bzw. Verschlüsselung.
- **Vergleichsdaten fehlen möglicherweise:** Ohne bestätigte fremde Stimmen ist keine persönliche Sicherheitskalibrierung möglich. Referenzmaterial muss rechtmäßig nutzbar und ausreichend ähnlich zur Aufnahmeumgebung sein; TTS allein genügt nicht.
- **Datentrennung ist mehr als eine ID:** Alte globale Fakten und Gesprächsverläufe dürfen keine persönliche Zuordnung vortäuschen. Umstellung des gesamten Gesprächsgedächtnisses bleibt ein späterer Auftrag; dieses Konzept liefert IDs, Zustände und sichere Schnittstellen dafür.
- **Server-Ressourcen:** Vosk, TTS und weitere Dienste teilen CPU/RAM. Embedding-Arbeit zunächst seriell bzw. mit begrenzter Warteschlange; Threadzahl messen. Keine neue große Modellabhängigkeit im Standardweg.

### Vergleich mit anderen Projekten: was übernommen wird

Die folgenden Befunde stammen aus direkt gelesenem Projektcode bzw. der Originaldokumentation. Die Konsequenzen für Proximus sind Entwurfsentscheidungen, keine von diesen Projekten bestätigten Garantien.

| Projekt / gelesener Pfad | Befund | Konsequenz für Proximus |
|---|---|---|
| [sherpa-onnx, Speaker-Identification-Beispiel](https://github.com/k2-fsa/sherpa-onnx/blob/9db1af1871ed78cee9357c23898e5a7d8702d21a/python-api-examples/speaker-identification.py) | Mittelt mehrere Dateien; Beispielschwelle 0,6; reicht echte Samplerate weiter und prüft beim Referenzaufbau `is_ready`. | Mittelwert als Kontrollvariante behalten, Modellbereitschaft prüfen; Beispielschwelle nicht übernehmen. |
| [SpeechBrain, `inference/speaker.py`](https://github.com/speechbrain/speechbrain/blob/89ead74d163463d30c62329a09cfdb4c54f5abc1/speechbrain/inference/speaker.py#L57-L99) | Berechnet `CosineSimilarity` und entscheidet per `score > threshold`, Default 0,25. | Rohkosinus und Grenzvergleich explizit definieren; selbst eine etablierte Bibliothek liefert keine universelle Schwelle. |
| [WeSpeaker, `cli/speaker.py`](https://github.com/wenet-e2e/wespeaker/blob/9fecd6cb4f47475d01761d87c826298dff4ef18c/wespeaker/cli/speaker.py#L130-L211) | Resampling/VAD vorgesehen; `cosine_similarity` liefert `(cos + 1)/2`. `recognize()` liefert den besten registrierten Namen ohne eigene Unknown-Schwelle. | Scoreadapter und eigene Fremdablehnung zwingend. **Aus dem Code folgt:** WeSpeaker-Score 0,5 entspricht Rohkosinus 0, nicht Proximus-Kosinus 0,5. |
| [openWakeWord, Custom Verifiers](https://github.com/dscripka/openWakeWord/blob/368c03716d1e92591906a84949bc477f3a834455/docs/custom_verifier_models.md) | Kleine zusätzliche Filter und Aufnahmen aus der tatsächlichen Einsatzumgebung; Negativmaterial umfasst Sprache ohne Wakeword und typische Fehlaktivierungen. | Getrenntes Wakeword-Modul und alltagsnahe Daten beibehalten. Diese Negativdefinition und die dort kleine Clipzahl nicht als Kalibrierung einer allgemeinen Sprechererkennung übernehmen. |
| [py-webrtcvad](https://github.com/wiseman/py-webrtcvad/blob/e283ca41df3a84b0e87fb1f5cb9b21580a286b09/README.rst) | Klare PCM-/Frame-Vorgaben, Aggressivität 0–3. | Leichte Sprachdauerprüfung vor Embedding; aggressive Einstellung kann leise Sprache verlieren und muss geprüft werden. |

Keines dieser Verfahren liefert allein den gewünschten Lebendigkeitsnachweis. Andere Projekte begründen hier klarere Audio-, Score- und Testgrenzen; ein neuer Softwarestapel ist dafür nicht erforderlich.

### Optionale Modellvergleiche

CAM++ bleibt zunächst bestehen. Sein Trainingsbenchmark beweist keine Leistung bei deutschen Kurzbefehlen über dieses USB-Mikrofon; zugleich ist nicht belegt, dass ECAPA-TDNN oder WeSpeaker hier besser wären. Die CAM++-Autoren beschreiben ein auf effiziente Sprecherverifikation ausgelegtes Modell. [CAM++-Originalarbeit](https://arxiv.org/abs/2303.00332)

| Option | Größe und Lizenz des konkreten Artefakts | CPU-Zeit auf i5-7500T / CT 107 |
|---|---|---|
| SpeechBrain `spkrec-ecapa-voxceleb` | Embedding-Gewichte ca. 83,3 MB, 192-dimensional; Modellkarte Apache-2.0. PyTorch/SpeechBrain benötigen zusätzlichen Laufzeit-RAM. [Dateien](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/tree/main), [Konfiguration](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb/blob/main/hyperparams.yaml), [Modellkarte](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) | **Nicht gemessen und nicht seriös aus Dateigröße ableitbar.** Optionales Abnahmebudget: warme Embedding-Inferenz p95 ≤1 s für vier Sekunden Sprache, Gesamtdienst ohne Swap und innerhalb 7 GB RAM. |
| WeSpeaker `wespeaker-voxceleb-resnet34-LM` | ONNX ca. 26,5 MB, 256-dimensional; Modellkarte CC-BY-4.0, Attribution vorsehen. Toolkit-Lizenz getrennt vom Modell behandeln. [Dateien](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/tree/main), [Modellkarte](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM) | **Nicht gemessen.** Dasselbe Budget; ONNX-Ladepfad und Vorverarbeitung zuerst auf Kompatibilität prüfen. |

Es gibt in den herangezogenen Quellen keine belastbare Zeitmessung für diesen CT. Auch „~40 ms“ im Kommentar der geprüften GitHub-Datei `speaker.py` ist ohne Testprotokoll kein Benchmark. Keine erfundenen Laufzeiten als Entscheidungsgrundlage verwenden.

Optionaler Vergleich: dasselbe lokale Korpus aus 2-/4-/8-s-Clips, fünf Aufwärmdurchläufe, mindestens 100 Messungen je Modell/Längenklasse, ein und zwei Threads, anschließend parallel zu gewöhnlichem Vosk/TTS-Betrieb messen. p50/p95-Wandzeit, CPU-Zeit und Spitzen-RAM ausweisen; Modellladen separat erfassen. Entscheidung nach besserer Zuordnung bei gleichem Fremdannahmelimit und passender Last, nicht nach publizierter VoxCeleb-EER. Bei Modellwechsel vollständige Neukalibrierung und Migration aus Audio. Dieser Versuch ist optional, kein Teil der Standardimplementierung.

## 11. Auftrag für Claude Code: Dateien und überprüfbare Etappen

**Umfang:** das Konzept implementieren, Tests und lokale Prüfanleitungen liefern. Kein Deployment, kein Neustart produktiver Dienste, keine automatische Migration oder Veränderung produktiver Stick-Daten. Synced Material unter `sources/` bleibt unverändert. Analysebasis ist GitHub-Commit `97151afad7a7510c4cb8d991f9618400a23a334f`; vor Umsetzung Unterschiede zum ausgewählten Zielbranch prüfen und dokumentieren.

### Dateiänderungen

| Datei | Konkrete Änderung |
|---|---|
| `src/enroll.py` | Bisherige Kopplung Wakeword → Fakten → Mittelwert auflösen. Abbrechbare Zustandsmaschine für Module; Ziel-ID binden; 24-Satz-Plan mit 18 Trainings-/sechs Prüfclips, Qualität/Wiederholung, Deadline, optionalem Baseline-Teil und Entwurf/Commit. `_refine()` mit fünf bzw. gruppierten Wakewords durch ausdrückliches Ergänzen echter Sätze ersetzen; keine abgesenkte Zuordnungsschwelle wie `REFINE_MATCH`. Die sieben Faktenfragen erhalten, getrennt und optional. |
| `src/speaker.py` | Gemeinsame PCM-/VAD-/Qualitätsfunktionen; tatsächliche Sprachlänge statt Dateilänge; sicheres Ergebnis bei zu wenig Sprache. Strikte Vektorvalidierung, V1-/V2-Codecs, drei Zentren, persönliche Schwellen, Mehrpersonenabstand, LOO-Kalibrierung, begrenzte Nachlernrevision. `identify()` liefert strukturierte Zustände statt allein `(name, score)`. Modell/Vorverarbeitung unverwechselbar versionieren. |
| `server/servitor_server.py` | `_identify()` in allen Fehlerpfaden auf eingeschränkten Modus setzen; kein unveränderter persönlicher Kontext als Fehlerfallback. Stimmverarbeitung ausschließlich in RAM. Gemeinsamer Pfad für Alltag und Einlernen, qualifizierte Antwort, Challenge-Sitzungen und begrenzte Inferenzwarteschlange; persönliche Daten erst nach zutreffender Aktionsfreigabe an weitere Komponenten geben. Freie Audio-/Transkript-Fehlerlogs dieses Pfads entfernen. |
| `src/memory.py` | V2-Profile, stabile IDs, atomare Revisionen und Limits; Legacy-Reader/Migrationsentwurf ohne Produktionsschreibzugriff. `save_voiceprint()` darf V2 nicht mit einem gewichteten Lebenszeit-Mittelwert verändern. `sanitize()` akzeptiert die begrenzten V2-Felder; `context()`/`guest_view()` sichern unbekannt, unklar und Fehler ab und geben nur öffentliche Persona-Direktiven frei. `unknown_speaker()` durch explizite Identitäts-/Freigabezustände ergänzen. Faktenfragen bis bestätigtem Commit als Entwurf halten; Schnittstelle `profile_id` für späteres Personengedächtnis. |
| Neu: `src/enroll_phrases.py` | Kuratierte Wörter/Muster, Laut-Tags, Satzplan, variable Baseline-Antworten, Normalisierung und WER-Sollwerte. Gemeinsame Nutzung durch Pi und Server; kein Dialogdispatcher für Testtexte. |
| UI-/Transportdateien | `src/ptt.py`, `src/display.py`, `src/people.py`, `src/protocol.py`, `src/remote_turn.py`: Einlern-IO um abbrechbare Aufnahme/Prüfung erweitern, Display und lokale Bestätigung anbinden, V2-Profile in begrenzter Core-Nachricht statt Header übertragen, Zustände bis zum Pi erhalten. Lokalen Rückfall auf öffentliche Funktionen begrenzen, persönliche Aktionen nur explizit lokal freigeben. In `people.py` `WEAK_VOICE`-Passphrase-Ausnahme ersetzen, Score-Prozentanzeige und Namenslogs entfernen. Statische Passphrase bleibt kompatible Metadaten, kein Freigabeersatz. |
| Tests / Prüfdokumentation | Bestehende Einlern-, Server-, Memory- und Transporttests erweitern; neue fokussierte Tests für Qualität, Kalibrierung, Generator, Challenge-Lebenszyklus und Migration. Echte Audioabnahme als lokal ausführbares Verfahren mit numerischem Bericht. |

### Schnittstellenvertrag

Identitätsergebnis mindestens: `status = matched | unknown | insufficient | ambiguous | unavailable | unverified | manual`, `profile_id` nur bei gültiger Zuordnung, beste/zweitbeste Ähnlichkeit, persönliche Schwelle, Qualitätsstatus und Revision. Aktionsfreigabe separat, standardmäßig `false`; keine Interpretation von fehlendem Feld als Bediener. `manual` darf nur ein bewusst bestätigter lokaler Auswahlvorgang erzeugen.

V2-Profilkontext nur im begrenzten Request-Body bzw. RAM-Session-Core übertragen. Vorgeschlagenes Startlimit: maximal fünf aktive Profile, Dimension ≤1024, 48 Proben und drei Zentren pro gespeichertem Profil, gesamte V2-Core-Nachricht ≤2 MiB. Im Alltagskontext nur Vergleichszentren, feste Ankerzentren und nötige Metadaten übertragen; Einzelproben, Rohclips und Fremdkorpora bleiben auf dem Stick bzw. im gesonderten Kalibrierungspfad. Tatsächliche Modell-Dimension zusätzlich exakt prüfen; generelles Größenlimit genügt nicht.

Vorgeschlagene neue, versionierte Serverwege; bei vorhandener gleichwertiger Schnittstelle dort integrieren:

- `POST /v2/enroll/session`: Sitzung eröffnen; Modus, Ziel-ID und gebundene lokale Geräteaktion übernehmen. Nur RAM, bestehende Geräte-/LAN-Zugriffskontrolle nutzen.
- `PUT /v2/session/{id}/core`: begrenzten V2-Kontext im Body in die bestehende RAM-Sitzung übernehmen, Digest/Revision bestätigen. Kein Transport der großen V2-Profile über `X-Servitor-Memory` oder die auf 16 KiB begrenzte V1-Message-Hülle.
- `POST /v2/enroll/session/{id}/challenge`: nächste neue Aufgabe und Challenge-ID ausgeben; geplante Bedingung und Cliprolle in der Sitzung halten.
- `POST /v2/enroll/session/{id}/clip?challenge_id=…`: begrenztes PCM, geprüfte Metadaten; Antwort mit Qualitätszahlen, Ergebniscode, WER und nur bei angenommener Aufnahme einem kompatiblen Embedding. Solltext aus Server-Sitzung, nicht aus ungeprüft vom Client mitgeliefertem „Beweis“ beziehen.
- `DELETE /v2/enroll/session/{id}`: sofort abbrechen und Nutzdaten verwerfen; zusätzlich kurze TTL für verwaiste Sitzungen.

Für Alltagsschutz dieselbe Challenge-Logik an eine einmalige Aktions-ID binden. Aufnahmetexte und Transkription bleiben in diesem Prüfpfad; keine Dialogaktion ausführen. Bestehendes `/v1/voiceprint` bei benötigter Kompatibilität erhalten, aber kein V2-Kalibrierungs-/Freigabenachweis daraus ableiten. Feature-Erkennung vor V2-Beginn; alter Server → klare Meldung und eingeschränkter Betrieb, kein stiller unsicherer Rückfall.

### Etappen mit Tests und Abschlusskriterien

**Etappe 1 — Iststand und Fehlerfreigabe.** Audio-/Profilpfade im Zielstand dokumentieren und die Fehlertypen aus Abschnitt 2 reproduzieren. Strukturierte Zustände einführen und serverseitige Gastansicht einschließlich persönlicher Schreibsperren erzwingen.

Tests: unter einer Sekunde echte Sprache auch in langer PCM-Datei, reine Stille, kein Modell, Ausnahme, kaputtes Profil, keine gültige ID, Transportfehler und lokaler Pi-Rückfall; alle sperren ohne ausdrückliche lokale Freigabe persönliche Inhalte und Änderungen. Statische Passphrase mit 0,3–0,5 allein hebt die Sperre nicht auf; persönliche Direktiven bleiben im Gastmodus verborgen. Eine gewöhnliche Gastanfrage bleibt benutzbar. Abschluss: kein Fehlerweg behauptet eine erkannte Person.

**Etappe 2 — Audioqualität und Schnittstellengleichheit.** Gemeinsamen PCM-/VAD-Pfad sowie numerische Qualitätsantwort implementieren. Signalton/TTS zeitlich ausschließen und temporäre Eingangs-WAVs durch RAM-Puffer ersetzen.

Tests: int16-LE-Konvertierung und echtes Resampling; Stille, viel Pause, Clipping, niedriger Pegel, ungültige SNR-Probe; echte lokale Nah-/Fern-/Leise-Fixtures zusätzlich prüfen. Gleiche PCM-Daten über Alltag und Einlernweg liefern gleiche VAD-Ergebnisse und Embeddings innerhalb definierter numerischer Toleranz; Modell-Selbstvergleich nahe 1. Vektor-Rundlaufänderung separat messen. Abschluss: verworfene Clips erzeugen weder Profilproben noch personenbezogene Server-Dateien.

**Etappe 3 — Profile und Kalibrierung.** V2-Schema, ID-Migration, drei Zentren und exakte LOO-Regel implementieren; Datenbank-/Stickänderungen zunächst nur auf Testkopie.

Tests: aus jeder LOO-Faltung ist der Prüfclip vollständig ausgeschlossen; Zentrumsauswahl wird neu berechnet. Separater Prüfdatensatz bleibt außerhalb. Synthetische Vektorfixtures für separierte/überlappende/vertauschte Verteilungen, Grenzwerte einschließlich `score == tau`, mehrere Personen, niedriger Zweitschwellenwert, fehlende Fremdproben, Modellmismatch und Nullnorm. Legacy-Rundlauf, doppelte Anzeigenamen, Namenswechsel, atomarer Schreibabbruch und stabile IDs. Abschluss: nicht trennbare Verteilungen erzeugen kein automatisch freigegebenes Profil.

**Etappe 4 — Generator und Einlern-Dialog.** Module, Satzplan, vollständige 24-Clip-Sitzung, optionale zwölf Baseline-Paare, separate Fakten und Anzeige anbinden. Gesteuerte Testzufälligkeit getrennt vom Produktionszufall.

Tests mit Fake-Uhr/Fake-IO: acht Clips je Bedingung, sechs Reservierungen, minimale Sprachmenge, neue Sätze bei Wiederholung, Laut-Tags gegen kuratiertes Lexikon, kein Satzduplikat, Grenzen von Versuchen und 420 s, B-Abbruch in jeder Phase und während blockierender IO. Bestehende sechs Hardwareaufnahme-Retries und 30-s-Netzwerk-Timeouts der Deadline unterordnen. Profil/Fakten erst nach Prüfung und E schreiben; Abbruch lässt Vorgänger intakt und löscht Entwurfsaudio. Fakten-/Wakewordmodule unabhängig, Testtexte erzeugen keine Fakten. Abschluss: Zeitgrenze eingehalten oder sauberer Abbruch ohne minderwertigen Commit.

B-Abbruch muss die UI innerhalb eines Startbudgets von 300 ms umschalten und Ausgabe/Aufnahme beenden; nicht erst auf einen Netzwerk-Timeout warten. Netzwerkoperation abbrechen bzw. abgekoppelt beenden, verspätete Antworten nach Abbruch verwerfen und Server-Sitzung bestmöglich löschen. Den neuen Core-Body-Pfad mit Größenlimit, falscher Revision, altem Server und abgezogenem Stick testen.

**Etappe 5 — Challenge und Aktionsfreigabe.** Frische Aufgaben, WER/Prüfwortprüfung, verbrauchte IDs/TTL und eindeutige Aktionsbindung implementieren. Lokale Bestätigung durch die tatsächliche Geräte-UI, kein beliebiges Freigabefeld aus einer Sprachantwort.

Tests: passende/unpassende Texte, Einfügungen/Löschungen, Zahlennormalisierung, korrektes WER bei falschem Prüfwort, alte ID, falsche Sitzung/Person/Aktion, abgelaufene Aufgabe, doppelte Nutzung und TTS-Ausgabe im Aufnahmefenster. Korrekte Challenge ohne lokale Bestätigung darf keine geschützte Aktion auslösen; hohe Sprecherähnlichkeit ebenso wenig. Abschluss: der berichtete Faktenspeicher-Angriff bleibt ohne bewusste Gerätefreigabe gesperrt, unabhängig vom CAM++-Score.

**Etappe 6 — Nachlernen und Datenhaltung.** Bestätigte Kandidaten, Tages-/Gesamtlimits, feste Anker, Entwurfsrevision und Regression gegen echte/fremde Prüfproben implementieren. Numerische Logs auf Stick begrenzen, Sitzungscaches löschen.

Tests: kein Lernen aus Gast/Unklar/zu kurz; Nachlernscore gegen feste Anker statt adaptive Zentren; Tageslimit, Ersetzung, 20-%-Gewichtsgrenze, keine automatische Schwellenabsenkung, neue Fremdannahme verhindert Commit, Rollback, Stickverlust und vollständige Profillöschung. Prüfen, dass Inhalte weder in numerischen Logs noch in persistenten Server-Dateien landen; vorhandene HTTP-/Transcript-Logs dieses Pfads einschließen. Abschluss: automatische Übernahme standardmäßig aus, Datenlimits eingehalten.

**Etappe 7 — Vorbereitete Hardwareabnahme und Übergabe.** Lokal ausführbare Testanleitung und Berichtsvorlage für Abschnitt 9 liefern; wenn echte Hardware und Testpersonen im Ausführungsauftrag verfügbar sind, die Abnahme ohne produktive Migration durchführen. Anderenfalls ausdrücklich „Hardwareabnahme offen“ melden, keine bestandenen Werte erfinden.

Tests: relevante vorhandene Einlern-/Memory-/Server-/UI-/Transportregressionen, dann unabhängige Audioabnahme und Ressourcenmessung. Optionales Zweitmodell nur bei verbleibendem Aufnahme-/Modellproblem und als gesonderten Versuch. Abschlussbericht nennt Änderungen, geprüften Repository-Stand, Softwaretests, echte Messergebnisse, offene Hardwarepunkte und Migrationsverfahren. Kein Deployment im Auftrag.
