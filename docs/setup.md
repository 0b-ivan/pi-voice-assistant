# Betriebssystem und erste Inbetriebnahme 🐧

## Basis und Entscheidungsstand

Raspberry Pi OS Lite ohne Desktop ist die vorgeschlagene Basis. Die genaue zuvor besprochene Version war beim Schreiben nicht verfügbar; **Release und 32/64-Bit-Auswahl sind daher noch offen**. Der Zero 2 W unterstützt 64 Bit, aber die Entscheidung muss insbesondere mit dem WM8960-Treiber und dem knappen RAM abgeglichen werden.

Vor dem Flashen konkrete Image-Version, Architektur und Download-Prüfsumme dokumentieren. Es gibt noch keinen nachgewiesen funktionierenden OS-/Kernel-/Treiberstand für dieses Gerät.

Quelle: [Raspberry Pi OS Downloads](https://www.raspberrypi.com/software/operating-systems/).

## 1. SD-Karte vorbereiten

Mit Raspberry Pi Imager das vereinbarte Lite-Image auf die 64-GB-Karte schreiben. Das überschreibt die Karte; vorher benötigte Daten sichern. Hostname `pi-voice`, eigenen Benutzer, SSH mit Schlüssel, WLAN und Zeitzone `Europe/Berlin` konfigurieren. Der Zero 2 W benötigt ein 2,4-GHz-WLAN.

## 2. Erster Start

Per SSH verbinden und Modell/OS erfassen:

```bash
cat /proc/device-tree/model
cat /etc/os-release
uname -a
free -h
lsblk
```

Betriebssystem aktualisieren und Diagnosewerkzeuge installieren:

```bash
sudo apt update
sudo apt full-upgrade
sudo apt install git alsa-utils i2c-tools
sudo reboot
```

Nach Updates den tatsächlich gestarteten Kernel notieren.

## 3. Audio zuerst

Den [Herstellerleitfaden](https://www.waveshare.com/wiki/WM8960_Audio_HAT) und den [Treiber](https://github.com/waveshareteam/WM8960-Audio-HAT) für den ausgewählten Kernel prüfen. Installationsskript vor Ausführung lesen und verwendeten Commit festhalten. Alte Wiki-Beispiele sind keine Kompatibilitätszusage für aktuelle Kernel. Keine automatische Treiberinstallation in diesem Repository.

Nach Installation und Neustart:

```bash
aplay -l
arecord -l
alsamixer
```

Erkannte Karte in den folgenden Beispielen einsetzen; `<KARTE>` ist ein Platzhalter. Leise beginnen:

```bash
AUDIO_CARD=wm8960soundcard # Durch den erkannten Kartenbezeichner ersetzen
speaker-test -D "plughw:CARD=${AUDIO_CARD},DEV=0" -c 2 -t wav -l 1
arecord -D "plughw:CARD=${AUDIO_CARD},DEV=0" -f S16_LE -r 16000 -c 2 -d 5 /tmp/pi-voice-test.wav
aplay -D "plughw:CARD=${AUDIO_CARD},DEV=0" /tmp/pi-voice-test.wav
rm /tmp/pi-voice-test.wav
```

Das Aufnahmeformat ist ein Testvorschlag. Bei Formatfehlern unterstützte Hardwareparameter ermitteln und anpassen. Aufnahmepegel und Mikrofonrouting in `alsamixer` prüfen.

## 4. Weitere Komponenten

Erst nach erfolgreichem Audio-Test Taste und Entprellung testen. Danach PiSugar2-Variante bestimmen, Herstelleranleitung anwenden und kontrolliertes Herunterfahren prüfen. Kamera und Display folgen später.

## Abnahmeprotokoll

| Prüfung | Ergebnis |
|---|---|
| Modell, Image, Architektur, Kernel dokumentiert | Offen |
| Treibercommit dokumentiert; keine Installationsfehler | Offen |
| Beide Lautsprecher hörbar | Offen |
| Mikrofonaufnahme verständlich | Offen |
| Taste zuverlässig erkannt | Offen |
| WLAN/SSH nach Neustart verfügbar | Offen |
| Akku-/Abschaltverhalten geprüft | Offen |

Keine dieser Prüfungen wurde bisher auf der Hardware ausgeführt.
