# ADR 0003: Wählbare lokale und Online-STT

Datum 05.10.2026. Status: angenommen, Vosk hardwareseitig bestätigt; reale auto-Abnahme offen.

Der Adapter unterstützt `vosk` (nur lokal), `openrouter` (nur online) und `auto` (OpenRouter zuerst, Vosk bei Fehler). Code-/Vorlagenstandard bleibt `openrouter`, weil der normale Installer Vosk nicht automatisch bereitstellt. **Der tatsächliche Pi-Betrieb nutzt ausdrücklich `vosk`.**

Aufnahme bleibt beim bestätigten 48-kHz-Stereoformat. Vosk erhält intern 16-kHz-Mono, Modell wird bei Bedarf geladen und im Dienst wiederverwendet. Das kleine deutsche Modell ist auf dem Pi funktionsfähig, benötigt aber merkliche Zeit/RAM und erkennt freie Sprache schwächer als der getestete Online-Pfad. [Messwerte und Setup](../speech-to-text.md).

`auto` ist Online-First, kann vor Fallback warten und lädt Vosk erstmals beim Ausfall. Es ist kein Offline-Modus. Die praktische Netz-/API-Ausfallprüfung wird zurückgestellt, solange der Nutzer lokal bleiben möchte.

LLM und TTS sind getrennte Entscheidungen. Lokale TTS ist auf dem Pi inzwischen getestet; damit ist die frühere Annahme, Offline-TTS sei grundsätzlich erst zu evaluieren, als Funktionsfrage überholt. Ressourcenoptimierung bleibt offen.
