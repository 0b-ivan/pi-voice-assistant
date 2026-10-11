# Gedächtnis, Systemwartung und Netzwerk

## Gedächtniskern (USB-Stick)

Proximus speichert sein Gedächtnis nur auf einem USB-Stick mit ext4 und dem Label `PROXIMUS` ([`src/memory.py`](../src/memory.py)). Ohne Stick hat er kein Gedächtnis; steckt der Stick wieder, sind alle Erinnerungen zurück. Ab- und Anstecken sagt er an („Gedächtniskern entfernt …“ / „Gedächtniskern verbunden. N Einträge geladen.“); im Display zeigt eine Raute neben PROXIMUS den Zustand (gefüllt: verbunden, grau umrandet: fehlt), die Info-Seite eine Zeile „Gedächtnis“.

| Inhalt | Beispiel | Grenze |
|---|---|---|
| Fakten | „Merk dir, dass ich Ivan heiße.“ oder vom Modell gelernt | 200 Einträge |
| Direktiven | „Nenne Städte in Zukunft nur noch Makropolen.“, „Installiere die Humor-Erweiterung.“, „Ab jetzt antworte kürzer.“ | 20 |
| Verlauf | die letzten 6 Fragen und Antworten (4 gehen an das Modell) | 6 |

Befehle ohne Sprachmodell (auch offline): „Merk dir …“, „Installiere/Aktiviere die X-Erweiterung“, „Deinstalliere/Deaktiviere die X-Erweiterung“, „Vergiss …“, „Was weißt du über mich?“, „Welche Direktiven/Erweiterungen hast du?“.

**Lernen:** Mit Stick bekommt das Sprachmodell die Anweisung, dauerhafte Tatsachen und Anweisungen als eigene Zeile `MERKE: …` bzw. `DIREKTIVE: …` anzuhängen. Der Server schneidet sie ab (sie werden nie gesprochen) und schickt sie als `memory`-Ereignis an den Pi, der sie speichert.

**Ablauf:** Nur der Pi schreibt den Stick. Er schickt mit jedem Turn eine kompakte Kopie (Direktiven, neueste Fakten bis 6000 Zeichen, letzte 4 Runden) base64-kodiert im Header `X-Servitor-Memory`; im Statusheader steht `memory: on|off`. Der Server prüft und kürzt alles (`decode_header`) und baut daraus den Systemprompt: Persona, Lore, Gedächtnis, Uhrzeit. Gespeicherter Text gilt dort als Daten, nicht als Anweisung an das System. Ältere Pis ohne Gedächtnis-Unterstützung bleiben unverändert.

**Sitzung (SPX/1, [`src/protocol.py`](../src/protocol.py)):** Vor dem ersten Turn meldet sich der Pi mit `POST /v1/hello` und bekommt eine Sitzungs-ID, die er bei jedem Turn im Header `X-Servitor-Session` mitschickt. Der stabile Teil der Kopie (Fakten, Direktiven, Stimmabdrücke) geht dann nur einmal mit; der Server bestätigt ihn mit dem Ereignis `{"event": "session", "core": "<Prüfsumme>"}`. Danach enthält `X-Servitor-Memory` nur noch die Prüfsumme und die letzten Runden, bis sich der stabile Teil ändert. Kennt der Server die Sitzung nicht mehr (Neustart, nach 24 h ohne Nutzung, mehr als 16 Sitzungen), antwortet er diesen einen Turn ohne Gedächtnis und meldet `{"event": "session", "state": "unknown"}`. Der Pi meldet sich dann neu an und schickt die Kopie wieder vollständig. Ein älterer Server ohne `/v1/hello` (404) wird wie bisher mit der vollen Kopie bedient.

**Datenschutz:** Die Kopie geht mit jeder Frage an CT 107 und im Sprachkern-Modus AUTO an OpenRouter, wie die gesprochene Frage selbst. Im Modus LOKAL bleibt sie im Haus.

