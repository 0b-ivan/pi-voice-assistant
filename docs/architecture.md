# Architektur

Der Pi übernimmt Taste, Audio, lokale STT und lokale TTS. OpenRouter wird ausschließlich für das LLM verwendet. **Implementierter Antwortpfad:**

```text
GPIO17 oder optional SHIM A
  → arecord: 16 kHz / Mono / S16_LE im lokalen Vosk-Pfad
  → Live-Vosk während gedrückter PTT-Taste
  → transcript / ERKANNT
  → src/llm.py
      OpenRouter Chat Completions
      Servitor-System-Prompt
      nicht-streamend
      Timeout + recoverable errors
  → resident Piper / Thorsten Emotional
  → Servitor-FFmpeg-Live-DSP
  → ALSA
  → WM8960
```

[`src/ptt.py`](../src/ptt.py) orchestriert GPIO, Aufnahme und die Zustandsfolge. [`src/transcribe.py`](../src/transcribe.py) enthält STT, [`src/llm.py`](../src/llm.py) ausschließlich den OpenRouter-LLM-Client und [`src/voice_controls.py`](../src/voice_controls.py) die lokalen Speech-/Piper-Prozesse. `llm.py` kennt weder GPIO noch ALSA, WM8960 oder Piper. Es gibt weiterhin nur **einen** Verarbeitungs-Slot, keine Warteschlange.

Nach STT-Abschluss werden die PTT-Eingänge resynchronisiert; gehaltene Tasten brauchen Release. B verwirft ein laufendes STT-Ergebnis, beendet aber keinen nativen Vosk-Aufruf. Der Slot bleibt bis zum Abschluss gesperrt. Das Vosk-Modell wird beim Dienststart vorgewärmt, damit der erste PTT-Zyklus keinen Modell-Load bezahlen muss.

## Servitor-Server (CT 107) mit lokalem Fallback

Ist `ASSISTANT_BASE_URL` gesetzt ([Vorlage](../config/client.env.example)), streamt der Pi die Aufnahme schon während des Tastendrucks als chunked `POST /v1/turn` an den [Servitor-Dienst](../server/servitor_server.py). Erkennung, LLM, Synthese und DSP laufen dort; der Pi spielt nur die fertige WAV ab (Opus wird vorher mit ffmpeg dekodiert). [`src/remote_turn.py`](../src/remote_turn.py) kapselt den Client.

```text
PTT gedrückt → arecord 16 kHz mono → Pump-Thread
   ├─ capture.part.pcm (für den Fallback)
   └─ Uplink-Thread → chunked POST /v1/turn (nie blockierend)
PTT los → Abschlusschunk → NDJSON lesen → Display → aplay remote-reply.wav
```

| Server-Ereignis | Pi-Event / Display |
|---|---|
| `stage: recognize` | Fortschritt `stt/live_finalize` → ERKENNEN |
| `transcript` | `transcript` → DENKEN |
| `stage: think` | `llm_start` → DENKEN |
| `reply` | `llm_response` → SYNTHESE |
| `stage: synthesize` / `render` | Fortschritt `tts/synthesis` → SYNTHESE, `tts/dsp_render` → RENDERN |
| `audio` + `done` | `speech_started`, Fortschritt `tts/playback` → AUSGABE |

Der Fallback setzt dort an, wo der Server ausgefallen ist: Verbindung/Upload → lokale Vosk-Erkennung der mitgeschriebenen Aufnahme; LLM-Fehler → lokales LLM mit dem Server-Transkript; Synthese-/Renderfehler → lokale Piper-Ausgabe der Server-Antwort. „Keine Sprache erkannt“ wird nicht lokal wiederholt. Das Token steht nur in `/etc/pi-voice-assistant.env` und wird nie geloggt.

Während der Server erreichbar ist, läuft auf dem Pi keine zusätzliche Live-Vosk-Erkennung. Im Modus `hybrid` bleibt der vorgeladene Vosk-Worker für den Fallback bereit und erkennt bei Bedarf die mitgeschriebene WAV.

