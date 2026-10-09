import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class InstallerTests(unittest.TestCase):
    def test_voice_service_installs_every_imported_local_module(self):
        """A module missing from the installer breaks ptt.py at import time."""
        installer = (ROOT / 'scripts' / 'install-voice-service.sh').read_text()
        installed = set(re.findall(r'src/(\w+)\.py', installer))
        local = {path.stem for path in (ROOT / 'src').glob('*.py')}
        pending, needed = ['ptt', 'speak'], set()
        while pending:
            name = pending.pop()
            if name in needed:
                continue
            needed.add(name)
            source = (ROOT / 'src' / f'{name}.py').read_text()
            imports = re.findall(r'^\s*(?:from|import)\s+(\w+)', source, re.MULTILINE)
            pending.extend(module for module in imports if module in local)
        self.assertEqual(needed - installed, set())

    def test_voice_service_keeps_the_display_in_step(self):
        """pi-display imports menu.py etc.; an old display process shifts the menu."""
        installer = (ROOT / 'scripts' / 'install-voice-service.sh').read_text()
        installed = set(re.findall(r'src/(\w+)\.py', installer))
        source = (ROOT / 'src' / 'display.py').read_text()
        local = {path.stem for path in (ROOT / 'src').glob('*.py')}
        imports = set(re.findall(r'^\s*(?:from|import)\s+(\w+)', source, re.MULTILINE)) & local
        self.assertEqual((imports | {'display'}) - installed, set())
        self.assertIn('systemctl restart pi-display.service', installer)


if __name__ == '__main__':
    unittest.main()
