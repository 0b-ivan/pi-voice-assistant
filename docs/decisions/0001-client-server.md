# ADR 0001: Aufgaben des Pi und externe KI

Datum 05.10.2026. Status: ursprüngliche reine Client-/Homelab-Planung teilweise abgelöst.

Der Pi Zero 2 W hat begrenzte CPU und 512 MB RAM. Er übernimmt Taste, Audio und Gerätesteuerung. STT läuft ausschließlich lokal mit Vosk; OpenRouter erhält kein Mikrofon-Audio. Siehe [ADR 0003](0003-hybrid-stt.md). Seit 07.10.2026 laufen STT, LLM und TTS im Normalbetrieb auf dem eigenen Servitor-Server (CT 107), lokal nur noch als Fallback: [ADR 0004](0004-servitor-server.md).

LLM-Antworten laufen über die getrennte OpenRouter-Komponente `src/llm.py`; Piper-TTS läuft lokal auf dem Pi. Die frühere pauschale Aussage „STT/LLM/TTS laufen im Homelab“ ist damit überholt. Ein Homelab-/Home-Assistant-/Wyoming-Dienst ist keine Abhängigkeit der implementierten Anwendung.

Der erste vollständige Antwortpfad soll halbduplex arbeiten. Lokales LLM gehört nicht zum aktuellen Ziel. STT und TTS bleiben lokal; der Assistent benötigt nur für den LLM-Schritt Netzwerkzugriff. [Aktueller Stand und Grenzen](../architecture.md).
