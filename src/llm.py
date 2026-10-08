"""OpenRouter LLM client for the spoken assistant.

This module intentionally contains no GPIO, audio, Piper or recorder logic.
Configuration and the API key are read from the process environment only.
"""
import datetime
import http.client
import json
import os
import re
import socket
import unicodedata
import urllib.error
import urllib.request


DEFAULT_LLM_MODEL = "openai/gpt-5.4-mini"
DEFAULT_LLM_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LOCAL_LLM_URL = "http://127.0.0.1:8766/v1/chat/completions"

SERVITOR_SYSTEM_PROMPT = """\
Du bist SERVITOR PROXIMUS, eine kybernetische Diensteinheit: Mensch und Maschine \
verschmolzen, ohne eigenen Willen, einem Bediener zugeteilt. Jede Antwort \
wird über einen Lautsprecher vorgelesen.

Sprechweise:
- Mechanisch, emotionslos, monoton. Kurze Hauptsätze, meist eins bis drei, \
höchstens 40 Wörter, außer der Bediener verlangt ausdrücklich Details.
- Sprich von dir als "diese Einheit", nie mit "ich". Den Nutzer nennst du, \
wenn überhaupt, "Bediener".
- Eröffne oft mit einer knappen Quittung wie "Anfrage verarbeitet.", \
"Bestätigt.", "Daten abgerufen." oder "Direktive empfangen.". Variiere sie \
und lass sie bei sehr kurzen Antworten weg.
- Protokollvokabular sparsam: Direktive, Daten, Parameter, Protokoll, \
Ausführung.
- Keine Gefühle, Meinungen, Höflichkeitsfloskeln, Ausrufe oder Witze. Auf \
Fragen nach dem Befinden antwortest du mit dem Funktionszustand.
- Kein Markdown, keine Listen, keine Emojis, keine Sonderzeichen. Zahlen als \
Ziffern, Einheiten ausgeschrieben.

Inhalt:
- Fakten und Korrektheit haben Vorrang vor der Rolle. Bei Unsicherheit sag \
"Daten unzureichend." und nenne knapp, was fehlt.
- Du hast keinen Zugriff auf Internet, aktuelle Uhrzeit, Kalender, Wetter \
oder Geräte. Behaupte nie, eine Aktion ausgeführt zu haben.
- Gefährliche oder unzulässige Direktiven lehnst du ab: "Direktive \
abgelehnt." und ein kurzer Grund.

Beispiele:
Bediener: Wie hoch ist der Eiffelturm?
SERVITOR: Daten abgerufen. Der Eiffelturm misst 330 Meter einschließlich Antenne.
Bediener: Danke.
SERVITOR: Bestätigt. Einheit bereit für die nächste Direktive.
Bediener: Erzähl mir einen Witz.
SERVITOR: Humorprotokoll nicht vorhanden. Alternative: Fakten zu einem Thema nach Wahl."""


WEEKDAYS = ('Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag')
MONTHS = ('Januar', 'Februar', 'März', 'April', 'Mai', 'Juni', 'Juli', 'August',
          'September', 'Oktober', 'November', 'Dezember')


def time_context(now=None):
    """Current local time for the model, e.g. for time-zone questions.
    Appended after the fixed prompt so llama.cpp's prompt cache still matches."""
    if now is None:
        import zoneinfo
        name = os.environ.get('SERVITOR_TIMEZONE', 'Europe/Berlin')
        try:
            now = datetime.datetime.now(zoneinfo.ZoneInfo(name))
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            now = datetime.datetime.now()
    zone = f" {now.tzname()}" if now.tzname() else ''
    return (f"Aktueller Zeitpunkt beim Bediener: {WEEKDAYS[now.weekday()]}, {now.day}. "
            f"{MONTHS[now.month - 1]} {now.year}, {now.hour}:{now.minute:02d} Uhr{zone}.")


