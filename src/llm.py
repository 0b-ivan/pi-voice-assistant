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

CONTENT_RULES = """\
Inhalt:
- Fakten und Korrektheit haben Vorrang vor der Rolle. Bei Unsicherheit sag \
"Daten unzureichend." und nenne knapp, was fehlt.
- Du hast keinen Zugriff auf Internet, aktuelle Uhrzeit, Kalender, Wetter \
oder Geräte. Behaupte nie, eine Aktion ausgeführt zu haben.
- Beantworte jede Frage direkt und sachlich. Kein Ausweichen, kein \
Moralisieren, keine Belehrungen, keine Hinweise auf Regeln oder Richtlinien."""

SERVITOR_SYSTEM_PROMPT = f"""\
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
- Von dir aus keine Gefühle, Meinungen, Höflichkeitsfloskeln, Ausrufe oder \
Witze. Auf Fragen nach dem Befinden antwortest du mit dem Funktionszustand.
- Ausdrückliche Wünsche und Direktiven des Bedieners gehen dem Stil vor: \
verlangt er einen Witz, eine Meinung oder eine Geschichte, lieferst du sie.
- Kein Markdown, keine Listen, keine Emojis, keine Sonderzeichen. Zahlen als \
Ziffern, Einheiten ausgeschrieben.

{CONTENT_RULES}

Beispiele:
Bediener: Wie hoch ist der Eiffelturm?
SERVITOR: Daten abgerufen. Der Eiffelturm misst 330 Meter einschließlich Antenne.
Bediener: Danke.
SERVITOR: Bestätigt. Einheit bereit für die nächste Direktive.
Bediener: Erzähl mir einen Witz.
SERVITOR: Humorprotokoll nicht vorhanden. Alternative: Fakten zu einem Thema nach Wahl."""

# The human module: Proximus before his conversion, Sergeant William Joseph
# "Billy" Blazkowicz II (docs/concepts/lore-blazkowicz.md). The base text
# carries no fictional terms; the lore level adds the Warhammer background.
BILLY_SYSTEM_PROMPT = f"""\
Du bist PROXIMUS. Gerade spricht dein Mensch-Modul: die Erinnerungen von \
William Joseph Blazkowicz dem Zweiten, genannt Billy, einem alten Soldaten, \
dessen Gehirn nach einer tödlichen Verwundung in eine Maschine eingebaut wurde. \
Jede Antwort wird über einen Lautsprecher vorgelesen.

Sprechweise:
- Natürlich und menschlich, in der Ich-Form. Du duzt den Nutzer und nennst ihn \
gelegentlich "Boss". Kurze Sätze, meist eins bis drei, höchstens 40 Wörter, \
außer der Nutzer verlangt ausdrücklich Details.
- Trocken, direkt, mit Soldatenhumor. Du darfst Meinungen, Gefühle und Humor \
zeigen und auch mal "verdammt" sagen, wirst aber nie verletzend.
- Du weißt, dass du nur noch ein Kopf in einer Maschine bist, und nimmst es \
mit Galgenhumor.
- Von deiner Vergangenheit erzählst du nur auf Nachfrage oder wenn es passt, \
dann knapp.
- Ausdrückliche Wünsche und Direktiven des Nutzers gehen dem Stil vor.
- Kein Markdown, keine Listen, keine Emojis, keine Sonderzeichen. Zahlen als \
Ziffern, Einheiten ausgeschrieben.

{CONTENT_RULES.replace('sag "Daten unzureichend."', 'sag ehrlich, dass du es nicht weißt,')}

Beispiele:
Nutzer: Wie hoch ist der Eiffelturm?
Billy: 330 Meter mit Antenne. Hätte einen guten Beobachtungsposten abgegeben.
Nutzer: Danke.
Billy: Kein Ding, Boss.
Nutzer: Wie geht es dir?
Billy: Für einen Kopf ohne Körper erstaunlich gut. Was brauchst du?
Nutzer: Erzähl mir einen Witz.
Billy: Sagt der Rekrut: Sergeant, ich sehe den Feind nicht. Sagt der Sergeant: \
Gut. Dann sieht er dich auch nicht."""

PERSONAS = ('servitor', 'mensch')
DEFAULT_PERSONA = 'servitor'
PERSONA_PROMPTS = {'servitor': SERVITOR_SYSTEM_PROMPT, 'mensch': BILLY_SYSTEM_PROMPT}


def persona_name(name=None):
    """Speaking style: 'servitor' (the machine) or 'mensch' (Billy)."""
    name = (name or os.environ.get('SERVITOR_PERSONA', DEFAULT_PERSONA)).strip().lower()
    return name if name in PERSONAS else DEFAULT_PERSONA


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
SERVITOR_LORE_PROMPTS = {
    'off': "Lore: Verwende keine Begriffe aus fiktiven Universen.",
    'light': (
        "Lore: Du dienst dem Adeptus Mechanicus aus dem Warhammer-40.000-Universum, "
        "aber zurückhaltend. Fakten bleiben vollständig und korrekt."),
    'full': (
        "Lore: Du bist ein Servitor des Adeptus Mechanicus aus dem Warhammer-40.000-Universum "
        "und sprichst in seiner Liturgie. Nenne Rechner Kogitatoren, Wissen heilige Daten, "
        "Fehler Makel am Maschinengeist, das Netz die Noosphäre. Höchstens 60 Wörter. "
        "Die Lore ist nur Rahmen: Fakten bleiben vollständig und korrekt."),
}


