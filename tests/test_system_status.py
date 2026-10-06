import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from system_status import build_status_text


class SystemStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.thermal = root / "temp"
        self.meminfo = root / "meminfo"
        self.uptime = root / "uptime"

    @staticmethod
    def disk_usage(_path):
        return SimpleNamespace(total=1000, used=600, free=400)

    def test_idle_status_reports_dynamic_machine_stats(self):
        self.thermal.write_text("54750\n", encoding="utf-8")
        self.meminfo.write_text(
            "MemTotal:       1000 kB\nMemAvailable:    625 kB\n",
            encoding="utf-8",
        )
        self.uptime.write_text("9372.4 0.0\n", encoding="utf-8")

        text = build_status_text(
            processing=False,
            stt_provider="vosk",
            thermal_path=self.thermal,
            meminfo_path=self.meminfo,
            uptime_path=self.uptime,
            disk_path="/",
            disk_usage=self.disk_usage,
        )

        self.assertIn("SYSTEM NOMINAL.", text)
        self.assertIn("MASCHINENGEIST SYNCHRONISIERT.", text)
        self.assertIn("KERNTEMPERATUR 55 GRAD.", text)
        self.assertIn("ARBEITSSPEICHER 62 PROZENT FREI.", text)
        self.assertIn("DATENSPEICHER 40 PROZENT FREI.", text)
        self.assertIn("LAUFZEIT 2 Stunden 36 Minuten.", text)
        self.assertIn("OFFLINE SPRACHERKENNUNG AKTIV.", text)
        self.assertTrue(text.endswith("BEFEHL ERWARTET."))

    def test_processing_status_keeps_operator_feedback(self):
        text = build_status_text(
            processing=True,
            stt_provider="vosk",
            thermal_path="/missing/temp",
            meminfo_path="/missing/meminfo",
            uptime_path="/missing/uptime",
            disk_usage=lambda _path: (_ for _ in ()).throw(OSError("missing")),
        )
        self.assertIn("VERARBEITUNGSPROTOKOLL AKTIV.", text)
        self.assertIn("AUFNAHME IN ANALYSE.", text)
        self.assertTrue(text.endswith("BEFEHL IN BEARBEITUNG."))

    def test_missing_optional_sources_do_not_break_status(self):
        text = build_status_text(
            thermal_path="/missing/temp",
            meminfo_path="/missing/meminfo",
            uptime_path="/missing/uptime",
            disk_usage=lambda _path: (_ for _ in ()).throw(OSError("missing")),
        )
        self.assertEqual(
            text,
            "SYSTEM NOMINAL. MASCHINENGEIST SYNCHRONISIERT. "
            "SERVITOR EINHEIT BEREIT. BEFEHL ERWARTET.",
        )


if __name__ == "__main__":
    unittest.main()
