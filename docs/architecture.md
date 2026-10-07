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

Messwerte stehen ausschließlich unter [STT](speech-to-text.md) und [TTS](local-speech.md); Hardware-Abnahmen unter [PTT](push-to-talk.md), [Button SHIM](button-controls.md) und [Erweiterungen](hardware-bring-up.md). Entscheidungen: [Pi-Client](decisions/0001-client-server.md), [OS](decisions/0002-operating-system.md), [Vosk-only STT](decisions/0003-hybrid-stt.md).
