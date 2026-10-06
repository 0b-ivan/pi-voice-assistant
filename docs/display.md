# Adafruit mini PiTFT 1,3″

Stand: 06.10.2026. Das vorhandene **Adafruit mini PiTFT 1,3″ (240 × 240, ST7789)** ist am Raspberry Pi Zero 2 W zusammen mit dem bestehenden WM8960-Aufbau grundsätzlich lauffähig.

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

## Warum noch kein Framebuffer

Für die Statusanzeige des Sprachassistenten wird zunächst direkt per SPI/ST7789 geschrieben. Das hält Display und Audio voneinander getrennt und vermeidet einen zusätzlichen Kernel-/Framebuffer-Stack.

Damit kann später sehr früh im Userspace eine kompakte Boot-/Statusanzeige erscheinen, zum Beispiel:

```text
SPI ........ OK
AUDIO ...... OK
WLAN ....... OK
VOSK ....... LOAD
TTS ........ LOAD
ASSISTANT ... READY
```

Die allerersten Kernelmeldungen direkt nach dem Einschalten sind damit nicht gemeint. Ein echter DRM-/Framebuffer-Weg wäre eine separate, invasivere Entscheidung.

## Nächster Schritt

Noch **nicht implementiert**:

- eigener Display-Dienst für Boot- und Laufzeitstatus
- Übernahme der vorhandenen strukturierten Events wie `stt_ready`, `tts_loading` und `tts_ready`
- Zustände wie `BEREIT`, `ZUHÖREN`, `DENKEN`, `SPRECHEN`
- sinnvolle Belegung der beiden PiTFT-Tasten auf GPIO23/24
- endgültige mechanische Montage im Hardware-Stack

Die funktionierende Audio-/Vosk-/Piper-Konfiguration soll dafür nicht verändert werden.
