# Hardware und Installation

Für Umbauten herunterfahren, PiSugar ausschalten und Versorgung trennen. Der USB-Hub benötigt die Datenverbindung am Pi-Anschluss **USB**, nicht **PWR IN**. Alle GPIO-Nummern sind BCM.

![Hardware-Stapel](images/hardware-stack-side-2026-10-05.jpg)

## Bestand

| Teil | Details und Beleg | Aufgabe |
|---|---|---|
| Raspberry Pi Zero 2 W | Projektbasis; im montierten Stapel nicht vollständig lesbar; 512 MB RAM laut Hersteller | Audio-Client |
| WM8960 Audio-HAT | Foto zeigt zwei Mikrofone, Taste, Lautsprecheranschlüsse und Kopfhörerbuchse | Audioaufnahme und Wiedergabe |
| Zwei Lautsprecher | Laut Ivan angeschlossen; im aktuellen Übersichtsfoto separat links und rechts dargestellt | Sprachausgabe |
| Integrierte Mikrofone | Zwei Mikrofone am HAT sichtbar; separates Mikrofon für MVP nicht nötig | Spracheingabe |
| HAT-Taste | Aufdruck `BUTTON`; Herstellerbelegung GPIO17, physischer Pin 11 | Aufnahme auslösen |
| Waveshare ETH/USB HUB HAT | Aufdruck auf neuem Foto; 1× RJ45 10/100 Mbit/s, 3× USB-A; **USB-Hub, RTL8152 und 100-Mbit-Link bestätigt**; LAN-SSH und drei Ports noch offen | Kabelnetzwerk und USB-Erweiterung |
| Pimoroni Button SHIM | Aufdruck `BTN SHIM`, fünf Tasten A–E; I²C `0x3f`, RGB-LED | Zusätzliche Bedienung und Statusanzeige |
| PiSugar 3 | Ursprünglich als PiSugar2 notiert; am 08.10.2026 per I²C als PiSugar 3 identifiziert (Akkucontroller `0x57`, Version `0x03`; RTC `0x68`) | Akkubetrieb, Ladestand auf dem PiTFT |
| Li-Ion-Akku | Aufdruck `PiSugar`, Modell `803052`; Kapazitätszeile nicht zuverlässig lesbar | Energiespeicher |
| Kamera | Zuvor angeschlossen, im aktuellen Übersichtsfoto separat abgelegt; Flexplatine trägt `Frank-S01-V1.0`; daraus kein sicherer Sensorname ableitbar | Spätere Bildanfragen |
| 64-GB-microSD | Von Ivan bestätigt; kein separates Foto | Betriebssystem |
| Adafruit mini PiTFT 1,3″ | Aufdruck `Adafruit miniPiTFT 1.3" 240x240`, zwei Taster | Optionale Statusanzeige |

## Schnittstellen und Planung

Alle GPIO-Angaben verwenden BCM-Nummern. Die Tabelle folgt den Herstellerbelegungen. Audio, SHIM und PiTFT wurden im erweiterten Aufbau genutzt; die Kamera ist noch nicht integriert.

