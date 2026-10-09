# Nächste Aufgaben

Stand 08.10.2026. Der Funktionsstand steht in der [README](../README.md); hier steht nur verbleibende Arbeit.

## Als Nächstes

- **Spracherkennung mit echter Stimme bewerten:** etwa 10 Sätze über das WM8960-Mikrofon aufnehmen, Vosk small gegen Whisper small auf CT 107 vergleichen (Branch `feat/servitor-whisper-stt`; mit synthetischer Sprache war Vosk small genauso gut und 50-mal schneller nach dem Loslassen). Aufnahmen danach löschen.
- **Aktivierungswort mit echter Stimme abnehmen** (Trefferquote, Fehlauslösungen über einen Tag, Pausenerkennung), danach eigenes „Hey Servitor“ trainieren (openWakeWord-Trainingspipeline mit Piper-Stimmen, auf CT 107).
- **Hardware-Abnahme der Bedienung:** PiTFT-Menü, „Display aus“, Status-LED-Farben, C/D mit Wiederholung, E im und außerhalb des Menüs.
- **Weitere Funktionen ohne LLM:** z. B. Lautstärke per Sprache, Timer/Wecker, „Wiederhole“. Aktionen auf dem Pi brauchen dafür eine Rückmeldung vom Server an den Pi.
- **Charakter verfeinern** anhand echter Gespräche ([`server/sample-persona.py`](../server/sample-persona.py)).

## Ideen

Gesammelt am 09.10.2026. Erledigtes bleibt zur Übersicht stehen.

1. **Nachrichten für bestimmte Personen** („Proximus, sag Anna, dass das Essen im Kühlschrank steht“): im Gedächtnis als Postfach je Person ablegen; erkennt der Server die Person an der Stimme, kommt zuerst „Eingehende Vox-Übertragung von …“. Baut auf Stimmprofilen, Gedächtnis und der Regel „Fremden keine persönlichen Daten“ auf; braucht ein neues Kommando und einen Postfach-Eintrag im Gedächtnis.
2. ~~**Morgenlitanei**~~: erledigt 09.10.2026 („Morgenbericht“, „Guten Morgen“; Antwort ohne Gruß im Servitor-Ton; Datum, Uhrzeit, Wetter über Open-Meteo, Akku/Server/Updates nur bei Bedarf), dazu Wetterfragen. [Architektur](architecture.md#morgenlitanei-und-wetter). Termine aus Nextcloud per CalDAV seit 09.10.2026, abgefragt vom Pi (gegen Radicale getestet, Abnahme mit der echten Nextcloud offen). Offen: wahlweise automatisch beim ersten Tastendruck des Tages.
3. **Homelab-Wächter:** Proxmox-API abfragen („Wie geht es dem Maschinengeist von CT 107?“: CPU, RAM, Backups, ausgefallene Container) und selbst melden („Warnung: Backup-Ritus fehlgeschlagen.“) über die vorhandenen Alarme. Braucht einen API-Token nur mit Leserechten auf dem Server.
4. **Durchsagen vom Server:** Endpoint `POST /v1/announce` auf CT 107, über den Home Assistant, Uptime Kuma oder Skripte Proximus etwas sagen lassen. Der Rückkanal Server → Pi ist dieselbe Voraussetzung wie für Timer/Wecker.
5. **Smart-Home-Steuerung** über Home Assistant („Aktiviere die Leuchtglobe im Wohnzimmer“), falls vorhanden.
6. ~~**Atmosphäre**~~: erledigt. Servo-Schädel mit leuchtendem Auge und Litanei-Regen auf dem Display gab es schon ([Display](display.md)); seit 09.10.2026 dazu der Quittungston beim Loslassen ([Architektur](architecture.md#sprachausgabe)). Offen: Ton am Gerät abhören, ggf. Lautstärke/Länge anpassen.
7. **Kamera** (siehe Hardware): „Was siehst du?“ schickt ein Foto an ein Vision-Modell über OpenRouter, Antwort als Sensorbericht.

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
