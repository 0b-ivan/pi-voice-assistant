# Button SHIM im Sprachdienst

Tasten/RGB sind auf `main` implementiert. Einzeltest A–E zweimal bestanden, Hersteller-LED-Test per Nutzer-Sichtprüfung bestätigt. Die neuere Dienstversion initialisiert SHIM und transkribiert auf dem Pi; vollständige Abnahme der integrierten Steuerung bleibt offen.

## Bedienung

| Taste | Funktion |
|---|---|
| A | Halten: aufnehmen; Loslassen: STT |
| GPIO17 / WM8960 BUTTON | Gleiche PTT-Funktion, parallel zu A |
| B | Eigene Dienstansage stoppen, Aufnahme/STT-Ergebnis verwerfen; bei offenem Menü: eine Ebene zurück, oben schließen (erst danach bricht B ab) |
| C / D | Digitalen `Playback`-Pegel um 2 dB senken/erhöhen, gehalten wiederholt |
| E | Ohne Menü: Status ansagen (Warnungen zuerst, dann Akku, Temperatur, Serververbindung, Sprachkern, Laufzeit; siehe [Architektur](architecture.md#antworten-ohne-llm-status-und-charakter)). Bei offenem Menü: **Bestätigen** |
| PiTFT-Taste oben (GPIO23) / unten (GPIO24) | Menü öffnen, Auswahl hoch/runter; bei ausgeschaltetem Display nur aufwecken |

A und GPIO17 bilden gemeinsam einen Aufnahmetaster: Aufnahme endet erst, wenn beide losgelassen sind. Alle Tasten beim Start loslassen; B–E lösen einmal pro Druck aus. B hat bei gleichzeitigen Aktionen Vorrang.

STT läuft im Hintergrund. B verwirft dessen Ergebnis, bricht den nativen Vosk-Aufruf aber nicht ab. Der Slot bleibt bis zum Abschluss belegt, danach braucht gehaltenes PTT Release. PTT stoppt eine vom Dienst gestartete Statusansage vor der Aufnahme; E spricht nicht während Aufnahme. Die Statusansage wird beim Druck neu aus `/proc`, `/sys` und dem Dateisystem aufgebaut; die 1-Minuten-Load wird auf die CPU-Kernzahl normiert und als `SYSTEMLAST` gesprochen. Eigene Wiedergabe läuft als verwalteter `aplay`- oder FFmpeg-Prozess; externe Audioprozesse werden nicht verwaltet.

C/D ändern nur den digitalen `Playback`-Regler (0,5-dB-Raster, 255 = 0 dB), nicht Speaker, Speaker AC/DC oder Mikrofonpegel: **2 dB pro Druck** (`TTS_VOLUME_STEP_DB`) im Bereich −60 bis 0 dB. Gehalten wiederholt sich der Schritt nach 0,45 s alle 0,15 s. Früher waren es „5 %“ der Rohwerte, also rund 6,4 dB pro Druck, was hörbar sprang. Ein bereits tiefer eingestellter Pegel wird beim Leiserstellen nicht auf −60 dB angehoben. Das PiTFT zeigt 2,5 s lang „LAUTSTÄRKE“ mit Balken (Prozent des Bereichs −60…0 dB, an den Grenzen MIN/MAX). Änderungen werden vom Dienst nicht für den nächsten Boot gespeichert.

## Menü auf dem PiTFT

Die beiden PiTFT-Tasten öffnen ein kleines Menü; **E bestätigt**, **B schließt**, nach 15 s ohne Taste schließt es selbst, PTT schließt es sofort. `pi-ptt` liest beide Tasten (Pull-up, aktiv Low, `PTT_PITFT_BUTTONS=23,24`, leer = aus) und besitzt den Menüzustand; das Display zeigt ihn nur an (feste Werte in `display-status.json`).

Bluetooth-Lautsprecher ([`src/bluetooth.py`](../src/bluetooth.py)) brauchen einmal [`deploy/install-bluetooth.sh`](../deploy/install-bluetooth.sh) (bluez-alsa, Gruppe bluetooth). Ein gekoppelter Lautsprecher wird alle 30 s geprüft und neu verbunden; Proximus sagt Verbinden und Trennen an. Die Lautstärke regelt der Lautsprecher selbst (C/D wirken nur auf den eingebauten). Auf dem Pi Zero 2 W teilen sich WLAN und Bluetooth einen Funkchip.

Das Menü hat Gruppen; E öffnet eine Gruppe, jede endet mit „Zurück“, B geht eine Ebene zurück und schließt oben.

| Gruppe | Eintrag | Wirkung |
|---|---|---|
| Sprache | Aktivierungswort AN/AUS | Mithören für „Proximus“/„Hey Jarvis“ ein/aus (nicht über Neustart gespeichert) |
| | Sprachkern AUTO/FREI/LOKAL | AUTO: OpenRouter (Mistral Medium 3.5), bei Ausfall lokal; FREI: wenig eingeschränktes Modell; LOKAL: nur das lokale Modell auf CT 107 |
| | Lore-Stufe AUS/DEZENT/VOLL | Warhammer-40k-Vokabular in Antworten und Status (Grundeinstellung `PTT_LORE_LEVEL`) |
| | Server nutzen AN/AUS | Zwischen CT 107 und rein lokalem Betrieb umschalten |
| Personen | Bekannte Personen | Liste der Stimmprofile, je Person Nachtrainieren/Details/Passphrase/Löschen mit Stimm- und Passphrase-Anmeldung ([Gedächtnis](memory.md#kennenlernen-und-stimmerkennung)) |
| | Kennenlernen | Neue Person: Stimmproben, Fragen, Stimmprofil |
| | Stimme nachtrainieren | 20 Stimmproben, dem passenden Profil zugerechnet |
| Audio | BT-Lautsprecher AN/AUS | Gekoppelten Bluetooth-Lautsprecher verbinden/trennen; verbunden geht jede Sprachausgabe dorthin, sonst über den eingebauten Lautsprecher |
| | Lautsprecher verbinden | Liste der gekoppelten Lautsprecher, E verbindet den gewählten |
| | Lautsprecher suchen | Liste erscheint sofort und wächst während der 12 s Suche (nur Audiogeräte, gekoppelte mit ✓); E auf einen Eintrag koppelt (falls nötig) und verbindet |
| | Lautsprecher entfernen | Gekoppelten Lautsprecher vergessen |
| Gerät | WLAN AN/AUS | Funk über rfkill; ohne LAN-Kabel ist der Pi danach offline |
| | Alarme AN/AUS | Ansagen stumm; Herunterfahren bei leerem Akku bleibt aktiv |
| | Status-LED AN/AUS | SHIM-LED aus; Aufnahme und Fehler zeigt sie trotzdem |
| | Display aus | Hintergrundbeleuchtung aus; die nächste PiTFT-Taste weckt nur |
| System | Systeminfo | IP, Laufzeit, RAM, Server, Gedächtnis, Version, Akku |
| | Status ansagen | Wie E ohne Menü |
| | Wartung | [Wartungsmodus](maintenance.md) |
| | Schließen (oberste Ebene) | Menü zu |

## Status-LED

Farben entsprechen dem Display (SERVER cyan, LOKAL gelb, AUSGABE orange):

| Farbe | Zustand |
|---|---|
| Gedämpftes Grün | Bereit; nächste Anfrage geht an den Server (bzw. Pi ohne konfigurierten Server) |
| Gedämpftes Gelb | Bereit, aber lokal: Server im Menü abgeschaltet oder letzte Anfrage lief im Fallback |
| Rot | Aufnahme (auch bei abgeschalteter LED) |
| Cyan, hell/dunkel pulsierend (0,6 s) | Verarbeitung auf CT 107 |
| Gelb, hell/dunkel pulsierend | Verarbeitung auf dem Pi |
| Orange in drei Helligkeitsstufen | Sprachausgabe, folgt der Sprachhüllkurve |
| Violett | Menü offen |
| alles auf 35 % gedimmt | Ruhe (wie der Bildschirm); Aufnahme, Fehler, kritische Alarme bleiben hell |
| aus | Schlaf; zusätzlich die grüne ACT-LED des Pi (udev-Regel [`deploy/91-pi-voice-leds.rules`](../deploy/91-pi-voice-leds.rules)) |
| Rot blinkend (4 Hz, 3 s) | Fehler (auch bei abgeschalteter LED) |
| Aus | LED im Menü abgeschaltet, Dienst beendet oder reine Probe |

Ein Farbwechsel am SHIM sind rund 190 I²C-Schreibzugriffe (gemessen ~0,5 ms pro Transaktion, also 50–90 ms). Früher liefen sie in der 10-ms-Hauptschleife und blockierten in dieser Zeit die Tastenabfrage. Jetzt schreibt `LedWriter` in einem eigenen Thread: nur die jeweils letzte gewünschte Farbe, höchstens alle 0,1 s; Tastenlesen und LED-Bits wechseln sich pro Transaktion ab. Animationen sind deshalb bewusst zweistufig statt weich überblendet. Abnahme auf der Hardware steht aus.

## Aktivieren

Vollständigen [Dienst installieren](setup.md#4-repository-und-dienst-installieren), [SHIM einzeln prüfen](hardware-bring-up.md), `i2c-dev` verfügbar machen. In `/etc/pi-ptt.env` folgende Einstellungen setzen:

```text
PTT_BUTTON_SHIM=1
PTT_SPEAK_COMMAND="/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py"
```

```bash
sudo systemctl restart pi-ptt.service
journalctl -u pi-ptt.service -n 30 --no-pager
```

Erwartet: `shim_ready`, Bus 1, `0x3f`. Der Dienst nutzt die Gruppe `i2c`. Bei I²C-Fehler wird SHIM bis zum Neustart deaktiviert; GPIO17 bleibt nutzbar. Beim späteren Busausfall wird der aktuelle Vorgang verworfen.

Der Dienst hält Piper resident; `speak.py` ist nur Fallback. Für das Servitor-Profil installiert [Piper-TTS](text-to-speech.md) zusätzlich `de_DE-thorsten_emotional-medium` und FFmpeg. Der residente Maschinenpfad streamt Piper-PCM direkt nach FFmpeg/ALSA; dadurch kann die Ausgabe mit dem ersten synthetisierten Chunk beginnen.

Keinen zweiten SHIM-/LED-Test parallel starten. Rücknahme: `PTT_BUTTON_SHIM=0`, Dienst neu starten. GPIO17 und STT-Konfiguration bleiben verwendbar.

## Abnahme

Rückmeldungen am 05.10.2026, Journalzeiten CEST:

| Prüfung | Tatsächlicher Beleg / noch offen |
|---|---|
| Initialisierung | Nach `modprobe i2c-dev`: 21:28:46 shim_ready, Bus 1/0x3f; erneut 21:42:18 |
| Release → Vosk | Clips 1,50 / 1,38 s liefern tester/testers; weitere Transkripte ja/nix kann man machen. A gegenüber GPIO17 nicht identifiziert |
| B | cancelled-Ereignisse; aktive Aufnahme/STT waren dabei nicht nachweislich im Gang |
| C/D | ursprünglich volume down/up, step=5, ohne mixer_error. Seit 08.10.2026 2-dB-Schritte mit Wiederholung; am Mixer geprüft (−39 → −37 → −39 dB), Tastenbedienung selbst noch nicht erneut abgenommen |
| E | Zunächst No module named piper; nach GitHub-TTS-Deployment status 21:43:05 → speech_finished 21:43:29, Exitcode 0. Nutzer meldet langsame Sprachausgabe. Exakte LED-Farbe und B während Ansage offen |
| Reboot | Frühere zusammenfassende Neustartbestätigung betrifft die Einzeltest-Phase. Reboot der aktuellen SHIM-/TTS-Dienstversion nicht belegt |

Weiter prüfen: A vs. GPIO17 und beide gemeinsam; B während Aufnahme, STT und Ansage; PTT während Ansage; E während Aufnahme; tatsächliche LED-Zustände, C/D-Pegel, Stop/Neustart. Kurze Clips um 0,13/0,38 s ohne Transcript widerlegen nicht die später erfolgreiche Transkription.

Für reine Probe den Dienst stoppen, in derselben Shell `/etc/pi-ptt.env` laden und `ptt.py --probe` ausführen wie im [Setup](setup.md#5-taste-prüfen-und-starten). Probe initialisiert den Expander und setzt LED aus, führt aber keine Aufnahme/STT/Mixer-/Sprachausgabe aus. Anschließend den Dienst wieder starten.

Tests simulieren kombinierte Tasten, B-Verwerfung/Slot-Sperre, Sprach-Prozessgruppe und LED-Datenformat. Sie ersetzen keine Hardware-Abnahme. [Performance der Statusansage](local-speech.md#performance).