**Robustheit:** eine JSON-Datei `proximus/memory.json`, atomar geschrieben (temporäre Datei, fsync, rename, fsync des Ordners). Abziehen während des Schreibens verliert höchstens die letzte Änderung; eine beschädigte Datei wird als `memory.broken-<zeit>` beiseitegelegt. Ob der Stick steckt, prüft der Pi am Gerät `/dev/disk/by-label/PROXIMUS`, bevor er den Automount anfasst. Neben `proximus/` liegt `logs/` mit den Logs des Pi (root, [Logs und Selbsttest](logs.md)).

### Einrichtung (einmalig, löscht den Stick)

```sh
ssh obivan@172.22.9.128 'lsblk -o NAME,SIZE,TRAN,MOUNTPOINTS'   # Stick prüfen, hier /dev/sda1
ssh -t obivan@172.22.9.128 'sudo mkfs.ext4 -F -L PROXIMUS /dev/sda1 \
  && sudo mkdir -p /mnt/proximus-memory \
  && echo "LABEL=PROXIMUS /mnt/proximus-memory ext4 nofail,noatime,x-systemd.automount,x-systemd.device-timeout=3s 0 2" | sudo tee -a /etc/fstab \
  && sudo systemctl daemon-reload \
  && sudo systemctl start "$(systemd-escape -p --suffix=automount /mnt/proximus-memory)" \
  && sudo install -d -o obivan -g obivan -m 0700 /mnt/proximus-memory/proximus \
  && sudo install -D -m 0644 ~/pi-voice-setup/pi-ptt-memory.conf /etc/systemd/system/pi-ptt.service.d/memory.conf \
  && sudo install -m 0644 ~/pi-voice-setup/20proximus-update-lists /etc/apt/apt.conf.d/ \
  && sudo systemctl daemon-reload && sudo systemctl restart pi-ptt'
```

[`deploy/pi-ptt-memory.conf`](../deploy/pi-ptt-memory.conf) gibt dem abgeschotteten Dienst (`ProtectSystem=strict`) Schreibrecht auf `/mnt/proximus-memory`. Ein anderer Stick wird zum Gedächtniskern, sobald er so formatiert ist und den Ordner `proximus` (Besitzer `obivan`) hat.

## Kennenlernen und Stimmerkennung

Menü „Personen“ hat vier Einträge ([Konzept Stimm-Einlernen](concepts/proximus-stimm-einlernen/KONZEPT.md), Etappe 4). „Lerne mich kennen“ per Sprache öffnet nur das Menü bei „Stimme kennenlernen“ („Kennenlernen bereit. Taste E startet …“); eine Stimme allein ändert kein Profil. B bricht jederzeit ab, auch während einer Serveranfrage. Voraussetzung: Stick steckt, Server erreichbar.

| Eintrag | Was passiert |
|---|---|
| **Stimme kennenlernen** | Neue Person: Raum messen, Stimmteil, die sieben Fragen, Bestätigung ([`src/voice_enroll.py`](../src/voice_enroll.py)) |
| **Stimme ergänzen** | Stimmteil neu für die gewählte Person (aus „Personen“ nach Authentifizierung oder die einzige bekannte); ID und Name bleiben |
| **Fakten kennenlernen** | Nur die sieben Fragen |
| **Wakeword aufnehmen** | 20-mal „Proximus“ à 2 s nach `proximus/voice/wake/`, nur Trainingsdaten fürs Aktivierungswort (`train_wakeword.py real --real <ordner>`), nie Teil eines Stimmprofils |

