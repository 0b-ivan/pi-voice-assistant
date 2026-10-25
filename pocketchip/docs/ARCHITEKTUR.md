# Proximus OS — Architekturentscheidung

Stand: 25.10.2026. Produktname **Proximus OS**; Cogitator OS bleibt eine Namensoption.
Ziel: eigenes Debian-basiertes Handheld-System mit Bootprofil, nativer Home-Shell,
lokalen Modulen, Systemintegration und Wiederherstellungsweg. Kein Browser-Kiosk.

## Repository-Befund vor der Implementierung

Ausgangspunkt: `main` bei `8ad44bb6be80f2f3e9c90ac4c51bf6244fce5042`.
`src/` enthält Pi-Hardware und Sprachlogik; `server/servitor_server.py` verbindet
STT, LLM, TTS und DSP; `web/` ist der bestehende Browserclient.
`POST /v1/ask` nimmt Text entgegen, führt aber `pipeline.synthesize` aus.
Text eingeben und Audio wegwerfen wäre daher kein echter Textmodus.
Die vorhandene CI prüft Python, Web und einen öffentlichen Live-Endpunkt.
Eine übergeordnete Projektlizenz/AGENTS.md wurde im Checkout nicht gefunden.
Der offene PR #140 betrifft Webgrafik und gehört nicht zu diesem Vorhaben.

Alle Änderungen liegen unter `pocketchip/` und in einer zusätzlichen CI-Datei.
Pi, Webclient und Sprachserver werden weder geändert noch automatisch installiert.
Die Umsetzung verwendet eine Feature-Branch `feat/proximus-os` und einen PR.

## Basisvergleich und Entscheidung

| Basis | Nachgewiesener Ausgangspunkt | Grenzen | Entscheidung |
|---|---|---|---|
| Debian 13 armhf | ARMv7/VFPv3, Paketverwaltung und armmp-Kernel | Kein fertiger PocketCHIP-Installer; Board-DT, NAND und Bootloader gesondert nötig | Userland und QEMU-MVP |
| PocketTRIX | GPL-2.0-Builder, Debian 13; USB/FEL plus Board-Overlay; experimenteller NAND/SLC-Pfad | Validierung des Autors nur an einer Toshiba-4G-MLC-Einheit; legacy U-Boot für NAND | Hardware-Referenz, zunächst USB/FEL; kein blindes Forken des Flashers |
| TinyCore/dCore | GPLv2 laut Projekt-FAQ; ARMv7hf-Ports für A10/A20 gelistet | Kein belegtes aktuelles R8/PocketCHIP-Profil mit LCD, Tastatur, Boot und NAND; piCore ist Raspberry-Pi-spezifisch | Nicht MVP-Basis; zusätzlicher Portierungsaufwand ohne Vorteil beim Bring-up |

PocketTRIX wurde lokal bei `c0e20421ffc64fc701ac63e80bbfad05dec86ece`
geprüft: README, `docs/nand.md`, Paketliste, Upstream-Skript und
`dts/pocketchip-v73-mainline.dtso`. Der Builder ist eine Referenz, kein im
Proximus-MVP ausgeführter Hardwaretest. Keine Upstream-Skripte werden automatisch
geladen oder als root ausgeführt. Recherchierte Quellen: [QUELLEN.md](QUELLEN.md).

## Hardware, Boot und NAND

Original: Allwinner R8/Cortex-A8 ARMv7, 512 MB RAM, 4 GB rohes NAND,
480×272 resistiver Touch, TCA8418-Tastatur, RTL8723BS und AXP209.
Der CHIP-DT im Linux-Quellbaum ist kein vollständiger PocketCHIP-DT: das
PocketCHIP-Overlay ergänzt insbesondere Panel/Touch/Tastatur.

Geplanter Erststart: Boot-ROM → FEL in RAM → passender mainline U-Boot →
Debian-armmp-Kernel + Initramfs + geprüfter DT/Overlay → USB-rootfs → systemd →
lokale Sitzung → Xorg → native Proximus-Home-Shell. USB/FEL umgeht das Ersetzen
der NAND-Bootkette. Auch FEL wird in diesem Auftrag nicht am Gerät ausgeführt.
Original PocketCHIP hat keinen normalen microSD-Slot; generische Pi-SD-Images
sind keine Installationslösung.

