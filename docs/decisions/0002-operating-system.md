# ADR 0002: Raspberry Pi OS Lite 64-bit (Trixie) 🐧

Datum: 05.10.2026. Status: angenommen; Hardwarevalidierung ausstehend.

## Entscheidung

Wir verwenden **Raspberry Pi OS Lite (64-bit) auf Debian 13 / Trixie** auf der 64-GB-microSD des Raspberry Pi Zero 2 W. Das System läuft ohne Desktop und wird per SSH verwaltet.

| Einstellung | Festlegung |
|---|---|
| Hostname | `wgz-voice-01` |
| Benutzer | `ivan` |
| WLAN-Land | `DE` |
| Zeitzone | `Europe/Berlin` |
| Zugriff | SSH mit Schlüssel |
| Clientbetrieb | systemd-Dienst |
| Python | Virtuelle Umgebung (`venv`) |
| Audio | ALSA und WM8960 |
| Kamera, später | rpicam/libcamera |

## Begründung

Raspberry Pi OS bietet den Raspberry-Pi-spezifischen Kernel und das zugehörige Hardwarewerkzeug. Lite spart Desktop-Prozesse und Arbeitsspeicher auf dem Pi mit 512 MB RAM. 64 Bit passt zum ARMv8-Prozessor und vereinfacht die Verwendung aktueller arm64-Software. Aufwendige Sprachmodelle laufen weiterhin im Homelab.

Trixie ist die vereinbarte Ausgangsbasis. Ein Wechsel auf einen anderen Unterbau erfolgt nur bei einem konkret nachgewiesenen Treiberproblem und wird als neue Entscheidung dokumentiert.

## Folgen und Abnahme

Die Entscheidung ersetzt keinen Kompatibilitätstest: WM8960-Treiber, PiSugar2-Software und Display müssen auf dem tatsächlich installierten Kernel geprüft werden. Image-Datum, Prüfsumme, Kernel und Treibercommit werden bei der Installation festgehalten.

Meilenstein M0: Trixie 64-bit startet auf dem Gerät, WLAN und SSH funktionieren auch nach einem Neustart. Danach Audioaufnahme und Wiedergabe prüfen.

## Quellen

- [Offizielle Raspberry Pi OS Images](https://www.raspberrypi.com/software/operating-systems/)
- [Raspberry Pi Zero 2 W](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
- [WM8960-Treiber](https://github.com/waveshareteam/WM8960-Audio-HAT)