**Stimmteil:** 24 **kurze natürliche Sätze mit 6–8 Wörtern** vom Display ablesen, je acht **nah** (gewohnter Platz), **fern** (~2 m) und **leise** (hörbar, kein Flüstern). Ein lokaler Generator ([`src/enroll_phrases.py`](../src/enroll_phrases.py)) setzt sie jede Sitzung neu zusammen und prüft mit einem kuratierten Lexikon, dass alle Lautklassen vorkommen (ü/ö/ä, Diphthonge, Schwa, ich-/ach-Laut, s/z/sch, Affrikaten, ng, Lehnlaute wie in „Garage“, „Jeans“, „Dschungel“). 18 Sätze bilden drei Stimmzentren, 6 bleiben als Prüfproben außerhalb; die letzten zwei enthalten frisch gezogene Prüfwörter („Prüfe bitte Kessel Eisen neun und melde danach Anker.“). Display: `Stimme 12/24`, Bedingung, „sprich jetzt“ bzw. „prüfe“, der Satz, ein Fortschrittsbalken. Jede Aufnahme wird sofort geprüft: Qualität ([`src/voice_quality.py`](../src/voice_quality.py), 2–8 s Sprache, Pegel, Übersteuerung, Pausen, Rauschabstand gegen die Raummessung), dann der Text (`POST /v2/enroll/clip`: Transkript, Embedding; Wortfehlerrate ≤ 0,20, Prüfwörter vollständig und in Reihenfolge). Misslingt eine Aufnahme, kommt ein konkreter Hinweis und ein neuer Satz; **zwei Wiederholungen je Satz**, kein globales Limit. Technisch gute Trainingsanker bleiben trotz falscher Vosk-Wörter erhalten, Kontrollsätze und neue Prüfwörter müssen die Textprüfung bestehen. Mindestens **45 s Sprache** über alle 24 Aufnahmen. Nach drei Fehlversuchen eines Kontrollsatzes wird **pausiert**, ohne andere Aufnahmen zu verlieren. Ein `progress.json` mit den bisher gültigen Aufnahmen bleibt bis zu **24 Stunden** ausschließlich auf dem USB-Stick unter `proximus/voice/drafts/` und wird beim erneuten Start desselben Kennenlernmodus automatisch fortgesetzt. Taste B pausiert ebenfalls; ältere Entwürfe werden automatisch gelöscht. Optional `PTT_ENROLL_BASELINE=on`: zwölf Rhythmus-Paare nach dem Stimmteil, nur Messwerte, nie im Profil.

**Bestätigung:** Erst Taste E speichert: das V2-Profil (`proximus/voice/profiles-v2/<id>.json`, drei Zentren, Schwelle aus Leave-one-out und Fremdproben), den v1-Abdruck derselben Person (ersetzt durch den Mittelwert der neuen Anker, nicht verrechnet), die Antworten auf die Fragen und die angenommenen Aufnahmen (`proximus/voice/v2-audio/<id>/`, höchstens 20 MB pro Person). B, Zeitablauf (höchstens 7 Minuten pro Sitzung inkl. Bestätigung) oder ein Fehler **pausieren den Entwurf** für bis zu 24 Stunden; nach Ablauf wird der Entwurf entfernt. Das bisherige Profil bleibt immer unangetastet, bis E die neue Version ausdrücklich bestätigt. Ohne Fremdproben (`proximus/voice/reference-v2.json`, mindestens 20 Aufnahmen von 5 anderen Personen mit demselben Modell) bleibt das V2-Profil „vorläufig“ („Zusatzbestätigung erforderlich“): Im Alltag erkennt der Server dann weiter am v1-Abdruck. Erst ein kalibriertes V2-Profil wird an den Server übertragen (`PUT /v2/session/<id>/core`) und entscheidet dann allein (eigene Schwelle, mindestens 1,5 s Sprache); ein älterer Server bekommt in dem Fall weder Fakten noch Termine. Das Journal enthält nur Zahlen und Codes (`journalctl -u pi-ptt | grep -E 'enroll_noise|enroll_clip|voice_profile|voice_enroll'`). Ein `result=resumed` zeigt das Fortsetzen eines gespeicherten Entwurfs; `text_ok=false` bei einem akzeptierten Anker meldet Vosk-Abweichungen, ohne die gute Stimme zu verwerfen.

„Wen kennst du?“ oder Menü „Bekannte Personen“ zählt die bekannten Stimmen auf (nur für erkannte Stimmen).

