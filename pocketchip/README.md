# Proximus OS — PocketCHIP-System-MVP

Eigenes Debian-13-armhf-Systemprofil mit nativer Home-Shell und lokalen Modulen.
**Entwicklungsstand 0.1: QEMU-Ziel, kein flashbares PocketCHIP-Image.**
[Architektur und Basisentscheidung](docs/ARCHITEKTUR.md) ·
[Quellen](docs/QUELLEN.md) · [Prüfprotokoll](docs/VALIDIERUNG.md)

Originalziel: R8 ARMv7, 512 MB, 4 GB NAND, 480×272, Hardwaretastatur und Touch.
Das bestehende Pi-/Webprojekt wird nicht verändert. Keine Warhammer-Assets.

## Schnellstart der nativen Shell

Auf Debian mit `python3-tk`, `fonts-dejavu-core` und X11:

```sh
PYTHONPATH=pocketchip python3 -m proximus_os.shell
```

Kein Webserver und kein Browser. Home per Esc; Module 1–6 oder Touch/Tab/Enter.
Vox ist ausschließlich Text. Archive speichert lokale Notizen, Befehle bietet
Uhr/Datum/Status und einen begrenzten Rechner (`rechne (12 + 3) / 2`).
Werkzeuge zeigt echte verfügbare Systemwerte, fehlende Akkuwerte als unbekannt.
Homelab prüft den eigenen CT-Textdienst; Proxmox-/Kuma-Dashboards sind spätere
Erweiterungen. Einstellungen öffnet auf Wunsch nmtui in einem lokalen Terminal.

## PocketCHIP Desktop-Simulator (kein Board-Emulator)

Auf einem Linux-Desktop mit Tk/X11 (unter macOS mit einer funktionierenden Tk-Installation):

```sh
PYTHONPATH=pocketchip python3 -m simulator.pocketchip
```

Ein Fenster zeigt die **echte** native Proximus-Shell mit 480×272 Pixeln. Mausklicks
entsprechen Touch-Aktionen. Das zweite Fenster enthält eine anklickbare Tastatur
und HOME/ESC/SPACE/BACKSPACE/ENTER. Zur Texteingabe zuerst das Eingabefeld der
Shell anklicken. Standardmäßig benutzt der Simulator ein temporäres lokales Archiv
und **keine** CT107-Verbindung; mit `--config` und `--data` können Testpfade
explizit angegeben werden. Nicht für ein echtes Gerät oder NAND-Flash geeignet.

Es werden **keine** Allwinner-R8-, NAND-, WLAN-, Akku-, PMIC-, Displaycontroller-
oder echten Touchcontroller-Zustände emuliert. Das Werkzeuge-Modul zeigt bei
Verfügbarkeit Hostwerte; diese sind keine PocketCHIP-Messwerte. ARM-Boot- und
Board-Tests müssen getrennt erfolgen.

## ARM-System bauen und booten

Docker unter Linux oder Docker Desktop; die Buildumgebung benötigt für
chroot/binfmt privilegierte Rechte **innerhalb der isolierten Linux-VM**.
Kein Zugriff auf USB/Blockgeräte wird durchgereicht. Das Skript schreibt nur
reguläre Image-Dateien und verweigert das Überschreiben eines vorhandenen Builds.

```sh
docker build -t proximus-builder -f pocketchip/image/Containerfile .
docker run --rm --privileged -v "$PWD:/src" proximus-builder pocketchip/image/container-build.sh
docker run --rm -v "$PWD:/src" proximus-builder python3 pocketchip/image/boot-test.py
```

Auf Docker Desktop empfiehlt sich ein benanntes Volume an `/src/pocketchip/build`
für das Linux-rootfs (Unix-Gerätedateien/Rechte). Alle folgenden Container müssen
**dasselbe** Volume einbinden. Beispielzusatz für jeden Aufruf:
`--mount type=volume,src=proximus-os-build,dst=/src/pocketchip/build`.

Artefakte: `pocketchip/build/{zImage,initrd.img,rootfs.ext4,packages.tsv,SHA256SUMS,boot.log}`.
Paketmetadaten werden kryptografisch durch Debian geprüft; Versionen stehen im
Manifest. Der Builder nutzt aktuelle Trixie-Pakete, ist daher noch **kein**
bitidentischer Snapshot-Build. Binärrelease erst nach Quellpaketarchivierung.

Interaktiv: Der Emulator zeigt die native Xorg-Sitzung auf VNC. Docker muss auf
Loopback veröffentlichen und QEMU im Container auf dessen Interface lauschen:

```sh
# Ohne Docker (Linux mit qemu-system-arm):
pocketchip/image/run-qemu.sh interactive
# VNC-Viewer mit 127.0.0.1:5900 verbinden.
```

Im Docker-Container ist QEMU-VNC absichtlich ebenfalls auf Loopback beschränkt;
für eine grafische Prüfung dort einen lokalen Tunnel/Viewer im selben Netzwerk-
Namensraum verwenden. Kein ungeschütztes VNC im LAN veröffentlichen.
Das Image bootet im Snapshot-Modus ohne Netzwerk. Änderungen gehen beim Beenden
verloren; persistente VM-Daten benötigen später ein separates Datenlaufwerk.