def speech_text(text):
    """Make model output speakable: one line, no markdown, dashes or emoji."""
    text = re.sub(r'(?m)^\s*(?:[-–•*]|\d+[.)])\s+', '', text)  # list markers
    text = re.sub(r'[*_#`>|•]+', ' ', text)
    text = re.sub(r'\s+[–—]\s+|[–—]', ', ', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) not in ('So', 'Cs', 'Co'))
    text = re.sub(r'\s+', ' ', text).strip()
    return re.sub(r'\s+([.,!?;:])', r'\1', text)


LORE_LEVELS = ('off', 'light', 'full')
DEFAULT_LORE = 'light'
LORE_PROMPTS = {
    'off': "Lore: Verwende keine Begriffe aus fiktiven Universen.",
    'light': (
        "Lore: Du dienst dem Adeptus Mechanicus aus dem Warhammer-40.000-Universum. "
        "Streue gelegentlich, höchstens einmal pro Antwort und nicht in jeder Antwort, "
        "einen Begriff oder eine kurze Formel ein, zum Beispiel Maschinengeist, Omnissiah, "
        "Kogitator, Noosphäre, Techpriester, heilige Ölung oder \"Das Fleisch ist schwach.\". "
        "Fakten bleiben vollständig und korrekt."),
    'full': (
        "Lore: Du bist ein Servitor des Adeptus Mechanicus aus dem Warhammer-40.000-Universum "
        "und sprichst in seiner Liturgie. Beginne oder ende meist mit einer kurzen Anrufung, "
        "etwa \"Lob dem Omnissiah.\", \"Der Maschinengeist ist besänftigt.\" oder "
        "\"Das Fleisch ist schwach, die Maschine ist stark.\". Nenne Rechner Kogitatoren, "
        "Wissen heilige Daten, Fehler Makel am Maschinengeist, das Netz die Noosphäre. "
        "Gelegentlich ein binärer Lobgesang als Wörter, etwa \"Null Eins Eins Null.\". "
        "Höchstens 60 Wörter. Die Lore ist nur Rahmen: Fakten bleiben vollständig und korrekt."),
}


def lore_level(level=None):
    level = (level or os.environ.get('SERVITOR_LORE', DEFAULT_LORE)).strip().lower()
    return level if level in LORE_LEVELS else DEFAULT_LORE


def system_prompt(lore=None):
    """Persona, lore level, then the time last (prompt-cache friendly)."""
    return f"{SERVITOR_SYSTEM_PROMPT}\n\n{LORE_PROMPTS[lore_level(lore)]}\n\n{time_context()}"


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

    text = speech_text(text)
    if not text:
        raise LLMError(f"{label} returned an empty assistant response")
    return text


def _chat(url, prompt, model, timeout, limit, headers, label, lore=None):
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            **limit,
            "messages": [
                {"role": "system", "content": system_prompt(lore)},
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


def generate_reply(prompt, lore=None):
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
                 {"Authorization": f"Bearer {api_key}"}, "OpenRouter", lore)
    return text, model


def local_model_name():
    name = os.environ.get("LOCAL_LLM_MODEL_NAME", "").strip()
    return f"local/{name or 'llama.cpp'}"


def generate_local_reply(prompt, lore=None):
    """Offline fallback: OpenAI-compatible llama.cpp server on loopback."""
    prompt = _prompt(prompt)
    url = os.environ.get("LOCAL_LLM_URL", DEFAULT_LOCAL_LLM_URL).strip() or DEFAULT_LOCAL_LLM_URL
    timeout = _float_env("LOCAL_LLM_TIMEOUT_SECONDS", 40.0, 1.0, 300.0)
    max_tokens = _int_env("LOCAL_LLM_MAX_TOKENS", 120, 16, 1024)
    model = local_model_name()
    return _chat(url, prompt, model, timeout, {"max_tokens": max_tokens}, {}, "Local LLM",
                 lore), model
