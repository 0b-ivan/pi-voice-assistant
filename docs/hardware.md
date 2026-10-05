# Hardwarebestand und Fotodokumentation 🔧

Stand: 05.10.2026. Grundlage: Ivans Angaben und acht Projektfotos. Sichtbare Beschriftungen sind von noch nicht geprüften technischen Details getrennt.

## Erweiterter Aufbau — 05.10.2026 📸

Neu ergänzt: **Waveshare ETH/USB HUB HAT** und **Pimoroni Button SHIM**. Beide sind auf den neuen Fotos im Stapel montiert. Ihre Softwarefunktion ist noch nicht abgenommen; die Anleitung steht unter [Hardware-Erweiterungen in Betrieb nehmen](hardware-bring-up.md).

![Ethernet- und USB-HAT mit Button SHIM A–E](images/eth-usb-button-shim-2026-10-05.jpg)

Der Aufdruck lautet `ETH/USB HUB HAT`, ohne Zusatz `(B)` oder `PoE`. Sichtbar sind RJ45 und drei USB-A-Buchsen. Laut Waveshare bietet dieses Modell 10/100-Mbit/s-Ethernet mit RTL8152B. Netzwerk und USB nutzen die USB-Datenverbindung zum Pi; das HAT ist keine I²C-Netzwerkkarte.

![Seitenansicht des erweiterten Stapels mit USB-Brücke und Akku](images/hardware-stack-side-2026-10-05.jpg)

Die Seitenaufnahme zeigt Audio-HAT oben, Hub darunter, Pi, Stromversorgung und Akku sowie eine USB-Brücke. LEDs leuchten; daraus folgt noch kein Nachweis für Ethernet, USB-Geräteerkennung oder Akkulaufzeit. Die Brückenverbindung wird bei der Inbetriebnahme geprüft.

![WM8960 oben und fünf seitlich zugängliche Tasten des Button SHIM](images/wm8960-button-shim-2026-10-05.jpg)

Die fünf Tasten A–E liegen an der Außenkante. Der vorhandene WM8960-Taster bleibt zusätzlich verfügbar. Button SHIM nutzt einen TCA9554A-I/O-Expander an I²C-Adresse `0x3f`; auch die APA102-RGB-LED wird über den Expander angesteuert. Keine fünf zusätzlichen Raspberry-Pi-GPIOs oder SPI-Pins erforderlich.

## Früherer Aufbau — 05.10.2026 📸

![Aktueller Aufbau mit Audio-HAT auf dem Pi, zwei Lautsprechern und separat abgelegtem Display und Kamera](images/hardware-assembly-2026-10-05.jpg)

In der Mitte ist das WM8960-HAT auf dem Pi-Stapel montiert. Links und rechts liegen die beiden Lautsprecher mit Anschlussleitungen; am HAT ist der weiße Lautsprecherstecker belegt. Die Leitungen verlaufen teilweise außerhalb des Bildes, daher lässt sich die vollständige Kanalverdrahtung aus diesem Foto nicht prüfen.

Das Adafruit mini PiTFT und die Kamera mit Aufdruck `Frank-S01-V1.0` liegen separat unterhalb des Stapels. Sie sind in dieser Aufnahme nicht montiert; die Kamera ist anders als auf den früheren Fotos nicht am Pi angeschlossen. PiSugar2 und Akku sind im Stapel nicht ausreichend sichtbar, um Revision oder Anschlusszustand erneut zu bestimmen.

Dies ist der frühere Aufbau für die Audio-Inbetriebnahme. Aufnahme und Wiedergabe wurden inzwischen bestätigt, siehe [Audio-Abnahme](setup.md). Die drei neueren Fotos oben zeigen die Hardware-Erweiterungen.

## Bestand

| Teil | Details und Beleg | Aufgabe |
|---|---|---|
| Raspberry Pi Zero 2 W | Projektbasis; im montierten Stapel nicht vollständig lesbar; 512 MB RAM laut Hersteller | Audio-Client |
| WM8960 Audio-HAT | Foto zeigt zwei Mikrofone, Taste, Lautsprecheranschlüsse und Kopfhörerbuchse | Audioaufnahme und Wiedergabe |
| Zwei Lautsprecher | Laut Ivan angeschlossen; im aktuellen Übersichtsfoto separat links und rechts dargestellt | Sprachausgabe |
| Integrierte Mikrofone | Zwei Mikrofone am HAT sichtbar; separates Mikrofon für MVP nicht nötig | Spracheingabe |
| HAT-Taste | Aufdruck `BUTTON`; Herstellerbelegung GPIO17, physischer Pin 11 | Aufnahme auslösen |
| Waveshare ETH/USB HUB HAT | Aufdruck auf neuem Foto; 1× RJ45 10/100 Mbit/s, 3× USB-A; Erkennung noch offen | Kabelnetzwerk und USB-Erweiterung |
| Pimoroni Button SHIM | Aufdruck `BTN SHIM`, fünf Tasten A–E; I²C `0x3f`, RGB-LED | Zusätzliche Bedienung und Statusanzeige |
| PiSugar2 | Von Ivan benannt; Foto zeigt Stromversorgungsplatine, Akku und Status-LEDs | Akkubetrieb |
| Li-Ion-Akku | Aufdruck `PiSugar`, Modell `803052`; Kapazitätszeile nicht zuverlässig lesbar | Energiespeicher |
| Kamera | Zuvor angeschlossen, im aktuellen Übersichtsfoto separat abgelegt; Flexplatine trägt `Frank-S01-V1.0`; daraus kein sicherer Sensorname ableitbar | Spätere Bildanfragen |
| 64-GB-microSD | Von Ivan bestätigt; kein separates Foto | Betriebssystem |
| Adafruit mini PiTFT 1,3″ | Aufdruck `Adafruit miniPiTFT 1.3" 240x240`, zwei Taster | Optionale Statusanzeige |

