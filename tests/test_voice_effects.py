import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from voice_effects import apply_voice_profile, build_sox_command, resolve_voice_profile


class VoiceEffectsTests(unittest.TestCase):
    def test_default_profile_is_normal(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_voice_profile(), "normal")

    def test_invalid_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported TTS_VOICE_PROFILE"):
            resolve_voice_profile("servo-skull")

    def test_normal_profile_skips_sox(self):
        runner = Mock()
        result = apply_voice_profile(
            "/tmp/input.wav", "/tmp/output.wav", profile="normal", runner=runner
        )
        self.assertEqual(result, Path("/tmp/input.wav"))
        runner.assert_not_called()

    def test_servitor_profile_builds_lightweight_sox_chain(self):
        command = build_sox_command(
            "/tmp/input.wav",
            "/tmp/output.wav",
            profile="servitor",
        )
        self.assertEqual(
            command[:3],
            ["/usr/bin/sox", "/tmp/input.wav", "/tmp/output.wav"],
        )
        self.assertIn("pitch", command)
        self.assertIn("-300", command)
        self.assertIn("highpass", command)
        self.assertIn("lowpass", command)
        self.assertIn("overdrive", command)
        self.assertIn("tremolo", command)
        self.assertIn("reverb", command)

    def test_servitor_profile_runs_sox_without_shell(self):
        runner = Mock()
        result = apply_voice_profile(
            "/tmp/input.wav",
            "/tmp/output.wav",
            profile="servitor",
            runner=runner,
        )
        self.assertEqual(result, Path("/tmp/output.wav"))
        runner.assert_called_once()
        kwargs = runner.call_args.kwargs
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
