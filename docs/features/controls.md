# Tasten, Push-to-Talk und Menü

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
- **System:** Systeminfo, Status, [Wartung](maintenance.md).

Der Modus **LOKAL** nutzt ein Offline-LLM nur, wenn **CT 107** erreichbar ist; auf dem Pi selbst läuft kein Offline-LLM. [Persönlichkeit](personality.md) · [Architektur](../architecture.md).

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

## Aktivieren und testen

Einmalig den [SHIM-Einzeltest](../hardware-bring-up.md) durchführen, dann `/etc/pi-ptt.env` konfigurieren:

```ini
PTT_BUTTON_SHIM=1
PTT_PITFT_BUTTONS=23,24
```

```bash
sudo systemctl restart pi-ptt
journalctl -u pi-ptt -n 40 --no-pager
```

`shim_ready` muss Bus 1 und Adresse `0x3f` nennen. Vor manuellen GPIO-/Audio-Tests den Sprachdienst stoppen und danach wieder starten. **Die Abnahme der aktuellen Dienstversion ist noch offen** ([Roadmap](../roadmap.md)); ältere Messergebnisse stehen in [PTT-Abnahme 05.10.](../push-to-talk.md) und [Hardwaretests](../hardware-bring-up.md).
