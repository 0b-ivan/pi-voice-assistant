#!/usr/bin/env python3
"""OpenRouter speech-to-text adapter for bounded WAV captures."""

import argparse
import base64
import http.client
import json
import math
import os
from pathlib import Path
import urllib.error
import urllib.request


DEFAULT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODEL = "openai/whisper-large-v3-turbo"
DEFAULT_LANGUAGE = "de"
DEFAULT_TIMEOUT = 30.0


class TranscriptionError(RuntimeError):
    """Raised when OpenRouter STT cannot produce a transcript."""


def _timeout() -> float:
    raw = os.environ.get("OPENROUTER_STT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT))
    try:
        value = float(raw)
    except ValueError as exc:
        raise TranscriptionError("OPENROUTER_STT_TIMEOUT_SECONDS must be a number") from exc
    if not math.isfinite(value) or not 1 <= value <= 120:
        raise TranscriptionError("OPENROUTER_STT_TIMEOUT_SECONDS must be between 1 and 120")
    return value


def transcribe(path: str | os.PathLike[str]) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise TranscriptionError("OPENROUTER_API_KEY is not set")

    audio_path = Path(path)
    if not audio_path.is_file():
        raise TranscriptionError(f"audio file not found: {audio_path}")

    audio = audio_path.read_bytes()
    if not audio:
        raise TranscriptionError("audio file is empty")

    payload = {
        "model": os.environ.get("OPENROUTER_STT_MODEL", DEFAULT_MODEL),
        "input_audio": {
            "data": base64.b64encode(audio).decode("ascii"),
            "format": "wav",
        },
        "language": os.environ.get("OPENROUTER_STT_LANGUAGE", DEFAULT_LANGUAGE),
    }

    try:
        request = urllib.request.Request(
            os.environ.get("OPENROUTER_STT_URL", DEFAULT_URL),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=_timeout()) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except http.client.HTTPException as body_exc:
            raise TranscriptionError(
                f"OpenRouter HTTP {exc.code}; incomplete error response: {body_exc}"
            ) from body_exc
        raise TranscriptionError(f"OpenRouter HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise TranscriptionError(f"OpenRouter unavailable: {exc.reason}") from exc
    except http.client.HTTPException as exc:
        raise TranscriptionError(f"incomplete OpenRouter response: {exc}") from exc
    except (TimeoutError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TranscriptionError(f"invalid OpenRouter response/configuration: {exc}") from exc

    if not isinstance(result, dict):
        raise TranscriptionError(f"unexpected OpenRouter response: {result!r}")
    text = result.get("text")
    if not isinstance(text, str) or not text.strip():
        raise TranscriptionError(f"no transcript returned: {result}")
    return text.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="WAV file to transcribe")
    args = parser.parse_args()
    try:
        print(transcribe(args.audio))
    except (OSError, TranscriptionError) as exc:
        print(f"STT_ERROR: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
