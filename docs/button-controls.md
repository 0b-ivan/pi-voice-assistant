# Button SHIM im Sprachdienst

Stand 05.10.2026: Alle fünf Tasten in zwei Einzeltests bestanden; RGB-Farbtest vom Nutzer bestätigt. Dienstintegration implementiert und lokal automatisiert geprüft. Auf pi-assistent sind die SHIM-Initialisierung nach manuellem Laden von i2c-dev und zwei Aufnahme→Vosk-Durchläufe bestätigt. Die verwendete Aufnahmetaste und die sichtbaren LED-Zustände sind noch nicht separat zurückgemeldet; B–E und die Neustart-Abnahme stehen aus. Bisherige Belege: [hardware-bring-up.md](hardware-bring-up.md).

## Bedienung

| Taste | Funktion |
|---|---|
| A | Halten: aufnehmen. Loslassen: transkribieren. |
| B | Dienstansage stoppen; Aufnahme oder laufendes STT-Ergebnis verwerfen. |
| C | Digitalen WM8960-Playback-Pegel um 5 Prozentpunkte senken. |
| D | Digitalen WM8960-Playback-Pegel um 5 Prozentpunkte erhöhen. |
| E | Bereitschaft bzw. laufende Verarbeitung und konfigurierten STT-Modus ansagen. |
| WM8960 BUTTON / GPIO17 | Weiterhin halten zum Aufnehmen. |

A und GPIO17 bilden zusammen einen Aufnahmetaster: Bei gleichzeitigem Halten endet die Aufnahme erst, wenn beide losgelassen sind. 40 ms Entprellung und das bisherige 30-s-Limit gelten weiterhin. B–E lösen einmal pro Druck aus, ohne Wiederholung beim Halten. Beim Dienststart gehaltene Tasten müssen erst losgelassen werden.

| RGB | Zustand |
|---|---|
| Grün | Bereit; beim Start erst alle Tasten loslassen. |
| Rot | Aufnahme läuft. |
| Blau | STT läuft, auch nach B bis der verworfene Auftrag beendet ist. |
| Türkis | Vom Dienst gestartete Statusansage läuft. |
| Aus | Dienst beendet bzw. reine Tastenprobe. |

Es gibt weiterhin einen Aufnahmeslot. STT läuft im Hintergrundthread; die Tasten bleiben bedienbar und das Vosk-Modell bleibt im Prozess geladen. Ein nativer Vosk-Aufruf wird durch B nicht gewaltsam unterbrochen: Sein Ergebnis wird verworfen, der Slot bleibt bis zum Abschluss belegt. Währenddessen startet A/GPIO17 keine neue Aufnahme. Danach gehaltene Tasten müssen erst losgelassen werden.

PTT beendet eine Dienstansage vor dem Aufnahmestart. E spricht während einer Aufnahme nicht; B hat bei gleichzeitigen Tastendrücken Vorrang. B beendet nur vom Dienst gestartete Sprachprozesse samt Wiedergabe-Kindprozess. Externe manuelle `aplay`-Sitzungen werden nicht verwaltet.

C/D ändern ausschließlich `amixer -c wm8960soundcard sset Playback 5%-/5%+`. ALSA begrenzt den Pegel auf den gültigen Bereich. Speaker, Speaker AC/DC und Mikrofonpegel bleiben bei den bestehenden Einstellungen. Die Änderung bleibt zunächst im laufenden ALSA-Mixer; der Dienst speichert keinen neuen Boot-Pegel.

## Installation auf pi-assistent

Im Repository-Checkout der Feature-Version bzw. nach Merge auf main:

```bash
sudo apt install python3-smbus i2c-tools
sudo bash scripts/install-voice-service.sh
sudo vim /etc/pi-ptt.env
```

Vorhandene Konfigurationen werden vom Installer erhalten. In `/etc/pi-ptt.env` folgende Einträge ergänzen bzw. vorhandene Zeilen ändern:

```text
PTT_BUTTON_SHIM=1
PTT_SPEAK_COMMAND="/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py"
```

Mit Esc, `:wq`, Enter speichern. `/etc/pi-voice-assistant.env` bleibt unverändert; auf diesem Pi bleibt insbesondere `STT_PROVIDER=vosk` erhalten.

`speak.py` ist die Schnittstelle zur separat eingerichteten TTS: Der Befehl erhält den ganzen deutschen Text als ein zusätzliches Argument. Es wird keine Shell gestartet. Der Installer erstellt oder überschreibt kein `speak.py` und installiert keinen zweiten TTS-Motor. Falls die lokale TTS ein venv benötigt, den Interpreter in `PTT_SPEAK_COMMAND` entsprechend setzen. Modelle und Programme müssen für obivan lesbar unter `/opt` oder einem anderen erlaubten Pfad liegen; `ProtectHome=yes` bleibt aktiv. Ohne funktionsfähige TTS erzeugt E `speech_error`; A–D bleiben verfügbar.

Die neue Unit ergänzt `i2c` zu den Gruppen `audio` und `gpio`. Der vorhandene I²C-Bus 1 und `i2c-dev` müssen verfügbar sein. Die zuvor eingerichtete `/etc/modules-load.d/pi-voice-i2c.conf` bleibt bestehen. Kein Audio-Overlay-Wechsel.

