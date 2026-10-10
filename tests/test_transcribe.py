from array import array
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
import unittest.mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import transcribe as stt
from transcribe import TranscriptionError


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name) / "capture.wav"
        self.audio.write_bytes(b"RIFF-test-audio")

    def test_default_provider_is_vosk_and_needs_no_api_key(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "transcribe.transcribe_vosk", return_value="lokal"
        ) as local:
            result = stt.transcribe_with_provider(self.audio)
        self.assertEqual(result, ("lokal", "vosk"))
        local.assert_called_once_with(self.audio)

    def test_explicit_vosk_provider_is_accepted(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "vosk"}, clear=True), patch(
            "transcribe.transcribe_vosk", return_value="lokal"
        ) as local:
            self.assertEqual(stt.transcribe_with_provider(self.audio), ("lokal", "vosk"))
        local.assert_called_once_with(self.audio)

    def test_cloud_stt_modes_are_rejected(self):
        for provider in ("openrouter", "auto", "magic"):
            with self.subTest(provider=provider), patch.dict(
                "os.environ", {"STT_PROVIDER": provider}, clear=True
            ):
                with self.assertRaisesRegex(
                    TranscriptionError, "STT_PROVIDER must be vosk"
                ):
                    stt.transcribe_with_provider(self.audio)


class TermTests(unittest.TestCase):
    def test_kogitator_mishearings_are_fixed(self):
        # Vosk outputs of 10.10.2026 (operator and system test).
        for heard in ("was hat der cookie tat wir für eine hardware",
                      "was hat der cookie tattoo für einen hardware",
                      "verbindung zum cookie tatort verloren"):
            with self.subTest(heard=heard):
                self.assertIn("kogitator", stt.fix_terms(heard))
        for text in ("ich esse einen cookie", "die tat war schlimm", "wie geht es dir"):
            with self.subTest(text=text):
                self.assertEqual(stt.fix_terms(text), text)

    def test_vosk_results_pass_through_the_fix(self):
        self.assertEqual(stt._result_text('{"text": "wie geht es dem cookie tatort"}', "final"),
                         "wie geht es dem kogitator")


class LiveVoskTests(unittest.TestCase):
    def test_incremental_recognizer_collects_segments_and_final_text(self):
        class FakeRecognizer:
            def __init__(self):
                self.calls = 0

            def AcceptWaveform(self, _pcm):
                self.calls += 1
                return self.calls == 1

            def Result(self):
                return json.dumps({"text": "eins"})

            def FinalResult(self):
                return json.dumps({"text": "zwei"})

        class FakeVosk:
            @staticmethod
            def KaldiRecognizer(_model, rate):
                self_rate.append(rate)
                return recognizer

        recognizer = FakeRecognizer()
        self_rate = []
        with patch.dict("os.environ", {"STT_PROVIDER": "vosk"}, clear=True), patch(
            "transcribe._load_vosk_model", return_value=object()
        ), patch("transcribe._vosk_module", return_value=FakeVosk):
            live = stt.LiveVoskRecognizer()
            live.accept_pcm(b"\0\0" * 160)
            live.accept_pcm(b"\0\0" * 160)
            self.assertEqual(live.finish(), "eins zwei")

        self.assertEqual(self_rate, [16000])

    def test_incremental_recognizer_rejects_unaligned_pcm(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "vosk"}, clear=True), patch(
            "transcribe._load_vosk_model", return_value=object()
        ), patch("transcribe._vosk_module") as vosk:
            vosk.return_value.KaldiRecognizer.return_value = unittest.mock.Mock()
            live = stt.LiveVoskRecognizer()
            with self.assertRaisesRegex(TranscriptionError, "aligned 16-bit PCM"):
                live.accept_pcm(b"\0")


class VoskErrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.model = Path(self.tmp.name) / "model"
        self.model.mkdir()
        self.audio = Path(self.tmp.name) / "capture.wav"
        with wave.open(str(self.audio), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 1600)

    def test_plain_exception_from_model_is_normalized(self):
        class FakeVosk:
            @staticmethod
            def SetLogLevel(_level):
                pass

            @staticmethod
            def Model(_path):
                raise Exception("Failed to create a model")

        env = {"STT_PROVIDER": "vosk", "VOSK_MODEL_PATH": str(self.model)}
        with patch.dict("os.environ", env, clear=True), patch(
            "transcribe._vosk_module", return_value=FakeVosk
        ), patch.object(stt, "_VOSK_MODEL", None), patch.object(
            stt, "_VOSK_MODEL_PATH", None
        ):
            with self.assertRaisesRegex(
                TranscriptionError, "failed to load Vosk model.*Failed to create a model"
            ):
                stt._load_vosk_model()

    def test_plain_exception_from_recognizer_is_normalized(self):
        class FakeVosk:
            @staticmethod
            def KaldiRecognizer(_model, _rate):
                raise Exception("native recognizer failure")

        with patch.dict("os.environ", {"STT_PROVIDER": "vosk"}, clear=True), patch(
            "transcribe._load_vosk_model", return_value=object()
        ), patch("transcribe._vosk_module", return_value=FakeVosk):
            with self.assertRaisesRegex(
                TranscriptionError, "Vosk transcription failed: native recognizer failure"
            ):
                stt.transcribe_vosk(self.audio)


class VoskAudioTests(unittest.TestCase):
    def test_48khz_stereo_is_downmixed_and_resampled_to_16khz_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            samples = array(
                "h",
                [
                    300,
                    300,
                    600,
                    600,
                    900,
                    900,
                    1200,
                    1200,
                    1500,
                    1500,
                    1800,
                    1800,
                ],
            )
            if sys.byteorder != "little":
                samples.byteswap()
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(2)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(samples.tobytes())

            payload = b"".join(stt._iter_vosk_pcm(path))
            converted = array("h")
            converted.frombytes(payload)
            if sys.byteorder != "little":
                converted.byteswap()

        self.assertEqual(list(converted), [600, 1500])


if __name__ == "__main__":
    unittest.main()
