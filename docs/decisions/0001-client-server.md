# ADR 0001: Aufgaben des Pi und externe KI

Datum 05.10.2026. Status: ursprüngliche reine Client-/Homelab-Planung teilweise abgelöst.

Der Pi Zero 2 W hat begrenzte CPU und 512 MB RAM. Er übernimmt Taste, Audio und Gerätesteuerung. STT läuft inzwischen lokal mit Vosk; Online-STT bleibt optional, siehe [ADR 0003](0003-hybrid-stt.md).

Für LLM-Antworten ist OpenRouter vorgesehen, aber noch nicht angebunden. Piper-TTS ist inzwischen lokal auf dem Pi getestet; Wrapper und Installer liegen im Repo. Die frühere pauschale Aussage „STT/LLM/TTS laufen im Homelab“ ist damit überholt. Ein Homelab-/Home-Assistant-/Wyoming-Dienst ist keine Abhängigkeit der implementierten Anwendung.

Der erste vollständige Antwortpfad soll halbduplex arbeiten. Lokales LLM gehört nicht zum aktuellen Ziel. Offline-STT und lokale TTS allein ergeben noch keinen vollständig offline antwortenden Assistenten. [Aktueller Stand und Grenzen](../architecture.md).
