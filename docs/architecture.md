# Architektur

Der Pi übernimmt Tasten, Aufnahme, Display, LED und Wiedergabe. Erkennung, Antwort und Stimme laufen im Normalbetrieb auf dem eigenen Servitor-Server **CT 107** ([ADR 0004](decisions/0004-servitor-server.md)); der Pi kann alles davon auch lokal, langsamer, als Fallback.

```text
PTT gedrückt (GPIO17 / SHIM A)
  → arecord 16 kHz mono → Uplink: chunked POST /v1/turn an CT 107 (+ Status-Snapshot des Pi)
CT 107
  → Vosk (live während des Uploads)
  → Regelwerk ohne LLM (Uhrzeit, Datum, Status, Akku, Identität) — sonst:
  → OpenRouter (Servitor-Persona + aktuelles Datum/Uhrzeit) — bei Ausfall: Qwen3-4B lokal
  → Piper Thorsten Emotional + Servitor-DSP → NDJSON (Fortschritt, Text, WAV)
Pi
  → Display/LED aus den Ereignissen → aplay → WM8960
```

[`src/ptt.py`](../src/ptt.py) orchestriert Tasten, Aufnahme, Menü und Zustandsfolge; [`src/remote_turn.py`](../src/remote_turn.py) ist der Server-Client; [`server/servitor_server.py`](../server/servitor_server.py) der Dienst auf CT 107. Lokal: [`src/transcribe.py`](../src/transcribe.py) (Vosk), [`src/llm.py`](../src/llm.py) (OpenRouter bzw. lokales LLM, Persona), [`src/voice_controls.py`](../src/voice_controls.py) (Piper, Wiedergabe, Lautstärke). Es gibt **einen** Verarbeitungs-Slot, keine Warteschlange.

Nach STT-Abschluss werden die PTT-Eingänge resynchronisiert; gehaltene Tasten brauchen Release. B verwirft ein laufendes STT-Ergebnis, beendet aber keinen nativen Vosk-Aufruf. Der Slot bleibt bis zum Abschluss gesperrt. Das Vosk-Modell wird beim Dienststart vorgewärmt, damit der erste PTT-Zyklus keinen Modell-Load bezahlen muss.

## Servitor-Server (CT 107) mit lokalem Fallback

Ist `ASSISTANT_BASE_URL` gesetzt ([Vorlage](../config/client.env.example)), streamt der Pi die Aufnahme schon während des Tastendrucks als chunked `POST /v1/turn` an den [Servitor-Dienst](../server/servitor_server.py). Erkennung, LLM, Synthese und DSP laufen dort; der Pi spielt nur die fertige WAV ab (Opus wird vorher mit ffmpeg dekodiert). [`src/remote_turn.py`](../src/remote_turn.py) kapselt den Client.

```text
PTT gedrückt → arecord 16 kHz mono → Pump-Thread
   ├─ capture.part.pcm (für den Fallback)
   └─ Uplink-Thread → chunked POST /v1/turn (nie blockierend)
PTT los → Abschlusschunk → NDJSON lesen → Display → aplay remote-reply.wav
```

| Server-Ereignis | Pi-Event / Display |
|---|---|
| `stage: recognize` | Fortschritt `stt/live_finalize` → ERKENNEN |
| `transcript` | `transcript` → DENKEN |
| `stage: think` | `llm_start` → DENKEN |
| `reply` | `llm_response` → SYNTHESE |
| `stage: synthesize` / `render` | Fortschritt `tts/synthesis` → SYNTHESE, `tts/dsp_render` → RENDERN |
| `audio` + `done` | `speech_started`, Fortschritt `tts/playback` → AUSGABE |

Der Fallback setzt dort an, wo der Server ausgefallen ist: Verbindung/Upload → lokale Vosk-Erkennung der mitgeschriebenen Aufnahme; LLM-Fehler → lokales LLM mit dem Server-Transkript; Synthese-/Renderfehler → lokale Piper-Ausgabe der Server-Antwort. „Keine Sprache erkannt“ wird nicht lokal wiederholt. Das Token steht nur in `/etc/pi-voice-assistant.env` und wird nie geloggt.

Während der Server erreichbar ist, läuft auf dem Pi keine zusätzliche Live-Vosk-Erkennung. Im Modus `hybrid` bleibt der vorgeladene Vosk-Worker für den Fallback bereit und erkennt bei Bedarf die mitgeschriebene WAV.

**Hardware-Messung 07.10.2026** (Pi Zero 2 W, LAN, WAV, Frage „Wie hoch ist der Eiffelturm?“, 3,6 s Aufnahme): Server nach Upload-Ende 1,67 s (STT-Abschluss 0,05 s, LLM 0,95 s, Synthese 0,48 s, Render 0,19 s). Loslassen bis Wiedergabestart ca. 2,0–2,5 s, vorher lokal ca. 13,7 s. Stimme unverändert; das Display durchlief alle Schritte. Diese Messung lief noch mit mitlaufender lokaler Vosk-Erkennung.

**SSH-Test 07.10.2026, Stand #37** mit [`scripts/test-remote-turn.py`](../scripts/test-remote-turn.py) (gleiche Uplink-/Job-Klassen wie `pi-ptt`, 1,61 s Frage in Echtzeit gestreamt, Zeiten ab Upload-Ende):

| Fall | Ergebnis |
|---|---|
| WAV, LAN | Audio bereit nach 1,48 s (Server 1,27 s), Wiedergabe auf dem WM8960 vollständig |
| Opus, LAN | Server 1,34 s, aber Audio erst nach 6,64 s bereit: die ffmpeg-Dekodierung kostet auf dem Pi Zero ca. 5 s |
| Server nicht erreichbar | `upload/network` nach 3 ms, lokaler Fallback erlaubt |
| Erste URL tot, zweite CT 107 | Umschalten nach 1,5 s Connect-Timeout, Audio nach 2,1 s (im echten Betrieb überlappt der Timeout mit der Aufnahme) |

