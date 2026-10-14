# Web-Client

Proximus im Browser, aus derselben Codebasis wie der Pi. Der Server auf CT 107
liefert die Seite selbst aus (`web/`) und beantwortet sie mit genau derselben
Pipeline wie den Pi: Vosk → Regelwerk/Intents → OpenRouter bzw. lokales Qwen →
Piper Thorsten + Servitor-DSP. Es gibt keinen zweiten Server und keinen
Build-Schritt; die Seite besteht aus vier statischen Dateien.

```text
Browser: Taste halten → Mikrofon → 16 kHz PCM → POST /v1/turn  ┐
         Frage tippen ───────────────────────→ POST /v1/ask   ├→ CT 107 (servitor_server.py)
         lange Geschichte, nächster Abschnitt → POST /v1/story ┘   NDJSON: transcript, reply, audio …
```

## Aufrufen

| Weg | Adresse | Mikrofon |
|---|---|---|
| Cloudflare-Tunnel (HTTPS) | `https://<tunnel-host>/web/` | ja |
| Auf CT 107 selbst | `http://localhost:8765/web/` | ja |
| LAN | `http://<CT-107-IP>:8765/web/` | nein, nur Texteingabe |

Browser geben das Mikrofon nur in einem sicheren Kontext frei (HTTPS oder
localhost). Über die LAN-Adresse funktioniert deshalb nur das Tippen; für
Sprache den Tunnel nehmen. `/` leitet auf `/web/` weiter.

Beim ersten Aufruf fragt die Seite nach dem Server-Token (`SERVITOR_API_TOKEN`
aus `/etc/servitor-voice.env`). Er bleibt nur im Arbeitsspeicher der Seite,
außer „Token in diesem Browser merken“ ist angehakt (dann `localStorage`).
Auf dem Handy lässt sich die Seite zum Startbildschirm hinzufügen (Manifest).

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
| `web/app.js` | Aufnahme, Anfragen, NDJSON-Auswertung, Wiedergabe |
| `web/capture-worklet.js` | AudioWorklet: Mikrofon → 16 kHz mono s16le |
| `web/style.css` | Darstellung |
| `web/manifest.webmanifest` | Startbildschirm-Symbol |
| `assets/display/servo-skull.png` | derselbe Schädel wie auf dem PiTFT |

`server/install-ct.sh` kopiert `web/` mit; bei einem Checkout direkt in
`/opt/servitor-voice/repo` liegt es ohnehin dort. Danach
`systemctl restart servitor-voice`.
