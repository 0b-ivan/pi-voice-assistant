"""Root unit, voice daemon and PiTFT must use the same setup AP settings."""
import sys
import time
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hotspot_config as cfg
import hotspot_control as ctl
import proximus_setup as setup
import display


class SharedHotspotConfigTests(unittest.TestCase):
    def test_all_components_share_one_ap_configuration(self):
        self.assertEqual(cfg.SSID, setup.AP_NAME)
        self.assertEqual(cfg.SSID, ctl.SSID)
        self.assertEqual(cfg.ADDRESS, setup.AP_IP)
        self.assertEqual(cfg.PORTAL_URL, setup.AP_URL)
        self.assertEqual(cfg.PORTAL_URL, ctl.PORTAL)
        self.assertEqual(cfg.INFO_FILE, setup.INFO_FILE)
        self.assertEqual(cfg.INFO_FILE, ctl.INFO_FILE)
        self.assertEqual(cfg.DISPLAY_VIEW_FILE, display.PEOPLE_FILE)
        self.assertEqual(cfg.IFACE, setup.IFACE)
        self.assertEqual(cfg.SESSION_SECONDS, setup.MAX_AGE)

    def test_expiry_rejects_invalid_or_expired_dates(self):
        now = time.time()
        for expiry in (None, "tomorrow", True, False, float("nan"),
                       now, now - 1, now + cfg.SESSION_SECONDS + 61):
            self.assertFalse(cfg.live_expiration(expiry, now), expiry)
        self.assertTrue(cfg.live_expiration(now + cfg.SESSION_SECONDS, now))
        self.assertTrue(cfg.live_expiration(now + 1, now))

    def test_installers_deploy_shared_configuration(self):
        root = Path(__file__).resolve().parents[1]
        for script in ("scripts/install-voice-service.sh",
                       "scripts/install-display.sh"):
            data = (root / script).read_text()
            self.assertIn("src/hotspot_config.py", data)

if __name__ == "__main__":
    unittest.main()
