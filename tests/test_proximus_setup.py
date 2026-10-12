"""Proximus AP provisioning without root, DBus, WLAN or real sockets."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import proximus_setup as ps


class SetupValidationTests(unittest.TestCase):
    def test_wifi_ssids_and_passwords(self):
        self.assertTrue(ps.network_name("Würzburg WLAN"))
        self.assertTrue(ps.password_ok("strong#pass?"))
        for ssid in ("", "A" * 33, "contains\nnewline", " lead", "trail "):
            self.assertFalse(ps.network_name(ssid), ssid)
        for pwd in ("short", "ü" * 16, "contains\nnewline", "A" * 64):
            self.assertFalse(ps.password_ok(pwd), pwd)

    def test_keyfile_is_root_marked_no_shell_commands(self):
        identity = "11111111-2222-3333-4444-555555555555"
        profile = ps.keyfile_text("test:wifi", "secret-1234", identity)
        self.assertIn(ps.MARKER, profile)
        self.assertIn("[wifi-security]\nkey-mgmt=wpa-psk\npsk=secret-1234", profile)
        self.assertIn("mode=infrastructure", profile)
        self.assertIn("autoconnect=false", profile)
        self.assertIn("method=auto", profile)
        ap = ps.keyfile_text(ps.AP_NAME, "random123456", identity, ap=True)
        self.assertIn("mode=ap", ap)
        self.assertIn("method=shared", ap)
        self.assertIn("address1=192.168.77.1/24", ap)
        self.assertIn("method=disabled", ap)

    def test_keyfile_escaping_and_bad_values(self):
        data = ps.keyfile_text("X\\Y", "A\\B-secure", "11111111-2222-3333-4444-555555555555")
        self.assertIn("ssid=X\\\\Y", data)
        self.assertIn("psk=A\\\\B-secure", data)
        with self.assertRaises(ps.SetupError):
            ps.keyfile_text("SSID", "secret\nbad", "11111111-2222-3333-4444-555555555555")

    def test_file_written_atomically_with_owner_only_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secret"
            ps.private_write(path, "credential")
            self.assertEqual(path.read_text(), "credential")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            ps.private_write(path, "new")
            self.assertEqual(path.read_text(), "new")
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_nft_blocks_setup_clients_from_lan(self):
        self.assertIn('iifname "wlan0" drop', ps.NFT_RULES)
        self.assertIn("chain forward", ps.NFT_RULES)
        self.assertIn("udp dport { 53, 67 }", ps.NFT_RULES)
        self.assertIn("tcp dport { 53, 80 }", ps.NFT_RULES)

    def test_command_does_not_leak_stderr(self):
        def fake(args, **_kw):
            return SimpleNamespace(returncode=10, stdout="", stderr="SECRET-WIFI-PASSWORD")
        with patch("proximus_setup.subprocess.run", side_effect=fake):
            with self.assertRaises(ps.SetupError) as exception:
                ps.command(["nmcli", "connection", "up", "uuid", "xxx"])
        self.assertNotIn("SECRET", str(exception.exception))


class FakeWifi:
    def active_uuid(self):
        return None

    def scan(self):
        return [("Home", 91), ("Guest", 67)]


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.calls = []
        self.fail_candidate = False
        self.app = ps.Provisioning(
            directory=self.root, info_path=self.root / "physical-info",
            run=self.run, wifi=FakeWifi())

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if self.fail_candidate and len(args) > 5 and "up" in args and "uuid" in args:
            identity = args[args.index("uuid") + 1]
            if identity != self.app.ap_uuid:
                raise ps.SetupError("Secret-password-never-logged")
        return ""

    def test_setup_scans_before_ap_and_installs_nft_first(self):
        with patch("proximus_setup.shutil.which", return_value="/usr/bin/mock"), \\
             patch("proximus_setup.pwd.getpwnam",
                   return_value=SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())):
            self.app.setup()
        self.assertEqual(self.app.found, ["Home", "Guest"])
        self.assertTrue(self.app.ap_path.is_file())
        self.assertEqual(self.calls[0][0], ["nft", "-f", "-"])
        self.assertEqual(self.calls[1][0][:3], ["nmcli", "connection", "load"])
        info = json.loads(self.app.info_path.read_text())
        self.assertEqual((info["ssid"], info["url"]), (ps.AP_NAME, ps.AP_URL))
        self.assertEqual(len(info["password"]), 16)
        self.assertTrue(ps.password_ok(info["password"]))
        self.app.cleanup()
        self.assertFalse(self.app.ap_path.exists())
        self.assertFalse(self.app.info_path.exists())

    def test_saved_profile_remains_after_success(self):
        self.app.connect("Home", "correctpassword")
        self.assertTrue(self.app.done)
        profiles = list(self.root.glob("proximus-net-*.nmconnection"))
        self.assertEqual(len(profiles), 1)
        calls = [item[0] for item in self.calls]
        self.assertTrue(any("connection.autoconnect" in c for c in calls))
        self.assertFalse(any("correctpassword" in str(c) for c in calls))
        self.app.cleanup()
        self.assertTrue(profiles[0].is_file())

    def test_failed_password_removes_profile_and_tries_ap_again(self):
        self.fail_candidate = True
        self.app.connect("Home", "wrongpassword")
        self.assertFalse(self.app.done)
        self.assertEqual(list(self.root.glob("proximus-net-*.nmconnection")), [])
        self.assertIn("Passwort prüfen", self.app.error)
        calls = [x[0] for x in self.calls]
        self.assertTrue(any(self.app.ap_uuid in c and "up" in c for c in calls))
        self.assertFalse(any("wrongpassword" in str(c) for c in calls))
        self.assertFalse(self.app.busy)

    def test_csrf_attempt_limit_and_invalid_ssid_rejected(self):
        app = self.app
        with patch.object(app, "connect"):
            self.assertFalse(app.try_connect("Home", "validpassword", "wrong-token"))
            self.assertFalse(app.try_connect("hi\nthere", "validpassword", app.csrf))
        self.assertEqual(app.attempts, 0)

    def test_html_escapes_discovered_ssids_and_does_not_show_wpa_key(self):
        self.app.found = ['<img src=x onerror=evil()>', 'Würzburg']
        markup = self.app.page()
        self.assertNotIn("<img", markup)
        self.assertIn("&lt;img", markup)
        self.assertIn("Würzburg", markup)
        self.assertNotIn(self.app.psk, markup)
        self.assertIn('name="token"', markup)
        self.assertIn('name="password"', markup)
        self.assertIn('name="ssid"', markup)
        self.assertIn('name="manual_ssid"', markup)

    def test_manual_ssid_does_not_depend_on_javascript(self):
        markup = self.app.page()
        self.assertNotIn("onchange", markup)
        self.assertNotIn("<script", markup)
        self.assertIn("SSID manuell", markup)


if __name__ == "__main__":
    unittest.main()