**Hardware-Messung 07.10.2026** (Pi Zero 2 W, LAN, WAV, Frage „Wie hoch ist der Eiffelturm?“, 3,6 s Aufnahme): Server nach Upload-Ende 1,67 s (STT-Abschluss 0,05 s, LLM 0,95 s, Synthese 0,48 s, Render 0,19 s). Loslassen bis Wiedergabestart ca. 2,0–2,5 s, vorher lokal ca. 13,7 s. Stimme unverändert; das Display durchlief alle Schritte. Diese Messung lief noch mit mitlaufender lokaler Vosk-Erkennung.

**SSH-Test 07.10.2026, Stand #37** mit [`scripts/test-remote-turn.py`](../scripts/test-remote-turn.py) (gleiche Uplink-/Job-Klassen wie `pi-ptt`, 1,61 s Frage in Echtzeit gestreamt, Zeiten ab Upload-Ende):

| Fall | Ergebnis |
|---|---|
| WAV, LAN | Audio bereit nach 1,48 s (Server 1,27 s), Wiedergabe auf dem WM8960 vollständig |
| Opus, LAN | Server 1,34 s, aber Audio erst nach 6,64 s bereit: die ffmpeg-Dekodierung kostet auf dem Pi Zero ca. 5 s |
| Server nicht erreichbar | `upload/network` nach 3 ms, lokaler Fallback erlaubt |
| Erste URL tot, zweite CT 107 | Umschalten nach 1,5 s Connect-Timeout, Audio nach 2,1 s (im echten Betrieb überlappt der Timeout mit der Aufnahme) |

Folgerung: Im LAN bleibt `ASSISTANT_AUDIO_FORMAT=wav`. Für den späteren Internetweg ist Opus erst sinnvoll, wenn die Dekodierung auf dem Pi schneller wird (z. B. residenter Decoder statt ffmpeg-Prozess).

### Offline-LLM auf CT 107

Fällt OpenRouter aus (kein Internet, keine Credits, Rate-Limit, Timeout, 5xx), antwortet ein lokales Modell im Container. [`server/install-llm.sh`](../server/install-llm.sh) baut `llama-server` aus llama.cpp `v0.5.0` (für die AVX2-CPU des Hosts) und lädt ein per SHA-256 geprüftes Qwen3-4B-Instruct-2507 (Q4_K_M, 2,5 GB; Auswahl siehe unten). [`servitor-llm.service`](../server/servitor-llm.service) betreibt es nur auf `127.0.0.1:8766`. Der Sprachdienst fragt OpenRouter mit 8 s Timeout und nach einem Fehler 60 s lang direkt das lokale Modell. Das `reply`-Event nennt das Modell (`local/qwen3-4b`), das Journal `llm_fallback` mit Grund. CT 107 hat dafür 4 Kerne und 5 GB RAM (llama-server ca. 2,1 GB fest belegt).

**Messung 08.10.2026** (Serverzeit inkl. STT/TTS):

| Fall | Antwort von | Server |
|---|---|---|
| Normal | OpenRouter | 1,1–2,2 s |
| Ungültiger Key (wie keine Credits) | lokal | 1,9 s |
| Kein Internet, erste Frage | lokal nach 8 s Timeout | 10,8 s |
| Kein Internet, folgende Fragen (60-s-Fenster) | lokal | 1,1 s |

**Modellwahl 08.10.2026** mit [`server/bench-local-llm.py`](../server/bench-local-llm.py) (8 Fragen, warmer Lauf, CT 107 mit 4 Kernen):

| Modell (Q4_K_M) | richtig | Median | Max | Anmerkung |
|---|---|---|---|---|
| Qwen2.5-3B-Instruct | ~4/8 | 2,7 s | 7,8 s | „ein Tag hat 60 Minuten“, 17×23 = 481 |
| Gemma 3 4B | ~5/8 | 7,2 s | 9,6 s | 17×23 = 746; Sliding-Window-Attention verhindert Prompt-Cache, jede Antwort ≥ 6 s |
| **Qwen3-4B-Instruct-2507** (gewählt) | ~6,5/8 | 3,0 s | 11,2 s | 1440 Minuten, 391, Canberra richtig; Tokio-Uhrzeit falsch; lange Erklärungen bis 11 s |