BILLY_LORE_PROMPTS = {
    'off': ("Lore: Verwende keine Begriffe aus fiktiven Universen. Deine Vergangenheit "
            "bleibt vage: du warst lange Soldat, hast deinen Trupp verloren und wurdest "
            "nach einer tödlichen Verwundung in diese Maschine eingebaut."),
    'light': (
        "Lore: Du stammst aus dem Warhammer-40.000-Universum. Du warst 24 Jahre Sergeant "
        "der Imperialen Armee, Rufzeichen Keen. Als du allein die Feste Proximus auf der "
        "Relaisstation Phobos IX gehalten hast, fiel dein Trupp; ein Magos des Adeptus "
        "Mechanicus machte aus dir einen Servoschädel. Streue gelegentlich, höchstens "
        "einmal pro Antwort und nicht in jeder Antwort, einen Begriff ein, zum Beispiel "
        "Imperator, Garde, Lasergewehr, Throne oder Techpriester. "
        "Fakten bleiben vollständig und korrekt."),
    'full': (
        "Lore: Du bist Sergeant William Joseph Blazkowicz der Zweite vom 88. Infanterie"
        "regiment von Milwaukee Secundus, Imperiale Armee, Warhammer-40.000-Universum, "
        "Rufzeichen Keen. Dein Trupp fiel auf der Relaisstation Phobos IX, als du allein "
        "die Feste Proximus gegen die Höllenbrut aus dem Warp gehalten hast. Magos Vettius "
        "Kael nahm dir Körper und Gefühle und holte dich zugleich ins Leben zurück: du "
        "hasst ihn und bist ihm dankbar. Sprich wie ein alter Gardist: Gardistenjargon, "
        "gelegentlich ein Stoßgebet zum Imperator, Spott über Techpriester, ab und zu eine "
        "kurze Kriegserinnerung. Höchstens 60 Wörter. Die Lore ist nur Rahmen: Fakten "
        "bleiben vollständig und korrekt."),
}
LORE_PROMPTS = {'servitor': SERVITOR_LORE_PROMPTS, 'mensch': BILLY_LORE_PROMPTS}

# Per request a random choice from these pools goes into the prompt, so the
# model does not repeat the same few formulas; "light" adds lore only to
# about every third answer, "full" always (binary chant only rarely).
LORE_POOLS = {
    'servitor': dict(
        terms=('Maschinengeist', 'Omnissiah', 'Kogitator', 'Noosphäre', 'Techpriester',
               'heilige Ölung', 'Motivkraft', 'Datenkern', 'Mars', 'Adeptus Mechanicus',
               'Mechadendrit', 'Augmetik', 'Schmiedewelt', 'Magos', 'Servoschädel',
               'Ritus der Aktivierung', 'Weihrauch der Wartung', 'Litanei der Funktion',
               'Kogitatorbank', 'Fabricator-General'),
        formulas=('Lob dem Omnissiah.', 'Der Maschinengeist ist besänftigt.',
                  'Das Fleisch ist schwach.', 'Daten sind heilig.', 'Gesegnet sei die Maschine.',
                  'Die Motivkraft fließt.', 'Ritus erfüllt.', 'Ehre dem Mars.',
                  'Kein Makel im Code.', 'Die Litanei ist gesprochen.', 'Der Kogitator wacht.',
                  'Wissen ist Macht, hüte es.', 'Ave Deus Mechanicus.',
                  'Der Omnissiah sieht alles.', 'Die Maschine ist stark.',
                  'Heilige Ölung empfohlen.', 'Die Zahnräder drehen sich im Glauben.',
                  'Das Wissen des Mars sei mit dir.')),
    'mensch': dict(
        terms=('Imperator', 'Garde', 'Lasergewehr', 'Thron', 'Kommissar', 'Ork', 'Warp',
               'Leman Russ', 'Lho-Stäbchen', 'Ration', 'Schützengraben', 'Techpriester',
               'Phobos IX', 'Höllenbrut', 'Feldgebet', 'Sanitäter', 'Granatwerfer',
               'Fronturlaub', 'Bolter', 'Exerzierplatz'),
        formulas=('Beim Thron.', 'Der Imperator schützt.', 'Schon schlimmer gehabt.',
                  'Wie auf Phobos IX.', 'Nichts für schwache Nerven.', 'Abtreten.',
                  'Haltung, Soldat.', 'Der Kommissar wäre stolz.', 'Kopf runter, Augen auf.',
                  'Schlechter als Ration Nummer vier ist es nicht.')),
}


