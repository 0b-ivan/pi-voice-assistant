# Speech-to-Text: Vosk und OpenRouter

Auf `pi-assistent` läuft bewusst **`STT_PROVIDER=vosk`**. PTT→Vosk mit echten WM8960-Aufnahmen ist am 05.10.2026 bestätigt. Vosk 0.3.45 funktioniert unter Python 3.13/aarch64 auf Trixie. Das liefert deutschen Text, keine KI-Antwort.

## Modi und Installation

| `STT_PROVIDER` | Verhalten |
|---|---|
| `vosk` | Vollständig lokale STT, kein Netzwerk/API-Key nötig |
| `openrouter` | Online-STT; Code-/Vorlagenstandard, weil Vosk separat installiert wird |
| `auto` | Zuerst OpenRouter; bei STT-/Netz-/API-Fehler Vosk, reale Ausfall-Abnahme noch offen |

[Setup](setup.md#4-repository-und-dienst-installieren) enthält die vollständige Installation. Optionaler Vosk-Installer: `sudo bash scripts/install-vosk.sh`. Paket liegt in `/opt/pi-voice-assistant/vendor`, Modell in `/opt/pi-voice-assistant/models/vosk-model-small-de-0.15`. Kein Modell in Git; kein Vosk-venv für den Dienst: er verwendet `/usr/bin/python3` mit zusätzlichem Vendor-Pfad.

Konfiguration in `/etc/pi-voice-assistant.env` ändern, danach Dienst neu starten. Für Vosk:

```text
STT_PROVIDER=vosk
VOSK_MODEL_PATH=/opt/pi-voice-assistant/models/vosk-model-small-de-0.15
VOSK_PYTHON_PATH=/opt/pi-voice-assistant/vendor
```

Für `openrouter`/Online-Pfad von `auto` zusätzlich echten `OPENROUTER_API_KEY` setzen. Bisher getestetes Online-Modell: `openai/whisper-large-v3-turbo`, Sprachhinweis `de`, Standard-HTTP-Timeout 30 s. Keine LLM-Anbindung aus der STT-Konfiguration ableiten.

## Format und Ausführung

Bei **`STT_PROVIDER=vosk`** nimmt der PTT-Dienst jetzt direkt in **16 kHz / Mono / S16_LE** auf und speist diese PCM-Daten bereits während gedrückter PTT-Taste in einen inkrementellen Vosk-Recognizer. Das Modell wird beim Dienststart vorgewärmt. Beim Loslassen muss Vosk dadurch nur noch `FinalResult()` liefern; eine zweite komplette WAV-Nachtranskription entfällt im Normalfall. Für `openrouter` und `auto` bleibt der bisherige 48-kHz-/Stereo-WAV-Pfad bestehen. Fällt Live-Vosk aus, wird die erzeugte WAV weiterhin über den bisherigen Adapter transkribiert.

Es bleibt bei einem Aufnahmeslot. Bei lokalem Vosk läuft die Erkennung schon im Capture-Thread parallel zur Aufnahme; nach Release wird das bereits fertige Ergebnis in den bestehenden Job-/Controller-Pfad übernommen. B verwirft die laufende Aufnahme bzw. das Ergebnis wie bisher. Providerfehler erzeugen `stt_error`; bei `auto` werden bei beidseitigem Scheitern beide Ursachen gemeldet. Leerer Online-Text ist ein Fehler und löst Fallback aus. Die Unit wartet nicht auf `network-online.target`.

## Hardware-Messwerte

Einzelmessungen vom 05.10.2026, kein allgemeines Leistungsversprechen:

| Messung | Ergebnis |
|---|---|
| 6-s-Clip, erster Modellaufruf | 15,59 s |
| Gleicher Clip, Modell schon geladen | 6,75 / 6,78 s |
| Laufender PTT-Dienst mit geladenem Vosk | 145728 kB RSS ≈ 142 MiB |
| Gesamtsystem zum Messzeitpunkt | 415 MiB RAM, 120 MiB verfügbar, 105 MiB Swap belegt |

System-Swap ist nicht allein Vosk zuzurechnen. Warm liegt die Verarbeitungszeit für diesen Clip etwa bei der Audiolänge; „offline“ bedeutet hier nicht „schnell“.

OpenRouter erkannte einen Vergleichsclip deutlich besser. Linker/rechter Mikrofonkanal lieferten ähnliche Vosk-Ergebnisse; SoX-Konvertierung brachte für diesen Clip kaum Verbesserung. Das kleine deutsche Modell ist deshalb der wahrscheinliche Hauptengpass in diesen Beispielen. Das schließt Audio-/Abstandsprobleme bei anderen Aufnahmen nicht aus. Keine zusätzliche SoX-Pipeline im normalen Dienst erforderlich.

Optionale Command-Grammar ist eine Idee für begrenzte Befehle, noch keine Funktion und kein Qualitätsversprechen für freie Fragen.

## Standalone-Test und Logs

Vorhandene eigene WAV im normalen `/tmp` testen, als `obivan` mit lesbarer Konfiguration:

```bash
set -a
. /etc/pi-voice-assistant.env
set +a
/usr/bin/python3 /opt/pi-voice-assistant/src/transcribe.py /tmp/stt-test.wav
```

stdout enthält Text, stderr `STT_PROVIDER_USED=vosk` bzw. `openrouter`. Fehler: `STT_ERROR:` und Exitstatus 1. Das verwendet einen neuen Prozess und misst keine warme Dienstlatenz.

PTT-Journal: bei lokalem Live-Vosk erscheinen beim Dienststart `stt_loading`/`stt_ready`, während Aufnahme `recording` mit `stt=vosk-live`, danach `capture_ready` mit `live_stt=true`, `processing` und direkt `transcript`/`ERKANNT: ...`. Audiodaten sind flüchtig, Transkripte werden protokolliert. [Troubleshooting](troubleshooting.md#stt), [Betrieb](operation.md).
