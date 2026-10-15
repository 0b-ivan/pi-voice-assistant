"""Read-only PiTFT network information, never network configuration."""
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import network_info
from network_control import WifiError


class NetworkInfoTests(unittest.TestCase):
    class FakeWifi:
        def __init__(self, eth="", wlan=""):
            self.eth, self.wlan, self.commands = eth, wlan, []

        def call(self, *args, timeout=None):
            self.commands.append((args, timeout))
            self._read_only(args)
            return self.eth if args[-1] == "eth0" else self.wlan

        def _read_only(self, args):
            self_obj = self
            del self_obj
            assert args[:4] == ("-t", "-e", "yes", "-f")
            assert args[-3:-1] == ("device", "show")

    def test_lan_and_wifi_details_are_read_only(self):
        wifi = self.FakeWifi(
            eth="""GENERAL.STATE:100 (connected)
GENERAL.CONNECTION:netplan-eth0
IP4.ADDRESS[1]:192.168.1.38/24
IP4.GATEWAY:192.168.1.1
IP4.DNS[1]:192.168.1.1
""",
            wlan="""GENERAL.STATE:100 (connected)
GENERAL.CONNECTION:MyProfile
IP4.ADDRESS[1]:192.168.1.49/24
IP4.GATEWAY:192.168.1.1
IP4.DNS[1]:1.1.1.1
""")
        view = network_info.rows(wifi, ap_active=False,
                                 station={"ssid": "Mein WLAN", "dbm": -57})
        self.assertIn("LAN: verbunden", view)
        self.assertIn("LAN-IP: 192.168.1.38/24", view)
        self.assertIn("LAN-GW: 192.168.1.1", view)
        self.assertIn("WLAN: verbunden", view)
        self.assertIn("SSID: Mein WLAN", view)
        self.assertIn("WLAN-IP: 192.168.1.49/24", view)
        self.assertIn("Signal: -57 dBm", view)
        self.assertIn("DNS: 1.1.1.1", view)
        self.assertEqual(len(wifi.commands), 2)
        self.assertTrue(all(timeout == 5 for _, timeout in wifi.commands))

    def test_ap_shows_client_count_without_station_scan(self):
        wifi = self.FakeWifi(
            eth="GENERAL.STATE:30 (disconnected)\n",
            wlan="GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:192.168.77.1/24\n")
        with patch("network_info._station_link", side_effect=AssertionError("must not scan")):
            view = network_info.rows(wifi, ap_active=True,
                                     clients={"clients": [{"mac": "aa:bb:cc:dd:ee:ff"}]})
        self.assertIn("LAN: getrennt", view)
        self.assertIn("WLAN: Access Point", view)
        self.assertIn("SSID: Proximus-Setup", view)
        self.assertIn("WLAN-IP: 192.168.77.1/24", view)
        self.assertIn("AP-Clients: 1", view)
        self.assertIn("Portal: 192.168.77.1", view)

    def test_missing_nm_or_disconnected_network_still_shows_status(self):
        class OfflineWifi:
            def call(self, *args, **kwargs):
                raise WifiError("NetworkManager unavailable")
        view = network_info.rows(OfflineWifi(), ap_active=False, station={})
        self.assertEqual(view, ["LAN: unbekannt", "WLAN: unbekannt"])
        view = network_info.rows(self.FakeWifi(
            "GENERAL.STATE:30 (disconnected)\n",
            "GENERAL.STATE:30 (disconnected)\n"), ap_active=False, station={})
        self.assertEqual(view, ["LAN: getrennt", "WLAN: getrennt"])

    def test_invalid_ip_and_control_characters_never_reach_display(self):
        wifi = self.FakeWifi(
            eth="GENERAL.STATE:100 (connected)\nIP4.ADDRESS[1]:INJECT\nIP4.GATEWAY:bad\n",
            wlan="GENERAL.STATE:100 (connected)\nGENERAL.CONNECTION:weird profile\n")
        view = network_info.rows(wifi, ap_active=False,
                                 station={"ssid": "House\nSecret\tSSID", "dbm": -150})
        self.assertIn("SSID: HouseSecretSSID", view)
        self.assertNotIn("LAN-GW: bad", view)
        self.assertFalse(any("INJECT" in v or "\n" in v for v in view))
        self.assertFalse(any(v.startswith("Signal:") for v in view))

    def test_iw_link_is_bounded_and_read_only(self):
        output = """Connected to 00:11:22:33:44:55 (on wlan0)
 SSID: HOME-NET
 signal: -64 dBm
"""
        with patch("network_info.subprocess.run",
                   return_value=SimpleNamespace(returncode=0, stdout=output)) as run:
            self.assertEqual(network_info._station_link(),
                             {"ssid": "HOME-NET", "dbm": -64})
        self.assertEqual(run.call_args.args[0], ["iw", "dev", "wlan0", "link"])
        self.assertFalse(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
