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
    'dns': (8, 'DNS gestört'),
    'wifi_weak': (9, 'WLAN-Signal schwach'),
    'latency': (10, 'Netz langsam'),
}
# Three battery warnings, the last one announces the shutdown.
BATTERY_STAGES = (15, 10, 6)
BATTERY_RESET = 20
SHUTDOWN_DELAY = 60.0
CPU_ON, CPU_OFF, CPU_SUSTAIN = 90, 70, 60.0         # % load, seconds
MEMORY_ON, MEMORY_OFF, SWAP_ON = 8, 15, 85          # % free RAM, % swap used
TEMP_ON, TEMP_OFF = 75, 70                          # °C
LINK_FAILURES = 2
WIFI_ON, WIFI_OFF = -80, -72                         # dBm
SLOW_ON, SLOW_OFF = 400, 200                         # ms TCP connect to the Internet
NET_SUSTAIN = 60.0                                   # seconds a network problem must last
UPDATE_REMINDER = 24 * 3600.0                        # repeat pending updates at most daily

SHUTDOWN_NOW = "Energiespeicher erschöpft. Herunterfahren."
SHUTDOWN_CANCELLED = "Herunterfahren abgebrochen."
SHUTDOWN_FAILED = "Herunterfahren nicht möglich. Bitte manuell ausschalten."
# Billy (persona "mensch"): the same events in his words, style 'billy' or
# 'billy_full' (see system_status.phrase_style).
BILLY_SHUTDOWN = {
    SHUTDOWN_NOW: "Akku leer. Ich mach jetzt die Augen zu.",
    SHUTDOWN_CANCELLED: "Doch kein Herunterfahren. Danke, Boss.",
    SHUTDOWN_FAILED: "Ich krieg mich nicht runtergefahren. Mach du das bitte von Hand.",
}


def _billy(lore):
    return lore in ('billy', 'billy_full')


def shutdown_text(text, lore='off'):
    """SHUTDOWN_* in the current style."""
    return BILLY_SHUTDOWN.get(text, text) if _billy(lore) else text


def memory_phrase(present, facts, lore):
    """Memory stick plugged in or pulled out."""
    full = lore == 'full'
    if _billy(lore):
        if not present:
            return ("Gedächtnis-Stick ist raus. Alles weg, wie nach dem Umbau."
                    if lore == 'billy_full' else "Gedächtnis-Stick ist raus. Ich vergesse alles.")
        count = (" Über 100 Einträge." if facts and facts > 100
                 else f" {facts} Einträge." if facts else " Noch leer.")
        return "Gedächtnis ist wieder da." + count
    if not present:
        return ("Gedächtniskern entfernt. Erinnerungen verloren. Das Fleisch vergisst, "
                "nun auch die Maschine." if full
                else "Gedächtniskern entfernt. Keine Erinnerungen verfügbar.")
    count = (" Mehr als 100 Einträge geladen." if facts and facts > 100
             else f" {facts} Einträge geladen." if facts else " Kern ist leer.")
    return ("Gedächtniskern verbunden. Erinnerungen kehren zurück." + count if full
            else "Gedächtniskern verbunden." + count)


# Spoken when the unit wakes from sleep, per lore level. Each variant has its
# own prerecorded clips (alarm_audio.known_pieces lists all of them), so the
# chosen text and the played audio always match; a variant without clips is
# synthesized live with exactly that text.
WAKE_PHRASES = {
    'off': ("Aktiviert. Systeme werden vorbereitet.", "Aktiviert. Bereit in wenigen Sekunden."),
    'light': ("Proximus erwacht. Systeme werden vorgewärmt.",
              "Proximus aktiv. Systeme werden vorbereitet."),
    'full': ("Der Maschinengeist erwacht. Kogitatoren werden vorgewärmt.",
             "Proximus erwacht. Die Kogitatoren laufen an."),
    'billy': ("Bin wach. Moment, ich sortier mich.", "Bin da. Gib mir einen Moment.",
              "Wach. Ich komme gleich in Gang."),
    'billy_full': ("Bin wach. Systeme laufen warm.", "Bin da. Gib mir einen Moment.",
                   "Wach. Ich komme gleich in Gang."),
}


