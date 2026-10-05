# ADR 0003 — Hybrides STT mit OpenRouter und Vosk

## Status

Accepted — 05.10.2026

## Kontext

Der Pi Voice Assistant soll mobil mit PiSugar2 betrieben werden. OpenRouter/Whisper liefert die bessere Online-Erkennung, macht die Spracheingabe aber vollständig von Netzwerk und API-Verfügbarkeit abhängig.

Der Raspberry Pi Zero 2 W besitzt nur begrenzte CPU- und RAM-Ressourcen. Ein lokales LLM ist deshalb nicht Teil des aktuellen MVP. Lokale deutsche Spracherkennung mit einem kleinen Vosk-Modell ist dagegen realistisch und hält die grundlegende Eingabekette auch ohne Internet funktionsfähig.

## Entscheidung

Der STT-Adapter unterstützt:

- `STT_PROVIDER=openrouter`: nur OpenRouter-STT
- `STT_PROVIDER=vosk`: nur lokales Vosk-STT
- `STT_PROVIDER=auto`: OpenRouter zuerst, bei STT-/Netzfehler Vosk als Fallback

`openrouter` bleibt zunächst der Standard, damit bestehende Installationen ihr Verhalten nicht ändern.

Die bereits bestätigte WM8960-Aufnahme bleibt 48 kHz Stereo. Für Vosk wird das WAV intern auf 16 kHz Mono heruntergemischt. So wird der funktionierende ALSA-Aufnahmepfad nicht gleichzeitig mit dem STT-Backend verändert.

Vosk ist eine optionale Installation. Paket und Modell liegen außerhalb des Git-Repositories unter `/opt/pi-voice-assistant`. Das Modell wird erst bei tatsächlicher lokaler Nutzung geladen und danach im Dienstprozess gecacht.

## Konsequenzen

Vorteile:

- PTT + Aufnahme + deutsche STT funktionieren im `vosk`-Modus ohne Internet.
- `auto` kombiniert die bessere Online-Erkennung mit einem lokalen Fallback.
- Bestehender OpenRouter-Pfad bleibt kompatibel.
- Kein zusätzliches Audioformat für den WM8960-Recorder nötig.

Nachteile:

- Vosk benötigt auf dem Zero 2 W zusätzlichen RAM und CPU.
- Der erste lokale Aufruf hat Modell-Ladezeit.
- Vosk erreicht voraussichtlich nicht in allen Situationen die Erkennungsqualität von Whisper.
- Das reale Speicher- und Latenzverhalten muss auf der Zielhardware gemessen werden.

## Nicht Teil dieser Entscheidung

Die eigentliche KI-Antwort bleibt zunächst bei OpenRouter. Lokales LLM, Wake Word und Offline-TTS werden separat bewertet.
