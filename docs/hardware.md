# Hardwarebestand 🔧

## Bestätigter Bestand

| Teil | Aufgabe | Stand |
|---|---|---|
| Raspberry Pi Zero 2 W | Audio-Client und Steuerung | Projektbasis; Modell vor Inbetriebnahme auslesen |
| WM8960 Audio-HAT | Aufnahme und Wiedergabe | Vorhanden |
| Zwei angeschlossene Lautsprecher | Sprachausgabe | Vorhanden; Funktion noch testen |
| Mikrofone auf dem WM8960-HAT | Spracheingabe | Bereits integriert; kein separates Mikrofon für den MVP nötig |
| Taste auf dem Audio-HAT | Aufnahme auslösen | Vorhanden; GPIO/Polarität am konkreten Board prüfen |
| PiSugar2 | Mobile Stromversorgung | Vorhanden; Variante, Akkukapazität und Software noch erfassen |
| Angeschlossene Kamera | Spätere Bildverarbeitung | Modell und Funktion offen |
| 64-GB-microSD | Betriebssystem und Client | Vorhanden |

## Zusätzliches Display

Das zusätzliche fotografierte Teil wurde bisher als **Waveshare Pico-LCD-1.3 (240 × 240)** eingeordnet. Diese Identifikation ist vor Anschluss anhand der Beschriftung zu bestätigen. Ein Pico-Modul ist nicht automatisch ein Raspberry-Pi-HAT. Anschluss, Pegel, Pinbelegung und gegebenenfalls Pico als Steuergerät müssen geklärt werden. Das Display ist für den MVP optional.

## Schnittstellen prüfen

Für das WM8960-HAT nennt die Herstellerdokumentation I²C und I²S. Die erwarteten BCM-GPIOs sind I²C 2/3 und I²S 18/19/20/21. GPIO17 für die Taste stammt aus der bisherigen Planung und muss am Schaltplan der konkreten Revision bestätigt werden. BCM-Nummern sind keine physischen Header-Pinnummern.

PiSugar2 verwendet ebenfalls I²C. Gemeinsamer Bus ist möglich, wenn Adressen, Pegel und Versorgung zusammenpassen. Vor weiteren Modulen eine vollständige Pin- und Adressliste erstellen. Noch keine verbindliche Displayverdrahtung festgelegt.

## Noch erfassen

- Boardrevisionen und Fotos der Typenbezeichnungen.
- Kameramodell und passendes Kabel.
- Lautsprecherimpedanz und Nennleistung.
- Akkukapazität, Ladezustand und Abschaltverhalten.
- Gehäuse, Abstandshalter, Belüftung und Zugentlastung.

## Quellen

- [Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
- [WM8960 Audio-HAT Wiki](https://www.waveshare.com/wiki/WM8960_Audio_HAT)
- [WM8960 Herstellertreiber](https://github.com/waveshareteam/WM8960-Audio-HAT)

Die Quellen beschreiben die Produkte; Kompatibilität der konkreten Kombination ist noch zu testen.
