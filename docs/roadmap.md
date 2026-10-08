# Nächste Aufgaben

Stand 06.10.2026. Der aktuelle Funktionsstand steht in der [README](../README.md); diese Liste enthält nur verbleibende Arbeit.

## Jetzt: lokale Sprache zuverlässig betreiben

1. Textübergabe im aktuellen Piper-Wrapper prüfen/korrigieren: `--` darf nicht Teil des Sprachtexts sein. Die entsprechenden Benchmarkfehler sind seit `d056897` behoben.
2. Feste Piper-Statusansagen einmal erzeugen und als WAV cachen. [Pi-Messungen](piper-resources.md) zeigen schnelle geladene Synthese, aber deutlichen Speicherdruck neben Vosk; vorerst keinen dauerhaften Piper-Prozess aktivieren.
3. Wechsel zwischen STT und TTS auf dem Pi testen: nächste Vosk-Erkennung nach dem Swapping, Cache-Treffer ohne Synthese und variable Ansagen. Deren Latenz ist noch nicht gemessen.
4. SHIM-Dienstabnahme vervollständigen: A gegenüber GPIO17, beide gemeinsam, B während Aufnahme/STT/Ansage, C/D mit ausgelesenen Pegeln, LED-Farben und Reboot mit der aktuellen Version.
5. Offline-Vosk anhand mehrerer bekannter Sätze bewerten; optionale Command-Grammar nur für feste Kommandos evaluieren. Kein Versprechen, dass sie freie Sprache verbessert.

## Jetzt: vollständigen Sprachloop auf Hardware abnehmen

- Integrierten Pfad Vosk → OpenRouter-LLM → Piper/Servitor → WM8960 auf dem Pi testen.
- STT-, LLM- und TTS-Latenzen aus den strukturierten `latency`-/TTS-Events messen und die End-to-End-Latenz bewerten.
- B/Abbruch während LLM und TTS sowie OpenRouter-Timeout/Netzausfall prüfen; der Dienst muss danach weiter nutzbar sein.
- Wiederholte Interaktionen auf Speicher, zram und Audio-xruns prüfen.
- Vosk-Qualität mit mehreren bekannten Sätzen und unterschiedlichen Sprechabständen messen; Cloud-STT ist bewusst nicht Teil der Architektur.

## Betrieb und Hardware

- Offene Codepunkte aus PR #4/#13 prüfen: dedizierter Dienstbenutzer, Runtime-Pfad und Installer-Preflight.
- PiSugar 3: Akkukapazität, Laufzeit und kontrolliertes Abschalten testen (Revision und Ladestand-Anzeige erledigt).
- SSH/Routertest über LAN und alle drei externen USB-Ports abnehmen.
- Verbleibende PTT-Grenztests: elektrische Prellimpulse, Aufnahme unter 100 ms, Boot mit gehaltener Taste.
- Image-Prüfsumme, WLAN-Land/Zeitzone bei nächster Systemaufnahme ergänzen.
- PiTFT GPIO23/24 sinnvoll belegen; Boot-/Statusdienst und Live-Zustände inklusive `DENKEN` sind umgesetzt. Danach endgültige Montage/Gehäuse.
- Kamera identifizieren/testen. Wake Word erst später bewerten.

Abnahmekriterium für den ersten antwortenden Assistenten: Taste → verständlicher Text → hörbare deutsche Antwort; nach einem Fehler wieder nutzbar. Der Codepfad ist implementiert, die Hardware-Abnahme dieses vollständigen Ablaufs steht noch aus.
