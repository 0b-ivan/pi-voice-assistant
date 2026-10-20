# ADR 0005: Nextcloud über einen MCP-Server, schreibend nur nach Rückfrage

Datum 20.10.2026. Status: vorgeschlagen.

## Kontext

Proximus kennt Termine bisher nur über CalDAV auf dem Pi (Morgenbericht, „Was steht heute an?“, Kalendereintrag bei Fahrten). Fragen wie „Wann hat Luca Geburtstag?“, „Was liegt auf meinem Deck-Board?“ oder „Trag Freitag den Zahnarzt ein“ kann er nicht beantworten. Ein Nextcloud-MCP-Server bietet über 150 Werkzeuge für alle Nextcloud-Apps, darunter Mails senden, Freigaben und Löschen.

## Entscheidung

- Der Server (CT 107) gibt OpenRouter für Nextcloud-Fragen des **erkannten Bedieners** eine feste, kleine Werkzeugliste mit; Lesen läuft sofort, Schreiben (Termin, Aufgabe, Notiz, Einkaufsliste) nur nach gesprochenem „Ja“ derselben Stimme. Alles andere bietet Proximus nie an ([Nextcloud](../nextcloud.md)).
- Eigener, schlanker MCP-Client ohne neue Abhängigkeiten ([`src/nextcloud_mcp.py`](../../src/nextcloud_mcp.py)); die Liste erlaubter Werkzeuge steht im Code, nicht in der Konfiguration.
- Ohne Werkzeuge und ohne Rückschritt, wenn etwas fehlt: MCP-Server oder OpenRouter weg, Gast, Web-Client, LOKAL – dann antwortet Proximus wie bisher.

## Varianten für den MCP-Server

| | Variante | Bewertung |
|---|---|---|
| **A** | Eigene Instanz auf CT 107, nur `127.0.0.1`, eigenes App-Passwort | empfohlen: nichts nach außen offen, unabhängig von Tunnel/Hetzner, App-Passwort einzeln widerrufbar |
| B | Instanz auf dem Hetzner-Server öffentlich (`mcp.obivan.org`) mit Token | Vollzugriff auf das Nextcloud-Konto aus dem Internet erreichbar |
| C | Über den späteren Site-to-Site-Tunnel pfSense ↔ Hetzner | erst, wenn der Tunnel steht |

## Folgen

- OpenRouter sieht Nextcloud-Inhalte, die ein Werkzeug zurückgibt (Termine, Kontakte, Notiztexte), als Text – bisher nur die Frage. Gäste lösen das nie aus.
- Zusätzliche Wartezeit nur bei Nextcloud-Fragen (eine bis drei weitere Modellrunden).
- Ein falsch verstandenes Wort kann höchstens etwas *vorschlagen*; ausgeführt wird nur nach „Ja“ der erkannten Stimme, je Vorschlag einmal.
