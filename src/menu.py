"""Small control menu on the PiTFT, driven by its two buttons and SHIM E.

GPIO23 (upper PiTFT button) and GPIO24 (lower) open the menu and move the
selection; SHIM E confirms; SHIM B goes one level up and closes at the top.
The top level holds groups (Sprache, Personen, Gerät, System); each group
ends with "Zurück". No hardware or display code in here: VoiceController
owns a Menu and publishes its state for display.py, which shares LABELS.
"""

GROUPS = {
    'voice': ('wake', 'llm', 'lore', 'server', 'back'),
    'people': ('people', 'enroll', 'refine', 'back'),
    'device': ('wlan', 'alarms', 'led', 'screen', 'back'),
    'system': ('info', 'status', 'maintenance', 'back'),
}
TOP = ('voice', 'people', 'device', 'system', 'close')
# Every selectable action (for publishing and tests).
ITEMS = tuple(item for group in GROUPS.values() for item in group if item != 'back') + ('close',)
LABELS = {
    'voice': 'Sprache', 'people': 'Personen', 'device': 'Gerät', 'system': 'System',
    'close': 'Schließen', 'back': 'Zurück',
    'wake': 'Aktivierungswort', 'llm': 'Sprachkern', 'lore': 'Lore-Stufe',
    'server': 'Server nutzen', 'enroll': 'Kennenlernen', 'refine': 'Stimme nachtrainieren',
    'wlan': 'WLAN', 'alarms': 'Alarme', 'led': 'Status-LED', 'screen': 'Display aus',
    'info': 'Systeminfo', 'status': 'Status ansagen', 'maintenance': 'Wartung',
}
# "people" is both a group and the person list inside it.
LABELS_IN_GROUP = {'people': 'Bekannte Personen'}
# Selecting these leaves the menu (another screen or an action takes over).
CLOSING = ('close', 'status', 'screen', 'maintenance', 'enroll', 'refine', 'people')
TIMEOUT_SECONDS = 15.0


def entries(group):
    """Item ids shown at a level (None: top level)."""
    return GROUPS[group] if group else TOP


def label(item, group=None):
    if group and item in LABELS_IN_GROUP:
        return LABELS_IN_GROUP[item]
    return LABELS[item]


class Menu:
    def __init__(self, timeout=TIMEOUT_SECONDS):
        self.timeout = timeout
        self.index = None       # None: closed
        self.group = None       # None: top level
        self.page = None        # 'list' or 'info'
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
        if self.page == 'info':
            self.page = 'list'
        else:
            self.index = (self.index + step) % len(self.items)
        self._touch(now)

    def back(self, now=0.0):
        """B: info page -> list, group -> top level, top level -> closed.
        Returns True when the menu closed."""
        self._touch(now)
        if self.page == 'info':
            self.page = 'list'
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
        if self.page == 'info':
            self.page = 'list'
            return None
        item = self.items[self.index]
        if self.group is None and item in GROUPS:
            self.group, self.index = item, 0
            return None
        if item == 'back':
            self.index, self.group = TOP.index(self.group), None
            return None
        if item == 'info':
            self.page = 'info'
        elif item in CLOSING:
            self.close()
        return item

    def expire(self, now):
        if self.open and now - self.touched_at >= self.timeout:
            self.close()
            return True
        return False
