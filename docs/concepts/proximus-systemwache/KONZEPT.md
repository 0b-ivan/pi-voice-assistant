# Proximus: Systemwache – dynamische Überwachung fremder Hosts und Uptime Kuma

Stand: 15.10.2026, Fassung 2. Konzept zur Abstimmung, noch nicht umgesetzt. Anlass: [Vorfall 15.10.2026](VORFALL_2026-10-15.md). Ersetzt die erste Fassung (fest verdrahteter Sammler auf dem Hetzner-Server); deren Bericht-Modus bleibt als sicherste Zugriffsstufe erhalten (Abschnitt 4).

## 1. Ziel

Nichts fest verdrahten. Der Bediener gibt Proximus zur Laufzeit ein **Wachziel**:

- einen Host mit Adresse, Port, Nutzer und SSH-Schlüssel, oder
- eine Uptime-Kuma-Instanz (z. B. `https://uptime.obivan.org`) mit API-Schlüssel.

Proximus **erkundet** selbst, was dort läuft (Betriebssystem, systemd, Docker, Compose-Projekte, offene Ports, Monitore …), schlägt passende Wachen vor, **überwacht** sie und **meldet Veränderungen**: was neu ist, was verschwunden ist, was ausgefallen oder sich erholt hat, was sich schleichend verschlechtert.

Beispiele:

- „Was hat sich auf dem Hetzner-Server verändert?“ → „Seit gestern: Audiobookshelf neu gestartet, Image unverändert. Neuer Container `ovumcy-web`. Platte von 95 auf 97 Prozent.“
- Morgenbericht: „Uptime Kuma: Audiobookshelf war heute Nacht 3 Stunden nicht erreichbar, seit 21 Uhr wieder da.“
- Alarm, mit Ursache aus beiden Quellen: „Audiobookshelf ausgefallen. Ursache auf dem Host: Container hängt an einem verwaisten Netzwerk von Lazytainer.“

## 2. Grundsätze

1. **Erkunden heißt: feste Sonden ausführen, keine frei formulierten Befehle.** Proximus kennt einen Katalog lesender Sonden (Abschnitt 5). Was er findet, entscheidet, welche Sonden und Wachen er aktiviert. Ein Sprachmodell erzeugt nie Shell-Befehle.
2. **Alles vom Zielsystem ist Fremddaten.** Containernamen, Logzeilen, Monitornamen können Text enthalten, der wie eine Anweisung aussieht. Sie gelangen nur strukturiert und zitiert in einen Prompt und werden nie in Befehle eingesetzt, ohne gegen ein enges Muster geprüft zu sein.
3. **Zuerst nur lesen.** Aktionen (Abschnitt 10) kommen frühestens in einer späteren Stufe, aus einer festen Liste, jeweils mit Taste E bestätigt. Nie Updates, `pull`, `prune` oder `compose down` – wie beim eigenen Server (Entscheidung 08.10.2026).
4. **Geheimnisse bleiben auf CT 107.** Private Schlüssel und API-Schlüssel verlassen CT 107 nie: nicht zum Pi, nicht ins Gedächtnis, nicht zu OpenRouter, nicht ins Repository.
5. **Infrastrukturdaten standardmäßig nicht in die Cloud.** Ansagen entstehen regelbasiert wie beim Selbsttest; wo ein Sprachmodell zusammenfasst, nur das lokale Qwen auf CT 107, außer der Bediener erlaubt OpenRouter ausdrücklich.
6. **Kein Rauschen.** Schwellen, Hysterese und Zusammenfassen wie im [Selbsttest](../../logs.md): Ein einzelner Aussetzer ist keine Meldung.

## 3. Wachziele anlegen

