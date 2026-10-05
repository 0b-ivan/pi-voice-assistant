# Piper auf dem Pi Zero 2 W messen

Der aktuelle speak.py-Wrapper startet für jede Ansage einen neuen Piper-Prozess
und wartet auf das vollständige WAV vor der Wiedergabe. Dieser Test vergleicht
frische CLI-Prozesse mit einer einmal geladenen PiperVoice im selben Prozess.
Er startet keinen dauerhaften Dienst und ändert weder PTT noch Mixer oder Modelle.

## Voraussetzung und Ablauf

Piper ist bereits unter `/opt/pi-voice-assistant/.venv` installiert, das Modell
`/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx` und seine `.onnx.json` liegen
vor. Der Test verwendet die in PR #14 eingerichtete Umgebung; kein erneuter
Download und keine Paketinstallation durch das Messskript.

Vorher A/GPIO17 einmal für einen kurzen Satz benutzen und das `transcript` mit
`provider=vosk` abwarten, damit das Vosk-Modell im Sprachdienst geladen ist.
Eine laufende Piper-Ansage ebenfalls bis `speech_finished` abwarten. Während
des Tests keine Tasten drücken und keinen zweiten TTS-/STT-Test starten.
Zwischen der Vosk-Transkription und der Messung den Dienst nicht neu starten:
das Modell wird erst bei der ersten Transkription geladen. Eine Statusansage
mit dem Text „Offline-Spracherkennung“ bestätigt nur die Konfiguration.

Im Checkout der Messversion als **obivan**, ohne sudo:

```bash
python3 scripts/profile-piper.py --output /tmp/pi-piper-resources.json
cat /tmp/pi-piper-resources.json
```

Der Test dauert abhängig vom Pi mehrere Minuten und meldet jeden fertigen
Abschnitt. Standard: drei neue Piper-CLI-Prozesse, dann einmal Modell laden,
drei Erzeugungen mit demselben geladenen Modell und zehn Sekunden Ruhe mit
dem weiterhin geladenen Modell. Testtext: `Hallo Ivan, ich bin bereit.`
Erzeugte WAV-Dateien werden nicht abgespielt und am Ende gelöscht.
Die CLI erhält den Text über stdin; die API erhält denselben Text direkt.
Eigener `--text` muss eine einzelne Zeile sein. Führende und abschließende
Leerzeichen werden für beide Wege entfernt und der verwendete Text steht im Bericht.
Die JSON-Auswertung bleibt am angegebenen Pfad; bitte zurückmelden, nicht committen.

Ctrl-C beendet den Test und seine Piper-Kindprozesse. Ein Zeitlimit von 180 s
gilt je frischem CLI-Durchlauf bzw. für den gesamten Modell-im-Speicher-Worker;
bei Abbruch bleibt ein Teilbericht mit `error` erhalten. Das gilt auch für
SIGTERM/SIGHUP während der ersten Snapshots;
noch nicht erfasste Vorherwerte bleiben `null`. Weitere Signale während der
Bereinigung und Berichtsspeicherung lösen keinen zweiten Abbruch aus.
Modell und venv müssen vorhanden sein. Die Limits lassen sich für einen kürzeren Test
mit `--repeats 2 --idle-seconds 5` ändern. Der produktive Dienst läuft weiter.

## Welche Werte wir vergleichen

