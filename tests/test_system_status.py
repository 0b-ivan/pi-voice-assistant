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

    def test_log_timestamp_survives_transport_but_nonfinite_values_do_not(self):
        self.assertEqual(ss.sanitize_snapshot({'log_checked_at': 1791660000.9}),
                         {'log_checked_at': 1791660000})
        for value in (float('nan'), float('inf'), float('-inf'), True):
            self.assertEqual(ss.sanitize_snapshot({'log_checked_at': value}), {})


class FirstChoice:
    """rng for variants.pick: always the first wording (exact strings in tests)."""
    @staticmethod
    def choice(options):
        return options[0]


class StatusTextTests(unittest.TestCase):
    NOMINAL = dict(temp_c=45, load_pct=10, mem_free_pct=40, disk_free_pct=70,
                   uptime_s=7380, battery_pct=83, battery_charging=True,
                   battery_plugged=True, throttled=0, server="ok", llm="openrouter")

    def setUp(self):
        import variants
        variants.reset()

    def status(self, snapshot, **kw):
        import variants
        variants.reset()          # pick() avoids the last wording on purpose
        return ss.status_text(snapshot, rng=FirstChoice, **kw)

    def test_nominal_status_is_short_and_ordered(self):
        self.assertEqual(
            self.status(self.NOMINAL),
            "Status nominal. Energiespeicher 83 Prozent. Ladung aktiv. Kerntemperatur 45 Grad. "
            "Verbindung zum Server stabil. Laufzeit zwei Stunden und drei Minuten. "
            "Befehl erwartet.")

    def test_opening_and_closing_vary_but_facts_stay(self):
        texts = {ss.status_text(self.NOMINAL) for _ in range(20)}
        self.assertGreater(len(texts), 1)
        for text in texts:
            self.assertTrue(text.startswith(ss.STATUS_OPEN['servitor']), text)
            self.assertTrue(text.endswith(ss.STATUS_CLOSE['off']), text)
            self.assertIn("Energiespeicher 83 Prozent. Ladung aktiv.", text)
            self.assertIn("45 Grad.", text)
            self.assertIn("zwei Stunden und drei Minuten.", text)
            self.assertTrue(any(server in text for server in ss.STATUS_FACTS['server']), text)
            # The facts keep their order whatever the wording.
            self.assertLess(text.index("83 Prozent"), text.index("45 Grad"))
            self.assertLess(text.index("45 Grad"), text.index("zwei Stunden"))

    def test_network_and_cpu_profile(self):
        text = self.status(dict(self.NOMINAL, wifi_dbm=-60, net_ms=40, dns='ok', cpu='eco'))
        self.assertIn("Kerntemperatur 45 Grad. WLAN-Empfang gut, Internet erreichbar. "
                      "Verbindung zum Server stabil. Prozessor im Sparmodus.", text)
        self.assertIn("WLAN-Empfang schwach, Namensauflösung gestört.",
                      self.status(dict(self.NOMINAL, wifi_dbm=-85, dns='fail')))
        self.assertIn("WLAN-Empfang mittel, Kein Internet.".replace('Kein', 'kein'),
                      self.status(dict(self.NOMINAL, wifi_dbm=-70, dns='ok')))
        self.assertIn("Internet langsam.", self.status(dict(self.NOMINAL, net_ms=900)))
        # Not measured yet, WLAN off (a warning already), balanced CPU: no words.
        for quiet in (self.NOMINAL, dict(self.NOMINAL, wlan='off', wifi_dbm=-60),
                      dict(self.NOMINAL, cpu='balanced')):
            text = self.status(quiet)
            self.assertNotIn("WLAN-Empfang", text)
            self.assertNotIn("Prozessor", text)
        self.assertIn("Prozessor übertaktet.", self.status(dict(self.NOMINAL, cpu='overclock')))
        self.assertNotIn("übertaktet", self.status(dict(self.NOMINAL, cpu='overclock',
                                                        throttled=0x4)))

    def test_uptime_words(self):
        for seconds, words in ((30, "unter einer Minute"), (60, "eine Minute"),
                               (7200, "zwei Stunden"), (86400, "ein Tag"),
                               (90000, "ein Tag und eine Stunde")):
            self.assertEqual(ss._uptime_from_seconds(seconds), words)
        self.assertEqual(ss._uptime_from_seconds(3 * 86400 + 3600, dative=True),
                         "drei Tagen und einer Stunde")
        self.assertEqual(ss._uptime_from_seconds(30, dative=True), "weniger als einer Minute")

    def test_warnings_come_first(self):
        text = self.status(dict(self.NOMINAL, battery_pct=12, battery_charging=False,
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
        self.assertIn("Sprachkern im Notbetrieb.", self.status(dict(self.NOMINAL, llm="offline")))
        down = self.status(dict(self.NOMINAL, server="down"))
        self.assertIn("Server nicht erreichbar. Lokaler Betrieb.", down)
        self.assertTrue(down.startswith("Status nominal."))  # informational, not a warning
        self.assertIn("Nur lokaler Betrieb.", self.status(dict(self.NOMINAL, server="off")))

    def test_processing_and_missing_data(self):
        self.assertEqual(self.status({}, processing=True), "Direktive in Bearbeitung.")
        self.assertEqual(self.status({}), "Statusdaten nicht verfügbar.")

    def test_unknown_network_readings_are_not_an_outage(self):
        for level in ('off', 'light', 'full', 'billy_full'):
            with self.subTest(lore=level):
                result = ss.network_text({'server': 'ok'}, lore=level)
                self.assertIn('Pi-Netzwerkdaten', result) if level != 'billy_full' else (
                    self.assertIn('Netzwerkdaten des Pi fehlen', result))
                self.assertNotIn('Internet nicht erreichbar', result)
                self.assertNotIn('Noosphäre ist vermessen', result)
        self.assertIn('Internetstatus nicht ermittelt',
            ss.network_text({'wlan': 'on', 'server': 'ok'}))
        self.assertIn('Internet erreichbar',
            ss.network_text({'wlan': 'on', 'net_ms': 80}))

    def test_persona_and_voice_are_whitelisted(self):
        self.assertEqual(ss.sanitize_snapshot({"persona": "mensch", "voice": "natural"}),
                         {"persona": "mensch", "voice": "natural"})
        self.assertEqual(ss.sanitize_snapshot({"persona": "chaos", "voice": 1}), {})

    def test_lore_levels(self):
        light = self.status(dict(self.NOMINAL, lore="light"))
        self.assertTrue(light.endswith("Maschinengeist ruhig. Befehl erwartet."))
        full = self.status(dict(self.NOMINAL, lore="full"))
        self.assertTrue(full.startswith("Status-Litanei beginnt."))
        self.assertTrue(full.endswith("Lob dem Omnissiah."))
        warned = self.status(dict(self.NOMINAL, lore="full", throttled=1))
        self.assertIn("Makel am Maschinengeist erkannt. Warnung: Unterspannung.", warned)
        self.assertTrue(self.status(dict(self.NOMINAL, lore="off")).endswith("Befehl erwartet."))
        # Light lore with a real problem closes plainly.
        self.assertTrue(self.status(dict(self.NOMINAL, lore="light", throttled=1))
                        .endswith(". Befehl erwartet."))
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
