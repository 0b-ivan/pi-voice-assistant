# Architektur

Der **Pi Zero 2 W** ist der Client für Mikrofon, Tasten, Display, LED und Wiedergabe. Im Normalbetrieb übernimmt der **Servitor-Server CT 107** die rechenintensiven Schritte. Grundlage: [ADR 0004](decisions/0004-servitor-server.md).

```text
WM8960 → PTT/Wake Word → Pi → chunked POST /v1/turn → CT 107
                                             Vosk → direkte Antwort oder LLM
                                                    (OpenRouter / Qwen3-4B lokal)
                                             Piper → Servitor-DSP
Pi ← NDJSON-Fortschritt und Audio ←───────────┘
  → Display/LED und ALSA/WM8960-Lautsprecher
```

| Komponente | Aufgabe | Code |
|---|---|---|
| Pi-Controller | PTT, Menüs, Aufnahme, Fehlerbehandlung, lokaler Fallback | [`src/ptt.py`](../src/ptt.py) · [`src/ptt_config.py`](../src/ptt_config.py) |
| Server | Vosk, Intents, LLM, Piper/DSP und Stimmerkennung | [`server/servitor_server.py`](../server/servitor_server.py) |
| Transport | Live-Upload, NDJSON, Sitzungen | [`src/remote_turn.py`](../src/remote_turn.py) · [`src/protocol.py`](../src/protocol.py) |
| Persistenz | Fakten, Direktiven, Verlauf und Stimmprofile auf USB-Stick | [Gedächtnis](features/memory.md) |

## Servitor-Server (CT 107) mit lokalem Fallback

`ASSISTANT_BASE_URL` enthält Serveradressen, `ASSISTANT_TOKEN` den Bearer-Token. Der Pi streamt während der Aufnahme und spielt das zurückgelieferte Audio ab. Gemessen im LAN am 07.10.2026: etwa **2,0–2,5 s vom Loslassen bis zum ersten Ton**; keine garantierte Latenz.

| Situation | Verarbeitung |
|---|---|
| CT 107 und Internet funktionieren | Vosk → direkte Antwort oder OpenRouter → Piper auf CT 107 |
| Internet/OpenRouter gestört, CT 107 erreichbar | Vosk → direkte Antwort oder **Qwen3-4B auf CT 107** → Piper |
| CT 107 nicht erreichbar, Internet verfügbar | Vosk und Piper auf dem Pi; freie Fragen via OpenRouter |
| CT 107 **und** Internet nicht erreichbar | Nur direkte Antworten mit Vosk/Piper auf dem Pi; **kein Offline-LLM** |
| Sprachkern LOKAL, CT 107 nicht erreichbar | Nur direkte Antworten; OpenRouter wird nicht verwendet |

Der Pi kann mitgeschriebenes Audio lokal transkribieren. Liegt schon ein Server-Transkript vor, kann er bei zugelassenem Modus den OpenRouter-Schritt wiederholen. Ein vorhandener Antworttext kann lokal gesprochen werden. Ein leeres Transkript führt nicht zur erneuten Erkennung.

### Offline-LLM auf CT 107

`llama.cpp` stellt Qwen3-4B auf `127.0.0.1:8766` bereit; nach OpenRouter-Fehlern wird kurzzeitig direkt lokal geantwortet. **Dieses LLM läuft nicht auf dem Pi.** Messwerte: [Regressionstest](history/servitor-regression-2026-10-07.md).

### Größeres Vosk-Modell: verworfen

`vosk-model-de-0.21` benötigt deutlich mehr RAM und war ohne speicherintensives Rescoring nicht besser als `small-de-0.15`. Ein Vergleich gegen Whisper mit **echter** WM8960-Sprache bleibt offen ([Roadmap](roadmap.md)).

## Antworten ohne LLM, Status und Charakter

`src/intents.py` beantwortet feste Fragen direkt. Die Persona (`src/llm.py`) kann Servitor oder Billy sein; Lore und Stimmeffekt sind umschaltbar. Mehr dazu: [Persönlichkeit](features/personality.md) und [Sprache](features/speech.md).

### Morgenlitanei und Wetter

Wetterdaten werden für fünf Tage auf dem **Gedächtnis-Stick** zwischengespeichert; offline sind sie nur mit vorhandenem Cache verfügbar. Nextcloud-CalDAV-Termine holt der Pi, nicht CT 107; Zugangsdaten bleiben auf dem Pi. Einrichtung unter [Betrieb](operation.md#konfiguration), Wetteranzeige unter [Display](features/display.md).

## Aktivierungswort

„Hey Jarvis“ nutzt openWakeWord **auf dem Pi**. Es ist implementiert, die echte Treffer- und Fehlalarmquote noch nicht abschließend abgenommen. Ein eigenes „Hey Servitor“ ist geplant.

## Gerätesteuerung per Sprache

`src/device_control.py` erkennt unter anderem WLAN an/aus, Ruhemodus, Neustart und Herunterfahren. **Ausgeführt werden Geräteaktionen ausschließlich auf dem Pi.** Neustart/Shutdown erfordern Bestätigung; bei unbekannter Stimme nur Taste E. [Tasten](features/controls.md) · [Wartung](features/maintenance.md).

## Alarme, Strom und Netz

PiSugar-3-Akku, Spannung, Temperatur, Last und Netzverbindung werden überwacht. Die Abschaltung bei kritischem Akkustand bleibt aktiv, selbst wenn Sprachausgabe-Alarme stummgeschaltet sind. Die sichere Abschaltung/Akkulaufzeit braucht noch eine echte Geräte-Abnahme ([Roadmap](roadmap.md)).

## Sprachausgabe

CT 107 rendert Piper Thorsten Emotional mit Servitor-DSP; der Pi kann Piper im Fallback verwenden. Die Ausgabe läuft über ALSA/WM8960. Details: [Sprache](features/speech.md).

## SPX/1, Netzwerk und Sicherheit

`POST /v1/hello` eröffnet eine HTTP-Sitzung. Anschließend werden der Gedächtniskern per Hash und nur der kurze Verlauf übertragen. `POST /v1/message` verarbeitet bislang **`ping`** (mit `ack`); Outbox, Dead-Letter, WebSocket und Server→Pi-Push sind **noch nicht umgesetzt** ([Konzept](concepts/persoenlichkeit-und-protokoll.md)).

**Sicherheitsgrenze:** Der LAN-Pfad zu Port 8765 ist HTTP; Token und Audio sind dort nicht transportverschlüsselt. Der Cloudflare-Weg nutzt HTTPS mit Bearer-Token. OpenRouter erhält Text und je nach Modus erlaubten Kontext, keine Mikrofon-Audiodaten. Journal-Logs können Transkripte enthalten. [ADR 0004](decisions/0004-servitor-server.md).

## Betrieb und Grenzen

`pi-ptt` und `pi-display` laufen als `obivan` mit systemd-Einschränkungen. Ein dedizierter Dienstbenutzer ist noch offen. **Kamera-Vision und Echounterdrückung sind nicht implementiert.** [Setup](setup.md) · [Betrieb](operation.md) · [Troubleshooting](troubleshooting.md). Frühere Benchmarks und Abnahmen liegen unter [history/](history).
