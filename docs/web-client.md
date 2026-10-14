# Web-Client

Proximus im Browser, aus derselben Codebasis wie der Pi. Der Server auf CT 107
liefert die Seite selbst aus (`web/`) und beantwortet sie mit genau derselben
Pipeline wie den Pi: Vosk → Regelwerk/Intents → OpenRouter bzw. lokales Qwen →
Piper Thorsten + Servitor-DSP. Es gibt keinen zweiten Server und keinen
Build-Schritt; die Seite besteht aus wenigen statischen Dateien.

```text
Browser: Taste halten → Mikrofon → 16 kHz PCM → POST /v1/turn  ┐
         Frage tippen ───────────────────────→ POST /v1/ask   ├→ CT 107 (servitor_server.py)
         lange Geschichte, nächster Abschnitt → POST /v1/story ┘   NDJSON: transcript, reply, audio …
```

## Aufrufen

| Weg | Adresse | Mikrofon |
|---|---|---|
| Cloudflare-Tunnel (HTTPS) | **https://proximus.obivan.org/** | ja |
| Auf CT 107 selbst | `http://localhost:8765/` | ja |
| LAN | `http://172.22.9.107:8765/` | nein, nur Texteingabe |

Browser geben das Mikrofon nur in einem sicheren Kontext frei (HTTPS oder
localhost). Über die LAN-Adresse funktioniert deshalb nur das Tippen; für
Sprache den Tunnel nehmen. Die Seite liegt unter `/` und zusätzlich unter
`/web/`; ihre Dateien lädt sie immer aus `/web/`.

`proximus.obivan.org` zeigt über cloudflared auf CT 100 bereits auf
`172.22.9.107:8765` (derselbe Weg wie der Fallback des Pi), am Tunnel ist
nichts einzustellen.

Beim ersten Aufruf fragt die Seite nach dem Server-Token (`SERVITOR_API_TOKEN`
aus `/etc/servitor-voice.env`). Er bleibt nur im Arbeitsspeicher der Seite,
außer „Token in diesem Browser merken“ ist angehakt (dann `localStorage`).
Auf dem Handy lässt sich die Seite zum Startbildschirm hinzufügen (Manifest).

## Darstellung: das Vox-Terminal

Die Seite sieht aus wie ein Vox-Gerät des Imperiums: eisernes Gehäuse,
Phosphor-Röhre, Lampen und Zeigerinstrument. Wer sie öffnet, **ruft Proximus
über Vox an**:

1. **Anruf-Bildschirm** mit Lautsprechergitter und Röhre. Vox-Code (Token)
   eingeben oder hinterlegt lassen, dann „Verbindung herstellen“.
2. **Wählsequenz** (ca. 4 s, mit Ton): Terminal erwacht, Litanei, Suche nach
   dem Trägersignal (Rauschen mit Pfeifton), gemessene Signallaufzeit, Prüfung
   des Vox-Codes über `GET /v1/status`, binharische Bestätigung. Ein falscher
   Code ergibt „ZUGANG VERWEIGERT“, ein fehlender Server „KEIN TRÄGERSIGNAL“.
3. Die Röhre schaltet sich ein. Ein leises Rauschen zeigt den offenen Kanal an.
   ⏻ trennt den Kanal und führt zurück zum Anruf.

Im Betrieb:

| Element | Bedeutung |
|---|---|
| Oszilloskop oben in der Röhre | eigene Stimme beim Senden, Proximus beim Antworten, sonst Trägerrauschen; unruhig, während der Server arbeitet |
| Zeigerinstrument „SIGNAL“ | Sprachpegel beim Senden/Empfangen, sonst Signalstärke aus der gemessenen Laufzeit zu `/health` |
| grüne Lampe | Kanal verbunden, Server bereit |
| rote Lampe | leuchtet beim Senden, blinkt bernsteinfarben, während der Server arbeitet |
| Schädel | grüner Geist auf der Röhre, glimmt je nach Schritt |
| Protokoll | `BEDIENER »` und `PROXIMUS »` wie ein Fernschreiber; Proximus' Zeilen tippen sich ein |

