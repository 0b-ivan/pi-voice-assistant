# Web-Client

Übergabe für die Weiterarbeit: [GPT_UEBERGABE](concepts/proximus-web-client/GPT_UEBERGABE.md).

Proximus im Browser, aus derselben Codebasis wie der Pi. Der Server auf CT 107
liefert die Seite selbst aus (`web/`) und beantwortet sie mit genau derselben
Pipeline wie den Pi: Vosk → Regelwerk/Intents → OpenRouter bzw. lokales Qwen →
Piper Thorsten + Servitor-DSP. Es gibt keinen zweiten Server und keinen
Build-Schritt; die Seite besteht aus wenigen statischen Dateien.

```text
Browser: Taste halten → Mikrofon → 16 kHz PCM → POST /v1/turn  ┐
         Frage tippen ───────────────────────→ POST /v1/ask   ├→ CT 107 (servitor_server.py)
         lange Geschichte, nächster Abschnitt → POST /v1/story ┘   NDJSON: transcript, reply, audio …
```

## Aufrufen

| Weg | Adresse | Mikrofon |
|---|---|---|
| Cloudflare-Tunnel (HTTPS) | **https://proximus.obivan.org/** | ja |
| Auf CT 107 selbst | `http://localhost:8765/` | ja |
| LAN | `http://172.22.9.107:8765/` | nein, nur Texteingabe |

Browser geben das Mikrofon nur in einem sicheren Kontext frei (HTTPS oder
localhost). Über die LAN-Adresse funktioniert deshalb nur das Tippen; für
Sprache den Tunnel nehmen. Die Seite liegt unter `/` und zusätzlich unter
`/web/`; ihre Dateien lädt sie immer aus `/web/`.

`proximus.obivan.org` zeigt über cloudflared auf CT 100 bereits auf
`172.22.9.107:8765` (derselbe Weg wie der Fallback des Pi), am Tunnel ist
nichts einzustellen.

### Anmelden mit Kennwort

Im Anruf-Terminal steht ein Kennwortfeld. Nach dem Login setzt der Server ein
Sitzungs-Cookie; mit „Angemeldet bleiben“ hält es 30 Tage, sonst 12 Stunden.
⚙ → „Abmelden“ beendet die Sitzung. Der Pi bleibt beim Token.

Einrichten auf CT 107 (das Kennwort selbst steht nirgends, nur sein
scrypt-Hash):

```sh
cd /opt/servitor-voice/repo
/opt/servitor-voice/.venv/bin/python server/servitor_server.py --hash-password
# Ausgabe SERVITOR_WEB_PASSWORD_HASH=scrypt$16384$8$… an /etc/servitor-voice.env anhängen
systemctl restart servitor-voice
```

Ohne `SERVITOR_WEB_PASSWORD_HASH` meldet das Terminal „Kennwort nicht
eingerichtet“. In dasselbe Feld kann man auch weiterhin den Server-Token
eingeben (oder ihn unter ⚙ hinterlegen); er wird erkannt, wenn das Kennwort
nicht passt.

Sitzungen liegen nur im Arbeitsspeicher: Ein Neustart des Dienstes meldet alle
Browser ab.
Auf dem Handy lässt sich die Seite zum Startbildschirm hinzufügen (Manifest).

## Darstellung: das Vox-Terminal

Die Seite sieht aus wie ein Vox-Gerät des Imperiums: eisernes Gehäuse,
Phosphor-Röhre, Lampen und Zeigerinstrument. Wer sie öffnet, **ruft Proximus
über Vox an**:

1. **Anruf-Bildschirm:** Kennwort eingeben oder eine vorhandene Sitzung nutzen.
   Optional kann stattdessen ein Server-Token verwendet werden. „Verbindung herstellen“ startet die Anwahl.