NAND später: Geometrie, Hersteller, ECC/OOB, Bad Blocks, BBT und Page-Pairing
zuerst sichern und bestimmen. `sunxi_nand` allein beweist keinen sicheren Boot.
PocketTRIX nutzt für Toshiba 4G MLC einen gepatchten legacy CHIP-U-Boot und
SLC-on-MLC mit UBI/UBIFS; dadurch verbleiben ungefähr 2 GiB logische Kapazität.
SPL/Page-Mapping ist herstellerspezifisch. Hynix und andere Revisionen sind
kein freigegebenes Ziel. Kein ext4 direkt auf rohem MTD, keine Umgehung der
MLC-UBI-Sperre, kein erfundenes universelles NAND-Layout.

## MVP-Systemgrenze

Ein separat gebautes Debian-13-armhf-rootfs wird mit armmp-Kernel in QEMU `virt`
auf einem ARMv7 Cortex-A15, einem Kern und 512 MiB RAM gebootet. Das prüft echte
ARM-Programme, systemd, lokale Daten und die Shell; es emuliert **nicht** R8,
NAND, PMIC, WLAN, Tastaturmatrix oder das PocketCHIP-LCD. Ein Xvfb mit 480×272
prüft dieselbe Tk-Shell im Gast. Der interaktive Gast nutzt eine VNC-Anzeige.
Das Image ist ausschließlich für QEMU; es ist kein PocketCHIP-Flashimage.

Shell: Python-Standardbibliothek und Tk/X11, ohne Browser, Compositor, GPU-Zwang,
Audio-, STT- oder LLM-Modelle. Sechs Module: Vox (Text), Homelab (CT-Kanalstatus),
Archive (lokale Notizen), Befehle (Uhr/Datum/Status/Rechner), Einstellungen
(Netzwerkdiagnose/Konfiguration), Werkzeuge (lokale Systemwerte).
Home startet per Touch oder 1–6, Tab/Enter, Escape führt zurück; Texte scrollen.
Netzwerkaufrufe laufen außerhalb des GUI-Threads. Keine automatische Wiederholung
von Nachrichten, keine Offline-Warteschlange, keine erfundenen Offline-Antworten.
Notizen bleiben lokal, Gesprächsverlauf nur im RAM und begrenzt.

CT107 erhält optional einen **separaten** `proximus-text`-Dienst auf Loopback
Port 8770. `POST /v1/text` spricht ausschließlich einen OpenAI-kompatiblen lokalen
Text-Backenddienst (z. B. vorhandenes llama.cpp auf 8766) an. Eigener deutscher
Systemprompt kennt PocketCHIP statt Pi und fordert lesbaren Text. Kein Import
der Sprachpipeline, keine Synthese, keine Audioantwort. Das MVP hat weder
automatische Nextcloud-Rechte noch Zugriff auf das Pi-Sprechergedächtnis.
Backendausfall liefert einen Fehler; CT107 muss separat und ausdrücklich
konfiguriert werden. Dieser Auftrag deployt nichts auf CT107.

## Designsprache

Eigenständiges industrielles Terminal: dunkles Graphit, warmes Pergament,
Bernstein für Fokus, gedämpftes Rot für Fehler, grün für bestätigte Verbindung.
Gezeichnete Linien/Platten und ein abstraktes P-Signet, keine Totenschädel,
Fraktionszeichen, offiziellen Illustrationen, Fonts oder kopierten Webassets.
Deutsch als UI-Sprache; klare Zustände statt flackernder Effekte. Schaltflächen
mindestens 40 Pixel hoch, Navigation auch ohne Touch. Keine Animationen im Leerlauf.

## Ressourcen, Eingabe und Betrieb

Zielbudgets (auf Originalhardware **noch nicht gemessen**): Shell RSS <60 MiB,
gesamter Leerlauf <180 MiB, rootfs <1 GiB, mindestens 25 % freier NAND-Platz.
Build manifestiert installierte Paketversionen; QEMU-Smoke erfasst Speichernutzung.
Nur RAM-Journal mit Größenlimit; keine periodischen Cloudabfragen. Batteriewerte
kommen aus `/sys/class/power_supply`, fehlende Werte heißen „unbekannt“.
Keine erfundene Prozentrechnung aus Spannung. Suspend, Ladegrenzen und sichere
Unterspannungsabschaltung benötigen Messung an der AXP209-Hardware.

