"""Short, factual answers about the PiSugar 3 power supply.

Only measurements from the Pi's validated status snapshot are spoken. The PiSugar 3
reports output current but not remaining battery energy; runtime cannot be inferred.

Spoken German without vendor jargon: the PiSugar is "das Akkumodul", output
current is what the Pi draws ("Stromverbrauch"), the watchdog is the
"Absturzwächter". Servitor answers stay terse, Billy talks like a person
(no machine words, see test_billy_phrases).
"""
from system_status import is_billy


def _number(snapshot, key, low, high):
    value = snapshot.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
        return value
    return None


def _volts(mv):
    return f"{mv / 1000:.2f}".replace('.', ',')


def detail(snapshot, style='off'):
    """Description for voice and the energy menu. No cloud/LLM involved."""
    billy = is_billy(style)
    percent = snapshot.get('battery_pct')
    if percent is None:
        return ("Vom Akku kommt gerade nichts an. Schau mal, ob das Akkumodul richtig sitzt."
                if billy else "Keine Akkudaten. Verbindung zum Akkumodul prüfen.")
    plugged, charging = snapshot.get('battery_plugged'), snapshot.get('battery_charging')
    mv = _number(snapshot, 'battery_mv', 2500, 4500)
    current = _number(snapshot, 'output_current_ma', 1, 5000)
    chip = _number(snapshot, 'pisugar_chip_c', -40, 85)
    if billy:
        state = ("Netzteil steckt, ich lade." if plugged and charging else
                 "Netzteil steckt, geladen wird gerade nicht." if plugged else
                 "Ich laufe auf Akku.")
        parts = [f"Akku bei {percent} Prozent.", state]
        if mv is not None:
            parts.append(f"Die Zellspannung liegt bei {_volts(mv)} Volt.")
        if current is not None:
            parts.append(f"Ich ziehe gerade {current} Milliampere.")
        if chip is not None:
            parts.append(f"Das Akkumodul hat {chip} Grad.")
        return ' '.join(parts)
    state = ("Netzbetrieb, Ladung aktiv." if plugged and charging else
             "Netzbetrieb, Ladung pausiert." if plugged else "Akkubetrieb.")
    parts = [f"Energiespeicher {percent} Prozent.", state]
    if mv is not None:
        parts.append(f"Zellspannung {_volts(mv)} Volt.")
    if current is not None:
        parts.append(f"Stromverbrauch {current} Milliampere.")
    if chip is not None:
        parts.append(f"Akkumodul {chip} Grad.")
    return ' '.join(parts)


def current(snapshot, style='off'):
    """Targeted reply: output current is what the Pi draws, not the battery's
    discharge current (the PiSugar does not measure that)."""
    measured = _number(snapshot, 'output_current_ma', 1, 5000)
    if is_billy(style):
        return (f"Ich ziehe gerade {measured} Milliampere." if measured is not None
                else "Meinen Stromverbrauch kann ich gerade nicht messen.")
    return (f"Stromverbrauch {measured} Milliampere." if measured is not None
            else "Stromverbrauch derzeit nicht messbar.")


# Switches of the PiSugar: (snapshot key, servitor on/off, Billy on/off).
SWITCHES = (
    ('pisugar_soft_poweroff',
     ('Ausschalten per Software aktiv.', 'Warnung. Ausschalten per Software ist deaktiviert.'),
     ('Ausschalten per Software geht.', 'Achtung, Ausschalten per Software ist aus.')),
    ('pisugar_charge_protection',
     ('Ladeschutz aktiv.', 'Ladeschutz aus.'),
     ('Der Ladeschutz ist an.', 'Der Ladeschutz ist aus.')),
    ('pisugar_watchdog',
     ('Absturzwächter aktiv.', 'Absturzwächter aus.'),
     ('Der Absturzwächter passt auf.', 'Der Absturzwächter ist aus.')),
    ('pisugar_auto_power_on',
     ('Automatischer Start beim Anstecken des Netzteils.',
      'Kein automatischer Start beim Anstecken des Netzteils.'),
     ('Wenn das Netzteil kommt, starte ich von selbst.',
      'Wenn das Netzteil kommt, starte ich nicht von selbst.')),
    ('pisugar_rtc_alarm',
     ('Weckzeit gestellt.', 'Keine Weckzeit gestellt.'),
     ('Eine Weckzeit ist gestellt.', 'Keine Weckzeit gestellt.')),
)


def config(snapshot, style='off'):
    """Report configured hardware settings; missing data stays explicitly unknown."""
    billy = is_billy(style)
    known = ('pisugar_firmware', 'charge_limit_pct', 'safe_shutdown_pct',
             'safe_shutdown_delay_s', 'pisugar_soft_poweroff', 'pisugar_rtc_alarm',
             'pisugar_charge_protection', 'pisugar_watchdog', 'pisugar_auto_power_on')
    if not any(key in snapshot for key in known):
        return ("Die Einstellungen vom Akkumodul kann ich gerade nicht lesen."
                if billy else "Einstellungen des Akkumoduls nicht lesbar.")
    parts = []
    firmware = snapshot.get('pisugar_firmware')
    if isinstance(firmware, str) and firmware:
        parts.append(f"Akkumodul Version {firmware}.")
    if 'charge_limit_pct' in snapshot:
        low, high = snapshot.get('charge_restart_pct', 0), snapshot['charge_limit_pct']
        parts.append(f"Ich lade zwischen {low} und {high} Prozent." if billy
                     else f"Ladebereich {low} bis {high} Prozent.")
    if 'safe_shutdown_pct' in snapshot:
        delay = snapshot.get('safe_shutdown_delay_s')
        after = f", nach {delay} Sekunden" if delay is not None else ""
        parts.append(f"Bei {snapshot['safe_shutdown_pct']} Prozent schalte ich sicher ab{after}."
                     if billy else
                     f"Sichere Abschaltung bei {snapshot['safe_shutdown_pct']} Prozent{after}.")
    elif 'safe_shutdown_delay_s' in snapshot:
        parts.append(f"Abschaltverzögerung {snapshot['safe_shutdown_delay_s']} Sekunden.")
    for key, servitor, human in SWITCHES:
        value = snapshot.get(key)
        if value in ('on', 'off'):
            parts.append((human if billy else servitor)[0 if value == 'on' else 1])
    return ' '.join(parts)


def runtime(snapshot, style='off'):
    """Never fake a duration from state of charge without measured power draw."""
    billy = is_billy(style)
    if snapshot.get('battery_pct') is None:
        return ("Wie lange der Akku hält, weiß ich gerade nicht. Vom Akku kommen keine Daten."
                if billy else "Restlaufzeit unbekannt. Keine Akkudaten.")
    if billy:
        return ("Wie lange der Akku noch hält, kann ich nicht ehrlich sagen. "
                "Ich sehe den Ladestand, aber nicht, wie viel Energie wirklich noch drin ist.")
    return ("Restlaufzeit nicht zuverlässig bestimmbar. Das Akkumodul meldet den Ladestand, "
            "aber nicht die verbleibende Energie.")