| Wert | Bedeutung |
|---|---|
| fresh_process / elapsed_seconds | Zeit bis zum fertigen WAV einschließlich Python-, Piper- und Modellstart. |
| load_once | Import von Piper/ONNX und Modellladen im langlebigen Worker; Interpreterstart liegt davor. |
| resident_1 | Erste Erzeugung nach Modellladen; kann zusätzliche Initialisierung enthalten. |
| resident_2 / resident_3 | Weitere Erzeugungen mit demselben Modell; Kandidaten für den späteren Dauerbetrieb. |
| resident_idle / rss_mib | Belegter RAM nach mehreren Erzeugungen, während das Modell zehn Sekunden geladen bleibt. |
| resident_idle / cpu_pct_one_core | CPU-Verbrauch während dieser Ruhephase. |
| peak_rss_mib | Linux-RSS-Spitze des Piper-CLI-Prozesses oder des geladenen Workers, jeweils Lebenszeit-Höchstwert. |
| swap_mib | Aktuell ausgelagerte Speicherseiten des geladenen Workers. |
| audio_seconds / real_time_factor | WAV-Dauer und Zeit bis zum fertigen WAV geteilt durch Audio-Dauer. |
| system_before / system_during / system_after | Verfügbarer System-RAM und gesamte Swap-Belegung vor, während und nach dem Test. |
| swap_in_mib / swap_out_mib | Systemweite kumulative Swap-Zähler; Differenz vor/nach zeigt tatsächlich stattgefundenes Swapping. |
| voice_service_before / voice_service_after | RSS, Swap und kumulative CPU-Zeit des parallel laufenden pi-ptt-Dienstes. |
| pi_before / pi_after | Temperatur, momentane CPU-Frequenz und vcgencmd-Throttling-Flags, soweit lesbar. |

CPU 100 % bedeutet einen ausgelasteten CPU-Kern; bei mehreren parallel
arbeitenden Kernen können Werte über 100 % auftreten. Auf vier Kernen entsprechen
400 % rechnerisch voller CPU-Kapazität. RAM-Werte sind MiB, keine dezimalen MB.

RSS enthält gemeinsam benutzte Bibliotheken und ist kein exklusiver physischer
Speicherverbrauch. Die RSS-Werte verschiedener Prozesse deshalb nicht als exakt
additive RAM-Kosten interpretieren. Der geladene Worker meldet seinen kumulativen
RSS-Höchstwert; dadurch gehört die Spitze nicht zwingend zur zuletzt genannten
Einzelerzeugung. Jede CLI läuft dagegen in einem separaten Mess-Worker mit nur
einem Piper-Kindprozess, sodass dessen RSS-Spitze unabhängig gemessen wird.

Das Modell bleibt nur im letzten Worker zwischen Erzeugungen geladen.
„Frischer Prozess“ bedeutet nicht leere Linux-Dateicaches: Der Test leert keine
Caches und provoziert keinen Reboot. Die Werte bilden wiederholte Ansagen im
normalen laufenden System ab. Die erste resident-Erzeugung kann noch ONNX-interne
Initialisierung auslösen; spätere Erzeugungen getrennt bewerten. Piper kann
leicht unterschiedliche Audio-Dauern erzeugen. Der Ein-Satz-Text vermeidet
abweichende Satzpausen zwischen CLI und PiperVoice-API.

