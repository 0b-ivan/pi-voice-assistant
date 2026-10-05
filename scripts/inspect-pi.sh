#!/usr/bin/env bash
# Lesende Bestandsaufnahme. Ausgabe vor Weitergabe auf persönliche Daten prüfen.
set -u
section() { printf '\n## %s\n' "$1"; }
section 'Modell'
if [[ -r /proc/device-tree/model ]]; then cat /proc/device-tree/model; printf '\n'; fi
section 'Betriebssystem'
if [[ -r /etc/os-release ]]; then cat /etc/os-release; fi
uname -a
section 'Arbeitsspeicher'
command -v free >/dev/null && free -h
section 'Speichergeräte'
command -v lsblk >/dev/null && lsblk
section 'Audiowiedergabe'
if command -v aplay >/dev/null; then aplay -l; else printf 'aplay fehlt\n'; fi
section 'Audioaufnahme'
if command -v arecord >/dev/null; then arecord -l; else printf 'arecord fehlt\n'; fi
section 'I2C-Busse (kein Gerätescan)'
if command -v i2cdetect >/dev/null; then i2cdetect -l; else printf 'i2cdetect fehlt\n'; fi