Mit Qwen3 4B und ungültigem OpenRouter-Key: Serverzeit 2,5–3,9 s. `llama-server` belegt ca. 2,1 GB fest (für AVX2 umsortierte Gewichte) plus freigebbaren Dateicache.

### Größeres Vosk-Modell: verworfen

[`server/bench-stt.py`](../server/bench-stt.py) vergleicht Modelle auf 20 mit Piper synthetisierten Fragen (zwei Sprecher, zwei Sprechtempi):

| Modell | WER | RAM | Ergebnis |
|---|---|---|---|
| `vosk-model-small-de-0.15` (aktiv) | 8,1 % | 225 MB | – |
| `vosk-model-de-0.21` ohne `rescore`/`rnnlm` | 10,5 % | 790 MB | nicht besser (teils nur Schreibweise „wieviel“) |
| `vosk-model-de-0.21` ohne `rescore` | – | – | OOM bei 5 GB Container-RAM |
| `vosk-model-de-0.21` vollständig | – | > 4,6 GB | OOM, auch ohne laufendes LLM |

Der Nutzen käme erst mit dem 2,1 GB großen `rescore`-Sprachmodell, für das der Host keinen RAM frei hat. Typische Restfehler des kleinen Modells: „ein Tag“ → „ein paar“, „nenne“ → „wenn die“. Eine bessere Erkennung bräuchte ein anderes Verfahren (z. B. Whisper), nicht ein größeres Vosk-Modell.

### Whisper als Erkenner: auf synthetischer Sprache kein Gewinn

[`server/bench-stt.py`](../server/bench-stt.py) mit denselben 20 Piper-Clips (Groß-/Kleinschreibung, Satzzeichen und Ziffern normalisiert; [`server/install-whisper.sh`](../server/install-whisper.sh), faster-whisper 1.2.1, CPU int8, `beam_size=1`, CT 107 mit 4 Kernen), 08.10.2026:

| Erkenner | WER | Wartezeit nach Loslassen | RAM |
|---|---|---|---|
| Vosk `small-de-0.15` (aktiv, streamt mit) | 6,5 % | 0,04 s | 233 MB |
| Whisper base | 24,2 % | 0,69 s | 409 MB |
| Whisper small | 8,9 % | 1,93 s | ~1 GB |
| Whisper large-v3-turbo | 5,6 % | 9,70 s | 2,5 GB |

Whisper erkennt erst nach dem Loslassen; nur large-v3-turbo ist genauer, aber zu langsam. Die Fehlerarten unterscheiden sich: Whisper verschreibt eher harmlos („Eifelturm“, „Tokyo“), Vosk liegt eher inhaltlich daneben („stell einen Timer“ → „still einen keine“). Saubere Synthese begünstigt Vosk; die Entscheidung braucht Aufnahmen echter Stimme über das WM8960-Mikrofon. Whisper small bleibt dafür auf CT 107 installiert (getrennte Venv, vom Dienst nicht genutzt).

## Statusansage und Antwortpfad

SHIM E erzeugt den Status im Dienst selbst. [`src/system_status.py`](../src/system_status.py) liest normierte Systemlast, CPU-Temperatur, freien RAM/Datenspeicher, Uptime und STT-Modus. Fehlende Werte werden ausgelassen. PTT stoppt die eigene Statusansage vor Aufnahme; E spricht nicht während Aufnahme.

Piper 1.8.0 bleibt resident. `normal` nutzt Thorsten Low. `servitor` nutzt Thorsten Emotional mit neutralem Speaker und einer Sprechkonfiguration, bei der Wörter nur leicht langsamer sind, während zusätzliche Satzpausen den schweren Befehlston erzeugen.

