"""Live station discovery, private snapshot, no leaked secrets or stale guests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hotspot_config as cfg
import hotspot_control as ctl
import proximus_setup as server
import display


class APClientsTests(unittest.TestCase):
    SAMPLE = """Station AA:BB:CC:DD:EE:01 (on wlan0)
  inactive time: 20 ms
  signal: -57 dBm
  authorized: yes
Station 10:20:30:40:50:60 (on wlan0)
  signal: -82 [-82, -83] dBm
  authorized: no
Station 12:34:56:78:9A:BC (on wlan0)
  signal: -72 dBm
  authorized: yes
"""

    def test_parses_only_authorized_stations_with_signal(self):
        self.assertEqual(server.associated_stations(self.SAMPLE), [
            {"mac": "aa:bb:cc:dd:ee:01", "signal_dbm": -57},
            {"mac": "12:34:56:78:9a:bc", "signal_dbm": -72}])
        self.assertEqual(server.associated_stations(""), [])

    def test_client_snapshot_is_short_lived_and_validated(self):
        with tempfile.TemporaryDirectory() as dir:
            path = Path(dir) / "clients.json"
            now = time.time()
            payload = {"updated_at": now,
                       "last_joined_at": now,
                       "clients": server.associated_stations(self.SAMPLE)}
            path.write_text(json.dumps(payload))
            path.chmod(0o600)
            fresh = ctl.read_clients(path, now=now)
            self.assertEqual(len(fresh["clients"]), 2)
            self.assertTrue(display.ap_screen_info(fresh, now)["joined"])
            self.assertFalse(display.ap_screen_info(fresh, now + 9)["joined"])
            self.assertIsNone(ctl.read_clients(path, now=now + 9))
            payload["clients"] = [{"mac": "invalid", "signal_dbm": -60}]
            payload["updated_at"] = now + 1
            path.write_text(json.dumps(payload))
            self.assertIsNone(ctl.read_clients(path, now=now + 1))
            payload["clients"] = []
            path.write_text(json.dumps(payload))
            self.assertEqual(ctl.read_clients(path, now=now+1)["clients"], [])

    def test_signal_levels_offline_lan_and_ap_have_distinct_pictograms(self):
        self.assertEqual(display.network_view(True, {"wifi_dbm": -54})[0:2], ("wifi", ""))
        self.assertEqual(display.network_view(True, {"wifi_dbm": -54})[3], 4)
        self.assertEqual(display.network_view(True, {"wifi_dbm": -81})[3], 1)
        self.assertEqual(display.network_view(True, {})[0], "lan")
        self.assertEqual(display.network_view(False, {})[0], "off")
        self.assertEqual(display.network_view(False, {"ap": {"count": 2, "joined": True}})[0:2],
                         ("ap", "AP + NEU"))
        self.assertEqual(display.network_view(False, {"ap": {"count": 0, "joined": False}})[1],
                         "AP 0")

    def test_client_view_is_scrollable_and_isolated_from_public_status(self):
        from unittest.mock import patch, Mock
        from ptt import VoiceController, _display_status
        import ptt
        with tempfile.TemporaryDirectory() as tmp:
            status = Path(tmp) / "status.json"
            with patch.dict(os.environ, {
                    "PTT_DISPLAY_STATUS_PATH": str(status),
                    "PTT_DISPLAY_EVENT_PATH": str(Path(tmp) / "event.json"),
                    "PTT_SETTINGS_FILE": str(Path(tmp) / "settings.json")}):
                recorder, speaker = Mock(), Mock()
                recorder.process = None
                recorder.take_live_transcript.return_value = None
                speaker.poll.return_value = None
                _display_status.clear()
                controller = VoiceController(recorder, speaker, .04, 30, remote=True)
                from dataclasses import replace
                controller.config = replace(controller.config, runtime_dir=Path(tmp))
                result = {"clients": [
                    {"mac": "aa:bb:cc:dd:ee:01", "signal_dbm": -55}],
                    "updated_at": time.time(), "last_joined_at": time.time()}
                with patch("ptt.hotspot_control.read_clients", return_value=result):
                    controller._wifi_hotspot_item("wifi_hotspot_clients")
                self.assertEqual(controller.picker["action"], "wifi_clients")
                self.assertIn("aa:bb:cc:dd:ee:01", controller.picker["items"][0][1])
                self.assertNotIn("aa:bb:cc:dd:ee:01", status.read_text())
                self.assertNotIn("password", status.read_text().lower())


if __name__ == "__main__":
    unittest.main()