WLAN: NetworkManager/nmtui, keine vorkonfigurierten Netze oder Passwörter.
Hardwareprofil benötigt rtl8723bs + gesondert lizenzierte Firmware. Fn-Layout,
Kalibrierung, DPMS und Backlight werden aus geprüftem Boardprofil übernommen,
nicht aus der VM abgeleitet. MVP zeigt Status und öffnet nmtui als lokalen
Benutzer; privilegierte Netzänderungen erfordern lokale Sitzungsrechte.

## Security, Updates und Recovery

Kein Root-GUI, kein SSH-Server, keine Standardpasswörter, kein Listener auf dem
Handheld. Config/Token nur benutzereigen und Modus 0600; TLS-Zertifikatsprüfung,
keine Tokenweitergabe bei Redirect, HTTP nur Loopback für Tests/SSH-Tunnel.
CT-Dienst: konstanter Tokenvergleich, Body-/Antwortlimits, Zeitlimits, ein
Backendauftrag zugleich, keine Shellausführung, keine Anfrageinhalte in Logs.
Modelltext kann niemals Gerätesteuerung auslösen.

MVP-Updates: manuell über signierte Debian-Repositories und versionierten
Systemoverlay. Keine unbeaufsichtigte OTA-/Bootloader-/NAND-Aktualisierung.
App-Rollback über vorheriges Git-Release/Backup; Paket-Rollback ist kein
atomarer Systemrollback. Produkt-OTA bleibt gesperrt, bis signierte Metadaten,
Anti-Rollback, Stromausfalltests und Speicherreserve belegt sind. Wegen effektiv
~2 GiB kein ungeprüftes A/B-Versprechen. Kernel/SPL außerhalb App-Update halten.

Recovery: QEMU läuft mit Snapshot, frischer Start verwirft Änderungen; persistente
Notizen separat sichern. Hardware später UART + FEL + bekannt gutes USB-rootfs,
offline gesicherte originale Bootassets/NAND-Geometrie und individuelle Daten.
Vor NAND-Schreiben Backup und Restore demonstrieren und explizite Freigabe
einholen. Dieses Repository enthält für Proximus OS **keinen Flasher**.

## Lizenzgrenzen

Das bestehende Repository hat keine globale Lizenz: keine pauschale Behauptung,
es sei frei weiterverteilbar. Neue eigenständige Dateien in `pocketchip/` stehen
unter der dortigen MIT-Lizenz. Keine bestehenden Grafiken oder Texte werden
übernommen. PocketTRIX GPLv2 und eventuelle kopierte/abgeleitete Hardwaredateien
müssten separat gekennzeichnet und mit Quellangebot/Lizenz verteilt werden;
im MVP wird daraus kein Code kopiert. Debian ist eine Paketkollektion mit
Einzellizenzen, nicht „alles GPL“. Kernel GPL-2.0-only, U-Boot GPL-2.0+,
Python/Tcl/Tk und Fonts behalten ihre Paketlizenzen. Firmware-Realtek liegt in
non-free-firmware und muss vor einer Hardwareverteilung einzeln auditiert werden.
Ein rootfs führt `/usr/share/doc/*/copyright`, Paketmanifest und Quellpaketnamen
mit; für öffentliche Binärreleases müssen zugehörige Quellen gesichert werden.
Warhammer/Mechanicus sind fremde Marken; keine Zugehörigkeitsbehauptung.

## Freigabestufen

1. Dieser MVP: ARM-QEMU-Boot, native UI, Offline-Module, CT-Text-Protokoll,
   isolierte CI und Regression der bestehenden Clients.
2. Mit Gerät und gesonderter Freigabe: read-only Bestandsaufnahme, UART/FEL/USB,
   Display/Fn/Touch/WLAN/Akku/Power vermessen und dokumentieren.
3. NAND erst nach Geometrieabgleich, Restore-Probe, Lizenz-/Quellpaketprüfung
   und expliziter Schreibfreigabe. Anschließend Update-/Recovery-Stromausfalltests.
