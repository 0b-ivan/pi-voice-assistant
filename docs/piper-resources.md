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
Die JSON-Auswertung bleibt am angegebenen Pfad; bitte zurückmelden, nicht committen.

Ctrl-C beendet den Test und seine Piper-Kindprozesse. Ein Zeitlimit von 180 s
gilt je frischem CLI-Durchlauf bzw. für den gesamten Modell-im-Speicher-Worker;
bei Abbruch bleibt ein Teilbericht mit `error` erhalten. Modell und venv müssen
vorhanden sein. Die Limits lassen sich beispielsweise für einen kürzeren Test
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
Vosk bleibt parallel geladen, führt während dieser Prüfung aber keine weitere
Transkription aus. Gleichzeitige aktive STT-/TTS-Last wird hier nicht gemessen.

Systemwerte erfassen auch Vosk und andere Programme. Ein Anstieg der gesamten
Swap-Belegung ist nicht automatisch allein Piper zuzuordnen; deshalb betrachten
wir gleichzeitig Worker-RAM, Prozess-Swap und System-Swap-Aktivität. Fehlende
optionale Pi-/Prozess-Metriken sind `null`. Vorher/nachher-Temperatur und Frequenz
sind Momentaufnahmen und keine vollständige Messung von Throttling unter Last.

## Entscheidung nach dem Test

Wir beurteilen wiederholte Erzeugungszeit, belegten RAM im Ruhezustand, CPU in
Ruhe und Swap-Aktivität bei gleichzeitig geladenem Vosk. Erst die Pi-Ergebnisse
entscheiden, ob ein dauerhaft geladenes Modell sinnvoll ist. Für feste
Statusansagen bleibt eine vorberechnete WAV-Datei eine weitere Option.
Die Hardwaremessung ist noch offen; lokale Tests validieren nur Messablauf,
Modell-Wiederverwendung, Fehlerbehandlung und Prozessbereinigung mit einem Stub.