## Audio-HAT

![WM8960 Audio-HAT mit Mikrofonen und Taste](images/wm8960-audio-hat.jpg)

Am Board sichtbar: zwei Mikrofone, die zentrale Taste, weißer Lautsprecherstecker, grüne Schraubklemme und 3,5-mm-Kopfhörerbuchse. Ob ein Stecker und eine Klemme gleichzeitig belegt sind, sagt noch nichts über das tatsächliche Audiorouting aus. Die beiden Lautsprecher sind als vorhandene Hardware erfasst; Impedanz, Leistung und Wiedergabe müssen noch geprüft werden.

## Stromversorgung

![PiSugar-Stromversorgung und Akku](images/pisugar-power.jpg)

Sichtbar sind Akku, Anschlussleitungen, Stromversorgungsplatine und leuchtende LEDs. Das Foto belegt keinen getesteten Ladezustand oder eine bestimmte Laufzeit. Die genaue PiSugar2-Revision und die Akkukapazität bleiben bis zum Ablesen offen.

## Kamera

![Kamera mit Flexplatinenaufdruck Frank-S01-V1.0](images/camera-flex.jpg)

Die früheren Fotos zeigen die Kamera über ein Flexkabel angeschlossen; im aktuellen Aufbau liegt sie separat neben dem Pi. Der lesbare Platinenaufdruck wird dokumentiert; das Sensormodell wird später mit rpicam/libcamera und gegebenenfalls weiteren Typenangaben ermittelt.

## Display

![Adafruit mini PiTFT 1,3 Zoll 240x240 mit zwei Tastern](images/adafruit-mini-pitft.jpg)

Das Foto identifiziert das Display als **Adafruit mini PiTFT 1,3″, 240 × 240**. Die frühere Einordnung als Waveshare Pico-LCD war falsch. Es ist ein Raspberry-Pi-Zusatzdisplay und benötigt für seinen vorgesehenen Einsatz keinen Raspberry Pi Pico. Es wird erst nach dem Audio-MVP angeschlossen und getestet. Laut Adafruit erlaubt die 1,3″-Variante kein einfaches Durchstecken eines Stacking-Headers durch die Platine. Montage oberhalb des Audio-HATs oder Anschluss per geeigneter Verlängerung separat planen.

## Schnittstellen und Planung

Alle GPIO-Angaben verwenden BCM-Nummern. Die Tabelle folgt den Herstellerbelegungen; der gemeinsame Aufbau ist noch nicht getestet.

| Bauteil | Schnittstelle / GPIOs | Prüfung |
|---|---|---|
| WM8960 Steuerung | I²C: GPIO2/3 | Bus zusammen mit PiSugar prüfen |
| WM8960 Audio | I²S: GPIO18/19/20/21 | Overlay und Treiber prüfen |
| HAT-Taste | GPIO17, physischer Pin 11 | Polarität und Entprellung prüfen |
| ETH/USB HUB HAT | USB-Datenverbindung zum Pi; GPIO-Stapel für Versorgung und Durchführung | USB-Erkennung, RTL8152-Treiber, LAN-Link und DHCP prüfen |
| Button SHIM | I²C: GPIO2/3, physische Pins 3/5; Adresse `0x3f`; 5 V, 3,3 V und Masse | Adresskonflikte, Tasten A–E und RGB-LED prüfen |
| mini PiTFT | SPI: GPIO10/11, CS GPIO8, DC GPIO25; Backlight GPIO22 | Pinbelegung und Montage vor Anschluss abgleichen |
| Displaytaster | GPIO23/24 | Optional zusätzliche Bedienung |
| PiSugar2 | I²C; weitere Details revisionsabhängig | Adresse und Versorgung prüfen |
| Kamera | Kameraanschluss/Flexkabel | Sensor und Treiber prüfen |

Audio und Display nutzen nach dieser Belegung unterschiedliche Signalpins. Das ist eine Planungsgrundlage; mechanische Stapelbarkeit, Versorgung und weitere Funktionen der konkreten PiSugar-Revision werden gesondert geprüft.

## Erste Abnahme der Erweiterungen — 05.10.2026 ✅

Ivans Ausgabe bestätigt den USB-Hub (1a40:0101, Terminus Technology) und Ethernet-Adapter (0bda:8152, Realtek RTL8152). eth0 ist in der Folgeausgabe UP und hat **172.22.9.108/24**; WLAN bleibt unter **172.22.9.128/24** erreichbar. Geschwindigkeit, SSH über LAN, die drei externen USB-Ports und Button SHIM stehen noch aus. Einzelne Prüfergebnisse stehen im [Abnahmeprotokoll](hardware-bring-up.md).

## Noch offen

- Ethernet-/USB-HAT: Treibername, Linkgeschwindigkeit, Adressvergabe, Router-/SSH-Test über LAN und drei USB-Anschlüsse. Linux-Erkennung und LAN-IP sind bestätigt.
- Button SHIM: Buszugriff, Tasten A–E, RGB-LED und gemeinsamer Betrieb mit WM8960/PiSugar2.
- PiSugar2-Revision, Akkukapazität und sauberes Abschaltverhalten.
- Kamerasensor und Testbild.
- Lautsprecherimpedanz, Leistung und Aufnahme-/Wiedergabetest.
- Endgültige Montage, Abstandshalter und Gehäuse.

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