2. **Wählsequenz:** Die Litanei begleitet die Verbindungssuche mit Rauschen
   und Pfeifton. Die angezeigte Antwortzeit stammt von `/health`; sie ist
   keine gemessene Funkfeldstärke. Die Anmeldung läuft über `/v1/login` oder
   prüft einen Server-Token über `/v1/status`. Bei Fehlern erscheint eine
   konkrete Meldung statt eines erfundenen Verbindungszustands.
3. Die Röhre schaltet sich ein. Ein leises Rauschen zeigt den offenen Kanal an.
   ⏻ trennt den Kanal und führt zurück zum Anruf.

Im Betrieb:

| Element | Bedeutung |
|---|---|
| Oszilloskop oben in der Röhre | eigene Stimme beim Senden, Proximus beim Antworten, sonst Trägerrauschen; unruhig, während der Server arbeitet |
| Zeigerinstrument „SIGNAL“ | Sprachpegel beim Senden/Empfangen, sonst Signalstärke aus der gemessenen Laufzeit zu `/health` |
| grüne Lampe | Kanal verbunden, Server bereit |
| rote Lampe | leuchtet beim Senden, blinkt bernsteinfarben, während der Server arbeitet |
| Schädel | grüner Geist auf der Röhre, glimmt je nach Schritt |
| Protokoll | `BEDIENER »` und `PROXIMUS »` wie ein Fernschreiber; Proximus' Zeilen tippen sich ein |

Geräusche (alle im Browser erzeugt, nichts wird geladen): Klicken der
Sendetaste, kurzer digitaler Quittungston beim Absenden,
Trägerstoß vor jeder Antwort, Rauschschwanz am Ende. Proximus' Stimme läuft
zusätzlich durch einen milden Funkfilter (Hochpass 180 Hz, Tiefpass 5200 Hz,
Präsenzanhebung und leichte Sättigung):
Er klingt weit weg. Gelegentlich reißt das Bild kurz ab und knackt es.

„Vox-Effekte“ in den Einstellungen schaltet Rauschen, Funkfilter, Störungen
und Schreibmaschineneffekt ab; die Antworten bleiben gleich. Bei „Bewegung
reduzieren“ im Betriebssystem entfallen alle Animationen.

- **Mobil (unter 900 px):** Der Status steht in der Röhre unter dem
  Oszilloskop. Die Texteingabe nutzt die Breite; Sprachaufnahme, Senden
  und Abbrechen teilen sich eine kompakte Bedienzeile.
- **Desktop (ab 900 px):** Status in der Kopfzeile, großer Schädel,
  separate Bedienleiste und Bedienteil rechts. Nachrichten erscheinen
  als Dialog mit Bediener- und Proximus-Zeilen.

### Synchroner Fernschreiber und Bedienteil

- **Proximus schreibt, während er spricht:** Eine Antwort erscheint zuerst nur
  als blinkender Cursor. Jeder Audioteil tippt seinen Text in genau der Zeit,
  die die Stimme dafür braucht, beginnend mit dem Ton. Abbrechen zeigt den
  Rest sofort. Ohne Ton (Fehler) erscheint der Text allein.
- **Kein Modellname** mehr unter den Antworten.
- **Gehäuse in Feldfunk-Oliv** (angelehnt an Bundeswehr-Funkgeräte): weiße
  Schablonenschrift, gelbe Beschriftungsstreifen, Nummernschilder.
- **Desktop-Bedienteil** rechts neben dem Monitor: großes Zeigerinstrument,
  Wahlschalter (Bakelit-Drehknopf mit gelbem Streifen) für Persona, Stimme,
  Lore und Sprachkern, Kippschalter für Störungen und Sendeart, Lautstärke mit
  Pfeiltasten und Lampenzeile. Knopf anklicken schaltet weiter (auch mit
  Pfeiltasten), ein Feld im Streifen wählt direkt. Dieselben Werte wie im
  Einstellungsdialog, der auf dem Handy bleibt.
- **Anruf-Gerät auf dem Desktop** breit: großer Monitor links, rechts
  Nummernschild, Lautstärke, Kennwort und Verbinden.
