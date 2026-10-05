# ADR 0001: Pi als Audio-Client

Datum: 05.10.2026. Status: teilweise durch [ADR 0003](0003-hybrid-stt.md) abgelöst; die LLM-/TTS-Entscheidung bleibt bestehen.

## Kontext

Der Pi Zero 2 W besitzt begrenzten Arbeitsspeicher und Rechenleistung. WM8960 und Lautsprecher liefern die lokale Ein-/Ausgabe. Ein Homelab steht für aufwendige Verarbeitung zur Verfügung.

## Entscheidung

Der ursprüngliche Beschluss sah vor, dass der Pi Aufnahme, Taste, Wiedergabe und Gerätezustand übernimmt und STT, LLM und TTS auf stärkerer Hardware laufen. ADR 0003 ändert davon ausschließlich STT: Vosk darf lokal auf dem Pi laufen; LLM und TTS bleiben zunächst externe Verarbeitung. Der erste MVP nutzt eine Taste und arbeitet halbduplex.

## Folgen

WLAN und ein verfügbarer Homelab-Dienst sind für Sprachantworten erforderlich. Netzwerkausfälle müssen behandelt werden. Lokale Verarbeitung größerer Modelle ist kein MVP-Ziel. Kamera und Display werden nach dem Audio-MVP integriert. Das konkrete Backend bleibt offen. Die OS-Basis ist in [ADR 0002](0002-operating-system.md) festgelegt.