Geräusche (alle im Browser erzeugt, nichts wird geladen): Klicken der
Sendetaste, binharische Bitfolge beim Absenden (wie der Quittungston des Pi),
Trägerstoß vor jeder Antwort, Rauschschwanz am Ende. Proximus' Stimme läuft
zusätzlich durch einen Funkfilter (Bandpass 320–3300 Hz, leichte Sättigung):
Er klingt weit weg. Gelegentlich reißt das Bild kurz ab und knackt es.

„Vox-Effekte“ in den Einstellungen schaltet Rauschen, Funkfilter, Störungen
und Schreibmaschineneffekt ab; die Antworten bleiben gleich. Bei „Bewegung
reduzieren“ im Betriebssystem entfallen alle Animationen.

- **Mobil (unter 600 bzw. 900 px):** Der Schritt steht in der Röhre unter dem
  Oszilloskop, das Eingabefeld liegt über einer großen Sendetaste.
- **Desktop (ab 900 px):** Schritt in der Kopfzeile, großer Schädel, Sendetaste,
  Eingabe und Übertragen in einer Zeile, Bediener rechts, Proximus links.

### Synchroner Fernschreiber und Bedienteil

- **Proximus schreibt, während er spricht:** Eine Antwort erscheint zuerst nur
  als blinkender Cursor. Jeder Audioteil tippt seinen Text in genau der Zeit,
  die die Stimme dafür braucht, beginnend mit dem Ton. Abbrechen zeigt den
  Rest sofort. Ohne Ton (Fehler) erscheint der Text allein.
- **Kein Modellname** mehr unter den Antworten.
- **Gehäuse in Feldfunk-Oliv** (angelehnt an Bundeswehr-Funkgeräte): weiße
  Schablonenschrift, gelbe Beschriftungsstreifen, Nummernschilder.
- **Desktop-Bedienteil** rechts neben dem Monitor: großes Zeigerinstrument,
  Wahlschalter (Bakelit-Drehknopf mit gelbem Streifen) für Persona, Stimme,
  Lore und Sprachkern, Kippschalter für Störungen und Sendeart, Lautstärke mit
  Pfeiltasten und Lampenzeile. Knopf anklicken schaltet weiter (auch mit
  Pfeiltasten), ein Feld im Streifen wählt direkt. Dieselben Werte wie im
  Einstellungsdialog, der auf dem Handy bleibt.
- **Anruf-Gerät auf dem Desktop** breit: großer Monitor links, rechts
  Nummernschild, Störungsschalter, Lautstärke, Vox-Code und Verbinden.
- Fernschreiberschrift: Share Tech Mono (SIL OFL, `web/fonts/`).

### Schrift, Symbole, Litaneien

- **Schrift:** Beschriftungen auf Gehäuse, Knöpfen und im Protokoll in
  *Century Schoolbook*, der Schrift der Adeptus-Mechanicus-Transferbögen
  (von der Community so identifiziert). Ausgeliefert wird die freie
  Variante TeX Gyre Schola (GUST Font License, `web/fonts/`), auf Latin-1
  verkleinert, je ca. 19 KB. Der Fernschreibertext bleibt Monospace.
- **Symbole** (selbst gezeichnete SVGs nach den Transferbögen):
  Schädel im Zahnrad (Kopfzeile, Anruf-Gerät, Browser-Symbol
  `web/mechanicus.svg`), Zahnrad mit drei Sechsecken (Wasserzeichen im
  Anruf-Bildschirm), Zahnrad mit Dürers magischem Quadrat (dreht sich in der
  Kopfzeile, solange der Server arbeitet, und steht über dem leeren Protokoll).
- **Litaneien:** Die Wählsequenz folgt der Litanei der Zündung („Die Seele
  des Maschinengottes umgibt dich …“, „Lebe!“), der Zugang wird mit „Dignus
  est intrare“ gewährt, ein falscher Code mit „Im Namen des Omnissiah,
  identifiziere dich“ abgewiesen. Auflegen endet mit „Die Lebenden schwinden.
  Aber die Maschine besteht.“ Über dem leeren Protokoll steht ein zufälliger
  Spruch („Mens sana in machina sano“ …). Alle Texte stehen in `LITANY`
  in `web/app.js`.

## Bedienung

- **Halten zum Sprechen** (Maus, Finger oder Leertaste), loslassen schickt die
  Aufnahme. In den Einstellungen auf „Antippen statt halten“ umstellbar.
  Höchstens 29 s (Server-Grenze `SERVITOR_MAX_AUDIO_SECONDS=30`).
