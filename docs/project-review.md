# Projektprüfung vom 05.10.2026

Geprüft: GitHub `main` **55bf5df**, alle PRs **#1–#16** mit Diskussionen und Review-Threadzuständen; offene Heads #14 **ae3c927**, #16 **87ca1ba**; verfügbare Projekt-Chats und die PTT-Codex-Chats. Dies ist ein Dokumentations-/Codeabgleich, keine neue Hardware-Abnahme. Offene PRs werden durch diese Doku nicht gemergt oder verändert.

## Nachtrag: PR #16 und Pi-Messungen

Die folgende ursprüngliche Prüfung beschreibt den Stand vor `d056897`. Beide
Benchmarkbefunde wurden inzwischen mit diesem Commit behoben; 56 Tests bestanden
unter Python 3.13 auf GitHub. Die [zwei Pi-Messungen](piper-resources.md) zeigen
schnelle Synthese mit geladenem Modell, aber deutlichen Speicherdruck neben Vosk.
Vorerst keinen dauerhaften Piper-Prozess aktivieren; Status-WAV-Cache als nächsten
Schritt prüfen. Die historischen Review-Threadzustände unten wurden nicht erneut
erhoben. Die Benchmarkkorrektur behebt nicht automatisch den Wrapper aus PR #14.

## Auffällige Aussagen und Korrekturen

