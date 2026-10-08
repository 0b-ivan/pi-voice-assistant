import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import maintenance  # noqa: E402


class ModuleTests(unittest.TestCase):
    def test_commands(self):
        cases = {
            "wartungsmodus": "enter", "aktiviere den wartungsmodus": "enter",
            "aktualisiere den pi": "update_pi", "installiere die updates": "update_pi",
            "aktualisiere den server": "update_server", "starte den server neu": "reboot_server",
            "pi neu starten": "reboot_pi", "wartung beenden": "exit",
            "gibt es updates": None, "wie spät ist es": None,
        }
        for text, expected in cases.items():
            self.assertEqual(maintenance.command(text), expected, text)

    def test_confirmation_expires(self):
        now = [100.0]
        mode = maintenance.Mode(clock=lambda: now[0])
        mode.enter()
        mode.ask("update_pi")
        now[0] += maintenance.CONFIRM_SECONDS + 1
        self.assertIsNone(mode.take_confirmed())
        mode.ask("reboot_pi")
        self.assertEqual(mode.take_confirmed(), "reboot_pi")
        self.assertIsNone(mode.take_confirmed())

    def test_request_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "requests").mkdir()
            maintenance.request("update", tmp)
            self.assertTrue((Path(tmp) / "requests" / "update").exists())
            with self.assertRaises(ValueError):
                maintenance.request("rm -rf", tmp)
            (Path(tmp) / "status.json").write_text(json.dumps(
                dict(action="update", state="done", at=5, upgraded=12, reboot=True, code=0, x="y")))
            self.assertEqual(maintenance.status(tmp), dict(
                action="update", state="done", at=5, upgraded=12, reboot=True, code=0))
        self.assertIn("12 Pakete installiert. Neustart empfohlen",
                      maintenance.result_text("pi", dict(state="done", upgraded=12, reboot=True)))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        import ptt
        self.ptt = ptt
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "PTT_DISPLAY_STATUS_PATH": str(Path(self.tmp.name) / "status.json"),
            "PTT_DISPLAY_EVENT_PATH": str(Path(self.tmp.name) / "event.json"),
            "PTT_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        context = redirect_stdout(StringIO())
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.speech.active = False
        self.speech.synthesizing = False
        self.speech.poll.return_value = None
        self.c = ptt.VoiceController(self.recorder, self.speech, .04, 30)
        self.said = []
        self.c._say = self.said.append

    def status(self):
        return json.loads((Path(self.tmp.name) / "status.json").read_text())

    def test_action_needs_mode_and_button_confirmation(self):
        self.assertIn("Erst Wartungsmodus", self.c._maintenance_op("update_pi", speak=False))
        self.c._maintenance_op("enter")
        self.assertEqual(self.status()["maint"], "on")
        self.c._maintenance_buttons(confirm=True, cancel=False)      # E on "Pi aktualisieren"
        self.assertEqual(self.c.maint.pending, "update_pi")
        self.assertEqual(self.status()["maint_confirm"], "update_pi")
        self.assertIn("Bestätigen mit Taste E", self.said[-1])
        with patch.object(maintenance, "request") as request:
            self.c._maintenance_buttons(confirm=True, cancel=False)  # E again: run
        request.assert_called_once_with("update")
        self.assertIn("pi", self.c.maint_jobs)
        self.assertNotIn("maint_confirm", self.status())

    def test_b_cancels_then_leaves(self):
        self.c._maintenance_op("enter")
        self.c._maintenance_op("reboot_server", speak=False)
        with patch.object(maintenance, "request_server") as server:
            self.c._maintenance_buttons(confirm=False, cancel=True)
            self.assertIsNone(self.c.maint.pending)
            self.assertTrue(self.c.maint.active)
            self.c._maintenance_buttons(confirm=False, cancel=True)
        server.assert_not_called()
        self.assertFalse(self.c.maint.active)

    def test_server_unreachable_is_reported(self):
        self.c._maintenance_op("enter")
        self.c._maintenance_op("update_server", speak=False)
        with patch.object(maintenance, "request_server", return_value="unreachable"):
            self.c._maintenance_buttons(confirm=True, cancel=False)
        self.assertIn("nur im lokalen netz", self.said[-1].lower())
        self.assertEqual(self.c.maint_jobs, {})

    def test_result_is_announced_once(self):
        self.c.maint_jobs["pi"] = 1000.0
        done = dict(action="update", state="done", at=1200, upgraded=3, reboot=False)
        with patch.object(maintenance, "status", return_value=done), \
                patch.object(maintenance, "server_status", return_value=None):
            first = self.c._check_maintenance()
            second = self.c._check_maintenance()
        self.assertEqual(first, ["Pi: Aktualisierung abgeschlossen, 3 Pakete installiert."])
        self.assertEqual(second, [])

    def test_display_renders_confirmation(self):
        import display

        class Capture:
            def image(self, image, rotation=0):
                self.picture = image

        capture = Capture()
        display.render_maintenance(capture, dict(maint="on", maint_index=1,
                                                 maint_confirm="update_server", upd_pi=0,
                                                 upd_srv=40, upd_srv_sec=21))
        self.assertIsNotNone(capture.picture.getbbox())


class ServerTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
        import servitor_server as ss
        import http.client
        import threading
        self.http = http.client
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config({"SERVITOR_API_TOKEN": "x" * 40, "SERVITOR_WORKDIR": self.tmp.name,
                            "SERVITOR_TRUSTED_PROXIES": "10.9.9.9"})
        self.service = ss.Service(config, Mock())
        self.service.maintenance_dir = Path(self.tmp.name) / "maint"
        self.server = ss.Server(("127.0.0.1", 0), ss.Handler)
        self.server.service = self.service
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def post(self, body, token="x" * 40):
        conn = self.http.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        conn.request("POST", "/v1/maintenance", body=json.dumps(body),
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        conn.close()
        return response.status

    def test_maintenance_endpoint(self):
        self.assertEqual(self.post(dict(action="update"), token="wrong" * 10), 401)
        self.assertEqual(self.post(dict(action="update")), 503)    # worker not installed
        (self.service.maintenance_dir / "requests").mkdir(parents=True)
        self.assertEqual(self.post(dict(action="shell")), 400)
        self.assertEqual(self.post(dict(action="update")), 202)
        self.assertTrue((self.service.maintenance_dir / "requests" / "update").exists())
        self.assertEqual(self.post(dict(action="reboot")), 429)    # at most every 5 min
        self.service.config.trusted_proxies = {"127.0.0.1"}        # as if through the tunnel
        self.service.maintenance_at = None
        self.assertEqual(self.post(dict(action="reboot")), 403)


if __name__ == "__main__":
    unittest.main()