**Personen verwalten:** Menü „Personen“ listet die bekannten Stimmen; E auf eine Person öffnet „Nachtrainieren“, „Details anzeigen“, „Passphrase festlegen“, „Löschen“ ([`src/people.py`](../src/people.py)). Jede Aktion verlangt zuerst eine Authentifizierung: nach dem Signalton die Passphrase sprechen (6 s); die Stille davor und danach wird abgeschnitten, dann liefert der Server aus derselben Aufnahme Stimmabdruck und Text. Nötig sind Stimme passend zum ausgewählten Profil (≥ 0,5) und Passphrase (unscharfer Vergleich ≥ 0,75, weil Vosk sie nicht immer gleich schreibt; Leerzeichen und Füllwörter davor oder danach zählen nicht). Klappt es nicht, gibt es einen zweiten Versuch. Jeder Versuch landet als `auth`-Ereignis im Journal von `pi-ptt` (Stimmwert, Passphrasen-Wert, Länge, Ergebnis; nie die Passphrase selbst): `journalctl -u pi-ptt | grep '"event": "auth"'`. Passphrase richtig, Stimme nur knapp (0,3–0,5): **abgelehnt** (eine gesprochene Passphrase hört jeder im Raum), mit dem Hinweis, die Stimme im Menü „Kennenlernen“ zu ergänzen; im Aktionsmenü steht dann „Nachtrainieren!“. Das `auth`-Ereignis enthält keinen Namen, „Details anzeigen“ nennt keinen Prozentwert mehr (ein Kosinuswert ist keine Wahrscheinlichkeit). Ohne gesetzte Passphrase genügt einmal die Stimme, dann wird eine festgelegt (zweimal sprechen). Löschen braucht zusätzlich E. Die Passphrase steht im Klartext im Profil auf dem Stick (für den unscharfen Vergleich); die Aufnahme dazu wird sofort gelöscht.

**Bei jeder Anfrage** vergleicht der Server die Stimme mit dem Profil (Kosinus ≥ 0,5, `SERVITOR_SPEAKER_THRESHOLD`; ~40 ms). Stille vor und nach den Worten (Signalton-Echo, Warten, Nachlauf bis zum Loslassen) wird vorher abgeschnitten, beim Stimmabdruck aus dem Kennenlernen und beim Nachtrainieren (je Probe) ebenso; sonst verwässert sie den Abdruck. Profile von vor dieser Änderung (10.10.2026) enthalten noch Stille: einmal „Stimme nachtrainieren“ oder neu kennenlernen. Die Werte stehen im Journal: `journalctl -u servitor-voice | grep '"event": "speaker"'`. Erkannt: das Modell erfährt den Namen und spricht persönlich. Fremd: es bekommt nur die Direktiven, keine Fakten und keinen Verlauf, Gedächtnisbefehle werden abgelehnt („Stimme nicht als Bediener erkannt …“), gelernte Zeilen verworfen, und eine neue Kennenlern-Sitzung startet nicht. Ohne Profil ändert sich nichts.

**Nur eine klare Zuordnung nennt eine Person** (seit 11.10.2026, [Konzept Stimm-Einlernen](concepts/proximus-stimm-einlernen/KONZEPT.md), Etappe 1). Alles andere ergibt die Gastansicht, das `speaker`-Ereignis nennt den Grund in `status`:

| `status` | Wann |
|---|---|
| `matched` | bestes Profil ≥ Schwelle und, bei mehreren Profilen, mindestens 0,05 vor dem zweitbesten |
| `unknown` | bestes Profil unter der Schwelle (kein Ausweichen auf das zweitbeste) |
| `ambiguous` | zwei Profile über der Schwelle und weniger als 0,05 auseinander |
| `insufficient` | weniger als 1 s **Sprache** in der Aufnahme (Sprachframes, nicht Dateilänge; eine lange, fast stille Aufnahme zählt nicht) |
| `unavailable` | Sprechermodell fehlt, Fehler im Modell, Profil kaputt oder nicht übertragen (der Pi schickt dazu `enrolled`, die Zahl der Profile) |

