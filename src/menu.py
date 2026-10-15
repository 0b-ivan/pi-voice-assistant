"""Small control menu on the PiTFT, driven by its two buttons and SHIM E.

GPIO23 (upper PiTFT button) and GPIO24 (lower) open the menu and move the
selection; SHIM E confirms; SHIM B goes one level up and closes at the top.
The top level holds groups (Persönlichkeit, Sprache, Netzwerk, Bluetooth,
Energie, Personen, System); each group ends with "Zurück". No hardware or display code in here: VoiceController
owns a Menu and publishes its state for display.py, which shares LABELS.
"""

GROUPS = {
    'persona': ('human', 'persona', 'voice_fx', 'lore', 'emotions', 'back'),
    'voice': ('wake', 'llm', 'server', 'cue', 'back'),
    'network': ('net_info', 'wifi_scan', 'wifi_saved', 'hotspot', 'wlan', 'back'),
    'hotspot': ('wifi_hotspot_start', 'wifi_hotspot_show',
                'wifi_hotspot_clients', 'wifi_hotspot_rotate',
                'wifi_hotspot_stop', 'back'),
    'audio': ('bt_speaker', 'bt_connect', 'bt_scan', 'bt_forget', 'back'),
    'energy': ('energy_status', 'cpu_mode', 'power_notices', 'energy_config',
               'energy_shutdown', 'back'),
    'people': ('people', 'enroll', 'refine', 'facts', 'wake_rec', 'back'),
    # Formerly a separate "Gerät" group: display, LED and alarms belong to
    # the unit's own settings, next to its information and maintenance.
    'system': ('info', 'status', 'screen', 'led', 'alarms', 'maintenance', 'back'),
}
# Most used first; eight rows fit the screen without scrolling.
TOP = ('persona', 'voice', 'network', 'audio', 'energy', 'people', 'system', 'close')
# A single nested sub-menu keeps AP functions separate from normal Wi-Fi.
PARENTS = {'hotspot': 'network'}
# Every selectable action (for publishing and tests).
ITEMS = tuple(item for group in GROUPS.values() for item in group if item != 'back') + ('close',)
LABELS = {
    'voice': 'Sprache', 'persona': 'Persönlichkeit', 'people': 'Personen', 'audio': 'Bluetooth',
    'network': 'Netzwerk', 'hotspot': 'Access Point', 'energy': 'Energie',
    'system': 'System', 'bt_speaker': 'BT-Lautsprecher', 'bt_scan': 'Lautsprecher suchen',
    'bt_forget': 'Lautsprecher entfernen', 'bt_connect': 'Lautsprecher verbinden',
    'close': 'Schließen', 'back': 'Zurück',
    'wake': 'Aktivierungswort', 'llm': 'Sprachkern', 'lore': 'Lore-Stufe',
    'server': 'Server nutzen', 'cue': 'Quittungston', 'human': 'Menschlich',
    'emotions': 'Gefühle',
    'voice_fx': 'Stimmeffekt', 'enroll': 'Stimme kennenlernen', 'refine': 'Stimme ergänzen',
    'facts': 'Fakten kennenlernen', 'wake_rec': 'Wakeword aufnehmen',
    'wlan': 'WLAN', 'net_info': 'Netzwerkinfos', 'wifi_scan': 'WLAN suchen',
    'wifi_saved': 'Gespeicherte WLANs',
    'wifi_hotspot_start': 'AP starten', 'wifi_hotspot_show': 'Passwort anzeigen',
    'wifi_hotspot_rotate': 'Passwort erneuern', 'wifi_hotspot_stop': 'AP beenden',
    'wifi_hotspot_clients': 'Verbundene Geräte',
    'alarms': 'Alarme', 'led': 'Status-LED', 'screen': 'Display aus',
    'info': 'Systeminfo', 'status': 'Status ansagen', 'maintenance': 'Wartung',
    'energy_status': 'Akku & Strom', 'energy_config': 'Akku-Einstellungen',
    'power_notices': 'Netzteil-Ansagen', 'cpu_mode': 'CPU-Leistung',
    'energy_shutdown': 'Ausschalten',
}
# One line under the list for the selected row: what it does (or what E does).
HINTS = {
    'persona': 'Servitor/Billy, Stimme, Lore', 'voice': 'Weckwort, Sprachkern, Server',
    'network': 'WLAN, Access Point, Netzinfos', 'audio': 'Bluetooth-Lautsprecher',
    'energy': 'Akku, CPU-Leistung, Ausschalten', 'people': 'Stimmen und Fakten lernen',
    'system': 'Infos, Display, LED, Wartung', 'close': 'Menü schließen',
    'hotspot': 'Eigenes WLAN für die Einrichtung',
    'human': 'Billy mit natürlicher Stimme', 'persona_item': 'Sprechstil Servitor / Billy',
    'voice_fx': 'Maschinen- oder Menschenstimme', 'lore': 'Wie viel Warhammer',
    'emotions': 'Gefühle zeigen', 'wake': 'Auf das Weckwort hören',
    'llm': 'AUTO, FREI oder nur LOKAL', 'server': 'Server oder nur der Pi',
    'cue': 'Ton nach dem Weckwort', 'wlan': 'Ohne WLAN kein SSH!',
    'net_info': 'IP, Signal, Verbindung', 'wifi_scan': 'Netze in der Nähe',
    'energy_status': 'Akku- und CPU-Werte zeigen', 'cpu_mode': 'E: ECO › NORMAL › POWER',
    'power_notices': 'Ansage bei Netzteil an/ab', 'energy_config': 'Akkumodul ansagen',
    'energy_shutdown': 'Fragt nach, E bestätigt', 'screen': 'Eine Taste weckt es',
    'led': 'Licht an der Tastenleiste', 'alarms': 'Warnungen ansagen',
    'info': 'Version, IP, Speicher', 'status': 'Lagebericht vorlesen',
    'maintenance': 'Updates und Neustarts', 'back': 'Eine Ebene zurück',
}
# Actions that end the unit's work: shown in warning colour.
DANGER = ('energy_shutdown',)
# Rows that open a page on the display instead of acting.
PAGES = {'info': 'info', 'energy_status': 'energy'}
# "people" is both a group and the person list inside it, "persona" both a
# group and the speaking style switch inside it.
LABELS_IN_GROUP = {'people': 'Bekannte Personen', 'persona': 'Sprechstil'}
# Selecting these leaves the menu (another screen or an action takes over).
CLOSING = ('close', 'status', 'screen', 'maintenance', 'energy_config', 'energy_shutdown',
           'enroll', 'refine', 'facts', 'wake_rec',
           'people', 'bt_scan',
           'bt_forget', 'bt_connect', 'wifi_scan', 'wifi_saved', 'net_info',
           'wifi_hotspot_start', 'wifi_hotspot_show',
           'wifi_hotspot_rotate', 'wifi_hotspot_stop',
           'wifi_hotspot_clients')