Die QEMU-CPU ist Cortex-A15 ARMv7, ein Kern, 512 MiB; **kein** PocketCHIP/R8-
Emulator. Der Smoke-Boot startet systemd und testet die Shell als normalen
Benutzer in Xvfb 480×272. Er verlangt beide Marker `PROXIMUS_GUI_OK` und
`PROXIMUS_BOOT_OK` und ein reguläres Ende des Emulators. Kernel, Initramfs und
rootfs sind real, NAND-/FEL-/PMIC-Unterstützung wird dadurch nicht geprüft.

## Echter Textkanal auf CT107 (separat, optional)

`/v1/ask` des alten Sprachservers wird nicht benutzt: dort läuft TTS weiter.
Der neue Dienst importiert nur eigene Textmodule und die Python-Standardbibliothek.
Er nutzt standardmäßig das lokale OpenAI-kompatible Backend auf
`http://127.0.0.1:8766/v1/chat/completions` (z. B. llama.cpp); kein STT/Piper/DSP.

Eine Installation auf CT107 ist in diesem Auftrag **nicht** erfolgt. Für einen
späteren kontrollierten Aufbau: `proximus_os/` nach `/opt/proximus/` kopieren und
`systemd/proximus-text.service` als separaten Dienst installieren. Kein bestehendes
Installationsskript ausführen. Python 3.11+ genügt, Tk ist auf dem Server unnötig.
In `/etc/proximus-text.env` (root:root 0600) setzen:

```text
PROXIMUS_TEXT_TOKEN=<eigener zufälliger Token, mindestens 32 ASCII-Zeichen>
PROXIMUS_LLM_URL=http://127.0.0.1:8766
PROXIMUS_LLM_MODEL=local
```

Token z. B. mit `python3 -c 'import secrets; print(secrets.token_hex(32))'` erzeugen.
Der Dienst bindet ausschließlich an 127.0.0.1:8770. Für das Handheld einen
TLS-Reverseproxy mit gültigem/vertrauenswürdigem Zertifikat oder SSH-Tunnel
verwenden. Kein Bearer-Token über unverschlüsseltes LAN-HTTP.
Das lokale Backend muss ohne neue Authentifizierung im CT erreichbar sein;
Backendtoken-/Cloudintegration sind im MVP nicht enthalten.

Am Handheld `~/.config/proximus/client.json` anlegen, Modus **0600**:

```json
{"url":"https://ct107-text.example.org","token":"EIGENER_TOKEN_MIT_MINDESTENS_32_ZEICHEN"}
```

Alternativ `http://127.0.0.1:8770` durch einen selbst eingerichteten SSH-Tunnel.
Keine Secrets im Image oder Git. Redirects und Umgebungsproxies sind deaktiviert.
Anfragen werden nicht gespeichert/automatisch wiederholt. Keine Sprecheridentität,
Nextcloud-/Gedächtnisrechte und keine Aktionen durch LLM-Ausgaben.

Protokoll: `GET /health` ist öffentlich und sagt nur `ok`. Authentifiziertes
`GET /v1/status` prüft den Textdienst, ausdrücklich keine erfolgreiche Inferenz.
`POST /v1/text` akzeptiert ausschließlich `{"text":"…"}` (1–2000 Zeichen) und
liefert `{"mode":"text","reply":"…"}`. 401 bei falschem Token, 400/413 bei
ungültiger Eingabe, 503 bei belegtem Kern, 502 bei Backendfehler. Kein Audiofeld.

## Tests und CI

```sh
PYTHONPATH=pocketchip python3 -m unittest discover -s pocketchip/tests -v
xvfb-run -a -s '-screen 0 480x272x24' env PYTHONPATH=pocketchip python3 -m unittest discover -s pocketchip/tests -v
shellcheck pocketchip/image/*.sh pocketchip/image/xinitrc
```

Zusätzliche CI: `.github/workflows/proximus-os.yml`; bestehende Tests laufen
unverändert. Die ARM-CI baut und bootet das vollständige Gastimage und bietet
es sieben Tage als Entwicklungsartefakt an. Kein automatisches Release/Deployment.

## Noch nicht freigegeben

PocketCHIP-Hardwareprofil (Board-DT, Tastatur-Fn, Touch, WLAN-Firmware), NAND,
Lade-/Unterspannungslogik, Suspend, Hardwareperformance, produktives CT107,
atomare OTA und Recovery nach Stromausfall. PocketTRIX dient als geprüfte
Quellenreferenz; daraus wurde kein Hardwareflasher übernommen. Vor Gerätetests
Bestandsaufnahme und UART-/FEL-/USB-Recovery vorbereiten; NAND-Schreiben verlangt
immer eine separate explizite Freigabe. [Details](docs/ARCHITEKTUR.md).

Neue Dateien unter `pocketchip/`: [MIT](LICENSE). Das übrige Repository und
Debian-/Upstream-Komponenten behalten ihren jeweiligen Lizenzstatus.