def wake_phrase(style):
    """One wake-up line for ``style``, not the same as last time."""
    import variants
    return variants.pick(f'wake.{style}', WAKE_PHRASES.get(style, WAKE_PHRASES['light']))


@dataclass
class _State:
    active: bool = False
    since: float = 0.0
    spoken_at: float = 0.0
    failures: int = 0
    extra: dict = field(default_factory=dict)


def _battery_phrase(stage, percent, lore):
    full = lore == 'full'
    if _billy(lore):
        if stage == len(BATTERY_STAGES):
            return (f"Letzte Warnung. Akku bei {percent} Prozent. In {int(SHUTDOWN_DELAY)} "
                    "Sekunden bin ich weg. Netzteil, schnell.")
        return (f"Warnung {stage} von {len(BATTERY_STAGES)}. Akku bei {percent} Prozent. "
                "Ich brauch Strom, Boss.")
    if stage == len(BATTERY_STAGES):
        tail = (" Die Einheit legt sich zur Ruhe." if full else "")
        return (f"Letzte Warnung. Energiespeicher bei {percent} Prozent. "
                f"Herunterfahren in {int(SHUTDOWN_DELAY)} Sekunden. Netzteil anschließen.{tail}")
    hunger = " Die Einheit verlangt Nahrung." if full else ""
    return (f"Warnung {stage} von {len(BATTERY_STAGES)}. Energiespeicher bei {percent} Prozent."
            f"{hunger} Netzteil anschließen.")


def power_source_phrase(plugged, percent, lore):
    if _billy(lore):
        source = "Strom ist dran. Tut gut." if plugged else "Kein Netzteil mehr, ich lauf auf Akku."
        return source if percent is None else f"{source} Akku bei {percent} Prozent."
    if lore == 'full':
        source = ("Energiezufuhr hergestellt. Der Maschinengeist wird genährt."
                  if plugged else "Energiezufuhr getrennt. Akkubetrieb.")
    else:
        source = "Netzbetrieb." if plugged else "Akkubetrieb."
    return source if percent is None else f"{source} Energiespeicher {percent} Prozent."


BILLY_RECOVERED = {
    'internet': "Internet ist wieder da.",
    'network': "Netz ist wieder da.",
    'dns': "Namensauflösung geht wieder.",
    'wifi_weak': "WLAN ist wieder stabil.",
    'latency': "Netz ist wieder schnell.",
    'server': "Server ist wieder da.",
}


def _billy_alarm(key, snapshot):
    percent, temp = snapshot.get('battery_pct'), snapshot.get('temp_c')
    return {
        'undervoltage': "Alarm. Unterspannung. Das Netzteil schwächelt, schau mal nach.",
        'battery': f"Achtung. Akku bei {percent} Prozent. Ich brauch Strom, Boss.",
        'memory': "Alarm. Mein Arbeitsspeicher ist fast voll. Gleich wird's eng.",
        'temperature': f"Alarm. Mir ist heiß, {temp} Grad im Kern.",
        'cpu': f"Warnung. Ich schufte seit über einer Minute mit {snapshot.get('load_pct')} "
               "Prozent Last.",
        'internet': "Warnung. Internet ist weg. Ich mach lokal weiter.",
        'network': "Warnung. Netz ist weg. Ich mach lokal weiter.",
        'server': "Warnung. Der Server antwortet nicht. Ich mach allein weiter.",
        'dns': "Warnung. Namensauflösung klemmt. Internet geht nur halb.",
        'wifi_weak': "Warnung. WLAN ist schwach.",
        'latency': "Warnung. Netz ist langsam, Antworten dauern.",
    }[key]


