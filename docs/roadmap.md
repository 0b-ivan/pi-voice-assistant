# Nächste Aufgaben

Stand 08.10.2026. Der Funktionsstand steht in der [README](../README.md); hier steht nur verbleibende Arbeit.

## Als Nächstes

- **Spracherkennung mit echter Stimme bewerten:** etwa 10 Sätze über das WM8960-Mikrofon aufnehmen, Vosk small gegen Whisper small auf CT 107 vergleichen (Branch `feat/servitor-whisper-stt`; mit synthetischer Sprache war Vosk small genauso gut und 50-mal schneller nach dem Loslassen). Aufnahmen danach löschen.
- **Aktivierungswort mit echter Stimme abnehmen** (Trefferquote, Fehlauslösungen über einen Tag, Pausenerkennung), danach eigenes „Hey Servitor“ trainieren (openWakeWord-Trainingspipeline mit Piper-Stimmen, auf CT 107).
- **Hardware-Abnahme der Bedienung:** PiTFT-Menü, „Display aus“, Status-LED-Farben, C/D mit Wiederholung, E im und außerhalb des Menüs.
- **Weitere Funktionen ohne LLM:** z. B. Lautstärke per Sprache, Timer/Wecker, „Wiederhole“. Aktionen auf dem Pi brauchen dafür eine Rückmeldung vom Server an den Pi.
- **Charakter verfeinern** anhand echter Gespräche ([`server/sample-persona.py`](../server/sample-persona.py)).

## Server und Netz

- ~~Zugang über Cloudflare als zweite URL~~: erledigt 08.10.2026, `https://proximus.obivan.org` (nur Bearer-Token). Ein Access-Service-Token bleibt optional möglich (`ASSISTANT_CF_ACCESS_*`).
- Opus für den Internetweg erst mit schnellerer Dekodierung auf dem Pi (ffmpeg kostet dort ca. 5 s).
- LLM-Streaming mit satzweiser Synthese prüfen, um die Zeit bis zum ersten Ton weiter zu senken.

## Betrieb und Hardware

- PiSugar 3: Akkukapazität, Laufzeit und kontrolliertes Abschalten.
- Dedizierter Dienstbenutzer statt `obivan`.
- SSH/Router über LAN und die drei USB-Ports des Hubs abnehmen.
- PTT-Grenztests: Prellimpulse, Aufnahme unter 100 ms, Boot mit gehaltener Taste.
- Endgültige Montage/Gehäuse; Kamera identifizieren. Wake Word später.
- Möglicherweise zeitkritischer Test in der Suite (ein Hänger, ein einmaliger Fehler, nicht reproduzierbar).