| Bauteil | Schnittstelle / GPIOs | Prüfung |
|---|---|---|
| WM8960 Steuerung | I²C: GPIO2/3 | Audio funktioniert; PiSugar 3 teilt den Bus auf `0x57`/`0x68` |
| WM8960 Audio | I²S: GPIO18/19/20/21 | Vorhandenes Kernelmodul/Overlay funktionieren |
| HAT-Taste | GPIO17, physischer Pin 11 | Aktiv Low und normale Zyklen bestätigt; gezielte Prelltests offen |
| ETH/USB HUB HAT | USB-Datenverbindung zum Pi; GPIO-Stapel für Versorgung und Durchführung | Erkennung/r8152/100-Mbit-Link bestätigt; LAN-SSH und externe Ports offen |
| Button SHIM | I²C: GPIO2/3, physische Pins 3/5; Adresse `0x3f`; 5 V, 3,3 V und Masse | Einzeltests bestätigt; aktuelle Dienstabnahme/Adresskonflikte separat prüfen |
| mini PiTFT | SPI: GPIO10/11, CS GPIO8, DC GPIO25; Backlight GPIO22 | SPI0 und `/dev/spidev0.0/.1` bestätigt; ST7789-Farbtest und Boot-/Statusdienst bestanden |
| Displaytaster | GPIO23/24 | Optional zusätzliche Bedienung |
| PiSugar 3 | I²C `0x57` (Akkucontroller), `0x68` (RTC) | Spannung, Ladestand, Netzteil und Ladefreigabe gelesen; Abschaltung offen |
| Kamera | Kameraanschluss/Flexkabel | Sensor und Treiber prüfen |

Audio und Display nutzen nach dieser Belegung unterschiedliche Signalpins. SPI0 wurde zusammen mit der bestehenden WM8960-Konfiguration aktiviert; die endgültige mechanische Montage, Versorgung und weitere Funktionen der konkreten PiSugar-Revision werden gesondert geprüft.

## Strom, Kamera und Montage

PiSugar 3 wurde über I²C `0x57` (Version `0x03`) und RTC `0x68` identifiziert. Das Ladestandregister schwankt mit Ladeimpulsen; die Anzeige mittelt über eine Minute. Ein PiSugar-Dienst ist nicht installiert. Akkukapazität, Laufzeit und sichere Abschaltung sind offen. Kamerasensor (`Frank-S01-V1.0` ist nur ein Platinenaufdruck), Lautsprecherimpedanz/-leistung und endgültige Montage bleiben offen. Das mini PiTFT gehört an das Ende des GPIO-Stapels; Audio und Display nutzen unterschiedliche Signalpins. Offene Abnahmen: [Roadmap](roadmap.md).

## Pi-Erstinstallation

