# Button SHIM im Sprachdienst

Tasten/RGB sind auf `main` implementiert. Einzeltest A–E zweimal bestanden, Hersteller-LED-Test per Nutzer-Sichtprüfung bestätigt. Die neuere Dienstversion initialisiert SHIM und transkribiert auf dem Pi; vollständige Abnahme der integrierten Steuerung bleibt offen.

## Bedienung

| Taste | Funktion |
|---|---|
| A | Halten: aufnehmen; Loslassen: STT |
| GPIO17 / WM8960 BUTTON | Gleiche PTT-Funktion, parallel zu A |
| B | Eigene Dienstansage stoppen, Aufnahme/STT-Ergebnis verwerfen |
| C / D | Digitalen `Playback`-Pegel um 5 Prozentpunkte senken/erhöhen |
| E | Dynamischen Systemstatus ansagen: Systemlast, Temperatur, freier RAM/Datenspeicher, Laufzeit, STT-Modus und Servitor-Zustand |

A und GPIO17 bilden gemeinsam einen Aufnahmetaster: Aufnahme endet erst, wenn beide losgelassen sind. Alle Tasten beim Start loslassen; B–E lösen einmal pro Druck aus. B hat bei gleichzeitigen Aktionen Vorrang.

STT läuft im Hintergrund. B verwirft dessen Ergebnis, bricht den nativen Vosk-Aufruf aber nicht ab. Der Slot bleibt bis zum Abschluss belegt, danach braucht gehaltenes PTT Release. PTT stoppt eine vom Dienst gestartete Statusansage vor der Aufnahme; E spricht nicht während Aufnahme. Die Statusansage wird beim Druck neu aus `/proc`, `/sys` und dem Dateisystem aufgebaut; die 1-Minuten-Load wird auf die CPU-Kernzahl normiert und als `SYSTEMLAST` gesprochen. Eigene Wiedergabe läuft als verwalteter `aplay`- oder FFmpeg-Prozess; externe Audioprozesse werden nicht verwaltet.

C/D ändern nur den digitalen `Playback`-Regler (0,5-dB-Raster, 255 = 0 dB), nicht Speaker, Speaker AC/DC oder Mikrofonpegel: **2 dB pro Druck** (`TTS_VOLUME_STEP_DB`) im Bereich −60 bis 0 dB. Gehalten wiederholt sich der Schritt nach 0,45 s alle 0,15 s. Früher waren es „5 %“ der Rohwerte, also rund 6,4 dB pro Druck, was hörbar sprang. Ein bereits tiefer eingestellter Pegel wird beim Leiserstellen nicht auf −60 dB angehoben. Das PiTFT zeigt 2,5 s lang „LAUTSTÄRKE“ mit Balken (Prozent des Bereichs −60…0 dB, an den Grenzen MIN/MAX). Änderungen werden vom Dienst nicht für den nächsten Boot gespeichert.

| RGB im Code | Zustand |
|---|---|
| Grün | Bereit, auch beim anfänglichen Warten auf Release |
| Rot | Aufnahme |
| Rot ↔ Gelb, klar blinkend | STT/Verarbeitung bzw. späteres „Nachdenken“ |
| Türkis ↔ Orange | Sprachausgabe: Türkis in Pausen, Orange während Sprachsegmenten |
| Aus | Dienst beendet oder reine Probe |

Die Sprachfarbe folgt der Piper-Chunk-Timeline: Satzpausen bleiben Türkis, Sprachsegmente blenden weich Richtung Orange. Verarbeitung blinkt unabhängig davon klar zwischen Rot und Gelb (500 ms pro Farbe). Die tatsächliche optische Wirkung auf der Hardware muss noch abgenommen werden. Kein eigener Fehler-/Offline-LED-Zustand implementiert.

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
