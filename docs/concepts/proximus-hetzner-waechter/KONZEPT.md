# Proximus: Wächter für den Hetzner-Server

Stand: 15.10.2026. Konzept, noch nicht umgesetzt. Anlass: [Vorfall 15.10.2026](VORFALL_2026-10-15.md). Erweitert Idee 3 „Homelab-Wächter“ aus der [Roadmap](../../roadmap.md) um den externen Hetzner-Server mit rund 30 Compose-Projekten (Nextcloud, Matrix, Vaultwarden, Audiobookshelf, Paperless …).

## 1. Ziel

Proximus soll den Zustand des Hetzner-Servers kennen, Auffälliges im Morgenbericht und auf Nachfrage nennen und kritische Lagen als Alarm melden – nach denselben Regeln wie der eigene [Selbsttest](../../logs.md): feste Codes, Schwellen, kein Rauschen.

Beispiele:

- Morgenbericht: „Externer Server: Platte zu 97 Prozent voll. Audiobookshelf ist von außen nicht erreichbar, seit 3 Tagen.“
- „Wie geht es dem Hetzner-Server?“ / „Serverwache“: Urteil, dann bis zu fünf Befunde mit Vorschlag.
- Lore VOLL: „Der ferne Kogitator in Falkenstein ächzt. Sein Speicher-Reliquiar ist zu 97 Teilen gefüllt.“

## 2. Grundsätze

1. **Zuerst nur lesen.** Stufe 1 ändert auf dem Hetzner-Server nichts.
2. **Keine Root- oder Docker-Rechte für Proximus.** Zugriff auf den Docker-Socket ist gleichbedeutend mit Root. CT 107 bekommt deshalb nur einen fertigen Bericht, nie eine Shell.
3. **Nie aktualisieren, nie aufräumen.** Wie beim eigenen Server (Entscheidung 08.10.2026): kein `pull`, kein `prune`, kein `compose down`. Reparaturen nur aus einer festen Liste (Stufe 3), jede mit Taste E bestätigt.
4. **Zwei Blickwinkel.** Der Hetzner-Server prüft sich selbst (Platte, Speicher, Container), CT 107 prüft von außen (öffentliche URLs). Nur der Blick von außen hätte den Vorfall vom 15.10. sicher erkannt.
5. **Keine Geheimnisse im Repo.** SSH-Schlüssel und Ziel-Host nur in `/etc/servitor-voice.env` bzw. unter `/etc/servitor-voice/`.

## 3. Aufbau

```text
Hetzner                                    CT 107                           Pi
proximus-watch.timer (root, 5 min)         hostwatch (in servitor-voice,    Morgenbericht, Alarme,
  └─ proximus-watch-collect                  alle 10 min)                    "Serverwache"
       → /var/lib/proximus-watch/status.json  ├─ ssh proximus-watch@hetzner ──→ (forced command: cat status.json)
                                              ├─ HTTP-Proben öffentlicher URLs
                                              └─ Codes → Status-Snapshot ─────→ GET /v1/status
```

### 3.1 Sammler auf dem Hetzner-Server

Ein kleines Skript `proximus-watch-collect` (POSIX-sh oder Python ohne Zusatzpakete) läuft als root per systemd-Timer alle 5 Minuten und schreibt atomar (`tmp` + `mv`) `status.json`, lesbar für den Nutzer `proximus-watch`. Es erhebt:

| Bereich | Quelle | Feld |
|---|---|---|
| Platte | `df -P` je realem Dateisystem | `disk[]: mount, used_pct, free_gb` |
| Speicher | `/proc/meminfo` | `mem_avail_pct, swap_used_pct` |
| OOM | `journalctl -k --since -24h`, Scope `docker-<id>.scope` → Containername | `oom[]: at, process, container` |
| Projekte | `docker compose ls -a --format json` | `projects[]: name, status` |
| Container | `docker ps -a --format json` + `docker inspect` | `containers[]: name, project, state, health, restart_count, started_at, image_tagged, network_mode` |
| Verwaiste Namespaces | `network_mode = container:<id>` und `started_at` des Kindes < `started_at` des Elternteils | `netns_orphans[]: child, parent` |
| Lokale Proben | `curl -fsS --max-time 10` auf konfigurierte Host-Ports | `probes[]: name, url, ok, code` |
| Erzeugt | Zeitstempel | `generated_at` |

Konfiguration in `/etc/proximus-watch.conf`: zu beobachtende Container (`WATCH=audiobookshelf nextcloud-app vaultwarden …`), bewusst gestoppte Projekte (`IGNORE=authentik nextcloud-staging calibre-web …`), lokale Proben (`PROBE_abs=http://localhost:13378/`). Gestoppte Projekte auf der Ignorierliste erzeugen keine Befunde.

### 3.2 Übertragung

Eigener Nutzer `proximus-watch` ohne Passwort, ohne Docker-Gruppe. In seiner `authorized_keys` genau ein Schlüssel von CT 107:

```text
restrict,command="cat /var/lib/proximus-watch/status.json" ssh-ed25519 AAAA… servitor@ct107
```

`restrict` verbietet PTY, Weiterleitungen und Agent. Der Schlüssel kann nur den fertigen Bericht lesen. `from="…"` ergänzen, falls CT 107 eine feste öffentliche Adresse hat. Auf CT 107 liegen Schlüssel und `known_hosts` unter `/etc/servitor-voice/` (0600, Nutzer `servitor`); Host-Key-Prüfung bleibt an (`StrictHostKeyChecking=yes`).

