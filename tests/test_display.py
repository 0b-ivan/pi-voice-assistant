import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import display


class DisplayStatusTests(unittest.TestCase):
    def ready_states(self, **overrides):
        states = {
            "spi": True,
            "audio": True,
            "network": True,
            "vosk": True,
            "tts": True,
            "voice": True,
        }
        states.update(overrides)
        return states

    def test_network_is_informational_for_ready_gate(self):
        states = self.ready_states(network=False)

        self.assertTrue(display.is_ready(states))
        self.assertEqual(display.footer_status(states)[0], "SYSTEM READY")

    def test_missing_critical_state_keeps_booting(self):
        states = self.ready_states(voice=False)

        self.assertFalse(display.is_ready(states))
        self.assertEqual(display.footer_status(states)[0], "BOOTING ...")

    def test_network_probe_timeout_returns_false(self):
        timeout = subprocess.TimeoutExpired(["ip"], 2)
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", side_effect=timeout),
        ):
            self.assertFalse(display.network_ok())

    def test_network_probe_execution_error_returns_false(self):
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", side_effect=OSError("broken")),
        ):
            self.assertFalse(display.network_ok())

    def test_network_probe_accepts_non_loopback_ipv4(self):
        result = SimpleNamespace(
            stdout=(
                "lo               UNKNOWN        127.0.0.1/8\n"
                "wlan0            UP             172.22.9.128/24\n"
            )
        )
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", return_value=result),
        ):
            self.assertTrue(display.network_ok())

    def test_voice_probe_timeout_returns_false(self):
        timeout = subprocess.TimeoutExpired(["systemctl"], 2)
        with patch.object(display.subprocess, "run", side_effect=timeout):
            self.assertFalse(display.service_ok())

    def test_voice_probe_execution_error_returns_false(self):
        with patch.object(display.subprocess, "run", side_effect=OSError("broken")):
            self.assertFalse(display.service_ok())

    def test_voice_probe_active_returns_true(self):
        with patch.object(
            display.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ):
            self.assertTrue(display.service_ok())

    def test_servitor_profile_is_normalized_before_model_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vosk = root / "vosk"
            normal = root / "normal.onnx"
            servitor = root / "servitor.onnx"
            env_file = root / "voice.env"

            vosk.mkdir()
            servitor.write_bytes(b"model")
            env_file.write_text(
                "\n".join(
                    (
                        f"VOSK_MODEL_PATH={vosk}",
                        f"PIPER_MODEL={normal}",
                        f"TTS_SERVITOR_MODEL={servitor}",
                        "TTS_VOICE_PROFILE= SERVITOR ",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(display, "ENV_FILE", env_file):
                self.assertEqual(display.models_ok(), (True, True))

    def test_non_servitor_profile_uses_normal_model_like_voice_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vosk = root / "vosk"
            normal = root / "normal.onnx"
            servitor = root / "servitor.onnx"
            env_file = root / "voice.env"

            vosk.mkdir()
            normal.write_bytes(b"model")
            env_file.write_text(
                "\n".join(
                    (
                        f"VOSK_MODEL_PATH={vosk}",
                        f"PIPER_MODEL={normal}",
                        f"TTS_SERVITOR_MODEL={servitor}",
                        "TTS_VOICE_PROFILE=unexpected",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(display, "ENV_FILE", env_file):
                self.assertEqual(display.models_ok(), (True, True))



class DisplayVoiceEventTests(unittest.TestCase):
    def test_runtime_event_mapping(self):
        expected = {
            "waiting_for_release": "BEREIT",
            "recording": "ZUHÖREN",
            "processing": "VERSTEHEN",
            "transcript": "BEREIT",
            "speech_started": "SPRECHEN",
            "speech_finished": "BEREIT",
            "stt_error": "FEHLER",
        }
        for event_name, state in expected.items():
            with self.subTest(event=event_name):
                self.assertEqual(display.voice_state_for_event(event_name), state)

    def test_loading_events_keep_boot_screen(self):
        self.assertEqual(display.voice_state_for_event("stt_loading"), "STARTET")
        self.assertEqual(display.voice_state_for_event("tts_loading"), "STARTET")

    def test_irrelevant_event_has_no_runtime_state(self):
        self.assertIsNone(display.voice_state_for_event("volume"))

    def test_read_voice_event_accepts_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(
                '{"version":1,"event":"recording","timestamp":12.5}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                display.read_voice_event(path),
                {
                    "event": "recording",
                    "timestamp": 12.5,
                    "error_timestamp": None,
                },
            )

    def test_read_voice_event_rejects_malformed_or_incomplete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('{"event":"recording"}', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('null', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('[]', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('"recording"', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))

    def test_read_voice_event_preserves_error_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(
                '{"version":1,"event":"waiting_for_release",'
                '"timestamp":101.0,"error_timestamp":100.0}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                display.read_voice_event(path),
                {
                    "event": "waiting_for_release",
                    "timestamp": 101.0,
                    "error_timestamp": 100.0,
                },
            )


if __name__ == "__main__":
    unittest.main()
