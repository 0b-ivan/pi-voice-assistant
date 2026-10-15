# Übergabe: Proximus-Web-Client (Vox-Terminal)

Stand 15.10.2026, aktueller Branch `fix/proximus-journal-nur-dienste-20261014`,
auf `main` einschließlich #116 und #119 (`92c2d46`) aktualisiert.
Der konsolidierte Web-PR ersetzt den älteren Gateway-Entwurf #105.
Bedienerdoku: [`docs/web-client.md`](../../web-client.md).

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

## Journal: gültiger Stand

- Der Sprachdienst liest ausschließlich lokale `servitor-voice.service`- und
  `servitor-llm.service`-Einträge. Units und Rechnername werden zusätzlich im
  Server geprüft; fremde Systemdienste gehören nicht in die Browseranzeige.
- Der frühere Volljournal-Versuch vom 14.10.2026 übertrug auch Proxmox-,
  Kernel-, SSH- und Rclone-Meldungen. Dieser Versuchsstand ist verworfen.
- `deploy/journal-forward/install.sh` ist gesperrt. Keine Host- oder
  Pi-Vollweiterleitung einrichten. Bereits vorhandene Host-Uploader und
  CT-Empfänger gemäß [Bedienerdoku](../../web-client.md#journal-sicherheit-und-abschaltung-der-vollweiterleitung)
  stilllegen; dort auch bereits empfangene Dateien prüfen.
- Auf dem ausgeschalteten Pi wurde keine Weiterleitung abgenommen.
  Pi-Forwarding bleibt aus, bis eine ausschließlich auf Proximus-Dienste
  begrenzte, separat geprüfte Lösung vorliegt.
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
Abbruch, Zeitlimit, Texteingabe und fünf mobile Größen. Die frühere Prüfung
der LAN-Empfänger und des Host-Uploads ist nur ein historischer Nachweis des verworfenen Versuchs. Keine Pi-Abnahme bei
ausgeschaltetem Gerät; keine bestätigte Stilllegung der alten Dienste.

## Weitere offene Punkte / Ideen

- „system status?“ ist inzwischen als Status-Intent abgesichert. Fehlende
  Pi-Messwerte werden ausdrücklich als nicht verfügbar gemeldet.
- Echte neue LLM-Dialoge mit Servitor/Billy und Lore light/full auf CT 107
  qualitativ abnehmen; Prompt-/Regressionstests ersetzen diese Prüfung nicht.
- Servitor-Schädel mit bionischer Hälfte (AdMech-Transferbogen) als Variante für
  Persona „Servitor“ – angeboten, nicht umgesetzt.
- Größeres LLM: `OPENROUTER_LLM_MODEL` in `/etc/servitor-voice.env`; Bediener
  hat abgelehnt („passt schon“).
- Konsolidierten Web-PR nach aktueller CI prüfen; Merge und Deployment
  benötigen weiterhin eine ausdrückliche Anweisung.

## Ausrollen und Prüfen

Nur den ausdrücklich freigegebenen vollständigen Commit-SHA ausrollen.
Die alten Entwicklungsbranches sind keine Deployment-Ziele. Aktuelle
Anleitung: [Ausrollen auf CT 107](../../web-client.md#ausrollen-auf-ct-107).
Vor dem Neustart die Stilllegung der Volljournal-Weiterleitung separat prüfen.

Neustart meldet alle Browser-Sitzungen ab (Sitzungen nur im RAM).

## Entwickeln und Testen

```sh
python3 -m unittest tests.test_servitor_server      # ~60 s
python3 -m unittest discover -s tests               # ~150 s; test_hotspot_qr braucht das Paket qrcode
node --check web/app.js && node --check web/vox.js
```

Browser-Tests liefen mit Playwright/Chromium gegen einen lokalen Server mit
`tests/test_servitor_server.FakePipeline` (Fake-Mikrofon per
`--use-fake-device-for-media-stream`). Regeln, die die Tests absichern:
jede Datei in `WEB_FILES` existiert; HTML/CSS verweisen nur auf ausgelieferte
Dateien; `app.js` ruft nur `/v1/turn|ask|story|status|journal|login|logout|session`.
CSP erlaubt keine Inline-Skripte/-Styles im HTML (CSSOM über JS ist ok), keine
fremden Hosts. Geheimnisse nie ins Repo.
