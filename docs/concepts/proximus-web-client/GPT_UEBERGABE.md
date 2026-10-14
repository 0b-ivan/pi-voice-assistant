# Übergabe: Proximus-Web-Client (Vox-Terminal)

Stand 14.10.2026, aktueller Branch `codex/proximus-web-journal-20261014`,
Basis `claude/wizardly-euler-ogqg5u` (letzter Code-Commit siehe `git log`),
**kein PR** nach `main`. Bedienerdoku: [`docs/web-client.md`](../../web-client.md).

## Korrektur: kein fremdes Systemjournal im Web

Die unten protokollierte Vollweiterleitung des Proxmox-Journals ist ein
**überholter Versuchsstand**. Sie umfasst fremde Dienste und darf nicht
weiterbetrieben werden. Der Fix beschränkt die Browser-API auf lokale
`servitor-voice.service`- und `servitor-llm.service`-Logs. Der alte
Installer ist gesperrt. Host-Uploader und CT-Empfänger müssen separat
abgeschaltet werden; Anweisungen stehen in `docs/web-client.md`.
Pi-Forwarding bleibt ausgeschaltet. Der Live-Fix auf CT 107 steht noch aus.

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

## Journal: Stand nach der Weiterarbeit am 14.10.2026

Branch der Weiterarbeit: `codex/proximus-web-journal-20261014`, auf Basis des
aktuellen `claude/wizardly-euler-ogqg5u` einschließlich Handy-Fix `de75251`.

- Der Sprachdienst sieht das System-Journal von CT 107. Gegenprobe mit den
  **tatsächlichen Zusatzgruppen** zeigte dieselben Systemmeldungen wie root.
  `runuser -u servitor` allein übernimmt die Zusatzgruppen der Unit nicht;
  daraus darf man keine fehlenden Journal-Rechte ableiten. Keine ACL-Änderung nötig.
- Host-Weiterleitung eingerichtet und live geprüft: `wgz-pve-01` sendet alle
  lokalen Journal-Meldungen über `systemd-journal-upload` an CT 107. In der
  Browser-API kamen Kernel, SSH, systemd-logind und Rclone an (in einer Abfrage
  34 Host-Zeilen, darunter 16 Kernelmeldungen).
- Der Empfänger hat je einen HTTP-Socket/Dienst an `172.22.2.107:19532` und
  `172.22.9.107:19532`. **Je Adresse eine Instanz:** der native Empfänger kann
  nicht zwei geerbte Sockets gleichzeitig als HTTP behandeln. Der Host nutzt
  das direkt erreichbare Verwaltungsnetz (`http://172.22.2.107:19532`), der Pi
  später das Pi-LAN (`http://172.22.9.107:19532`). Der geroutete Weg vom Host
  zur Pi-LAN-Adresse war für diesen Port nicht erreichbar.
- Skripte: `deploy/journal-forward/install.sh server [LAN-IP ...]` bzw.
  `client <URL>` und `seed-cursor.py`. Cursor im RAM; nach Boot etwa 80 alte
  Einträge plus alle neuen. Remote-Dateien unter `/var/log/journal/remote/`;
  Gruppenrechte per Sprachdienst-Drop-in. HTTP im LAN ist unverschlüsselt
  und ohne Client-Authentifizierung; nicht über den Tunnel veröffentlichen.
- **Pi ausgeschaltet:** Installer vorbereitet, dort noch nicht installiert oder
  abgenommen. Bei der nächsten Pi-Sitzung den Client einrichten (Befehle und
  Prüfung in `docs/web-client.md`).
- Browser hält 60 echte Logs und höchstens drei Effektzeilen. Litaneien und
  Störungen bleiben alle 4–12 s; sie verdrängen keine echten Einträge mehr.
- Eine feste Warnzeile zeigt Quellfehler, unterbrochene Abfragen und abgelaufene
  Anmeldung. Polls haben ein Zeitlimit und überlappen nicht; Auflegen bricht
  alte Abfragen ab. `instance` neben `seq` erkennt einen neuen RAM-Puffer nach
  Server-Neustarts, auch wenn sein Zähler den alten schon überholt hat.
- `journalctl`-Abbrüche und stderr werden sichtbar; ANSI wird entfernt und
  Adressen werden maskiert. Fantasie-Zahlen und Binärzeilen aus Störungen
  sowie die zufällige Fremdkanal-Nummer entfernt.
- Mobil: Texteingabe über volle Breite, zwei Zeilen, wächst bis vier Zeilen;
  Sprach-, Sende- und Abbrechen-Buttons in einer kompakten Zeile. Enter sendet,
  Shift+Enter fügt einen Zeilenumbruch ein. Kopfzeile passt auch auf 320 px.

Validierung: 130 Server-Tests, 37 Journal-/Upload-Tests; echter Chrome mit
simulierter API für lange Ruhephasen, Anmeldung, Quellfehler, Neustart,
Abbruch, Zeitlimit, Texteingabe und fünf mobile Größen. Beide LAN-Empfänger
und echter Host-Upload geprüft. Keine Pi-Abnahme bei ausgeschaltetem Gerät.

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