Der Bericht ist klein (< 64 KB); CT 107 verwirft größere Antworten und alles, was nicht dem Schema entspricht.

### 3.3 Auswertung auf CT 107

Neues Modul `src/hostwatch.py` nach dem Muster von `src/logwatch.py`: Abruf alle 10 Minuten in einem Daemon-Thread, Regeln mit Schwellen, Ergebnis als Codes mit Anzahl und Zeitpunkt im Status-Snapshot (`host_findings`, `host_last`, `host_checked_at`). Der Pi lässt nur bekannte Codes durch, wie bei `log_findings`.

| Code | Schwere | Bedingung | Vorschlag |
|---|---|---|---|
| `host_disk_full` | 0 | ein Dateisystem ≥ 95 % | `docker system df` prüfen, alte Images und Backups entfernen |
| `host_disk_low` | 1 | ≥ 90 % | wie oben, nicht dringend |
| `host_oom` | 0 | OOM-Kill in der letzten Stunde | Speicherhungrige Container begrenzen oder stoppen |
| `host_swap` | 1 | Swap ≥ 90 % über 30 min | dito |
| `host_netns_orphan` | 0 | verwaister Namespace bei beobachtetem Container | Kind-Container neu starten |
| `host_probe_failed` | 0 | lokale oder externe Probe zweimal in Folge fehlgeschlagen | Logs des Dienstes und Proxy prüfen |
| `host_container_down` | 1 | beobachteter Container nicht `running` | `docker compose up -d` im Projekt |
| `host_unhealthy` | 1 | `health = unhealthy` | Logs prüfen |
| `host_restarting` | 1 | `restarting` oder `restart_count` +3 in 24 h | Logs prüfen, ggf. stoppen |
| `host_image_untagged` | 2 | laufender Container ohne Image-Tag | Tag setzen, sonst scheitert `compose up` |
| `host_watch_stale` | 1 | `generated_at` älter als 15 min | Timer auf dem Server prüfen |
| `host_unreachable` | 1 | SSH dreimal in Folge gescheitert | Server, Netz, Schlüssel prüfen |

**Externe Proben** laufen direkt von CT 107 gegen öffentliche URLs aus `SERVITOR_HOSTWATCH_PROBES` (z. B. Audiobookshelf, Nextcloud, Vaultwarden hinter ihrem Reverse-Proxy), nur `HEAD`/`GET` ohne Zugangsdaten. Lazytainer: Eine Antwort der Statusseite („startet“) gilt nicht als Ausfall; der genaue Statuscode wird bei der Umsetzung gemessen.

### 3.4 Ausgabe

- **Morgenbericht:** nach den eigenen Befunden höchstens zwei aktuelle Hetzner-Befunde, die schwersten zuerst, ohne Vorschlag. Nichts, wenn alles ruhig ist.
- **Nachfrage:** neues Intent `hostwatch` („Wie geht es dem Hetzner-Server?“, „Serverwache“, „Externer Server“, „Wie geht es Nextcloud?“, auch in Vosk-small-Schreibweisen wie „hetzner server“, „hetz na“) – Urteil, bis zu fünf Befunde mit Vorschlag, aktuelle zuerst.
- **Alarm:** Schwere 0 einmalig über die vorhandenen Alarme (stummschaltbar), Erholung wird angesagt.
- Sprachgebrauch: Der Hetzner-Server ist nicht „mein Server“ (das ist CT 107), sondern „der externe Server“, Lore VOLL „der ferne Kogitator“.

### 3.5 Reparatur (Stufe 3, optional)

Nur wenn Stufe 1 und 2 stabil laufen und der Bediener es ausdrücklich freigibt. Zweiter Schlüssel mit eigenem forced command auf ein Skript `proximus-watch-repair`, das genau eine Aktion kennt:

- `restart-orphan <container>`: nur wenn `<container>` in `REPAIR_ALLOW` steht **und** der Sammler den verwaisten Namespace in diesem Moment selbst erneut feststellt; dann `docker restart <container>`.

Auslösung nur im Wartungsmodus mit Taste E, höchstens einmal pro 30 Minuten und dreimal am Tag, Ergebnis wird nach 60 s mit einer neuen Probe geprüft und angesagt. Kein Update, kein Pull, kein Neuerstellen.

## 4. Rechte und Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| Schlüssel von CT 107 wird gestohlen | Er kann nur `status.json` lesen; Reparaturschlüssel getrennt und nur Allowlist |
| Bericht verrät interne Struktur | Enthält nur Container-Namen und Zahlen, keine Umgebungsvariablen, Logs oder Pfade zu Geheimnissen |
| SSH-Brute-Force auf dem Hetzner-Server | Nutzer ohne Passwort, nur Schlüssel; `from=` wenn möglich |
| Fehlalarme durch Lazytainer-Schlafphasen | Lazytainer-Statusseite als „schläft“ werten, Proben zweimal in Folge |
| Sammler verbraucht Ressourcen | `docker inspect` gebündelt, alle 5 min, `Nice=10`, `IOSchedulingClass=idle` |

## 5. Offene Entscheidungen des Bedieners

1. Stufe 3 (Reparatur verwaister Namespaces) überhaupt erlauben?
2. Welche Container gelten als beobachtet, welche Projekte sind bewusst gestoppt?
3. Welche öffentlichen URLs darf CT 107 prüfen?
4. Soll der vorhandene `abs-watchdog.sh` bleiben oder durch Stufe 3 ersetzt werden?

Umsetzung: [Claude-Auftrag](CLAUDE_AUFTRAG.md).
