# Ethernet, USB und Button SHIM in Betrieb nehmen 🔧

Stand: 05.10.2026. Zielgerät: Pi Zero 2 W, Raspberry Pi OS Lite 64-bit / Trixie, Benutzer **obivan**, Hostname **pi-assistent**.

Der erweiterte Aufbau ist [mit drei neuen Fotos dokumentiert](hardware.md). Ethernet-HAT und Button SHIM sind sichtbar montiert. USB-Hub und Ethernet sind erkannt, eth0 hat eine LAN-IP, und an I²C-Adresse 0x3f antwortet ein Gerät. Tasten A–E sind in zwei Testläufen bestätigt. Die RGB-LED und die angeleitete Neustartprüfung sind durch den Nutzer bestätigt; einzelne Diagnosewerte nach Neustart liegen noch nicht als Ausgabe vor. Alle folgenden Befehle werden auf dem Pi ausgeführt. Testergebnisse erst nach Rückmeldung in die Abnahmetabelle eintragen.

## 1. Bestandsaufnahme

LAN-Kabel zum Router/Switch anschließen. WLAN für die bestehende SSH-Verbindung zunächst beibehalten. Der Waveshare-Hub benötigt eine USB-Datenverbindung zum Datenanschluss des Pi, beschriftet **USB**; **PWR IN** ist kein Datenanschluss. Eine USB-Brücke ist im Seitenfoto sichtbar. Wenn Umbau nötig ist: herunterfahren, PiSugar ausschalten und Stromversorgung trennen, bevor Platinen oder GPIO-Verbindungen verändert werden.

~~~bash
lsusb
lsusb -t
ip -br link
ip -br addr
i2cdetect -l
aplay -l
arecord -l
~~~

Fehlende Diagnoseprogramme installieren:

~~~bash
sudo apt update
sudo apt install usbutils ethtool i2c-tools python3-smbus python3-venv
~~~

Erwartung: USB-Hub und Realtek-Ethernet-Gerät in lsusb, zusätzliche Netzwerkschnittstelle in ip, I²C-Bus 1 und weiterhin wm8960soundcard als Aufnahme-/Wiedergabegerät. Der Interface-Name kann eth0 oder enx… lauten; tatsächlichen Namen verwenden.

## 2. Ethernet prüfen

Das HAT bietet laut Hersteller RTL8152B und 10/100-Mbit/s-Ethernet. Zuerst den vorhandenen Linux-Treiber prüfen; keinen externen Treiber und kein zusätzliches Audio-Overlay installieren.

~~~bash
nmcli device status
nmcli connection show
ip route
sudo journalctl -k -b --no-pager | grep -Ei 'usb|r8152|rtl8152|under.voltage'
~~~

Wenn NetworkManager nicht vorhanden ist, dessen Installation nicht automatisch nachholen; zuerst die vorhandene Netzwerkverwaltung bestimmen.

Im folgenden Beispiel eth0 durch den tatsächlich erkannten Interface-Namen ersetzen:

~~~bash
ETH_IFACE=eth0
sudo ethtool -i "$ETH_IFACE"
sudo ethtool "$ETH_IFACE"
ip -4 addr show dev "$ETH_IFACE"
ip route show dev "$ETH_IFACE"
~~~

Abnahme: Treiber erkannt (üblicherweise r8152), Link detected: yes, ausgehandelte Geschwindigkeit und IPv4-Adresse dokumentieren. 10 Mbit/s ist möglich; 100 Mbit/s ist bei passender Gegenstelle zu erwarten.

Ist eine Ethernet-Schnittstelle erkannt, aber trotz LAN-Kabel ohne IPv4-Adresse, zunächst den NetworkManager-Zustand prüfen. Nur bei vorhandenem NetworkManager und verwaltetem Interface:

~~~bash
sudo nmcli device connect "$ETH_IFACE"
~~~

Das kann ein Verbindungsprofil aktivieren oder erstellen und die Standardroute ändern. WLAN bleibt eingeschaltet. Keine bestehenden Profile löschen.

Die Routeradresse aus der LAN-Route einsetzen und explizit über LAN testen:

~~~bash
LAN_GATEWAY=192.168.1.1  # tatsächliche Adresse aus ip route verwenden
ping -I "$ETH_IFACE" -c 3 "$LAN_GATEWAY"
~~~

