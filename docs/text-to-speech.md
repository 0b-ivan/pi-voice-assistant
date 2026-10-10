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

Die Wörter werden mit `length_scale=1.02` bewusst kurz und hart gehalten. Niedrigere `noise_scale`/`noise_w` reduzieren emotionale Schwankung und machen die Ausgabe kälter. Die **320 ms Satzpause** bleibt bestehen; die Schwere kommt damit aus den Pausen statt aus gedehnten Wörtern. Der DSP senkt die Grundtonhöhe jetzt nur noch um rund **1,35 Halbtöne** ab. Metallische Resonanzen und Chorus liegen deutlich weiter vorn. Zusätzlich laufen zwei sprachgebundene Tracer-Ebenen sowie eine zeitstromgebundene Maschinen-Aura: kurze gestaffelte Digital-Echos, eine 6-Hz-Sägezahn-Hüllkurve durch einen Phaser und ein eigenes leises 220-Hz-Säge-/Phaser-Brummen mit 440-Hz-Oberwelle und deutlich langsamem Phaser-Sweep. Die Maschinen-Aura läuft auch durch Satzpausen weiter und endet erst mit der gesamten Ansage.

## Aussprache

Der deutsche espeak-Phonemizer hinter Piper betont manche Lore-Wörter falsch. [`src/pronounce.py`](../src/pronounce.py) schreibt sie direkt vor Piper um (Server, Pi, Streaming und `speak.py`); Display, Journal und Gedächtnis behalten die echte Schreibweise. Bisher: **Omnissiah → „Omnissi-ah“**. espeak machte daraus `ˈɔmnɪsˌiːɑː` (Betonung auf „OM“), jetzt `ɔmnˈɪsiːˈɑː` (Betonung auf „NIS“, wie in der Lore). Neue Regeln vorher mit dem Phonemizer aus Pipers venv prüfen (Befehl im Modulkopf). Die Alarm-Clips sind nach der gesprochenen Form benannt: nach einer neuen Regel `alarm_audio.py build --prune` laufen lassen, dann werden die betroffenen Clips neu gerendert.

## Servitor-DSP

Das Profil rendert keine zweite Effekt-WAV mehr. Ablauf:

```text
resident Piper
  -> erster PCM-Chunk sofort per Pipe
  -> FFmpeg Filtergraph
       - Pitch-Absenkung
       - Direktsignal für Verständlichkeit
       - metallische EQ-Resonanzen + ein starker Flanger
       - Chorus / Mehrstimmen-Layer
       - Tracer-/Glitch-Spur mit Tremolo und gestaffelten Echos
       - Saw-Phase-Spur: 6-Hz-Sägezahn-Hüllkurve + Phaser
       - unabhängige Maschinen-Aura: 220-Hz-Sägezahn + 440-Hz-Oberwelle + langsamer Phaser-Sweep, auch in Satzpausen
       - kurzer Hall
       - Limiter
       - kurzer natürlicher Echo-Tail
  -> ALSA / WM8960
```

Der residente Servitor-Pfad schreibt Piper-PCM direkt auf FFmpeg-stdin und startet die Wiedergabe mit dem ersten verfügbaren Audio-Chunk. Die Aura wird nicht als frei laufende FFmpeg-Quelle erzeugt, sondern aus der Zeitbasis des eingehenden PCM-Stroms. Dadurch bleibt sie während der explizit eingespeisten Satzpausen hörbar, kann die Sprach-Pipe aber nicht durch vorauseilende Synthese zurückstauen. Eine vollständige Quell-WAV muss nicht mehr fertig synthetisiert werden. Der frühere Reverse-Fade wurde entfernt, weil er die komplette Ansage puffern und damit Streaming verhindern würde. Der kurze Echo-Tail sorgt weiterhin für ein kontrolliertes Ausklingen.

## Billys Stimme

Billy (Sprechstil `mensch`, Stimmeffekt `natural`) spricht auf CT 107 mit einer eigenen Piper-Stimme: `de_DE-thorsten-high`, ruhiger eingestellt als Pipers Standard (`SERVITOR_NATURAL_NOISE_SCALE=0.4`, `SERVITOR_NATURAL_NOISE_W_SCALE=0.4`, `SERVITOR_NATURAL_LENGTH_SCALE=1.05`), also weniger Schwankung in Tonfall und Tempo. Danach kommt nur noch ein Limiter (`BILLY_FILTER_GRAPH`), kein Stimmfilter. `server/install-ct.sh` lädt das Modell (sha256-geprüft) und setzt `SERVITOR_NATURAL_PIPER_MODEL`. Ohne diese Variable, und lokal auf dem Pi, bleibt Billy bei Thorsten emotional mit `NATURAL_FILTER_GRAPH`.

Ausgewählt am 10.10.2026 nach Hörproben (thorsten-high, karlsson, pavoque, mls). Verworfen: RVC mit einem Blazkowicz-Modell (Echtzeitfaktor ~1,6 auf CT 107, Speicher über 3 GB bei 9 s Audio, PR #80) und `de_DE-mls-medium`, das kurze Sätze nur als Kauderwelsch spricht (trainiert auf langen Hörbuchpassagen).

## Dynamischer Status auf SHIM E

Taste **E** baut den Text beim Tastendruck neu aus lokalen Systemwerten. Laufzeit-Zahlen werden als deutsche Zahlwörter normalisiert, damit Piper Zusammensetzungen wie `53` zuverlässig als „dreiundfünfzig“ spricht. Wenn verfügbar, werden angesagt:

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
LAUFZEIT zwei Stunden sechsunddreißig Minuten. ERKENNUNG LOKAL.
SERVITOR BEREIT. BEFEHL ERWARTET.
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

Der aktuelle Maschinenfilter ist bewusst aggressiv und für die zwei kleinen WM8960-Lautsprecher abgestimmt. Nach der Hardware-Abnahme wurde der Live-DSP von elf Mix-Eingängen auf sechs reduziert: redundante Flanger-, Doppler-, Ring- und Aura-Zweige entfallen; Metall, Chorus, Tracer, Saw-Phase und die Maschinen-Aura bleiben erhalten. Zusätzlich laufen die eigentlichen Effekte intern mit 24 kHz statt 48 kHz; erst unmittelbar vor ALSA wird auf 48 kHz resampelt. Damit halbiert sich die Sample-Arbeit der teuren Echtzeitfilter weitgehend, ohne deren Frequenz-/Zeitparameter zu ändern. Der digitale `Playback`-Regler und der analoge `Speaker`-Pegel bleiben davon getrennt.

Pi-Messungen und Speichergrenzen: [TTS-Performance](local-speech.md) und [Ressourcenbericht](piper-resources.md).
