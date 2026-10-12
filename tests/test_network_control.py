"""NetworkManager parsing, authorization boundaries and failed-connect rollback."""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import network_control as wifi

HOME = "11111111-2222-3333-4444-555555555555"
OFFICE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class FakeNmcli:
    def __init__(self, fail_connect=False):
        self.commands = []
        self.current = HOME
        self.fail_connect = fail_connect

    def __call__(self, args, **kwargs):
        self.commands.append(tuple(args))
        assert kwargs["stdin"] == wifi.subprocess.DEVNULL
        assert kwargs["env"]["LC_ALL"] == "C"
        cmd = tuple(args)
        if "connection" in cmd and cmd[-1] == "show":
            out = f"Home:{HOME}:802-11-wireless\nOffice:{OFFICE}:802-11-wireless\n"
        elif "-g" in cmd:
            out = "Home\n" if HOME in cmd else "Office\n"
        elif "--active" in cmd:
            out = f"{self.current}:802-11-wireless:wlan0\n"
        elif "device" in cmd and "wifi" in cmd:
            out = "Home:91\nCafe\\:Guest:72\nHome:77\n:21\n"
        elif "up" in cmd:
            if self.fail_connect and OFFICE in cmd:
                return SimpleNamespace(returncode=10, stdout="", stderr="SECRET")
            self.current = OFFICE if OFFICE in cmd else HOME
            out = "ok"
        else:
            raise AssertionError(cmd)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")


class WifiTests(unittest.TestCase):
    def test_escaped_delimiters(self):
        self.assertEqual(wifi.split_terse(r"Cafe\:Guest:75"), ["Cafe:Guest", "75"])
        self.assertEqual(wifi.split_terse(r"A\\B:10"), [r"A\B", "10"])

    def test_saved_profiles_are_by_uuid_not_name(self):
        fake = FakeNmcli()
        manager = wifi.Wifi(fake)
        self.assertEqual(manager.saved(), [
            dict(name="Home", uuid=HOME, ssid="Home"),
            dict(name="Office", uuid=OFFICE, ssid="Office")])
        self.assertEqual(manager.active_uuid(), HOME)

    def test_scan_deduplicates_and_sorts(self):
        manager = wifi.Wifi(FakeNmcli())
        self.assertEqual(manager.scan(), [("Home", 91), ("Cafe:Guest", 72)])

    def test_invalid_uuid_never_runs_nmcli(self):
        fake = Mock()
        with self.assertRaises(wifi.WifiError):
            wifi.Wifi(fake).connect("123; rm -rf /")
        fake.assert_not_called()

    def test_saved_connection_and_no_secrets_in_argv(self):
        fake = FakeNmcli()
        self.assertTrue(wifi.Wifi(fake).connect(OFFICE))
        self.assertEqual(fake.current, OFFICE)
        self.assertIn(("nmcli", "--wait", "20", "connection", "up", "uuid",
                       OFFICE, "ifname", "wlan0"), fake.commands)

    def test_connect_failure_restores_previous_profile(self):
        fake = FakeNmcli(fail_connect=True)
        with self.assertRaises(wifi.WifiError) as ctx:
            wifi.Wifi(fake).connect(OFFICE)
        self.assertEqual(fake.current, HOME)
        self.assertNotIn("SECRET", str(ctx.exception))
        self.assertIn(("nmcli", "--wait", "10", "connection", "up", "uuid",
                       HOME, "ifname", "wlan0"), fake.commands)

    def test_unavailable_networkmanager_does_not_crash(self):
        fake = Mock(side_effect=OSError("missing nmcli"))
        with self.assertRaises(wifi.WifiError):
            wifi.Wifi(fake).scan()

    def test_scan_never_discloses_passwords(self):
        fake = FakeNmcli()
        wifi.Wifi(fake).scan()
        self.assertFalse(any("password" in " ".join(cmd) for cmd in fake.commands))


if __name__ == "__main__":
    unittest.main()
