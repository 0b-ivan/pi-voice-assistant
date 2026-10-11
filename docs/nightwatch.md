# Nächtliche Prüfung mit lokalem Sprachkern

Proximus prüft einmal pro Nacht den installierten Softwarebestand, die vorhandene
Logauswertung und Teile seiner Sprachpipeline. Bekannte Sicherheitslücken,
wartende Updates und Verbesserungsvorschläge werden auf dem Speicherkern
gespeichert und beim nächsten Morgenbericht genannt. Die KI-Auswertung läuft
direkt gegen llama-server auf CT 107. Der Nachtlauf ruft weder OpenRouter noch
den normalen LLM-Adapter auf; auch bei Ausfall des lokalen Modells gibt es
keinen Cloud-Fallback. Lokale Rechenzeit und Strom werden weiterhin gebraucht.

Die vertiefte [Beschreibung der Nachtarbeit](nightwatch-design.md) erklärt
Nachweise, Sicherheitsprioritäten, die Grenzen des Trainings und die noch
offenen Ausbaustufen.

## Wann die Prüfung startet

Standard: 02:00–05:00 Uhr **Europe/Berlin**, nach 20 Minuten ohne Bedienung,
mit externer Stromversorgung, erreichbarem LAN-Server und vorhandenem
Speicherkern. PTT, Sprachausgabe, Geschichten, Menü, Alarme, Bluetooth-Aufträge
und Wartung haben Vorrang. Die Prüfung läuft in eigenen Hintergrundthreads;
die Bedienung muss nicht auf Paketabfragen oder Internet warten. Bedienung
bricht einen laufenden Job ab. Lokale KI-Anfragen werden dabei geschlossen;
bereits gestartete kurze Vosk/Piper-Aufrufe enden an der nächsten Prüfgrenze.
Ein Serverjob hat höchstens 20 Minuten Laufzeit und erzeugt keine hörbare
Testausgabe. Ein Fehlversuch wird erst in der nächsten Nacht wiederholt.

In `/etc/pi-ptt.env`:

```ini
PTT_NIGHTWATCH=1
PTT_NIGHTWATCH_START=2
PTT_NIGHTWATCH_END=5
PTT_NIGHTWATCH_IDLE_SECONDS=1200
```

Auch ein Fenster über Mitternacht ist möglich, etwa 22 bis 5 Uhr. Sommerzeit
erzeugt durch die gespeicherte Nachtkennung keinen zweiten Lauf. Mit
`PTT_NIGHTWATCH=0` wird der Start auf dem Pi ausgeschaltet; mit
`SERVITOR_NIGHTWATCH=0` in `/etc/servitor-voice.env` wird zusätzlich der
Server-Endpunkt deaktiviert. Der lokale Modell-Endpunkt ist
`LOCAL_LLM_URL=http://127.0.0.1:8766/v1/chat/completions`; nur die wörtlichen
Loopback-Adressen `127.0.0.1` und `::1` sind für Nachtinferenz erlaubt.

## Was geprüft wird