**Diese Anleitung betrifft die Erstinstallation des Pi-Clients und seines lokalen Fallbacks.** Der Normalbetrieb mit CT 107 wird anschließend über [Betrieb](operation.md#konfiguration) und [Serverpfad und Grenzen](architecture.md#servitor-server-ct-107-mit-lokalem-fallback) konfiguriert. Für Updates eines bereits eingerichteten Pi gilt vorrangig [`pi-voice-install`](operation.md#aus-einem-git-commit-einspielen-aktuelle-praxis).

Alle Befehle außer SSH-Verbindung und SD-Vorbereitung laufen auf dem Pi. Ziel: Raspberry Pi Zero 2 W, Raspberry Pi OS Lite **64-bit / Debian 13 Trixie**, Benutzer `obivan`, Hostname `pi-assistent`.

## 1. System vorbereiten

Mit Raspberry Pi Imager das Lite-64-bit-Image auf die SD-Karte schreiben. Benutzer, SSH, 2,4-GHz-WLAN, Land DE und Zeitzone Europe/Berlin setzen. Flashen überschreibt die Karte. Der vorhandene Pi ist bereits installiert; für ihn diesen Schritt überspringen.

Vom Mac verbinden:

```bash
ssh obivan@pi-assistent.local
```

Falls mDNS nicht funktioniert, die **aktuelle** IP aus dem Router verwenden. Frühere IP-Adressen sind keine festen Zugangsdaten.

Auf dem Pi:

```bash
cat /etc/os-release
uname -r
uname -m
sudo apt update
sudo apt full-upgrade
sudo apt install git alsa-utils python3 python3-libgpiod gpiod python3-smbus i2c-tools
sudo reboot
```

Danach erneut anmelden. Bestätigter Teststand: Kernel `6.18.50+rpt-rpi-v8`, `aarch64`, Python 3.13, libgpiod 2.2.1. Diese Werte beschreiben die Abnahme und sind keine Versionsvorgaben für spätere Updates.

## 2. WM8960 mit vorhandenem Overlay

Auf dem getesteten Kernel funktionieren vorhandener Codec-Treiber und `wm8960-soundcard`-Overlay. **Kein zusätzlicher Waveshare-Treiber/DKMS-Installer nötig.** Wenn Audio bereits funktioniert, Konfiguration und Mixer zunächst nur auslesen.

```bash
ls /boot/firmware/overlays/wm8960-soundcard.dtbo
modinfo snd_soc_wm8960
aplay -l
arecord -l
```

Wenn WM8960 noch fehlt, `/boot/firmware/config.txt` sichern und bearbeiten. Unter dem wirksamen `[all]` folgende Einträge ergänzen, sofern nicht vorhanden:

```ini
dtparam=i2c_arm=on
dtparam=i2s=on
dtoverlay=wm8960-soundcard
```

```bash
sudo reboot
```

Nach dem Login müssen `aplay -l` und `arecord -l` die Karte `wm8960soundcard` zeigen. Kartennummern können wechseln; deshalb in Befehlen den Namen verwenden.

## 3. Audio testen

Bei vorhandenem Dienst vor manuellen Audiotests stoppen und anschließend wieder starten. Zunächst Pegel/Routing auslesen; leise beginnen:

```bash
sudo systemctl stop pi-ptt.service  # nur wenn bereits installiert
amixer -c wm8960soundcard scontents
speaker-test -D plughw:CARD=wm8960soundcard,DEV=0 -c 2 -t wav -l 1
arecord -D plughw:CARD=wm8960soundcard,DEV=0 -f S16_LE -r 48000 -c 2 -d 5 /tmp/mikrofontest.wav
aplay -D plughw:CARD=wm8960soundcard,DEV=0 /tmp/mikrofontest.wav
rm /tmp/mikrofontest.wav
sudo systemctl start pi-ptt.service  # nur wenn bereits installiert
```

Der manuelle Hörtest nutzt S16_LE, **48 kHz, Stereo**. Der aktuelle PTT-Pfad streamt **16 kHz Mono S16_LE**; der WAV-Fallback akzeptiert 16/48 kHz PCM16. Ein funktionierendes Mixerprofil nicht wegen einer schlechten Transkription ungeprüft ändern.

Auf dem vorhandenen Pi wurden beide `Output Mixer PCM`-Wege und beide `Input Mixer Boost`-Wege aktiviert, LINPUT1/RINPUT1-Boost auf 3 und `ADC High Pass Filter` auf `on` gesetzt. Capture 39 (+12 dB), ADC PCM 195 (0 dB), Playback 100 % und Speaker AC 5 stammen aus früheren Rückmeldungen; sie sind kein universelles Mixerprofil. ALC/Noise Gate wurden nicht aktiviert. Aufnahme wurde als gut gehört; Clipping/SNR wurden nicht gemessen.

**Letzte Speaker-Ausgabe:** beide Kanäle 102/127, **80 %, −19 dB**, danach `alsactl store` bestätigt. Frühere 95 % / 0 dB sind überholt. C/D ändern den separaten digitalen `Playback`-Regler, nicht `Speaker`.

```bash
amixer -c wm8960soundcard sget Speaker
amixer -c wm8960soundcard sget Playback
sudo alsactl store wm8960soundcard  # erst nach erfolgreicher Hörprüfung
```

Aufnahme und Wiedergabe sind nach Neustart bestätigt. Bei fehlendem Ton siehe [Troubleshooting](operation.md).

## 4. Repository und Dienst installieren

Falls noch kein Checkout vorhanden ist:

```bash
git clone https://github.com/0b-ivan/pi-voice-assistant.git
cd pi-voice-assistant
```

Bestehende Checkouts zuerst mit `git status` prüfen; nicht über ungesicherte lokale Änderungen hinweg aktualisieren. Den geprüften Branch/Commit verwenden. Der Dienstinstaller liefert den Piper-Wrapper mit; Paket und Stimme werden separat installiert.

Vor Installation:

```bash
id obivan
getent group audio gpio i2c
command -v i2cdetect
/usr/bin/python3 -c 'import smbus, gpiod; assert hasattr(gpiod, "request_lines")'
sudo bash scripts/install-voice-service.sh
sudo bash scripts/install-vosk.sh
sudo bash scripts/install-piper.sh
```

Nach der Installation `/etc/pi-ptt.env` und `/etc/pi-voice-assistant.env` prüfen und wie unten beschrieben konfigurieren.

Der Dienstinstaller kopiert PTT-, STT-, LLM-, Button- und TTS-Module und die Unit. Vorhandene Konfigurationen bleiben erhalten. Der Vosk-Installer installiert `vosk==0.3.45` nach `/opt/pi-voice-assistant/vendor` und das kleine deutsche Modell nach `/opt/pi-voice-assistant/models/`; er kann einen laufenden Dienst stoppen und startet ihn bei Erfolg wieder. Bei Installationsfehlern Dienststatus prüfen.

In `/etc/pi-voice-assistant.env`:

```text
STT_PROVIDER=vosk
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor

OPENROUTER_API_KEY=YOUR_OPENROUTER_KEY_HERE
OPENROUTER_LLM_MODEL=openai/gpt-5.4-mini
OPENROUTER_LLM_TIMEOUT_SECONDS=15
OPENROUTER_LLM_MAX_TOKENS=180

TTS_VOICE_PROFILE=servitor
```

Dieses Beispiel konfiguriert den **Pi-Fallback** (Modell `openai/gpt-5.4-mini`). Auf CT 107 verwendet der Server gemäß [`server/install-ct.sh`](../server/install-ct.sh) standardmäßig `mistralai/mistral-medium-3-5` und bei Ausfall Qwen3-4B. Für den Serverpfad müssen auf dem Pi zusätzlich `ASSISTANT_BASE_URL` und `ASSISTANT_TOKEN` gemäß [Betrieb](operation.md#konfiguration) gesetzt sein. Vosk selbst benötigt keinen API-Key. Der Key wird ausschließlich für den anschließenden OpenRouter-LLM-Schritt gebraucht und gehört nur in diese lokale Environment-Datei. Die Vorlage setzt `STT_PROVIDER=vosk` und enthält keinen echten Key. `/etc/pi-voice-assistant.env` hat `root:obivan`, Modus 0640; nicht öffentlich lesbar machen.

In `/etc/pi-ptt.env`: GPIO17 aktiv Low, `PTT_RUNTIME_DIR=/run/pi-ptt`. Unter der mitgelieferten Unit den Runtime-Pfad beibehalten. `PTT_BUTTON_SHIM=0` ist die Voreinstellung; SHIM erst nach [Einzeltest](#i²c--shim-einzeltest) auf 1 setzen. Details zu Dienstrechten: [Betrieb](operation.md).

## 5. Taste prüfen und starten

Bei gestopptem Dienst Konfiguration laden und GPIO17 ohne Aufnahme prüfen:

```bash
sudo systemctl stop pi-ptt.service
set -a
. /etc/pi-ptt.env
set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/ptt.py --probe
```

Alle Tasten zunächst loslassen, dann drücken/loslassen. Erwartet: je ein `button`-Start/Release. Ctrl-C beendet die Probe. Danach den normalen Dienst starten:

```bash
sudo systemctl enable --now pi-ptt.service
systemctl status pi-ptt.service --no-pager
journalctl -u pi-ptt.service -f
```

Taste halten, eine Frage sprechen, loslassen. **Ohne Serverkonfiguration** verläuft der lokale Test über Aufnahme → Vosk → (direkter Intent oder OpenRouter) → Piper → Wiedergabe. **Mit `ASSISTANT_BASE_URL`** erfolgt der normale Durchlauf auf CT 107, und die Pi-Logs zeigen zusätzlich Remote-/Fallback-Events; feste Intents brauchen kein LLM. Für die Abnahme auf `recording`, Transkript/Antwort, `speech_started`, `speech_finished` und den passenden Remote- oder lokalen Verarbeitungsweg achten. Fehler und Latenzen im Journal prüfen; Ctrl-C beendet nur die Loganzeige.

Für den **lokalen TTS-Fallback** [Piper-Paket und Stimme installieren](#lokalen-fallback-einmalig-einrichten). [Button SHIM A–E](user-guide.md) und [PiTFT](user-guide.md) sind bereits integriert; eine vollständige Abnahme der aktuellen Dienstversion ist weiterhin offen. PiSugar-Abschaltung/Laufzeit und Kamera bleiben offen ([Roadmap](roadmap.md)).


## PiTFT installieren

SPI in `/boot/firmware/config.txt` per `dtparam=spi=on` aktivieren. **WM8960/I²S nicht überschreiben.** Danach neu starten.

```bash
sudo bash scripts/install-display.sh
sudo systemctl enable --now pi-display
systemctl status pi-display --no-pager
journalctl -u pi-display -n 30 --no-pager
```

GPIOs (BCM): MOSI 10, SCLK 11, CE0 8, D/C 25, Backlight 22, Tasten 23/24. Das Display liest feste Statuswerte aus `/run/pi-ptt/`; es speichert dort weder Transkripte noch Antworttexte.


## Netzwerk und USB

```bash
sudo apt install usbutils ethtool i2c-tools python3-smbus python3-venv
lsusb
lsusb -t
ip -br link
ip -br addr
```

Bestätigt am 05.10.2026: Terminus-Hub `1a40:0101`, Realtek `0bda:8152`, `eth0`, Treiber `r8152 v1.12.13`, Link **100 Mb/s Full Duplex**. Das ist ausgehandelte Linkgeschwindigkeit, kein gemessener Durchsatz. Frühere LAN-IP `172.22.9.108/24` und WLAN-IP `172.22.9.128/24` sind Momentaufnahmen.

Tatsächlichen Interface-Namen einsetzen:

```bash
ETH_IFACE=eth0
sudo ethtool -i "$ETH_IFACE"
sudo ethtool "$ETH_IFACE"
ip -4 addr show dev "$ETH_IFACE"
ip route show dev "$ETH_IFACE"
```

Fehlt die IP trotz Link, vorhandene Netzwerkverwaltung bestimmen. Bei bereits vorhandenem NetworkManager `nmcli device status` prüfen; nur für dessen verwaltetes Interface gegebenenfalls `sudo nmcli device connect "$ETH_IFACE"`. Dies kann Profile aktivieren und die Route ändern. WLAN für den bestehenden SSH-Zugang beibehalten.

Router über das LAN-Interface testen (echte Gateway-IP aus der Route einsetzen) und vom Mac eine zweite SSH-Verbindung zur **aktuellen LAN-IP** öffnen. Allgemeiner Internetzugang könnte weiterhin über WLAN laufen. Diese beiden LAN-Tests sind noch offen.

Eine bekannte sparsame USB-Tastatur nacheinander an die drei externen USB-A-Buchsen anschließen. Jeweils `lsusb`, `lsusb -t` und Kerneljournal prüfen. Hub-Erkennung bestätigt nicht alle Ports. Alle drei Einzelprüfungen sowie Laufzeit/Unterspannung unter Zusatzlast bleiben offen.

## I²C / SHIM-Einzeltest

Button SHIM: TCA9554A an Bus 1 / `0x3f`, A–E als aktive Low-Eingänge; LED über Expander, keine fünf zusätzlichen Pi-GPIOs. GPIO17 bleibt die WM8960-Taste.

**Vor Test Dienst stoppen; anschließend wieder starten.** Keine zweite LED-/SHIM-Anwendung parallel betreiben.

```bash
sudo systemctl stop pi-ptt.service
sudo modprobe i2c-dev
i2cdetect -l
sudo i2cdetect -y 1 0x3f 0x3f
python3 scripts/test-button-shim.py --seconds 60
```

Nur erwartete SHIM-Adresse prüfen, kein pauschaler Scan über Codec und Akkucontroller. `3f` bedeutet Antwort; `--` keine Antwort; `UU` Kernelbindung, nicht mit Force zugreifen. Bei Rechten zuerst Gruppe/udev prüfen; für die Einzelprobe notfalls `sudo python3 scripts/test-button-shim.py --seconds 60`.

Alle Tasten vor Test loslassen, danach A–E einzeln drücken/loslassen. Erwartet alle fünf PASS, Exitcode 0; 1 bedeutet unvollständig, 2 Bus-/Konfigurationsfehler. Der Test liest Register, initialisiert weder Expander noch LED. Auf dem Pi sind zwei vollständige A–E-Testläufe bestätigt; Langzeit-/Prelltest offen.

## RGB-Einzeltest, optional

Bereits per Nutzer-Sichtprüfung als Rot → Grün → Blau → aus dokumentiert. Für erneuten unabhängigen Test bei weiterhin gestopptem Dienst:

```bash
python3 -m venv --system-site-packages ~/button-shim-test-venv
~/button-shim-test-venv/bin/python -m pip install buttonshim==0.0.2
sudo ~/button-shim-test-venv/bin/python - <<'PYCODE'
import time
import buttonshim
buttonshim.set_brightness(0.2)
try:
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        buttonshim.set_pixel(*rgb)
        time.sleep(2)
finally:
    buttonshim.set_pixel(0, 0, 0)
PYCODE
```

Sichtprüfung erforderlich; erfolgreiche Ausführung allein beweist keine Farben. Diese separate Testumgebung wird vom Sprachdienst nicht gebraucht, dessen Expander-Treiber liegt im Repo.


## SHIM aktivieren

Nach bestandenem SHIM-Einzeltest in `/etc/pi-ptt.env` `PTT_BUTTON_SHIM=1` und `PTT_PITFT_BUTTONS=23,24` setzen. Nach Neustart muss das Journal `shim_ready`, Bus 1 und `0x3f` melden. GPIO17 bleibt auch bei SHIM-Ausfall verfügbar.

## Lokalen Fallback einmalig einrichten

Aus dem geprüften Checkout **auf dem Pi**:

```bash
sudo bash scripts/install-vosk.sh
sudo bash scripts/install-piper.sh
```

Konfiguration auf dem Pi: `/etc/pi-voice-assistant.env`. Der Server-Token und seine URL kommen aus [Betrieb](operation.md#konfiguration).

| Funktion | Einstellung / Ort |
|---|---|
| Offline-STT | `STT_PROVIDER=vosk`; `VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15` |
| Vosk-Python | `VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor` |
| Piper | Interpreter `/opt/pi-voice-assistant/.venv/bin/python`; Modelle unter `tts/` |
| Stimmeffekt | `TTS_VOICE_PROFILE=servitor`, Thorsten Emotional Speaker 4 + DSP |
| Audio | `plughw:CARD=wm8960soundcard,DEV=0` |

Die Aufnahme erfolgt mit **16 kHz Mono S16_LE**. Während PTT wird sie zum Server gestreamt. Ohne CT 107 nutzt der Pi inkrementelles Vosk; WAV-Fallback unterstützt PCM16 bei 16/48 kHz. OpenRouter erhält nur Text, kein Mikrofon-Audio.


## Aktivierungswort

[`scripts/install-wakeword.sh`](../scripts/install-wakeword.sh) lädt die geprüften openWakeWord-Modelle. Als Dienstnutzer aus einem geprüften Checkout ausführen, mit Schreibrecht auf das Modellverzeichnis; danach `PTT_WAKE_WORD=hey_jarvis_v0.1` in `/etc/pi-voice-assistant.env` setzen und `pi-ptt` neu starten. Die vortrainierten Modelle sind CC BY-NC-SA 4.0. Echte Treffer-/Fehlalarmquote noch abnehmen.

## Gedächtnis-Stick einrichten

**Formatieren löscht die gewählte Partition.** Mit `lsblk -o NAME,SIZE,TRAN,MOUNTPOINTS` den richtigen Stick bestimmen, vorhandene Daten sichern und die Partition aushängen. `/dev/sdX1` unten durch die geprüfte Partition ersetzen. Auf dem Pi aus dem geprüften Checkout:

```sh
sudo mkfs.ext4 -L PROXIMUS /dev/sdX1
sudo mkdir -p /mnt/proximus-memory
```

In `/etc/fstab` genau einmal ergänzen:

```text
LABEL=PROXIMUS /mnt/proximus-memory ext4 nofail,noatime,x-systemd.automount,x-systemd.device-timeout=3s 0 2
```

```sh
sudo systemctl daemon-reload
sudo systemctl start "$(systemd-escape -p --suffix=automount /mnt/proximus-memory)"
sudo install -d -o obivan -g obivan -m 0700 /mnt/proximus-memory/proximus
sudo install -D -m 0644 deploy/pi-ptt-memory.conf /etc/systemd/system/pi-ptt.service.d/memory.conf
sudo install -m 0644 deploy/20proximus-update-lists /etc/apt/apt.conf.d/
sudo systemctl daemon-reload
sudo systemctl restart pi-ptt
```

Das Drop-in erlaubt dem Dienst das Schreiben trotz `ProtectSystem=strict`. Vor Entfernen laufende Schreibvorgänge beenden und den Stick sauber aushängen.

## CT 107

Der vorhandene Container verwendet `/opt/servitor-voice/repo` als Checkout, Benutzer `servitor`, venv `/opt/servitor-voice/.venv`, Vosk unter `models/` und Piper unter `tts/`. [`server/install-ct.sh`](../server/install-ct.sh) installiert Unit, Sprechererkennung und Env, setzt aber diese Basis samt Piper/Vosk voraus; es ist kein vollständiger Container-Bootstrap. Einmalig root aus dem geprüften Checkout `sh server/install-ct.sh` ausführen. Für Qwen3-4B [`server/install-llm.sh`](../server/install-llm.sh) verwenden. Zugangsdaten und Client-Verbindung: [Betriebsleitfaden](operation.md#konfiguration).

## Quellen

Die acht Projektfotos stammen von Ivan und sind hier abgelegt. Die neuen Dateien sind verkleinerte JPEG-Kopien ohne EXIF-Metadaten; die Aufnahmen wurden nicht inhaltlich verändert.

- [Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
- [WM8960 Wiki und Pinbelegung](https://www.waveshare.com/wiki/WM8960_Audio_HAT)
- [Adafruit mini PiTFT 1,3″, Produkt 4484](https://www.adafruit.com/product/4484)
- [Adafruit Pinbelegung](https://learn.adafruit.com/adafruit-mini-pitft-135x240-color-tft-add-on-for-raspberry-pi/pinouts)

- [Waveshare ETH/USB HUB HAT](https://www.waveshare.com/product/raspberry-pi/hats/interface-power/eth-usb-hub-hat.htm)
- [ETH/USB HUB HAT Wiki](https://www.waveshare.com/wiki/ETH/USB_HUB_HAT)
- [Pimoroni Button SHIM](https://shop.pimoroni.com/products/button-shim)
- [Pimoroni Button-SHIM Python-Bibliothek](https://github.com/pimoroni/button-shim)
