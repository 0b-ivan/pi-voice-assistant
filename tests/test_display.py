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
            "transcript": "DENKEN",
            "llm_start": "DENKEN",
            "llm_response": "SYNTHESE",
            "speech_started": "SYNTHESE",
            "speech_finished": "BEREIT",
            "stt_error": "FEHLER",
            "llm_error": "FEHLER",
        }
        for event_name, state in expected.items():
            with self.subTest(event=event_name):
                self.assertEqual(display.voice_state_for_event(event_name), state)

    def test_loading_events_identify_startup(self):
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


class DetailedProgressTests(unittest.TestCase):
    def test_real_phase_replaces_general_speech_start(self):
        event = dict(event='speech_started', timestamp=10)
        phase = dict(stage='tts', metric='dsp_render', timestamp=11)
        self.assertEqual(display.screen_details('SYNTHESE', event, phase)[:4],
                         ('RENDERN', 'Audioeffekte berechnen', 'sliders', 4))

    def test_later_cancel_or_error_supersedes_stale_phase(self):
        phase = dict(stage='tts', metric='synthesis', timestamp=10)
        event = dict(event='cancelled', timestamp=11)
        self.assertEqual(display.screen_details('BEREIT', event, phase)[0], 'BEREIT')
        phase['timestamp'] = 12
        self.assertEqual(display.screen_details('FEHLER', event, phase)[0], 'FEHLER')

    def test_unknown_or_invalid_progress_is_ignored(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'progress.json'
            for value in ([], None, dict(stage='tts', metric='secret', timestamp=1),
                          dict(stage='tts', metric='synthesis', timestamp=float('nan'))):
                path.write_text(json.dumps(value))
                self.assertIsNone(display.read_progress(path))

    def test_phase_publishes_at_start_and_keeps_no_user_content(self):
        import json
        from runtime_metrics import phase
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PTT_RUNTIME_DIR':tmp}):
            with phase('tts', 'synthesis'):
                payload = json.loads((Path(tmp)/'display-progress.json').read_text())
                self.assertEqual(payload['metric'], 'synthesis')
                self.assertEqual(set(payload), {'version','stage','metric','timestamp'})

    def test_standby_model_load_cannot_replace_recording(self):
        from runtime_metrics import display_progress
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PTT_RUNTIME_DIR':tmp}):
            display_progress('stt', 'model_load')
            self.assertFalse((Path(tmp)/'display-progress.json').exists())


class PartialFrameTests(unittest.TestCase):
    def test_partial_frames_reconstruct_rotated_screen_exactly(self):
        from PIL import Image, ImageChops
        class Hardware:
            def __init__(self):
                self.canvas = Image.new('RGB', (240,240))
                self.sizes = []
            def image(self, image, rotation=0, x=0, y=0):
                self.sizes.append(image.size)
                self.canvas.paste(image, (x,y))
        hardware = Hardware()
        output = display.PartialDisplay(hardware)
        first = Image.new('RGB',(240,240),'black')
        output.image(first,180)
        second = first.copy()
        second.paste((255,170,0),(12,80,52,120))
        output.image(second,180)
        self.assertIsNone(ImageChops.difference(hardware.canvas,second.rotate(180)).getbbox())
        self.assertEqual(hardware.sizes,[(240,240),(40,40)])
        output.image(second,180)
        self.assertEqual(len(hardware.sizes),2)

    def test_failed_transfer_is_retried(self):
        from PIL import Image
        from unittest.mock import Mock
        hardware = Mock()
        hardware.image.side_effect = [OSError('SPI busy'),None]
        output = display.PartialDisplay(hardware)
        frame = Image.new('RGB',(240,240),'black')
        with self.assertRaises(OSError):output.image(frame,180)
        output.image(frame,180)
        self.assertEqual(hardware.image.call_count,2)


if __name__ == "__main__":
    unittest.main()
