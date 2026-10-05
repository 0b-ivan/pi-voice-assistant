# Betriebssystem und erste Inbetriebnahme 🐧

## Basis und Entscheidungsstand

Festgelegt am 05.10.2026: **Raspberry Pi OS Lite (64-bit), Debian 13 / Trixie**, ohne Desktop. Die Begründung steht in [ADR 0002](decisions/0002-operating-system.md).

Betrieb: Headless über SSH, systemd für den Client, Python mit virtueller Umgebung, ALSA für WM8960 und später rpicam/libcamera für die Kamera.

Vor dem Flashen konkrete Image-Version, Architektur und Download-Prüfsumme dokumentieren. Boot und SSH sind bestätigt; die Kompatibilität der Hardwaretreiber bleibt zu prüfen.

Quelle: [Raspberry Pi OS Downloads](https://www.raspberrypi.com/software/operating-systems/).

## Editor

Für alle Konfigurationsänderungen verwenden wir **Vim**. Falls noch nicht installiert: `sudo apt install vim`. Datei mit `sudo vim <Pfad>` öffnen, mit `i` bearbeiten und mit `Esc`, `:wq`, Enter speichern und schließen.

## 1. SD-Karte vorbereiten

Mit Raspberry Pi Imager das vereinbarte Lite-Image auf die 64-GB-Karte schreiben. Das überschreibt die Karte; vorher benötigte Daten sichern. Hostname `pi-assistent`, Benutzer `obivan`, SSH (Passwortanmeldung bei der Erstinbetriebnahme bestätigt), 2,4-GHz-WLAN mit Land `DE` und Zeitzone `Europe/Berlin` konfigurieren. Der Zero 2 W benötigt ein 2,4-GHz-WLAN.

## Installationsprotokoll — 05.10.2026 📸

Raspberry Pi Imager **v2.0.11.1** auf macOS. Der Abschlussbildschirm bestätigt **Raspberry Pi Zero 2 W**, **Raspberry Pi OS Lite (64-bit)** sowie angewendete Anpassungen für Hostname, Lokalisierung, Benutzerkonto, WLAN und aktiviertes SSH. Das Image wird im Imager als **Debian Trixie**, veröffentlicht **2026-09-15**, angezeigt. Die Image-Prüfsumme wurde noch nicht erfasst; der laufende Kernel ist im folgenden Bootprotokoll dokumentiert.

Tatsächlicher Hostname: **`pi-assistent`** (ersetzt die ursprüngliche Planung `wgz-voice-01`). WLAN wurde eingerichtet. Die spätere SSH-Anmeldung bestätigt Benutzer `obivan` und Passwortauthentifizierung. WLAN-Land `DE` und Zeitzone `Europe/Berlin` bleiben zu überprüfende Planwerte.

**Bestätigt:** Schreibvorgang abgeschlossen und Karte automatisch ausgeworfen. Der Pi ist gestartet, im Netzwerk erreichbar und die SSH-Anmeldung funktioniert. **Noch offen:** Hardwaretests.

### Modell auswählen

![Raspberry Pi Zero 2 W im Imager ausgewählt](images/setup/03-image.png)

### Betriebssystem auswählen

![Raspberry Pi OS Lite 64-bit, Debian Trixie, Image vom 15.09.2026](images/setup/05-image.png)

### Hostname konfigurieren

![Tatsächlicher Hostname pi-assistent](images/setup/06-image.png)

### WLAN konfigurieren

![WLAN im Imager eingerichtet; Passwort ist verborgen](images/setup/07-image.png)

### SD-Karte schreiben

![Beginn des Schreibvorgangs](images/setup/08-image.png)

![Schreibvorgang bei 62 Prozent](images/setup/01-image.png)

### Erfolgreicher Abschluss

![Schreibvorgang abgeschlossen, Anpassungen angewendet, SSH aktiviert](images/setup/02-image.png)

Alle acht Originalscreenshots liegen unter `docs/images/setup/`. [Screenshot 04](images/setup/04-image.png) ist identisch mit Screenshot 03 und wird deshalb nur einmal eingebettet.

## Boot und erste SSH-Anmeldung — 05.10.2026 ✅

| Beobachtung | Bestätigter Wert |
|---|---|
| Hostname | `pi-assistent` |
| IPv4 beim ersten Start | `172.22.9.128` (kann sich ohne Reservierung ändern) |
| Benutzer | `obivan` |
| SSH | Anmeldung mit Passwort erfolgreich |
| Architektur | `aarch64` |
| Kernel | `6.18.50+rpt-rpi-v8` |
| Kernelpaket laut Loginbanner | `Debian 1:6.18.50-1+rpt1 (2026-09-11)` |

![Netzwerkscan zeigt pi-assistent unter 172.22.9.128](images/setup/first-boot-01.png)

![Erfolgreiche SSH-Anmeldung als obivan, Kernelbanner und Shellprompt](images/setup/first-boot-02.png)

Der Login belegt einen erfolgreichen Boot und SSH-Zugriff über die IP-Adresse. Erneute SSH-Anmeldung nach dem Update ist inzwischen bestätigt (siehe Audio-Bestandsaufnahme). mDNS (`pi-assistent.local`) wurde noch nicht bestätigt.

## 2. Erster Start

Per SSH mit `ssh obivan@pi-assistent.local` verbinden (alternativ die IP-Adresse verwenden) und Modell/OS erfassen:

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

Bei `[sudo] password for obivan:` das Benutzerpasswort eingeben; dabei werden keine Zeichen angezeigt. Bei `Continue? [Y/n]` bestätigt Enter die Vorgabe Ja. Wenn Paket-Hinweise mit `(press q to quit)` erscheinen, mit **q** den Betrachter schließen und die Aktualisierung fortsetzen lassen.

Nach `sudo reboot` wird die SSH-Verbindung getrennt. Danach erneut verbinden:

```bash
ssh obivan@pi-assistent.local  # alternativ die aktuell ermittelte IP-Adresse verwenden
```

Nach erfolgreicher Anmeldung den tatsächlich gestarteten Kernel und die Audioerkennung erfassen:

```bash
uname -r
aplay -l
arecord -l
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS
```

### Updateprotokoll — 05.10.2026

Die sieben eingereichten Screenshots wurden geprüft. Drei Bilder belegen die relevanten Schritte; Downloadfortschritt und überlappende Zwischenstände wurden weggelassen.

| Schritt | Nachweis / Status |
|---|---|
| Paketquellen aktualisieren | Debian Trixie, Updates, Security und Raspberry-Pi-Archiv abgerufen; anschließendes Upgrade zeigt 14 aktualisierbare Pakete |
| `apt full-upgrade` | 14 Aktualisierungen angekündigt, keine Neuinstallationen oder Entfernungen; spätere Ausgabe zeigt Paketkonfiguration und abgeschlossene Trigger vor Beginn der Werkzeuginstallation |
| Paket-Hinweise | rsync-Hinweise im Betrachter; mit **q** verlassen |
| `alsa-utils` | Bereits aktuell: `1.2.14-1+rpt1` |
| Git | Installation abgeschlossen: `1:2.47.3-0+deb13u1` |
| I²C-Werkzeuge | Installation abgeschlossen: `i2c-tools 4.4-2` |
| Neustart | Verbindung nach `sudo reboot` geschlossen; anschließender Login und Diagnosebefehle bestätigt |
| Erneute SSH-Anmeldung | Erfolgreich; Loginbanner und Shellprompt im folgenden Screenshot |
| Audioerkennung | Nur HDMI-Wiedergabe; kein Aufnahmegerät; WM8960 noch nicht erkannt |

![Paket-Hinweise während des Updates; q schließt den Betrachter](images/setup/update-package-notes.png)

![Upgrade abgeschlossen und Werkzeuginstallation wartet auf Bestätigung](images/setup/update-complete-install-confirmation.png)

![Git und i2c-tools eingerichtet; SSH getrennt und erneute Verbindung gestartet](images/setup/tools-installed-reconnect.png)

Die Updateausgabe referenziert Kerneldateien für `6.18.50+rpt-rpi-v8`. Der nachfolgende Screenshot bestätigt diesen Kernel mit `uname -r`. APT zeigt **27,1 GB verfügbaren Platz** an. Dieser Wert ist die freie Kapazität des Dateisystems und bestätigt nicht die Größe der eingelegten SD-Karte; die geplanten 64 GB werden mit `lsblk` geprüft.

### Audio-Bestandsaufnahme nach Neustart — 05.10.2026

![SSH-Anmeldung nach Neustart, Kernel und ALSA-Gerätelisten](images/setup/audio-baseline.png)

| Diagnose | Ergebnis |
|---|---|
| `uname -r` | `6.18.50+rpt-rpi-v8` |
| `aplay -l` | Nur Karte 0 `vc4hdmi`, Gerät 0 `MAI PCM i2s-hifi-0` |
| `arecord -l` | Leere Liste der Aufnahmegeräte |
| WM8960 | Noch nicht als ALSA-Karte erkannt |
| Audiofunktion | Noch nicht getestet |

Die leere Aufnahmegeräteliste belegt keine defekten Mikrofone. Zunächst müssen Treiber und Device-Tree-Konfiguration geprüft werden. Der nächste Schritt ist die WM8960-Einrichtung für den bestätigten Kernel.

## 3. Audio zuerst

### Vorhandenes Kernel-Overlay aktivieren

Am 05.10.2026 wurden `/boot/firmware/overlays/wm8960-soundcard.dtbo` und der im Kernel enthaltene Codec-Treiber `snd_soc_wm8960` für `6.18.50+rpt-rpi-v8` nachgewiesen. Die lokale Overlay-Dokumentation beschreibt ausdrücklich das Waveshare-HAT mit 12,288 MHz MCLK. Es war keine zusätzliche Waveshare-Treiberinstallation erforderlich.

Vorher waren I²C/I²S auskommentiert, kein WM8960-Overlay gesetzt und nur HDMI als ALSA-Wiedergabegerät sichtbar.

Konfiguration sichern und bearbeiten:

```bash
sudo cp /boot/firmware/config.txt /boot/firmware/config.txt.before-wm8960
sudo vim /boot/firmware/config.txt
```

In Vim mit **i** in den Einfügemodus wechseln. Unter dem bestehenden `[all]` am Dateiende ergänzen:

```ini
# Waveshare WM8960 Audio HAT
dtparam=i2c_arm=on
dtparam=i2s=on
dtoverlay=wm8960-soundcard
```

Mit **Esc**, **`:wq`**, **Enter** speichern und schließen. Dann `sudo reboot` ausführen und erneut per SSH anmelden. Anschließend:

```bash
aplay -l
arecord -l
```

### Ergebnis nach Neustart — 05.10.2026 ✅

![WM8960 als Wiedergabe- und Aufnahmegerät nach Neustart erkannt](images/setup/wm8960-detected.png)

Beide Listen zeigen Karte 0 **`wm8960soundcard`**, Gerät 0, mit `bcm2835-i2s-wm8960-hifi`. HDMI ist zusätzlich als Karte 1 verfügbar. Damit ist die Geräteerkennung bestätigt; hörbare Wiedergabe und verständliche Mikrofonaufnahme sind noch nicht geprüft.

Zunächst Mixerzustand erfassen, bevor Pegel oder Signalwege verändert werden:

```bash
amixer -c wm8960soundcard scontents
```

Erkannten Kartenbezeichner in `AUDIO_CARD` einsetzen. Leise beginnen:

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
| WM8960-Treiber / Overlay | Kernelmodul und vorhandenes Overlay verwendet; ALSA-Erkennung nach Neustart bestätigt |
| Beide Lautsprecher hörbar | Offen |
| Mikrofonaufnahme verständlich | Offen |
| Taste zuverlässig erkannt | Offen |
| Netzwerkerreichbarkeit und SSH nach Neustart | Bestätigt; konkrete WLAN-Schnittstelle noch nicht erfasst |
| Akku-/Abschaltverhalten geprüft | Offen |

SD-Karte, erster Boot und SSH-Anmeldung am 05.10.2026 bestätigt. Systemupdate und Werkzeuginstallation sind bestätigt. Erneute SSH-Anmeldung und laufender Kernel sind bestätigt. WM8960 wird nach Overlay-Aktivierung als Aufnahme- und Wiedergabegerät erkannt. Lautsprecher-, Mikrofon- und weitere Hardwaretests stehen aus.
