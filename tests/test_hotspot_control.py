"""No-privilege access to physical hotspot controls and private credentials."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import hotspot_control


class HotspotControlTests(unittest.TestCase):
    def test_systemd_control_uses_bus_without_setuid(self):
        with patch("hotspot_control._systemctl", return_value=True) as ctl:
            self.assertTrue(hotspot_control.control("start"))
            ctl.assert_called_with("start", "proximus-setup.service", timeout=30)
            self.assertTrue(hotspot_control.control("stop"))
            ctl.assert_called_with("stop", "proximus-setup.service", timeout=30)
            self.assertTrue(hotspot_control.is_active())
            ctl.assert_called_with("is-active", "--quiet",
                                   "proximus-setup.service", timeout=5)
        with self.assertRaises(ValueError):
            hotspot_control.control("restart")
        with self.assertRaises(ValueError):
            hotspot_control.control("enable")

    def _payload(self, password="validSetupPass123", ssid=hotspot_control.SSID,
                 issued_at=None, expires_at=None):
        t = time.time() if issued_at is None else issued_at
        return {"ssid": ssid, "password": password, "url": hotspot_control.PORTAL,
                "issued_at": t, "expires_at": t + 900 if expires_at is None else expires_at}

    def test_read_info_returns_only_live_valid_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "secret"
            target.write_text(json.dumps(self._payload()), encoding="utf-8")
            target.chmod(0o600)
            valid = hotspot_control.read_info(target)
            self.assertEqual(valid["password"], "validSetupPass123")
            self.assertGreater(valid["expires_at"], time.time())
            target.write_text(json.dumps(self._payload(ssid="Other")))
            self.assertIsNone(hotspot_control.read_info(target))
            target.write_text(json.dumps(self._payload(password="wrong")))
            self.assertIsNone(hotspot_control.read_info(target))

    def test_rejects_missing_symlink_stale_and_malformed_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "secret"
            self.assertIsNone(hotspot_control.read_info(target))
            target.write_text("{}")
            self.assertIsNone(hotspot_control.read_info(target))
            target.write_text(json.dumps(self._payload(password="correctPassword123")))
            link = root / "link"
            link.symlink_to(target)
            self.assertIsNone(hotspot_control.read_info(link))
            with patch("hotspot_control.time.time",
                       return_value=time.time() + hotspot_control.MAX_INFO_AGE + 10):
                self.assertIsNone(hotspot_control.read_info(target))
            target.write_text("A" * 1025)
            self.assertIsNone(hotspot_control.read_info(target))

    def test_expired_forged_and_legacy_session_info_is_never_shown(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "secret"
            t = time.time()
            variants = [
                {k: v for k, v in self._payload(issued_at=t).items()
                 if k != "expires_at"},
                self._payload(issued_at=t - 901, expires_at=t - 1),
                self._payload(issued_at=t, expires_at=t + 1000),
                self._payload(issued_at=t + 180, expires_at=t + 500),
            ]
            for value in variants:
                target.write_text(json.dumps(value))
                self.assertIsNone(hotspot_control.read_info(target), value)

    def test_polkit_is_narrow_and_sudo_is_not_used(self):
        root = Path(__file__).resolve().parents[1]
        rule = (root / "deploy/52-pi-voice-hotspot.rules").read_text()
        self.assertIn('"org.freedesktop.systemd1.manage-units"', rule)
        self.assertIn('action.lookup("unit") === "proximus-setup.service"', rule)
        self.assertIn('verb === "start" || verb === "stop"', rule)
        controller = (root / "src/hotspot_control.py").read_text()
        self.assertNotIn("/usr/bin/sudo", controller)
        self.assertNotIn("shell=True", controller)
        service = (root / "deploy/pi-ptt.service").read_text()
        self.assertIn("NoNewPrivileges=yes", service)
        installer = (root / "scripts/install-voice-service.sh").read_text()
        self.assertIn("52-pi-voice-hotspot.rules", installer)
        self.assertIn("src/hotspot_control.py", installer)
        self.assertNotIn("deploy/proximus-setup.sudoers", installer)


if __name__ == "__main__":
    unittest.main()