Folgerung: Im LAN bleibt `ASSISTANT_AUDIO_FORMAT=wav`. Für den späteren Internetweg ist Opus erst sinnvoll, wenn die Dekodierung auf dem Pi schneller wird (z. B. residenter Decoder statt ffmpeg-Prozess).

### Offline-LLM auf CT 107

Fällt OpenRouter aus (kein Internet, keine Credits, Rate-Limit, Timeout, 5xx), antwortet ein lokales Modell im Container. [`server/install-llm.sh`](../server/install-llm.sh) baut `llama-server` aus llama.cpp `v0.5.0` (für die AVX2-CPU des Hosts) und lädt ein per SHA-256 geprüftes Qwen3-4B-Instruct-2507 (Q4_K_M, 2,5 GB; Auswahl siehe unten). [`servitor-llm.service`](../server/servitor-llm.service) betreibt es nur auf `127.0.0.1:8766`. Der Sprachdienst fragt OpenRouter mit 8 s Timeout und nach einem Fehler 60 s lang direkt das lokale Modell. Das `reply`-Event nennt das Modell (`local/qwen3-4b`), das Journal `llm_fallback` mit Grund. CT 107 hat dafür 4 Kerne und 5 GB RAM (llama-server ca. 2,1 GB fest belegt).

**Messung 08.10.2026** (Serverzeit inkl. STT/TTS):

| Fall | Antwort von | Server |
|---|---|---|
| Normal | OpenRouter | 1,1–2,2 s |
| Ungültiger Key (wie keine Credits) | lokal | 1,9 s |
| Kein Internet, erste Frage | lokal nach 8 s Timeout | 10,8 s |
| Kein Internet, folgende Fragen (60-s-Fenster) | lokal | 1,1 s |

**Modellwahl 08.10.2026** mit [`server/bench-local-llm.py`](../server/bench-local-llm.py) (8 Fragen, warmer Lauf, CT 107 mit 4 Kernen):

| Modell (Q4_K_M) | richtig | Median | Max | Anmerkung |
|---|---|---|---|---|
| Qwen2.5-3B-Instruct | ~4/8 | 2,7 s | 7,8 s | „ein Tag hat 60 Minuten“, 17×23 = 481 |
| Gemma 3 4B | ~5/8 | 7,2 s | 9,6 s | 17×23 = 746; Sliding-Window-Attention verhindert Prompt-Cache, jede Antwort ≥ 6 s |
| **Qwen3-4B-Instruct-2507** (gewählt) | ~6,5/8 | 3,0 s | 11,2 s | 1440 Minuten, 391, Canberra richtig; Tokio-Uhrzeit falsch; lange Erklärungen bis 11 s |

Mit Qwen3 4B und ungültigem OpenRouter-Key: Serverzeit 2,5–3,9 s. `llama-server` belegt ca. 2,1 GB fest (für AVX2 umsortierte Gewichte) plus freigebbaren Dateicache.

### Größeres Vosk-Modell: verworfen

[`server/bench-vosk.py`](../server/bench-vosk.py) vergleicht Modelle auf 20 mit Piper synthetisierten Fragen (zwei Sprecher, zwei Sprechtempi):

| Modell | WER | RAM | Ergebnis |
|---|---|---|---|
| `vosk-model-small-de-0.15` (aktiv) | 8,1 % | 225 MB | – |
| `vosk-model-de-0.21` ohne `rescore`/`rnnlm` | 10,5 % | 790 MB | nicht besser (teils nur Schreibweise „wieviel“) |
| `vosk-model-de-0.21` ohne `rescore` | – | – | OOM bei 5 GB Container-RAM |
| `vosk-model-de-0.21` vollständig | – | > 4,6 GB | OOM, auch ohne laufendes LLM |

Der Nutzen käme erst mit dem 2,1 GB großen `rescore`-Sprachmodell, für das der Host keinen RAM frei hat. Typische Restfehler des kleinen Modells: „ein Tag“ → „ein paar“, „nenne“ → „wenn die“. Eine bessere Erkennung bräuchte ein anderes Verfahren (z. B. Whisper), nicht ein größeres Vosk-Modell.

## Antworten ohne LLM, Status und Charakter

[`src/intents.py`](../src/intents.py) erkennt wenige feste Fragen konservativ und beantwortet sie ohne LLM: **Uhrzeit** („Zeitindex: 8 Uhr 37.“), **Datum**, **Status**, **Akku**, **„wer bist du“**, **Wetter** und die **Morgenlitanei**. Fragen mit einem anderen Ort („wie spät ist es in Tokio“, „Wetter in Rom“) und alles Unklare gehen an das LLM. Auf dem Server läuft der Abgleich vor OpenRouter (Zeiten in `SERVITOR_TIMEZONE`, Standard Europe/Berlin, weil CT 107 auf UTC läuft), auf dem Pi im lokalen Fallback; damit funktionieren diese Antworten auch ganz ohne Netz. Gemessen vom Pi aus: 0,5–1,7 s bis zum fertigen Audio, fast nur Sprachausgabe. Das Display zeigt solche Antworten als „direkt“.

Der **Status** ([`src/system_status.py`](../src/system_status.py)) entsteht aus einem Snapshot des Pi (Akku, Temperatur, Last, Speicher, Laufzeit, Spannungsflags), den der Pi bei jeder Anfrage als `X-Servitor-Status` mitschickt (nur Zahlen; der Server prüft und filtert). Reihenfolge: zuerst Warnungen (Unterspannung, Akku kritisch, Temperatur, Arbeits-/Datenspeicher knapp, Server nicht erreichbar), dann Energie, Temperatur, Verbindung, Zustand des Sprachkerns (extern oder Notbetrieb) und Laufzeit; Last und Speicher nur, wenn auffällig. Beispiel: „Status nominal. Energiespeicher 75 Prozent. Akkubetrieb. Kerntemperatur 46 Grad. Verbindung zum Server stabil. Laufzeit zwölf Stunden zwanzig Minuten. Befehl erwartet.“ Taste E und die gesprochene Frage nutzen denselben Text.

