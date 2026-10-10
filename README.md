# Pi Voice Assistant (SERVITOR)

Sprachassistent auf einem **Raspberry Pi Zero 2 W** mit WM8960-HAT (zwei Mikrofone, zwei Lautsprecher, PTT-Taste), Pimoroni Button SHIM, Adafruit mini PiTFT 1,3″ und PiSugar 3 (Akku). Er antwortet als **Servitor Proximus**: eine kybernetische Diensteinheit, knapp, mechanisch, Fakten vor Rolle, mit verfremdeter Stimme und wahlweise Warhammer-40k-Vokabular (Lore-Stufe im Menü).

**Stand 09.10.2026:** Der Pi nimmt auf, die Rechenarbeit läuft auf dem eigenen Server **CT 107** im Proxmox-Homelab. Fällt der Server oder das Internet aus, arbeitet das System stufenweise lokal weiter.

```text
Taste halten → Pi streamt Audio → CT 107: Vosk → [Uhrzeit/Datum/Status direkt | OpenRouter | lokales Qwen3-4B]
             → Piper Thorsten + Servitor-DSP → Pi spielt ab (≈ 1,5 s nach dem Loslassen)
Server weg   → Pi: Vosk → [direkt | OpenRouter] → Piper → Lautsprecher (langsamer, aber funktionsfähig)
```

