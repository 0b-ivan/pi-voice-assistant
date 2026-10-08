import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sysmon  # noqa: E402
from alarms import AlarmMonitor, NET_SUSTAIN  # noqa: E402
from system_status import network_text, sanitize_snapshot, updates_text  # noqa: E402

APT = """NOTE: This is only a simulation!
Inst libssl3t64 [3.5.1-1] (3.5.1-1+deb13u1 Debian-Security:13/stable-security [arm64])
Inst openssl [3.5.1-1] (3.5.1-1+deb13u1 Debian-Security:13/stable-security [arm64])
Inst raspi-firmware [1:1.2025] (1:1.2026 Raspberry Pi Foundation:stable [all])
Conf libssl3t64 (3.5.1-1+deb13u1 Debian-Security:13/stable-security [arm64])
"""

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   47.  -63.  -256        0      0      0      0     11        0
"""


class SysmonTests(unittest.TestCase):
    def test_pending_updates_counts_security(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = Path(tmp) / "deb.debian.org_dists_trixie_InRelease"
            release.touch()
            os.utime(release, (time.time() - 3 * 86400,) * 2)
            run = lambda *a, **k: SimpleNamespace(returncode=0, stdout=APT)
            self.assertEqual(sysmon.pending_updates(run, tmp),
                             dict(pending=3, security=2, lists_age_days=3))
        failed = lambda *a, **k: SimpleNamespace(returncode=100, stdout="")
        self.assertIsNone(sysmon.pending_updates(failed))

    def test_wifi_signal_and_snapshot_fields(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write(WIRELESS)
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(sysmon.wifi_dbm(handle.name), -63)
        fields = sysmon.snapshot_fields(
            dict(wifi_dbm=-63, net_ms=24, lan_ms=None, dns="ok"),
            dict(pi=dict(pending=3, security=2, lists_age_days=1), server=None))
        self.assertEqual(sanitize_snapshot(fields), dict(
            wifi_dbm=-63, net_ms=24, dns="ok", updates=3, updates_security=2, apt_age_days=1))

    def test_lan_target_uses_the_http_url(self):
        env = dict(ASSISTANT_BASE_URL="http://172.22.9.107:8765,https://proximus.obivan.org")
        self.assertEqual(sysmon.lan_target(env), ("172.22.9.107", 8765))

    def test_spoken_reports(self):
        text = network_text(dict(wifi_dbm=-81, net_ms=520, dns="fail", server="down"))
        self.assertIn("minus 81 dBm, schwach", text)
        self.assertIn("Verbindung langsam", text)
        self.assertIn("Namensauflösung gestört", text)
        self.assertEqual(updates_text({}), "Wartungsdaten noch nicht erhoben.")
        self.assertIn("Keine Aktualisierungen", updates_text(dict(updates=0)))
        self.assertIn("veraltet" if False else "8 Tage alt", updates_text(dict(updates=0, apt_age_days=8)))


class NetworkAlarmTests(unittest.TestCase):
    def test_weak_wifi_must_last_and_recovers(self):
        m = AlarmMonitor()
        snap = dict(wifi_dbm=-84, net_ms=30, dns="ok")
        self.assertEqual(m.update(snap, 0, network=True), [])
        self.assertEqual(m.update(snap, NET_SUSTAIN / 2, network=True), [])
        said = m.update(snap, NET_SUSTAIN + 1, network=True)
        self.assertEqual(said, ["Warnung. WLAN-Signal schwach."])
        self.assertEqual(m.update(dict(snap, wifi_dbm=-76), NET_SUSTAIN + 20, network=True), [])
        self.assertEqual(m.update(dict(snap, wifi_dbm=-60), NET_SUSTAIN + 30, network=True),
                         ["WLAN-Signal wieder stabil."])

    def test_no_quality_alarms_without_network(self):
        m = AlarmMonitor()
        for now in (0, 100, 200):
            m.update(dict(wifi_dbm=-90, net_ms=900, dns="fail"), now, network=None)
        self.assertEqual(m.active, [])

    def test_updates_announced_when_new_then_daily(self):
        m = AlarmMonitor()
        snap = dict(updates=3, updates_security=1)
        self.assertIn("3 Aktualisierungen, davon 1 sicherheitsrelevant",
                      m.updates_notice(snap, 0))
        self.assertIsNone(m.updates_notice(snap, 3600))
        self.assertIsNotNone(m.updates_notice(dict(snap, updates=4), 7200))
        self.assertIsNone(m.updates_notice(dict(snap, updates=4), 7200 + 3600))
        self.assertIsNotNone(m.updates_notice(dict(snap, updates=4), 7200 + 86400))
        self.assertIsNone(m.updates_notice(dict(updates=0), 200000))


if __name__ == "__main__":
    unittest.main()
