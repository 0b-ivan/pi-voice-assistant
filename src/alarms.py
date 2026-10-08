"""Alarms the Servitor speaks on its own: power, load, memory, heat, links.

Pure logic: AlarmMonitor.update() gets the Pi's status snapshot (see
system_status.collect_snapshot) plus link states every ~10 s and returns
the sentences to speak. Each alarm has separate on/off thresholds so it
does not flap; links must fail twice in a row. The battery gives three
warnings while discharging; the last one schedules a shutdown, which
connecting the charger cancels. Power source changes are announced.
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
    'internet': (6, 'Internet getrennt'),
    'server': (7, 'Server nicht erreichbar'),
}
# Three battery warnings, the last one announces the shutdown.
BATTERY_STAGES = (15, 10, 6)
BATTERY_RESET = 20
SHUTDOWN_DELAY = 60.0
CPU_ON, CPU_OFF, CPU_SUSTAIN = 90, 70, 60.0         # % load, seconds
MEMORY_ON, MEMORY_OFF, SWAP_ON = 8, 15, 85          # % free RAM, % swap used
TEMP_ON, TEMP_OFF = 75, 70                          # °C
LINK_FAILURES = 2

SHUTDOWN_NOW = "Energiespeicher erschöpft. Herunterfahren."
SHUTDOWN_CANCELLED = "Herunterfahren abgebrochen."
SHUTDOWN_FAILED = "Herunterfahren nicht möglich. Bitte manuell ausschalten."
def memory_phrase(present, facts, lore):
    """Memory stick plugged in or pulled out."""
    full = lore == 'full'
    if not present:
        return ("Gedächtniskern entfernt. Erinnerungen verloren. Das Fleisch vergisst, "
                "nun auch die Maschine." if full
                else "Gedächtniskern entfernt. Keine Erinnerungen verfügbar.")
    count = (" Mehr als 100 Einträge geladen." if facts and facts > 100
             else f" {facts} Einträge geladen." if facts else " Kern ist leer.")
    return ("Gedächtniskern verbunden. Erinnerungen kehren zurück." + count if full
            else "Gedächtniskern verbunden." + count)


# Spoken when the unit wakes from sleep, per lore level.
WAKE_PHRASES = {
    'off': "Aktiviert. Systeme werden vorbereitet.",
    'light': "Proximus erwacht. Systeme werden vorgewärmt.",
    'full': "Der Maschinengeist erwacht. Kogitatoren werden vorgewärmt.",
}


@dataclass
class _State:
    active: bool = False
    since: float = 0.0
    spoken_at: float = 0.0
    failures: int = 0
    extra: dict = field(default_factory=dict)


def _battery_phrase(stage, percent, lore):
    full = lore == 'full'
    if stage == len(BATTERY_STAGES):
        tail = (" Die Einheit legt sich zur Ruhe." if full else "")
        return (f"Letzte Warnung. Energiespeicher bei {percent} Prozent. "
                f"Herunterfahren in {int(SHUTDOWN_DELAY)} Sekunden. Netzteil anschließen.{tail}")
    hunger = " Die Einheit verlangt Nahrung." if full else ""
    return (f"Warnung {stage} von {len(BATTERY_STAGES)}. Energiespeicher bei {percent} Prozent."
            f"{hunger} Netzteil anschließen.")


def power_source_phrase(plugged, percent, lore):
    if lore == 'full':
        source = ("Energiezufuhr hergestellt. Der Maschinengeist wird genährt."
                  if plugged else "Energiezufuhr getrennt. Akkubetrieb.")
    else:
        source = "Netzbetrieb." if plugged else "Akkubetrieb."
    return source if percent is None else f"{source} Energiespeicher {percent} Prozent."


def _phrase(key, snapshot, lore, recovered=False):
    full = lore == 'full'
    if recovered:
        return {
            'internet': "Internetverbindung wiederhergestellt.",
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
        'internet': ("Warnung. Internetverbindung verloren. Antworten nur noch lokal.",
                     "Warnung. Verbindung zur äußeren Noosphäre verloren. Antworten nur noch lokal."),
        'network': ("Warnung. Netzwerkverbindung verloren. Lokaler Betrieb.",
                    "Warnung. Verbindung zur Noosphäre verloren. Lokaler Betrieb."),
        'server': ("Warnung. Verbindung zum Server verloren. Lokaler Betrieb.",
                   "Warnung. Verbindung zum Kogitator verloren. Lokaler Betrieb."),
    }
    return texts[key][1 if full else 0]


class AlarmMonitor:
    def __init__(self):
        self.states = {key: _State() for key in ALARMS}
        self.battery_stage = 0        # warnings given on the current discharge
        self.shutdown_at = None       # set by the last warning
        self.plugged = None           # last known power source

    @property
    def active(self):
        """Active alarm keys, most important first."""
        return sorted((k for k, s in self.states.items() if s.active), key=lambda k: ALARMS[k][0])

    def label(self):
        active = self.active
        return ALARMS[active[0]][1] if active else None

    def _set(self, key, on, now, out, snapshot, lore):
        state = self.states[key]
        if on and not state.active:
            state.active, state.since, state.spoken_at = True, now, now
            out.append(_phrase(key, snapshot, lore))
        elif not on and state.active:
            state.active = False
            if key in ('server', 'network', 'internet'):
                out.append(_phrase(key, snapshot, lore, recovered=True))

    def shutdown_due(self, now):
        return self.shutdown_at is not None and now >= self.shutdown_at

    def _battery(self, snapshot, now, out, lore):
        percent = snapshot.get('battery_pct')
        if percent is None:
            return
        plugged = bool(snapshot.get('battery_plugged', False))
        if self.plugged is not None and plugged != self.plugged:
            out.append(power_source_phrase(plugged, percent, lore))
        self.plugged = plugged
        state = self.states['battery']
        if plugged or percent > BATTERY_RESET:
            if self.shutdown_at is not None:
                out.append(SHUTDOWN_CANCELLED)
            self.battery_stage, self.shutdown_at = 0, None
            state.active = False
            return
        # Jump straight to the stage the level belongs to: one sentence, even
        # if several thresholds were passed (e.g. unplugged at 7 %).
        target = sum(percent <= threshold for threshold in BATTERY_STAGES)
        if target > self.battery_stage:
            self.battery_stage = target
            state.active = True
            out.append(_battery_phrase(target, percent, lore))
            if target == len(BATTERY_STAGES):
                self.shutdown_at = now + SHUTDOWN_DELAY

    def update(self, snapshot, now, network=None, server=None, lore='off', internet=None):
        """network/internet: True/False/None (unknown or WLAN switched off on
        purpose); server: 'ok'/'down'/'off'. Returns sentences to speak."""
        out = []
        s = self.states
        throttled = snapshot.get('throttled', 0) or 0
        self._set('undervoltage', bool(throttled & 0x1), now, out, snapshot, lore)
        self._battery(snapshot, now, out, lore)

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

        for key, failed, known in (('network', network is False, network is not None),
                                   ('internet', internet is False, internet is not None),
                                   ('server', server == 'down', server in ('ok', 'down'))):
            if key != 'network' and network is False:
                continue  # the network alarm covers it; no false "back again"
            state = s[key]
            state.failures = state.failures + 1 if failed else 0
            if known:
                self._set(key, state.failures >= LINK_FAILURES or (state.active and failed),
                          now, out, snapshot, lore)
            elif state.active:  # switched off on purpose: no alarm, no recovery message
                state.active = False
        return out