- **Kein vollständiger Assistent:** main endet am Transcript. LLM und automatische Antwortwiedergabe fehlen. Lokales STT/TTS bedeutet nicht automatisch lokale KI-Antworten.
- **Pi-Deployment weiter als main:** Piper läuft auf dem Pi aus einem Feature-Stand, ist aber noch nicht gemergt. README trennt beide Stände. Historische detached Checkouts verändern den Dienst erst durch den Installer.
- **WM8960 braucht hier keinen zusätzlichen Waveshare-Treiber:** vorhandenes Kernelmodul/Overlay funktioniert. Keine Neuinstallation/DKMS-Migration ohne konkreten neuen Befund.
- **Falsche Displayannahmen in frühen Chats:** tatsächlich Adafruit mini PiTFT 1,3″; Waveshare/Pico-Bezeichnungen und daraus abgeleitete GPIO19/20/21-Konflikte treffen nicht auf das vorhandene Display zu. [Adafruit-Pinout](https://learn.adafruit.com/adafruit-mini-pitft-135x240-color-tft-add-on-for-raspberry-pi/pinouts) bestätigt die dokumentierte getrennte Signalbelegung. Montage bleibt offen.
- **Audiopegel:** 95 % / 0 dB war früher korrekt, letzte zurückgemeldete Speaker-Ausgabe ist 80 % / −19 dB. Playback und Speaker sind verschiedene Regler; C/D ändern nur Playback. Historie ist keine aktuelle Sollkonfiguration.
- **Asynchrone STT:** seit PR #13 läuft STT im Thread; alte Texte beschrieben synchrones Blockieren. B verwirft das Ergebnis, native Verarbeitung läuft zu Ende.
- **Unvollständige manuelle Installation:** alte PTT-Anleitung kopierte zwei Dateien, benötigt werden inzwischen vier Dienstmodule. Setup verwendet den vorhandenen Installer und stellt nach Tests den Dienst wieder her.
- **Unbelegte Qualitätsgarantien:** Kanal-/SoX-Vergleich spricht beim getesteten Clip für das Vosk-Modell als Limit, beweist aber keine allgemeine Mikrofon-/Resampler-Perfektion. Command-Grammar ist noch eine Idee für feste Befehle.
- **TTS-Latenz:** Journal zeigt 24 s für Status→speech_finished, nicht isolierte Synthesezeit. Modell-Neustart ist ein plausibler Kostenanteil, Zahlen für residente Piper-Optimierung fehlen. `speak.sh` aus einem Chat existiert nicht im geprüften Repo.
- **Unnötige Architekturpfade:** kein Home-Assistant-/Wyoming-/Homelab-Backend implementiert, kein Wake Word, Streaming, Cache oder dauerhafter Piper-Prozess. Diese Möglichkeiten werden nicht als Setup-Abhängigkeiten dargestellt.

## PRs und Reviews

„Offen“ in der Tabelle meint tatsächlich unaufgelöste Review-Threads zum Prüfzeitpunkt, auch bei gemergten PRs. Ein Merge oder veralteter Copilot-Überblick beweist weder Behebung noch aktuellen Fehler. Kommentare wurden gegen Code/Doku abgeglichen; Threads wurden nicht pauschal aufgelöst.

| PR | Zustand | Ergebnis / Nacharbeit |
|---|---|---|
| [#1](https://github.com/0b-ivan/pi-voice-assistant/pull/1) | Gemergt | SD-Installation; keine Findings |
| [#2](https://github.com/0b-ivan/pi-voice-assistant/pull/2) | Gemergt | Bootnachweis-Link behoben/resolved; Historie erhalten |
| [#3](https://github.com/0b-ivan/pi-voice-assistant/pull/3) | Gemergt | Starre DHCP-IP behoben/resolved; aktuelle IP als Alternative zu Hostname |
| [#4](https://github.com/0b-ivan/pi-voice-assistant/pull/4) | Gemergt, 2 Threads offen | Runtime-Pfad in Unit fest, Option wirkte frei; Doku/Beispiel begrenzen ihn jetzt. Dedizierter Benutzer weiterhin nicht umgesetzt: Unit/Installer nutzen obivan |
| [#5](https://github.com/0b-ivan/pi-voice-assistant/pull/5) | Gemergt | PTT übernommen; keine Review-Findings |
| [#6](https://github.com/0b-ivan/pi-voice-assistant/pull/6) | Gemergt, 1 Thread offen | Dienststart nach Vordergrundtest fehlte; neue Anleitung enthält explizite Wiederherstellung |
| [#7](https://github.com/0b-ivan/pi-voice-assistant/pull/7) | Gemergt, 1 Thread offen | Kleine Grammatikstelle und alte Abnahmesätze durch kompakte PTT-Abnahme ersetzt |
| [#8](https://github.com/0b-ivan/pi-voice-assistant/pull/8) | Gemergt, 3 resolved | HTTP-IncompleteRead normalisiert/testabgedeckt; Secret root:obivan/0640. Frühere Installationsfixes waren später durch neue Module wieder überholt |
| [#9](https://github.com/0b-ivan/pi-voice-assistant/pull/9) | Gemergt, 1 resolved | Vosk Model-/Recognizer-Ausnahmen normalisiert/testabgedeckt |
| [#10](https://github.com/0b-ivan/pi-voice-assistant/pull/10) | Gemergt | Erweiterte Hardware/Einzeltests; keine Findings |
| [#11](https://github.com/0b-ivan/pi-voice-assistant/pull/11) | Gemergt, 1 Thread offen | PR-Text und spätere Abnahme widersprachen sich. Doku trennt Ausgabe, Nutzer-Sichtprüfung und neue Dienstabnahme; frühere Pauschalbestätigung ersetzt keinen aktuellen Reboot-Test |
| [#12](https://github.com/0b-ivan/pi-voice-assistant/pull/12) | Gemergt | Reale Vosk-Abnahme/Messwerte; keine Findings |
| [#13](https://github.com/0b-ivan/pi-voice-assistant/pull/13) | Gemergt, 1 Thread offen | Installer prüft smbus/i2c-Gruppe, aber nicht i2cdetect. Offen im Code; Setup prüft es vorab und behauptet keinen vollständigen Installer-Preflight |
| [#14](https://github.com/0b-ivan/pi-voice-assistant/pull/14) | Offen, 4 resolved | Ownership-/Test-/Link-Findings auf Head behoben. Zusätzlich hier gefunden: CLI-Text enthält -- wie #16; reale Eingabe prüfen/korrigieren |
| [#15](https://github.com/0b-ivan/pi-voice-assistant/pull/15) | Gemergt | SHIM-Dienststart/Transkripte, keine Findings. Neuere Chatlogs zeigen danach erfolgreiche TTS-Statusausgabe |
| [#16](https://github.com/0b-ivan/pi-voice-assistant/pull/16) | Offen, 2 Threads offen | Früher Interrupt ohne Teilreport und verschiedene CLI/API-Texte; Ressourcenvergleich vor Auswertung korrigieren |

### Technische Restpunkte

1. **Piper-Textübergabe (#14/#16):** beide senden `--` als unbekanntes CLI-Argument. Upstream Piper 1.8.0 fügt unbekannte Argumente zum Text zusammen. Lokaler argparse-Nachweis: `['--', 'Hallo Ivan']` → `-- Hallo Ivan`. Unterstützte stdin-Übergabe verwenden und echten Textpfad testen. [Upstream](https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/src/piper/__main__.py).
2. **Report-Abbruch (#16):** Signalhandler vor Report/try; ein Signal während initialer Snapshots umgeht Speichern des Teilberichts. Initialisierung in geschützten Block ziehen, frühzeitige Unterbrechung prüfen.
3. **Dienstkonto (#4):** obivan teilt UID mit Login; dokumentierte systemd-Härtung ist kein dedizierter Benutzer. Migration braucht gemeinsame Anpassung von Unit, Installern, Secretgruppe und TTS-Rechten.
4. **Runtime-Pfad (#4):** Doku/Kommentar korrigiert; Code akzeptiert weiter andere Pfade für manuelle Tests. Wer eine andere Unit benötigt, muss RuntimeDirectory/Schreibrechte passend ändern.
5. **Installer (#13):** smbus/i2c-Gruppe sind auch bei deaktiviertem SHIM Voraussetzung; i2cdetect-Check fehlt. Wiederholte Installationen ohne Preflight/Fehler-Rücknahme können einen Dienst gestoppt lassen. Für künftige Codearbeit gezielt prüfen.
6. **Historisches chown (#14/Chat):** neuer Feature-Installer vergibt nur root für Basis/src/scripts und obivan für venv/tts. Früheres rekursives chown auf dem echten Pi kann weitere Dateien betreffen; der aktuelle gesamte Rechtebestand ist nicht ausgelesen belegt. Keinen weiteren pauschalen chown empfehlen.

Wiederkehrendes Muster: Anleitungen hinken neuen Modulen hinterher; alte Status-/Abnahme-Sätze bleiben in mehreren Dateien stehen; Feature-Pi und main werden gleichgesetzt. Deshalb jetzt ein Setup, eigener Betrieb/Troubleshooting und Fachseiten mit begrenzten Abnahmen. Keine Codeänderung an Audio/STT/TTS in diesem Doku-PR. Bei späterer Übernahme von PR #14/#16 deren überlappende README-/Architektur-/Setup-Änderungen in die neue Struktur einordnen; nicht wieder alte Statusblöcke anhängen.

## Verwendeter Gesprächskontext

Verfügbare Projekt-Chats: **Hardware Bestandsaufnahme**, **Betriebssystembasis festlegen**, **Repo aufsetzen und dokumentieren**, **Projektstart planen**, **Projekte vergleichen**, **Push To Talk Bauen**, **Nächster Sprachloop Schritt**, **Vosk Offline STT testen**, **Hardware erweitert Pi Assistenten**, **Lokale Sprachausgabe einrichten**, **Nächste Schritte messen**, **Prüfe BSS Richtigkeit**, **Doku prüfen und bereinigen**; ergänzend Codex **Implementiere WM8960 Push-to-Talk** und **Pi-Sprachassistent: PTT prüfen und Sprachpipeline…**.

Frühere KI-Vorschläge wurden gegen Dateien/PRs und Nutzer-Rückmeldungen geprüft, nicht als bestätigte Hardwarefunktion übernommen. Abbildungen wurden in dieser Prüfung nicht neu als Sensornachweis interpretiert; der Repo-Fotobestand bleibt erhalten. Keine neue Pi-Messung, keine CI-Erfolgsaussage allein aus einem alten Chat. Lokale Tests ersetzen die [noch offenen Hardwaretests](roadmap.md) nicht.

## Validierung der Bereinigung

47 vorhandene unittest-Tests mit Python 3.13 bestanden; lokale Doku-/Bildlinks und Markdown-Anker sowie Bash-Syntax aller 36 Beispielblöcke geprüft. Servicecode, Installer, Tests und Fotos sind unverändert; nur Dokumentation und zwei erklärende Runtime-Pfad-Kommentarzeilen geändert. Kein neuer Pi-/Piper-Hardwarebenchmark.
