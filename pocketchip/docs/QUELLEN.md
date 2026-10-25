# Quellenprüfung, 25.10.2026

Primärquellen; Aussagen des Hardware-Builders sind Fremdvalidierung.

| Quelle | Prüfung und Schluss |
|---|---|
| [PocketTRIX](https://github.com/m4xx3d0ut/pocketchip-debian-builder/tree/c0e20421ffc64fc701ac63e80bbfad05dec86ece) und [NAND-Dokument](https://github.com/m4xx3d0ut/pocketchip-debian-builder/blob/c0e20421ffc64fc701ac63e80bbfad05dec86ece/docs/nand.md) | Gepinnte Quellen lokal gelesen; USB/FEL und NAND unterschiedlich; Toshiba-SLC-Pfad experimentell, kein allgemeiner Hardwarebeweis |
| [Debian 13 armhf](https://www.debian.org/releases/trixie/armhf/ch02s01.en.html) | ARMv7/VFPv3 kompatibel; SoC-Unterstützung garantiert weder Board-Boot noch NAND. Der Abschnitt enthält ältere generische sunxi-Aussagen; konkreter DT/Kernel/Builder sind zusätzlich nötig |
| [Linux 6.12 CHIP-DT](https://github.com/torvalds/linux/blob/v6.12/arch/arm/boot/dts/allwinner/sun5i-r8-chip.dts) | R8, AXP209, SDIO und USB; PocketCHIP-Peripherie braucht zusätzliche Integration |
| [TinyCore Ports](https://distro.ibiblio.org/tinycorelinux/ports.html), [FAQ/Lizenz](https://distro.ibiblio.org/tinycorelinux/faq.html) | dCore ARMv7hf A10/A20 ist kein geprüftes PocketCHIP-Image; GPLv2 für Projektcode laut FAQ, Pakete separat |
| [Realtek-Paket](https://packages.debian.org/trixie/firmware-realtek), [Copyright](https://metadata.ftp-master.debian.org/changelogs/non-free-firmware/f/firmware-nonfree/stable_copyright) | Firmware ist separat in non-free-firmware; keine pauschale GPL-Annahme |
| [QEMU virt](https://www.qemu.org/docs/master/system/arm/virt.html) | Synthetisches Board, ausdrücklich keine Reproduktion der Zielhardware |
| [Debian-Lizenzgrundlagen](https://www.debian.org/social_contract#guidelines) | Einzellizenzen und Quellverfügbarkeit je Paket berücksichtigen |

Kein Zugriff auf Originalgerät oder CT107 in diesem Auftrag. Der referenzierte
ChatGPT-Chat ließ sich mangels `read_thread`-Werkzeug nicht vollständig laden;
maßgeblich sind der übergebene Ausschnitt und die konkrete aktuelle Anforderung.
