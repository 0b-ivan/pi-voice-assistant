"""The Pi's own LEDs (the green ACT LED): off while Proximus sleeps.

Writing /sys/class/leds/ACT needs the udev rule deploy/91-pi-voice-leds.rules
(group gpio may write brightness and trigger). Without it nothing changes and
the controller logs one event.
"""
from pathlib import Path

LEDS = ('ACT',)


class BoardLeds:
    def __init__(self, base=Path('/sys/class/leds'), names=LEDS):
        self.base, self.names = Path(base), names
        self.saved = {}            # name -> trigger before switching off

    def _trigger(self, name):
        text = (self.base / name / 'trigger').read_text()
        start, end = text.find('['), text.find(']')
        return text[start + 1:end] if 0 <= start < end else None

    def off(self):
        """Switch the LEDs off; returns an error message or None."""
        for name in self.names:
            if not (self.base / name).is_dir():
                continue
            try:
                self.saved.setdefault(name, self._trigger(name))
                (self.base / name / 'trigger').write_text('none')
                (self.base / name / 'brightness').write_text('0')
            except OSError as exc:
                return str(exc)
        return None

    def restore(self):
        for name, trigger in list(self.saved.items()):
            try:
                (self.base / name / 'trigger').write_text(trigger or 'default-on')
            except OSError:
                pass
            del self.saved[name]
