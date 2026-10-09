"""Menu choices that survive a restart (persona, voice effect, lore level).

One small JSON file in the service's state directory (systemd StateDirectory,
/var/lib/pi-ptt). Values are checked by the caller on load; a missing or
broken file just means "use the defaults from the environment". Saving never
raises: losing a menu choice must not take the assistant down, and without
the state directory (tests, an old unit file) nothing is written.
"""
import json
import os
from pathlib import Path

DEFAULT_PATH = '/var/lib/pi-ptt/settings.json'


class Settings:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get('PTT_SETTINGS_FILE', DEFAULT_PATH))

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, **values):
        """Merge ``values`` into the file atomically; True when written."""
        if not self.path.parent.is_dir():
            return False
        data = dict(self.load(), **values)
        tmp = self.path.with_name(self.path.name + '.tmp')
        try:
            tmp.write_text(json.dumps(data, sort_keys=True) + '\n', encoding='utf-8')
            os.replace(tmp, self.path)
        except OSError:
            tmp.unlink(missing_ok=True)
            return False
        return True
