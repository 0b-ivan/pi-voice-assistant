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

## Billys Stimme mit RVC

Billy (Sprechstil `mensch`, Stimmeffekt `natural`) kann auf CT 107 seine eigene Stimme bekommen: Piper spricht wie bisher Deutsch, danach überträgt ein RVC-v2-Modell (Retrieval-based Voice Conversion) die Klangfarbe, z. B. ein fertiges Modell von B.J. Blazkowicz. Die Aussprache bleibt die von Thorsten, die Stimme wird die des Modells. Das geht nur auf dem Server. Fällt er aus, spricht der Pi Billy wie bisher mit Thorsten und leichtem Filter.

```text
Piper Thorsten → servitor-rvc (127.0.0.1:8767) → RVC_FILTER_GRAPH (nur Wärme + Limiter) → Pi
       └── RVC aus, zu langsam oder Fehler → NATURAL_FILTER_GRAPH wie bisher
```

- [`server/rvc_worker.py`](../server/rvc_worker.py) hält Modell, HuBERT und RMVPE resident ([rvc-python](https://github.com/daswer123/rvc-python) 0.1.5, PyTorch 2.1.2 CPU) und läuft als `servitor-rvc.service` in einer eigenen Python-3.10-Umgebung (rvc-python verlangt fairseq 0.12.2 und numpy 1.23). Er nimmt nur Anfragen von `127.0.0.1` an und begrenzt sich auf 2 GB RAM (`MemoryMax`).
- Der Sprachdienst schickt nur Antworten mit Stimme `natural` hin, im Schritt `render` (das Display zeigt RENDERN). Ohne Antwort binnen `SERVITOR_RVC_TIMEOUT_SECONDS` (10 s) oder bei einem Fehler bleibt es bei Thorsten, das Journal meldet `rvc_fallback`, und RVC wird `SERVITOR_RVC_RETRY_SECONDS` (60 s) lang übersprungen. Die Zeit steht als `convert` in den `timings`.
- **Sicherheit:** Ein RVC-Modell (`.pth`) ist eine Pickle-Datei und könnte beim Laden Code ausführen. Der Dienst lädt es deshalb nur als reine Gewichte (`weights_only`) und weist alles andere ab. Nur bei einem Modell, dem du vertraust, hilft `SERVITOR_RVC_UNSAFE_LOAD=1` in `/etc/servitor-rvc.env`.
- **Rechte:** Die Stimme gehört dem Sprecher der Spielfigur. Das Modell und erzeugte Aufnahmen bleiben privat, nicht im Repository und nicht im Netz.

**Einrichten** (als root in CT 107; das Modell vorher von Hand herunterladen, als `.zip` mit `.pth` und `added_*.index` oder als einzelne `.pth`):

```bash
sh server/install-rvc.sh /root/bj-blazkowicz.zip      # Dienst, PyTorch CPU, HuBERT/RMVPE, Modell
/opt/servitor-voice/.venv/bin/python server/bench-rvc.py --pitch 0 -2 -4 --f0 rmvpe pm
sh server/install-rvc.sh --enable                     # Billy spricht über RVC
sh server/install-rvc.sh --disable                    # zurück zu Thorsten
```

`bench-rvc.py` spricht fünf typische Billy-Antworten (von „Gemerkt.“ bis zu zwei Sätzen) genau wie der Sprachdienst, schickt sie durch den laufenden Dienst und gibt je Kombination aus Tonhöhenmethode und Halbtönen den Echtzeitfaktor (Rechenzeit pro Sekunde Audio), die längste Wartezeit gegenüber dem Timeout sowie den RAM von Dienst und Container aus. Die Hörproben liegen in `/tmp/rvc-bench` (`NN-piper.wav` gegen `NN-rvc-<f0>-p<pitch>.wav`). Die beste Kombination kommt nach `/etc/servitor-rvc.env` (`SERVITOR_RVC_PITCH`, `SERVITOR_RVC_F0_METHOD`), dann `systemctl restart servitor-rvc`.

**Erwartung:** Gemessen ist auf CT 107 noch nichts. In einem Test-Container mit 4 Kernen brauchte schon der RVC-Synthesizer allein (mit Zufallsgewichten, ohne HuBERT) etwa 2 s Rechenzeit pro Sekunde Audio. Eine Antwort von 6 s dürfte also eher 10 s und mehr kosten und damit an den Timeout stoßen. `pm` ist schneller als `rmvpe`, klingt aber rauer. Wenn kurze Antworten passen und lange nicht, bleiben die langen bei Thorsten. Dann wechselt Billy mitten im Gespräch die Stimme, das ist beim Hören zu entscheiden.

**Alarm-Clips:** Billys vorgefertigte Ansagen rendert der Server. Nach `--enable` auf dem Pi einmal `alarm_audio.py build --force --voice natural` laufen lassen (~25 min), dann sprechen auch sie mit der neuen Stimme. Die Servitor-Clips bleiben unverändert.

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