def _phrase(key, snapshot, lore, recovered=False):
    full = lore == 'full'
    if _billy(lore):
        return BILLY_RECOVERED[key] if recovered else _billy_alarm(key, snapshot)
    if recovered:
        return {
            'internet': "Internetverbindung wiederhergestellt.",
            'network': "Netzwerkverbindung wiederhergestellt.",
            'dns': "Namensauflösung wiederhergestellt.",
            'wifi_weak': "WLAN-Signal wieder stabil.",
            'latency': "Netzwerk wieder schnell.",
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
        'dns': ("Warnung. Namensauflösung gestört. Internetdienste eingeschränkt.",
                "Warnung. Die Namen der Noosphäre lösen sich nicht auf. Internetdienste "
                "eingeschränkt."),
        'wifi_weak': ("Warnung. WLAN-Signal schwach.",
                      "Warnung. Das Signal der Noosphäre ist schwach."),
        'latency': ("Warnung. Netzwerk langsam. Antworten verzögert.",
                    "Warnung. Die Noosphäre ist träge. Antworten verzögert."),
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
            if key in ('server', 'network', 'internet', 'dns', 'wifi_weak', 'latency'):
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
                out.append(shutdown_text(SHUTDOWN_CANCELLED, lore))
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

    def _sustained(self, key, bad, good, now):
        """True once ``bad`` held for NET_SUSTAIN; stays on until ``good``."""
        state = self.states[key]
        if bad:
            state.extra.setdefault('since', now)
        elif good or not state.active:
            state.extra.pop('since', None)
        if state.active:
            return not good
        return bad and now - state.extra.get('since', now) >= NET_SUSTAIN

    def _network_quality(self, snapshot, now, out, lore, links):
        """WLAN signal, latency and DNS from the network watch (Pi only)."""
        if not links:  # WLAN off, reconnecting or no network: other alarms speak
            for key in ('dns', 'wifi_weak', 'latency'):
                self.states[key].active = False
                self.states[key].extra.clear()
            return
        dbm = snapshot.get('wifi_dbm')
        if dbm is not None:
            self._set('wifi_weak', self._sustained('wifi_weak', dbm <= WIFI_ON, dbm > WIFI_OFF, now),
                      now, out, snapshot, lore)
        latency = snapshot.get('net_ms')
        if latency is not None:
            self._set('latency', self._sustained('latency', latency >= SLOW_ON, latency < SLOW_OFF,
                                                 now), now, out, snapshot, lore)
        dns = snapshot.get('dns')
        if dns is not None:
            self._set('dns', self._sustained('dns', dns == 'fail', dns == 'ok', now),
                      now, out, snapshot, lore)

    def updates_notice(self, snapshot, now, lore='off'):
        """Sentence when new updates appear, else a reminder once a day."""
        from system_status import updates_sentence
        sentence = updates_sentence(snapshot)
        key = (snapshot.get('updates', 0) + snapshot.get('server_updates', 0),
               snapshot.get('updates_security', 0) + snapshot.get('server_updates_security', 0))
        store = getattr(self, 'notice_store', None)
        if not hasattr(self, '_updates'):
            # What was announced before a service restart (``now`` is wall time then).
            self._updates = (store.load() if store else None) or ((0, 0), None)
        last_key, last_at = self._updates
        if sentence is None:
            self._updates = (key, None)
            return None
        more = key[0] > last_key[0] or key[1] > last_key[1]
        if not more and last_at is not None and 0 <= now - last_at < UPDATE_REMINDER:
            return None
        self._updates = (key, now)
        if store:
            store.save(key, now)
        if lore == 'full':
            return sentence + " Die Riten der Wartung sind fällig."
        if lore == 'billy_full':
            return sentence + " Soll sich ein Techpriester drum kümmern."
        return sentence

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

        self._network_quality(snapshot, now, out, lore, links=network is True)
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