- Fernschreiberschrift: Share Tech Mono (SIL OFL, `web/fonts/`).

### Live-Journal und Fremdkanal

Nur die lokalen Proximus-Dienste `servitor-voice.service` und
`servitor-llm.service` liefern echte Journal-Einträge
(`GET /v1/journal?after=N`, Token oder Sitzung, alle 2,5 s). Der Dienst
liest es mit `journalctl -f -u servitor-voice.service -u servitor-llm.service`
(Gruppe `systemd-journal`)
und hält die letzten 300 Zeilen im Arbeitsspeicher. Zugriffszeilen des
Webservers fehlen; IPv4- und IPv6-Adressen werden maskiert. Ohne `journalctl`
(oder mit `SERVITOR_JOURNAL_SOURCE=own`) zeigt es nur die Ausgabe des Dienstes.
Fehler der Journal-Quelle, eine unterbrochene Verbindung oder eine abgelaufene
Anmeldung erscheinen als feste Warnzeile. Nach einem Server-Neustart erkennt
der Browser den neuen Journal-Puffer auch dann, wenn dessen Zähler schon
weitergelaufen ist als vorher.

- **Desktop:** eigene Spalte rechts auf dem Röhrenbildschirm, der Chat links.
  **Handy:** hinter dem Chat; die Chat-Zeilen bleiben deckend.
- Neueste Zeile oben, ältere blenden nach unten aus. Zeit, Dienst, Meldung
  (`19:41:36 servitor-voice READY load_seconds=9.84`);
  Warnungen bernsteinfarben, Fehler rot.
- Neue Meldungen laufen einzeln im Abstand von 160 ms ein; vorhandene Zeilen
  werden während 240 ms weich weitergeschoben. Ohne neue Meldungen ruht die
  Anzeige. „Bewegung reduzieren“ zeigt neue Zeilen ohne Animation. Beim
  Auflegen wird die noch wartende Anzeige geleert.
- Die Anzeige hält 60 echte Meldungen und höchstens drei Effektzeilen.
  Litaneien und Störungen kommen weiterhin alle 4–12 s; sie verdrängen keine
  echten Meldungen mehr, wenn das System ruhig ist.
- Mit Vox-Effekten: Litaneien und Noosphären-Störungen dazwischen, und etwa
  alle 25–70 s rutscht ein **Fremdkanal** herein: im Browser erzeugtes
  Sprechfunk-Gemurmel (wandernde Tonhöhe, Silben mit springenden Formanten,
  schmalbandig verzerrt, mit Rauschen und Squelch), sprachähnlich, aber ohne
  verständliche Wörter. Nie während man sendet oder Proximus spricht. Im
  Journal steht dann „FREMDKANAL · unverständlich“.

### Textstil für das Vox-Terminal

Die Oberfläche verbindet imperiale Funk-Atmosphäre mit überprüfbaren Zuständen:

- **Zustände:** Kurz und eindeutig („Server erreichbar“, „Anmeldung abgelaufen“,
  „Verbindung getrennt“). Nur tatsächliche Messwerte anzeigen. Die Antwortzeit
  von `/health` ist kein RF-Pegel.
- **Fehler:** Zuerst die Ursache, dann eine praktikable Handlung
  („Kennwort oder Token ungültig“, „Verbindung prüfen“). Keine vagen
  Meldungen wie „Klärung erforderlich“.
- **Litaneien:** Dürfen poetisch sein, müssen aber von echten Journal-Einträgen
  unterscheidbar bleiben. Kein erfundenes Prüfergebnis und keine Behauptung,
  Wartung oder Datenübertragung sei tatsächlich erfolgt.
- **Funkstörungen/Fremdkanäle:** Sind ausdrücklich simulierte Effekte im Browser,
  keine protokollierten Netzfehler. Alle 4–12 Sekunden höchstens eine
  Litanei oder Störung, ohne echte Einträge zu verdrängen.