Vom Mac eine zweite SSH-Verbindung zur ermittelten LAN-IP öffnen:

~~~bash
ssh obivan@<LAN-IP>
~~~

Die LAN-IP kann von der bisherigen WLAN-IP 172.22.9.128 abweichen. Erst der zweite Login bestätigt SSH über LAN. Ein allgemeiner Internettest ohne Interface-Bindung könnte weiterhin über WLAN laufen.

## 3. Drei USB-Anschlüsse prüfen

Eine bekannte USB-Tastatur nacheinander in jede der drei USB-A-Buchsen stecken und jeweils die Erkennung prüfen:

~~~bash
lsusb
lsusb -t
sudo journalctl -k -b -n 40 --no-pager
~~~

Für jeden Port dokumentieren, dass das zusätzliche Gerät erscheint und nach dem Abziehen wieder verschwindet. Die Hub-/Ethernet-Erkennung allein bestätigt nicht alle externen USB-Buchsen. Für den ersten Test ein Gerät mit geringem Strombedarf verwenden. Zusatzlast, Akkulaufzeit und mögliche Unterspannungsmeldungen später unter realem Betrieb prüfen.

## 4. Button SHIM gezielt erkennen

Button SHIM verwendet TCA9554A an 0x3f, I²C GPIO2/3 und Versorgung. Tasten A–E sind aktive Low-Eingänge am Expander. Sie entsprechen keinen fünf BCM-GPIO-Nummern. Der WM8960-Taster bleibt auf GPIO17.

Nur die bekannte Adresse prüfen, kein pauschaler Scan über Audio-Codec und Akkucontroller:

~~~bash
sudo i2cdetect -y 1 0x3f 0x3f
~~~

- **3f:** Gerät antwortet; Identität und Tastenfunktion anschließend prüfen.
- **--:** Kontakt, Ausrichtung, Versorgung oder Bus prüfen.
- **UU:** Adresse bereits durch Kernel-Treiber belegt. Nicht mit Force-Optionen zugreifen; zuerst die Treiberbindung ermitteln.

Der WM8960-Treiber oder das Audio-Overlay wird für diesen Test nicht entfernt. Ein antwortendes Gerät schließt einen Adresskonflikt nicht allein aus.

## 5. Tasten A–E testen

Das Diagnoseprogramm liest ausschließlich Register des Expanders; es ändert weder dessen Konfiguration noch die LED. Es prüft alle fünf vollständigen Drücken-/Loslassen-Zyklen, entprellt für 30 ms und endet spätestens nach 60 Sekunden.

