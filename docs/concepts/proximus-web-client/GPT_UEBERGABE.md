# Übergabe: Proximus-Web-Client (Vox-Terminal)

Stand 14.10.2026, Branch `claude/wizardly-euler-ogqg5u` (Commit `3aa04d0`), noch
**kein PR** nach `main`. Bedienerdoku: [`docs/web-client.md`](../../web-client.md).

## Ziel

Proximus aus derselben Codebasis als Browser-Client. CT 107 (`server/servitor_server.py`)
liefert die Seite aus und beantwortet sie mit genau derselben Pipeline wie den Pi
(Vosk → Intents/Regelwerk → OpenRouter/lokales Qwen → Piper + Servitor-DSP).
Erreichbar unter **https://proximus.obivan.org/** (cloudflared auf CT 100 →
`172.22.9.107:8765`). Optik: imperiales Vox-Gerät (Warhammer 40k, Adeptus
Mechanicus) mit Bedienteil im Stil von Bundeswehr-Funkgeräten.

## Wünsche des Bedieners (wichtig für alles Weitere)

- Antworten und UI auf Deutsch; Commits auf Deutsch wie im Repo üblich.
- **Pragmatisch:** keine Deko-Texte, keine Fantasie-Nummern, keine Binärzeilen
  (wurden ausdrücklich entfernt). Effekte ja, Füllstoff nein.
- Atmosphäre ausdrücklich erwünscht: Rauschen, Bildstörungen, Litaneien,
  Fremdkanal-Gemurmel. **Litaneien-Häufigkeit bleibt wie sie ist** (alle 4–12 s).
- Proximus muss **verständlich** bleiben (Funkfilter wurde deshalb gemildert).
- Text und Stimme laufen synchron (Fernschreiber tippt mit der Stimme).
- Kein Modellname unter Antworten.

## Dateien

| Datei | Inhalt |
|---|---|
| `web/index.html` | Anruf-Terminal (`#dial`), Kopfzeile, Röhre (`#chat` mit `#scope`, `#journal`, `#log`), Bedienteil (`aside.panel`), Steuerleiste, Einstellungsdialog, SVG-Symbole (Schädel-Zahnrad, Zahnrad mit Sechsecken, Zahnrad mit Dürers magischem Quadrat) |
| `web/app.js` | Anruf/Login, Aufnahme (16 kHz PCM), Anfragen + NDJSON, Wiedergabe-Warteschlange, synchroner Fernschreiber (`addReply`/`typeAlong`/`closeReply`), Hardware-Schalter (`POSITIONS`, `setOption`, `buildHardware`/`renderHardware`), Journal (`pollJournal`, `describe`, `severity`, Litaneien `LITANY`, `interference`) |
| `web/vox.js` | Web-Audio: Geräusche (Klick, Squelch, Bitfolge, Tuning, Knacken, **Fremdkanal** `crosstalk`), Funkfilter auf der Stimme (dry/wet), Kanalrauschen, Lautstärke, Oszilloskop, S-Meter-Zeiger, Bildstörungen |
| `web/capture-worklet.js` | AudioWorklet Mikrofon → 16 kHz s16le |
| `web/style.css` | Gesamte Optik; mobil zuerst, Desktop ab 900 px (Bedienteil, Log-Spalte rechts) |
| `web/fonts/` | TeX Gyre Schola (Century Schoolbook der AdMech-Transfers, GUST-Lizenz), Share Tech Mono (OFL), Lizenzen + README |
| `web/mechanicus.svg` | Schädel-Zahnrad als Datei (Favicon) |
| `server/servitor_server.py` | `WEB_FILES` (Whitelist), `WEB_CSP`, `/v1/ask`, Login/Sitzungen, Journal, Request-Ereignisse, Fahrt-Antwort für Clients ohne Fahrtauskunft |
| `tests/test_servitor_server.py` | Tests für alles Obige (126 Tests in der Datei) |
| `server/install-ct.sh` | kopiert `web/` samt Unterordnern mit |

## Server-Endpunkte (neu)

- `GET /`, `GET /web/…` ohne Anmeldung, nur Dateien aus `WEB_FILES`, strenge CSP.
- `POST /v1/ask` `{"text": …}` (≤ 500 Zeichen): getippte Frage nimmt den Weg eines
  Transkripts (`run_turn(typed=…)`).
- `POST /v1/login` `{"password", "remember"}` → Cookie `servitor_session`
  (HttpOnly, SameSite=Strict, über Tunnel Secure; 12 h / 30 Tage; nur RAM).
  5 Fehlversuche → 15 min Sperre. `GET /v1/session`, `POST /v1/logout`.
  Hash: `servitor_server.py --hash-password` → `SERVITOR_WEB_PASSWORD_HASH` in
  `/etc/servitor-voice.env` (Kennwort ändern: alte Zeile löschen, neu erzeugen).
- Cookie zählt **nur mit Header `X-Servitor-Client: web`** und nur für
  `/v1/turn|speak|story|ask` und `/v1/journal`. `/v1/status`, Wartung,
  Nachtprüfung, Stimmdaten bleiben **token-only**. Der Pi ist unverändert (Token).
- `GET /v1/journal?after=N` → `{seq, host, lines:[{seq, ts, text, unit, prio, host}]}`.

## Journal (der offene Hauptpunkt)

Soll: **alle Logs des gesamten Systems** hinter dem Chat – Kernel, alle Dienste,
und sichtbar, wenn der Pi kommuniziert.