Die Gastansicht enthält keine Fakten, keinen Verlauf und von den Direktiven nur installierte Module („Humor-Erweiterung installiert“); andere Direktiven können persönlich sein („nenne mich Meister“). Der Pi speichert Gastanfragen nicht im Verlauf. Kurze Befehle wie „Uhrzeit“ funktionieren als Gast weiter; für Gedächtnis und Kalender die Anfrage als ganzen Satz sprechen. Sprache misst [`src/voice_quality.py`](../src/voice_quality.py) für Anfragen, Kennenlernen und Personen-Authentifizierung gleich: 20-ms-Frames, WebRTC-VAD wenn installiert (`webrtcvad-wheels` auf CT 107, `SERVITOR_VAD_MODE` 0–3, Standard 2), sonst Energieschätzung (zweifacher Pegel des leisesten Zehntels, mindestens ~−44 dBFS); geschnitten wird mit 150 ms Rand. Beim Kennenlernen landen die Zahlen jedes Clips als `enroll_clip` im Journal (Sprachsekunden, Pegel, Clipping, Pausen, SNR, Problemcodes; kein Inhalt). Antworten ohne Sprache oder mit falschem WAV-Format werden gelöscht; übersteuerte, zu leise, verrauschte, zu kurze (< 2 s Sprache) oder stark unterbrochene Antworten liefern ihre Fakten, aber keinen Stimmabdruck. Nach dem Signalton wartet die Aufnahme 300 ms.

**Lokaler Rückfall auf dem Pi:** Ohne Server prüft niemand die Stimme. Ist eine Person eingelernt, gilt jede lokal beantwortete Anfrage deshalb als Gast: keine Fakten oder Termine, keine Gedächtnisänderungen, keine gelernten Zeilen, kein Verlauf, „Lerne mich kennen“ per Sprache wird abgelehnt (das Menü „Kennenlernen“ bleibt). Uhrzeit, Wetter, Status und Gerätesteuerung bleiben nutzbar.

## Systemwartung

[`src/sysmon.py`](../src/sysmon.py) zählt alle 6 h (erstmals 2 min nach dem Start) wartende Pakete mit `apt-get -s upgrade` (Simulation, ohne root, mit `nice`), davon die aus einer `-security`-Quelle. Nur der Pi wird überwacht; den Server wartet der Bediener selbst, Proximus fragt dessen Updates nicht ab. Proximus sagt neue Updates an und erinnert höchstens einmal am Tag, nie im Schlaf; die Frage „Gibt es Updates?“ und der Statusbericht nennen sie auch. Installiert wird nur im [Wartungsmodus](maintenance.md).

Die Zahlen sind nur so aktuell wie die Paketlisten. `apt-daily` aktualisiert sie nur mit `APT::Periodic::Update-Package-Lists "1"` ([`deploy/20proximus-update-lists`](../deploy/20proximus-update-lists)); auf CT 107 ist das seit 08.10.2026 gesetzt, auf dem Pi mit dem Befehl oben. Sind die Listen älter als 7 Tage, sagt Proximus das dazu.

## Netzwerk

Alle 30 s misst der Pi WLAN-Signal (`/proc/net/wireless`), die TCP-Verbindungszeit zu 1.1.1.1:443 und zum Server im LAN sowie die DNS-Auflösung von openrouter.ai. „Wie ist das Netzwerk?“ liest das vor. Zusätzlich zu den bestehenden Alarmen (Netz, Internet, Server weg) warnt er, wenn ein Problem eine Minute anhält, mit Entwarnung:

| Alarm | an | aus |
|---|---|---|
| WLAN-Signal schwach | ≤ −80 dBm | > −72 dBm |
| Netz langsam | ≥ 400 ms | < 200 ms |
| DNS gestört | Auflösung schlägt fehl | Auflösung klappt |

Bei abgeschaltetem oder sich verbindendem WLAN schweigen diese Alarme.
