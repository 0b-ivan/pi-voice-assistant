# ADR 0001: Aufgaben des Pi und externe KI

Datum 05.10.2026. Status: ursprüngliche reine Client-/Homelab-Planung teilweise abgelöst.

Der Pi Zero 2 W hat begrenzte CPU und 512 MB RAM. Er übernimmt Taste, Audio und Gerätesteuerung. Vosk ist der einzige STT-Anbieter; OpenRouter erhält kein Mikrofon-Audio. Seit 07.10.2026 laufen STT, LLM und TTS im Normalbetrieb auf dem eigenen Servitor-Server (CT 107, [ADR 0004](0004-servitor-server.md)). Auf dem Pi laufen Vosk und Piper nur noch als Fallback bzw. ohne konfigurierten Server ([ADR 0003](0003-hybrid-stt.md)).

Für den **Normalbetrieb** gilt [ADR 0004](0004-servitor-server.md): Der Pi streamt die Aufnahme an CT 107; Erkennung, LLM (OpenRouter, bei Ausfall lokales Qwen3-4B auf CT 107) und Synthese mit Servitor-DSP laufen dort, der Pi spielt nur ab. Der Servitor-Dienst im Homelab ist damit eine Abhängigkeit des schnellen Pfads, aber keine zwingende: ohne Server arbeitet der Pi weiter.

Für den **Betrieb ohne konfigurierten Server und als Fallback** gilt weiterhin der ursprüngliche Pi-Pfad: Vosk und Piper lokal auf dem Pi, LLM über die getrennte OpenRouter-Komponente `src/llm.py`. Nur der LLM-Schritt braucht dort Netzwerkzugriff. Home Assistant oder Wyoming sind in keinem der beiden Wege eine Abhängigkeit.

Der Antwortpfad arbeitet halbduplex. [Aktueller Stand und Grenzen](../architecture.md).