TIMEOUT_SECONDS = 15.0


def entries(group):
    """Item ids shown at a level (None: top level)."""
    return GROUPS[group] if group else TOP


def label(item, group=None):
    if group and item in LABELS_IN_GROUP:
        return LABELS_IN_GROUP[item]
    return LABELS[item]


def hint(item, group=None):
    """Footer line for the selected row, or None."""
    if group and item == 'persona':
        return HINTS['persona_item']
    return HINTS.get(item)


def is_group(item, group=None):
    """True for a row that opens a sub-menu at this level."""
    return item in GROUPS and (group is None or PARENTS.get(item) == group)


class Menu:
    def __init__(self, timeout=TIMEOUT_SECONDS):
        self.timeout = timeout
        self.index = None       # None: closed
        self.group = None       # None: top level
        self.page = None        # 'list' or a page of PAGES
        self.touched_at = 0.0

    @property
    def open(self):
        return self.index is not None

    @property
    def items(self):
        return entries(self.group)

    def _touch(self, now):
        self.touched_at = now

    def show(self, now):
        self.index, self.group, self.page = 0, None, 'list'
        self._touch(now)

    def close(self):
        self.index, self.group, self.page = None, None, None

    def select(self, item, now=0.0):
        """Point the cursor at ``item`` (opening its group), e.g. for tests."""
        self.page = 'list'
        self._touch(now)
        for group, members in GROUPS.items():
            if item in members:
                self.group, self.index = group, members.index(item)
                return
        self.group, self.index = None, TOP.index(item)

    def move(self, step, now):
        """Upper button step=-1, lower step=+1; an info page returns to the list."""
        if not self.open:
            self.show(now)
            return
        if self.page != 'list':
            self.page = 'list'
        else:
            self.index = (self.index + step) % len(self.items)
        self._touch(now)

    def back(self, now=0.0):
        """B: page -> list, sub-menu -> parent, group -> top, top -> closed."""
        self._touch(now)
        if self.page != 'list':
            self.page = 'list'
        elif self.group in PARENTS:
            parent = PARENTS[self.group]
            self.group, self.index = parent, GROUPS[parent].index(self.group)
        elif self.group is not None:
            self.index, self.group = TOP.index(self.group), None
        else:
            self.close()
            return True
        return False

    def confirm(self, now):
        """Return the selected action id, or None for navigation."""
        if not self.open:
            return None
        self._touch(now)
        if self.page != 'list':
            self.page = 'list'
            return None
        item = self.items[self.index]
        if is_group(item, self.group):
            self.group, self.index = item, 0
            return None
        if item == 'back':
            self.back(now)
            return None
        if item in PAGES:
            self.page = PAGES[item]
        elif item in CLOSING:
            self.close()
        return item

    def expire(self, now):
        if self.open and now - self.touched_at >= self.timeout:
            self.close()
            return True
        return False