```bash
sudo systemctl restart pi-ptt.service
sudo systemctl status pi-ptt.service --no-pager
sudo journalctl -u pi-ptt.service -n 30 --no-pager
sudo journalctl -u pi-ptt.service -f
```

Erwartet: `shim_ready`, Bus 1, Adresse 0x3f; anschließend grüne LED. Keinen zweiten Button-/LED-Test gleichzeitig starten: Der Dienst besitzt die Expander-Konfiguration. Der kleine synchrone Treiber verwendet die Pinbelegung der [Pimoroni-Bibliothek 0.0.2](https://github.com/pimoroni/button-shim/blob/master/library/buttonshim/__init__.py) (A–E Bits 0–4, LED-Clock Bit 6, LED-Data Bit 7). LED-Schreibzugriffe erfolgen nur bei Zustandswechseln. Die tatsächlichen Farben und Reaktionszeiten dieser Implementierung gehören zur Hardware-Abnahme.

Wenn der SHIM beim Start fehlt oder später ein I²C-Fehler auftritt, wird `shim_error` geloggt und GPIO17 bleibt nutzbar. Bei Ausfall während des Betriebs wird der aktuelle Vorgang verworfen. Nach Beheben des Fehlers Dienst neu starten, um den SHIM erneut zu öffnen. Keine automatische Bus-Suche oder Dauer-Neustarts.

Reine Probe bei gestopptem Dienst, ohne STT, Wiedergabe und Mixeränderungen:

```bash
sudo systemctl stop pi-ptt.service
sudo -u obivan env PTT_BUTTON_SHIM=1 /usr/bin/python3 /opt/pi-voice-assistant/src/ptt.py --probe
```

Die Probe initialisiert den Expander und setzt die LED aus. Mit Ctrl-C beenden, danach `sudo systemctl start pi-ptt.service`. Das ursprüngliche lesende `scripts/test-button-shim.py` bleibt für die unabhängige Einzelprüfung verfügbar.

## Abnahme der Dienstintegration

| Prüfung | Erwartung | Status |
|---|---|---|
| Start mit losgelassenen Tasten | shim_ready, grüne LED | shim_ready am 05.10.2026 21:28:46 CEST bestätigt; Start-Tastenstellung und LED-Farbe nicht separat bestätigt |
| A halten, deutschen Satz sprechen, loslassen | Rot → Blau → Grün; transcript mit provider=vosk | Zwei erfolgreiche Release→Vosk-Durchläufe bestätigt; A gegenüber GPIO17 und LED-Farben noch nicht bestätigt |
| GPIO17; dann A/GPIO17 gemeinsam halten | Aufnahme endet erst nach beiden Releases | Offen |
| B während Aufnahme bei noch gehaltenem A | Aufnahme verworfen; keine Wiederholung | B erzeugt cancelled; Abbruch während laufender Aufnahme noch nicht belegt |
| B während STT | Kein transcript des verworfenen Clips; danach neue Aufnahme möglich | Offen |
| C einmal, D einmal | Playback sinkt/steigt; Speaker und Eingangspegel unverändert | volume down/up, step=5, ohne mixer_error belegt; tatsächliche Pegel und Analogwerte noch nicht ausgelesen |
| E, danach B | Hörbare Ansage/türkise LED; B stoppt sie | E startet Statusaufruf; speech_error: System-Python enthält kein piper. Hörprüfung und B während Ansage offen |
| A während Statusansage | Ansage endet vor Aufnahme | Offen |
| E während Aufnahme | Keine Statuswiedergabe in Mikrofonaufnahme | Offen |
| Stop und Neustart | LED aus bei Stop; danach Tasten und Audio wieder nutzbar | Offen |

Zum Auslesen nach dem Lautstärketest:

```bash
amixer -c wm8960soundcard sget Playback
amixer -c wm8960soundcard sget Speaker
amixer -c wm8960soundcard sget 'Speaker AC'
```

Lokale Tests prüfen Entprellung, kombinierte PTT-Eingänge, B-Abbruch samt Ergebnisverwerfung und Slot-Sperre, Bedienung während STT, Probe ohne Audio, STT-Fehler, Wiedergabe-Prozessgruppe und LED-Datenformat. Sie ersetzen die Hardware-Abnahme weder für I²C/LED noch für ALSA-Pegel oder lokale TTS.

Rücknahme: In Vim `PTT_BUTTON_SHIM=0` setzen und den Dienst neu starten. GPIO17 und die bestehende STT-Konfiguration bleiben nutzbar.

## Rückmeldung vom Pi — 05.10.2026

Quelle: vom Nutzer eingereichte Terminalausgabe. Ziel pi-assistent, Benutzer obivan. Zeiten aus dem Journal in CEST. Installation aus dem separaten Git-Worktree `~/pi-voice-button-test` auf Commit `a7ad3bd`; der ältere Checkout `~/pi-voice-ptt-test` enthält vorgemerkte Vosk-Änderungen und blieb erhalten.

- Die neue Unit `WM8960 voice controls with optional Button SHIM` startete um 21:27:06 und 21:27:39. Der SHIM meldete zunächst `[Errno 2] No such file or directory`; GPIO17-Fallback und PTT-Schleife blieben aktiv.
- Nach `sudo modprobe i2c-dev` existierte `/dev/i2c-1`. Die unmittelbar folgende Ausgabe zeigte `crw------- root root`; daraus wird kein endgültiger Rechtezustand nach Abschluss von udev abgeleitet. `i2cdetect -l` meldete Bus 1 und Bus 2 (bcm2835). Ein eventueller Rechtefehler ist im nachfolgenden Dienststart nicht beobachtet worden.
- Nach Dienstneustart bestätigte Prozess 2191 um 21:28:46 `shim_ready` auf Bus 1, Adresse 0x3f. Damit ist der zuvor fehlende I²C-Zugriff in dieser Sitzung wiederhergestellt.
- Erster erfolgreicher Durchlauf: recording 21:28:59; capture_ready/release und processing 21:29:01; WAV PCM S16_LE, 48 kHz, Stereo, 72016 Frames (rund 1,50 s). transcript 21:29:13: `tester`, provider `vosk`.
- Zweiter erfolgreicher Durchlauf: recording 21:29:28; capture_ready/release und processing 21:29:29; gleiches WAV-Format, 66016 Frames (rund 1,38 s). transcript 21:29:32: `testers`, provider `vosk`.
- Vor der I²C-Wiederherstellung wurde ein sehr kurzer Clip mit 18016 Frames (rund 0,38 s) aufgenommen; Vosk meldete dafür `Vosk returned no transcript`. Die späteren Durchläufe belegen die Wiederherstellung der Transkription. Gesprochener Solltext, Erkennungsgenauigkeit und Modell-Ladezeit sind in dieser Rückmeldung nicht separat belegt.
- Der Nutzer öffnete anschließend `/etc/modules-load.d/pi-voice-i2c.conf` in Vim. Gespeicherter Inhalt und erfolgreicher Dienststart nach einem erneuten Reboot sind noch nicht durch diese Ausgabe bestätigt.

Die Ereignisse identifizieren derzeit nicht, ob A oder GPIO17 die zwei erfolgreichen Aufnahmen ausgelöst hat. Sichtbare LED-Farben, Abbruch durch B, Lautstärkeregelung C/D und Statusansage E bleiben deshalb als separate Hardwareprüfungen offen.

## Weitere Tastenprüfung — 05.10.2026, 21:32–21:35 CEST

Die neue Nutzer-Ausgabe zeigt weitere erfolgreiche Release→Vosk-Durchläufe (`ja` um 21:32:20; `nix kann man machen` um 21:33:41). Eine weitere Aufnahme mit 6024 Frames (rund 0,13 s) endet um 21:34:41 mit `Vosk returned no transcript`.

- B: vier `cancelled`-Ereignisse zwischen 21:34:43 und 21:34:45. Damit ist die Abbruchaktion über den SHIM ausgelöst. Der vorherige STT-Auftrag war um 21:34:41 bereits beendet; diese Ausgabe belegt deshalb keinen Abbruch während einer laufenden Aufnahme oder STT-Verarbeitung und keine Release-Sperre bei weiter gehaltenem A.
- C/D: zahlreiche `volume`-Ereignisse mit `direction=down/up`, `step=5` zwischen 21:34:46 und 21:34:56. Kein `mixer_error` in der eingereichten Ausgabe; die amixer-Aufrufe wurden erfolgreich ausgeführt. Tatsächlicher Playback-Pegel, analoge Speaker-Werte und Zahl der physischen Betätigungen sind nicht mitgeliefert.
- E: vier Statusaufrufe zwischen 21:34:57 und 21:35:00 mit `Ich bin bereit. Offline-Spracherkennung.`. Die lokale speak.py startet jeweils `/usr/bin/python3 -m piper`; dieser Interpreter meldet `No module named piper`. Der Dienst protokolliert jeweils `speech_error` mit returncode 1 und bleibt bedienbar. Dies ist ein Interpreter-/TTS-Abhängigkeitsfehler, keine bestätigte Statuswiedergabe.

Der aktuelle TTS-Branch aus PR #14 verwendet `/opt/pi-voice-assistant/.venv/bin/python` als Piper-Interpreter und unterstützt `PIPER_PYTHON`. Bei der lokalen älteren speak.py muss der Piper-Unterprozess ebenfalls mit diesem venv-Interpreter gestartet werden. Importprüfung und hörbare Ansage unter dem Dienst stehen nach dieser Korrektur noch aus. Es wurde keine globale Installation von Piper empfohlen.

LED-Sichtprüfung, Identifizierung von A gegenüber GPIO17, aktiver B-Abbruch, tatsächliche Lautstärkewerte sowie Dienststart nach Reboot bleiben offen. Mehrere Ereignisse in einer Sekunde sind ohne bestätigte Anzahl/Art der physischen Tastendrücke kein Nachweis für oder gegen wiederholtes Auslösen beim Halten.
