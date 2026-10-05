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
2. Tastendruck startet Aufnahme; erneuter Tastendruck stoppt sie. Zusätzlich gilt ein konfigurierbares Zeitlimit.
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
