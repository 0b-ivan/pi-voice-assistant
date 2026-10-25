# Proximus OS – Prüfprotokoll

Stand: 25. Oktober 2026 · Repository `0b-ivan/pi-voice-assistant` · PR [#141](https://github.com/0b-ivan/pi-voice-assistant/pull/141)

## Bestätigte automatisierte Prüfungen

| Prüfung | CI-Nachweis | Ergebnis |
| --- | --- | --- |
| Native Python-, GUI-, Netzwerk-/Sicherheits- und Simulator-Tests | [Proximus OS #40205557988](https://github.com/0b-ivan/pi-voice-assistant/actions/runs/40205557988), `native-and-protocol` | PASS |
| ARMv7-System: Debian 13 armhf, systemd, Xvfb 480×272, Tk und lokales Archiv in QEMU mit 512 MiB | gleicher Lauf, `armhf-system-boot`, `boot.log` | PASS |
| ARMv7-System: Xorg-Dummy-Softwarebildschirm mit Modus 480×272, systemd-Sitzung, `startx`, echte Tk-Smoke-Prüfung | gleicher Lauf, `armhf-system-boot`, `xorg-boot.log` | PASS |
| Bestehende Pi-, Live-Smoke- und Webclient-Workflows | [PTT tests #40205557919](https://github.com/0b-ivan/pi-voice-assistant/actions/runs/40205557919) | PASS |

Getesteter Commit: `cc14127b34fcfd8b7808b2045e7c4d902df71667`.

Bestätigte Gast-Ausgaben aus dem Jobprotokoll:

```text
PROXIMUS_GUI_OK 480x272 modules=6 text-only offline archive calculator
PROXIMUS_BOOT_OK armhf systemd uid=unprivileged ram=512MiB

PROXIMUS_XINITRC_STARTED
PROXIMUS_GUI_OK 480x272 modules=6 text-only offline archive calculator
PROXIMUS_XORG_BOOT_OK armhf Xorg Tk 480x272
```

Der zweite Boot verwendet `xserver-xorg-video-dummy` und eine eigene 480×272-Modeline. Das ist **ein echter ARM-Linux-/Xorg-/Tk-Prozesstest**, aber **kein Test einer Bildausgabe auf einer emulierten GPU**. Der normale, interaktive VNC-Modus verwendet diesen Dummy-Treiber nicht und wurde **nicht als funktionierend abgenommen**. Zuvor fehlten im Gast `/dev/dri/card0` und `/dev/fb0`; weitere GPU-/Framebuffer-Integration ist separat zu prüfen.

## Reproduzieren

Aus dem Repository-Root auf einem geeigneten Linux-/Docker-Host:

```sh
docker build -t proximus-builder -f pocketchip/image/Containerfile .
docker run --rm --privileged -v "$PWD:/src" proximus-builder pocketchip/image/container-build.sh
docker run --rm -v "$PWD:/src" proximus-builder python3 pocketchip/image/boot-test.py smoke
docker run --rm -v "$PWD:/src" proximus-builder python3 pocketchip/image/boot-test.py xorg-smoke
```

Die Build-Verzeichnisse müssen vor dem Neubau leer sein. Die Tests arbeiten auf regulären Dateien im QEMU-Snapshotmodus; kein Geräteflasher ist enthalten. Beim Docker-Desktop-Build die Volume-Hinweise in `README.md` beachten.

## Noch offen / nicht behaupten

- Kein Test auf einem originalen PocketCHIP und **keine Freigabe zum NAND-Schreiben/Flashen**.
- Allwinner-R8-Boot, Gerätetree, NAND/SLC, FEL/Recovery, Displaycontroller, Touchscreen, Hardwaretastatur/Fn-Tasten und WLAN-Firmware nicht validiert.
- PMIC, Akku, Ladezustände, Suspend, thermische Grenzen und realer Speicherbedarf auf 512-MB-Originalhardware nicht validiert.
- CT107-Textdienst nicht auf CT107 produktiv installiert; echte Netzwerkverbindung vom PocketCHIP nicht getestet.
- Updates/OTA, Offline-Upgrade, Rollback und Stromausfall-Recovery nicht freigegeben; Build noch nicht bitidentisch/reproduzierbar fixiert.
- Der Desktop-Simulator prüft Tk-Maus-/Tastatureingaben, nicht die elektrischen Touch- und Keyboard-Protokolle des PocketCHIP.

## Abnahmegrenzen

**Software-MVP in QEMU:** Die beiden oben genannten Boot-Smokes sind bestanden. **Interaktiver GPU-/VNC-Grafikpfad und Originalhardware:** bleiben offen. PR #141 bleibt deshalb vorerst ein Entwurf. Der Raspberry-Pi-, Web- und bestehende Sprachserver-Code bleibt unangetastet.
