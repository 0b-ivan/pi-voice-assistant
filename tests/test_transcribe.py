import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from transcribe import TranscriptionError, transcribe


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


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


if __name__ == "__main__":
    unittest.main()
