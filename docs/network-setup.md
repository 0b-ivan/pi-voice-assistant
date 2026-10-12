# Proximus – WLAN-Einrichtung über Setup-Hotspot

## Voraussetzung und Hardware

Pi Zero 2 W, Raspberry Pi OS Lite / Debian Trixie, NetworkManager auf wlan0.
Der interne Funkchip erlaubt AP und Managed-Interface parallel nur auf einem
gemeinsamen Kanal. Proximus wechselt daher bewusst zwischen WLAN-Client und
Setup-Hotspot; beide werden nicht gleichzeitig aktiv betrieben. Ethernet bleibt
während der Einrichtung unverändert.

Die Netzwerk-Menügruppe enthält WLAN suchen, gespeicherte WLANs, WLAN AN/AUS,
Hotspot starten und Hotspot beenden. Unbekannte WLANs werden am Display nicht
nach Passwort gefragt: Die Eingabe findet nur auf der lokalen Setup-Seite statt.

## Abhängigkeiten (einmalig, VOR dem Testen am Gerät)

Auf dem Pi (mit funktionierendem Ethernet/SSH) prüfen:

```bash
systemctl is-active NetworkManager
nmcli device status
command -v nmcli nft dnsmasq
sudo visudo -cf /etc/sudoers.d/proximus-setup
systemd-analyze verify /etc/systemd/system/proximus-setup.service
```

Wenn `nft` oder `dnsmasq` fehlen, müssen zunächst die passenden Debian-Pakete
(`nftables`, `dnsmasq-base`) installiert werden. Die Voice-Installation
führt absichtlich kein automatisches apt-Upgrade und keine Paketinstallation
durch. Vor dem ersten Hotspot-Test physischer Zugang zum Pi empfohlen.

## Manuelle Einrichtung

1. PiTFT **Netzwerk → Hotspot starten**. WLAN muss eingeschaltet sein.
2. PiTFT zeigt **Proximus-Setup** und das pro Start zufällige WPA2-Passwort.
3. Handy mit **Proximus-Setup** verbinden. Mit dem Browser
   **http://192.168.77.1/** öffnen. Automatische Captive-Popup-Erkennung wird
   **nicht** garantiert, DNS-Wildcard/NoDogSplash sind nicht aktiviert.
4. Gefundenes WLAN auswählen oder SSID von Hand eingeben, WPA2-Passwort
   eingeben, Verbinden auswählen.
5. Der Hotspot wechselt auf WLAN-Client. Bei Erfolg bleibt das neue
   NetworkManager-Profil für spätere Starts gespeichert. Bei falschem
   Passwort wird das neue Profil gelöscht und der Hotspot wieder aktiviert.
6. Über das PiTFT **Hotspot beenden** oder nach 15 Minuten automatisch.
   Ein zuvor aktives WLAN-Profil wird beim Beenden nach Möglichkeit wieder
   aktiviert, sofern nicht inzwischen ein neues Profil erfolgreich verbunden ist.

*Wichtige Einschränkung:* Nur WPA2-PSK-Profile (ASCII-Passwort 8–63 Zeichen)
werden in dieser Stufe provisioniert. Open/WPA-Enterprise/802.1X,
automatischer Einrichtungsmodus, QR-Code und gleichzeitiger AP+STA folgen
getrennt. Es gibt maximal fünf Einrichtungsversuche pro Sitzung.

## Sicherheit

- Die Setup-Unit wird **nicht** automatisch aktiviert. Nur physische
  Menübedienung oder ein expliziter \`systemctl start\` kann sie starten.
- `proximus-setup.service` erhält nur die benötigten Linux-Capabilities.
  Der Sprachdienst bleibt Benutzer `obivan`, kein Root.
- Ein eigenes, zufälliges WPA2-Kennwort wird auf dem PiTFT angezeigt und
  höchstens während der Sitzung in `/run/pi-ptt/` gespeichert.
- Die lokale Webseite bindet ausschließlich an `192.168.77.1:80`.
  Sie wird **nicht** über \`proximus.obivan.org\` geroutet.
- nftables blockiert sämtliches Forwarding **vom WLAN** und erlaubt vom
  Setup-WLAN zum Pi ausschließlich DHCP, DNS und HTTP Port 80. Dadurch
  wird NetworkManagers sonst standardmäßiges NAT zu `eth0` blockiert.
- WPA-Zugangsdaten des Zielnetzes werden weder im `nmcli`-Argumentvektor
  noch im Journal ausgegeben. Sie werden in einer Root-only-NM-Keyfile-Datei
  gespeichert; diese bleibt nur nach erfolgreicher Verbindung bestehen.
- Header- und CSRF-Validierung, Eingabegrenzen, 15-Minuten-Ablaufzeit und
  max. fünf Versuche. Kein externer Webzugang während des Setup-Modus.

Der durch `sudo /usr/local/sbin/pi-voice-install <commit>` installierte Stand
legt **nur** Unit, sudoers und Skript ab; er startet den Hotspot nicht.
Der echte Übergang AP↔STA sowie Firewallregeln müssen auf der Hardware
abgenommen werden. Nach dem Start prüfen:

```bash
systemctl status proximus-setup.service --no-pager
sudo nft list table inet proximus_setup
nmcli -f GENERAL.STATE,GENERAL.CONNECTION device show wlan0
journalctl -u proximus-setup.service -n 40 --no-pager
```

Rücknahme: PiTFT Hotspot beenden oder
`sudo systemctl stop proximus-setup.service`. Die Unit räumt das
Setup-Profil und die Firewallregeln auf. Ein bei Erfolg gespeichertes
WLAN-Profil wird dabei **nicht** gelöscht.

Bei beschädigter Verbindung (nur am Pi/über eth0):
`sudo /usr/bin/python3 /opt/pi-voice-assistant/src/proximus_setup.py --cleanup`.

## Quellen

- [NetworkManager ipv4.method shared / dnsmasq](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html)
- [RaspAP – AP-STA Einschränkungen](https://docs.raspap.com/features-experimental/ap-sta/)
- [NoDogSplash](https://github.com/nodogsplash/nodogsplash) – zusätzliche
  Captive-Erkennung ist getrennt und für manuelle lokale IP nicht nötig.
