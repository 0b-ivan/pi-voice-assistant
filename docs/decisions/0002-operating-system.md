# ADR 0002: Raspberry Pi OS Lite 64-bit / Trixie

Datum 05.10.2026. Status: angenommen, Boot/SSH/Audio/PTT/Vosk auf dem Pi bestätigt; weitere Hardware teilweise offen.

Raspberry Pi Zero 2 W mit 64-GB-microSD, **Raspberry Pi OS Lite 64-bit auf Debian 13 Trixie**, ohne Desktop. Hostname `pi-assistent`, Benutzer `obivan`, Verwaltung per SSH, Anwendung per systemd. Land DE / Zeitzone Europe/Berlin sind Planwerte, noch nicht separat ausgelesen bestätigt. Passwort-SSH ist bestätigt; mDNS muss bei Bedarf geprüft werden.

Lite spart Desktop-Prozesse; 64-bit passt zu aarch64-Software. Raspberry-Pi-Kernel und vorhandenes WM8960-Overlay funktionieren auf dem getesteten Kernel `6.18.50+rpt-rpi-v8`; kein zusätzlicher Waveshare-Treiber installiert. Kein OS-Wechsel wegen einer bloßen älteren Herstelleranleitung.

Dienst verwendet System-Python 3.13 und APT-libgpiod; Vosk separat im Vendor-Verzeichnis. Piper nutzt ein eigenes venv. Die frühere pauschale Vorgabe „alles im venv“ beschreibt den implementierten Dienst nicht.

PiSugar-Abschaltung, Display und Kamera müssen separat geprüft werden; ein OS-Boot bestätigt ihre Integration nicht. Reproduzierbare Schritte: [Setup](../setup.md). Installationsscreenshots und damaliger Bootstand: [Historie](../history/setup-2026-10-05.md#boot-und-erste-ssh-anmeldung--05102026-). Image-Prüfsumme fehlt noch.

Quelle: [Offizielle Raspberry Pi OS Images](https://www.raspberrypi.com/software/operating-systems/).