```text
SHIM E
  -> kompakter dynamischer Systemtext
  -> resident Piper
  -> erster 16-Bit-PCM-Chunk
  -> FFmpeg Live-DSP über stdin
       metal / flanger / chorus / stutter / aura / doppler / ringmod / limiter
  -> ALSA
  -> WM8960
```

Der residente Servitor-Pfad erzeugt keine TTS-WAV mehr: Piper liefert Audio-Chunks direkt an FFmpeg. Dadurch beginnt die Ausgabe mit dem ersten synthetisierten Chunk. Das Normalprofil und der Standalone-Fallback können weiterhin dateibasiert arbeiten. [`src/voice_effects.py`](../src/voice_effects.py) enthält beide FFmpeg-Befehlsvarianten.

## Display

Das Adafruit mini PiTFT 1,3″ läuft separat vom Sprachdienst direkt über SPI/ST7789. `src/display.py` prüft beim Boot und während des Betriebs SPI, WM8960, Netzwerk, Vosk-/TTS-Modellpfade und den Zustand von `pi-ptt.service`. Die zugehörige Unit ist `deploy/pi-display.service`; die Display-Abhängigkeiten liegen in einer eigenen Venv unter `/opt/pi-voice-assistant/.venv-display`.

Der Display-Dienst greift nicht in Aufnahme, STT oder TTS ein. `ptt.py` veröffentlicht zusätzlich zu den vollständigen Journal-Events einen minimierten, atomar ersetzten Snapshot unter `/run/pi-ptt/display-event.json`. Darin stehen nur Eventname, Version und Zeitstempel. Der Display-Prozess liest diesen Snapshot mit 100-ms-Takt und bildet ihn auf `BEREIT`, `ZUHÖREN`, `VERSTEHEN`, `DENKEN`, `SPRECHEN` oder kurzzeitig `FEHLER` ab. System-/Netzwerkprobes bleiben auf einem separaten 2-s-Takt.

`DENKEN` wird durch `transcript`/`llm_start` gesetzt; `llm_response` bzw. `speech_started` wechseln auf `SPRECHEN`. LLM-Fehler werden wie STT-/TTS-Fehler kurz als `FEHLER` angezeigt.

OpenRouter verarbeitet nur Text. Mikrofon-Audio bleibt bei Vosk lokal, und die Antwort wird lokal mit Piper erzeugt. Der API-Key wird ausschließlich aus dem von systemd geladenen Environment gelesen. Lokales LLM, Wake Word, Echounterdrückung sowie die Kamera sind keine aktuellen Funktionen.

## Betrieb und Grenzen

Die [Unit](../deploy/pi-ptt.service) läuft als `obivan` mit `audio/gpio/i2c`, ohne root. Runtime-Verzeichnis ist `/run/pi-ptt`; Code unter `/opt`, Home gesperrt. Ein dedizierter Dienstbenutzer ist eine offene Verbesserung, keine bereits implementierte Isolation.

Aufnahme hat standardmäßig 30 s Limit; STT-/LLM-/TTS-Fehler werden protokolliert und beenden den Dienst nicht. Vosk braucht kein Netzwerk, der LLM-Schritt dagegen schon. Die Unit wartet trotzdem nicht auf `network-online.target`: ein Netz-/OpenRouter-Ausfall wird als `llm_error` behandelt, danach bleibt PTT nutzbar. WAVs sind flüchtig; Transkripte und LLM-Antworten stehen im Journal.

Messwerte stehen ausschließlich unter [STT](speech-to-text.md) und [TTS](local-speech.md); Hardware-Abnahmen unter [PTT](push-to-talk.md), [Button SHIM](button-controls.md) und [Erweiterungen](hardware-bring-up.md). Entscheidungen: [Pi-Client](decisions/0001-client-server.md), [OS](decisions/0002-operating-system.md), [Vosk-only STT](decisions/0003-hybrid-stt.md), [Servitor-Server](decisions/0004-servitor-server.md).
