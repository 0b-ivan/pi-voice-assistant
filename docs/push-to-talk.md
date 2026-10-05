# WM8960 Push-to-Talk

Stand 05.10.2026: Implementierung und automatisierte Tests vorhanden. Installation, Polarität, Entprellung und PTT-Audio auf dem echten Pi **noch nicht abgenommen**. Die zuvor bestätigte allgemeine Audio-Neustartprüfung steht in [setup.md](setup.md).

## Plan und Hardwarebeleg

1. GPIO-Chip, Leitung und Polarität zunächst ohne Audio prüfen.
2. Gedrückt halten startet eine Aufnahme; Loslassen beendet sie. Das ersetzt den früheren Toggle-Entwurf.
3. Danach systemd installieren und Aufnahme, Fehlerfälle und Neustart prüfen.
4. Später STT und OpenRouter sowie deutsche TTS anschließen; in diesem Schritt keine Netzwerkanfrage, Schlüssel oder Sprachausgabe implementieren.

Das [Waveshare-Wiki](https://www.waveshare.com/wiki/WM8960_Audio_HAT) ordnet `BUTTON` ausdrücklich **P17 / BCM GPIO17** zu, am Pi physischer Pin 11. Auch die vorhandene [Hardwaredokumentation](hardware.md) nennt diese Belegung. Der [Herstellerschaltplan](https://files.waveshare.com/upload/f/fa/WM8960_Audio_HAT_Schematic.pdf), Seite 1, zeigt K1, P17, R1 4,7 kΩ, 3V3 und GND. Voreinstellung: aktiv Low mit Pull-up; die tatsächliche Polarität des montierten HATs vor Betrieb mit `--probe` bestätigen. Kein Treiberwechsel nötig.

BCM17 ist auf dem Zero 2 W normalerweise Offset 17 am Haupt-GPIO-Chip. `/dev/gpiochip0` ist eine konfigurierbare Vorgabe, kein geprüfter Wert dieses Systems. `gpiodetect`/`gpioinfo` müssen den Chip und die Leitung bestätigen. GPIO17 darf nicht bereits von einem anderen Dienst belegt sein. I²C GPIO2/3 und I²S GPIO18–21 bleiben für das Audio-HAT; Kamera und mini PiTFT liegen derzeit separat.

## Verhalten und Grenzen

`src/ptt.py` verwendet die libgpiod-Python-API v2, die [Debian 13 als python3-libgpiod bereitstellt](https://packages.debian.org/trixie/python/python3-libgpiod). Keine RPi.GPIO-/sysfs-Abhängigkeit und keine pip-Installation erforderlich. Die Bibliothek wird nur beim Start des Hardwaredienstes importiert; die Zustands- und Aufnahmetests laufen ohne GPIO-Hardware.

- Abfrage alle 10 ms; ein Pegel muss 40 ms stabil bleiben. Entprellung gilt für Drücken und Loslassen. Sehr kurze Tastendrücke können bewusst entfallen.
- Beim Dienststart muss die Taste zunächst stabil losgelassen sein. Eine beim Boot gehaltene Taste startet keine Aufnahme.
- `arecord` nimmt WAV / PCM S16_LE, 48 kHz, Stereo über `plughw:CARD=wm8960soundcard,DEV=0` auf, entsprechend dem bestätigten manuellen Mikrofontest. Keine Mixeränderungen durch den Dienst.
- Standardlimit 30 Sekunden (etwa 5,76 MB Audionutzdaten). Einstellbar 1–120 Sekunden. Zusätzlich begrenzt `arecord -d` die Aufnahme; damit schützt auch ein blockierter GPIO-Ablauf vor endlosem Audio.
- Nach Zeitlimit oder Fehler während gehaltenem Taster muss zuerst losgelassen werden. Keine wiederholten Aufnahmen bei dauerhaft gedrückter Taste.
- Loslassen sendet SIGINT an `arecord` und wartet höchstens zwei Sekunden. arecord schreibt zunächst rohe PCM-Daten; der Dienst erzeugt und prüft anschließend selbst das WAV (Mindestlänge 100 ms, vollständige Stereoframes, begrenzte Größe). Damit hängt der WAV-Header nicht von der ALSA-Signalbehandlung ab. Bei selbst angefordertem Stop werden Exitstatus 0, 1 (unterbrochener ALSA-Leseaufruf) oder SIGINT akzeptiert, wenn gültige PCM-Daten vorhanden sind; ungeplantes Prozessende mit Fehler wird verworfen. Hängende Prozesse werden beendet. Siehe [ALSA-Quellcode: Signalhandler und pcm_read](https://github.com/alsa-project/alsa-utils/blob/master/aplay/aplay.c).
- SIGTERM/SIGINT beendet die laufende Aufnahme und löscht die Audiodateien. `systemd` startet den Dienst bei Prozess-/GPIO-Ausfall neu, mit begrenzter Neustartfrequenz. Aufnahmefehler erscheinen als JSON-Ereignis im Journal; danach ist eine neue Aufnahme möglich.

## Installation auf pi-assistent

Zuerst PR #3 integrieren, dann diesen PTT-Branch; der PTT-PR basiert auf dem Dokumentationsbranch. Im Repository auf dem Pi:

```bash
sudo apt update
sudo apt install python3 python3-libgpiod gpiod alsa-utils vim
gpiodetect
gpioinfo
id obivan
ls -l /dev/gpiochip* /dev/snd
/usr/bin/python3 -c "import gpiod; print(gpiod.__version__); assert hasattr(gpiod, 'request_lines')"
```

Chip mit den BCM-Leitungen auswählen und Offset 17 prüfen. Erwartet ist ein freier Eingang; keine belegte Leitung erzwingen. Falls `gpioinfo` wegen Berechtigungen scheitert, einmal lesend `sudo gpioinfo` verwenden. Für den Dienst benötigen `obivan` bzw. seine Zusatzgruppen Zugriff auf Audio und den gewählten GPIO-Chip. Raspberry Pi OS verwendet gewöhnlich `audio` und `gpio`; vor Installation der Unit deren Existenz und Geräterecht prüfen. Die Unit setzt diese Gruppen selbst. Bei anderem Benutzer `User=` und gegebenenfalls Gruppen mit Vim anpassen.

```bash
sudo install -d -m 0755 /opt/pi-voice-assistant/src
sudo install -m 0644 src/ptt.py /opt/pi-voice-assistant/src/ptt.py
sudo install -m 0644 config/ptt.env.example /etc/pi-ptt.env
sudo vim /etc/pi-ptt.env
```

`PTT_GPIO_CHIP` und `PTT_GPIO_LINE` anhand der Diagnose setzen. Alle Änderungen mit **Vim**: `i`, bearbeiten, Esc, `:wq`, Enter. Den PTT-Dienst für Probe und manuelle Audiotests gestoppt lassen; GPIO und Aufnahmegerät werden exklusiv benutzt.

In der SSH-Shell als `obivan` die mitgelieferte vertrauenswürdige Konfiguration laden, danach **nur die Taste prüfen**:

```bash
set -a
. /etc/pi-ptt.env
set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/ptt.py --probe
```

Erwartung: Losgelassen kein `start`; beim Drücken genau ein `button/start`, beim Loslassen genau ein `button/release`. Bei falscher Polarität mit Ctrl-C beenden und `PTT_ACTIVE_LOW` mit Vim korrigieren (0 = aktiv High / Pull-down). Werte erneut laden und Probe wiederholen. Die GPIO-Leitung wird beim Beenden freigegeben. Probe zeigt auch `limit`, schreibt aber keine Audiodatei.

Erst nach erfolgreicher Probe den Dienst installieren:

```bash
sudo install -m 0644 deploy/pi-ptt.service /etc/systemd/system/pi-ptt.service
sudo vim /etc/systemd/system/pi-ptt.service
sudo systemd-analyze verify /etc/systemd/system/pi-ptt.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-ptt.service
systemctl status pi-ptt.service --no-pager
journalctl -u pi-ptt.service -f
```

Ctrl-C beendet nur die Journalanzeige. Der Dienst verwendet `obivan` mit Audio-/GPIO-Gruppen, benötigt kein root und schreibt nur nach `/run/pi-ptt`. `ProtectHome` verhindert Abhängigkeit vom Benutzer-Checkout; darum liegt der installierte Code unter `/opt`. Nach Änderungen an der Konfiguration: `sudo systemctl restart pi-ptt.service`. Dienst entfernen/deaktivieren: `sudo systemctl disable --now pi-ptt.service`; installierte Dateien bei Bedarf entfernen. Das WM8960-Overlay und ALSA bleiben davon unabhängig.

## Schnittstelle für spätere Sprachverarbeitung

Rohe Daten liegen während der Aufnahme in `capture.part.pcm`; beim Verpacken entsteht kurzzeitig zusätzlich das WAV (zusammen höchstens etwa doppelte Audio-Nutzdatengröße). Nach Fertigstellung werden die Rohdaten gelöscht. Eine vollständige Aufnahme wird atomar von `capture.part.wav` nach `/run/pi-ptt/capture.wav` umbenannt. Erst danach erscheint eine JSON-Zeile auf stdout bzw. im Journal:

```json
{"version":1,"event":"capture_ready","path":"/run/pi-ptt/capture.wav","reason":"release","format":"wav","encoding":"PCM_S16_LE","sample_rate":48000,"channels":2,"frames":96000}
```

`reason` ist `release`, `limit` oder `process_exit` (natürliches arecord-Zeitlimit). `frames` bestimmt die Dauer. Ereignisse `waiting_for_release`, `recording`, `button` (Probe) und `error` dienen der Diagnose. Kein Audioinhalt wird ins Journal geschrieben.

Es gibt **einen** lokalen Aufnahmeslot. Die Datei bleibt bis zum nächsten Aufnahmestart, Dienststop oder Reboot verfügbar; keine unbegrenzte Warteschlange und keine dauerhafte Speicherung. Ein künftiger Adapter wird direkt nach erfolgreichem `finish` aufgerufen und übernimmt das WAV vor der nächsten Aufnahme. Vor dem Anschluss muss eine Processing-/Playback-Sperre samt konsumiertem Dateiinhalt ergänzt werden; Journal-Tailing ist keine verlässliche Transport-API. STT erhält Audio, danach OpenRouter Text; deutsche TTS liefert später Wiedergabeaudio. Netzwerkvertrag, Authentifizierung, Timeouts und Downmix/Resampling gehören in diesen Adapter. Gegenwärtig existieren weder STT, OpenRouter, TTS noch automatische Wiedergabe.

## Validierung

Automatisiert lokal am 05.10.2026: `python3 -m unittest discover -s tests -v` — 12 Tests bestanden. Prüft Entprellung, Start mit gehaltenem Taster, Zeitlimit ohne Wiederholung, Wiederfreigabe nach Fehler, arecord-Aufruf, SIGINT/WAV-Übergabe einschließlich unterbrochenem ALSA-Leseaufruf, Fehler/Leeraufnahme, unvollständige PCM-Frames, natürliches Aufnahmeende, Stop-Bereinigung und erzwungenes Beenden eines hängenden Recorders. Prozess und Audio sind im Test simuliert; dies bestätigt weder libgpiod-Geräterechte noch reale ALSA-Signalbehandlung. CI führt dieselben Tests bei Pull Requests aus. Python-Syntaxprüfung bestanden. systemd-Prüfung auf dem Ziel-Pi noch offen (lokale Arbeitsumgebung macOS).

Auf dem Pi folgende Ergebnisse mit Datum, Kernel, `dpkg-query -W python3-libgpiod gpiod alsa-utils`, GPIO-Chip/Offset, Polarität und Konfiguration protokollieren:

| Prüfung | Erwartung | Status |
|---|---|---|
| Probe: 20 normale Drück-/Loslasszyklen | Genau ein Start und ein Ende je Zyklus | Offen |
| Probe: kurze/prellende Berührungen | Keine Mehrfachstarts | Offen |
| Start bei gehaltenem Taster | Aufnahme erst nach Loslassen und erneutem Drücken | Offen |
| 2–5 s deutschen Satz halten/loslassen | `capture_ready`, gültiges verständliches Stereo-WAV | Offen |
| Taste länger als 30 s halten | Eine begrenzte Aufnahme; keine zweite bis erneutes Drücken | Offen |
| Sehr kurzer Tastendruck | Keine fertige Leeraufnahme | Offen |
| Dienst während Aufnahme stoppen | arecord beendet; `/run/pi-ptt` bereinigt | Offen |
| Falsches ALSA-Gerät konfigurieren | `error`, keine fertige Datei; nach Korrektur wieder nutzbar | Offen |
| Neustart mit aktiviertem Dienst | Dienst läuft, GPIO frei, Aufnahme/Wiedergabe erneut möglich | Offen |
| Speicher/CPU beobachten | Kein Dateiwachstum über einen Slot; Last messen | Offen |

Fertige Aufnahme vor dem nächsten Tastendruck manuell abhören (der Dienst spielt nie selbst ab):

```bash
aplay -D plughw:CARD=wm8960soundcard,DEV=0 /run/pi-ptt/capture.wav
sudo systemctl stop pi-ptt.service
sudo systemctl start pi-ptt.service
```

Beim manuellen Abspielen die Taste nicht drücken. Anschließend Stop/Start entfernt die Testaufnahme. Kein WAV committen. Endgültigen Speaker-Wert mit den lesenden Befehlen aus [setup.md](setup.md) nachtragen; die bereits bestätigte allgemeine Audio-Neustartprüfung ist keine PTT-Abnahme.