Der **Charakter** steht im Systemprompt in [`src/llm.py`](../src/llm.py): kybernetische Diensteinheit ohne eigenen Willen, „diese Einheit“ statt „ich“, „Bediener“, kurze Quittungen („Daten abgerufen.“), keine Gefühle oder Floskeln, Fakten vor Rolle („Daten unzureichend.“), keine erfundenen Aktionen, drei kurze Beispiele. Das LLM bekommt Datum und Uhrzeit des Bedieners am Ende des Prompts (der Prompt-Cache des lokalen Modells bleibt so gültig). Antworten werden für Piper geglättet: eine Zeile, keine Listen, Markdown, Gedankenstriche oder Emojis. [`server/sample-persona.py`](../server/sample-persona.py) vergleicht OpenRouter und lokales Modell mit festen Fragen. Mit OpenRouter trifft der Ton gut („Funktionszustand stabil. Keine Abweichungen.“, „Direktive abgelehnt. Diese Einheit hat keinen Zugriff auf Geräte.“); das lokale Modell ist inhaltlich schwächer und braucht 3–10 s.

### Morgenlitanei und Wetter

„Guten Morgen“, „Morgenbericht“, „Morgenlitanei“ oder „Lagebericht“ liefert einen kurzen Bericht ohne LLM. Proximus grüßt nicht zurück (ein Servitor kennt keine Höflichkeit), sondern meldet „Morgenbericht.“ (ab 11 Uhr „Tagesbericht.“, ab 18 Uhr „Abendbericht.“), bei erkannter Stimme davor „Bediener … identifiziert.“; dann Wochentag, Datum und Uhrzeit, Wetter, danach nur, was Aufmerksamkeit braucht: Akku im Akkubetrieb, Server nicht erreichbar oder WLAN aus, wartende Aktualisierungen. Beispiel: „Bediener Ivan identifiziert. Morgenbericht. Datum: Freitag, der neunte Oktober. Zeitindex: 7 Uhr 5. Außentemperatur 4 Grad, bedeckt. Heute 3 bis 12 Grad. Niederschlag möglich, 40 Prozent. Bericht Ende.“ In der Lore-Stufe VOLL beginnt er mit „Die Morgenlitanei beginnt. Ave Omnissiah.“

„Wie ist das Wetter?“, „Regnet es heute?“ beantwortet nur den Wetterteil. Das Wetter kommt von Open-Meteo ([`src/weather.py`](../src/weather.py), ohne API-Schlüssel) für den Ort aus `WEATHER_LAT`/`WEATHER_LON`; ohne Ort fehlt es im Bericht. Der Server fragt höchstens alle 20 Minuten nach (3 s Timeout, nach einem Fehler 2 Minuten Pause) und spricht keine Daten, die älter als eine Stunde sind. Im lokalen Fallback blockiert der Pi nie: er nutzt den letzten Stand und fragt im Hintergrund nach. Termine kommen per CalDAV aus Nextcloud (siehe unten).

**Termine (Nextcloud/CalDAV):** [`src/agenda.py`](../src/agenda.py) läuft auf dem **Pi**: ein Hintergrund-Thread fragt alle 5 Minuten die Kalender aus `CALDAV_URLS` ab (nach einem Fehler nach 1 Minute; eine `REPORT`-Anfrage je Kalender, nur der heutige Tag, 8 s Timeout), die Bedienung wartet nie darauf. Mit `<C:expand>` löst Nextcloud Serientermine und Zeitzonen selbst auf; der Server liest nur Titel, Beginn, Ende und abgesagte Termine. Gesprochen werden die heute noch anstehenden Termine (ganztägige zuerst, höchstens fünf, danach „Und 2 weitere.“), je Termin ein Satz: „Termine heute. Ganztägig: Geburtstag Anna. 9 Uhr 30: Zahnarzt.“, in der Lore-Stufe VOLL „Direktiven des Tages.“. Fragen: „Welche Termine habe ich heute?“, „Was steht heute an?“; der Morgenbericht nennt sie nach dem Wetter. Ist Nextcloud nicht erreichbar, gilt der letzte Stand des Tages, nie der vom Vortag. Die Zugangsdaten (App-Passwort) liegen nur in `/etc/pi-voice-assistant.env`. Der Pi schickt die heute noch anstehenden Termine (höchstens 12, Titel bis 80 Zeichen) mit jeder Anfrage als `X-Servitor-Agenda` mit, wie Status und Gedächtnis; der Server prüft sie, speichert nichts und braucht keine Zugangsdaten. Im Offline-Fallback beantwortet der Pi Terminfragen selbst. Erkennt der Server eine Stimme nicht als Bediener, antwortet er auf Terminfragen „Stimme nicht als Bediener erkannt. …“ und lässt die Termine im Morgenbericht weg. Im Offline-Fallback gibt es (wie beim Gedächtnis) keine Stimmerkennung. Getestet gegen einen CalDAV-Server (Radicale) mit Serientermin, Zeitzone und ganztägigem Termin; gegen die echte Nextcloud noch nicht.

### Lore-Stufen (Warhammer 40.000)

Die Einheit heißt **Servitor Proximus**. Wie viel Mechanicus-Vokabular einfließt, bestimmt die Lore-Stufe: Menü „Lore-Stufe AUS/DEZENT/VOLL“, Grundeinstellung `PTT_LORE_LEVEL` (Pi) bzw. `SERVITOR_LORE` (Server), Standard DEZENT. Der Pi schickt die Stufe mit seinem Status-Snapshot; der Server gibt sie an OpenRouter bzw. das lokale Modell und an die direkten Antworten weiter, der lokale Fallback nutzt sie ebenso.

