import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class InstallerTests(unittest.TestCase):
    def test_aux_setup_runs_before_voice_stop_and_is_installed_for_manual_use(self):
        installer = (ROOT / 'scripts' / 'install-voice-service.sh').read_text()
        helper = '/usr/bin/python3 "${repo_root}/scripts/configure-wm8960.py" --if-present'
        self.assertLess(installer.index(helper), installer.index('systemctl stop pi-ptt.service'))
        self.assertIn('/opt/pi-voice-assistant/scripts/configure-wm8960.py', installer)
        self.assertIn('python3-qrcode iw alsa-utils', installer)

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


    def test_every_install_copies_one_file_to_one_target(self):
        """A stray continuation line turns the last target into a directory
        and aborts the installer half-way (seen on the Pi)."""
        import shlex
        for script in ('install-voice-service.sh', 'install-display.sh'):
            text = (ROOT / 'scripts' / script).read_text().replace('\\\n', ' ')
            for line in text.splitlines():
                words = shlex.split(line, comments=True)
                if not words or words[0] != 'install':
                    continue
                args, skip = [], False
                for word in words[1:]:
                    if skip:
                        skip = False
                    elif word in ('-m', '-o', '-g'):
                        skip = True
                    elif word == '-d':
                        break
                    elif not word.startswith('-'):
                        args.append(word)
                else:
                    self.assertEqual(len(args), 2, f'{script}: {line.strip()}')

    def test_hotspot_deploy_checks_running_ap_and_qr_dependency(self):
        installer = (ROOT / 'scripts' / 'install-voice-service.sh').read_text()
        self.assertIn('systemctl is-active --quiet proximus-setup.service', installer)
        self.assertIn('sudo systemctl stop proximus-setup.service', installer)
        self.assertIn("import qrcode", installer)
        self.assertIn("python3-qrcode", installer)
        display_setup = (ROOT / 'scripts' / 'install-display.sh').read_text()
        self.assertIn("python3-qrcode", display_setup)
        self.assertIn('install -m 0644 "${repo_root}/src/display.py"', installer)


    def test_private_repository_installer_uses_pinned_local_git_archive(self):
        script_path = ROOT / 'deploy' / 'pi-voice-install'
        subprocess.run(['/bin/sh', '-n', str(script_path)], check=True)
        src = script_path.read_text()
        self.assertIn('cat-file -e "$commit^{commit}"', src)
        self.assertIn('archive --format=tar --output="$work/repo.tar" "$commit"', src)
        self.assertIn('/home/obivan/pi-voice-assistant', src)
        self.assertIn('git fetch origin main', src)
        self.assertIn('curl -fsSL --retry 2', src)
        self.assertIn('-o "$work/repo.tar.gz"', src)
        self.assertNotIn('curl -fsSL "https://codeload', src)
        self.assertIn('bash "$work/scripts/install-voice-service.sh"', src)
        self.assertIn('cut -c1-7 > /opt/pi-voice-assistant/src/DEPLOYED', src)

    def test_git_archive_uses_committed_bytes_not_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / 'checkout'
            checkout.mkdir()
            subprocess.run(['git', 'init', '-q', str(checkout)], check=True)
            subprocess.run(['git', '-C', str(checkout), 'config', 'user.email',
                            'ci@example.invalid'], check=True)
            subprocess.run(['git', '-C', str(checkout), 'config', 'user.name',
                            'CI'], check=True)
            data = checkout / 'scripts'
            data.mkdir()
            target = data / 'install-voice-service.sh'
            target.write_text('echo committed\n')
            subprocess.run(['git', '-C', str(checkout), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(checkout), 'commit', '-qm',
                            'pinned'], check=True)
            commit = subprocess.check_output(
                ['git', '-C', str(checkout), 'rev-parse', 'HEAD'], text=True).strip()
            target.write_text('echo dirty\n')
            archive = root / 'snapshot.tar'
            subprocess.run(['git', '-C', str(checkout), 'archive',
                            '--format=tar', '--output=' + str(archive),
                            commit], check=True)
            with tarfile.open(archive) as tar:
                content = tar.extractfile('scripts/install-voice-service.sh').read()
                self.assertEqual(content, b'echo committed\n')



if __name__ == '__main__':
    unittest.main()