Umgesetzt:
- `follow_system_journal()` startet `journalctl -f --merge -n 80 -o json` (Thread,
  Neustart nach 5 s). Fallback ohne journalctl: `Tee` auf stdout/stderr
  (`SERVITOR_JOURNAL_SOURCE=own` erzwingt das).
- `Journal.add()` filtert HTTP-Zugriffszeilen und „Request timed out“, maskiert
  IPv4 (`*.*.*.9`) und IPv6, hält 300 Zeilen im RAM.
- `Handler.log_request()` schreibt pro API-Anfrage `{"event":"request","client":"pi|web|anon",…}`
  → im Browser „PI ▸ POST /v1/turn → 200“ (hell hervorgehoben).
- Client: Desktop eigene Spalte rechts in der Röhre, Handy hinter dem Chat;
  neueste Zeile oben; `flex: none` verhindert das frühere Zusammenstauchen.

**Problem laut Bediener (noch offen): „Nach einiger Zeit sehe ich nur noch
Litaneien“ – das Log muss funktionieren.** Hypothesen, in dieser Reihenfolge prüfen:
1. Auf CT 107 ausgerollt ist evtl. nicht der neueste Stand
   (`cat /opt/servitor-voice/DEPLOYED` muss `3aa04d0…` sein).
2. Der Dienst sieht das Journal nicht: `sudo -u servitor journalctl -n 5 --merge`
   in CT 107. Unit hat `SupplementaryGroups=systemd-journal`; prüfen, ob die
   Gruppe im Container existiert und der Journald persistent/volatil läuft.
3. `journalctl -f` bricht im Container ab (Thread startet alle 5 s neu, ohne
   Meldung, weil stderr nach DEVNULL geht) – testweise stderr ins Journal
   schreiben bzw. `curl -s -H 'Authorization: Bearer <TOKEN>' https://proximus.obivan.org/v1/journal?after=0 | head`.
4. CT 107 ist schlicht sehr ruhig; dann fehlt die Weiterleitung (Punkt unten).
   Erst dann eventuell ein Lebenszeichen (z. B. periodisches Status-Ereignis)
   ergänzen – **nicht** die Litaneien reduzieren.

**Noch nicht gebaut: Journal-Weiterleitung** (Kernel-Log gehört dem Proxmox-Host,
CT 107 ist LXC und sieht ihn nicht; Pi-Logs ebenso). Plan:
`deploy/journal-forward/install.sh` mit Rollen
- `server` (CT 107): `systemd-journal-remote` installieren, Socket 19532,
  Override `ExecStart=… --listen-http=-3 --output=/var/log/journal/remote/`,
  Drop-in für `servitor-voice` mit `SupplementaryGroups=systemd-journal-remote`
  (nicht in die Haupt-Unit: fehlt die Gruppe, startet der Dienst nicht).
- `client <URL>` (Proxmox-Host, Pi): `systemd-journal-upload` mit
  `URL=http://172.22.9.107:19532`.
Danach zeigt `--merge` alles; `describe()` setzt bei fremdem `_HOSTNAME`
„wgz-pve-01 ▸ kernel …“ davor. Lesbarkeit der Remote-Dateien für den Dienst
nach Einrichtung mit `sudo -u servitor journalctl -m -n 5` prüfen. HTTP im LAN
ist unverschlüsselt (Hinweis an den Bediener).

## Weitere offene Punkte / Ideen

- „system status?“ trifft nicht die Status-Regel (`src/intents.py`) und landet
  beim LLM, das „alles optimal“ erfindet – betrifft auch den Pi. Angeboten,
  nicht umgesetzt.
- Servitor-Schädel mit bionischer Hälfte (AdMech-Transferbogen) als Variante für
  Persona „Servitor“ – angeboten, nicht umgesetzt.
- Größeres LLM: `OPENROUTER_LLM_MODEL` in `/etc/servitor-voice.env`; Bediener
  hat abgelehnt („passt schon“).
- PR nach `main` noch nicht erstellt.

## Ausrollen und Prüfen

```sh
# Proxmox-Host
pct exec 107 -- sh -c 'cd /opt/servitor-voice/repo && git fetch origin claude/wizardly-euler-ogqg5u && git checkout --detach FETCH_HEAD && git rev-parse HEAD > /opt/servitor-voice/DEPLOYED && systemctl restart servitor-voice'
curl -s https://proximus.obivan.org/health
```

Neustart meldet alle Browser-Sitzungen ab (Sitzungen nur im RAM).

## Entwickeln und Testen

```sh
python3 -m unittest tests.test_servitor_server      # ~60 s
python3 -m unittest discover -s tests               # ~150 s; test_hotspot_qr braucht das Paket qrcode
node --check web/app.js web/vox.js
```

Browser-Tests liefen mit Playwright/Chromium gegen einen lokalen Server mit
`tests/test_servitor_server.FakePipeline` (Fake-Mikrofon per
`--use-fake-device-for-media-stream`). Regeln, die die Tests absichern:
jede Datei in `WEB_FILES` existiert; HTML/CSS verweisen nur auf ausgelieferte
Dateien; `app.js` ruft nur `/v1/turn|ask|story|status|journal|login|logout|session`.
CSP erlaubt keine Inline-Skripte/-Styles im HTML (CSSOM über JS ist ok), keine
fremden Hosts. Geheimnisse nie ins Repo.