| Stufe | LLM-Antworten | Direkte Antworten |
|---|---|---|
| AUS | keine Begriffe aus fiktiven Welten | „Zeitindex: 9 Uhr 15.“, Status endet mit „Befehl erwartet.“ |
| DEZENT | höchstens ein Begriff pro Antwort, nicht in jeder („Das Fleisch ist schwach.“) | Status endet mit „Maschinengeist ruhig. Befehl erwartet.“ |
| VOLL | Mechanicus-Liturgie, Anrufungen, binäre Lobgesänge, höchstens 60 Wörter | „Der heilige Chronometer meldet: 9 Uhr 15. Lob dem Omnissiah.“, Status als Litanei |

Fakten bleiben in allen Stufen vollständig; gemessen mit OpenRouter blieb etwa „330 Meter einschließlich Antenne“ in allen drei Stufen gleich.

### Sprechstil und Stimmeffekt

Neben dem Servitor gibt es das **Mensch-Modul**: Proximus vor seinem Umbau, Sergeant William Joseph „Billy“ Blazkowicz II ([Lore](concepts/lore-blazkowicz.md)). Er spricht in der Ich-Form, duzt, hat Soldatenhumor und darf Meinungen zeigen; die Inhaltsregeln (Fakten vor Rolle, keine erfundenen Aktionen) teilen sich beide Stile. Die Lore-Stufe gilt für beide: Billy ohne Lore ist ein namenloser Veteran ohne Begriffe aus Warhammer, DEZENT nennt Imperiale Armee und Phobos IX, VOLL spricht als Gardist.

| Schalter | Werte | Wirkung |
|---|---|---|
| Sprechstil | `servitor` / `mensch` | Systemprompt (`PERSONA_PROMPTS`, `LORE_PROMPTS[persona]` in `src/llm.py`) |
| Stimmeffekt | `servitor` / `natural` | Servitor-DSP oder `NATURAL_FILTER_GRAPH` (etwas tiefer und wärmer) in `src/voice_effects.py`; Piper-Sprecher bleibt gleich |

Der Pi schickt `persona` und `voice` mit dem Status-Snapshot; der Server wählt danach Prompt und Effekt. Ältere Pis ohne die Felder bekommen den Servitor. Lokal auf dem Pi wirkt der Stimmeffekt nur mit `TTS_VOICE_PROFILE=servitor` (dort ist das `thorsten_emotional`-Modell geladen); vorgefertigte Ansagen (Alarme, Aufwachen) klingen vorerst weiter nach Servitor.

**Feste Sätze** (Uhrzeit, Datum, Akku, Status, Netz, Updates, Morgenbericht, Alarme, Aufwachen, Herunterfahren, Gedächtnisbefehle, Wartungsergebnisse) gibt es in fünf Stilen (`system_status.phrase_style`): `off`, `light`, `full` für den Servitor sowie `billy` und `billy_full` für Billy, also ohne bzw. mit voller Lore. Billy sagt dann z. B. „Es ist 7 Uhr 15.“ statt „Zeitindex: 7 Uhr 15.“, „Achtung. Akku bei 9 Prozent. Ich brauch Strom, Boss.“ oder „Gemerkt.“. Die Fakten bleiben gleich. Den Stil leiten Pi und Server aus `lore` und `persona` im Status-Snapshot ab.

„Wer bist du?“ und „Wie geht es dir?“ beantwortet der Servitor mit festen Sätzen ([`src/intents.py`](../src/intents.py)); im Sprechstil Billy gehen beide Fragen an das Sprachmodell, damit er selbst antwortet. „Systemstatus“, Uhrzeit, Akku usw. bleiben in beiden Stilen feste Antworten.

### Gefühle

[`src/mood.py`](../src/mood.py) simuliert genau eine Emotion mit einer Stärke von 0 bis 1: zufrieden, freudig, neugierig, gelangweilt, gereizt, müde oder besorgt. Sie halbiert sich alle 10 Minuten; unter 0,15 gilt sie als neutral. Der Pi führt die Stimmung, schickt sie mit jedem Turn im Status-Snapshot (`mood`, `mood_level`, `mood_refuse`) mit, und der Server baut daraus einen Prompt-Abschnitt. Schalter: Menü Persönlichkeit → **Gefühle AN/AUS** (gespeichert, Grundeinstellung `PTT_EMOTIONS`).

| Auslöser | Gefühl |
|---|---|
| Lob, Dank | zufrieden, bei wiederholtem Lob freudig |
| Beleidigung („Blechbüchse“, „Halt die Klappe“), dieselbe Frage zweimal binnen 2 min | gereizt (vom Bediener verursacht) |
| „Warum …“, „Erzähl …“ | neugierig |
| Akku unter 20 % ohne Netzteil | müde |
| CPU-Temperatur ab 70 °C oder Last ab 90 % | gereizt (vom System verursacht) |
| Server nicht erreichbar | besorgt |
| Aufwachen nach über 1 Stunde Ruhe | gelangweilt |
| Reaktion des Modells: `[stimmung:…]` am Anfang der Antwort | schiebt die Stimmung ein Stück in diese Richtung |

Die Markierung `[stimmung:…]` wird auf dem Server und auf dem Pi entfernt und nie gesprochen. **Billy** zeigt die Stimmung offen im Ton. Beim **Servitor** bricht sie höchstens einmal pro Antwort als Fehler durch („Fehler. … Korrektur.“). **Verweigern** darf er nur, wenn er vom Bediener stark gereizt ist (ab 0,7), höchstens jede zweite Anfrage und nie bei Hilferufen („Hilfe“, „Notfall“, „brennt“, „Arzt“ …). Uhrzeit, Alarme, Gedächtnis- und Menübefehle laufen ohnehin nicht über das Sprachmodell.

