# Ethernet, USB und Button SHIM testen

Pi Zero 2 W / Trixie, Benutzer `obivan`. [Hardware/Fotos](hardware.md), [Dienstintegration](button-controls.md). Für Umbauten herunterfahren, PiSugar ausschalten und Versorgung trennen. Der Hub braucht USB-Daten am Anschluss **USB**, nicht **PWR IN**.

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

## Boot und Dienst

Fehlt `/dev/i2c-1` nach Boot, in `/etc/modules-load.d/pi-voice-i2c.conf` bestehende Einträge erhalten und `i2c-dev` ergänzen. Vorhandenes WM8960-Overlay/I²C-Konfiguration beibehalten. Das Modul ermöglicht Userspace-Zugriff und ersetzt keinen Audiotreiber.

```bash
sudo systemctl start pi-ptt.service
aplay -l
arecord -l
systemctl status pi-ptt.service --no-pager
```

Für integrierte Steuerung `PTT_BUTTON_SHIM=1` wie unter [Button-Bedienung](button-controls.md) aktivieren. Nach aktuellem Deployment bei Reboot I²C, SHIM, GPIO17, Audio und Dienst nochmals prüfen.

## Belege und offene Abnahme

| Bereich | Beleg / Grenze |
|---|---|
| Hub / Ethernet / Link | Linux-Ausgaben und ethtool wie oben bestätigt |
| I²C | Bus 1/2 nach modprobe, Gerät 0x3f; Dienst später shim_ready |
| A–E-Einzeltest | Zwei vollständige PASS-Durchläufe im bisherigen Protokoll |
| Hersteller-LED-Test | Nutzer-Sichtprüfung „geht gut“ im bisherigen Protokoll; keine Dienstfarben-Abnahme |
| Früherer Neustart | Frühere zusammenfassende Nutzerbestätigung „geht gut“ im Protokoll; keine erneuten Einzelwerte und kein Nachweis aller physischen Tasten-/Audiotests |
| Aktuelle Dienstversion | SHIM-Initialisierung und Release→Vosk bestätigt; aktuelle Reboot-/Abbruch-/LED-Abnahme offen |
| Weiter offen | Router/SSH über LAN, alle drei USB-Buchsen, Akkulaufzeit/Unterspannung |

PR #11 hat trotz Merge einen offenen Review zur damals widersprüchlichen Beschreibung der Abnahme. Einzeltest-/Nutzerbestätigung und neue Dienstabnahme werden hier getrennt; daraus wird keine Vollabnahme des aktuellen Stacks abgeleitet. [Projektprüfung](project-review.md).

Quellen: [Waveshare Anschlussanleitung](https://www.waveshare.com/wiki/ETH/USB_HUB_HAT), [Pimoroni Bibliothek/Registerbelegung](https://github.com/pimoroni/button-shim/blob/master/library/buttonshim/__init__.py).
