# Architektur 🧠

## Verantwortlichkeiten

| Komponente | Aufgabe |
|---|---|
| Pi-Client | Taste lesen, Aufnahme begrenzen, Audio übertragen, Antwort abspielen, Fehler anzeigen |
| Homelab-Dienst | STT, Anfrage an Sprachmodell, TTS und Rückgabe der Audiodatei |
| PiSugar2-Integration | Später Akkustatus und kontrolliertes Herunterfahren |
| Kamera/Display | Spätere optionale Erweiterung |

## MVP-Ablauf

1. Client wartet auf Tastendruck.
2. Gedrückt halten startet die Aufnahme; Loslassen stoppt sie. Zusätzlich gilt ein konfigurierbares Zeitlimit (PTT-Standard 30 Sekunden).
3. Client sendet die Aufnahme an den Homelab-Dienst.
4. Dienst transkribiert, erzeugt Antwort und synthetisiert Sprache.
5. Client spielt die Antwort ab und kehrt in den Wartezustand zurück.

Während Verarbeitung und Wiedergabe startet keine neue Aufnahme. Wake Word, Unterbrechen der Sprachausgabe und Echounterdrückung gehören nicht zum ersten MVP.

## Geplanter Vertrag

HTTP-Upload einer begrenzten Audiodatei, Rückgabe einer begrenzten Audiodatei. Route, Authentifizierung, Fehlerformat, Codec, Samplingrate und maximale Größen werden vor Implementierung festgelegt. Noch keine existierende API.

ALSA-Geräte werden anhand der erkannten Karten ausgewählt, nicht anhand einer fest angenommenen Kartennummer. Audioformat erst nach einem Aufnahme- und Wiedergabetest festlegen.

## Betrieb und Fehler

- Verbindungs- und Antwort-Timeouts; begrenzte Wiederholungen und verständliche Fehlerrückmeldung.
- Nach Fehlern Rückkehr in den Wartezustand, keine Endlosschleife.
- Zustände: bereit, Aufnahme, Verarbeitung, Wiedergabe, Fehler.
- Client später als systemd-Dienst mit dediziertem Benutzer und erforderlichen Audio/GPIO-Rechten.
- Zugangsdaten außerhalb von Git; Homelab-Endpunkt zunächst nur intern erreichbar.
- Audio nur für die Anfrage verarbeiten; keine dauerhafte Speicherung als Standard.

## Erfolgskriterien

Ein Tastendruck startet zuverlässig eine Aufnahme. Ein deutscher Testsatz wird transkribiert, beantwortet und verständlich abgespielt. Nach Netzwerkausfall oder Dienstfehler lässt sich eine neue Anfrage starten. Start nach Neustart funktioniert ohne manuellen Eingriff. Latenz und Speicherverbrauch werden gemessen; Zielwerte folgen nach dem ersten Durchlauf.

## Implementierter erster Baustein

Der lokale [Push-to-Talk-Dienst](push-to-talk.md) liest GPIO17 mit libgpiod v2 und erzeugt geprüftes Stereo-WAV (48 kHz, S16_LE) im flüchtigen Verzeichnis `/run/pi-ptt`. `capture_ready` bezeichnet den Übergabepunkt für einen späteren STT-Adapter. Das Halten/Loslassen ersetzt den früheren Toggle-Entwurf. Hardware-Abnahme steht aus.

OpenRouter ist laut Nutzer das Ziel für die spätere KI-API, deutsche TTS das Ziel für die Sprachausgabe. STT-Auswahl und Verarbeitung im Homelab bleiben offen. Dieser Schritt führt ausschließlich lokale Audioaufnahme aus. Vor Anschluss der Sprachpipeline werden Verarbeitung/Wiedergabe verriegelt, Timeouts festgelegt und Audioformate für STT/TTS angepasst; OpenRouter erhält später transkribierten Text. Noch keine Netzwerk- oder TTS-Implementierung.

