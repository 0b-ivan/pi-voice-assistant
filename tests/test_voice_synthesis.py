import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_controls import _synthesize_voice


class VoiceSynthesisTests(unittest.TestCase):
    def test_servitor_uses_command_cadence_and_sentence_silence(self):
        configs = []

        class FakeSynthesisConfig:
            def __init__(self, **kwargs):
                configs.append(kwargs)
                self.kwargs = kwargs

        chunk1 = SimpleNamespace(
            sample_rate=16000,
            sample_width=2,
            sample_channels=1,
            audio_int16_bytes=b"\x01\x00" * 2,
        )
        chunk2 = SimpleNamespace(
            sample_rate=16000,
            sample_width=2,
            sample_channels=1,
            audio_int16_bytes=b"\x02\x00" * 2,
        )
        voice = Mock()
        voice.config.num_speakers = 8
        voice.synthesize.return_value = [chunk1, chunk2]
        fake_piper = ModuleType("piper")
        fake_piper.__path__ = []
        fake_piper_config = ModuleType("piper.config")
        fake_piper_config.SynthesisConfig = FakeSynthesisConfig

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.wav"
            with patch.dict(os.environ, {}, clear=True), patch.dict(
                sys.modules, {"piper": fake_piper, "piper.config": fake_piper_config}
            ):
                with wave.open(str(path), "wb") as audio:
                    _synthesize_voice(voice, "Eins. Zwei.", audio, "servitor")

            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getframerate(), 16000)
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getsampwidth(), 2)
                # 2 frames + 320 ms silence + 2 frames.
                self.assertEqual(audio.getnframes(), 5124)

        self.assertEqual(
            configs,
            [{
                "speaker_id": 4,
                "length_scale": 1.10,
                "noise_scale": 0.30,
                "noise_w_scale": 0.25,
            }],
        )
        voice.synthesize.assert_called_once()
        self.assertEqual(voice.synthesize.call_args.args[0], "Eins. Zwei.")

    def test_normal_profile_keeps_default_piper_path(self):
        voice = Mock()

        def write_audio(_text, audio):
            audio.setframerate(16000)
            audio.setsampwidth(2)
            audio.setnchannels(1)
            audio.writeframes(bytes(4))

        voice.synthesize_wav.side_effect = write_audio
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "normal.wav"
            with wave.open(str(path), "wb") as audio:
                _synthesize_voice(voice, "Hallo", audio, "normal")
            with wave.open(str(path), "rb") as audio:
                self.assertEqual(audio.getnframes(), 2)
        voice.synthesize_wav.assert_called_once()


if __name__ == "__main__":
    unittest.main()
