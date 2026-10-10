# Architektur

Der **Pi Zero 2 W** ist der Client für Mikrofon, Tasten, Display, LED und Wiedergabe. Im Normalbetrieb übernimmt der **Servitor-Server CT 107** die rechenintensiven Schritte. Die Entscheidung für CT 107 entlastet die 512 MB RAM des Pi; Home Assistant und Wyoming sind keine Abhängigkeit.

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
| Persistenz | Fakten, Direktiven, Verlauf und Stimmprofile auf USB-Stick | [Gedächtnis](operation.md) |

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

`llama.cpp` stellt Qwen3-4B auf `127.0.0.1:8766` bereit; nach OpenRouter-Fehlern wird kurzzeitig direkt lokal geantwortet. **Dieses LLM läuft nicht auf dem Pi.**

### Größeres Vosk-Modell: verworfen

`vosk-model-de-0.21` benötigt deutlich mehr RAM und war ohne speicherintensives Rescoring nicht besser als `small-de-0.15`. Ein Vergleich gegen Whisper mit **echter** WM8960-Sprache bleibt offen ([Roadmap](roadmap.md)).

## Antworten ohne LLM, Status und Charakter

`src/intents.py` beantwortet feste Fragen direkt. Die Persona (`src/llm.py`) kann Servitor oder Billy sein; Lore und Stimmeffekt sind umschaltbar. Mehr dazu: [Persönlichkeit](user-guide.md) und [Sprache](operation.md).

### Morgenlitanei und Wetter

Wetterdaten werden für fünf Tage auf dem **Gedächtnis-Stick** zwischengespeichert; offline sind sie nur mit vorhandenem Cache verfügbar. Nextcloud-CalDAV-Termine holt der Pi, nicht CT 107; Zugangsdaten bleiben auf dem Pi. Einrichtung unter [Betrieb](operation.md#konfiguration), Wetteranzeige unter [Display](user-guide.md).

## Aktivierungswort

„Hey Jarvis“ nutzt openWakeWord **auf dem Pi**. Es ist implementiert, die echte Treffer- und Fehlalarmquote noch nicht abschließend abgenommen. Ein eigenes „Hey Servitor“ ist geplant.

## Gerätesteuerung per Sprache

`src/device_control.py` erkennt unter anderem WLAN an/aus, Ruhemodus, Neustart und Herunterfahren. **Ausgeführt werden Geräteaktionen ausschließlich auf dem Pi.** Neustart/Shutdown erfordern Bestätigung; bei unbekannter Stimme nur Taste E. [Tasten](user-guide.md) · [Wartung](operation.md).

## Alarme, Strom und Netz

PiSugar-3-Akku, Spannung, Temperatur, Last und Netzverbindung werden überwacht. Die Abschaltung bei kritischem Akkustand bleibt aktiv, selbst wenn Sprachausgabe-Alarme stummgeschaltet sind. Die sichere Abschaltung/Akkulaufzeit braucht noch eine echte Geräte-Abnahme ([Roadmap](roadmap.md)).

## Sprachausgabe

CT 107 rendert Piper Thorsten Emotional mit Servitor-DSP; der Pi kann Piper im Fallback verwenden. Die Ausgabe läuft über ALSA/WM8960. Details: [Sprache](operation.md).

## SPX/1, Netzwerk und Sicherheit

`POST /v1/hello` eröffnet eine HTTP-Sitzung. Anschließend werden der Gedächtniskern per Hash und nur der kurze Verlauf übertragen. `POST /v1/message` verarbeitet bislang **`ping`** (mit `ack`); Outbox, Dead-Letter, WebSocket und Server→Pi-Push sind **noch nicht umgesetzt**.

**Sicherheitsgrenze:** Der LAN-Pfad zu Port 8765 ist HTTP; Token und Audio sind dort nicht transportverschlüsselt. Der Cloudflare-Weg nutzt HTTPS mit Bearer-Token. OpenRouter erhält Text und je nach Modus erlaubten Kontext, keine Mikrofon-Audiodaten. Journal-Logs können Transkripte enthalten.

## Betrieb und Grenzen

`pi-ptt` und `pi-display` laufen als `obivan` mit systemd-Einschränkungen. Ein dedizierter Dienstbenutzer ist noch offen. **Kamera-Vision und Echounterdrückung sind nicht implementiert.** [Installation](setup.md) · [Betrieb und Fehlersuche](operation.md). Frühere Benchmarks und Abnahmen: siehe Git-Historie unten.


## Sicherheitsdetails

Der öffentliche Cloudflare-Weg `https://proximus.obivan.org` stellt `/v1/turn` und `/v1/speak` bereit. Schutz: Bearer-Token (mindestens 32 Zeichen, konstanter Vergleich), Größen-/Ratenlimits. `CF-Connecting-IP` nur von `SERVITOR_TRUSTED_PROXIES` akzeptieren (im vorhandenen Aufbau CT 100, `172.22.2.100`); es ist kein Cloudflare-Access-Service-Token eingerichtet. Port 8765 ist im LAN offen; die Proxmox-Firewall ist im dokumentierten Aufbau deaktiviert. Token und Audio sind dort über HTTP lesbar.

Stimmprofile und Passphrasen sind kein starker Identitätsnachweis: Passphrase liegt im Klartext auf dem Stick, kurze Äußerungen unter einer Sekunde gelten als Bediener, Pi-Fallback hat keine Stimmerkennung. Ohne Profil greifen diese Beschränkungen nicht. Private Archive von Env/ALSA und Logs nicht veröffentlichen. Root-Aktionen sind fest begrenzt: siehe [Wartung](operation.md#wartungsmodus).

## Entscheidungen und Historie

Aktuelle Entscheidungen: Raspberry Pi OS Lite 64-bit/Trixie ohne Desktop; System-Python und APT-libgpiod für den Pi-Dienst, Vosk im Vendor-Verzeichnis, Piper im eigenen venv. Vosk bleibt der STT-Anbieter; Audio geht im Normalbetrieb nur an CT 107, OpenRouter bekommt Text. Pi-Fallback hat kein lokales LLM. Antworten sind halbduplex; Kamera/Vision und Echounterdrückung fehlen.

Historische Testberichte, vier ADRs und ausführliche Servitor-/Billy-Konzepte bleiben im [festen Git-Stand vor der Konsolidierung](https://github.com/0b-ivan/pi-voice-assistant/tree/6845df8f3f864d3e7d48eb1f47fbfcdedd612e9d/docs) auffindbar. Sie beschreiben damalige Tests bzw. Entwürfe und ersetzen keine aktuelle Geräteabnahme. Künftige Änderungen: [Git-Historie](https://github.com/0b-ivan/pi-voice-assistant/commits/docs/roadmap-and-documentation-cleanup-20261010/). Neue Testberichte brauchen keine zusätzlichen Markdown-Dateien; Ergebnisse gehören in PRs/Commits, offene Abnahmen in die [Roadmap](roadmap.md).
