# Nächste Aufgaben

Stand 05.10.2026. Der aktuelle Funktionsstand steht in der [README](../README.md); diese Liste enthält nur verbleibende Arbeit.

## Jetzt: lokale Sprache zuverlässig betreiben

1. Textübergabe im geprüften Wrapperstand aus PR #14 prüfen/korrigieren: `--` darf nicht Teil des Sprachtexts sein. Die entsprechenden Benchmarkfehler sind seit `d056897` behoben.
2. Feste Piper-Statusansagen einmal erzeugen und als WAV cachen. [Pi-Messungen](piper-resources.md) zeigen schnelle geladene Synthese, aber deutlichen Speicherdruck neben Vosk; vorerst keinen dauerhaften Piper-Prozess aktivieren.
3. Wechsel zwischen STT und TTS auf dem Pi testen: nächste Vosk-Erkennung nach dem Swapping, Cache-Treffer ohne Synthese und variable Ansagen. Deren Latenz ist noch nicht gemessen.
4. SHIM-Dienstabnahme vervollständigen: A gegenüber GPIO17, beide gemeinsam, B während Aufnahme/STT/Ansage, C/D mit ausgelesenen Pegeln, LED-Farben und Reboot mit der aktuellen Version.
5. Offline-Vosk anhand mehrerer bekannter Sätze bewerten; optionale Command-Grammar nur für feste Kommandos evaluieren. Kein Versprechen, dass sie freie Sprache verbessert.

## Danach: vollständiger Sprachloop

- OpenRouter-LLM anbinden; tatsächliche Fehler-/Timeoutbehandlung festlegen.
- Transcript → LLM → Piper → WM8960 halbduplex orchestrieren und auf dem Pi testen.
- Wiederholte Interaktionen und Gesamtlatenz messen.
- `auto` bei realem Netz-/API-Ausfall abnehmen, wenn Online-STT wieder gewünscht ist; der Pi bleibt vorerst `vosk`.

## Betrieb und Hardware

- Offene Codepunkte aus PR #4/#13 prüfen: dedizierter Dienstbenutzer, Runtime-Pfad und Installer-Preflight.
- PiSugar2-Revision, Akkukapazität, Laufzeit und kontrolliertes Abschalten testen.
- SSH/Routertest über LAN und alle drei externen USB-Ports abnehmen.
- Verbleibende PTT-Grenztests: elektrische Prellimpulse, Aufnahme unter 100 ms, Boot mit gehaltener Taste.
- Image-Prüfsumme, WLAN-Land/Zeitzone bei nächster Systemaufnahme ergänzen.
- Kamera identifizieren/testen, Adafruit mini PiTFT anschließen und Montage prüfen; anschließend Gehäuse. Wake Word erst später bewerten.

Abnahmekriterium für den ersten antwortenden Assistenten: Taste → verständlicher Text → hörbare deutsche Antwort; nach einem Fehler wieder nutzbar. Dieses Kriterium ist noch nicht erreicht.
