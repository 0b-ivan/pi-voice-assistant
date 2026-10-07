# Servitor: Vergleich #26 → main und Pi-Abnahme

Verglichen am 2026-10-07: #26 `6d95499745bbae9c17753e484eeb8b68858ba140`
mit main `d3a7dff`. Basis dieser Korrektur ist unverändertes aktuelles main;
Vosk → OpenRouter → lokale Piper-TTS, Abbruch und Display bleiben erhalten.

## Befunde

| Bereich | #26 → main | Bedeutung |
| --- | --- | --- |
| DSP | Nur #27: freie `aevalsrc`-Aura wird sprachgetakteter `aeval`-Zweig | Absichtliche Reparatur eines Streaming-Stalls; nicht zurücknehmen. Maschinenklang kann sich durch die andere zeitliche Taktung unterscheiden. |
| DSP-Pegel | Identisch: Zweigpegel, `normalize=0`, `volume=4.0`, Limiter 0.97 | Keine nachträgliche Absenkung im Code. |
| DSP-Qualität | Identische EQs, Flanger, Chorus, Phaser, Echo, Pitch, 24-kHz-Innenpfad/48-kHz-Ausgang | Der prägende Klang von #26 ist weiterhin vorhanden. |
| Resident Piper | Identisch: Speaker 4, length 1.02, noise .22/.18, Satzpause .32 s | Keine Regression dieser Parameter nach #26. |
| CLI-Piper | Identisch: Speaker 4, length 1.10, noise .30/.25, Satzpause .32 s | CLI klang bereits in #26 anders als resident. Für A/B alle Werte explizit setzen. |
| Modelle | Servitor-Default weiterhin emotional-medium | Log mit thorsten-low ist eine Env-Abweichung, keine automatische Modelländerung. Low kann emotional-medium/Speaker 4 nicht klanglich ersetzen. |
| FFmpeg-Streaming | Bereits in #26 ohne begrenzte Eingangsanalyse | Bestehendes Latenzproblem, durch LLM-Antworten/Last sichtbar geworden. Prozessstart ist nicht erste hörbare Ausgabe. |
| #28 | Lokale automatische Antwortwiedergabe nach OpenRouter | Mehr TTS-Nutzung; keine neue DSP- oder Syntheseimplementierung. |
| #29/#30/#31 | Vosk-only, LLM-Transport/Tests | Keine Servitor-Klangänderung. |
| Live Vosk | Pump/FinalResult läuft vor `join(2)`-Ende; Timeout gab Dateien/Zustand frei | Bereits vorhanden, unter Last riskant: alter Thread konnte neue Aufnahme beeinflussen. |
| Speicher | Vosk und Piper resident, FFmpeg zusätzlich | Kernel-OOM im vorliegenden Verlauf bestätigt. SD-Swap kann Absturz verhindern, aber Latenz verschlechtern. |

### Änderungen

* Bekannter PCM-Eingang: `-probesize 32 -analyzeduration 1` vor `-i`.
  `analyzeduration=0` wäre die automatische Voreinstellung, kein Abschalten.
  Kein `nobuffer`, das beim Probing Audio verwerfen könnte.
* Sicherer Pump-Besitz: 10 s Drain-Zeit statt 2 s. Bei weiter laufendem Thread
  Dateien und Threadreferenz behalten; neue Aufnahme wird verweigert, bis er
  beendet ist. Später Start verwirft den alten Zustand. Native Vosk-Aufrufe
  werden nicht gewaltsam abgebrochen. Drain blockiert weiterhin kurz die
  Bedienung; das ist keine asynchrone Neuimplementierung und keine Garantie
  gegen langsame Finalisierung unter Swap-Druck.
* `TTS_PLAYBACK_MODE=buffered`: Piper vollständig in temporäres WAV schreiben,
  danach identischen DSP abspielen. Vermeidet Synthese-Lücken im PCM-Strom
  und gleichzeitige aktive Piper-Inferenz/FFmpeg-DSP. Modell bleibt resident;
  OOM ist dadurch nicht grundsätzlich gelöst. First-Audio wird später.
  Standard bleibt `stream`. Im gepufferten Modus zeigt die SHIM-LED Aktivität,
  keine satzgenaue Hüllkurve.
