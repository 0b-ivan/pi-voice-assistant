# ADR 0001: Pi als Audio-Client

Datum: 05.10.2026. Status: aus der bisherigen Projektplanung übernommen.

## Kontext

Der Pi Zero 2 W besitzt begrenzten Arbeitsspeicher und Rechenleistung. WM8960 und Lautsprecher liefern die lokale Ein-/Ausgabe. Ein Homelab steht für aufwendige Verarbeitung zur Verfügung.

## Entscheidung

Der Pi übernimmt Aufnahme, Taste, Wiedergabe und Gerätezustand. STT, LLM und TTS laufen auf stärkerer Hardware. Der erste MVP nutzt eine Taste und arbeitet halbduplex.

## Folgen

WLAN und ein verfügbarer Homelab-Dienst sind für Sprachantworten erforderlich. Netzwerkausfälle müssen behandelt werden. Lokale Verarbeitung größerer Modelle ist kein MVP-Ziel. Kamera und Display werden nach dem Audio-MVP integriert. Das konkrete Backend und die OS-Version bleiben gesonderte Entscheidungen.
