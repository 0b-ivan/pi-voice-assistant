import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import speak as tts


class SpeakTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _output_file(self):
        fd, name = tempfile.mkstemp(dir=self.tmp.name, suffix=".wav")
        return fd, Path(name)

    def test_empty_input_does_nothing(self):
        with patch("speak.tempfile.mkstemp") as mkstemp, patch(
            "speak.subprocess.run"
        ) as run:
            tts.speak("   ")

        mkstemp.assert_not_called()
        run.assert_not_called()

    def test_runs_piper_then_aplay_with_configured_values(self):
        fd, wav = self._output_file()
        env = {
            "PIPER_MODEL": "/models/test.onnx",
            "PIPER_PYTHON": "/venv/bin/python",
            "TTS_AUDIO_DEVICE": "test-device",
        }

        with patch.dict(os.environ, env, clear=True), patch(
            "speak.tempfile.mkstemp", return_value=(fd, str(wav))
        ), patch("speak.subprocess.run") as run:
            tts.speak("  Hallo Welt  ")

        self.assertEqual(
            run.call_args_list,
            [
                call(
                    [
                        "/venv/bin/python",
                        "-m",
                        "piper",
                        "-m",
                        "/models/test.onnx",
                        "-f",
                        str(wav),
                        "Hallo Welt",
                    ],
                    check=True,
                ),
                call(
                    ["aplay", "-q", "-D", "test-device", str(wav)],
                    check=True,
                ),
            ],
        )
        self.assertFalse(wav.exists())

    def test_servitor_profile_filters_before_playback(self):
        raw_fd, raw = self._output_file()
        effect_fd, effect = self._output_file()
        env = {
            "PIPER_MODEL": "/models/test.onnx",
            "PIPER_PYTHON": "/venv/bin/python",
            "TTS_AUDIO_DEVICE": "test-device",
            "TTS_VOICE_PROFILE": "servitor",
        }

        with patch.dict(os.environ, env, clear=True), patch(
            "speak.tempfile.mkstemp",
            side_effect=[(raw_fd, str(raw)), (effect_fd, str(effect))],
        ), patch("speak.subprocess.run") as run:
            tts.speak("Status")

        self.assertEqual(run.call_count, 3)
        sox = run.call_args_list[1]
        self.assertEqual(
            sox.args[0][:3],
            ["/usr/bin/sox", str(raw), str(effect)],
        )
        self.assertIn("tremolo", sox.args[0])
        self.assertEqual(
            run.call_args_list[2],
            call(
                ["aplay", "-q", "-D", "test-device", str(effect)],
                check=True,
            ),
        )
        self.assertFalse(raw.exists())
        self.assertFalse(effect.exists())

    def test_removes_temporary_file_when_synthesis_fails(self):
        fd, wav = self._output_file()
        error = subprocess.CalledProcessError(1, ["piper"])

        with patch(
            "speak.tempfile.mkstemp", return_value=(fd, str(wav))
        ), patch("speak.subprocess.run", side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                tts.speak("Hallo")

        self.assertFalse(wav.exists())

    def test_removes_temporary_file_when_playback_fails(self):
        fd, wav = self._output_file()
        error = subprocess.CalledProcessError(1, ["aplay"])

        with patch(
            "speak.tempfile.mkstemp", return_value=(fd, str(wav))
        ), patch("speak.subprocess.run", side_effect=[None, error]):
            with self.assertRaises(subprocess.CalledProcessError):
                tts.speak("Hallo")

        self.assertFalse(wav.exists())


if __name__ == "__main__":
    unittest.main()