Sprache taugt nicht für Schlüssel. Wachziele werden im **Web-Client** angelegt (https://proximus.obivan.org, nur nach Anmeldung des Bedieners, neue Seite „Systemwache“), ersatzweise per Kommandozeile auf CT 107 (`servitor-watch add …`).

### 3.1 Host per SSH

1. Bediener gibt Name, Adresse, Port und Nutzer ein.
2. **Schlüssel:** Proximus erzeugt auf CT 107 ein eigenes Ed25519-Schlüsselpaar je Wachziel und zeigt den öffentlichen Schlüssel samt fertiger Zeile für `authorized_keys` an. Alternativ lädt der Bediener einen vorhandenen privaten Schlüssel hoch (wird sofort mit 0600 abgelegt und nie wieder angezeigt). Empfohlen ist das selbst erzeugte Paar: Der private Schlüssel existiert dann nur auf CT 107.
3. **Host-Schlüssel:** Beim ersten Kontakt zeigt Proximus den Fingerabdruck an; erst nach Bestätigung wird er gespeichert. Danach `StrictHostKeyChecking=yes`. Ändert er sich später, stoppt die Wache und meldet das, statt still weiterzumachen.
4. **Erkunden** startet sofort (Abschnitt 5); das Ergebnis erscheint als Inventar mit vorgeschlagenen Wachen zum An- und Abwählen.

### 3.2 Uptime Kuma

1. Bediener gibt die URL ein (`https://uptime.obivan.org`) und einen **API-Schlüssel** (Uptime Kuma: Einstellungen → API-Schlüssel). Damit liest Proximus den Prometheus-Endpunkt `/metrics` mit Basic-Auth (leerer Nutzer, Schlüssel als Passwort). Das ist offiziell, nur lesend und liefert je Monitor Status, Antwortzeit, Typ, Ziel und Restlaufzeit des Zertifikats.
2. Optional eine öffentliche **Statusseite** (`/api/status-page/<slug>` und `/api/status-page/heartbeat/<slug>`), ganz ohne Schlüssel, aber nur mit den Monitoren dieser Seite.
3. Optional ein **Webhook** aus Uptime Kuma (Benachrichtigung „Webhook“) an CT 107, damit Ausfälle sofort ankommen statt beim nächsten Abruf. Das ist zugleich der Rückkanal aus Roadmap-Idee 4 (`POST /v1/announce`), abgesichert mit eigenem Token.
4. Proximus ordnet Monitore beim Erkunden Hosts und Containern zu (gleicher Hostname, Port, Containername). Die Zuordnung ist ein Vorschlag, den der Bediener bestätigt oder korrigiert.

Der Link `…/dashboard/17` zeigt auf Monitor 17. Ein Wachziel ist aber immer die ganze Instanz; einzelne Monitore wählt der Bediener im Inventar an oder ab.

Ein Zugang mit Benutzername und Passwort über die interne Socket.IO-Schnittstelle (z. B. Bibliothek `uptime-kuma-api`) könnte auch Monitore anlegen. Sie ist inoffiziell und ändert sich zwischen Versionen (2.0). Deshalb nur als spätere Stufe (Abschnitt 10).

## 4. Zugriffsstufen auf einem Host

Docker lesen erfordert Zugriff auf den Docker-Socket, und der ist gleichbedeutend mit Root. Proximus arbeitet mit jeder Stufe und meldet beim Erkunden, was ihm fehlt („Docker vorhanden, aber kein Zugriff. Möglichkeiten: …“).

| Stufe | Einrichtung auf dem Host | Sieht | Risiko bei gestohlenem Schlüssel |
|---|---|---|---|
| **A Basis** | normaler Nutzer ohne Gruppen | OS, Platte, Speicher, Last, offene Ports (ohne Prozess), systemd-Units, eigenes Journal | gering: Lesezugriff eines normalen Nutzers |
| **B Sonden-Wrapper** (empfohlen) | zusätzlich `proximus-probe` (kleines Skript aus dem Repo) und eine sudoers-Zeile nur dafür | zusätzlich Docker (Container, Health, Neustarts, Images, Netzmodus), Kernel-Journal (OOM), Compose-Projekte; Umgebungsvariablen werden im Wrapper herausgefiltert | gering: nur die festen Unterbefehle des Wrappers |
| **C Socket-Proxy** | `docker-socket-proxy` nur lesend (`CONTAINERS=1 INFO=1 IMAGES=1`, alles andere 0) auf `127.0.0.1`, Proximus geht per SSH-Tunnel | Docker-API lesend, ohne `exec` | gering bis mittel: `inspect` enthält Umgebungsvariablen, Proximus muss sie verwerfen |
| **D Voll** | Nutzer in Gruppe `docker` | alles | **hoch: Root auf dem Host** |
| **R Bericht** (Fassung 1) | root-Timer schreibt `status.json`, Schlüssel mit `restrict,command="cat …"` | nur der feste Bericht | minimal, dafür kein Erkunden |

Stufe B ist der Kompromiss aus „dynamisch“ und „sicher“: Der Wrapper bietet viele lesende Unterbefehle (`docker-ps`, `docker-inspect <name>`, `compose-ls`, `kernel-oom`, `listening`, `failed-units`, `apt-pending`), Proximus entscheidet selbst, welche er nutzt, kann aber nichts darüber hinaus.

## 5. Erkunden

Ein Erkundungslauf ist eine Folge fester Sonden in einer einzigen SSH-Sitzung (ControlMaster). Jede Sonde hat eine Bedingung, einen Befehl ohne eingesetzte Fremddaten oder mit geprüften Parametern, einen Zeitrahmen und einen Parser.

| Sonde | Bedingung | liefert |
|---|---|---|
| `os` | immer | `/etc/os-release`, Kernel, Uptime, CPU, RAM |
| `disk` | immer | `df -P` je realem Dateisystem |
| `mem` | immer | `/proc/meminfo`, Swap |
| `systemd` | `systemctl` vorhanden | fehlgeschlagene Units, laufende Dienste |
| `ports` | `ss` vorhanden | lauschende Ports (Prozess nur mit Rechten) |
| `docker` | Socket oder Wrapper erreichbar | Container mit Projekt, Image, Tag, Zustand, Health, Neustarts, Start, Netzmodus, Ports |
| `compose` | Docker gefunden | Projekte mit Status und Pfad der Compose-Datei |
| `oom` | Kernel-Journal lesbar | OOM-Kills 24 h, Containerzuordnung über `docker-<id>.scope` |
| `apt` | Debian/Ubuntu | wartende Updates, Sicherheitsupdates, Neustart nötig (nur melden, nie ausführen) |
| `podman`, `k3s`, `zfs`, `smart` | wenn erkannt | spätere Erweiterungen, gleiches Muster |

Aus dem Inventar leitet Proximus **Wachen** ab, zum Beispiel:

- Docker gefunden → Containerwache (Zustand, Health, Neustartschleifen, verwaiste Netze, fehlende Image-Tags).
- Compose-Projekt mit `restart: unless-stopped`, aber `exited` → Vorschlag „beobachten“ oder „bewusst gestoppt“.
- Container mit veröffentlichtem Port → Portprobe auf dem Host; passt ein Uptime-Kuma-Monitor dazu, wird er verknüpft.
- `apt` mit Sicherheitsupdates → Hinweis im Morgenbericht.

Neu erkundet wird einmal täglich, auf Knopfdruck und auf Zuruf („Erkunde den Hetzner-Server neu“). Ändert sich dabei das Inventar (neuer Container, neues Projekt, neuer Port), ist das selbst eine Veränderung (Abschnitt 6) und neue Dinge werden als „noch nicht eingeordnet“ vorgeschlagen, statt sie stillschweigend zu überwachen oder zu ignorieren.

## 6. Veränderungen erkennen

Jeder Abruf erzeugt einen normalisierten **Zustand**: Entitäten (Host, Dateisystem, Container, Projekt, Unit, Port, Monitor) mit Attributen. Proximus vergleicht ihn mit dem vorigen und schreibt **Ereignisse** in ein Ereignisjournal:

| Art | Beispiel | Schwere |
|---|---|---|
| neu / verschwunden | Container `ovumcy-web` neu, Projekt `factorio` entfernt | Hinweis |
| Zustand | `running` → `exited`, `healthy` → `unhealthy`, Monitor DOWN/UP | Störung bzw. Erholung |
| Neustart | `started_at` neu, `restart_count` +3 in 24 h | Hinweis bzw. Störung |
| Image | anderes Image unter gleichem Namen (Update erfolgt), Tag verloren | Hinweis |
| Schwelle | Platte ≥ 90 / 95 %, Swap ≥ 90 %, Zertifikat < 14 Tage, Antwortzeit dauerhaft über dem Dreifachen des 7-Tage-Medians | Warnung bzw. kritisch |
| Muster | verwaistes Netz (Kind älter als Elternteil), OOM-Kill | kritisch |
| Zugang | Host nicht erreichbar, Host-Schlüssel geändert, API-Schlüssel ungültig | Störung |

Regeln gegen Rauschen: Zustandswechsel erst nach zwei Abrufen in Folge, Flattern (mehr als drei Wechsel pro Stunde) wird zu einem Ereignis „instabil“ zusammengefasst, Erholung wird gemeldet, gleichartige Ereignisse werden gebündelt („4 Container neu gestartet, alle im Projekt nextcloud“).

**Korrelation:** Ein Ereignis aus Uptime Kuma und eines vom Host, die zeitlich und über die Zuordnung zusammenpassen, werden zu einer Meldung mit Ursache verbunden. Genau das hätte am 15.10. die Antwort „von außen tot, innen healthy, Netz verwaist“ geliefert.

## 7. Ausgabe

- **Morgenbericht:** nach den eigenen Befunden höchstens zwei aktuelle Ereignisse der Systemwache, schwerste zuerst; Erholungen der Nacht in einem Satz.
- **Nachfrage** (neue Intents, auch in Vosk-small-Schreibweisen): „Wie geht es dem Hetzner-Server?“, „Was hat sich verändert?“, „Was ist bei Uptime Kuma los?“, „Wie geht es Nextcloud?“, „Erkunde den Hetzner-Server neu“.
- **Alarm:** kritische Ereignisse einmal über die vorhandenen Alarme, stummschaltbar, mit Erholungsmeldung.
- **Web-Client:** Seite „Systemwache“ mit Wachzielen, Inventar, offenen Befunden und Ereignisjournal.
- **Optional schriftlich:** Ereignisse zusätzlich in einen Matrix-Raum (der Hetzner-Server betreibt Matrix) oder per ntfy. Erst nach Entscheidung des Bedieners.
- Sprachgebrauch: CT 107 bleibt „mein Server“; andere Hosts heißen mit ihrem Wachziel-Namen („der Hetzner-Server“), Lore VOLL „der ferne Kogitator Hetzner“.

## 8. Ablage auf CT 107

```text
/var/lib/servitor-voice/watch/
  targets.json            Wachziele ohne Geheimnisse (Name, Typ, Adresse, Nutzer, Stufe, gewählte Wachen)
  secrets/<ziel>/         id_ed25519, known_hosts, kuma.key   (0600, Nutzer servitor)
  inventory/<ziel>.json   letztes Erkundungsergebnis
  state/<ziel>.json       letzter Zustand für den Vergleich
  events.jsonl            Ereignisjournal, 90 Tage, höchstens 20 MB
```

Nichts davon liegt auf dem Pi oder dem Gedächtnis-Stick; der Pi bekommt nur Codes, Zahlen und fertige Sätze über den Status-Snapshot.

## 9. Abläufe und Last

- Hosts: Zustandsabruf alle 5 min (eine SSH-Sitzung, gebündelte Sonden, `nice` auf dem Ziel), Erkunden täglich.
- Uptime Kuma: `/metrics` jede Minute (klein), Webhook sofort.
- Zeitlimits je Sonde, Gesamtlimit je Lauf; ein hängender Host blockiert keine anderen Wachziele.
- Ausfall von CT 107 selbst ist nicht Sache der Systemwache, das meldet der Pi wie bisher.

## 10. Stufen

| Stufe | Inhalt | Voraussetzung |
|---|---|---|
| 1 | Wachziele (SSH, Uptime Kuma lesend), Schlüsselerzeugung, Host-Key-Bestätigung, Erkunden, Inventar im Web-Client | Entscheidungen 1–3 |
| 2 | Zustandsvergleich, Ereignisjournal, Morgenbericht, Nachfrage, Alarme, Korrelation | Stufe 1 |
| 3 | Uptime-Kuma-Webhook (`/v1/announce`), optional Matrix/ntfy | Entscheidung 4 |
| 4 | Vorschläge: Uptime-Kuma-Monitor für neu entdeckten Dienst anlegen (Socket.IO-Zugang), nach Bestätigung | Entscheidung 5 |
| 5 | Aktionen aus fester Liste, z. B. „verwaisten Container neu starten“, über eigenen Wrapper-Unterbefehl, Taste E, Ratenlimit | Entscheidung 5, Stufe B auf dem Host |

## 11. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Schlüssel auf CT 107 wird gestohlen | ein Schlüssel je Wachziel, Stufe B statt D, `from=` in `authorized_keys`, wenn CT 107 eine feste Adresse hat; Widerruf im Web-Client löscht Schlüssel und zeigt die zu entfernende Zeile |
| Prompt-Injection über Containernamen oder Logzeilen | Grundsatz 1 und 2; Namen gegen `^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$` geprüft; Logzeilen werden nie an ein Modell gegeben |
| Geheimnisse in `docker inspect` | Wrapper filtert `Env`; ohne Wrapper verwirft Proximus `Config.Env` vor jeder Ablage |
| Web-Client als Einfallstor | Seite nur für angemeldeten Bediener, nicht für Gäste; Anlegen und Löschen von Wachzielen protokolliert |
| Fehlalarme durch Lazytainer | dessen Statusseite als „schläft“ erkennen; Schwellen und zwei Abrufe in Folge |
| Last auf dem Zielsystem | gebündelte Sonden, Intervalle, `nice`/`ionice` |

## 12. Entscheidungen des Bedieners

1. **Zugriffsstufe auf dem Hetzner-Server:** B (Wrapper, empfohlen), C (Socket-Proxy) oder D (Gruppe `docker`, einfach, aber Root-gleich)?
2. **Uptime Kuma:** API-Schlüssel für `/metrics` anlegen? Gibt es eine öffentliche Statusseite, die zusätzlich genutzt werden soll?
3. **Sprachmodell für Infrastrukturdaten:** nur lokal (Vorschlag) oder OpenRouter erlaubt?
4. **Schriftliche Meldungen:** zusätzlich Matrix-Raum oder ntfy, oder nur Sprache und Web-Client?
5. **Schreibzugriff:** Sollen spätere Stufen Monitore anlegen oder Container neu starten dürfen, oder bleibt Proximus dauerhaft nur lesend?
6. **Weitere Wachziele:** Proxmox-Host und andere CTs gleich mitdenken (Roadmap-Idee 3), oder zuerst nur Hetzner und Uptime Kuma?

Nach diesen Entscheidungen wird der [Umsetzungsauftrag](CLAUDE_AUFTRAG.md) neu gefasst.