- **Frage tippen**: geht denselben Weg wie ein erkannter Satz, nur ohne
  Spracherkennung (`/v1/ask`, höchstens 500 Zeichen).
- **Abbrechen** oder `Esc`: Aufnahme verwerfen, Anfrage abbrechen, Ausgabe stoppen.
- **Einstellungen** (⚙): Persona (Servitor/Billy), Lore-Stufe, Stimme
  (Servitor/natürlich), Sprachkern (Auto/Frei/Lokal) – dieselben Werte wie im
  PiTFT-Menü, sie gehen pro Anfrage im Header `X-Servitor-Status` mit.
- Lange Geschichten („Erzähl mir eine lange Geschichte …“) laufen in Teilen;
  den nächsten Abschnitt holt die Seite, während der letzte Teil noch spielt.

Antworten kommen als Ogg/Opus, wenn der Browser es abspielen kann, sonst WAV.

## Was der Web-Client nicht kann (absichtlich)

Der Browser ist ein **Gast**. Er schickt weder `memory` noch `devctl` noch
`trip` im Status, daher:

- kein Gedächtnis (Fakten, Direktiven, Verlauf liegen auf dem USB-Stick am Pi),
  keine Stimmerkennung, keine Termine aus Nextcloud,
- keine Gerätesteuerung (WLAN, Neustart, Herunterfahren) und kein Wartungsmodus,
- keine Fahrtauskunft (die fragt der Pi selbst ab),
- Akku-, Temperatur- und Netzfragen antworten „nicht verfügbar“ – es gibt kein
  Gerät, das sie messen könnte.

Uhrzeit, Datum, Wetter, „wer bist du“, freie Fragen an
das LLM und Geschichten funktionieren wie am Pi.

## Sicherheit

- Ohne Token liefert der Server nur die festen Dateien aus `WEB_FILES`
  (`server/servitor_server.py`); jeder andere Pfad unter `/web/` ergibt 404.
  Jede Anfrage der Seite an `/v1/*` braucht den Token.
- Die Seite läuft mit strenger Content-Security-Policy (nur eigene Skripte,
  kein Inline-Code, keine fremden Verbindungen) und setzt Texte nur als
  `textContent`.
- Rate-Limit und „ein Turn zugleich“ gelten für Browser und Pi gemeinsam: Spricht
  gerade der Pi, wartet der Browser bis zu `SERVITOR_BUSY_WAIT_SECONDS` und
  bekommt sonst „Server beschäftigt“.
- Der Browser nutzt denselben Token wie der Pi. Wer den Token im Browser
  speichert, sollte das Gerät entsprechend schützen.

## Dateien

| Datei | Inhalt |
|---|---|
| `web/index.html` | Oberfläche, Einstellungsdialog |
| `web/app.js` | Anruf, Aufnahme, Anfragen, NDJSON-Auswertung, Wiedergabe |
| `web/vox.js` | Vox-Effekte: Geräusche, Funkfilter, Oszilloskop, Zeiger, Störungen |
| `web/capture-worklet.js` | AudioWorklet: Mikrofon → 16 kHz mono s16le |
| `web/style.css` | Darstellung |
| `web/manifest.webmanifest` | Startbildschirm-Symbol |
| `assets/display/servo-skull.png` | derselbe Schädel wie auf dem PiTFT |

## Ausrollen auf CT 107

`/opt/servitor-voice/repo` ist ein Git-Checkout, `web/` kommt mit dem Commit:

```sh
ssh root@172.22.2.11
pct exec 107 -- sh -c 'cd /opt/servitor-voice/repo && git fetch origin <branch-oder-main> \
  && git checkout --detach FETCH_HEAD && git rev-parse HEAD > /opt/servitor-voice/DEPLOYED \
  && systemctl restart servitor-voice'
```

Prüfen (nach dem Laden der Modelle, ca. 10 s):

```sh
curl -s https://proximus.obivan.org/health                 # {"ok": true, "ready": true}
curl -sI https://proximus.obivan.org/ | grep -i content-type  # text/html
```

Wer aus einem anderen Checkout installiert, nimmt `server/install-ct.sh`; es
kopiert `web/` und den Schädel mit.
