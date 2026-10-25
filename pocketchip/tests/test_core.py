import json
import os
from pathlib import Path
import tempfile
import unittest

from proximus_os.core import Archive, calculate, local_command, system_status
from proximus_os.transport import ClientConfig, validate_url


class LocalTests(unittest.TestCase):
    def test_archive_survives_restart_with_private_atomic_file(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            archive.add('  Äther: lokaler Datensatz  ')
            self.assertEqual(Archive(directory).notes()[0]['text'], 'Äther: lokaler Datensatz')
            self.assertEqual(archive.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(directory).glob('.notes-*')), [])

    def test_corrupt_archive_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            archive.path.write_text('{broken')
            archive.path.chmod(0o600)
            with self.assertRaises(ValueError):
                archive.add('do not overwrite')
            self.assertEqual(archive.path.read_text(), '{broken')

    def test_archive_rejects_symlink_and_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            target = Path(directory) / 'target'
            target.write_text('[]')
            target.chmod(0o600)
            archive.path.symlink_to(target)
            with self.assertRaises(OSError):
                archive.notes()
            archive.path.unlink()
            archive.path.write_text('[]')
            archive.path.chmod(0o644)
            with self.assertRaises(ValueError):
                archive.notes()

    def test_archive_limits_without_truncating(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Archive(directory)
            for text in ('', 'x' * 4001):
                with self.assertRaises(ValueError):
                    archive.add(text)
            archive.path.write_text(json.dumps([{'time': 'now', 'text': 'x'}] * 80))
            archive.path.chmod(0o600)
            with self.assertRaises(ValueError):
                archive.add('one too many')
            self.assertEqual(len(archive.notes()), 80)

    def test_calculator_uses_bounded_decimal_arithmetic(self):
        self.assertEqual(calculate('0,1 + 0.2'), '0.3')
        self.assertEqual(calculate('-(12 + 3) / 2'), '-7.5')
        for expression in ('2**999999', '__import__("os")', 'True + 1', '1e300', '9' * 100):
            with self.assertRaises(ValueError):
                calculate(expression)
        self.assertIn('Ungültige', local_command('rechne 1 / 0'))
        self.assertIn('Unbekannter', local_command('rm -rf /'))

    def test_missing_battery_is_unknown_not_fabricated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = system_status(root, root)
            self.assertIn('unbekannt', result)
            self.assertNotIn('%', result)
            battery = root / 'class/power_supply/bat'
            battery.mkdir(parents=True)
            (battery / 'type').write_text('Battery')
            (battery / 'capacity').write_text('42')
            (battery / 'status').write_text('Discharging')
            self.assertIn('42 %', system_status(root, root))
            (battery / 'capacity').write_text('400')
            self.assertNotIn('400 %', system_status(root, root))


class ConfigTests(unittest.TestCase):
    def test_urls_require_tls_or_loopback_and_reject_embedded_credentials(self):
        for url in ('http://ct107:8770', 'https://token@host', 'https://host/path',
                    'file:///tmp/data', 'https://host/?token=secret', 'https://host:bad',
                    'https://host\nAuthorization:bad', 'https://host/#secret'):
            with self.assertRaises(ValueError, msg=url):
                validate_url(url)
        self.assertEqual(validate_url('https://ct107.example/'), 'https://ct107.example')
        self.assertEqual(validate_url('http://127.0.0.1:8770'), 'http://127.0.0.1:8770')

    def test_config_permission_and_content_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'client.json'
            path.write_text(json.dumps({'url': 'https://ct107.example', 'token': 'a' * 64}))
            path.chmod(0o600)
            self.assertEqual(ClientConfig.load(path).token, 'a' * 64)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                ClientConfig.load(path)
            path.chmod(0o600)
            for token in ('short', 'x' * 257, 'x' * 31 + '\n', 'ä' * 32):
                path.write_text(json.dumps({'url': 'https://ct107.example', 'token': token}))
                with self.assertRaises(ValueError):
                    ClientConfig.load(path)


if __name__ == '__main__':
    unittest.main()
