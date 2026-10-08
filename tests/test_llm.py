import http.client
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
        system = payload["messages"][0]["content"]
        self.assertTrue(system.startswith(llm.SERVITOR_SYSTEM_PROMPT))
        self.assertIn("Aktueller Zeitpunkt beim Bediener:", system)
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

    def test_malformed_http_error_body_falls_back_to_status(self):
        class BrokenBody:
            def read(self, *_args):
                raise http.client.IncompleteRead(b"{", 12)

            def close(self):
                pass

        error = urllib.error.HTTPError(
            llm.DEFAULT_LLM_URL,
            503,
            "Service Unavailable",
            {},
            BrokenBody(),
        )
        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch("llm.urllib.request.urlopen", side_effect=error),
        ):
            with self.assertRaises(llm.LLMError) as caught:
                llm.generate_reply("Test")

        self.assertIn("HTTP 503", str(caught.exception))
        self.assertNotIsInstance(caught.exception, http.client.HTTPException)

    def test_success_body_transport_error_is_wrapped(self):
        class BrokenResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                raise http.client.IncompleteRead(b"{", 12)

        with (
            patch.dict("os.environ", self.base_env(), clear=True),
            patch(
                "llm.urllib.request.urlopen",
                return_value=BrokenResponse(),
            ),
        ):
            with self.assertRaisesRegex(llm.LLMError, "OpenRouter request failed"):
                llm.generate_reply("Test")

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



class LocalLLMTests(unittest.TestCase):
    def test_local_request_uses_loopback_prompt_and_no_key(self):
        response = FakeResponse({"choices": [{"message": {"content": "Daten unzureichend."}}]})
        env = {"LOCAL_LLM_MODEL_NAME": "qwen2.5-3b", "LOCAL_LLM_MAX_TOKENS": "64"}
        with (
            patch.dict("os.environ", env, clear=True),
            patch("llm.urllib.request.urlopen", return_value=response) as urlopen,
        ):
            text, model = llm.generate_local_reply("Status?")
        self.assertEqual((text, model), ("Daten unzureichend.", "local/qwen2.5-3b"))
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, llm.DEFAULT_LOCAL_LLM_URL)
        self.assertIsNone(request.get_header("Authorization"))
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["max_tokens"], 64)
        self.assertNotIn("max_completion_tokens", payload)
        self.assertTrue(payload["messages"][0]["content"].startswith(llm.SERVITOR_SYSTEM_PROMPT))

    def test_openrouter_body_keeps_only_max_completion_tokens(self):
        response = FakeResponse({"choices": [{"message": {"content": "OK."}}]})
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "k"}, clear=True),
            patch("llm.urllib.request.urlopen", return_value=response) as urlopen,
        ):
            llm.generate_reply("Status?")
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertIn("max_completion_tokens", payload)
        self.assertNotIn("max_tokens", payload)

    def test_local_failure_is_labelled(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("llm.urllib.request.urlopen",
                  side_effect=urllib.error.URLError("refused")),
        ):
            with self.assertRaisesRegex(llm.LLMError, "Local LLM request failed"):
                llm.generate_local_reply("Status?")



class SpeechTextTests(unittest.TestCase):
    def test_model_output_becomes_one_speakable_line(self):
        raw = ("Anfrage verarbeitet.  \nRaspberry Pi – kleiner Computer.\n"
               "- Punkt eins\n2. Punkt zwei\n**Fett** 😀 `code`")
        self.assertEqual(llm.speech_text(raw),
                         "Anfrage verarbeitet. Raspberry Pi, kleiner Computer. "
                         "Punkt eins Punkt zwei Fett code")

    def test_numbers_and_units_survive(self):
        self.assertEqual(llm.speech_text("Der Eiffelturm misst 330 Meter, 3,5 Prozent."),
                         "Der Eiffelturm misst 330 Meter, 3,5 Prozent.")

    def test_lore_levels_in_system_prompt(self):
        for level, word in (("off", "keine Begriffe"), ("light", "höchstens einmal"),
                            ("full", "Liturgie")):
            prompt = llm.system_prompt(level)
            self.assertTrue(prompt.startswith(llm.SERVITOR_SYSTEM_PROMPT))
            self.assertIn(word, prompt)
            if level != "off":
                self.assertIn("Fakten bleiben", prompt)
            self.assertTrue(prompt.rstrip().split("\n")[-1].startswith("Aktueller Zeitpunkt"))
        self.assertEqual(llm.lore_level("unbekannt"), llm.DEFAULT_LORE)
        with patch.dict("os.environ", {"SERVITOR_LORE": "full"}):
            self.assertEqual(llm.lore_level(), "full")

    def test_time_context(self):
        import datetime
        now = datetime.datetime(2026, 10, 8, 9, 5)
        self.assertEqual(llm.time_context(now),
                         "Aktueller Zeitpunkt beim Bediener: Donnerstag, 8. Oktober 2026, 9:05 Uhr.")


if __name__ == "__main__":
    unittest.main()