**Nach einem Neustart** ist er neutral. Jeder Verlaufseintrag auf dem Gedächtnis-Stick speichert die Stimmung seiner Antwort (`mood: "gereizt:0.62"`). Aus den neuesten drei, die jünger als 12 Stunden sind, entsteht eine schwache Grundstimmung (höchstens 0,3).

**Display:** Billys Gesicht zeigt die Stimmung im Ruhezustand: Grinsen, Zähne, unruhiger Blick, abgewandter Kopf. Beim Servitor blitzt bei starker Stimmung (ab 0,5) alle 15 Sekunden für eine halbe Sekunde Billys Gesicht als Bildstörung durch den Schädel.

### Gerätesteuerung per Sprache

[`src/device_control.py`](../src/device_control.py) erkennt wenige feste Befehle, auf dem Server und im Offline-Fallback des Pi; ausgeführt wird immer auf dem Pi:

| Befehl (Beispiele) | Wirkung |
|---|---|
| „WLAN aus“, „Schalte das WLAN aus“ | WLAN sofort per `rfkill` aus; danach läuft alles lokal („WLAN deaktiviert. Lokaler Betrieb.“) |
| „WLAN an“, „WLAN einschalten“ | WLAN wieder an (wird offline auf dem Pi erkannt) |
| „Starte dich neu“, „Neustart“ | Rückfrage, dann Neustart des Pi über den Wartungsdienst (sonst `systemctl reboot`) |
| „Geh schlafen“, „Ruhemodus“, „Energiesparmodus“ | ohne Rückfrage in den Schlafzustand (Display und LED aus, WLAN je nach `PTT_SLEEP_WLAN`); Aktivierungswort oder Taste weckt |
| „Fahr dich herunter“, „Herunterfahren“, „Schalt dich aus“, „Mach dich aus“, „Terminiere dich selbst“, „Zerstöre dich“, „Geh sterben“ | Rückfrage, dann `systemctl poweroff`; wieder an nur per Schalter |

**Bestätigung:** Neustart und Herunterfahren fragen zurück („… Bestätigen: Bestätigt oder Taste E.“). Bestätigt wird innerhalb von 20 s mit „Bestätigt“, „Ja“ oder Taste E; die Antwort muss allein stehen (höchstens mit „bitte“), „Mach das Licht an“ bestätigt nie. „Nein“/„Abbrechen“ oder Taste B brechen ab („Abgebrochen.“), jede andere Frage verwirft die Rückfrage und wird normal beantwortet. Der Pi hält die offene Rückfrage und schickt sie im Status-Snapshot mit (`pending`), so erkennt auch der Server die Bestätigung. Ausgeführt wird erst, wenn die Ansage („Einheit fährt herunter.“) gesprochen ist.

**WLAN bei Bedarf:** Ist das WLAN aus und eine Frage braucht das Sprachmodell, schaltet der Pi es selbst ein („Anfrage braucht Netz. WLAN wird aktiviert.“), wartet bis zu 25 s auf eine Adresse und beantwortet die Frage dann über OpenRouter; folgende Anfragen gehen wieder an CT 107. Im Sprachkern LOKAL (Modell auf CT 107) bittet er stattdessen, die Frage gleich zu wiederholen. Uhrzeit, Status, Termine und Gedächtnis brauchen kein Netz und schalten nichts ein.

**Rechte:** Fremde Stimmen (Server erkennt sie nicht als Bediener) bekommen „Stimme nicht als Bediener erkannt. Befehl verweigert.“; offline gibt es wie beim Gedächtnis keine Stimmprüfung, Taste E setzt Zugang zum Gerät voraus. Im Wartungsmodus bleibt „Starte neu“ dessen eigene Aktion (Bestätigung mit E); den **Server** startet nur der Wartungsmodus neu. Herunterfahren und Neustart erlaubt die polkit-Regel [`deploy/50-pi-voice-poweroff.rules`](../deploy/50-pi-voice-poweroff.rules) dem Dienstbenutzer, `scripts/install-voice-service.sh` installiert sie. Sprechstil BILLY hat eigene Sätze („Ich mach dann mal aus.“).

### Stoppwörter

„Stop“, „Abbruch“, „Sei still“, „Klappe halten“, „Halt den Mund“, „Hör auf“, „Das reicht“, „Ruhe“ und ähnliche Wörter beenden die Antwort in beiden Sprechstilen: Proximus sagt nichts, verwirft wartende Ansagen und kehrt in den Ruhezustand zurück (das Aktivierungswort hört wieder mit). Erkannt wird das nur, wenn die ganze Äußerung ein solches Wort ist, höchstens mit Füllwörtern wie „bitte“, „jetzt“ oder „Proximus“ (`intents.is_stop`); „Wie stoppt man eine Blutung?“ geht weiter an das Sprachmodell. Der Server antwortet dann mit dem Ereignis `stop` ohne Audio; der Pi prüft das Transkript zusätzlich selbst, auch gegenüber älteren Servern.

Während Proximus spricht, ist das Mikrofon aus (sonst hört es die eigene Stimme). Unterbrechen geht dann mit der Sprechtaste (stoppt die Ausgabe sofort; „Stop“ hineinsprechen beendet ohne neue Antwort) oder mit B. Unterbrechen nur per Stimme bräuchte Echounterdrückung und ist noch offen.

## Aktivierungswort

Neben den Tasten startet **„Hey Jarvis“** eine Anfrage (vortrainiertes openWakeWord-Modell; ein eigenes „Hey Servitor“ ist geplant). Ist `PTT_WAKE_WORD` gesetzt, hört [`src/wake_listener.py`](../src/wake_listener.py) im Ruhezustand mit und gibt das Mikrofon frei, sobald eine Taste gedrückt wird, eine Anfrage läuft oder der Servitor spricht (plus 0,6 s gegen das eigene Echo). Nach dem Wort startet die normale Aufnahme; [`src/endpoint.py`](../src/endpoint.py) beendet sie nach 0,9 s Sprechpause oder verwirft sie still, wenn 5 s lang niemand spricht. Eine Taste während einer solchen Aufnahme übernimmt sie (Ende beim Loslassen). Das Menü schaltet das Mithören ab („Aktivierungswort AUS“); das Display zeigt dann wieder „Zum Sprechen halten“ statt „„Hey Jarvis“ oder Taste“. Audio verlässt den Pi erst nach dem Aktivierungswort.

