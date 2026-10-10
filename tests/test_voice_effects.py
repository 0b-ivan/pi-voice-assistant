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
        self.assertIn("asplit=6", graph)
        self.assertIn("flanger=delay=6:depth=6:regen=38", graph)
        self.assertEqual(graph.count("flanger="), 1)
        self.assertEqual(graph.count("aphaser="), 2)
        self.assertIn("chorus=0.42:0.52", graph)
        self.assertIn("tremolo=f=17:d=0.82", graph)
        self.assertIn("34|68|102", graph)
        self.assertIn("0.48+0.52*(t*6-floor(t*6))", graph)
        self.assertIn("speed=0.52:type=triangular", graph)
        self.assertIn("t*220-floor(t*220)", graph)
        self.assertIn("t*440-floor(t*440)", graph)
        self.assertNotIn("aevalsrc=", graph)
        self.assertIn("[machine0]aeval=", graph)
        self.assertIn("speed=0.46:type=triangular", graph)
        self.assertIn("volume=1.02[machinehum]", graph)
        self.assertIn("amix=inputs=6", graph)
        self.assertIn("alimiter=", graph)
        self.assertIn("aresample=24000", graph)
        self.assertIn("aresample=48000[out]", graph)
        self.assertIn("asetrate=sample_rate=22200", graph)
        self.assertNotIn("areverse", graph)
        self.assertNotIn("[doppler", graph)
        self.assertNotIn("[ring", graph)
        self.assertEqual(command[-3:], ["-f", "alsa", "test-device"])


    def test_servitor_stream_reads_raw_pcm_from_stdin(self):
        command = build_stream_playback_command(
            22050, 1, "test-device", profile="servitor"
        )
        self.assertEqual(command[0], "/usr/bin/ffmpeg")
        self.assertEqual(
            command[command.index("-f"):command.index("-f") + 8],
            ["-f", "s16le", "-ar", "22050", "-ac", "1", "-i", "pipe:0"],
        )
        self.assertIn("-filter_complex", command)
        self.assertEqual(command[-3:], ["-f", "alsa", "test-device"])

    def test_natural_effect_replaces_machine_layers(self):
        from voice_effects import NATURAL_FILTER_GRAPH, build_render_command, voice_effect
        stream = build_stream_playback_command(22050, 1, "test-device", effect="natural")
        graph = stream[stream.index("-filter_complex") + 1]
        self.assertEqual(graph, NATURAL_FILTER_GRAPH)
        for layer in ("flanger", "chorus", "tremolo", "aphaser"):
            self.assertNotIn(layer, graph)
        self.assertTrue(graph.endswith("aresample=48000[out]"))
        render = build_render_command("in.wav", "out.wav", effect="natural")
        self.assertEqual(render[render.index("-filter_complex") + 1], NATURAL_FILTER_GRAPH)
        file_play = build_playback_command("in.wav", "dev", profile="servitor", effect="natural")
        self.assertIn(NATURAL_FILTER_GRAPH, file_play)
        self.assertEqual(voice_effect("NATURAL"), "natural")
        self.assertEqual(voice_effect("laut"), "servitor")
        default = build_render_command("in.wav", "out.wav")
        self.assertIn("flanger", default[default.index("-filter_complex") + 1])

    def test_rvc_effect_keeps_the_converted_pitch(self):
        from voice_effects import RVC_FILTER_GRAPH, build_render_command, voice_effect
        render = build_render_command("in.wav", "out.wav", effect="rvc")
        graph = render[render.index("-filter_complex") + 1]
        self.assertEqual(graph, RVC_FILTER_GRAPH)
        self.assertNotIn("asetrate", graph)  # RVC already set the pitch
        self.assertTrue(graph.endswith("aresample=48000[out]"))
        self.assertEqual(voice_effect("rvc"), "servitor")  # never a menu value

    def test_servitor_ffmpeg_path_can_be_overridden(self):
        with patch.dict(os.environ, {"TTS_FFMPEG_BIN": "/custom/ffmpeg"}, clear=True):
            command = build_playback_command(
                "/tmp/input.wav", "test-device", profile="servitor"
            )
        self.assertEqual(command[0], "/custom/ffmpeg")


if __name__ == "__main__":
    unittest.main()
