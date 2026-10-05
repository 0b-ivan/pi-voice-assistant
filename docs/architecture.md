# Architektur

Der Pi übernimmt Taste, Audio und lokale STT. **Implementiert auf `main`:**

```text
GPIO17 oder optional SHIM A
  → arecord: 48 kHz / Stereo / S16_LE
  → validierter WAV-Slot /run/pi-ptt/capture.wav
  → STT-Hintergrundthread
      vosk: intern 16 kHz Mono, Modell im Prozess wiederverwendet
      openrouter: Online-STT
      auto: Online zuerst, bei Fehler Vosk
  → transcript / ERKANNT im Journal
```

[`src/ptt.py`](../src/ptt.py) steuert GPIO und Aufnahme, [`src/voice_controls.py`](../src/voice_controls.py) den STT-Auftrag und eigene Sprachprozesse, [`src/transcribe.py`](../src/transcribe.py) die Provider. Der Hintergrundthread hält die Tasten bedienbar; es gibt weiterhin nur **einen** Aufnahmeslot, keine Warteschlange.

Nach STT-Abschluss werden die PTT-Eingänge resynchronisiert; gehaltene Tasten brauchen Release. B verwirft ein laufendes STT-Ergebnis, beendet aber keinen nativen Vosk-Aufruf. Der Slot bleibt bis zum Abschluss gesperrt. Modell-Laden erfolgt bei der ersten lokalen Transkription, nicht beim Dienststart.

## Statusansage und geplanter Antwortpfad

SHIM E kann bereits eine **separat installierte** Sprach-CLI für Statusmeldungen starten. PTT stoppt die vom Dienst gestartete Ansage vor Aufnahme; E spricht nicht während Aufnahme. Das ist keine LLM-Antwort. Externe manuelle Playback-Prozesse verwaltet der Dienst nicht.

Piper 1.8.0 mit deutscher Thorsten-Stimme ist auf dem Pi getestet. Wrapper/Installer stehen in offenem PR #14; ein frischer `main`-Checkout enthält sie noch nicht. Ressourcenvergleich in PR #16 ist ebenfalls offen. Siehe [lokale Sprachausgabe](local-speech.md).

Geplant: Transcript → OpenRouter-LLM → Piper → WM8960. **LLM-Aufruf und automatische Antwort-Orchestrierung fehlen.** Der vorhandene Offline-STT-Pfad liefert daher noch keinen vollständig offline antwortenden Assistenten. Lokales LLM, Wake Word, Streaming, Echounterdrückung sowie Kamera/Display sind keine aktuellen Funktionen.

## Betrieb und Grenzen

Die [Unit](../deploy/pi-ptt.service) läuft als `obivan` mit `audio/gpio/i2c`, ohne root. Runtime-Verzeichnis ist `/run/pi-ptt`; Code unter `/opt`, Home gesperrt. Ein dedizierter Dienstbenutzer ist eine offene Verbesserung, keine bereits implementierte Isolation.

Aufnahme hat standardmäßig 30 s Limit; Provider-/Aufnahmefehler werden protokolliert. Vosk braucht kein Netzwerk, die Unit wartet nicht auf `network-online.target`. `auto` ist implementiert, aber der reale Ausfalltest steht aus. WAVs sind flüchtig, Transkripte stehen im Journal.

Messwerte stehen ausschließlich unter [STT](speech-to-text.md) und [TTS](local-speech.md); Hardware-Abnahmen unter [PTT](push-to-talk.md), [Button SHIM](button-controls.md) und [Erweiterungen](hardware-bring-up.md). Entscheidungen: [Pi-Client](decisions/0001-client-server.md), [OS](decisions/0002-operating-system.md), [STT-Modi](decisions/0003-hybrid-stt.md).
