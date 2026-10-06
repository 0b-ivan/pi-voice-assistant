import os
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_effects import (
    SERVITOR_FILTER_GRAPH,
    build_playback_command,
    build_stream_playback_command,
    resolve_voice_profile,
)


class VoiceEffectsTests(unittest.TestCase):
    def test_default_profile_is_normal(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_voice_profile(), "normal")

    def test_invalid_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported TTS_VOICE_PROFILE"):
            resolve_voice_profile("servo-skull")

    def test_normal_profile_uses_aplay(self):
        command = build_playback_command(
            "/tmp/input.wav", "test-device", profile="normal"
        )
        self.assertEqual(
            command,
            ["/usr/bin/aplay", "-q", "-D", "test-device", "/tmp/input.wav"],
        )

    def test_servitor_profile_streams_ffmpeg_directly_to_alsa(self):
        command = build_playback_command(
            "/tmp/input.wav", "test-device", profile="servitor"
        )
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertIn("-filter_complex", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(graph, SERVITOR_FILTER_GRAPH)
        self.assertIn("flanger=delay=8:depth=8:regen=48", graph)
        self.assertIn("speed=1.0", graph)
        self.assertIn("volume=3.45[flange]", graph)
        self.assertIn("chorus=", graph)
        self.assertIn("volume=0.54[main]", graph)
        self.assertIn("volume=2.35[choir]", graph)
        self.assertIn("tremolo=f=13:d=0.94", graph)
        self.assertIn("tremolo=f=42:d=0.55", graph)
        self.assertIn("tremolo=f=17:d=0.82", graph)
        self.assertIn("34|68|102", graph)
        self.assertIn("0.48+0.52*(t*6-floor(t*6))", graph)
        self.assertIn("speed=0.85:type=triangular", graph)
        self.assertIn("volume=0.58[sawphase]", graph)
        self.assertIn("t*96-floor(t*96)", graph)
        self.assertIn("volume=0.52[machinehum]", graph)
        self.assertIn("amix=inputs=11", graph)
        self.assertIn("alimiter=", graph)
        self.assertIn("asetrate=sample_rate=44400", graph)
        self.assertNotIn("areverse", graph)
        self.assertEqual(command[-3:], ["-f", "alsa", "test-device"])


    def test_servitor_stream_reads_raw_pcm_from_stdin(self):
        command = build_stream_playback_command(
            22050, 1, "test-device", profile="servitor"
        )
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertEqual(
            command[4:12],
            ["-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "pipe:0"],
        )
        self.assertIn("-filter_complex", command)
        self.assertEqual(command[-3:], ["-f", "alsa", "test-device"])

    def test_servitor_ffmpeg_path_can_be_overridden(self):
        with patch.dict(os.environ, {"TTS_FFMPEG_BIN": "/custom/ffmpeg"}, clear=True):
            command = build_playback_command(
                "/tmp/input.wav", "test-device", profile="servitor"
            )
        self.assertEqual(command[0], "/custom/ffmpeg")


if __name__ == "__main__":
    unittest.main()
