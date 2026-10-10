# Fahrten: Straßenbahn, Zug, Erinnerung, Kalender

Proximus beantwortet Fahrplanfragen für WVV-Straßenbahnen und DB-Züge mit gerouteter Gehzeit und bietet danach optional eine Erinnerung am Gerät und/oder einen Kalendereintrag an. **Eine Fahrplanfrage schreibt nie etwas.** Geschrieben wird erst nach einem konkreten Vorschlag und dessen Bestätigung.

Code: [`src/transit.py`](../src/transit.py) (MoBY/EFA-Adapter), [`src/journeys.py`](../src/journeys.py) (Erkennung und Controller auf dem Pi), CalDAV-Schreiben in [`src/agenda.py`](../src/agenda.py). Tests: `tests/test_transit.py`, `tests/test_journeys.py`, `tests/test_journey_paths.py`.

## Bedienung

| Sagen | Ergebnis |
|---|---|
| „Wann fährt die nächste Straßenbahn in die Stadt?“ | fragt nach dem Start, wenn keiner genannt ist („Von zu Hause oder ab welcher Haltestelle?“) |
| „Wie komme ich von zu Hause nach Nürnberg?“ | bis zu drei erreichbare Verbindungen |
| „Wann fährt der nächste Zug ab Würzburg Hbf?“ | nächste Abfahrten an diesem Bahnhof |
| „Nur Regionalzüge“ | dieselbe Anfrage ohne ICE/IC |
| „Die zweite“ | wählt eine Verbindung |
| „Erinnere mich daran“ / „Trag die Fahrt in meinen Kalender ein“ / „Beides“ | konkreter Vorschlag mit Zeiten, Kalender und Hinweis |
| „Ja“ oder Taste **E** | führt genau diesen Vorschlag aus |
| „Nein“, „Abbrechen“, Taste **B**, „Stop“ | nichts wird ausgeführt |

Jede Verbindung nennt Linie bzw. Zugnummer, Richtung, Abfahrt, Ankunft, Gehzeit und Losgehzeit, Bahnreisen zusätzlich Umstiege und Gleise. Ohne Echtzeitdaten sagt Proximus „Laut Fahrplan“, bei Verspätung „… Minuten später als geplant“. Keine Ticketpreise, keine Buchung, keine Überwachung gespeicherter Fahrten.

„Von zu Hause“ nutzt `TRANSIT_HOME`. „Hier“ bedeutet nie automatisch zu Hause: der Pi hat keine Ortung und fragt nach. „In die Stadt“ braucht `TRANSIT_CITY_STOP`; ohne diesen Wert fragt Proximus nach einer Haltestelle. Andere Namen sucht der Stop-Finder, die gewählte Haltestelle wird in der Antwort genannt.

## Ablauf und Zustimmung

Auskunft → Auswahl → Vorschlag → Zustimmung → Ausführung → Ergebnis.

- Der Server (CT 107) erkennt nur, dass eine Fahrtfrage gestellt wurde, und prüft wie gewohnt die Stimme. Er schickt ein `journey`-Ereignis mit der strukturierten Anfrage, dem erkannten Sprecher und der Vorschlags-ID, dann `done`, **ohne Audio und ohne LLM**. Der Pi fragt den Fahrplan selbst ab und spricht die Antwort. Startadresse und Kalenderzugang verlassen den Pi nicht. Ohne Server läuft derselbe Controller über den lokalen Weg.
- Der Pi handelt nur nach einem vollständigen Turn (`done` empfangen). Bricht der Strom ab, beantwortet er den Turn lokal; die einmalige Ausführung je Vorschlags-ID verhindert doppelte Aktionen.
- Es gibt höchstens eine offene Bestätigungsfrage. Sie gehört zu einer Vorschlags-ID und wird vom nächsten Turn (oder von B/E) verbraucht. Jede andere Antwort verwirft sie.
- Die Frage gilt 90 Sekunden nach vollständiger Ansage (höchstens 5 Minuten ab Vorschlag).
- Per Stimme bestätigen kann nur dieselbe Stimme, die den Vorschlag ausgelöst hat; ein nicht erkannter Gast nie. Taste E gilt immer. Gäste bekommen keine Vorschläge und keinen Start „zu Hause“.
- Vor der Ausführung fragt der Pi die Fahrt erneut ab. Ändert sich die Abfahrt, Losgehzeit oder Ankunft um zwei Minuten oder mehr, das Abfahrtsgleis, oder fällt die Fahrt aus, gibt es einen neuen Vorschlag statt einer Ausführung. Ist der Fahrplan nicht erreichbar, wird nichts geschrieben.

## Gehzeit

Nur geroutete Fußwege aus `XML_TRIP_REQUEST2`, keine Luftlinie und keine LLM-Schätzung. Die Ansage rundet auf volle Minuten auf.

- **Direkte Straßenbahn/Bus:** Losgehen = Abfahrt − gerouteter Zugang − Puffer (`TRANSIT_BUFFER_MINUTES`, Standard 2). Wartezeit an der Haltestelle zählt nicht als Gehzeit.
- **Bahnreise:** Losgehen = Beginn der gesamten Reisekette − Puffer. Anschlüsse und Umsteigewege bleiben wie geroutet.
- Vergangene, ausgefallene und nicht mehr erreichbare Fahrten sowie reine Fußwege fallen weg. Fehlt eine Wegdauer, heißt es „Gehzeit unbekannt“ und es gibt keine Erinnerung. Fehlt eine Ankunft, wird keine erfunden; der Kalendertermin hat dann kein Ende.
- Echtzeit gilt nur für Abschnitte mit `isRealtimeControlled`; ein `…Estimated`-Feld allein reicht nicht.

