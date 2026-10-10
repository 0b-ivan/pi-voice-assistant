# Adafruit mini PiTFT 1,3″

Stand: 06.10.2026. Das vorhandene **Adafruit mini PiTFT 1,3″ (240 × 240, ST7789)** ist am Raspberry Pi Zero 2 W zusammen mit dem bestehenden WM8960-Aufbau grundsätzlich lauffähig.

## Oberfläche und Arbeitsschritte

Seit dem 07.10.2026 zeigt die Anzeige **Zuhören → Erkennen → Denken → Synthese
→ Rendern → Ausgabe** mit passenden Symbolen, kurzen Beschreibungen und der
verstrichenen Zeit je Schritt.

![Vorschau der sechs Arbeitsschritte auf dem PiTFT](images/display-steps-preview.png)

Die Vorschau wurde mit dem Display-Code und den Schriftarten des Pi gerendert;
sie ist kein Foto des Geräts. Die einzelnen Ansichten sind 240 × 240 Pixel groß.
[Zahnrad-Animation und technische Details](display-work-steps.md#thinking-animation).

## Bestätigter Stand

SPI0 war zunächst deaktiviert. In `/boot/firmware/config.txt` wurde nur `dtparam=spi=on` aktiviert; die vorhandene WM8960-Konfiguration blieb unverändert:

```ini
dtparam=i2s=on
dtoverlay=wm8960-soundcard
dtparam=spi=on
```

Nach dem Neustart wurden beide SPI0-Geräte bestätigt:

```text
/dev/spidev0.0
/dev/spidev0.1
```

Der anschließende ST7789-Farbtest wurde auf dem Pi bestätigt. Dafür ist kein zusätzlicher Kernel-/Framebuffer-Treiber nötig.

## Pinbelegung

Alle GPIO-Angaben verwenden BCM-Nummern.

| Funktion | GPIO | Physischer Pin |
|---|---:|---:|
| SPI MOSI | 10 | 19 |
| SPI SCLK | 11 | 23 |
| SPI CE0 / CS | 8 | 24 |
| Display D/C | 25 | 22 |
| Backlight | 22 | 15 |
| Taste A | 23 | 16 |
| Taste B | 24 | 18 |

Der WM8960 verwendet I²C auf GPIO2/3, I²S auf GPIO18/19/20/21 und die vorhandene PTT-Taste auf GPIO17. Damit überschneiden sich die für das PiTFT verwendeten Display-Signale nicht mit den WM8960-Signalpins.

## Testumgebung

Die Display-Bibliotheken werden bewusst in einer separaten Python-Umgebung installiert:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pil

mkdir -p ~/venvs
python3 -m venv ~/venvs/pitft --system-site-packages
source ~/venvs/pitft/bin/activate

python -m pip install --upgrade pip
python -m pip install adafruit-blinka adafruit-circuitpython-rgb-display
```

Danach:

```bash
python scripts/pitft-color-test.py
```

Der Test zeigt jeweils kurz Rot, Grün, Blau, Weiß und Schwarz.

## Boot-/Statusdienst

Der Statusbildschirm läuft direkt über SPI/ST7789 und benötigt keinen zusätzlichen Kernel-/Framebuffer-Treiber. `src/display.py` prüft SPI, WM8960, Netzwerk, Vosk-Modell, aktives TTS-Modell und `pi-ptt.service`. Netzwerk ist informativ und blockiert `SYSTEM READY` nicht, weil der dokumentierte Vosk-Betrieb offline funktioniert.

Installation:

```bash
sudo bash scripts/install-display.sh
sudo systemctl enable --now pi-display.service
```

Prüfung:

```bash
systemctl status pi-display.service --no-pager
journalctl -u pi-display.service -n 30 --no-pager
```

Der Dienst läuft als `obivan`, erhält nur die zusätzlichen Gruppen `spi` und `gpio`, nutzt eine eigene virtuelle Umgebung unter `/opt/pi-voice-assistant/.venv-display` und startet beim Boot über `multi-user.target`. Die funktionierende Audio-/Vosk-/Piper-Konfiguration wird nicht verändert.

Bestätigte Anzeige:

```text
PI ASSISTANT

SPI      .... OK
AUDIO    .... OK
NETWORK  .... OK
VOSK     .... OK
TTS      .... OK
VOICE    .... OK

SYSTEM READY
```

Die Systemprüfungen laufen nur alle zwei Sekunden. Der Live-Zustand wird dagegen alle 100 ms aus `/run/pi-ptt/display-event.json` gelesen, damit der Pi Zero nicht permanent `systemctl` und `ip` starten muss.

## Live-Zustände

`src/ptt.py` schreibt für Display-relevante strukturierte Events atomar einen kleinen Snapshot in das vorhandene Runtime-Verzeichnis. Persistiert werden bewusst nur Eventname, Version und Zeitstempel; Transkript, Fehlermeldungen oder andere Event-Felder landen nicht in dieser Datei. Das Journal behält weiterhin die vollständigen strukturierten Events.

Aktuelle Abbildung:

```text
waiting_for_release / transcript / cancelled
  -> BEREIT

recording
  -> ZUHÖREN

capture_ready / processing
  -> VERSTEHEN

status / speech_started
  -> SPRECHEN

stt_error / tts_error / speech_error
  -> FEHLER (3 Sekunden, danach BEREIT wenn das System gesund ist)
```

`stt_loading` und `tts_loading` halten während des Starts die Bootansicht aktiv. Ein Netzwerkausfall bleibt für den lokalen Vosk-Modus nur eine Information und blockiert `BEREIT` nicht.

`DENKEN` wird beim tatsächlichen LLM-Aufruf gesetzt; `SPRECHEN` beziehungsweise `AUSGABE` zeigt die laufende Wiedergabe auch bei Antworten von CT 107 oder aus dem Pi-Fallback. Die Anzeige setzt die vom Sprachdienst veröffentlichten Zustände um; frühere Display-Prototypen ohne vollständigen Antwortpfad sind überholt.

Die allerersten Kernelmeldungen direkt nach dem Einschalten werden weiterhin nicht angezeigt; ein echter DRM-/Framebuffer-Weg wäre eine separate, invasivere Entscheidung.

## Wetter

![Wetteransicht: heute Regen, dann Mittwoch mit Gewitter gewählt](images/display-weather.gif)

Beim Morgenbericht und bei Wetterfragen setzt `ptt.py` `weather_day` (0 = heute … 4) in `display-status.json`; `display.py` zeichnet dann mit 8 Bildern pro Sekunde `render_weather` aus `/run/pi-ptt/display-weather.json` (die vom Sprachdienst auf dem Gedächtnis-Stick gehaltene Fünf-Tage-Vorhersage). Oben „AUSPEX · WETTERDATEN“ (Sprechstil BILLY: „WETTER“) und der Abrufzeitpunkt, links das Piktogramm (24×24 Pixel, dreifach skaliert, mit Scanlines), rechts Tag, Temperatur, Spanne, Wetterlage und Regenwahrscheinlichkeit, unten der Fünf-Tage-Streifen. Die Vorschau ist mit dem Display-Code gerendert, kein Foto. [Architektur](architecture.md#morgenlitanei-und-wetter).

## Ruhe und Schlaf

`ptt.py` kennt drei Stufen und schreibt sie als `power` in `display-status.json`:

| Stufe | ab (ohne Aktivität) | Display | LED |
|---|---|---|---|
| `awake` | – | Schädel mit pulsierendem Auge, Litaneien, zweites Auge in Statusfarbe | normal |
| `rest` | 30 s (`PTT_REST_SECONDS`) | auf 35 % abgedunkelt, rotes Auge schwach und ruhig, zweites Auge aus, keine Litaneien; Neuzeichnen etwa einmal pro Minute (Uhr, Akku, Alarm) | 30 % |
| `sleep` | 10 min (`PTT_SLEEP_SECONDS`) | Hintergrundbeleuchtung aus, kein Rendern | aus |

Beim Aufwachen aus dem Schlaf sagt Proximus kurz an, dass er erwacht (je nach Lore-Stufe, z. B. „Der Maschinengeist erwacht. Kogitatoren werden vorgewärmt.“; vorgefertigter Clip wie bei den Alarmen). Weckt ihn das Aktivierungswort, hört er erst nach dieser Ansage zu. Nach Druck auf die Sprechtaste, bei Alarmen und Statusansagen entfällt sie.

Aktivität sind Tastendrücke, Aufnahme, Verarbeitung, Sprachausgabe (auch Alarme), offenes Menü. Das Aktivierungswort lauscht in allen Stufen. Der erste Druck auf eine PiTFT-Taste weckt nur. Mit `PTT_SLEEP_WLAN=off` schaltet der Schlaf zusätzlich das WLAN ab und das Aufwachen wieder an (nur wenn der Schlaf es abgeschaltet hat); dann ist der Pi im Schlaf nicht per SSH erreichbar, und der erste Befehl nach dem Aufwachen läuft eventuell lokal, solange das WLAN sich verbindet.

## Statusinformationen mit Servitor-Server

![Neun Display-Zustände mit Verarbeitungsort, Serverstatus, WLAN, Temperatur, Uhrzeit und letzter Antwort](images/display-status-preview.png)

Vorschau mit dem echten Display-Code und den Pi-Schriften, erzeugt mit [`scripts/render-display-preview.py`](../scripts/render-display-preview.py); kein Foto des Bildschirms.

| Stelle | Inhalt |
|---|---|
| Kopfzeile rechts | CPU-Temperatur (ab 70 °C rot) und Akku: Symbol mit Füllstand und Prozent, cyan mit Blitz beim Laden, gelb ≤ 40 %, rot ≤ 15 % |
| Schrittzeile rechts | **SERVER** (cyan) oder **LOKAL** (gelb): wo der aktuelle Durchlauf gerechnet wird; im Ruhezustand („NÄCHSTE ANFRAGE“), wohin die nächste Anfrage geht |
| Zeile unter der Beschreibung (Ruhe) | letzte Antwort: Wartezeit vom Loslassen bis zum ersten Ton und Herkunft (`Server`, `Offline-LLM` = lokales Modell auf CT 107, `Pi lokal` = Fallback) |
| Zeile darunter (Ruhe) | Akku-Detail „Akku 83 % · 3,86 V · lädt / Netz · voll / Akkubetrieb“; Vorrang haben Warnungen des Pi: `UNTERSPANNUNG!` (rot), `CPU gedrosselt`, `Unterspannung seit Start` |
| Menü / Systeminfo | Über die PiTFT-Tasten, siehe [Button-Bedienung](button-controls.md#menü-auf-dem-pitft); „Display aus“ schaltet die Hintergrundbeleuchtung (GPIO22) ab |
| Lautstärke (C/D) | 2,5 s lang „LAUTSTÄRKE“ mit Balken und Prozent (Bereich −60…0 dB), an den Grenzen MAX/MIN; ersetzt kurz die unteren Zeilen, auch während einer Antwort |
| Fußzeile links | `CT107 OK` / `CT107 AUS` / `NUR PI` (kein `ASSISTANT_BASE_URL`) |
| Fußzeile Mitte | WLAN-Signal in dBm (grün ≥ −67, gelb ≥ −78, sonst rot), `NET OK` ohne WLAN-Wert, `OFFLINE` |
| Fußzeile rechts | Uhrzeit |

Datenquellen: `ptt.py` schreibt `/run/pi-ptt/display-status.json` nur mit festen Werten (`route`, `last_route`: `server`/`pi`; `last_llm`: `openrouter`/`offline`; `last_latency_ms`), nie Transkript oder Antworttext; unbekannte Werte werden verworfen. Der Display-Dienst fragt `GET /health` des ersten `ASSISTANT_BASE_URL` alle 10 s in einem Hintergrund-Thread ab (0,5 s Timeout, ohne Token), liest Temperatur aus `/sys/class/thermal` und das WLAN-Signal aus `/proc/net/wireless`. Den Akku liest er alle 5 s per I²C von der PiSugar 3 (`0x57`, nur Lesezugriffe; Register wie im Herstellertreiber) und mittelt Ladestand und Spannung über eine Minute, weil das Ladestand-Register mit den Ladeimpulsen springt. „lädt“ folgt der Herstellerlogik: Netzteil angesteckt und Laden erlaubt (unter 100 %). Die Spannungswarnungen stammen aus `vcgencmd get_throttled`. Ohne PiSugar bleiben die Akkufelder leer.

## Nächster Schritt

Noch **nicht implementiert**:

- endgültige mechanische Montage im Hardware-Stack
