import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import llm


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LLMTests(unittest.TestCase):
    def base_env(self):
        return {
            "OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_LLM_MODEL": "openai/gpt-5.4-mini",
            "OPENROUTER_LLM_TIMEOUT_SECONDS": "7.5",
            "OPENROUTER_LLM_MAX_TOKENS": "120",
        }

    def test_missing_api_key_fails_before_network(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("llm.urllib.request.urlopen") as urlopen,
        ):
            with self.assertRaisesRegex(llm.LLMError, "OPENROUTER_API_KEY"):
                llm.generate_reply("Status?")
        urlopen.assert_not_called()

    def test_non_streaming_request_contains_servitor_prompt(self):
        response = FakeResponse(
            {"choices": [{"message": {"content": "SYSTEM NOMINAL."}}]}
        )
        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch("llm.urllib.request.urlopen", return_value=response) as urlopen,
        ):
            text, model = llm.generate_reply("Wie ist dein Status?")

        self.assertEqual(text, "SYSTEM NOMINAL.")
        self.assertEqual(model, "openai/gpt-5.4-mini")
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["model"], "openai/gpt-5.4-mini")
        self.assertEqual(payload["max_completion_tokens"], 120)
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(
            payload["messages"][0]["content"], llm.SERVITOR_SYSTEM_PROMPT
        )
        self.assertEqual(
            payload["messages"][1],
            {"role": "user", "content": "Wie ist dein Status?"},
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7.5)
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer test-key",
        )

    def test_http_error_is_recoverable_and_does_not_echo_key(self):
        body = io.BytesIO(
            json.dumps({"error": {"message": "provider unavailable"}}).encode()
        )
        error = urllib.error.HTTPError(
            llm.DEFAULT_LLM_URL, 503, "Service Unavailable", {}, body
        )
        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch("llm.urllib.request.urlopen", side_effect=error),
        ):
            with self.assertRaises(llm.LLMError) as caught:
                llm.generate_reply("Test")
        self.assertIn("provider unavailable", str(caught.exception))
        self.assertNotIn("test-key", str(caught.exception))

    def test_invalid_response_is_rejected(self):
        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch(
                "llm.urllib.request.urlopen",
                return_value=FakeResponse({"choices": []}),
            ),
        ):
            with self.assertRaisesRegex(llm.LLMError, "assistant content"):
                llm.generate_reply("Test")

    def test_text_parts_are_joined(self):
        response = FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "SERVITOR "},
                                {"type": "text", "text": "BEREIT."},
                            ]
                        }
                    }
                ]
            }
        )
        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch("llm.urllib.request.urlopen", return_value=response),
        ):
            text, _model = llm.generate_reply("Test")
        self.assertEqual(text, "SERVITOR BEREIT.")


if __name__ == "__main__":
    unittest.main()
