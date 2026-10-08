"""Small control menu on the PiTFT, driven by its two buttons and SHIM E.

GPIO23 (upper PiTFT button) and GPIO24 (lower) open the menu and move the
selection; SHIM E confirms; SHIM B closes. No hardware or display code in
here: VoiceController owns a Menu and publishes its state for display.py.
"""

ITEMS = ('info', 'server', 'llm', 'wake', 'lore', 'wlan', 'alarms', 'led', 'screen',
         'enroll', 'maintenance', 'status', 'close')
TIMEOUT_SECONDS = 15.0


class Menu:
    def __init__(self, timeout=TIMEOUT_SECONDS):
        self.timeout = timeout
        self.index = None       # None: closed
        self.page = None        # 'list' or 'info'
        self.touched_at = 0.0

    @property
    def open(self):
        return self.index is not None

    def _touch(self, now):
        self.touched_at = now

    def show(self, now):
        self.index, self.page = 0, 'list'
        self._touch(now)

    def close(self):
        self.index, self.page = None, None

    def move(self, step, now):
        """Upper button step=-1, lower step=+1; an info page returns to the list."""
        if not self.open:
            self.show(now)
            return
        if self.page == 'info':
            self.page = 'list'
        else:
            self.index = (self.index + step) % len(ITEMS)
        self._touch(now)

    def confirm(self, now):
        """Return the selected item id; 'info' turns into a page, 'close' closes."""
        if not self.open:
            return None
        self._touch(now)
        if self.page == 'info':
            self.page = 'list'
            return None
        item = ITEMS[self.index]
        if item == 'info':
            self.page = 'info'
        elif item in ('close', 'status', 'screen', 'maintenance', 'enroll'):
            self.close()
        return item

    def expire(self, now):
        if self.open and now - self.touched_at >= self.timeout:
            self.close()
            return True
        return False