- **Sprache:** Deutsch und knapp; etablierte Begriffe wie Vox, Omnissiah oder
  Maschinengeist bleiben. Kein Modellname unter Antworten.

### Lore-Prüfung der Formulierungen

Die `Litanei der Zündung` ist an die überlieferte `Litany of Ignition` angelehnt.
Andere atmosphärische Funksprüche sind **eigene Projekttexte** und nicht
automatisch offizielle Mechanicus-Gebete. `Dignus est intrare` und
`Mens sana in machina sana` entfallen als pseudo-kanonische Losungen.

Billy, Phobos IX, Milwaukee Secundus und Magos Kael sind projektinterne
Geschichte, kein offizieller Warhammer-Kanon. Der Prompt trennt das nur,
wenn ausdrücklich nach kanonischer Lore gefragt wird; ansonsten bleibt
die Figur in ihrer Rolle.

Die Antworten „Status nominal“ und „Alle Riten erfüllt“ dürfen bei fehlenden
Pi-Messwerten nicht als Befund erscheinen. `system status?` wird als
Status-Intent erkannt. Die Antwort nennt ausdrücklich fehlende Telemetrie.

**Testgrenze:** Der Testlauf prüft feste Antworten, Lore-Kontext, Persona-
Grenzen, Browserabläufe und Fehlerfälle; er misst **keine tatsächlichen
generierten Modellantworten**. Hierfür sind auf CT 107 authentifizierte
Modell-Stichproben mit `server/sample-persona.py dialog` nötig.

### Journal-Sicherheit und Abschaltung der Vollweiterleitung

Die bisherige Weiterleitung mit `systemd-journal-upload` war zu breit:
Sie übertrug sämtliche Host-/Pi-Systemmeldungen, darunter SSH, Kernel,
andere Dienste und Proxmox-Verwaltung. **Diese Vollweiterleitung muss auf den betroffenen Rechnern abgeschaltet
werden und darf nicht erneut installiert werden.**

Der Fix-Branch beschränkt die Browseranzeige auf `servitor-voice.service` und
`servitor-llm.service` aus dem lokalen Journal (`journalctl -u`).
Zusätzlich verwirft eine Whitelist fremde Units und fremde Hostnamen.
Der frühere Installer `deploy/journal-forward/install.sh` ist gesperrt.

**Einmalig bestehende Installation abschalten:**

```sh
# Auf dem Proxmox-Host wgz-pve-01 als root:
systemctl disable --now systemd-journal-upload.service

# In CT 107 als root (nur die beiden Proximus-Remote-Empfänger):
systemctl disable --now 'systemd-journal-remote@172.22.2.107.socket' \
  'systemd-journal-remote@172.22.9.107.socket'
systemctl stop 'systemd-journal-remote@172.22.2.107.service' \
  'systemd-journal-remote@172.22.9.107.service'
```

Für sofortige Einschränkung **vor** der Code-Aktualisierung in
`/etc/servitor-voice.env` den Wert `SERVITOR_JOURNAL_SOURCE=own`
setzen und `systemctl restart servitor-voice` ausführen. Dies zeigt
nur noch die Proximus-Serverausgabe, leert den RAM-Puffer und beendet
bestehende Browser-Sitzungen.

Bereits empfangene Dateien unter `/var/log/journal/remote/` werden
durch die Abschaltung **nicht automatisch gelöscht**. Nach dem Stoppen
der Empfänger gesondert prüfen und bereinigen; lokale Journale erhalten.

**Pi-Weiterleitung bleibt aus**, bis es einen ausschließlich auf
Proximus-Dienste eingeschränkten, authentifizierten Transport gibt.

### Arbeitsprotokoll von Proximus

