# Nextcloud über MCP

Stand 20.10.2026. Proximus beantwortet Fragen zu Kalender, Kontakten, Deck, Notizen, Einkaufsliste und Dateien des Bedieners über einen [Nextcloud-MCP-Server](https://github.com/cbcoutinho/nextcloud-mcp-server) und legt auf Wunsch Termine, Aufgaben, Notizen und Einkaufsartikel an – schreibend nur nach Rückfrage. Entscheidung und Abwägung: [ADR 0005](decisions/0005-nextcloud-mcp.md).

```text
Bediener: „Was steht nächste Woche im Kalender?“
CT 107: Vosk → Stimme = Bediener → Thema Nextcloud → OpenRouter mit Werkzeugen ⇄ MCP-Server → Antwort → Piper
Bediener: „Trag Freitag 10 Uhr Zahnarzt ein.“ → „Soll ich den Termin „Zahnarzt“ am 24.10. um 10:00 Uhr eintragen? Sag ja oder nein.“
Bediener: „Ja, bitte eintragen.“ → Termin angelegt → „Termin eingetragen.“
```

## Wann die Werkzeuge greifen

Alle Bedingungen müssen erfüllt sein, sonst antwortet Proximus wie bisher ohne Werkzeuge (kein Umweg, keine zusätzliche Wartezeit):

- `SERVITOR_NEXTCLOUD_MCP_URL` ist auf CT 107 gesetzt.
- Die Stimme wurde als **Bediener** erkannt (Gäste, unbekannte Stimmen, Web-Client und getippte Fragen bekommen keine Werkzeuge).
- Die Frage handelt von Kalender, Terminen, Aufgaben, Kontakten, Deck, Notizen, Einkaufsliste oder Dateien (`nextcloud_mcp.wants`); feste Absichten wie „Was steht heute an?“ beantwortet weiterhin das Regelwerk mit den Terminen vom Pi.
- Sprachkern nicht LOKAL, OpenRouter nicht in der Abkühlpause nach einem Fehler. Das lokale Modell bekommt nie Werkzeuge.

Fällt der MCP-Server oder OpenRouter aus, antwortet Proximus wie vor dieser Funktion (Journal: `nextcloud_failed`).

## Erlaubte Werkzeuge

Fest im Code (`READ_TOOLS`, `WRITE_TOOLS` in [`src/nextcloud_mcp.py`](../src/nextcloud_mcp.py)); was der Server nicht anbietet (App nicht installiert), fehlt einfach.

| Art | Werkzeuge |
|---|---|
| Lesen, sofort | Kalender und Termine, freie Zeiten, Aufgaben, Adressbücher, Kontaktsuche (ohne Fotos), Deck-Boards/-Karten, Notizen suchen/lesen, Einkaufslisten, Dateisuche nach Name/Typ, Favoriten |
| Schreiben, nur nach „Ja“ | Termin anlegen, Aufgabe anlegen, Notiz anlegen, Artikel auf die Einkaufsliste |
| Nie | Mail, Freigaben, Löschen, Verschieben, Ändern bestehender Einträge, alles andere |

## Rückfrage

Höchstens eine offene Rückfrage. Sie gehört der Stimme, die gefragt hat, und gilt 90 Sekunden.

- „Ja“, „Bestätigt“, „Ja, bitte eintragen“, „Ja, trag es ein“: ausführen, genau einmal.
- „Nein“, „Abbrechen“: verwerfen.
- Etwas anderes: verwerfen, die neue Frage wird normal beantwortet.
- „Ja“ ohne sicher erkannte Stimme (kurze Wörter liegen oft unter der Schwelle): Rückfrage bleibt offen, Proximus bittet um „Ja, bitte eintragen“. Eine andere erkannte Stimme kann nicht bestätigen.

Das Modell kann nichts selbst ausführen: Der erste schreibende Werkzeugaufruf beendet die Runde und wird zur Rückfrage.

## Einrichten

1. MCP-Server bereitstellen, nur für CT 107 erreichbar (Variante A in ADR 0005: eigene Instanz auf CT 107 an `127.0.0.1:8770`, eigenes Nextcloud-App-Passwort).
2. In `/etc/servitor-voice.env`: `SERVITOR_NEXTCLOUD_MCP_URL=http://127.0.0.1:8770/mcp`, optional `OPENROUTER_TOOLS_MAX_TOKENS` (Standard 400).
3. `systemctl restart servitor-voice.service`; prüfen mit „Was steht nächste Woche im Kalender?“ (Journal ohne `nextcloud_failed`).

Nextcloud-Fragen nutzen immer das eingestellte Standardmodell (`OPENROUTER_LLM_MODEL`, das muss Werkzeugaufrufe beherrschen; `mistralai/mistral-medium-3-5` kann es, geprüft 20.10.2026), auch bei Sprachkern FREI: Das FREI-Modell kennt keine Werkzeuge. OpenRouter sieht dabei Frage und Werkzeugergebnisse (Termine, Kontakte) als Text.

## Systemwache (Uptime Kuma) als Werkzeug

Stand 20.10.2026. Derselbe Werkzeugweg kennt ein zweites, nur lesendes Werkzeug ohne MCP: `kuma_status` ([`src/systemwache.py`](../src/systemwache.py), `StatusTool`). Es liefert dem Modell den Zustand, den die [Systemwache](concepts/proximus-systemwache/KONZEPT.md) auf CT 107 ohnehin jede Minute von den Kuma-Statusseiten holt: je Dienst erreichbar, ausgefallen oder in Wartung und seit wann, Verfügbarkeit der letzten 24 Stunden, Ereignisse bis 7 Tage zurück (Ausfälle, Wiederkehr mit Dauer, Wartung, Zertifikate, Statusseite nicht abrufbar). Kumas Fehlermeldungen und Adressen gibt es nie an das Modell weiter, Dienstnamen nur bereinigt.

```text
„Was ist bei Uptime Kuma los?“             → feste Antwort der Systemwache, wie bisher
„Wie lange war Vault gestern down?“         → Modell + kuma_status
„Hatte Vaultwarden heute Ärger?“            → Modell + kuma_status (Name eines überwachten Dienstes)
```

- **Wer:** erkannter Bediener am Pi **und** der angemeldete Web-Client (Kennwort-Sitzung oder Token, gesprochen oder getippt). Unbekannte Stimmen bekommen weiter „Zugriff verweigert“. Nextcloud-Werkzeuge bleiben beim Pi-Bediener, weil Schreiben ein „Ja“ derselben Stimme braucht.
- **Wann:** Allgemeine Fragen beantwortet die feste Absicht `watch`. Fragt man nach mehr („wann“, „wie lange“, „wie oft“, „gestern“, „Woche“, „Verfügbarkeit“, „Zertifikat“, „zuletzt“ – `systemwache.detail`) oder frei formuliert nach Ausfällen, Störungen oder einem überwachten Dienst (`systemwache.wants`), geht die Frage mit dem Werkzeug an das Modell.
- **Ausfall:** Klappt der Werkzeugweg nicht (OpenRouter weg, Sprachkern LOKAL), kommt die feste Antwort der Systemwache, nie ein geratener Zustand.
- **Voraussetzung:** nur `SERVITOR_KUMA_URL` und `SERVITOR_KUMA_PAGES`; `SERVITOR_NEXTCLOUD_MCP_URL` ist dafür nicht nötig. Sichtbar ist, was auf den eingestellten Statusseiten steht.
