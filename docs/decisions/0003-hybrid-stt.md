# ADR 0003: Vosk-only STT, OpenRouter nur für das LLM

Datum 05.10.2026. Aktualisiert 06.10.2026. Status: für den Normalbetrieb durch [ADR 0004](0004-servitor-server.md) ersetzt; gilt weiter für den lokalen Fallback und für Betrieb ohne `ASSISTANT_BASE_URL`.

## Entscheidung

Speech-to-Text läuft ausschließlich lokal mit Vosk.

```text
WM8960 / PTT
  -> Vosk STT lokal
  -> Text
  -> OpenRouter LLM
  -> Piper TTS lokal
```

OpenRouter verarbeitet kein Mikrofon-Audio. Die früheren STT-Modi `openrouter` und `auto` wurden aus dem aktiven Adapter entfernt. `STT_PROVIDER` akzeptiert nur noch `vosk`.

## Gründe

- Offline-Spracherkennung bleibt auch bei Netz-/API-Ausfall verfügbar.
- Audio verlässt den Pi nicht.
- STT und LLM haben klare Verantwortungsgrenzen.
- Es gibt nur noch einen STT-Codepfad zu testen und zu betreiben.
- Der OpenRouter-Key wird ausschließlich vom LLM-Client benötigt.

## Umsetzung

Der normale PTT-Pfad erfasst 16-kHz-Mono-PCM und speist Vosk bereits während gedrückter Taste. Das Modell wird beim Dienststart vorgewärmt. Ein validierter WAV-Slot bleibt als lokaler Vosk-Fallback bestehen.

Die bisherige Hardware-/Qualitätsbewertung und Messwerte stehen unter [Speech-to-Text](../speech-to-text.md). Ein lokales LLM ist damit nicht beschlossen; nur STT und TTS bleiben lokal.