Das Journal zeigt beobachtbare Verarbeitungsschritte mit einer Anfragekennung:
Spracherkennung, Stimmprüfung, erkannte feste Regel, Wetterdatenabruf
(Dienst oder Cache), LLM-Dienst/Modell, lokalen Rückfall, Sprachsynthese,
Funkfilter und Audio-Kodierung. Beginn, Abschluss, Fehlerklasse und tatsächliche
Laufzeiten machen sichtbar, wo eine Antwort Zeit braucht.

Bei Lore und Geschichten werden nur die tatsächlich in den Modellkontext
übernommenen Archiv-IDs angezeigt. Das belegt die Kontextauswahl, nicht die
Nutzung jedes Eintrags durch das Modell. Tokenzahlen erscheinen nur, wenn
der Anbieter sie zurückliefert. Das aktuelle LLM ruft keine autonomen
Recherche-Tools auf: Wetter und andere feste Werkzeuge steuert die Anwendung.
Allgemeines Modellwissen hat keine nachträglich ermittelbare Quellenliste.

Dieses Arbeitsprotokoll enthält keine internen Modellgedanken, vollständigen
Prompts, Nutzerfragen, Antworttexte, persönlichen Gedächtnisinhalte, Tokens
oder API-Schlüssel. Es liegt im begrenzten RAM-Journal hinter derselben
Anmeldung wie die bisherigen Dienstlogs. Bestehende sonstige Dienstlogs
bleiben davon unabhängig. Die Kennung verbindet Schritte derselben Anfrage;
sie ist keine Benutzerkennung.

### Zeigerinstrument

S-Meter mit Skala 1–9: roter Bereich links für schwaches Signal, rechts
stark. Im Ruhezustand zeigt es die Signalstärke aus der gemessenen Laufzeit
zu `/health`, beim Senden und Empfangen den Sprachpegel.

### Schrift, Symbole, Litaneien

- **Schrift:** Beschriftungen auf Gehäuse, Knöpfen und im Protokoll in
  *Century Schoolbook*, der Schrift der Adeptus-Mechanicus-Transferbögen
  (von der Community so identifiziert). Ausgeliefert wird die freie
  Variante TeX Gyre Schola (GUST Font License, `web/fonts/`), auf Latin-1
  verkleinert, je ca. 19 KB. Der Fernschreibertext bleibt Monospace.
- **Symbole** (selbst gezeichnete SVGs nach den Transferbögen):
  Schädel im Zahnrad (Kopfzeile, Anruf-Gerät, Browser-Symbol
  `web/mechanicus.svg`), Zahnrad mit drei Sechsecken (Wasserzeichen im
  Anruf-Bildschirm), Zahnrad mit Dürers magischem Quadrat (dreht sich in der
  Kopfzeile, solange der Server arbeitet, und steht über dem leeren Protokoll).
- **Litaneien:** Die Wählsequenz folgt der Litanei der Zündung („Die Seele
  des Maschinengottes umgibt dich …“, „Lebe!“), nach erfolgreicher Anmeldung erscheint die Bestätigung der Kennung, ein falscher Code mit „Im Namen des Omnissiah,
  identifiziere dich“ abgewiesen. Auflegen endet mit „Die Lebenden schwinden.
  Aber die Maschine besteht.“ Über dem leeren Protokoll steht ein zufälliger
  Spruch („Mens sana in machina sano“ …). Alle Texte stehen in `LITANY`
  in `web/app.js`.

## Bedienung

- **Halten zum Sprechen** (Maus, Finger oder Leertaste), loslassen schickt die
  Aufnahme. In den Einstellungen auf „Antippen statt halten“ umstellbar.
  Höchstens 29 s (Server-Grenze `SERVITOR_MAX_AUDIO_SECONDS=30`).
- **Frage tippen**: mobil über die volle Breite, zunächst zwei Zeilen; längere
  Texte vergrößern das Feld bis auf vier Zeilen. Darunter eine kompakte Zeile
  für Sprachaufnahme, Senden und gegebenenfalls Abbrechen. Enter sendet,
  Shift+Enter fügt einen Zeilenumbruch ein. Die Frage geht denselben
  Weg wie ein erkannter Satz, nur ohne Spracherkennung (`/v1/ask`, höchstens 500 Zeichen).
