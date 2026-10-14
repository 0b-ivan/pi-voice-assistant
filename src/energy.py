"""Short, factual answers about the PiSugar 3 power supply.

Only measurements from the Pi's validated status snapshot are spoken. The PiSugar 3
reports output current but not remaining battery energy; runtime cannot be inferred.
"""


def detail(snapshot):
    """Description for voice and the energy menu. No cloud/LLM involved."""
    percent = snapshot.get('battery_pct')
    if percent is None:
        return "Energiedaten nicht verfügbar. PiSugar Verbindung prüfen."
    parts = [f"Energiespeicher {percent} Prozent."]
    if snapshot.get('battery_plugged'):
        parts.append("Netzteil angeschlossen.")
        if snapshot.get('battery_charging'):
            parts.append("Laden freigegeben.")
        else:
            parts.append("Laden derzeit nicht aktiv.")
    else:
        parts.append("Akkubetrieb.")
    mv = snapshot.get('battery_mv')
    if isinstance(mv, int) and 2500 <= mv <= 4500:
        parts.append(f"Batteriespannung {mv / 1000:.2f}".replace('.', ',') + " Volt.")
    current = snapshot.get('output_current_ma')
    if isinstance(current, int) and not isinstance(current, bool) and 0 <= current <= 5000:
        parts.append(f"Ausgangsstrom {current} Milliampere.")
    chip = snapshot.get('pisugar_chip_c')
    if isinstance(chip, int) and -40 <= chip <= 85:
        parts.append(f"PiSugar Chiptemperatur {chip} Grad.")
    return ' '.join(parts)


def current(snapshot):
    """Targeted reply: output current is not battery discharge current."""
    measured = snapshot.get('output_current_ma')
    if isinstance(measured, int) and not isinstance(measured, bool) and 0 <= measured <= 5000:
        return f"Ausgangsstrom {measured} Milliampere. Kein gemessener Akkustrom."
    return "Ausgangsstrom derzeit nicht verfügbar."


def config(snapshot):
    """Report configured hardware settings; missing data stays explicitly unknown."""
    known = ('pisugar_firmware', 'charge_limit_pct', 'safe_shutdown_pct',
             'safe_shutdown_delay_s', 'pisugar_soft_poweroff', 'pisugar_rtc_alarm',
             'pisugar_charge_protection', 'pisugar_watchdog', 'pisugar_auto_power_on')
    if not any(key in snapshot for key in known):
        return "PiSugar Konfiguration nicht verfügbar. Power Manager Verbindung prüfen."
    parts = []
    firmware = snapshot.get('pisugar_firmware')
    if isinstance(firmware, str) and firmware:
        parts.append(f"PiSugar Firmware {firmware}.")
    if 'charge_limit_pct' in snapshot:
        parts.append(f"Ladefenster {snapshot.get('charge_restart_pct', 0)} bis "
                     f"{snapshot['charge_limit_pct']} Prozent.")
    if 'safe_shutdown_pct' in snapshot:
        parts.append(f"Power Manager Abschaltschwelle {snapshot['safe_shutdown_pct']} Prozent.")
    if 'safe_shutdown_delay_s' in snapshot:
        parts.append(f"Abschaltverzögerung {snapshot['safe_shutdown_delay_s']} Sekunden.")
    soft = snapshot.get('pisugar_soft_poweroff')
    if soft is not None:
        parts.append('Soft Poweroff aktiv.' if soft == 'on'
                     else 'Warnung. Soft Poweroff ist deaktiviert.')
    charge = snapshot.get('pisugar_charge_protection')
    if charge in ('on', 'off'):
        parts.append('Akku Eingangsschutz aktiv.' if charge == 'on'
                     else 'Akku Eingangsschutz deaktiviert.')
    watchdog = snapshot.get('pisugar_watchdog')
    if watchdog in ('on', 'off'):
        parts.append('Hardware Watchdog aktiv.' if watchdog == 'on'
                     else 'Hardware Watchdog aus.')
    auto_on = snapshot.get('pisugar_auto_power_on')
    if auto_on in ('on', 'off'):
        parts.append('Automatisches Einschalten bei Netzanschluss aktiv.' if auto_on == 'on'
                     else 'Automatisches Einschalten bei Netzanschluss aus.')
    alarm = snapshot.get('pisugar_rtc_alarm')
    if alarm is not None:
        parts.append('RTC Weckalarm aktiv.' if alarm == 'on'
                     else 'RTC Weckalarm aus.')
    return ' '.join(parts)


def runtime(snapshot):
    """Never fake a duration from state of charge without measured power draw."""
    if snapshot.get('battery_pct') is None:
        return "Restlaufzeit unbekannt. Energiedaten nicht verfügbar."
    return ("Restlaufzeit noch nicht zuverlässig bestimmbar. "
            "Der PiSugar misst den Ausgangsstrom, aber nicht die tatsächlich "
            "verbleibende Akkuenergie. Kapazität und Verbrauch müssen "
            "zuerst kalibriert werden.")