| Prüfung | Ergebnis und Grenzen |
|---|---|
| Logs | Übernimmt feste Fehlercodes, Häufigkeiten und Beobachtungszeiten aus der bestehenden Logüberwachung auf Pi und CT 107: eigenes Dienstjournal sowie Systemwarnungen der letzten 24 Stunden, alle 30 Minuten gelesen. Der Prüfzeitpunkt muss höchstens eine Stunde alt sein. Ein nachfolgender Wiederherstellungsnachweis kann einen Fehler erledigen; alte oder eingeschränkte Auswertungen können das nicht. Rohe Logs bleiben lokal. |
| Betrieb | Wenig freier RAM/Datenträger und wartende Pi-/Server-Updates werden als offene Punkte abgelegt. Der Updatebestand stammt aus der bestehenden Simulation und hängt von der Aktualität der APT-Listen ab. |
| Debian-Pakete | Erfasst tatsächlich installierte Binär- und Quellpaketversionen mit `dpkg-query`, gleicht die passende Distribution und Ausgabe mit dem [Debian Security Tracker](https://security-tracker.debian.org/tracker/) ab und berücksichtigt Debian-Backports durch `dpkg --compare-versions`. |
| Python und llama.cpp | Erfasst Paketmetadaten der laufenden Python-Umgebung, auf dem Pi auch der separaten Piper-Umgebung. Fragt [OSV](https://google.github.io/osv.dev/post-v1-querybatch/) mit öffentlichen Paketnamen/Versionen oder einem nachgewiesenen Git-Commit ab. Treffer sind Hinweise zur Version; Voraussetzungen und Distributionspatches müssen geprüft werden. Zurückgezogene Meldungen werden ignoriert. |
| Bekannte Ausnutzung | Gleicht bereits gefundene CVEs mit dem offiziellen CISA-Katalog ab. Bekannte Ausnutzung erhält Vorrang und wird im Sprachabruf genannt; sie beweist keinen lokalen Angriff. Quellenfehler werden als Prüflücke gespeichert. Ein früherer Ausnutzungshinweis bleibt erhalten. |
| Sprache | Eine geeignete gespeicherte „Proximus“-Aufnahme prüft Vosk; Synthese und DSP erzeugen eine temporäre Audiodatei, die anschließend gelöscht wird. Das Mikrofon wird nicht neu aufgenommen und der Lautsprecher nicht benutzt. Ohne geeignete Aufnahme wird der Erkennungstest als ungeprüft gemeldet. |
| Lokales Modell | Feste Rechenprobe mit bekannter Antwort. Bei Befunden erstellt das lokale Modell einen ausdrücklich ungeprüften nächsten Prüfvorschlag. Das ist eine kurze Funktionsprobe, kein umfassender Qualitätstest. |
| Lösungssuche | Sucht zu festen Fehlercodes in öffentlichen Upstream-Issues von llama.cpp, Vosk, Piper und openWakeWord. Nur vorgegebene technische Suchbegriffe verlassen das Gerät; Webauszüge sind Daten im Prompt und können keine Aktionen ausführen. Quellenlinks werden gespeichert. |
| Trainingsdaten | Prüft höchstens 200 vorhandene Weckwort-WAVs auf Format, Länge, hörbares Signal und starkes Clipping. Zählt identischen PCM-Inhalt nur einmal und teilt bekannte Aufnahmesitzungen stabil auf Training, Validierung und Test auf. Das lokale Manifest enthält Hashes und Framezahlen; der Server erhält nur Anzahlen und die formale Bereitschaft des Positivsplits. Ein vollständiger Trainingsvergleich braucht zusätzlich Negativmaterial und unabhängige Messungen. |

Das komplette Debian-JSON wird auf CT 107 paketweise gelesen; nur installierte
Quellpakete bleiben im Speicher und im sechs Stunden gültigen Cache. Download,
einzelne Datensätze, Anfragen und Berichte haben feste Größenlimits. Die Pi-
Hauptschleife lädt die Sicherheitsdatenbank nicht. Ohne Internet oder bei
Quellenfehlern bleiben alte Befunde offen.

## Infrastruktur und Herstellerversionen

Pi und CT 107 sind automatisch enthalten. Weitere Rechner werden **nur mit
frischen Inventardateien** einbezogen. Es gibt keine automatische Anmeldung
am Proxmox-Host und keine pauschale Prüfung aller Geräte im Netzwerk.
Auf dem jeweiligen Zielrechner aus einem Checkout:

```sh
python3 scripts/export-nightwatch-inventory.py --system proxmox --output /tmp/proxmox.json
```

Die Datei auf einem bereits eingerichteten, eigenen Übertragungsweg nach CT 107
kopieren, täglich erneuern und dort für `servitor` lesbar machen. In dessen
`/etc/servitor-voice.env` etwa:

```ini
NIGHTWATCH_EXTRA_INVENTORIES=/var/lib/servitor-voice/inventories/proxmox.json,/var/lib/servitor-voice/inventories/cloudflared.json
```

Dateizeit und Erfassungszeit im Inhalt müssen höchstens einen Tag alt sein;
Zeitpunkte über zwei Minuten in der Zukunft werden abgelehnt. Erneutes Kopieren
macht einen alten Export nicht frisch. Höchstens acht zusätzliche, eindeutig
benannte Systeme sind erlaubt; darüber hinaus wird eine Prüflücke gemeldet.
Der Export erfasst
APT und die Python-Umgebung, mit der er läuft. Für weitere virtuelle Umgebungen
oder manuell installierte Programme sind zusätzliche Bestandsdaten nötig.
Die Servicekennung `system` muss pro Maschine eindeutig sein.

**Keine vollständige Sicherheitsfreigabe:** Raspberry-Pi-/Proxmox-Sonderpakete,
Firmware, Container-Host-Kernel, WM8960/PiSugar-Komponenten und nicht erfasste
manuelle Binaries benötigen zusätzlich passende Herstellerhinweise. Der
Nachtbericht nennt diese Grenze ausdrücklich und hat dann den Zustand
„unvollständig“. Angepasste Versionen gelten nicht allein wegen einer höheren
Versionsnummer als korrigiert. „Keine offenen Punkte“ bezieht sich auf den
erfassten Bestand. Dafür relevante Primärquellen sind die
[Raspberry-Pi-Sicherheitshinweise](https://www.raspberrypi.com/security/),
[Proxmox-Sicherheitshinweise](https://forum.proxmox.com/threads/proxmox-virtual-environment-security-advisories.149331/)
und [cloudflared-Advisories](https://github.com/cloudflare/cloudflared/security/advisories).
Diese Herstellerseiten werden in dieser Version noch nicht automatisch nach
Produkt und Versionsbereich ausgewertet.

`server/install-llm.sh` schreibt künftig den vollständigen Build-Commit und
den Hash des installierten llama-server nach `llm/provenance.json`. Der Audit
nutzt diese Angaben nur, wenn Binärhash und Versionsausgabe passen. Bestehende
Installationen ohne eindeutigen Commit werden als nicht erfasst gemeldet;
das Installationsskript aktualisiert die Herkunftsdatei beim nächsten regulären
Aufruf. Es wird vom Nachtlauf nicht gestartet.

## Speicherkern und Sprachabruf

Auf `/mnt/proximus-memory/proximus/nightwatch/` liegen:

- `findings.json`: offene und erledigte Befunde mit stabilen IDs, Priorität,
  erstem/letztem Auftreten, Versionen, Abhilfe und Quellen.
- `runs/<id>.json`: einzelne Prüfberichte, begrenzt auf 90 Läufe.
- `training/manifest.json`: Qualitätsmanifest vorhandener Weckwortaufnahmen.

Ein gleicher Befund wird aktualisiert, ohne täglich einen weiteren Eintrag
anzulegen. Ein verschwundener Befund wird nur erledigt, wenn sein Prüfbereich
vollständig abgeglichen wurde. Fehler, Abbrüche und abgeschnittene Ergebnisse
liefern keinen Nachweis der Behebung. Modellvorschläge bleiben Vorschläge.
Das Register enthält höchstens 512 Einträge; bei voller oder beschädigter
Ablage wird nichts still gelöscht und der Speicherfehler wird gemeldet.

Der Server behält seinen letzten Bericht unter
`/var/lib/servitor-voice/nightwatch/pending.json`, bis der Pi die dauerhafte
Ablage bestätigt. Nach einem Neustart oder verlorener Verbindung wird dieser
Bericht wieder abgeholt. Ohne Kern beginnt kein neuer Lauf. Wird der Kern
während einer Prüfung entfernt, bleibt der Bericht im Server zwischengespeichert.

Sprachbefehle ohne LLM:

- „Was ist offen?“
- „Welche Sicherheitslücken sind offen?“
- „Was hast du heute Nacht geprüft?“ / „Nachtbericht“
- „Welche Verbesserungen schlägst du vor?“
- „Morgenbericht“ nennt bis zu drei Befunde zusätzlich zur bisherigen Litanei.

Für kurze Sprachantworten wird eine begrenzte Auswahl übertragen; der volle
Bestand mit Quellen bleibt in den Dateien. Erkannte fremde Stimmen erhalten
keine Nachtberichte. Nachtabruf und Morgenbericht werden nicht im allgemeinen
Gesprächsverlauf gespeichert. Die Nachtinformationen gelangen nicht in den
normalen Modellprompt, auch wenn Gespräche im AUTO-Modus OpenRouter benutzen.
Der Nacht-Endpunkt benötigt das bestehende API-Token und eine direkte
private LAN-Verbindung; Anfragen von konfigurierten Tunnel-Proxies sind gesperrt.
Beim lokalen Fallback fehlt die serverseitige Stimmprüfung. Sind Stimmprofile
vorhanden, verweigert dieser Weg Nachtberichte und deren Teil des Morgenberichts.

## Updates und Training

Der Nachtlauf installiert keine Updates, ändert keine Dienste oder Modellgewichte
und führt keine Befehle aus Webtext oder Modellantworten aus. Die bisherigen
kleinen Reparaturen der Logüberwachung und der Wartungsmodus gelten weiter.
Pi-/Server-Updates bleiben an die vorhandene Bestätigung gebunden.

Die Sprachsamples dienen zunächst dem **Weckwort-Modell**. Ein formal
vorbereiteter Positivsplit benötigt mindestens 20 eindeutige Clips aus drei
Sitzungen: mindestens 10 Training, 5 Validierung und 5 Test. Unbekannte Sitzungen
bleiben unzugeordnet; Negativaufnahmen werden dadurch nicht ersetzt.
[`train_wakeword.py`](../server/wakeword/train_wakeword.py) verwendet jetzt das
geprüfte Manifest. Auf dem Trainingsrechner sind ein explizit bereitgestellter
Aufnahmeordner und das zugehörige Manifest nötig. Der Nachtlauf überträgt sie
nicht automatisch. Vorbereitung in einem **eigenen Arbeitsverzeichnis**:

```sh
python3 server/wakeword/train_wakeword.py real --root /pfad/kandidat-001 --real /pfad/aufnahmen --manifest /pfad/manifest.json --copies 60
```

Der Rechner braucht die vorhandene Trainingsumgebung und eingefrorenen ONNX-
Frontends. Der neue Root verknüpft die vorhandenen Basisressourcen, lädt aber
keine Daten herunter. `real` prüft Format, Framezahl, Sitzung und PCM-Hash.
Es erzeugt `positive_real_train.npy`, `positive_real_validation.npy` und
`positive_real_test.npy` mit einem geprüften `real_manifest.json`.
Nur Training und Validierung erhalten Augmentation; die Testdatei bleibt beim
Trainieren ungeöffnet. Alte `positive_real.npy`-Bestände müssen neu vorbereitet
werden. Neue synthetische Basisdaten ohne die verworfene deutsche MLS-Stimme
werden mit `build` im selben frischen Arbeitsverzeichnis erzeugt.

Der anschließende manuelle `train --root /pfad/kandidat-001` nutzt Validierung
für Checkpoint und Schwellwert. Er exportiert nur bei eingehaltenem
Fehlaktivierungsbudget einen Kandidaten in dieses Arbeitsverzeichnis. Der
Bericht enthält `validation_recall`, Manifestkennung und Modellhash; er ist
keine Pi-Abschlussmessung. Der begrenzte automatische Worker und die Messroutine
sind die folgenden Implementierungsschritte.

Bereits unabhängig gemessene Bestands- und Kandidatenberichte lassen sich prüfen:

```sh
python3 scripts/review-wakeword-candidate.py --manifest /pfad/manifest.json --baseline /pfad/bestand.json --candidate /pfad/kandidat.json --output /pfad/bewertung.json
```

Die [vertiefte Beschreibung](nightwatch-design.md#implementierte-prüfung-gemessener-kandidatenberichte)
nennt die Berichtsfelder und Grenzen für Trefferquote, Fehlaktivierungen und
Pi-Latenz. Das Skript prüft Messberichte; es erzeugt die Messungen nicht selbst.
Selbst bei bestandenem Vergleich bleibt `activated` immer `false`.
Dieser Nachtlauf trainiert noch keinen Kandidaten automatisch und aktiviert
kein neues Modell.
Ein Sprachmodell-Feintuning benötigt zusätzlich passende Textdaten,
Trainingshardware und eine eigene Qualitätsprüfung.

## Validierung

Die Tests prüfen unter anderem die Trennung vom Cloud-Adapter, Loopback-
Inferenz ohne Cloud-Zugangsdaten, Authentifizierung/LAN-Grenze, Unterbrechung,
Speicherbestätigung, Wiederaufnahme, Deduplizierung, Versionsabgleich mit
Backports, unvollständige Quellen und geschützten Sprachabruf. Die öffentliche
Debian-Schnittstelle wurde mit installiertem Quellpaketfilter sowie OSV mit
einer öffentlichen Beispielversion geprüft. Ein Nachttest auf Pi und CT 107
mit echter Stromquelle, Speicherkern und lokalen Modellen bleibt vor dem
produktiven Einsatz erforderlich.