- **Abbrechen** oder `Esc`: Aufnahme verwerfen, Anfrage abbrechen, Ausgabe stoppen.
- **Einstellungen** (⚙): Persona (Servitor/Billy), Lore-Stufe, Stimme
  (Servitor/natürlich), Sprachkern (Auto/Frei/Lokal) – dieselben Werte wie im
  PiTFT-Menü, sie gehen pro Anfrage im Header `X-Servitor-Status` mit.
- Lange Geschichten („Erzähl mir eine lange Geschichte …“) laufen in Teilen;
  den nächsten Abschnitt holt die Seite, während der letzte Teil noch spielt.

Antworten kommen als Ogg/Opus, wenn der Browser es abspielen kann, sonst WAV.

## Was der Web-Client nicht kann (absichtlich)

Der Browser ist ein **Gast**. Er schickt weder `memory` noch `devctl` noch
`trip` im Status, daher:

- kein **persönliches Pi-Gedächtnis** (Fakten und Direktiven bleiben auf dem USB-Stick),
  keine Stimmerkennung, keine Termine aus Nextcloud,
- keine Gerätesteuerung (WLAN, Neustart, Herunterfahren) und kein Wartungsmodus,
- keine Fahrtauskunft (die fragt der Pi selbst ab),
- Akku-, Temperatur- und Netzfragen antworten „nicht verfügbar“ – es gibt kein
  Gerät, das sie messen könnte.

Uhrzeit, Datum, Wetter, „wer bist du“, freie Fragen an
das LLM und Geschichten funktionieren wie am Pi.

## Flüchtiges Webgespräch und nachvollziehbare Diagnose (Entwurf #121)

Der Web-Client hält einen temporären Gesprächsverlauf **nur im RAM von CT 107**.
Die Selektion der letzten und zum Thema passenden Runden benutzt die bereits
vorhandene `src/conversation.py`, aber nicht deren personenbezogene
Stick-Speicherung. Eine Browser-Anmeldung entspricht **keiner erkannten Stimme**.

- `POST /v1/web/conversation` erzeugt eine zufällige Gesprächs-ID; „Neues
  Gespräch“ beendet die alte Konversation samt noch offener Diagnoseaufträge.
- Die Gesprächs-ID gehört zu einem bestimmten Browser-Login (Cookie) oder
  einer pro Tab zufällig erzeugten Kennung, die nur mit gültigem API-Token
  akzeptiert wird. Zwei verschiedene Kennungen teilen keinen Kontext.
- Gespräch und Jobs haben ein hartes RAM-Ablaufdatum von zwölf Stunden.
  Abmelden löscht den Login-Kontext; Serverneustart verwirft alle Webgespräche.
  Der Browser zeigt dann einen ausdrücklich fehlenden Auftrag an; es wird
  niemals still ein vermeintlich fertiger Befund gezeigt.
- **Auftragsarchiv:** Kennung, Zeitraum, Status, Zeiten und die
  deterministische Zusammenfassung jedes Diagnoseauftrags stehen zusätzlich in
  `diagnose-jobs.json` im systemd-`StateDirectory` (`/var/lib/servitor-voice`,
  Rechte 0600), sieben Tage lang, höchstens 200 Aufträge. Keine Fragen,
  Antworten, Logauszüge, Modelltexte oder Browserkennungen. Ein Auftrag, der
  beim Neustart noch lief, gilt danach als „durch einen Neustart unterbrochen“.
  `SERVITOR_DIAGNOSE_ARCHIVE` setzt einen anderen Pfad, `off` schaltet das
  Archiv ab; ohne `StateDirectory` (lokale Entwicklung) bleibt alles im RAM.
