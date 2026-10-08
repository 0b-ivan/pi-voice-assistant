import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import system_status as ss  # noqa: E402

Usage = namedtuple("Usage", "total used free")


class SnapshotTests(unittest.TestCase):
    def test_collect_snapshot_reads_local_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "temp").write_text("45123\n")
            (tmp / "meminfo").write_text("MemTotal: 400000 kB\nMemAvailable: 100000 kB\n")
            (tmp / "loadavg").write_text("0.40 0.30 0.20 1/100 1\n")
            (tmp / "uptime").write_text("7380.2 100.0\n")
            snapshot = ss.collect_snapshot(
                battery=dict(percent=83, charging=True, plugged=True), throttled=0,
                server="ok", thermal_path=tmp / "temp", meminfo_path=tmp / "meminfo",
                loadavg_path=tmp / "loadavg", uptime_path=tmp / "uptime",
                disk_usage=lambda _p: Usage(100, 30, 70), cpu_count=lambda: 4)
        self.assertEqual(snapshot, dict(temp_c=45, load_pct=10, mem_free_pct=25,
                                        disk_free_pct=70, throttled=0, server="ok",
                                        uptime_s=7380, battery_pct=83,
                                        battery_charging=True, battery_plugged=True))

    def test_sanitize_drops_unknown_and_implausible_values(self):
        clean = ss.sanitize_snapshot({"temp_c": 45.6, "battery_pct": 300, "server": "mars",
                                      "battery_charging": "yes", "evil": "x" * 100,
                                      "llm": "offline", "uptime_s": True})
        self.assertEqual(clean, {"temp_c": 45, "llm": "offline"})
        self.assertEqual(ss.sanitize_snapshot("not a dict"), {})


class StatusTextTests(unittest.TestCase):
    NOMINAL = dict(temp_c=45, load_pct=10, mem_free_pct=40, disk_free_pct=70,
                   uptime_s=7380, battery_pct=83, battery_charging=True,
                   battery_plugged=True, throttled=0, server="ok", llm="openrouter")

    def test_nominal_status_is_short_and_ordered(self):
        self.assertEqual(
            ss.status_text(self.NOMINAL),
            "Status nominal. Energiespeicher 83 Prozent. Ladung aktiv. Kerntemperatur 45 Grad. "
            "Verbindung zum Server stabil. Laufzeit zwei Stunden drei Minuten. Befehl erwartet.")

    def test_warnings_come_first(self):
        text = ss.status_text(dict(self.NOMINAL, battery_pct=12, battery_charging=False,
                                   battery_plugged=False, temp_c=78, throttled=0x50005,
                                   mem_free_pct=5, load_pct=95))
        self.assertTrue(text.startswith(
            "Status eingeschränkt. Warnung: Unterspannung. "
            "Warnung: Energiespeicher kritisch, 12 Prozent. Warnung: Kerntemperatur 78 Grad. "
            "Warnung: Arbeitsspeicher knapp."))
        self.assertIn("Systemlast 95 Prozent.", text)
        self.assertEqual(text.count("Kerntemperatur"), 1)
        self.assertEqual(text.count("Energiespeicher"), 1)

    def test_connection_states(self):
        self.assertIn("Sprachkern im Notbetrieb.", ss.status_text(dict(self.NOMINAL, llm="offline")))
        down = ss.status_text(dict(self.NOMINAL, server="down"))
        self.assertIn("Server nicht erreichbar. Lokaler Betrieb.", down)
        self.assertTrue(down.startswith("Status nominal."))  # informational, not a warning
        self.assertIn("Nur lokaler Betrieb.", ss.status_text(dict(self.NOMINAL, server="off")))

    def test_processing_and_missing_data(self):
        self.assertEqual(ss.status_text({}, processing=True), "Direktive in Bearbeitung.")
        self.assertEqual(ss.status_text({}), "Status nominal. Befehl erwartet.")

    def test_lore_levels(self):
        light = ss.status_text(dict(self.NOMINAL, lore="light"))
        self.assertTrue(light.endswith("Maschinengeist ruhig. Befehl erwartet."))
        full = ss.status_text(dict(self.NOMINAL, lore="full"))
        self.assertTrue(full.startswith("Status-Litanei beginnt."))
        self.assertTrue(full.endswith("Lob dem Omnissiah."))
        warned = ss.status_text(dict(self.NOMINAL, lore="full", throttled=1))
        self.assertIn("Makel am Maschinengeist erkannt. Warnung: Unterspannung.", warned)
        self.assertTrue(ss.status_text(dict(self.NOMINAL, lore="off")).endswith("Befehl erwartet."))
        self.assertEqual(ss.sanitize_snapshot({"lore": "full"}), {"lore": "full"})
        self.assertEqual(ss.sanitize_snapshot({"lore": "chaos"}), {})

    def test_battery_sentence(self):
        self.assertEqual(ss.battery_sentence(dict(battery_pct=64)),
                         "Energiespeicher 64 Prozent. Akkubetrieb.")
        self.assertEqual(ss.battery_sentence(dict(battery_pct=100, battery_plugged=True)),
                         "Energiespeicher 100 Prozent. Netzbetrieb.")
        self.assertIsNone(ss.battery_sentence({}))


if __name__ == "__main__":
    unittest.main()