[`src/wakeword.py`](../src/wakeword.py) betreibt die drei ONNX-Modelle von openWakeWord direkt mit onnxruntime und numpy aus der Piper-Umgebung. Das Paket selbst würde scipy und scikit-learn nachziehen und verlangt `tflite-runtime`, das es für Python 3.13 auf ARM nicht gibt. Auf sechs Testclips waren die Werte identisch mit openwakeword 0.6.0 (Abweichung 0,0000). Modelle: [`scripts/install-wakeword.sh`](../scripts/install-wakeword.sh), Prüfsummen fest; Code Apache 2.0, vortrainierte Modelle **CC BY-NC-SA 4.0** (nur nicht-kommerziell).

**Rechenaufwand auf dem Pi Zero 2 W:** Ungeschaltet 56 % eines Kerns (das Einbettungsmodell kostet 36 von 45 ms je 80-ms-Block); Int8-Quantisierung verwarf zu viel Genauigkeit (0,94 → 0,77). Eine Ruheschaltung rechnet leise Blöcke nur mit dem billigen Mel-Spektrum und einer zwischengespeicherten Stille-Einbettung weiter. Gemessen mit `wake_stats` im Journal: stiller Raum ~16–20 % eines Kerns (16–18 % der Blöcke exakt), belebter Raum (Gespräch/TV) ~35–50 %. RAM: ~33 MB im Dienst. Ohne Aktivierungswort liegt `pi-ptt` bei ~4 %.

## Alarme, Strom und Netz

[`src/alarms.py`](../src/alarms.py) prüft alle 10 s (zusammen mit dem Akku) und spricht Alarme, sobald die Einheit frei ist; der wichtigste aktive Alarm steht rot im Ruhebildschirm, kritische lassen die LED orange blinken. Menü „Alarme AUS“ schaltet die Ansagen stumm (`PTT_ALARMS=0`).

| Auslöser | Verhalten |
|---|---|
| Akku (ohne Netzteil) | Warnung 1/2/3 bei 15/10/6 % (direkt auf die passende Stufe, ein Satz); nach der letzten **Herunterfahren nach 60 s** (`systemctl poweroff`, polkit-Regel [`deploy/50-pi-voice-poweroff.rules`](../deploy/50-pi-voice-poweroff.rules)); Netzteil anstecken bricht ab. Läuft auch bei stummen Alarmen. |
| Stromquelle wechselt | „Netzbetrieb. Energiespeicher N Prozent.“ bzw. „Akkubetrieb. …“ |
| Unterspannung, Temperatur ≥ 75 °C, RAM ≤ 8 % frei oder Swap ≥ 85 %, Last ≥ 90 % über eine Minute | Alarm mit Abstand zwischen Ein- und Ausschaltschwelle |
| Netzwerk, Internet (openrouter.ai/1.1.1.1:443 alle 30 s), Server | nach zwei Fehlprüfungen Alarm, Entwarnung bei Rückkehr; bei Netzausfall keine Folgealarme; nach dem Einschalten des WLAN 60 s Schonfrist |

**Vorgefertigte Ansagen:** Alarme werden nicht live synthetisiert. [`src/alarm_audio.py`](../src/alarm_audio.py) zerlegt jeden Alarmsatz an den Zahlen in Bausteine („Warnung“, „1“, „von“, „3“, „Energiespeicher bei“, „15“, „Prozent. Netzteil anschließen.“). Einmal nach jedem Deploy rendert

```sh
set -a; . /etc/pi-voice-assistant.env; set +a
python3 /opt/pi-voice-assistant/src/alarm_audio.py build --prune
```

alle Bausteine und die Zahlen 0–100 über `/v1/speak` in der Servitor-Stimme (144 Clips, nur fehlende; ein Clip alle 6 s, damit das Ratenlimit für echte Anfragen frei bleibt, ~15 min beim ersten Mal) nach `models/alarm-voice/` und schneidet die Stille an den Rändern ab. `pi-ptt` liest die Clips nur (der Dienst hat `ProtectSystem=strict`), hängt die WAV-Daten mit kurzen Pausen aneinander und spielt sie mit `aplay`. So kommen Alarme auch offline, ohne Server und bei Volllast oder Speichermangel ohne Rechenaufwand. Fehlt ein Baustein (neuer Text, Build nicht gelaufen), wird wie früher lokal mit Piper gesprochen; das Journal zeigt `speech_started` mit `clips=false`.

Es gibt zwei Clip-Sätze: die Servitor-Sätze in der Maschinenstimme (Dateinamen wie bisher) und Billys Sätze (Sprechstil `mensch`) in der natürlichen Stimme. Für diese schickt `build` `X-Servitor-Status: {"voice": "natural"}` mit, und ihr Name enthält die Stimme. Nach dem Update auf diese Version einmal `alarm_audio.py build --prune` laufen lassen: Die vorhandenen Servitor-Clips bleiben, Billys etwa 240 Clips kommen dazu (~25 min). Andere Kombinationen, also Servitor mit natürlicher Stimme oder Billy mit Maschinenstimme, finden keine Clips und werden live gesprochen.

**WLAN** lässt sich über `PTT_WLAN=on|off` (beim Dienststart) und das Menü schalten (`rfkill`, udev-Regel [`deploy/90-rfkill-netdev.rules`](../deploy/90-rfkill-netdev.rules)). Ohne LAN-Kabel ist der Pi dann offline: lokaler Betrieb, keine Server-/Netzalarme, Status „WLAN deaktiviert“.

