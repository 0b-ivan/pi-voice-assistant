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

- Mikrofon-Audio verlässt den Pi jetzt, aber nur zum eigenen CT 107: im LAN direkt, als zweite URL über den Cloudflare-Tunnel `https://proximus.obivan.org` (seit 08.10.2026; nur Bearer-Token, kein Access-Service-Token). OpenRouter erhält weiterhin nur Text.
- Vosk bleibt der einzige STT-Anbieter (auf dem Server und im Fallback).
- Der Zugriff ist per Bearer-Token geschützt; das Token steht nur in `/etc/pi-voice-assistant.env` bzw. `/etc/servitor-voice.env`.
- Restrisiko: Im LAN gehen Token und Audio unverschlüsselt per HTTP an CT 107. Wer im IoT-Netz mitlesen kann, sieht beides. Akzeptiert, weil der Weg nur im eigenen Netz verläuft; Cloudflare-Access-Zugangsdaten werden nur über HTTPS gesendet.
- Über `proximus.obivan.org` ist `/v1/turn` und `/v1/speak` aus dem Internet erreichbar; geschützt nur durch das Bearer-Token (mindestens 32 Zeichen, Vergleich mit `hmac.compare_digest`), Größenlimits und das Ratenlimit pro `CF-Connecting-IP`. Diesem Header glaubt der Server nur von `SERVITOR_TRUSTED_PROXIES` (CT 100, `172.22.2.100`). Der Client sendet einen eigenen User-Agent, weil Cloudflare Pythons Standard mit Fehler 1010 ablehnt.

## Folgen

- Gemessen am 07.10.2026: Loslassen bis Wiedergabestart ca. 2,0–2,5 s, Server 1,67 s ([Architektur](../architecture.md#servitor-server-ct-107-mit-lokalem-fallback)).
- Netz- oder Serverausfall kostet Latenz, aber keine Funktion.
- Ohne Internet oder ohne OpenRouter-Guthaben antwortet ein lokales LLM auf CT 107 (llama.cpp, Qwen3-4B-Instruct-2507); der gesamte Durchlauf bleibt dann im Homelab. Dessen Antwortqualität ist deutlich geringer als die von OpenRouter.
- Die Proxmox-Firewall bleibt deaktiviert (Docker auf dem Host); Port 8765 ist im LAN offen und nur per Token nutzbar.