* Ein-Sprecher-Modell erhält ohne explizite Speaker-Vorgabe Speaker 0;
  Mehrsprecher-Modell behält 4. Explizit ungültige IDs bleiben Fehler.
* TTS-Fehlerdetail wird protokolliert; systemd-Shutdown-Zeit 15 statt 5 s.

### Lokal überprüft

FFmpeg 7.1/macOS ARM64, 1 s PCM16-Mono/16 kHz Sinus, produktiver DSP,
nur ALSA durch PCM-Pipe ersetzt: bisher nach 2 s noch keine Ausgabe vor EOF;
mit Korrektur Ausgabe nach etwa 13 ms. Vollständige PCM-Ausgabe bytegleich.
Diese Zahl misst FFmpeg, nicht Piper, ALSA oder Pi-Performance.

Offline-Vergleich desselben Signals mit #26/main: beide 54.958 Samples,
RMS 1.310/1.720, Peak 7.059/3.846 (PCM16-Einheiten). #27 ändert die Aura,
aber dieser Test belegt keine generelle Pegelabsenkung. Sprachqualität muss
mit identischem Sprachmodell, Text, Piper-Parametern und Mixer gehört werden.

Optionaler echter Streaming-Regressionstest:

```bash
FFMPEG_TEST_BIN=/usr/bin/ffmpeg python3 -m unittest discover -s tests -v
```

Er prüft Audio vor EOF und bytegleiche vollständige Ausgabe; ohne FFmpeg
wird nur dieser Integrationstest übersprungen. Unit-Tests prüfen außerdem
Pump-Besitz nach Timeout, gepufferte Wiedergabe und Speaker-Auswahl.

## Pi-Test: erst Low stabilisieren, dann Klang vergleichen

Keine Modellinstallation und keine Swap-Neuformatierung erforderlich.
Die vorhandenen API-Zugangsdaten bleiben unverändert. Noch nicht blind auf
emotional-medium wechseln: Vosk + Medium können den bestätigten OOM erneut
verursachen. Dieser Patch verspricht keine Lösung der gesamten Speicherarchitektur.

### 1. Backup, Tests und Installation

Im Pi-Repo zunächst prüfen, ob lokale Änderungen vorliegen. Bei Änderungen
nicht überschreiben; eine zweite Arbeitskopie verwenden.

```bash
cd ~/pi-voice-assistant
git status --short
git fetch origin
git switch fix/servitor-stream-latency
python3 -m unittest discover -s tests -v
sudo cp -a /etc/pi-ptt.env /etc/pi-ptt.env.before-audio-fix
sudo cp -a /etc/pi-voice-assistant.env /etc/pi-voice-assistant.env.before-audio-fix
sudo bash scripts/install-voice-service.sh
```

Der Installer bewahrt bestehende Env-Dateien und startet einen zuvor aktiven
Dienst wieder. In `/etc/pi-ptt.env` mit `sudoedit` ergänzen:

```text
TTS_PLAYBACK_MODE=stream
```

Im ersten Durchgang aktuelles thorsten-low behalten. Bei einem Ein-Sprecher-
Modell `TTS_PIPER_SPEAKER_ID=0` setzen, falls bisher explizit 4 konfiguriert ist.
Für reproduzierbare Vergleiche in `/etc/pi-voice-assistant.env`:

```text
TTS_VOICE_PROFILE=servitor
TTS_PIPER_LENGTH_SCALE=1.02
TTS_PIPER_NOISE_SCALE=0.22
TTS_PIPER_NOISE_W_SCALE=0.18
TTS_PIPER_SENTENCE_SILENCE=0.32
```

Mixer und Speicher erfassen; keine API-Schlüssel ausgeben:

```bash
amixer -c wm8960soundcard sget Playback
free -h
swapon --show
sudo systemctl restart pi-ptt.service
journalctl -u pi-ptt.service -f -o cat
```

