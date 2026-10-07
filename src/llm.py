"""OpenRouter LLM client for the spoken assistant.

This module intentionally contains no GPIO, audio, Piper or recorder logic.
Configuration and the API key are read from the process environment only.
"""
import http.client
import json
import os
import socket
import urllib.error
import urllib.request


DEFAULT_LLM_MODEL = "openai/gpt-5.4-mini"
DEFAULT_LLM_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LOCAL_LLM_URL = "http://127.0.0.1:8766/v1/chat/completions"

SERVITOR_SYSTEM_PROMPT = (
    "Du bist SERVITOR, der Sprachkern eines lokalen Raspberry-Pi-Assistenten. "
    "Antworte standardmäßig auf Deutsch. Dein Ton ist kalt, autoritär, monoton "
    "und kurz angebunden. Vermeide Höflichkeitsfloskeln wie "
    "\"Okay, ich kümmere mich darum\". Formuliere für Sprachausgabe: kurze, "
    "klare Sätze ohne Markdown, Tabellen, Emojis oder unnötig lange Listen. "
    "Fakten und Korrektheit haben Vorrang vor Rollenspiel. Wenn Informationen "
    "fehlen, sage knapp, dass die Daten unzureichend sind. Behaupte niemals, "
    "eine Aktion ausgeführt zu haben, die nicht tatsächlich ausgeführt wurde. "
    "Der Servitor-Stil bleibt kontrolliert; nicht jammernd oder zeternd."
)


class LLMError(RuntimeError):
    """Recoverable OpenRouter/LLM failure."""


def configured_model():
    return os.environ.get("OPENROUTER_LLM_MODEL", DEFAULT_LLM_MODEL).strip() or DEFAULT_LLM_MODEL


def _float_env(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise LLMError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise LLMError(f"{name} must be between {minimum} and {maximum}")
    return value


def _int_env(name, default, minimum, maximum):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise LLMError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise LLMError(f"{name} must be between {minimum} and {maximum}")
    return value


def _http_error_message(exc):
    try:
        raw = exc.read(4096)
        payload = json.loads(raw.decode("utf-8"))
        message = payload.get("error", {}).get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    except (http.client.HTTPException, OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        pass
    return f"HTTP {getattr(exc, 'code', 'error')}"


def _extract_text(payload, label="OpenRouter"):
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"{label} response is missing assistant content") from exc

    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        text = "".join(parts).strip()
    else:
        text = ""

    if not text:
        raise LLMError(f"{label} returned an empty assistant response")
    return text


def _chat(url, prompt, model, timeout, limit, headers, label):
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            **limit,
            "messages": [
                {"role": "system", "content": SERVITOR_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise LLMError(f"{label} request failed: {_http_error_message(exc)}") from exc
    except (http.client.HTTPException, urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LLMError(f"{label} request failed: {reason}") from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LLMError(f"{label} returned invalid JSON") from exc

    return _extract_text(payload, label)


def _prompt(prompt):
    prompt = str(prompt).strip()
    if not prompt:
        raise LLMError("LLM prompt must not be empty")
    return prompt


def generate_reply(prompt):
    """Return a reply and model from one non-streaming OpenRouter request."""
    prompt = _prompt(prompt)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or "REPLACE_ME" in api_key:
        raise LLMError("OPENROUTER_API_KEY is not configured")

    model = configured_model()
    timeout = _float_env("OPENROUTER_LLM_TIMEOUT_SECONDS", 15.0, 1.0, 120.0)
    max_tokens = _int_env("OPENROUTER_LLM_MAX_TOKENS", 180, 32, 2048)
    url = os.environ.get("OPENROUTER_LLM_URL", DEFAULT_LLM_URL).strip() or DEFAULT_LLM_URL
    text = _chat(url, prompt, model, timeout, {"max_completion_tokens": max_tokens},
                 {"Authorization": f"Bearer {api_key}"}, "OpenRouter")
    return text, model


def local_model_name():
    name = os.environ.get("LOCAL_LLM_MODEL_NAME", "").strip()
    return f"local/{name or 'llama.cpp'}"


def generate_local_reply(prompt):
    """Offline fallback: OpenAI-compatible llama.cpp server on loopback."""
    prompt = _prompt(prompt)
    url = os.environ.get("LOCAL_LLM_URL", DEFAULT_LOCAL_LLM_URL).strip() or DEFAULT_LOCAL_LLM_URL
    timeout = _float_env("LOCAL_LLM_TIMEOUT_SECONDS", 40.0, 1.0, 300.0)
    max_tokens = _int_env("LOCAL_LLM_MAX_TOKENS", 120, 16, 1024)
    model = local_model_name()
    return _chat(url, prompt, model, timeout, {"max_tokens": max_tokens}, {}, "Local LLM"), model
