# Benutzerhandbuch

[Installation](setup.md) · [Betrieb und Fehlersuche](operation.md) · [Roadmap](roadmap.md)

## Tasten, Push-to-Talk und Menü

Der WM8960-Taster (**GPIO17**) und Button SHIM **A** aktivieren Push-to-Talk: **halten = aufnehmen, loslassen = verarbeiten**. Beide Tasten dürfen gleichzeitig gehalten werden; die Aufnahme endet erst, wenn beide losgelassen werden.

| Taste | Funktion |
|---|---|
| GPIO17 / SHIM A | Sprechen; Loslassen quittiert die Anfrage per Ton |
| SHIM B | Abbrechen bzw. eigene Sprachausgabe stoppen; im Menü zurück |
| SHIM C / D | Digitalen WM8960-`Playback`-Pegel um **2 dB** senken/erhöhen, gehalten wiederholen |
| SHIM E | Statusansage; Menü-Bestätigung; bestätigten Neustart/Shutdown auslösen |
| PiTFT oben/unten (GPIO23/24) | Menü öffnen, nach oben/unten navigieren; erste Taste weckt das Display |

Es gibt einen Aufnahme- und Verarbeitungsslot. B verwirft die laufende Anfrage; ein nativer Vosk-Aufruf kann noch bis zu seinem Ende rechnen. Die Aufnahme ist flüchtig unter `/run/pi-ptt`. Standardlimit: 30 s. C/D regeln **nicht** den analogen `Speaker`-Pegel.

## Menü auf dem PiTFT

PiTFT-Tasten wählen; E bestätigt, B geht zurück. Nach 15 s ohne Bedienung schließt es automatisch.

