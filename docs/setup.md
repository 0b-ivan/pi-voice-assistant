# Setup: Pi, Audio und Offline-Sprachdienst

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

Danach erneut anmelden. Bestätigter Teststand: Kernel `6.18.50+rpt-rpi-v8`, `aarch64`, Python 3.13, libgpiod 2.2.1. Diese Werte beschreiben die Abnahme und sind keine Versionsvorgaben für spätere Updates. [OS-Entscheidung](decisions/0002-operating-system.md), [historisches Installationsprotokoll mit Screenshots](history/setup-2026-10-05.md).

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
```

Bestätigtes Aufnahmeformat: S16_LE, **48 kHz, Stereo**. Vosk konvertiert intern; das funktionierende Aufnahmeformat nicht für STT ändern.

Auf dem vorhandenen Pi wurden beide `Output Mixer PCM`-Wege und beide `Input Mixer Boost`-Wege aktiviert, LINPUT1/RINPUT1-Boost auf 3 und `ADC High Pass Filter` auf `on` gesetzt. Capture 39 (+12 dB), ADC PCM 195 (0 dB), Playback 100 % und Speaker AC 5 stammen aus früheren Rückmeldungen; sie sind kein universelles Mixerprofil. ALC/Noise Gate wurden nicht aktiviert. Aufnahme wurde als gut gehört; Clipping/SNR wurden nicht gemessen.

**Letzte Speaker-Ausgabe:** beide Kanäle 102/127, **80 %, −19 dB**, danach `alsactl store` bestätigt. Frühere 95 % / 0 dB sind überholt. C/D ändern den separaten digitalen `Playback`-Regler, nicht `Speaker`.

```bash
amixer -c wm8960soundcard sget Speaker
amixer -c wm8960soundcard sget Playback
sudo alsactl store wm8960soundcard  # erst nach erfolgreicher Hörprüfung
```

Aufnahme und Wiedergabe sind nach Neustart bestätigt. Bei fehlendem Ton siehe [Troubleshooting](troubleshooting.md#audio).

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
```

Nach der Installation `/etc/pi-ptt.env` und `/etc/pi-voice-assistant.env` prüfen und wie unten beschrieben konfigurieren.

Der Dienstinstaller kopiert PTT-, STT-, Button- und TTS-Module und die Unit. Vorhandene Konfigurationen bleiben erhalten. Der Vosk-Installer installiert `vosk==0.3.45` nach `/opt/pi-voice-assistant/vendor` und das kleine deutsche Modell nach `/opt/pi-voice-assistant/models/`; er kann einen laufenden Dienst stoppen und startet ihn bei Erfolg wieder. Bei Installationsfehlern Dienststatus prüfen.

In `/etc/pi-voice-assistant.env`:

```text
STT_PROVIDER=vosk
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor
```

Für `vosk` ist kein API-Key erforderlich; den Platzhalter-Key entfernen oder leer lassen. Die Vorlage bleibt aus Kompatibilitätsgründen `openrouter`. `/etc/pi-voice-assistant.env` hat `root:obivan`, Modus 0640; nicht öffentlich lesbar machen.

In `/etc/pi-ptt.env`: GPIO17 aktiv Low, `PTT_RUNTIME_DIR=/run/pi-ptt`. Unter der mitgelieferten Unit den Runtime-Pfad beibehalten. `PTT_BUTTON_SHIM=0` ist die Voreinstellung; SHIM erst nach [Einzeltest](hardware-bring-up.md) auf 1 setzen. Details zu Dienstrechten: [Betrieb](operation.md).

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

Taste halten, einen Satz sprechen, loslassen: `recording` → `capture_ready` → `processing` → `transcript` mit `provider=vosk`. Erste Transkription ist wegen Modell-Laden langsamer. Ctrl-C beendet nur die Loganzeige. Dies ergibt Text, noch keine automatische KI-Antwort.

Optional: [Button SHIM A–E](button-controls.md), [Piper-Paket und Stimme installieren](text-to-speech.md). PiSugar-Abschaltung, PiTFT und Kamera folgen nach der Audio-/Performance-Abnahme.
