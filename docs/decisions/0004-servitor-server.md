# ADR 0004: Servitor-Server im eigenen Homelab, lokaler Fallback auf dem Pi

Datum 07.10.2026. Status: angenommen; ersetzt [ADR 0003](0003-hybrid-stt.md) für den Normalbetrieb.

## Kontext

Auf dem Pi Zero 2 W dauerte ein vollständiger lokaler Durchlauf (Vosk, OpenRouter, Piper, Servitor-DSP) etwa 13,7 s bis zum ersten Ton. Vosk und Piper konkurrieren um 512 MB RAM. Auf Proxmox steht mit CT 107 (`servitor-voice`) ein eigener Container mit residenten Modellen bereit.

## Entscheidung

Ist `ASSISTANT_BASE_URL` gesetzt, streamt der Pi die Aufnahme während des Tastendrucks an den [Servitor-Dienst](../../server/servitor_server.py) auf CT 107. Erkennung, LLM-Aufruf, Synthese und DSP laufen dort; der Pi spielt das fertige Audio ab.

```text
Pi: PTT -> arecord -> chunked POST /v1/turn ──LAN──> CT 107: Vosk -> OpenRouter -> Piper -> DSP
Pi: aplay <──────────── NDJSON (Fortschritt, Text, Audio) ───┘
```

Fällt der Server aus, setzt der Pi lokal dort fort, wo der Server aufgehört hat (Vosk auf der mitgeschriebenen WAV, lokales LLM bzw. lokale Piper-Ausgabe). Ohne `ASSISTANT_BASE_URL` arbeitet der Pi wie unter ADR 0003.

## Abgrenzung zu ADR 0003

- Mikrofon-Audio verlässt den Pi jetzt, aber nur zum eigenen CT 107: im LAN direkt, später über Cloudflare mit Access-Service-Token. OpenRouter erhält weiterhin nur Text.
- Vosk bleibt der einzige STT-Anbieter (auf dem Server und im Fallback).
- Der Zugriff ist per Bearer-Token geschützt; das Token steht nur in `/etc/pi-voice-assistant.env` bzw. `/etc/servitor-voice.env`.

## Folgen

- Gemessen am 07.10.2026: Loslassen bis Wiedergabestart ca. 2,0–2,5 s, Server 1,67 s ([Architektur](../architecture.md#servitor-server-ct-107-mit-lokalem-fallback)).
- Netz- oder Serverausfall kostet Latenz, aber keine Funktion.
- Die Proxmox-Firewall bleibt deaktiviert (Docker auf dem Host); Port 8765 ist im LAN offen und nur per Token nutzbar.