Im Repository-Checkout (Testskript seit PR #10 auf main):

~~~bash
python3 scripts/test-button-shim.py --seconds 60
~~~

Bei fehlenden Zugriffsrechten für den ersten Test:

~~~bash
sudo python3 scripts/test-button-shim.py --seconds 60
~~~

Alle Tasten vor dem Start loslassen, dann A, B, C, D und E einzeln drücken und loslassen. Erwartung:

~~~text
A: GEDRÜCKT
A: LOSGELASSEN
...
A: PASS
B: PASS
C: PASS
D: PASS
E: PASS
~~~

Exitcode 0 bedeutet fünf erkannte Zyklen; 1 bedeutet unvollständigen Test; 2 bedeutet Bus-/Konfigurationsfehler oder fehlende Abhängigkeit. Nach einem I²C-Fehler zuerst Ursache prüfen. Kein zweites Programm gleichzeitig auf denselben Button-SHIM-Expander zugreifen lassen.

## 6. RGB-LED mit Herstellerbibliothek testen

Die APA102-LED hängt am I²C-Expander, nicht an den SPI-Pins des Pi. Dafür verwenden wir Pimoronis bestehende Bibliothek. Der Test läuft in einer separaten virtuellen Umgebung und verändert die Sprachdienst-Installation nicht.

~~~bash
python3 -m venv --system-site-packages ~/button-shim-test-venv
~/button-shim-test-venv/bin/python -m pip install buttonshim==0.0.2
~~~

--system-site-packages macht das über APT installierte python3-smbus in dieser Testumgebung verfügbar. Kompatibilität von Bibliothek 0.0.2 mit Python/Trixie muss am Pi geprüft werden.

~~~bash
sudo ~/button-shim-test-venv/bin/python - <<'PY'
import time
import buttonshim

buttonshim.set_brightness(0.2)
try:
    for name, rgb in [
        ("Rot", (255, 0, 0)),
        ("Grün", (0, 255, 0)),
        ("Blau", (0, 0, 255)),
    ]:
        print(name, flush=True)
        buttonshim.set_pixel(*rgb)
        time.sleep(2)
finally:
    buttonshim.set_pixel(0, 0, 0)
    time.sleep(0.2)
PY
~~~

Abnahme durch Sichtprüfung: Rot → Grün → Blau, danach aus. Eine erfolgreiche Python-Ausführung allein bestätigt weder die Farben noch die LED-Funktion.

## 7. I²C beim Start verfügbar machen

Nach erfolgreichem Tasten- und LED-Test i2c-dev über systemd beim Boot laden. Der Nutzer hat die anschließende Neustartprüfung mit „geht gut“ bestätigt. Der konkrete Dateiinhalt wurde nicht separat zurückgemeldet.

~~~bash
sudo vim /etc/modules-load.d/pi-voice-i2c.conf
~~~

In der Datei folgende Modulzeile eintragen (bei vorhandener Datei bestehende Einträge beachten):

~~~text
i2c-dev
~~~

Mit Esc, :wq, Enter speichern. Das vorhandene WM8960-Overlay bleibt bestehen. Die Datei sorgt für die Userspace-Geräteschnittstelle; die bereits aktivierte I²C-Hardware-Konfiguration bleibt weiterhin nötig.

## 8. Gemeinsamer Betrieb und Neustart

Nach Hub-/Button-Test weiterhin Audio-Karte und bestehenden Dienst prüfen:

~~~bash
aplay -l
arecord -l
systemctl status pi-ptt.service --no-pager
sudo journalctl -u pi-ptt.service -n 20 --no-pager
~~~

Die WM8960-Taste einmal wie bisher zur Aufnahme verwenden und das Ergebnis prüfen. Für einen Lautsprechertest den bekannten Audio-Test verwenden. Danach kontrolliert neu starten und Ethernet-Erkennung, LAN-IP, SSH und Tasten nochmals prüfen:

~~~bash
sudo reboot
~~~

Bis zur Abnahme entsteht kein neuer Autostartdienst. Die Belegung A=PTT, B=Abbruch, C=leiser, D=lauter, E=Status ist ein Vorschlag; Tasten und LED sind noch nicht in den Sprachdienst integriert.

## Rückmeldung vom Pi — 05.10.2026

Linux erkennt den USB-Hub als **1a40:0101 Terminus Technology Inc. Hub** und Ethernet als **0bda:8152 Realtek RTL8152 Fast Ethernet Adapter**. Zunächst meldete eth0 NO-CARRIER und hatte keine Adresse. Die anschließende Ausgabe bestätigt eth0 UP mit IPv4 **172.22.9.108/24**. WLAN bleibt UP unter **172.22.9.128/24**. Herkunft der Adresse (DHCP/Profil), Treibername, Geschwindigkeit und SSH über LAN sind noch nicht ausgelesen bzw. getestet.

Zunächst war die I²C-Busliste leer. Nach sudo modprobe i2c-dev bestätigt die Nutzer-Ausgabe **i2c-1 (bcm2835, i2c@7e804000)** und **i2c-2 (bcm2835, i2c@7e805000)**. Die gezielte Prüfung auf Bus 1 zeigt **3f**: Ein Gerät an der erwarteten Button-SHIM-Adresse antwortet. Tastenfunktion, RGB-LED und ein möglicher Adresskonflikt sind damit noch nicht geprüft.

Bei einer leeren Busliste wurden diese Befehle erfolgreich verwendet:

~~~bash
sudo modprobe i2c-dev
i2cdetect -l
sudo i2cdetect -y 1 0x3f 0x3f
~~~

Das lädt die I²C-Geräteschnittstelle für Userspace in der laufenden Sitzung. Es ersetzt weder den WM8960-Treiber noch das bestehende Overlay. Erst nach erfolgreichem Zugriff prüfen, ob i2c-dev auch nach Neustart verfügbar ist; die dauerhafte Einrichtung folgt bei Bedarf.

### Tasten A–E — zwei erfolgreiche Durchläufe ✅

Der Nutzer hat den lesenden Tastentest zweimal ausgeführt. In beiden Durchläufen wurden für **A, B, C, D und E** vollständige Drücken-/Loslassen-Zyklen erkannt und alle fünf Tasten mit **PASS** gemeldet. Mehrfaches Drücken wurde ebenfalls als mehrere Zyklen ausgegeben. Damit ist die Bedienung der fünf Tasten in der laufenden Sitzung bestätigt; ein Langzeit-/Entprellungs-Stresstest sowie der Test nach Neustart stehen noch aus.

### RGB-LED — Sichtprüfung bestätigt ✅

Nach Installation von python3-venv und der Pimoroni-Bibliothek buttonshim 0.0.2 in einer separaten Testumgebung hat der Nutzer die zuvor angeleitete Farbsequenz **Rot → Grün → Blau → aus** mit „geht gut“ bestätigt. Damit ist der LED-Test als Nutzer-Sichtprüfung bestanden. Keine Farb-/Helligkeitsmessung und noch keine LED-Integration in den Sprachdienst.

### Neustartprüfung — Nutzerbestätigung ✅

Nach der Anleitung zum dauerhaften Laden von i2c-dev und dem Neustart hat der Nutzer die angeforderten Prüfungen (I²C-Busliste, Antwort an 0x3f, Netzwerkadressen, ALSA-Aufnahme/-Wiedergabegeräte und Status von pi-ptt.service) mit **„geht gut“** bestätigt. Dies ist eine zusammenfassende Nutzerbestätigung, keine neu eingereichte Terminalausgabe. Aktuelle IP-Adressen und genaue Dienst-/Gerätedetails nach Neustart wurden nicht erneut ausgelesen dokumentiert. Physische Tasten-/LED- und Aufnahme-/Wiedergabetests nach Neustart bleiben gesonderte Prüfungen.

## Abnahmeprotokoll

| Prüfung | Status / Ergebnis |
|---|---|
| Erweiterter Aufbau montiert, Fotos abgelegt | Foto-Nachweis 05.10.2026 |
| Hub und RTL8152B von Linux erkannt | Bestätigt: Terminus 1a40:0101, Realtek 0bda:8152 |
| LAN-Interface und Treiber | eth0 erkannt; Treibername noch offen |
| Link/Geschwindigkeit | Offen |
| LAN-IP und Router über LAN erreichbar | 172.22.9.108/24, eth0 UP; Router-Test offen |
| SSH über LAN | Offen |
| USB-A Port 1 / 2 / 3 | Offen / offen / offen |
| I²C 0x3f erreichbar, keine konkurrierende Nutzung | Antwort 3f auf Bus 1 bestätigt; konkurrierende Nutzung noch nicht geprüft |
| A / B / C / D / E drücken und loslassen | Alle fünf PASS, in zwei Hardware-Testläufen bestätigt |
| RGB Rot / Grün / Blau / aus | Nutzer-Sichtprüfung bestätigt: „geht gut“ |
| WM8960 und vorhandene PTT-Taste im neuen Stapel | Offen |
| i2c-dev dauerhaft beim Boot laden | Neustartprüfung laut Nutzer erfolgreich; Dateiinhalt nicht separat zurückgemeldet |
| Neustart: I²C, LAN, ALSA und PTT-Dienst | Nutzer bestätigt zusammenfassend „geht gut“; keine neue Terminalausgabe |
| Physische Tasten-/LED- und Audiotests nach Neustart | Noch nicht separat bestätigt |
| Leistungsaufnahme/Akkulaufzeit unter Zusatzlast | Offen |

## Quellen

- [Waveshare ETH/USB HUB HAT](https://www.waveshare.com/product/raspberry-pi/hats/interface-power/eth-usb-hub-hat.htm)
- [Waveshare Anschlussanleitung](https://www.waveshare.com/wiki/ETH/USB_HUB_HAT)
- [Pimoroni Button SHIM](https://shop.pimoroni.com/products/button-shim)
- [Pimoroni Python-Bibliothek und Registerbelegung](https://github.com/pimoroni/button-shim/blob/master/library/buttonshim/__init__.py)
