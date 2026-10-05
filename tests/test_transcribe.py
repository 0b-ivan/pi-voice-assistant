from array import array
import base64
import http.client
import json
import urllib.error
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import transcribe as stt
from transcribe import TranscriptionError, transcribe


class FakeResponse:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        if self.error is not None:
            raise self.error
        return json.dumps(self.payload).encode("utf-8")


class BrokenErrorBody:
    def read(self):
        raise http.client.IncompleteRead(b'{"error":')

    def close(self):
        pass


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name) / "capture.wav"
        self.audio.write_bytes(b"RIFF-test-audio")

    def test_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(TranscriptionError, "OPENROUTER_API_KEY"):
                transcribe(self.audio)

    def test_posts_base64_wav_and_returns_text(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"text": "Hallo, das ist ein Test."})

        env = {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_STT_MODEL": "test-model",
            "OPENROUTER_STT_LANGUAGE": "de",
            "OPENROUTER_STT_TIMEOUT_SECONDS": "12",
        }
        with patch.dict("os.environ", env, clear=True), patch(
            "transcribe.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            text = transcribe(self.audio)

        self.assertEqual(text, "Hallo, das ist ein Test.")
        self.assertEqual(captured["timeout"], 12.0)
        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["language"], "de")
        self.assertEqual(payload["input_audio"]["format"], "wav")
        self.assertEqual(
            base64.b64decode(payload["input_audio"]["data"]),
            self.audio.read_bytes(),
        )

    def test_rejects_empty_transcript(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True), patch(
            "transcribe.urllib.request.urlopen",
            return_value=FakeResponse({"text": "   "}),
        ):
            with self.assertRaisesRegex(TranscriptionError, "no transcript"):
                transcribe(self.audio)

    def test_incomplete_success_response_is_normalized(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True), patch(
            "transcribe.urllib.request.urlopen",
            return_value=FakeResponse(error=http.client.IncompleteRead(b'{"text":')),
        ):
            with self.assertRaisesRegex(TranscriptionError, "incomplete OpenRouter response"):
                transcribe(self.audio)

    def test_incomplete_http_error_body_is_normalized(self):
        error = urllib.error.HTTPError(
            url="https://openrouter.ai/api/v1/audio/transcriptions",
            code=502,
            msg="Bad Gateway",
            hdrs=None,
            fp=BrokenErrorBody(),
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}, clear=True), patch(
            "transcribe.urllib.request.urlopen",
            side_effect=error,
        ):
            with self.assertRaisesRegex(TranscriptionError, "incomplete error response"):
                transcribe(self.audio)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.audio = Path(self.tmp.name) / "capture.wav"
        self.audio.write_bytes(b"RIFF-test-audio")

    def test_default_provider_remains_openrouter(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "transcribe.transcribe_openrouter", return_value="cloud"
        ) as cloud, patch("transcribe.transcribe_vosk") as local:
            result = stt.transcribe_with_provider(self.audio)
        self.assertEqual(result, ("cloud", "openrouter"))
        cloud.assert_called_once()
        local.assert_not_called()

    def test_vosk_provider_bypasses_openrouter(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "vosk"}, clear=True), patch(
            "transcribe.transcribe_vosk", return_value="lokal"
        ) as local, patch("transcribe.transcribe_openrouter") as cloud:
            result = stt.transcribe_with_provider(self.audio)
        self.assertEqual(result, ("lokal", "vosk"))
        local.assert_called_once()
        cloud.assert_not_called()

    def test_auto_prefers_openrouter(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "auto"}, clear=True), patch(
            "transcribe.transcribe_openrouter", return_value="cloud"
        ) as cloud, patch("transcribe.transcribe_vosk") as local:
            result = stt.transcribe_with_provider(self.audio)
        self.assertEqual(result, ("cloud", "openrouter"))
        cloud.assert_called_once()
        local.assert_not_called()

    def test_auto_falls_back_to_vosk(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "auto"}, clear=True), patch(
            "transcribe.transcribe_openrouter",
            side_effect=TranscriptionError("network offline"),
        ), patch("transcribe.transcribe_vosk", return_value="offline text") as local:
            result = stt.transcribe_with_provider(self.audio)
        self.assertEqual(result, ("offline text", "vosk"))
        local.assert_called_once()

    def test_auto_reports_both_failures(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "auto"}, clear=True), patch(
            "transcribe.transcribe_openrouter",
            side_effect=TranscriptionError("network offline"),
        ), patch(
            "transcribe.transcribe_vosk",
            side_effect=TranscriptionError("model missing"),
        ):
            with self.assertRaisesRegex(
                TranscriptionError, "OpenRouter: network offline; Vosk: model missing"
            ):
                stt.transcribe_with_provider(self.audio)

    def test_invalid_provider_is_rejected(self):
        with patch.dict("os.environ", {"STT_PROVIDER": "magic"}, clear=True):
            with self.assertRaisesRegex(TranscriptionError, "STT_PROVIDER"):
                stt.transcribe_with_provider(self.audio)


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

        env = {"VOSK_MODEL_PATH": str(self.model)}
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

        with patch("transcribe._load_vosk_model", return_value=object()), patch(
            "transcribe._vosk_module", return_value=FakeVosk
        ):
            with self.assertRaisesRegex(
                TranscriptionError, "Vosk transcription failed: native recognizer failure"
            ):
                stt.transcribe_vosk(self.audio)


class VoskAudioTests(unittest.TestCase):
    def test_48khz_stereo_is_downmixed_and_resampled_to_16khz_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            samples = array("h", [
                300, 300, 600, 600, 900, 900,
                1200, 1200, 1500, 1500, 1800, 1800,
            ])
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
