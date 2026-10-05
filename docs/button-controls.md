# Button SHIM im Sprachdienst

Tasten/RGB sind auf `main` implementiert. Einzeltest A–E zweimal bestanden, Hersteller-LED-Test per Nutzer-Sichtprüfung bestätigt. Die neuere Dienstversion initialisiert SHIM und transkribiert auf dem Pi; vollständige Abnahme der integrierten Steuerung bleibt offen.

## Bedienung

| Taste | Funktion |
|---|---|
| A | Halten: aufnehmen; Loslassen: STT |
| GPIO17 / WM8960 BUTTON | Gleiche PTT-Funktion, parallel zu A |
| B | Eigene Dienstansage stoppen, Aufnahme/STT-Ergebnis verwerfen |
| C / D | Digitalen `Playback`-Pegel um 5 Prozentpunkte senken/erhöhen |
| E | Bereitschaft/Verarbeitung und konfigurierten STT-Modus ansagen; separate TTS erforderlich |

A und GPIO17 bilden gemeinsam einen Aufnahmetaster: Aufnahme endet erst, wenn beide losgelassen sind. Alle Tasten beim Start loslassen; B–E lösen einmal pro Druck aus. B hat bei gleichzeitigen Aktionen Vorrang.

STT läuft im Hintergrund. B verwirft dessen Ergebnis, bricht den nativen Vosk-Aufruf aber nicht ab. Der Slot bleibt bis zum Abschluss belegt, danach braucht gehaltenes PTT Release. PTT stoppt eine vom Dienst gestartete Statusansage vor der Aufnahme; E spricht nicht während Aufnahme. Externe `aplay`-Prozesse werden nicht verwaltet.

C/D ändern `amixer ... sset Playback 5%-/5%+`, nicht Speaker, Speaker AC/DC oder Mikrofonpegel. Änderungen werden vom Dienst nicht für den nächsten Boot gespeichert.

| RGB im Code | Zustand |
|---|---|
| Grün | Bereit, auch beim anfänglichen Warten auf Release |
| Rot | Aufnahme |
| Blau | STT, auch nach B bis Auftragsende |
| Türkis | Eigene Statusansage |
| Aus | Dienst beendet oder reine Probe |

Die tatsächlichen Dienstfarben/Reaktionszeiten sind noch nicht vollständig auf Hardware bestätigt. Kein roter Fehler-/Offline-LED-Zustand implementiert.

## Aktivieren

Vollständigen [Dienst installieren](setup.md#4-repository-und-dienst-installieren), [SHIM einzeln prüfen](hardware-bring-up.md), `i2c-dev` verfügbar machen. Mit Vim vorhandene `/etc/pi-ptt.env` ergänzen/ändern:

```text
PTT_BUTTON_SHIM=1
PTT_SPEAK_COMMAND="/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py"
```

```bash
sudo systemctl restart pi-ptt.service
journalctl -u pi-ptt.service -n 30 --no-pager
```

Erwartet: `shim_ready`, Bus 1, `0x3f`. Der Dienst nutzt die Gruppe `i2c`. Bei I²C-Fehler wird SHIM bis zum Neustart deaktiviert; GPIO17 bleibt nutzbar. Beim späteren Busausfall wird der aktuelle Vorgang verworfen.

Der Sprachbefehl erhält den gesamten Text als ein zusätzliches Argument ohne Shell. Auf `main` wird `speak.py` weder mitgeliefert noch überschrieben; für E ist die [separate TTS aus PR #14](local-speech.md) nötig. Der Wrapper startet Piper mit seinem venv-Interpreter. Ein alter lokaler Wrapper mit System-Python erzeugte `No module named piper`; die später installierte GitHub-Version erreicht `speech_finished` mit Exitcode 0.

Keinen zweiten SHIM-/LED-Test parallel starten. Rücknahme: `PTT_BUTTON_SHIM=0`, Dienst neu starten. GPIO17 und STT-Konfiguration bleiben verwendbar.

## Abnahme

Rückmeldungen am 05.10.2026, Journalzeiten CEST:

| Prüfung | Tatsächlicher Beleg / noch offen |
|---|---|
| Initialisierung | Nach `modprobe i2c-dev`: 21:28:46 shim_ready, Bus 1/0x3f; erneut 21:42:18 |
| Release → Vosk | Clips 1,50 / 1,38 s liefern tester/testers; weitere Transkripte ja/nix kann man machen. A gegenüber GPIO17 nicht identifiziert |
| B | cancelled-Ereignisse; aktive Aufnahme/STT waren dabei nicht nachweislich im Gang |
| C/D | volume down/up, step=5, ohne mixer_error; tatsächliche Pegel/Analogwerte noch nicht gemeinsam ausgelesen |
| E | Zunächst No module named piper; nach GitHub-TTS-Deployment status 21:43:05 → speech_finished 21:43:29, Exitcode 0. Nutzer meldet langsame Sprachausgabe. Exakte LED-Farbe und B während Ansage offen |
| Reboot | Frühere zusammenfassende Neustartbestätigung betrifft die Einzeltest-Phase. Reboot der aktuellen SHIM-/TTS-Dienstversion nicht belegt |

Weiter prüfen: A vs. GPIO17 und beide gemeinsam; B während Aufnahme, STT und Ansage; PTT während Ansage; E während Aufnahme; tatsächliche LED-Zustände, C/D-Pegel, Stop/Neustart. Kurze Clips um 0,13/0,38 s ohne Transcript widerlegen nicht die später erfolgreiche Transkription.

Für reine Probe den Dienst stoppen, in derselben Shell `/etc/pi-ptt.env` laden und `ptt.py --probe` ausführen wie im [Setup](setup.md#5-taste-prüfen-und-starten). Probe initialisiert den Expander und setzt LED aus, führt aber keine Aufnahme/STT/Mixer-/Sprachausgabe aus. Anschließend den Dienst wieder starten.

Tests simulieren kombinierte Tasten, B-Verwerfung/Slot-Sperre, Sprach-Prozessgruppe und LED-Datenformat. Sie ersetzen keine Hardware-Abnahme. [Performance der Statusansage](local-speech.md#performance).