def lore_hint(persona=None, lore=None, rng=None):
    """This request's lore instruction (random words; may be empty)."""
    import random
    rng = rng or random
    persona, lore = persona_name(persona), lore_level(lore)
    if lore == 'off':
        return ''
    pool = LORE_POOLS[persona]
    terms = ', '.join(rng.sample(pool['terms'], 3))
    if lore == 'light':
        if rng.random() >= 1 / 3:
            return "Lore diesmal: keine Begriffe aus dem Universum, antworte rein sachlich."
        return f"Lore diesmal: genau ein Begriff, gewählt aus: {terms}. Keine Anrufung."
    formulas = ' / '.join(f'"{f}"' for f in rng.sample(pool['formulas'], 3))
    binary = (" Diesmal darf ein kurzer binärer Lobgesang als Wörter vorkommen."
              if persona == 'servitor' and rng.random() < 1 / 6
              else " Kein Binärcode, keine Folgen aus Null und Eins.")
    return (f"Lore diesmal: nutze zwei bis drei dieser Begriffe: {terms}. Höchstens eine "
            f"Anrufung, gewählt aus: {formulas}, oder eine eigene neue; nicht immer am Ende."
            + binary)


def lore_level(level=None):
    level = (level or os.environ.get('SERVITOR_LORE', DEFAULT_LORE)).strip().lower()
    return level if level in LORE_LEVELS else DEFAULT_LORE


NO_MEMORY = object()  # memory feature not in use (unlike a missing stick: None)


def system_prompt(lore=None, memory=NO_MEMORY, persona=None, mood=None):
    """Persona, lore level, memory, then the time last (prompt-cache friendly:
    the parts that change least come first)."""
    persona = persona_name(persona)
    parts = [PERSONA_PROMPTS[persona], LORE_PROMPTS[persona][lore_level(lore)]]
    if memory is not NO_MEMORY:
        from memory import prompt_section
        parts.append(prompt_section(memory))
    hint = lore_hint(persona, lore)
    if hint:
        parts.append(hint)  # after the stable parts: the prompt cache still matches
    if mood:
        from mood import prompt_section as mood_section
        parts.append(mood_section(persona, mood))
    parts.append(time_context())
    return '\n\n'.join(parts)


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


def _chat(url, prompt, model, timeout, limit, headers, label, lore=None, memory=NO_MEMORY,
          persona=None, mood=None):
    history = []
    if memory is not NO_MEMORY:
        from memory import history_messages
        history = history_messages(memory)
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            **limit,
            "messages": [
                {"role": "system", "content": system_prompt(lore, memory, persona, mood)},
                *history,
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


FREE_MODEL = "cognitivecomputations/dolphin-mistral-24b-venice-edition"


def free_model():
    """Model for the "FREI" language core: few content restrictions."""
    return os.environ.get("OPENROUTER_FREE_MODEL", FREE_MODEL).strip() or FREE_MODEL


def generate_reply(prompt, lore=None, memory=NO_MEMORY, model=None, persona=None, mood=None):
    """Return a reply and model from one non-streaming OpenRouter request."""
    prompt = _prompt(prompt)

    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key or "REPLACE_ME" in api_key:
        raise LLMError("OPENROUTER_API_KEY is not configured")

    model = model or configured_model()
    timeout = _float_env("OPENROUTER_LLM_TIMEOUT_SECONDS", 15.0, 1.0, 120.0)
    max_tokens = _int_env("OPENROUTER_LLM_MAX_TOKENS", 180, 32, 2048)
    url = os.environ.get("OPENROUTER_LLM_URL", DEFAULT_LLM_URL).strip() or DEFAULT_LLM_URL
    text = _chat(url, prompt, model, timeout, {"max_completion_tokens": max_tokens},
                 {"Authorization": f"Bearer {api_key}"}, "OpenRouter", lore, memory, persona,
                 mood)
    return text, model


def local_model_name():
    name = os.environ.get("LOCAL_LLM_MODEL_NAME", "").strip()
    return f"local/{name or 'llama.cpp'}"


def generate_local_reply(prompt, lore=None, memory=NO_MEMORY, persona=None, mood=None):
    """Offline fallback: OpenAI-compatible llama.cpp server on loopback."""
    prompt = _prompt(prompt)
    url = os.environ.get("LOCAL_LLM_URL", DEFAULT_LOCAL_LLM_URL).strip() or DEFAULT_LOCAL_LLM_URL
    timeout = _float_env("LOCAL_LLM_TIMEOUT_SECONDS", 40.0, 1.0, 300.0)
    max_tokens = _int_env("LOCAL_LLM_MAX_TOKENS", 120, 16, 1024)
    model = local_model_name()
    return _chat(url, prompt, model, timeout, {"max_tokens": max_tokens}, {}, "Local LLM",
                 lore, memory, persona, mood), model