| Bereich | Stand |
|---|---|
| Antwortweg | Pi streamt während des Tastendrucks an CT 107; Antwort ca. 1,5 s nach dem Loslassen (vorher rein lokal ca. 13,7 s). [Architektur](docs/architecture.md), [ADR 0004](docs/decisions/0004-servitor-server.md) |
| Ohne LLM | Uhrzeit, Datum, Status, Akku, „wer bist du“, Wetter und die Morgenlitanei („Morgenbericht“ oder „Guten Morgen“, Antwort ohne Gruß: Datum, Uhrzeit, Wetter mit animiertem Piktogramm und Fünf-Tage-Vorhersage auf dem Display, offline vom Gedächtnis-Stick, Termine aus Nextcloud, was Aufmerksamkeit braucht; [Architektur](docs/architecture.md#morgenlitanei-und-wetter)) beantwortet ein Regelwerk direkt, auf dem Server und offline auf dem Pi (0,5–1,7 s inkl. Sprachausgabe) |
| LLM | OpenRouter, Standard `mistralai/mistral-medium-3-5`; Menü „Sprachkern“ FREI nutzt das wenig eingeschränkte `dolphin-mistral-24b-venice-edition`, LOKAL nie OpenRouter. Fällt OpenRouter aus (kein Netz, keine Credits, Timeout), antwortet Qwen3-4B lokal auf CT 107 |
| Charakter | Servitor-Systemprompt in [`src/llm.py`](src/llm.py); Datum/Uhrzeit des Bedieners werden mitgegeben, Antworten für die Sprachausgabe geglättet |
| Erkennung | Vosk `small-de-0.15`; größeres Vosk-Modell gemessen und verworfen ([Architektur](docs/architecture.md#größeres-vosk-modell-verworfen)); Whisper auf CT 107 optional (`SERVITOR_STT=whisper`, Vosk bleibt Sprachschranke und Schnellweg), Vergleich mit echter Stimme über `scripts/mic-check.py record` offen ([STT](docs/speech-to-text.md#whisper-auf-ct-107-optional)) |
| Aktivierungswort | „Hey Jarvis“ startet eine Anfrage, eine Sprechpause beendet sie; läuft auf dem Pi, abschaltbar im Menü. [Architektur](docs/architecture.md#aktivierungswort) |
| Bedienung | Quittungston (Servo + Binärfolge) beim Loslassen; Taste/SHIM A sprechen, B abbrechen, C/D Lautstärke (2 dB, halten wiederholt), E Status bzw. Menü-OK; PiTFT-Tasten öffnen ein Menü. [Button-Bedienung](docs/button-controls.md) |
| Display | Schritt, Verarbeitungsort SERVER/LOKAL, letzte Antwort, Akku, Temperatur, WLAN, Uhrzeit, Lautstärke, Menü. [Display](docs/display.md) |
| Alarme | Akku (3 Warnungen, dann Herunterfahren), Stromquelle, Unterspannung, Temperatur, Speicher, CPU, Netzwerk/Internet/Server; abschaltbar. [Architektur](docs/architecture.md#alarme-strom-und-netz) |
| Gedächtnis | Fakten, Direktiven („nenne Städte nur noch Makropolen“, „installiere die Humor-Erweiterung“) und Verlauf auf einem USB-Stick; ohne Stick kein Gedächtnis; lernt selbst mit. [Gedächtnis](docs/memory.md) |
| Kennenlernen | Menü „Kennenlernen“: 20 Stimmproben „Proximus“, Fragen zur Person, Stimmprofil; danach erkennt der Server den Bediener an der Stimme und gibt Fremden keine persönlichen Daten. [Kennenlernen](docs/memory.md#kennenlernen-und-stimmerkennung) |
| Wartung und Netz | Meldet wartende Updates (Pi und CT 107), WLAN-Signal, Latenz, DNS; „Wie ist das Netzwerk?“, „Gibt es Updates?“. [Gedächtnis, Wartung, Netz](docs/memory.md#systemwartung) |
| Gerätesteuerung | „WLAN aus/an“, „Geh schlafen“, „Starte dich neu“, „Fahr dich herunter“ („Terminiere dich selbst“ …); Neustart und Herunterfahren erst nach „Bestätigt“ oder Taste E; WLAN geht bei Bedarf selbst wieder an. [Architektur](docs/architecture.md#gerätesteuerung-per-sprache) |
| Selbsttest und Logs | Liest alle 30 min die eigenen Logs und die des Servers, behebt kleine Fehler selbst (aufgegebene Anzeige bzw. lokales LLM neu starten, Log-Kopie anstoßen) und nennt Auffälliges im Morgenbericht, ohne Rauschen; „Selbsttest“ fragt nach. Pi-Logs liegen im RAM und werden auf den Gedächtnis-Stick kopiert. [Logs und Selbsttest](docs/logs.md) |
| Wartungsmodus | Menü „Wartung“ oder Sprache; Updates und Neustarts für Pi und CT 107, jede Aktion mit Taste E bestätigt. [Wartungsmodus](docs/maintenance.md) |
| Status-LED | Farben passend zum Display, schreibt in eigenem Thread. [Button-Bedienung](docs/button-controls.md#status-led) |
| Hardware | WM8960, SHIM, PiTFT und PiSugar 3 laufen; Akkulaufzeit/Abschaltung und Kamera offen. [Hardware](docs/hardware.md) |

## Einrichten und betreiben

1. [Setup](docs/setup.md): OS, WM8960-Audio, Dienst und Offline-STT auf dem Pi.
2. [Betrieb](docs/operation.md): Konfiguration (inkl. `ASSISTANT_*` für den Server), Aktualisieren, Logs ([Logs und Selbsttest](docs/logs.md)).
3. Server CT 107: [`server/install-ct.sh`](server/install-ct.sh), Offline-LLM [`server/install-llm.sh`](server/install-llm.sh).
4. [Troubleshooting](docs/troubleshooting.md).

Vertiefung: [Architektur](docs/architecture.md), [Hardware](docs/hardware.md), [Display](docs/display.md), [Button-Bedienung und LED](docs/button-controls.md), [STT](docs/speech-to-text.md), [TTS](docs/text-to-speech.md), [Entscheidungen](docs/decisions/), [Roadmap](docs/roadmap.md).

Charakter und Lore: [Proximus/Billy – ausgearbeitete Geschichte und Sprachvorgaben](docs/concepts/proximus-billy-lore/LORE_PROXIMUS_BILLY.md) mit 33 Episoden, sechs Doom-/Wolfenstein-Sagen und dem Konzept für ausdrücklich lange Geschichten (10–20 Minuten). [Claude-Auftrag zur Implementierung](docs/concepts/proximus-billy-lore/CLAUDE_AUFTRAG.md). Status: ausgearbeitet, Laufzeitintegration noch umzusetzen.

Gesprächsgedächtnis: [Konzept für Kontext zwischen Anfragen auf dem Erinnerungskern](docs/concepts/proximus-context/KONZEPT.md) mit [Claude-Auftrag](docs/concepts/proximus-context/CLAUDE_AUFTRAG.md), [Recherche und begründeten Entscheidungen](docs/concepts/proximus-context/RECHERCHE_UND_ENTSCHEIDUNGEN.md) und [zusätzlichen Dialogfunktionen](docs/concepts/proximus-context/DIALOGFUNKTIONEN.md) sowie getesteter Offline-Referenz. Status: Konzept; Runtime-Integration noch offen.

Geheimnisse (OpenRouter-Key, Server-Token) stehen nur in `/etc/pi-voice-assistant.env` (Pi) bzw. `/etc/servitor-voice.env` (CT 107), nie im Repository. Die Proxmox-Firewall bleibt aus (Docker auf dem Host); Port 8765 ist im LAN offen und nur mit Token nutzbar.
