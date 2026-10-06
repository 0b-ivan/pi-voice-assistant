# Piper-TTS einrichten

Lokale deutsche Sprachausgabe auf Pi Zero 2 W / Trixie läuft mit **Piper 1.8.0**. Der Dienst hält das Modell resident. Zwei Profile sind vorgesehen:

| Profil | Stimme | Charakter |
|---|---|---|
| `normal` | `de_DE-thorsten-low` | unveränderte lokale Sprachausgabe |
| `servitor` | `de_DE-thorsten_emotional-medium`, Speaker 4 | neutral/kommandierend, längere Satzpausen, starker Maschinen-DSP |

## Installation

Im aktuellen Repo-Checkout auf dem Pi:

```bash
sudo bash scripts/install-piper.sh
sudo bash scripts/install-voice-service.sh
```

`install-piper.sh` installiert Piper 1.8.0, ALSA/FFmpeg und lädt beide deutschen Modelle nach `/opt/pi-voice-assistant/tts/`. Netzwerk ist nur für Paket- und Modelldownload nötig. Die spätere Synthese und der Servitor-DSP laufen lokal.

`/opt/pi-voice-assistant`, `src/` und `scripts/` bleiben root-verwaltet; nur `.venv/` und `tts/` sind für `obivan` beschreibbar.

## Konfiguration

`/etc/pi-voice-assistant.env`:

```text
PIPER_VENV=/opt/pi-voice-assistant/.venv
PIPER_MODEL=/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx
TTS_AUDIO_DEVICE=plughw:CARD=wm8960soundcard,DEV=0

TTS_VOICE_PROFILE=servitor
TTS_SERVITOR_MODEL=/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx
TTS_PIPER_SPEAKER_ID=4
TTS_PIPER_LENGTH_SCALE=1.02
TTS_PIPER_NOISE_SCALE=0.22
TTS_PIPER_NOISE_W_SCALE=0.18
TTS_PIPER_SENTENCE_SILENCE=0.32
# TTS_FFMPEG_BIN=/usr/bin/ffmpeg
```

Die Wörter werden mit `length_scale=1.02` bewusst kurz und hart gehalten. Niedrigere `noise_scale`/`noise_w` reduzieren emotionale Schwankung und machen die Ausgabe kälter. Die **320 ms Satzpause** bleibt bestehen; die Schwere kommt damit aus den Pausen statt aus gedehnten Wörtern. Der DSP senkt die Grundtonhöhe jetzt nur noch um rund **1,35 Halbtöne** ab. Metallische Resonanzen und Chorus liegen deutlich weiter vorn. Zusätzlich laufen zwei sprachgebundene Tracer-Ebenen sowie eine unabhängige Maschinen-Aura: kurze gestaffelte Digital-Echos, eine 6-Hz-Sägezahn-Hüllkurve durch einen Phaser und ein eigenes leises 220-Hz-Säge-/Phaser-Brummen mit 440-Hz-Oberwelle. Die Maschinen-Aura läuft auch durch Satzpausen weiter und endet erst mit der gesamten Ansage.

## Servitor-DSP

Das Profil rendert keine zweite Effekt-WAV mehr. Ablauf:

```text
resident Piper
  -> erster PCM-Chunk sofort per Pipe
  -> FFmpeg Filtergraph
       - Pitch-Absenkung
       - metallische EQ-Resonanzen
       - starker Flanger (Feedback 48 %, 1 Hz)
       - stärkerer Chorus / Mehrstimmen-Layer
       - Stutter/Tremolo
       - Phaser/Aura
       - Doppler-Flanger
       - >20-Hz-Tremolo als Ringmod-Textur
       - Tracer-/Glitch-Spur mit kurzen gestaffelten Echos
       - kontinuierliche Saw-Phase-Spur: 6-Hz-Sägezahn-Hüllkurve + Phaser
       - unabhängige Maschinen-Aura: 220-Hz-Sägezahn + 440-Hz-Oberwelle + Phaser, auch in Satzpausen
       - kurzer Hall
       - Limiter
       - kurzer natürlicher Echo-Tail
  -> ALSA / WM8960
```

Der residente Servitor-Pfad schreibt Piper-PCM direkt auf FFmpeg-stdin und startet die Wiedergabe mit dem ersten verfügbaren Audio-Chunk. Eine vollständige Quell-WAV muss nicht mehr fertig synthetisiert werden. Der frühere Reverse-Fade wurde entfernt, weil er die komplette Ansage puffern und damit Streaming verhindern würde. Der kurze Echo-Tail sorgt weiterhin für ein kontrolliertes Ausklingen.

## Dynamischer Status auf SHIM E

Taste **E** baut den Text beim Tastendruck neu aus lokalen Systemwerten. Wenn verfügbar, werden angesagt:

- normierte 1-Minuten-Systemlast aus `/proc/loadavg`
- CPU-Kerntemperatur
- freier Arbeitsspeicher in Prozent
- freier Root-Datenspeicher in Prozent
- Laufzeit
- aktiver STT-Modus

Beispiel:

```text
STATUS NOMINAL.
LAST 21 PROZENT. KERN 55 GRAD.
ARBEITSSPEICHER 62 FREI. SPEICHER 40 FREI.
LAUFZEIT 2 Stunden 36 Minuten. ERKENNUNG LOKAL.
SERVITOR BEREIT. DIREKTIVE ERWARTET.
```

Fehlt eine Quelle unter `/proc` oder `/sys`, wird nur dieser Wert ausgelassen; die Statusansage bleibt funktionsfähig. Während STT beginnt sie mit `VERARBEITUNGSPROTOKOLL AKTIV. AUFNAHME IN ANALYSE.`.

## Aktivieren und prüfen

```bash
sudo vim /etc/pi-voice-assistant.env
sudo systemctl restart pi-ptt.service
journalctl -u pi-ptt.service -n 40 --no-pager
```

Beim Start muss `tts_ready` das Profil `servitor` und das Emotional-Modell melden. Danach SHIM **E** drücken.

Fallback-Test ohne Dienst:

```bash
set -a
source /etc/pi-voice-assistant.env
set +a
/opt/pi-voice-assistant/src/speak.py "SYSTEM NOMINAL. SERVITOR BEREIT. BEFEHL ERWARTET."
```

## Wiedergabe und Grenzen

Der residente Servitor-Pfad erzeugt keine WAV mehr: Piper liefert 16-Bit-PCM-Chunks direkt an FFmpeg und damit an ALSA. Nur das Normalprofil verwendet weiterhin die temporäre WAV. B bzw. PTT kann die eigene Wiedergabe weiterhin über die Prozessgruppe abbrechen.

Der aktuelle Maschinenfilter ist bewusst aggressiv und für die zwei kleinen WM8960-Lautsprecher abgestimmt. Der digitale `Playback`-Regler und der analoge `Speaker`-Pegel bleiben davon getrennt.

Pi-Messungen und Speichergrenzen: [TTS-Performance](local-speech.md) und [Ressourcenbericht](piper-resources.md).
