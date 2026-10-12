"""Offline Wi-Fi QR code screen: bounded credentials, on-device rendering."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import display


class QRDisplayTests(unittest.TestCase):
    def test_wifi_qr_payload_and_special_character_escaping(self):
        self.assertEqual(display.wifi_qr_payload("Proximus-Setup", "Test-Password1234"),
                         "WIFI:T:WPA;S:Proximus-Setup;P:Test-Password1234;;")
        self.assertEqual(display.wifi_qr_payload("S;:Test", 'pw\\",,Test'),
                         'WIFI:T:WPA;S:S\\;\\:Test;P:pw\\\\\\"\\,\\,Test;;')

    def test_read_wifi_qr_only_accepts_valid_ephemeral_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "view.json"
            valid = dict(page="wifi_qr", ssid="Proximus-Setup",
                         password="TestPassword1234", title="WLAN VERBINDEN")
            p.write_text(json.dumps(valid))
            self.assertEqual(display.read_people(p)['password'], "TestPassword1234")
            self.assertNotIn("TestPassword1234", display.read_status.__doc__ or "")
            for name, value in [
                ("ssid", "Other-AP"), ("password", "short"),
                ("password", "a" * 64), ("password", "line\nbreak"),
                ("password", None),
            ]:
                edited = dict(valid)
                edited[name] = value
                p.write_text(json.dumps(edited))
                self.assertIsNone(display.read_people(p), (name, value))
            p.write_text("{broken")
            self.assertIsNone(display.read_people(p))

    def test_qr_renders_on_240px_display_without_remote_service(self):
        import qrcode
        class StubDisplay:
            def __init__(self):
                self.frame = None
            def image(self, frame, rotation):
                self.frame = frame
                self.rotation = rotation

        stub = StubDisplay()
        display.render_people(stub, {
            "page": "wifi_qr", "ssid": "Proximus-Setup",
            "password": "Safe-Password1234",
        })
        self.assertEqual(stub.frame.size, (240, 240))
        self.assertEqual(stub.rotation, 180)
        region = stub.frame.crop((15, 44, 225, 209))
        colors = region.getcolors(210 * 165 + 1)
        self.assertIsNotNone(colors)
        self.assertTrue(any(rgb == (0, 0, 0) for _, rgb in colors))
        self.assertTrue(any(rgb == (255, 255, 255) for _, rgb in colors))
        self.assertIsInstance(qrcode.QRCode().get_matrix(), list)

if __name__ == "__main__":
    unittest.main()
