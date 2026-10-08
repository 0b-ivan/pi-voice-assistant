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

`DENKEN` wird noch nicht vorgetäuscht: Dieser Zustand kommt erst mit dem tatsächlichen LLM-Aufruf. `SPRECHEN` ist aktuell bei der vorhandenen gesprochenen Statusansage sichtbar; der spätere Antwortpfad soll denselben generischen Eventzustand verwenden.

Die allerersten Kernelmeldungen direkt nach dem Einschalten werden weiterhin nicht angezeigt; ein echter DRM-/Framebuffer-Weg wäre eine separate, invasivere Entscheidung.

## Statusinformationen mit Servitor-Server

![Neun Display-Zustände mit Verarbeitungsort, Serverstatus, WLAN, Temperatur, Uhrzeit und letzter Antwort](images/display-status-preview.png)

Vorschau mit dem echten Display-Code und den Pi-Schriften, erzeugt mit [`scripts/render-display-preview.py`](../scripts/render-display-preview.py); kein Foto des Bildschirms.

| Stelle | Inhalt |
|---|---|
| Kopfzeile rechts | CPU-Temperatur, ab 70 °C rot |
| Schrittzeile rechts | **SERVER** (cyan) oder **LOKAL** (gelb): wo der aktuelle Durchlauf gerechnet wird; im Ruhezustand („NÄCHSTE ANFRAGE“), wohin die nächste Anfrage geht |
| Zeile unter der Beschreibung (Ruhe) | letzte Antwort: Wartezeit vom Loslassen bis zum ersten Ton und Herkunft (`Server`, `Offline-LLM` = lokales Modell auf CT 107, `Pi lokal` = Fallback) |
| Fußzeile links | `CT107 OK` / `CT107 AUS` / `NUR PI` (kein `ASSISTANT_BASE_URL`) |
| Fußzeile Mitte | WLAN-Signal in dBm (grün ≥ −67, gelb ≥ −78, sonst rot), `NET OK` ohne WLAN-Wert, `OFFLINE` |
| Fußzeile rechts | Uhrzeit |

Datenquellen: `ptt.py` schreibt `/run/pi-ptt/display-status.json` nur mit festen Werten (`route`, `last_route`: `server`/`pi`; `last_llm`: `openrouter`/`offline`; `last_latency_ms`), nie Transkript oder Antworttext; unbekannte Werte werden verworfen. Der Display-Dienst fragt `GET /health` des ersten `ASSISTANT_BASE_URL` alle 10 s in einem Hintergrund-Thread ab (0,5 s Timeout, ohne Token), liest Temperatur aus `/sys/class/thermal` und das WLAN-Signal aus `/proc/net/wireless`.

## Nächster Schritt

Noch **nicht implementiert**:

- sinnvolle Belegung der beiden PiTFT-Tasten auf GPIO23/24
- endgültige mechanische Montage im Hardware-Stack
