# Push-to-Talk: Verhalten und Abnahme

Die vorhandene WM8960-Taste liegt auf **BCM GPIO17 / Pin 11**, aktiv Low mit Pull-up. Auf dem Pi bestätigt: `/dev/gpiochip0`, `pinctrl-bcm2835`, Offset 17; libgpiod-Python-API v2. Kein RPi.GPIO, sysfs oder zusätzlicher Audiotreiber nötig. [Hardwarequelle](https://www.waveshare.com/wiki/WM8960_Audio_HAT).

Installation über den vollständigen [Dienstinstaller im Setup](setup.md#4-repository-und-dienst-installieren); keine manuelle Kopieranleitung einzelner Module. Vor Tests Dienst stoppen, danach [wieder starten](operation.md#start-stop-und-logs).

## Bedienung und Grenzen

- Halten startet Aufnahme, Loslassen beendet sie; kein Toggle. Beim Start und nach Limit/Fehler zunächst loslassen.
- GPIO-Abfrage alle 10 ms, Pegel 40 ms stabil; konfigurierbar 10–500 ms. Sehr kurze Berührungen können entfallen.
- Aufnahme mit `arecord`: bei `STT_PROVIDER=vosk` direkt PCM S16_LE, **16 kHz mono** für Live-Erkennung; bei den übrigen Providern bleibt **48 kHz stereo**. Standardlimit 30 s, konfigurierbar 1–120 s.
- Dienst beendet `arecord` beim Loslassen mit SIGINT und wartet begrenzt. Im Live-Vosk-Pfad liest ein Pump-Thread das 16-kHz-Mono-PCM gleichzeitig in die flüchtige Capture-Datei und in Vosk. Anschließend wird wie bisher ein validiertes WAV für Fallback/Diagnose erzeugt.
- Ein Slot, keine Warteschlange: während STT keine weitere Aufnahme. STT läuft seit SHIM-Integration in einem Hintergrundthread; Tasten bleiben bedienbar. Nach Verarbeitung benötigen gehaltene PTT-Tasten Release.
- Dienststop beendet Aufnahme/verwaltete Ansage und entfernt Audiodateien. systemd startet bei Prozessausfall mit begrenzter Neustartfrequenz erneut.

Zusätzliches A/GPIO17-Verhalten, B-Abbruch und Wiedergabesteuerung: [Button SHIM](button-controls.md).

## Übergabepunkt

Während Aufnahme: `capture.part.pcm`; beim Verpacken kurz zusätzlich `capture.part.wav`. Bei Live-Vosk entsteht `capture.part.pcm` durch den PCM-Pump statt direkt als `arecord`-Zieldatei. Fertiges WAV wird atomar nach `/run/pi-ptt/capture.wav` umbenannt, Rohdaten werden entfernt. Dann erscheint:

```json
{"version":1,"event":"capture_ready","path":"/run/pi-ptt/capture.wav","reason":"release","format":"wav","encoding":"PCM_S16_LE","sample_rate":48000,"channels":2,"frames":96000}
```

Dauer = Frames / 48000. `reason` ist `release`, `limit` oder `process_exit`. Auf `capture_ready` folgt `processing`, danach `transcript`/`ERKANNT` oder `stt_error`; keine automatische Antwort. Der Dienst ruft den STT-Adapter direkt auf, Journal-Tailing ist keine Transport-API.

Die fertige Datei bleibt bis zum nächsten Aufnahmestart, Dienststop oder Reboot verfügbar. **Vor Stop und nächstem Tastendruck abhören:**

```bash
aplay -D plughw:CARD=wm8960soundcard,DEV=0 /run/pi-ptt/capture.wav
```

Nicht während des Abhörens erneut aufnehmen. `/run/pi-ptt` ist privat; Zugriff erfolgt als Dienstbenutzer `obivan`. Unter der Standardunit Runtime-Pfad nicht ändern.

## Hardware-Abnahme am 05.10.2026

Grundlage: vom Nutzer eingereichte Ausgaben und Hörprüfungen, keine direkte Agentenverbindung zum Pi. Ergebnisse betreffen die jeweils getestete Version.

| Prüfung | Beleg / Ergebnis |
|---|---|
| GPIO-Polarität und normale Zyklen | 19 vollständige Probe-Start/Release-Paare, aktiv Low bestätigt |
| Aufnahme und manuelle Wiedergabe | Verständlich; systemd enabled/active und erneute Aufnahme nach Neustart, Nutzer „passt“ |
| 30-s-Limit ohne Wiederholung | 16:54:44 → 16:55:14 CEST, reason=limit, 1.434.016 Frames = 29,88 s; mindestens 40 s gehalten |
| Start bei gehaltener Taste | Dienststart 16:57:23; Aufnahme erst nach Release und erneutem Drücken laut Nutzer; kein gesonderter Pi-Boottest |
| Kurzbetätigungen | Elf Clips à 6000 Frames = 125 ms; kein Beleg für elektrische Prellfreiheit oder Verwerfung unter 100 ms |
| Stop während Aufnahme | recording 17:00:12, Stop 17:00:20, kein capture_ready; inactive, kein arecord, Runtime-Verzeichnis entfernt |
| Falsches ALSA-Gerät und Wiederherstellung | Sechs einzelne Versuche mit error, keine fertige Datei; nach Rücknahme erneute verständliche Aufnahme bestätigt |
| Ruhetest | 15 s ohne neue Ereignisse; Zwischenaufnahmen stammten laut Nutzer von weiteren Tastendrücken |
| PTT → Vosk | Später auf Hardware bestätigt; [STT-Messwerte](speech-to-text.md#hardware-messwerte) |

Offen: gezielte elektrische Prell-/Kurzdrücktests, Audio unter 100 ms, Pi-Boot mit gehaltener Taste und quantitative Dauerlast. Der fehlende zwanzigste Probezyklus ist kein nachgewiesener Funktionsfehler. Neue SHIM-/TTS-Versionen brauchen die zusätzliche [Dienstabnahme](button-controls.md#abnahme).

## Automatisierte Prüfung

```bash
python3 -m unittest discover -s tests -v
```

Tests simulieren GPIO, Recorder und Provider; sie prüfen Zustandslogik, WAV-Validierung, Fehlerbereinigung und STT-Übergabe. Reale Geräterecht-, Mixer- und Audioprüfungen erfolgen auf dem Pi.
