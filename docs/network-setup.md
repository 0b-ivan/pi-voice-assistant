# Proximus – WLAN einrichten über den lokalen Access Point

## Betrieb ohne LAN und Internet

Proximus nutzt auf dem Pi Zero 2 W `wlan0` entweder als Client **oder**
als Setup-Access-Point, nicht beides gleichzeitig. Das Setup-WLAN funktioniert
vollständig ohne Internet und ohne Ethernet. Android/iOS können „Kein Internet“
melden; in diesem Fall **Trotzdem verbinden** wählen, ggf. Mobilfunk vorübergehend
deaktivieren und im Browser `http://192.168.77.1/` öffnen.

Der Name des Hotspots bleibt **`Proximus-Setup`**. Zufällige Namen würden
die Verbindungssuche erschweren. Das WPA2-Passwort wird dagegen **bei jedem
Neustart des Hotspots frisch zufällig erzeugt** (16 Zeichen, ca. 96 Bit
Entropie, 15 Minuten Gültigkeit). **Passwortlose APs sind nicht vorgesehen**:
Auf der lokalen Landingpage werden später Zugangsdaten für das Heim-WLAN
eingegeben. Der QR-Code darf nicht öffentlich gezeigt werden.

## Menü auf dem 240×240 PiTFT

Unter **Netzwerk → Access Point** liegen folgende Aktionen:

| Aktion | Verhalten |
|---|---|
| AP starten | Startet den Setup-AP bei Bedarf. Wenn er schon läuft, bleibt das bisherige Passwort gültig. Es erscheint der WLAN-QR-Code. |
| Passwort anzeigen | Zeigt die Zugangsdaten eines **laufenden** APs, ohne ihn einzuschalten. |
| Passwort erneuern | Stoppt und startet den AP mit **neuem** zufälligen WPA2-Code. Bereits verbundene Handys werden getrennt und müssen den **neuen** QR-Code scannen. |
| AP beenden | Beendet Portal/AP und entfernt nur deren temporäre Konfiguration. |
| Zurück | Zurück zum Netzwerkmenü; ein aktiver AP läuft weiter. |

Nach **AP starten** oder **Passwort anzeigen** zeigt das Display standardmäßig
einen **WLAN-QR-Code**. Mit der Handy-Kamera/QR-WLAN-Funktion scannen, um
`Proximus-Setup` zu verbinden. **Button SHIM E** wechselt zwischen
QR-Code und Textansicht (SSID, vollständiges Passwort, Portal-IP).
**B** schließt die Anzeige, ohne den AP abzuschalten.

Das Standardformat im QR-Code ist `WIFI:T:WPA;S:…;P:…;;`.
Er wird **auf dem Pi lokal** als kontrastreicher QR-Code berechnet
(4 Module weißer Rand); keine Netzwerkverbindung und kein Online-Dienst.
Das temporäre Passwort bleibt in einer nur für den lokalen Dienstbenutzer
lesbaren Datei unter `/run/proximus-setup/info` (RAM) und beim aktiven
PiTFT-Dialog unter `/run/pi-ptt/display-people.json`. Beim Schließen wird
die temporäre Displaydatei entfernt. Es gehört **nicht** ins öffentliche
`display-status.json`, Git, Journal oder in Cloudantworten.

## Voraussetzung und Installation

Der schon geprüfte Stack benötigt NetworkManager, nftables und dnsmasq.
Der QR-Encoder `python3-qrcode` ist ein kleines reines Python-Paket aus
Debian Trixie; das venv `/opt/pi-voice-assistant/.venv-display` verwendet
`--system-site-packages`. **Bei einer bereits eingerichteten Anzeige**
einmalig vor dem PR-Deployment über Ethernet ausführen:

```bash
sudo apt-get update
sudo apt-get install -y python3-qrcode
/opt/pi-voice-assistant/.venv-display/bin/python -c 'import qrcode; print("QR bereit")'
```

Bei Neuinstallation erledigt `scripts/install-display.sh` dies automatisch.
Fehlt `qrcode`, zeigt die Ansicht einen Fehlertext und man kann mit E
immer noch das Passwort als Text anzeigen; der Displaydienst stürzt nicht ab.

Außerdem prüfen:

```bash
systemctl is-active NetworkManager pi-ptt pi-display
nmcli device status
command -v nmcli nft dnsmasq
systemd-analyze verify /etc/systemd/system/proximus-setup.service
```

`pi-ptt.service` behält **`NoNewPrivileges=yes`** und bleibt unprivilegiert
als `obivan`. Der Root-Setup-Dienst wird über `systemctl`/Systemd-DBus
mit einer **auf `start`/`stop` von `proximus-setup.service`
begrenzten Polkit-Regel** (`deploy/52-pi-voice-hotspot.rules`) gestartet.
Die alte `sudoers`-Freigabe wird vom Installer entfernt.
Ein Aufruf von `sudo` aus dem Sprachdienst funktioniert nicht und wird
deshalb nicht mehr verwendet.

Bei Updates **vorher einen laufenden Setup-AP beenden**, da ältere
PR-Vorversionen ihre temporären Informationen in einem anderen Verzeichnis
ablegen. Dann den PR-Commit mit
`sudo /usr/local/sbin/pi-voice-install <commit>` installieren.
Die Installation aktiviert den Hotspot **nicht** automatisch.

## Handy einrichten

1. PiTFT: **Netzwerk → Access Point → AP starten**.
2. QR-Code scannen. Alternativ **E** und SSID/Passwort manuell eingeben.
3. Browser am Handy öffnen: **http://192.168.77.1/** (explizit HTTP).
   Automatisches Captive-Popup ist nicht garantiert.
4. Ziel-WLAN auswählen oder SSID manuell eintragen; dessen WPA2-Passwort
   im Handy eingeben und verbinden.
5. Der Pi wechselt zum Ziel-WLAN und speichert das Profil bei Erfolg.
   Bei falschem Passwort wird der Setup-AP wieder aktiviert.
6. **AP beenden** oder 15 Minuten warten.

Nur WPA2-PSK-Zielnetze mit ASCII-Passwörtern (8–63 Zeichen) werden
aktuell unterstützt. Höchstens fünf Versuche pro Sitzung.

## Sicherheit und Hardware-Abnahme

- Netzwerkisolation mit `nftables`: vom Setup-WLAN nur DHCP, DNS und
  HTTP zur lokalen Einrichtung, kein Weiterleiten zum LAN.
- Der lokale HTTP-Server bindet an `192.168.77.1:80` und benötigt kein
  Internet. Das Portal ist selbst **nicht** HTTPS; daher unbedingt den
  zufälligen WPA2-Setup-Schlüssel beibehalten.
- Der Root-Dienst schreibt WPA-Schlüssel ausschließlich in private
  NetworkManager-Profile, nicht in Kommandozeilenargumente oder Logs.
- Keine Einrichtung, QR-Code-Generierung oder Schlüsselanzeige über
  OpenRouter/CT 107.
- Die Setup-Unit hat keine `[Install]`-Sektion und läuft nie beim Boot
  automatisch. Ein späteres bewusstes Offline-Autostart-Konzept ist
  ein getrenntes Feature.

Prüfen nach AP-Start:

```bash
systemctl status proximus-setup.service --no-pager
nmcli -f GENERAL.STATE,GENERAL.CONNECTION device show wlan0
sudo nft list table inet proximus_setup
curl --noproxy '*' -s -o /dev/null -w 'HTTP %{http_code}\n' http://192.168.77.1/
journalctl -u proximus-setup.service -n 40 --no-pager
```

Beenden per PiTFT oder `sudo systemctl stop proximus-setup.service`.
Notfall-Cleanup nur über funktionierenden Ethernet-/lokalen Zugang:
`sudo python3 /opt/pi-voice-assistant/src/proximus_setup.py --cleanup`.
Ein erfolgreich gespeichertes Ziel-WLAN-Profil wird dadurch **nicht**
gelöscht.

## Referenzen

- [Debian Trixie: python3-qrcode](https://packages.debian.org/trixie/python/python3-qrcode)
- [NetworkManager: IPv4 shared / dnsmasq](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nm-settings-nmcli.html)
- [RaspAP: AP/STA-Beschränkungen](https://docs.raspap.com/features-experimental/ap-sta/)
- [NoDogSplash](https://github.com/nodogsplash/nodogsplash): nicht benötigt,
  solange die lokale IP manuell aufgerufen wird.
