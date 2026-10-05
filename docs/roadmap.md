# Nächste Aufgaben

Stand 05.10.2026. Der aktuelle Funktionsstand steht in der [README](../README.md); diese Liste enthält nur verbleibende Arbeit.

## Jetzt: lokale Sprache zuverlässig betreiben

1. Offene Piper-Benchmarkfehler aus PR #16 beheben: identischer Eingabetext und Teilbericht bei frühem Signal. Dasselbe `--`-Textproblem betrifft auch `speak.py` aus PR #14; siehe [Projektprüfung](project-review.md).
2. Piper mit frischem und einmal geladenem Modell neben bereits geladenem Vosk messen: Synthesezeit, CPU, RSS, verfügbares RAM, Swap-Aktivität, Temperatur/Throttling. Noch keine Ergebnisse für diesen Vergleich vorhanden.
3. Danach entscheiden, ob ein geladener Piper-Prozess oder vorbereitete Status-WAVs helfen und in den RAM passen. Keine zusätzliche permanente Komponente vor Messung einführen.
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
