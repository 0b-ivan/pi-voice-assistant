# Button SHIM im Sprachdienst

Stand 05.10.2026: Alle fünf Tasten in zwei Einzeltests bestanden; RGB-Farbtest vom Nutzer bestätigt. Dienstintegration implementiert und lokal automatisiert geprüft. Die gemeinsame Hardware-Abnahme dieser neuen Version steht aus. Bisherige Belege: [hardware-bring-up.md](hardware-bring-up.md).

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
| Start mit losgelassenen Tasten | shim_ready, grüne LED | Offen |
| A halten, deutschen Satz sprechen, loslassen | Rot → Blau → Grün; transcript mit provider=vosk | Offen |
| GPIO17; dann A/GPIO17 gemeinsam halten | Aufnahme endet erst nach beiden Releases | Offen |
| B während Aufnahme bei noch gehaltenem A | Aufnahme verworfen; keine Wiederholung | Offen |
| B während STT | Kein transcript des verworfenen Clips; danach neue Aufnahme möglich | Offen |
| C einmal, D einmal | Playback sinkt/steigt; Speaker und Eingangspegel unverändert | Offen |
| E, danach B | Hörbare Ansage/türkise LED; B stoppt sie | Offen, lokale TTS erforderlich |
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