- Ein Web-Selbsttest startet einen eigenen, **rein lesenden** Diagnoseauftrag.
  `src/web_diagnostics.py` fragt in zwei festen, begrenzten
  `journalctl`-Abfragen die Proximus-Units `servitor-voice.service` und
  `servitor-llm.service` sowie Warnungen der Prioritäten 0–4 **aus dem
  lokalen CT-107-Systemjournal** der letzten zwei Stunden ab (je 120
  Einträge, 64 KiB, acht Sekunden). Das zweite Journal wird nur nach
  lokaler Hostkennung ausgewertet: Proxmox-Host-/Remote-Journale werden
  verworfen. Kernelmeldungen werden per bestehenden LogWatch-Regeln auf
  OOM, Datenträgerfehler, Unterspannung und USB-Störungen klassifiziert;
  fremde Dienste liefern ausschließlich Zählwerte, niemals Rohtexte, Units
  oder Geheimnisse an Browser oder Modell. Fehlende Rechte, Trunkierung oder
  unklare Hostzuordnung sind ausdrücklich keine Entwarnung. **Keine**
  Wartungsaktionen, Reparaturen oder freien Shellbefehle im Webpfad.
- Der Standardzeitraum von zwei Stunden lässt sich mit
  `SERVITOR_DIAGNOSE_HOURS` (1–24) in `/etc/servitor-voice.env` ändern. Eine
  Frage wie „Logs der letzten 6 Stunden“ oder „Selbsttest der letzten zwölf
  Stunden“ gilt nur für diesen Auftrag; mehr als 24 Stunden lehnt Proximus ab
  und verweist auf „Logs von gestern“. Die Grenze von 120 Zeilen je Quelle
  bleibt; ein längerer Zeitraum wird dadurch eher als unvollständig ausgewiesen.
- Klassifikation und Wiederherstellungsbelege kommen deterministisch von
  `src/logwatch.py`. Erst danach darf eine Modellinterpretation erfolgen;
  sie ist als Hypothese markiert. Rohtranskripte, Logfreitext und Secrets
  gelangen nicht in den Modell-Diagnosekontext.
- `GET /v1/web/jobs?conversation=...&after=N` gibt begrenzte,
  durchnummerierte Diagnoseereignisse zurück. Das Vox-Terminal prüft sie
  regelmäßig und fügt abgeschlossene Ergebnisse ohne weitere Frage hinzu.
  Nach der Anzeige bestätigt es den Empfang über `POST /v1/web/ack`;
  der Server protokolliert diese Bestätigung genau einmal. Eine Unterbrechung
  lässt sich über den Ereigniszähler wieder aufnehmen.
- Auftragsfragen mit vollständiger Kennung oder eindeutigem Präfix ab acht
  Hex-Zeichen werden direkt gegen die **eigene** RAM-Sitzung aufgelöst, niemals
  aus dem LLM oder dem Journal geraten. Danach sucht der Server im
  Auftragsarchiv, dort aber nur mit der **vollständigen** 24-stelligen Kennung:
  Sie ist eine 96-Bit-Zufallszahl, die nur dem auslösenden Browser angezeigt
  wurde, und gilt deshalb nach Neustart und neuer Anmeldung als Berechtigung
  für Status und Zusammenfassung, nicht für Ereignisse oder Empfangsbestätigung.
- Fragen nach den **Logs von gestern** lösen einen gesonderten, lesenden
  Diagnoseauftrag für den gestrigen Kalendertag in der konfigurierten Zeitzone
  aus. Beide Journalabfragen bleiben hart begrenzt; bei mehr als 120 Zeilen
  pro Quelle wird die unvollständige Abdeckung ausgewiesen. Ältere Journale
  sind nur verfügbar, sofern CT 107 sie noch vorhält.
- Das CT-107-Systemjournal ist **kein Gesprächsarchiv**. Der Web-Chat besitzt
  ausschließlich den laufenden, flüchtigen Browserdialog und kann Aussagen
  über Gespräche von gestern oder aus anderen Sitzungen weder bestätigen
  noch widerlegen. Solche Fragen dürfen keine erfundenen Archivbelege erzeugen.