## Kalender und Erinnerung

- **Kalender:** eine Reise ergibt einen Termin (`PUT` mit `If-None-Match: *`, `text/calendar`) von der ersten Fahrzeugabfahrt bis zur Ankunft, mit den Fahrtabschnitten in der Beschreibung und einem VALARM zur Losgehzeit. Die Ressource heißt nach einer stabilen UID der Fahrt; dieselbe Fahrt wird nie doppelt angelegt („steht bereits im Kalender“). Bei einer unklaren Zeitüberschreitung prüft der Pi genau diese Ressource. Erfolg wird erst nach `201/204` (oder bestätigter Existenz) gemeldet, `401/403` als verweigert. Danach wird die Tagesagenda neu geladen. Bei mehreren Kalendern fragt Proximus nach (oder `CALDAV_WRITE_CALENDAR`). Ob das Handy erinnert, hängt von dessen Kalender-Sync und Benachrichtigungen ab.
- **Erinnerung am Gerät:** wird genau einmal über die normale Alarmansage gesprochen, zur Losgehzeit (bei einer Abfahrtstafel einige Minuten vor Abfahrt, so angesagt). Sie liegt nur im Arbeitsspeicher; der Pi muss laufen, ein Neustart löscht sie. Das sagt der Vorschlag vor der Zustimmung. Ist die Fahrt verpasst (mehr als 3 Minuten nach der Losgehzeit oder nach Abfahrt), sagt Proximus nichts.
- **„Beides“:** Erinnerung und Kalendereintrag werden getrennt gemeldet. Scheitert der Kalender, bleibt die Erinnerung bestehen; es gibt keinen heimlichen Rollback.

## Einrichten

In `/etc/pi-voice-assistant.env` auf dem Pi (Vorlage: [`config/client.env.example`](../config/client.env.example)):

```text
TRANSIT_HOME=<Breitengrad>,<Längengrad>   # privat, nur hier
TRANSIT_CITY_STOP=<Stop-ID>               # vom Bediener festgelegt
```

Dann `sudo systemctl restart pi-ptt.service`. Der Kalender nutzt die vorhandenen `CALDAV_*`-Werte; das App-Passwort braucht Schreibrecht auf den gewählten Kalender. Der Server braucht keine neue Einstellung, nur den aktuellen Stand (`server/install-ct.sh`). Ohne ihn beantwortet das LLM Fahrtfragen wie bisher.

Stop-IDs finden (öffentliche Abfrage, ohne Startadresse):

```sh
curl -s -A 'pi-voice-assistant/1' 'https://whitelabel.bahnland-bayern.de/efa/XML_STOPFINDER_REQUEST?outputFormat=rapidJSON&version=11.0.6.72&language=de&type_sf=any&name_sf=Würzburg%20Rathaus' \
  | python3 -c 'import json,sys; [print(l.get("properties",{}).get("stopId"), l["name"]) for l in json.load(sys.stdin)["locations"]]'
```

Bekannte IDs: Rathaus `3700315`, Hauptbahnhof West (Straßenbahn) `80029080`, Würzburg Hbf (Züge) `80001152`, Nürnberg Hbf `80001020`.

Journal: `journey`-Events (`options`, `proposed`, `confirmed`, `done`, `changed`, `cancelled`, `expired`, `voice_refused`, `reminded`, `reminder_missed`, `query_failed`). Startadresse, Koordinaten und Passwörter stehen nicht darin.

## Offene Voraussetzungen

- **Zielhaltestelle für „in die Stadt“** muss der Bediener festlegen (`TRANSIT_CITY_STOP`). Rathaus war nur ein Testziel.
- **`TRANSIT_HOME`** auf dem Pi eintragen. Der geocodierte Hauspunkt ist kein vor Ort geprüfter Eingang; die erste Gehzeit kann um eine Minute abweichen.
- **Live-Abnahme auf dem Pi:** Die Tests laufen gegen nachgebaute rapidJSON-Antworten in der Form der Live-Antworten vom 10.10.2026. Aus der Entwicklungsumgebung war MoBY nicht erreichbar. Eine echte Fahrt (Straßenbahn und ICE), ein bewusst bestätigter Testeintrag in einem Testkalender und eine Erinnerung sind auf dem Gerät noch zu prüfen.
- **CalDAV-Schreibrecht** des App-Passworts und ein Testkalender: es wurden keine echten Einträge angelegt.
- **Sprachausgabe:** Fahrtantworten synthetisiert der Pi selbst (Piper lokal), auch auf dem Serverweg. Lange Antworten mit drei Verbindungen brauchen dadurch spürbar länger als Serverantworten.
- **Vosk-Erkennung** von Haltestellennamen und Ordinalzahlen mit echter Stimme ist ungeprüft; unbekannte Namen landen beim Stop-Finder, der die gewählte Haltestelle nennt.