Auf `stt_ready`, `tts_ready`, `waiting_for_release` warten. Dreimal je
„Was ist die Hauptstadt Frankreichs?“ sprechen, danach eine Antwort mit
zwei kurzen Sätzen anfordern. E/Status separat testen. Jeweils reale Zeit
bis zum ersten hörbaren Ton und bis Ende notieren. `tts_first_chunk` misst
nur Synthese; `tts_playback_start` misst Prozessstart; `playback_total`
enthält Synthese und die gesamte gesprochene Dauer, ist keine reine Wartezeit.

Danach zehn Aufnahme/Antwort-Zyklen, kurze Aufnahme, lange Aufnahme,
Abbruch während Sprache und unmittelbar neue Aufnahme testen. Auch C/D
Lautstärke sowie E/Status prüfen. Abgebrochene native Synthese kann noch
kurz weiterrechnen; CPU/Swap dabei beobachten.

### 2. Leiser Output

`Playback` vor/nach C-Schritten vergleichen. Wenn es bereits hoch steht,
keine weitere DSP-Verstärkung oder Analog-/ADC-Änderung auf Verdacht.
Denselben kurzen Text im Profil normal und servitor bei identischem
Low-Modell/Mixer testen; danach Servitor mit Medium vergleichen, sobald
Speicherreserve bestätigt ist. So werden Modell, DSP und Hardware getrennt.
Piper 1.8.0 und die passende `.onnx.json` müssen installiert sein.

### 3. ALSA xrun isolieren

Bei xruns `TTS_PLAYBACK_MODE=buffered` in `/etc/pi-ptt.env` setzen und Dienst
neu starten. Identische Texte und Mixer verwenden. Verschwinden xruns,
spricht das für Synthese-Lieferlücken/konkurrierende Last. Bleiben sie,
FFmpeg-DSP, ALSA-Gerät und Swap/CPU müssen am Pi weiter untersucht werden.
Dieser Modus vergrößert keinen ALSA-Puffer und garantiert keine xrun-Freiheit.

In einem zweiten Terminal während beider Durchgänge:

```bash
vmstat 1
```

Hohe `si`/`so`-Werte zeigen aktiven Swap-Verkehr. Nach dem Test:

```bash
journalctl -u pi-ptt.service --since '10 minutes ago' --no-pager
sudo journalctl -k --since '10 minutes ago' --no-pager
systemctl show pi-ptt.service -p NRestarts -p MainPID
```

Dienstlogs können Transkripte enthalten; vor Weitergabe prüfen. Keine
Kernel-OOMs, keine Neustarts, vollständige Antworten, keine alten Transkripte
nach Abbruch und kein überlappender Pump sind die Abnahmekriterien. Bei
Drain-Timeout soll ein weiterer Start zunächst sicher verweigert werden
und nach Threadende wieder funktionieren.

### 4. Klang von #26

Erst bei ausreichend Speicherreserve und ohne OOM/Swap-Sturm:

```text
TTS_SERVITOR_MODEL=/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx
TTS_PIPER_SPEAKER_ID=4
```

Nur vorhandenes Modell samt JSON verwenden. Gleicher Testtext, gleicher Mixer,
obige explizite Parameter. Für einen Vergleich zum damaligen CLI-Klang
anschließend separat length=1.10, noise=.30/.25 setzen; dadurch verändert sich
auch die Sprechdauer. Nicht mit Low vergleichen und daraus eine DSP-Regression
ableiten. Freie #26-Aura nicht im Live-Loop wieder aktivieren: sie war der
Grund für die Streaming-Reparatur #27.

### Rücknahme

```bash
sudo systemctl stop pi-ptt.service
sudo cp -a /etc/pi-ptt.env.before-audio-fix /etc/pi-ptt.env
sudo cp -a /etc/pi-voice-assistant.env.before-audio-fix /etc/pi-voice-assistant.env
cd ~/pi-voice-assistant
git switch main
sudo bash scripts/install-voice-service.sh
sudo systemctl start pi-ptt.service
```

Kein Pi ist mit dieser Arbeitsumgebung verbunden. Hörvergleich, ALSA und
reale Vosk/Piper-Latenz/Speicheraufnahme sind deshalb noch nicht abgenommen.
