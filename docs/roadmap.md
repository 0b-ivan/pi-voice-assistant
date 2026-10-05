# Roadmap und Aufgaben ✅

## Phase 0 — Grundlage

- [x] Repositorystruktur und erste Dokumentation erstellen.
- [x] Bestätigten Hardwarebestand und offene Punkte festhalten.
- [x] Client/Server-Entscheidung dokumentieren.
- [x] GitHub-Repository anlegen und initiale Dokumentation hochladen.
- [x] OS-Entscheidung dokumentieren: Raspberry Pi OS Lite 64-bit / Trixie.
- [x] Display anhand des Fotos identifizieren: Adafruit mini PiTFT 1,3″, 240 × 240.
- [ ] Kamerasensor und PiSugar2-Revision feststellen.

## Phase 1 — Hardware in Betrieb nehmen

- [x] Trixie 64-bit mit Imager auf SD-Karte schreiben; [Screenshots dokumentiert](setup.md).
- [x] Ersten Boot, Netzwerkerreichbarkeit und SSH-Anmeldung bestätigen.
- [x] Systemupdate durchführen; Git, ALSA- und I²C-Werkzeuge bereitstellen.
- [x] SSH und Netzwerkerreichbarkeit nach dem angestoßenen Neustart bestätigen.
- [ ] WLAN-Schnittstelle und Verbindung explizit erfassen.
- [x] Laufenden Kernel nach Neustart und ALSA-Bestandsaufnahme dokumentieren.
- [x] Vorhandenes WM8960-Kernelmodul und Overlay aktivieren; Aufnahme-/Wiedergabegerät nach Neustart bestätigt (kein zusätzlicher Treibercommit).
- [x] Lautsprecherwiedergabe testen und passende Lautstärke bestätigen; ALSA-Zustand speichern.
- [x] Mikrofonaufnahme und Wiedergabe testen; Aufnahmequalität mit Eingangsboost 3 bestätigt.
- [x] Gesamten ALSA-Zustand nach Mikrofonanpassung speichern und Audio nach Neustart prüfen; Nutzer bestätigt „passt“.
- [x] Speaker-Pegel auslesen: beide Kanäle 121 / 127, 95 %, 0,00 dB bestätigt.
- [x] Tasten-GPIO identifizieren: BCM17 / Pin 11; gpiochip0 (pinctrl-bcm2835), Offset 17 als freier Eingang bestätigt.
- [x] Aktiv-Low-Polarität und normale Tastenfunktion am HAT bestätigen (19 Probezyklen).
- [ ] Gezielte Entprellungs-, Kurzdrück- und Grenztests am HAT durchführen.
- [ ] Akkuversorgung und sauberes Herunterfahren testen.

Abnahme: Aufnahme und Wiedergabe funktionieren nach Neustart.

## Phase 2 — Sprach-MVP

- [x] Lokalen PTT-Recorder mit Halten/Loslassen, Entprellung und Zeitlimit implementieren; automatisierte Tests bestanden.
- [x] Normalen PTT-Aufnahme-/Wiedergabeablauf auf dem Pi inklusive Neustart abnehmen; Nutzer bestätigt „funktioniert“ / „passt“.
- [x] OpenRouter als STT-Ziel auswählen und deutschen Standalone-Test auf dem Pi erfolgreich durchführen.
- [x] OpenRouter-STT-Adapter ohne zusätzliche Python-Abhängigkeiten implementieren.
- [x] PTT→STT-Übergabe und Sperre/Resync während Verarbeitung implementieren.
- [x] Vosk als optionales lokales deutsches STT-Backend implementieren.
- [x] `STT_PROVIDER=openrouter|vosk|auto` mit OpenRouter→Vosk-Fallback implementieren.
- [x] Vosk-Installationspfad und kleines deutsches Modell dokumentieren.
- [x] Vosk mit echter WM8960-Aufnahme auf dem Pi Zero 2 W abnehmen; Qualität, Latenz und RAM gemessen.
- [ ] `auto` mit realem OpenRouter→Vosk-Ausfall auf dem Pi Zero 2 W abnehmen.
- [x] Integrierten PTT→Vosk-Ablauf nach Deployment auf Hardware abnehmen.
- [ ] OpenRouter-LLM an den erkannten Text anbinden.
- [ ] Deutsche TTS-Komponente auswählen und anbinden.
- [ ] Antwort über WM8960 ausgeben und Playback-Sperre implementieren.
- [ ] Verbleibende Grenz- und Fehlerprüfungen auf Hardware durchführen; siehe [Prüfplan](push-to-talk.md).
- [ ] Durchgehende deutsche Sprachinteraktion testen; Latenzen messen.

Abnahme: Taste → Frage → hörbare Antwort; Wiederherstellung nach Netzwerkausfall.

## Phase 3 — Zuverlässiger Betrieb

- [x] systemd-Unit und Konfiguration für lokalen PTT-Dienst erstellen.
- [x] systemd-Installation, Autostart und PTT nach Neustart am Pi bestätigen.
- [x] Installationsskript für PTT + OpenRouter-STT bereitstellen.
- [x] systemd-Dienst mit integrierter Vosk-STT-Konfiguration nach Merge erneut abnehmen.
- [x] Speicherverbrauch des geladenen Vosk-Dienstes messen (145728 kB RSS im Test).
- [ ] Startzeit, Akkulaufzeit und Temperatur messen.
- [ ] Wiederholte Interaktionen und Dienstneustart testen.
- [ ] Installationsanleitung mit tatsächlich getesteten Versionen vervollständigen.

## Phase 4 — Erweiterungen

- [ ] Display mit Zustands- und Akkuanzeige.
- [ ] Kamera und explizit ausgelöste Bildanfragen.
- [ ] Wake Word und Unterbrechen der Wiedergabe evaluieren.
- [ ] Gehäuse und mobile Bedienung verbessern.

Priorität: Offline-STT mit Vosk stabilisieren und die Erkennungsqualität verbessern; insbesondere optionale Command-Grammar für kurze Befehle evaluieren. Der `auto`-Fallback wird später abgenommen. Danach LLM-Anbindung und deutsche TTS fortsetzen.