- Unbekannte Fehler, fehlende Leserechte, abgeschnittene Logs und fehlende
  Recovery-Belege sind **keine Entwarnung**. Der Browser zeigt diese Grenzen.

Die **Pi-Selbsttest-Reparaturen** werden durch diesen Webzugriff nicht
erweitert. Die vorhandene Pi-Integration des persönlichen Konversationsschemas
bleibt bewusst einer späteren Identitäts- und Eigentümerprüfung vorbehalten.

## Sicherheit

- Ohne Anmeldung liefert der Server nur die festen Dateien aus `WEB_FILES`
  (`server/servitor_server.py`); jeder andere Pfad unter `/web/` ergibt 404.
- **Kennwort-Login:** `POST /v1/login` prüft gegen einen scrypt-Hash; fünf
  Fehlversuche von einer Adresse sperren sie 15 Minuten. Das Cookie ist
  `HttpOnly`, `SameSite=Strict` und über den Tunnel `Secure`. Es zählt nur
  zusammen mit dem Header `X-Servitor-Client: web`, den eine fremde Seite
  nicht setzen kann. Es öffnet nur Anfragen, getippte Fragen, Geschichten,
  Sprachausgabe und das Journal; Status, Wartung, Nachtprüfung und
  Stimmdaten brauchen weiter den Token. Logins erscheinen im Journal
  (`web_login`, `web_login_failed`).
- Die Seite läuft mit strenger Content-Security-Policy (nur eigene Skripte,
  kein Inline-Code, keine fremden Verbindungen) und setzt Texte nur als
  `textContent`.
- Rate-Limit und „ein Turn zugleich“ gelten für Browser und Pi gemeinsam: Spricht
  gerade der Pi, wartet der Browser bis zu `SERVITOR_BUSY_WAIT_SECONDS` und
  bekommt sonst „Server beschäftigt“.
- Wer statt des Kennworts den Token im Browser speichert, gibt diesem Gerät
  dieselben Rechte wie dem Pi.

## Dateien

| Datei | Inhalt |
|---|---|
| `web/index.html` | Oberfläche, Einstellungsdialog |
| `web/app.js` | Anruf, Aufnahme, Anfragen, NDJSON-Auswertung, Wiedergabe |
| `web/vox.js` | Vox-Effekte: Geräusche, Funkfilter, Oszilloskop, Zeiger, Störungen |
| `web/capture-worklet.js` | AudioWorklet: Mikrofon → 16 kHz mono s16le |
| `web/style.css` | Darstellung |
| `web/manifest.webmanifest` | Startbildschirm-Symbol |
| `assets/display/servo-skull.png` | derselbe Schädel wie auf dem PiTFT |

## Ausrollen auf CT 107

`/opt/servitor-voice/repo` ist ein Git-Checkout, `web/` kommt mit dem Commit.
Vorher den Merge-/Deploy-Auftrag und den vollständigen freigegebenen
Commit-SHA prüfen. Im folgenden Beispiel den Platzhalter durch diesen
40-stelligen SHA ersetzen; keine alten Entwicklungsbranches ausrollen:

```sh
ssh root@172.22.2.11
pct exec 107 -- sh -c 'cd /opt/servitor-voice/repo && git fetch origin <freigegebener-40-stelliger-commit-sha> \
  && git checkout --detach FETCH_HEAD && git rev-parse HEAD > /opt/servitor-voice/DEPLOYED \
  && systemctl restart servitor-voice'
```

Prüfen (nach dem Laden der Modelle, ca. 10 s):

```sh
curl -s https://proximus.obivan.org/health                 # {"ok": true, "ready": true}
curl -sI https://proximus.obivan.org/ | grep -i content-type  # text/html
```

Wer aus einem anderen Checkout installiert, nimmt `server/install-ct.sh`; es
kopiert `web/` und den Schädel mit.