Die Messung umfasst keine Audio-Wiedergabe und keine Latenz bis zum ersten
hörbaren Ton. Sie beantwortet, wie teuer Modellladen/Erzeugung sind und wie
viel RAM das geladene Modell in Ruhe benötigt. Die Erzeugung verwendet
[PiperVoice aus Piper 1.8.0](https://github.com/OHF-Voice/piper1-gpl/blob/v1.8.0/src/piper/voice.py)
mit den Standardparametern; es wird noch keine Produktionsoptimierung aktiviert.
Für den Vergleich neben Vosk muss dessen Modell vorher im Dienst geladen sein;
es führt während dieser Prüfung keine weitere Transkription aus.
Gleichzeitige aktive STT-/TTS-Last wird hier nicht gemessen.

Systemwerte erfassen auch Vosk und andere Programme. Ein Anstieg der gesamten
Swap-Belegung ist nicht automatisch allein Piper zuzuordnen; deshalb betrachten
wir gleichzeitig Worker-RAM, Prozess-Swap und System-Swap-Aktivität. Fehlende
optionale Pi-/Prozess-Metriken sind `null`. Vorher/nachher-Temperatur und Frequenz
sind Momentaufnahmen und keine vollständige Messung von Throttling unter Last.

## Entscheidung nach dem Test

Die beiden Pi-Läufe unten zeigen: Ein geladenes Piper-Modell beschleunigt kurze
Ansagen erheblich, aber neben Vosk entsteht deutlicher Speicherdruck. Auf diesem
Pi Zero 2 W vorerst keinen dauerhaften Piper-Dienst aktivieren. Als nächsten
Schritt feste Statusansagen einmal erzeugen und als WAV abspielen; bei einem
Cache-Treffer ist keine Piper-Synthese nötig. Variable Texte und der Wechsel
zwischen STT/TTS benötigen einen eigenen Praxistest. Der Cache ist noch nicht
implementiert. Lokale Tests validieren Messablauf, Modell-Wiederverwendung,
Fehlerbehandlung und Prozessbereinigung mit einem Stub.

## Erste Pi-Messung vom 5. Oktober 2026

Vom Benutzer auf `pi-assistent` ausgeführt, Messversion `d056897`, Berichtbeginn
20:22:01 UTC (22:22:01 MESZ). Pi Zero 2 W, vier CPUs, Python 3.13.5, Piper 1.8.0,
Kernel 6.18.50+rpt-rpi-v8, Modell `de_DE-thorsten-low.onnx` (63.104.526 Bytes).
Testtext: `Hallo Ivan, ich bin bereit.` Drei frische Prozesse, drei Erzeugungen
mit geladenem Modell, zehn Sekunden Ruhe; keine Wiedergabe.

| Abschnitt | Zeit | CPU in % eines Kerns | RSS |
|---|---:|---:|---:|
| Frischer Prozess 1 | 18,51 s | 119,8 % | Spitze 161,67 MiB |
| Frischer Prozess 2 | 19,83 s | 113,8 % | Spitze 161,76 MiB |
| Frischer Prozess 3 | 18,27 s | 122,3 % | Spitze 161,60 MiB |
| Modell einmal laden | 14,84 s | 110,6 % | aktuell 140,60 MiB |
| Geladenes Modell, Erzeugung 1 | 1,56 s | 386,2 % | aktuell 167,07 MiB |
| Geladenes Modell, Erzeugung 2 | 1,26 s | 391,7 % | aktuell 167,85 MiB |
| Geladenes Modell, Erzeugung 3 | 1,32 s | 391,3 % | aktuell 167,85 MiB |
| Geladenes Modell, zehn Sekunden Ruhe | 10,00 s | 6,8 % | aktuell 167,85 MiB |

Der Median sinkt von 18,51 s auf 1,32 s: rund Faktor 14. Das Laden ist damit
der größte Zeitanteil. Die Audio-Dauer lag bei 1,70–1,73 s für die CLI und
1,63–1,94 s für die API; bei gleichem Text kann die Synthese variieren.
Der RTF sinkt von 10,71–11,58 auf 0,77–0,81. Die API erzeugt diesen kurzen
Satz schneller als seine Wiedergabedauer. Das ist kein Streaming-Latenztest.

| Systemwert | Beobachtung |
|---|---|
| Vom OS gemeldeter Gesamt-RAM | 415,15 MiB |
| Verfügbarer RAM | vorher 270,43 MiB; Minimum während des Tests 135,30 MiB; nachher 265,13 MiB |
| Piper-Worker-Swap | 0 MiB bei allen gemeldeten API-Phasen |
| Gesamte Swap-Belegung | vorher 23,66 MiB; Maximum 23,66 MiB; nachher 23,63 MiB |
| Swap-Aktivität | kein zusätzlicher Swap-out; Swap-in stieg um 0,0234 MiB (24 KiB) |
| Temperatur | vorher 47,24 °C; nachher 52,08 °C |
| Frequenz / Throttling-Snapshots | beide 1000 MHz / `throttled=0x0` |
| PTT-Dienst | gleicher PID 548 und Startzeitwert; RSS etwa 14,81 MiB, RSS-Spitze 23,13 MiB, Swap 8,62 MiB |

**Einschränkung:** Dieser Bericht belegt kein gleichzeitig geladenes Vosk-Modell.
Die niedrige Lebenszeit-RSS-Spitze des PTT-Dienstes spricht dafür, dass seit
seinem Start noch keine Vosk-Transkription erfolgt war. Das ist eine Schlussfolgerung
aus den Prozesswerten; der Bericht enthält keinen direkten Modelllade-Status.
Deshalb sind die 135 MiB freien RAM keine belastbare Reserve für Piper plus Vosk.

Ein geladenes Piper-Modell ist für die Antwortzeit vielversprechend und kostet
in diesem Lauf etwa 168 MiB RSS. Die 6,8 % CPU in der kurzen Ruhephase entsprechen
etwa 1,7 % der vier Kerne; sie sind keine Langzeitmessung des Leerlaufs.
Die folgende Wiederholung prüft den Vergleich nach der Vorbereitung mit Vosk.
Eine permanente TTS-Komponente oder ein WAV-Cache ist weiterhin nicht aktiviert.


## Wiederholung neben Vosk

Gleiche Messversion `d056897`, gleiches Modell und gleiche Parameter, Berichtbeginn
20:31:05 UTC (22:31:05 MESZ) am 5. Oktober 2026. Der zurückgemeldete Bericht
`pi-piper-resources-with-vosk.json` zeigt nun den großen PTT-Speicherverbrauch
vor dem Benchmark: 198,65 MiB RSS, Lebenszeitspitze 226,62 MiB. Derselbe PID 548
und Startzeitwert vor/nach bestätigen, dass der PTT-Dienst nicht neu gestartet wurde.

| Abschnitt | Zeit | CPU in % eines Kerns |
|---|---:|---:|
| Frischer Prozess 1 / 2 / 3 | 27,92 / 21,20 / 20,10 s | 103,7 / 109,6 / 111,3 % |
| Modell einmal laden | 18,01 s | 93,0 % |
| Geladenes Modell, Erzeugung 1 / 2 / 3 | 1,55 / 1,26 / 1,67 s | 334,7 / 390,5 / 357,9 % |
| Geladenes Modell, zehn Sekunden Ruhe | 10,00 s | 7,2 % |

Der Median sinkt von 21,20 auf 1,55 s, rund Faktor 13,7. Die API-RTF liegen
zwischen 0,73 und 0,97. Piper bleibt für den kurzen Satz schnell; das größere
Problem ist jetzt der Speicher.

| Speicherwert | Vorher | Während / nachher |
|---|---:|---:|
| Verfügbarer System-RAM | 103,29 MiB | Minimum 42,73 MiB; nachher 220,68 MiB |
| Gesamte Swap-Belegung | 27,23 MiB | Maximum 284,70 MiB; nachher 221,58 MiB |
| PTT-Dienst RSS | 198,65 MiB | nachher 7,48 MiB |
| PTT-Dienst Swap | 4,48 MiB | nachher 185,80 MiB |
| Piper-Worker RSS | nach Laden 139,39 MiB | letzte Erzeugung 166,93 MiB; Ruhe 160,52 MiB |
| Piper-Worker Swap | nach Laden 0 MiB | letzte Erzeugung 7,07 MiB; Ruhe 13,48 MiB |

Im gesamten Vergleich steigen die systemweiten Swap-Zähler um **318,74 MiB
Swap-out** und **122,13 MiB Swap-in**. Diese Zähler messen bewegte Seiten,
nicht gleichzeitig belegten Swap, und umfassen alle Programme und sämtliche
CLI-/API-Phasen. Der Bericht zeigt nicht, welcher Durchlauf die Auslagerung
ausgelöst hat. Die PTT-Werte belegen aber, dass der weiterlaufende Dienst am Ende
größtenteils ausgelagert ist. Auch Piper besitzt in der Ruhephase Swap-Seiten.

Die nachher freien 221 MiB sind daher keine Reserve für beide Modelle im RAM:
Piper ist beendet und große Teile des PTT-Dienstes liegen im Swap. Beim nächsten
PTT-Einsatz ist erneutes Einlesen ausgelagerter Seiten zu erwarten; die daraus
entstehende STT-Verzögerung wurde hier noch nicht gemessen. RSS verschiedener
Prozesse weiterhin nicht als exakten additiven Speicherbedarf behandeln.

Temperatur 48,85 → 52,62 °C, Frequenz in beiden Snapshots 1000 MHz,
`throttled=0x0` vor/nach. Diese Snapshots zeigen keinen thermischen Engpass.
Die kurze Ruhephase verbraucht 0,717 CPU-Sekunden, entsprechend 7,2 % eines
Kerns bzw. 1,8 % der vier Kerne. Sie ist keine Langzeit-Leerlaufmessung.


## Weiterer Lauf mit überlappender Vosk-Transkription

Berichtbeginn 20:37:37 UTC (22:37:37 MESZ) am 5. Oktober 2026, gleiche
Messversion und Parameter. Der Benutzer meldete A-Bedienung beim Test und
lieferte anschließend das Journal. Die Aufnahme begann bereits kurz vor dem
Benchmark; Vosk verarbeitete sie bei dessen Start noch. Dieser Lauf zählt als
Versuch mit überlappender Verarbeitung, nicht als Wiederholung unter denselben
kontrollierten Bedingungen.

| Dienstereignis (MESZ) | Nachweis |
|---|---|
| 22:37:32 | `recording` |
| 22:37:36 | `capture_ready`, Grund `release`, PCM S16_LE, 48 kHz, Stereo, 174.016 Frames |
| 22:37:36 | `processing` |
| 22:37:37 | Beginn des Ressourcenberichts |
| 22:37:46 | `transcript`, `provider=vosk`, Text: „teste es test test browsers“ |

Die Aufnahme enthält 174.016 / 48.000 = 3,63 s Audio. Zwischen `processing` und
`transcript` liegen laut sekundengenauem Journal ungefähr zehn Sekunden;
etwa neun davon fallen nach den Berichtbeginn. Das bestätigt funktionierende
Aufnahme und erfolgreiche Offline-STT während des Starts der CLI-Vergleichsphase.
Die ersten Hardware-/System-Snapshots gehören zum Benchmarkstart; der genaue
Start des Piper-Kindprozesses steht nicht im Bericht. Die deutlich späteren
residenten Erzeugungen sind deshalb kein Nachweis gleichzeitiger Vosk-/Piper-Synthese.
Die Erkennungsgenauigkeit lässt sich ohne den tatsächlich gesprochenen Satz
nicht beurteilen.

| Messwert | Ergebnis |
|---|---|
| Frische Prozesse | 22,58 / 21,36 / 21,10 s |
| Modell einmal laden | 18,01 s |
| Erzeugungen mit geladenem Modell | 1,60 / 1,21 / 1,26 s |
| Piper in zehn Sekunden Ruhe | 149,56 MiB RSS und 14,63 MiB Swap; CPU 7,7 % eines Kerns |
| PTT-Dienst vor/nach | RSS 45,20 → 4,59 MiB; Swap 155,69 → 185,79 MiB |
| Verfügbarer System-RAM | vorher 200,83 MiB; Minimum 63,62 MiB; nachher 218,71 MiB |
| Gesamter Swap | vorher 182,47 MiB; Maximum 244,94 MiB; nachher 221,55 MiB |
| Swap-Aktivität im gesamten Lauf | 159,79 MiB zusätzlich ausgelagert, 114,36 MiB zurückgelesen |
| PTT-CPU-Zuwachs | 11,31 CPU-Sekunden, gegenüber 4,15 s im vorigen Lauf |
| Temperatur / Throttling-Snapshots | 47,77 → 52,62 °C; beide `throttled=0x0` |

Schon vor diesem Lauf lagen große Teile des PTT-Dienstes im Swap. Der niedrigere
Piper-RSS ist deshalb kein Nachweis einer Speicheroptimierung: auch der neue
Piper-Worker besitzt ausgelagerte Seiten. Das Journal bestätigt diesmal eine
beendete Transkription. Die rund zehn Sekunden sind eine Einzelmessung bei
überlappender Last und bereits vorhandenen Swap-Seiten; den Anteil von
Swapping, CPU-Konkurrenz und Eingabe am Zeitbedarf trennt dieser Versuch nicht.
Die Beobachtung ändert die Empfehlung zu Status-WAVs und gegen eine sofortige
permanente Piper-Komponente nicht. Abbruchverhalten und Gesamtlatenz bei
abwechselndem STT/TTS bleiben separat zu prüfen.

Ein anschließend zurückgemeldeter `free -h`-Snapshot zeigt 217 MiB verfügbaren
RAM und 212 MiB belegten Swap von insgesamt rund 414 MiB. Swap ist damit aktiv
und bleibt nach dem Benchmark belegt. Sein Medium (SD-Datei, Partition oder
zram) ist daraus nicht erkennbar; dafür die Ausgabe von `swapon --show` auslesen.
