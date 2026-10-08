"""Alarms the Servitor speaks on its own: power, load, memory, heat, links.

Pure logic: AlarmMonitor.update() gets the Pi's status snapshot (see
system_status.collect_snapshot) plus link states every ~10 s and returns
the sentences to speak. Each alarm has separate on/off thresholds so it
does not flap; links must fail twice in a row; a critical battery repeats.
"""
from dataclasses import dataclass, field

# Display label and priority (lower = more important) per alarm.
ALARMS = {
    'undervoltage': (0, 'ALARM: Unterspannung'),
    'battery': (1, 'ALARM: Akku fast leer'),
    'memory': (2, 'ALARM: Speicher knapp'),
    'temperature': (3, 'ALARM: Temperatur'),
    'cpu': (4, 'ALARM: CPU-Last hoch'),
    'network': (5, 'Netzwerk getrennt'),
    'server': (6, 'Server nicht erreichbar'),
}
BATTERY_ON, BATTERY_OFF, BATTERY_CRITICAL = 15, 20, 7
CPU_ON, CPU_OFF, CPU_SUSTAIN = 90, 70, 60.0         # % load, seconds
MEMORY_ON, MEMORY_OFF, SWAP_ON = 8, 15, 85          # % free RAM, % swap used
TEMP_ON, TEMP_OFF = 75, 70                          # °C
LINK_FAILURES = 2
REPEAT_CRITICAL = 300.0


@dataclass
class _State:
    active: bool = False
    since: float = 0.0
    spoken_at: float = 0.0
    failures: int = 0
    extra: dict = field(default_factory=dict)


def _phrase(key, snapshot, lore, recovered=False):
    full = lore == 'full'
    if recovered:
        return {
            'network': "Netzwerkverbindung wiederhergestellt.",
            'server': ("Verbindung zum Kogitator wiederhergestellt. Lob dem Omnissiah."
                       if full else "Server wieder erreichbar."),
        }[key]
    percent = snapshot.get('battery_pct')
    texts = {
        'undervoltage': ("Alarm. Unterspannung. Netzteil prüfen.",
                         "Alarm. Unterspannung. Der Maschinengeist hungert. Netzteil prüfen."),
        'battery': (f"Warnung. Energiespeicher bei {percent} Prozent. Netzteil anschließen.",
                    f"Alarm. Energiespeicher bei {percent} Prozent. "
                    "Die Einheit verlangt Nahrung. Netzteil anschließen."),
        'memory': ("Alarm. Arbeitsspeicher fast erschöpft. Systemstabilität gefährdet.",
                   "Alarm. Arbeitsspeicher fast erschöpft. Makel am Maschinengeist droht."),
        'temperature': (f"Alarm. Kerntemperatur {snapshot.get('temp_c')} Grad.",
                        f"Alarm. Kerntemperatur {snapshot.get('temp_c')} Grad. "
                        "Kühlungsritus erforderlich."),
        'cpu': (f"Warnung. Systemlast {snapshot.get('load_pct')} Prozent seit über einer Minute.",
                f"Warnung. Systemlast {snapshot.get('load_pct')} Prozent. "
                "Der Kogitator ist überlastet."),
        'network': ("Warnung. Netzwerkverbindung verloren. Lokaler Betrieb.",
                    "Warnung. Verbindung zur Noosphäre verloren. Lokaler Betrieb."),
        'server': ("Warnung. Verbindung zum Server verloren. Lokaler Betrieb.",
                   "Warnung. Verbindung zum Kogitator verloren. Lokaler Betrieb."),
    }
    return texts[key][1 if full else 0]


class AlarmMonitor:
    def __init__(self):
        self.states = {key: _State() for key in ALARMS}

    @property
    def active(self):
        """Active alarm keys, most important first."""
        return sorted((k for k, s in self.states.items() if s.active), key=lambda k: ALARMS[k][0])

    def label(self):
        active = self.active
        return ALARMS[active[0]][1] if active else None

    def _set(self, key, on, now, out, snapshot, lore, repeat=False):
        state = self.states[key]
        if on and not state.active:
            state.active, state.since, state.spoken_at = True, now, now
            out.append(_phrase(key, snapshot, lore))
        elif on and repeat and now - state.spoken_at >= REPEAT_CRITICAL:
            state.spoken_at = now
            out.append(_phrase(key, snapshot, lore))
        elif not on and state.active:
            state.active = False
            if key in ('server', 'network'):
                out.append(_phrase(key, snapshot, lore, recovered=True))

    def update(self, snapshot, now, network=None, server=None, lore='off'):
        """network: True/False/None (unknown or WLAN switched off on purpose);
        server: 'ok'/'down'/'off'. Returns sentences to speak, in order."""
        out = []
        s = self.states
        throttled = snapshot.get('throttled', 0) or 0
        self._set('undervoltage', bool(throttled & 0x1), now, out, snapshot, lore)

        percent = snapshot.get('battery_pct')
        plugged = snapshot.get('battery_plugged', False)
        if percent is not None:
            on = not plugged and (percent <= BATTERY_ON or (s['battery'].active and percent < BATTERY_OFF))
            self._set('battery', on, now, out, snapshot, lore,
                      repeat=on and percent <= BATTERY_CRITICAL)

        free = snapshot.get('mem_free_pct')
        swap = snapshot.get('swap_used_pct', 0) or 0
        if free is not None:
            on = free <= MEMORY_ON or swap >= SWAP_ON or (
                s['memory'].active and free < MEMORY_OFF)
            self._set('memory', on, now, out, snapshot, lore)

        temp = snapshot.get('temp_c')
        if temp is not None:
            on = temp >= TEMP_ON or (s['temperature'].active and temp > TEMP_OFF)
            self._set('temperature', on, now, out, snapshot, lore)

        load = snapshot.get('load_pct')
        if load is not None:
            cpu = s['cpu']
            if load >= CPU_ON:
                cpu.extra.setdefault('high_since', now)
            elif load < CPU_OFF:
                cpu.extra.pop('high_since', None)
            sustained = now - cpu.extra.get('high_since', now) >= CPU_SUSTAIN
            on = (load >= CPU_ON and sustained) or (cpu.active and load >= CPU_OFF)
            self._set('cpu', on, now, out, snapshot, lore)

        for key, failed in (('network', network is False),
                            ('server', server == 'down')):
            if key == 'server' and network is False:
                continue  # the network alarm covers it; no false "server back"
            state = s[key]
            state.failures = state.failures + 1 if failed else 0
            known = network is not None if key == 'network' else server in ('ok', 'down')
            if known:
                self._set(key, state.failures >= LINK_FAILURES or (state.active and failed),
                          now, out, snapshot, lore)
            elif state.active:  # switched off on purpose: no alarm, no recovery message
                state.active = False
        return out