**Sprachkern AUTO/FREI/LOKAL** (Menü, `PTT_LLM_MODE=auto|free|local`): AUTO nutzt `OPENROUTER_LLM_MODEL` (Mistral Medium 3.5), FREI das wenig eingeschränkte `OPENROUTER_FREE_MODEL` (Dolphin Mistral 24B Venice), beide mit lokalem Ersatz bei Ausfall. Bei LOKAL antwortet CT 107 nur mit dem lokalen Modell, OpenRouter wird nie gefragt; im Fallback auf dem Pi gibt es dann nur die Antworten ohne LLM. Der Persona-Prompt verbietet kein Thema und weist das Modell an, nicht auszuweichen oder zu moralisieren; Grenzen setzen nur noch Modell und OpenRouter selbst.

**Speicher:** Der Vosk-Bereitschaftsprozess (~190 MB) wird freigegeben, solange CT 107 erreichbar ist (verfügbarer RAM 50 → ~220 MB), und bei Serverausfall wieder vorgeladen.

## Display: Servo-Skull

Im Ruhezustand und beim Sprechen zeigt das PiTFT einen Servo-Skull ([`src/skull.py`](../src/skull.py)) mit rot pulsierendem Auge: ruhig atmend im Leerlauf (4 Bilder/s), beim Sprechen im Takt der Lautstärke der Antwort (Hüllkurve der WAV, 50-ms-Schritte, `/run/pi-ptt/speech-envelope.json`). Das Auge wird automatisch gefunden und in 12 Stufen vorberechnet. Das verwendete Pixel-Art-Bild (r/PixelArt, „16-color Warhammer servo skull wallpaper“) liegt **nur auf dem Pi** (`PI_DISPLAY_SKULL`, Standard `/opt/pi-voice-assistant/models/display/servo-skull.png`), nicht in diesem öffentlichen Repository; ohne Datei zeichnet der Code einen eigenen schlichten Schädel. Display-CPU im Leerlauf ~10 % eines Kerns.

**Billy (Sprechstil `mensch`)** zeigt statt des Schädels das Gesicht aus der Doom-Statusleiste ([`src/face.py`](../src/face.py)) in einem grauen, abgeschrägten Rahmen. Die Zeile richtet sich nach dem **Akku** wie die Gesundheit im Spiel (100–80 % sauber … unter 20 % blutig, ab 3 % ohne Netzteil tot), die Spalte nach dem, was er tut:

| Zustand | Gesicht |
|---|---|
| Bereit | geradeaus, alle paar Sekunden ein kurzer Blick zur Seite |
| Zuhören | geradeaus |
| Verstehen/Denken/Synthese | Kopf dreht sich langsam links und rechts |
| Sprechen | nach der Lautstärke der Antwort: leise geradeaus, mittel Zähne, laut offener Mund |
| Abbruch (B, „Stop“, „Klappe halten“) | kurz zusammenzucken, dann den Kopf wegdrehen (2,5 s) |
| Alarm | abwechselnd zusammenzucken und geradeaus |
| Laden | ab und zu ein Blitz „Gottmodus“ (gelbe Augen) |
| Ruhe | gedimmt, geradeaus |

**Zusätzliche Bilder** erzeugt `face.derive()` beim Start aus den Pixeln des Sheets, je Gesundheitsstufe: `blink` (Augen zu), `squint` (halb geschlossen), `talk_half` und `talk_open` (Mund wie bei „Zähne“ bzw. „Autsch“, aber mit ruhigen Augen) und `wide` (Brauen hoch, große Augen). Die Augen findet `face.py` dort, wo sich die beiden Geradeaus-Bilder unterscheiden: Doom-Augen sind eine bis zwei Pixelzeilen Augenweiß mit Pupille, darüber eine dunkle Oberlid-Linie; Brauen und Nasenrücken bleiben außen vor. Beim Blinzeln wird die Oberlid-Linie zu Haut im Schatten und das Auge zu einem dunklen Wimpernstrich (wie in Pixelart üblich), halb geschlossen wird das Weiß abgedunkelt. Findet sich in einer blutigeren Zeile kein eigenes Auge (rot unterlaufen, Haare), wird die Maske des sauberen Gesichts an die Stelle verschoben, an der dieses Gesicht sitzt. Den Mund findet es durch den Vergleich mit „Zähne“ und „Autsch“. Damit blinzelt Billy alle paar Sekunden, spricht mit zwei Mundstellungen statt Grimassen (wütend weiter mit „Zähne“ und „Autsch“), hat bei Müdigkeit schwere Lider, schaut neugierig oder besorgt mit großen Augen und döst im gedimmten Ruhezustand mit geschlossenen Augen.

**Sprechen:** Beim Abspielen einer Server-Antwort berechnet der Pi aus der Lautstärkekurve (50-ms-Schritte) und dem Antworttext Mund-Codes ([`src/visemes.py`](../src/visemes.py)). Die Spitzen der Kurve sind die Silben, und jede bekommt der Reihe nach den nächsten Vokal des Textes: a → offener Mund, e/i/ei/ä → Zähne, o/u/ö/ü/eu → runder Mund (`talk_round`, der offene Mund um je einen Pixel verengt), Pausen → Mund zu. Laute Stellen öffnen weiter, jede Form hält mindestens 100 ms. In die Hüllkurven-Datei (`/run/pi-ptt/speech-envelope.json`) kommen nur diese Codes (`.aAeEoO`), nie Text. Ohne Codes (lokale Piper-Ausgabe, ältere Pis) folgt der Mund wie bisher nur der Lautstärke. Ist Billy gereizt, spricht er weiter mit „Zähne“ und „Autsch“.