- **Sprache:** Aktivierungswort, Sprachkern AUTO/FREI/LOKAL, Server AN/AUS, Quittungston.
- **Persönlichkeit:** SERVITOR/BILLY, Stimmeffekt, Lore, Gefühle (Einstellungen bleiben nach Neustart gespeichert).
- **Personen:** Kennenlernen, Stimmprofil ansehen/nachtrainieren/löschen.
- **Audio:** Bluetooth-Lautsprecher verbinden/suchen/vergessen.
- **Gerät:** WLAN, Alarme, Status-LED, Display aus.
- **System:** Systeminfo, Status, [Wartung](operation.md#wartungsmodus).

Der Modus **LOKAL** nutzt ein Offline-LLM nur, wenn **CT 107** erreichbar ist; auf dem Pi selbst läuft kein Offline-LLM. [Persönlichkeit](#persönlichkeit-servitor-und-billy) · [Architektur](architecture.md).

## Status-LED

| Farbe | Bedeutung |
|---|---|
| Grün / Gelb gedimmt | Bereit; Serverpfad / lokaler Pfad |
| Rot | Aufnahme; blinkend bei Fehler |
| Cyan / Gelb pulsierend | Verarbeitung auf CT 107 / Pi |
| Orange | Ausgabe |
| Violett | Menü |
| Aus | Schlaf bzw. LED deaktiviert |

Die LED wird getrennt von der Tastenabfrage aktualisiert, damit I²C-Schreibzugriffe keine Tastendrücke blockieren.


## PiTFT: Status und Animationen

Das **Adafruit mini PiTFT 1,3″**, 240 × 240 / ST7789, läuft über SPI unabhängig vom Sprachdienst. Die gezeigten Bilder sind **softwareseitig gerenderte Vorschauen**, keine Fotos des Pi.

| Status | Arbeitsschritte |
|:--:|:--:|
| ![Statusvorschau](images/display-status-preview.png) | ![Arbeitsschritte](images/display-steps-preview.png) |

Auf der Anzeige erscheinen Zuhören, Erkennen, Denken, Synthese, Rendern, Ausgabe, Server/LOKAL, WLAN, Akku, Temperatur, Uhrzeit, Lautstärke, Menü und Fehlermeldungen. Servitor-Schädel/Billy-Gesicht und die Wettervorhersage sind integriert.

![Animierte Wetteranzeige](images/display-weather.gif)

## Energiesparstufen

Nach `PTT_REST_SECONDS` (Standard 30 s) dunkelt das Display ab; nach `PTT_SLEEP_SECONDS` (600 s) geht die Hintergrundbeleuchtung aus. Ein Tastendruck weckt es, auch im Schlaf kann das Aktivierungswort lauschen. Mit `PTT_SLEEP_WLAN=off` ist SSH in diesem Zustand nicht erreichbar.

**Offen:** abschließende Hardware-Abnahme der Tasten, LED, Wetteranimation und Lesbarkeit ([Roadmap](roadmap.md)).


## Persönlichkeit: Servitor und Billy

**SERVITOR** antwortet als mechanische Diensteinheit Proximus; **BILLY** als menschliches Engramm. Beide haben wählbare Lore-Stufen **AUS / DEZENT / VOLL** sowie einen natürlichen oder maschinellen Stimmeffekt. Fakten haben Vorrang vor Rollenprosa.

Im Menü **Persönlichkeit** werden Sprechstil, Stimmeffekt, Lore und Gefühle eingestellt. Die Wahl übersteht einen Neustart unter `/var/lib/pi-ptt/settings.json`.


## Gedächtnis und Personen


Proximus speichert sein Gedächtnis nur auf einem USB-Stick mit ext4 und dem Label `PROXIMUS` ([`src/memory.py`](../src/memory.py)). Ohne Stick hat er kein Gedächtnis; steckt der Stick wieder, sind alle Erinnerungen zurück. Ab- und Anstecken sagt er an („Gedächtniskern entfernt …“ / „Gedächtniskern verbunden. N Einträge geladen.“); im Display zeigt eine Raute neben PROXIMUS den Zustand (gefüllt: verbunden, grau umrandet: fehlt), die Info-Seite eine Zeile „Gedächtnis“.

| Inhalt | Beispiel | Grenze |
|---|---|---|
| Fakten | „Merk dir, dass ich Ivan heiße.“ oder vom Modell gelernt | 200 Einträge |
| Direktiven | „Nenne Städte in Zukunft nur noch Makropolen.“, „Installiere die Humor-Erweiterung.“, „Ab jetzt antworte kürzer.“ | 20 |
| Verlauf | die letzten 6 Fragen und Antworten (4 gehen an das Modell) | 6 |

Befehle ohne Sprachmodell (auch offline): „Merk dir …“, „Installiere/Aktiviere die X-Erweiterung“, „Deinstalliere/Deaktiviere die X-Erweiterung“, „Vergiss …“, „Was weißt du über mich?“, „Welche Direktiven/Erweiterungen hast du?“.

## Kennenlernen und Stimmerkennung

Menü „Kennenlernen“ oder „Lerne mich kennen“ startet eine Sitzung ([`src/enroll.py`](../src/enroll.py)); B bricht jederzeit ab. Voraussetzung: Stick steckt, Server erreichbar.

1. **Stimmproben:** nach jedem Signalton „Proximus“ sagen, 20-mal à 2 s (normal, leiser, aus ~2 m, wie im Alltag). Die Clips landen in `proximus/voice/wake/` und sind Trainingsdaten für das Aktivierungswort (`train_wakeword.py real --real <ordner>`, dann `train`: die Hälfte der Positivbeispiele kommt aus diesen Aufnahmen, ausgewählt wird nach der Trefferquote auf zurückgehaltenen Clips).
2. **Fragen:** Name, Wohnort, Tätigkeit, Interessen, wichtige Menschen, gewünschter Antwortstil, Sonstiges; Antwort jeweils 6 s nach dem Signalton. Der Server transkribiert nur (`POST /v1/turn?mode=transcribe`), der Pi speichert Fakten („Name des Bedieners: Ivan“) bzw. den Stil als Direktive. Clips in `proximus/voice/answers/`.
3. **Stimmprofil:** Der Server bildet aus jeder Antwort einen Stimmabdruck (`POST /v1/voiceprint`, sherpa-onnx mit CAM++/VoxCeleb, 512 Werte); ihr Mittelwert wird als `proximus/voice/voiceprint.json` gespeichert (int8, ~700 Zeichen) und mit jeder Anfrage im Gedächtnis-Header mitgeschickt.

**Mehrere Personen und Nachtrainieren:** Jede Person hat ein eigenes Profil (`proximus/voice/profiles/<name>.json`); eine weitere Kennenlern-Sitzung legt die nächste Person an. Menü „Stimme nachtrainieren“ oder „Stimmprofil nachtrainieren“ nimmt nur 20 „Proximus“-Proben auf (auch Trainingsdaten fürs Aktivierungswort), bildet je fünf Proben einen Abdruck und verrechnet ihren Mittelwert gewichtet mit dem Profil, dem die Stimme am ähnlichsten ist (Kosinus ≥ 0,4); passt keines, bleibt alles unverändert. „Wen kennst du?“ oder Menü „Bekannte Personen“ zählt die bekannten Stimmen auf (nur für erkannte Stimmen).

**Personen verwalten:** Menü „Personen“ listet die bekannten Stimmen; E auf eine Person öffnet „Nachtrainieren“, „Details anzeigen“, „Passphrase festlegen“, „Löschen“ ([`src/people.py`](../src/people.py)). Jede Aktion verlangt zuerst eine Authentifizierung: nach dem Signalton die Passphrase sprechen (6 s); die Stille davor und danach wird abgeschnitten, dann liefert der Server aus derselben Aufnahme Stimmabdruck und Text. Nötig sind Stimme passend zum ausgewählten Profil (≥ 0,5) und Passphrase (unscharfer Vergleich ≥ 0,75, weil Vosk sie nicht immer gleich schreibt; Leerzeichen und Füllwörter davor oder danach zählen nicht). Klappt es nicht, gibt es einen zweiten Versuch. Jeder Versuch landet als `auth`-Ereignis im Journal von `pi-ptt` (Stimmwert, Passphrasen-Wert, Länge, Ergebnis; nie die Passphrase selbst): `journalctl -u pi-ptt | grep '"event": "auth"'`. Passphrase richtig, Stimme nur knapp (0,3–0,5): durchgelassen mit „Nachtrainieren empfohlen“, und Nachtrainieren steht im Menü als „Nachtrainieren!“. Ohne gesetzte Passphrase genügt einmal die Stimme, dann wird eine festgelegt (zweimal sprechen). Löschen braucht zusätzlich E. Die Passphrase steht im Klartext im Profil auf dem Stick (für den unscharfen Vergleich); die Aufnahme dazu wird sofort gelöscht.

**Bei jeder Anfrage** vergleicht der Server die Stimme mit dem Profil (Kosinus ≥ 0,5, `SERVITOR_SPEAKER_THRESHOLD`; ~40 ms). Erkannt: das Modell erfährt den Namen und spricht persönlich. Fremd: es bekommt nur die Direktiven, keine Fakten und keinen Verlauf, Gedächtnisbefehle werden abgelehnt („Stimme nicht als Bediener erkannt …“), gelernte Zeilen verworfen, und eine neue Kennenlern-Sitzung startet nicht. Ohne Profil ändert sich nichts; Äußerungen unter 1 s gelten mangels sicherer Entscheidung als Bediener. Auf dem Pi (lokaler Rückfall) gibt es keine Stimmerkennung.

## Wetter, Termine und Netzwerk

„Was steht heute an?“ ruft Nextcloud-CalDAV-Termine ab; „Wie ist das Wetter?“ liefert eine Fünf-Tage-Vorhersage. Offline-Wetter benötigt den Cache `weather.json` auf dem Gedächtnis-Stick. „Wie ist das Netzwerk?“ nennt WLAN, Verbindung und DNS. Zugangsdaten und Alarmgrenzen stehen im [Betriebsleitfaden](operation.md).

## Geräteaktionen und Wartung

WLAN an/aus, Ruhemodus, Neustart und Herunterfahren werden auf dem Pi ausgeführt. Neustart/Shutdown verlangen eine Bestätigung; bei unbekannter Stimme nur Taste E. Schlaf mit `PTT_SLEEP_WLAN=off` unterbricht SSH. Der Wartungsmodus bietet Pi-Updates und Pi-/Server-Neustarts, verlangt immer E zur Bestätigung (B bricht ab, 20 s Frist) und endet nach zehn Minuten ohne Eingabe. CT 107 und Proxmox werden manuell aktualisiert. Details: [Wartungsmodus](operation.md#wartungsmodus).
