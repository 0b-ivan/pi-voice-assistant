# Pi Voice Assistant · SERVITOR

**Deutscher Sprachassistent auf einem Raspberry Pi Zero 2 W** mit WM8960-Audio, Button SHIM, Adafruit mini PiTFT (240 × 240) und PiSugar 3. Er spricht als **Servitor Proximus** oder **Billy** – mit wählbarer Lore und Stimmeffekt.

| Hardware-Aufbau | Status auf dem PiTFT |
|:--:|:--:|
| <img src="docs/images/hardware-stack-side-2026-10-05.jpg" width="320" alt="Pi Zero 2 W mit Audio- und Erweiterungsplatinen"> | <img src="docs/images/display-status-preview.png" width="320" alt="Mit dem Display-Code erzeugte Statusvorschauen"> |

*Links: echtes Projektfoto. Rechts: gerenderte Software-Vorschau, kein Foto des Displays.*

## So funktioniert es

```text
Taste halten / „Hey Jarvis“ → Pi: Audio, Tasten, Display
  → CT 107: Vosk → direkte Antwort / OpenRouter / lokales Qwen3-4B
  → Piper + Servitor-DSP → Pi → WM8960-Lautsprecher
```

**Funktionen:** PTT und Wake Word, Display-Menü, Wetter (Fünf-Tage-Vorhersage), Morgenbericht, Nextcloud-CalDAV-Termine, Gerätesteuerung, Alarme, Gedächtnis auf USB-Stick, mehrere Stimmprofile und Servitor/Billy-Persönlichkeit. Die [Roadmap](docs/roadmap.md) trennt implementierte Funktionen von **noch offenen Hardware-Abnahmen**.

**Offline-Grenzen:** Ohne Internet, aber mit CT 107, beantwortet Qwen3-4B freie Fragen lokal im Homelab. Ohne CT 107 nutzt der Pi Vosk, Piper und direkte Antworten; freie Fragen benötigen dann OpenRouter und Internet. **Auf dem Pi Zero 2 W läuft kein Offline-LLM.**

![Animierte Fünf-Tage-Wettervorschau](docs/images/display-weather.gif)

*Animation aus dem Display-Code; die Abnahme am echten PiTFT steht teilweise noch aus.*

## Dokumentation

- [Hardware und Installation](docs/setup.md): Teile, Pinbelegung, Pi/Audio/Display, Stick und CT 107.
- [Benutzerhandbuch](docs/user-guide.md): Tasten, Menü, Sprache, Persönlichkeit und Personen.
- [Betriebsleitfaden](docs/operation.md): Konfiguration, Updates, Wartung und Fehlersuche.
- [Architektur und Sicherheitsgrenzen](docs/architecture.md): Server, Fallbacks, SPX/1 und Git-Historie.
- [Roadmap](docs/roadmap.md): offene Arbeiten und Geräteabnahmen.

Auf einem eingerichteten Pi: `systemctl status pi-ptt pi-display --no-pager`; Logs: `journalctl -u pi-ptt -n 40 --no-pager`. **Keine Tokens/App-Passwörter ins Repository.** LAN-HTTP überträgt Token und Audio unverschlüsselt; Cloudflare nutzt HTTPS.

Stand 10.10.2026: [PR #79](https://github.com/0b-ivan/pi-voice-assistant/pull/79) (Logs/Selbsttest) ist noch nicht integriert.