Neu gezeichnet wird nur, wenn sich das Gesicht ändert. Beim Umschalten zwischen Servitor und Billy flackert nach dem Schließen des Menüs knapp eine Sekunde lang eine Bildstörung aus Schädel und Gesicht. Die Sprites (id Software) liegen in [`assets/display/doom-faces.png`](../assets/display/doom-faces.png); `install-voice-service.sh` kopiert sie nach `PI_DISPLAY_FACE` (Standard `/opt/pi-voice-assistant/models/display/doom-faces.png`). Erwartet wird das übliche Sheet mit fünf Zeilen à acht Gesichtern auf transparentem Grund, „Gottmodus“ hinter der ersten und „tot“ hinter der letzten Zeile. `face.py` findet die Sprites an den transparenten Lücken und skaliert nur ganzzahlig. Ohne Datei behält Billy den Schädel.

## Sprachausgabe

**Quittungston:** Beim Loslassen der Taste (oder wenn das Aktivierungswort-Mithören die Sprechpause erkennt) spielt der Pi sofort einen kurzen Ton ([`src/cue.py`](../src/cue.py), 0,37 s): ein anlaufender Servo, dann sieben Bit („P“ für Proximus) als hohe und tiefe Pieptöne. So ist hörbar, dass die Anfrage angenommen wurde, lange bevor die Antwort kommt. Der Ton wird beim Start mit der Standardbibliothek erzeugt und per `aplay` im Hintergrund abgespielt. Weil `plughw` nicht geteilt wird, wartet jede Sprachausgabe bis zu 0,6 s, bis der Ton fertig ist (in der Praxis nie: die Serverantwort kommt nach ca. 1,5 s). Menü „Quittungston AN/AUS“, Grundeinstellung `PTT_CUE`.

Im Normalbetrieb erzeugt CT 107 die Stimme (Piper Thorsten Emotional, Speaker 4, Referenz-DSP aus PR #26) und der Pi spielt die fertige WAV nur ab. Lokal auf dem Pi (Statusansage mit E, Fallback) bleibt Piper 1.8.0 resident: `servitor` nutzt dasselbe Modell mit einer Sprechkonfiguration, bei der Wörter nur leicht langsamer sind und zusätzliche Satzpausen den schweren Befehlston erzeugen; `normal` nutzt Thorsten Low.

```text
lokaler Text (E-Status, Fallback)
  -> resident Piper
  -> erster 16-Bit-PCM-Chunk
  -> FFmpeg-DSP (gestreamt; auf dem Pi derzeit TTS_PLAYBACK_MODE=buffered)
       metal / flanger / chorus / stutter / aura / doppler / ringmod / limiter
  -> ALSA -> WM8960
```

[`src/voice_effects.py`](../src/voice_effects.py) enthält die gestreamte und die dateibasierte FFmpeg-Variante.

## Display

Das Adafruit mini PiTFT 1,3″ läuft separat vom Sprachdienst direkt über SPI/ST7789. `src/display.py` prüft beim Boot und während des Betriebs SPI, WM8960, Netzwerk, Vosk-/TTS-Modellpfade und den Zustand von `pi-ptt.service`. Die zugehörige Unit ist `deploy/pi-display.service`; die Display-Abhängigkeiten liegen in einer eigenen Venv unter `/opt/pi-voice-assistant/.venv-display`.

Der Display-Dienst greift nicht in Aufnahme, STT oder TTS ein. `ptt.py` veröffentlicht zusätzlich zu den vollständigen Journal-Events einen minimierten, atomar ersetzten Snapshot unter `/run/pi-ptt/display-event.json`. Darin stehen nur Eventname, Version und Zeitstempel. Der Display-Prozess liest diesen Snapshot mit 100-ms-Takt und bildet ihn auf `BEREIT`, `ZUHÖREN`, `VERSTEHEN`, `DENKEN`, `SPRECHEN` oder kurzzeitig `FEHLER` ab. System-/Netzwerkprobes bleiben auf einem separaten 2-s-Takt.

`DENKEN` wird durch `transcript`/`llm_start` gesetzt; `llm_response` bzw. `speech_started` wechseln auf `SPRECHEN`. LLM-Fehler werden wie STT-/TTS-Fehler kurz als `FEHLER` angezeigt.

OpenRouter verarbeitet nur Text; Mikrofon-Audio geht nur an den eigenen CT 107. Schlüssel und Token kommen ausschließlich aus dem von systemd geladenen Environment. Wake Word, Echounterdrückung und Kamera sind keine aktuellen Funktionen. Menü, Statusinformationen, Akku und Lautstärke auf dem Display: [Display](display.md).

## Betrieb und Grenzen

Die [Unit](../deploy/pi-ptt.service) läuft als `obivan` mit `audio/gpio/i2c`, ohne root. Runtime-Verzeichnis ist `/run/pi-ptt`; Code unter `/opt`, Home gesperrt. Ein dedizierter Dienstbenutzer ist eine offene Verbesserung, keine bereits implementierte Isolation.

Aufnahme hat standardmäßig 30 s Limit; STT-/LLM-/TTS-Fehler werden protokolliert und beenden den Dienst nicht. Ohne Netz arbeitet der Pi lokal weiter (Vosk, Antworten ohne LLM, Piper); nur freie Fragen brauchen OpenRouter oder den Server. Die Unit wartet nicht auf `network-online.target`. WAVs sind flüchtig; Transkripte und LLM-Antworten stehen im Journal.

Messwerte stehen ausschließlich unter [STT](speech-to-text.md) und [TTS](local-speech.md); Hardware-Abnahmen unter [PTT](push-to-talk.md), [Button SHIM](button-controls.md) und [Erweiterungen](hardware-bring-up.md). Entscheidungen: [Pi-Client](decisions/0001-client-server.md), [OS](decisions/0002-operating-system.md), [Vosk-only STT](decisions/0003-hybrid-stt.md), [Servitor-Server](decisions/0004-servitor-server.md).
