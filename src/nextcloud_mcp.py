"""Nextcloud for the operator through a Nextcloud MCP server (server only).

The LLM gets a fixed list of Nextcloud tools (calendar, contacts, Deck,
notes, shopping list, file search) for questions that are about them, and
only when the voice was recognized as the operator. Reading tools run at
once; writing tools never do: the first write the model asks for becomes a
proposal that is spoken back ("Soll ich ... eintragen?") and runs only
after a spoken yes from the operator's voice (docs/nextcloud.md).

* ``Client``: minimal MCP client (streamable HTTP, JSON-RPC over POST,
  replies as JSON or server-sent events), stdlib only.
* ``wants(text)``: is the utterance about Nextcloud data at all? Everything
  else keeps the plain LLM path (no tool round trip, no extra latency).
* ``tool_specs(tools)``: OpenAI-style tool definitions for the allowed
  tools the server actually offers (apps that are not installed are absent).
* ``run(messages, chat, client)``: the tool loop; returns ``('answer', text)``
  or ``('propose', Proposal)``.
* ``Pending``: the one open proposal, answered only by the next turn of the
  same voice within ``CONFIRM_SECONDS``; no, unclear, timeout or a different
  voice drop it, and it runs at most once.

Never offered: mail, sharing, deleting, moving, anything not listed in
READ_TOOLS or WRITE_TOOLS. Configured by SERVITOR_NEXTCLOUD_MCP_URL
(unset: the feature is off).
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

PROTOCOL = '2025-06-18'
CONFIRM_SECONDS = 90.0
MAX_ROUNDS = 4            # model -> tools -> model ... before giving up
MAX_RESULT_CHARS = 4000   # per tool result handed back to the model
TOOLS_TTL = 600.0         # seconds the tool list is cached

READ_TOOLS = frozenset({
    'nc_calendar_list_calendars', 'nc_calendar_list_events', 'nc_calendar_get_event',
    'nc_calendar_get_upcoming_events', 'nc_calendar_find_availability',
    'nc_calendar_list_todos', 'nc_calendar_search_todos',
    'nc_contacts_list_addressbooks', 'nc_contacts_search_contacts',
    'deck_get_boards', 'deck_get_board_overview', 'deck_get_stacks', 'deck_get_cards',
    'deck_get_card',
    'nc_notes_search_notes', 'nc_notes_get_note',
    'nc_shopping_list_get_lists', 'nc_shopping_list_get_list', 'nc_shopping_list_get_items',
    'nc_webdav_search_files', 'nc_webdav_find_by_name', 'nc_webdav_list_favorites',
})
# Writing tools: only ever as a proposal. Value: what is created (spoken).
WRITE_TOOLS = {
    'nc_calendar_create_event': 'Termin',
    'nc_calendar_create_todo': 'Aufgabe',
    'nc_notes_create_note': 'Notiz',
    'nc_shopping_list_add_items': 'Einkaufsliste',
}
# Arguments the model must not choose (large or private payloads).
FORCED_ARGS = {'nc_contacts_search_contacts': {'include_photos': False}}


def _arguments_for(name, args):
    """The arguments sent to the server: forced ones, and for events without a
    calendar name all calendars (the server refuses an empty name, and the
    model then answered "keine Termine", web test 20.10.2026)."""
    args = {**args, **FORCED_ARGS.get(name, {})}
    if name == 'nc_calendar_list_events' and not args.get('calendar_name'):
        args['search_all_calendars'] = True
    return args

_TOPIC = re.compile(
    r'\b(kalender\w*|termin\w*|verabredung\w*|meeting\w*|besprechung\w*|'
    r'aufgabe\w*|todo\w*|to-do\w*|erledig\w*|'
    r'kontakt\w*|telefonnummer\w*|nummer von|adresse von|geburtstag\w*|'
    r'deck|board\w*|karte\w*|'
    r'notiz\w*|notier\w*|aufschreib\w*|schreib (mir )?auf|'
    r'einkauf\w*|einkaufsliste|besorg\w*|'
    r'datei\w*|dokument\w*|nextcloud|cloud)\b')

# A request to create something (the calendar intent must not answer it).
_WRITE = re.compile(
    r'\b(trag\w*|eintrag\w*|anleg\w*|leg\w*( \w+){0,6} an|erstell\w*|hinzufüg\w*|'
    r'füg\w*( \w+){0,6} hinzu|setz\w*( \w+){0,6} (auf|in)|notier\w*|aufschreib\w*|'
    r'schreib\w*( \w+){0,6} auf|plan\w*( \w+){0,6} ein)\b')

TOOL_HINT = (
    "Du hast Zugriff auf die Nextcloud des Bedieners (Kalender, Kontakte, Deck, Notizen, "
    "Einkaufsliste, Dateisuche) über Werkzeuge. Nutze sie nur, wenn die Frage das braucht, "
    "und erfinde keine Einträge. Schreibende Werkzeuge (Termin, Aufgabe, Notiz, Einkaufsliste) "
    "führen nichts sofort aus: Rufe sie mit vollständigen Angaben auf, der Bediener bestätigt "
    "danach. Löschen, Mails und Freigaben kannst du nicht. Termine stehen in mehreren "
    "Kalendern: ohne genannten Kalender alle durchsuchen, nach Datum ordnen und bei jedem "
    "Termin sagen, in welchem Kalender er steht (calendar_display_name, etwa „im Kalender "
    "Privat“). Meldet ein Werkzeug einen Fehler, "
    "sag, dass die Abfrage nicht geklappt hat, und behaupte nie, es gäbe keine Einträge. "
    "Antworte danach kurz und sprechbar.")

UNAVAILABLE = "Nextcloud ist gerade nicht erreichbar."
CANCELLED = "Abgebrochen, nichts eingetragen."
VOICE_UNSURE = ("Stimme nicht sicher erkannt. Zum Bestätigen sag: Ja, bitte eintragen. "
                "Sonst sag nein.")

# Longer yes forms ("ja, bitte eintragen") pass the voice threshold more often
# than a bare "ja"; device_control.answer covers ja/bestätigt/nein/abbrechen.
_YES_MORE = re.compile(r'(ja )?(bitte )?(trag (es|das|ihn|sie) (bitte )?ein|eintragen|'
                       r'leg (es|das|ihn|sie) (bitte )?an|anlegen|setz (es|das|sie) drauf|'
                       r'mach das|genau so)( bitte)?')


def confirmation(text):
    """'confirm', 'cancel' or None for the answer to an open proposal."""
    import device_control
    import intents
    words = intents.normalize(text)
    reply = device_control.answer(words)
    if reply is not None:
        return reply
    return 'confirm' if _YES_MORE.fullmatch(words) else None


class MCPError(Exception):
    pass


def configured_url():
    return os.environ.get('SERVITOR_NEXTCLOUD_MCP_URL', '').strip()


def wants(text):
    """True for utterances about calendar, contacts, Deck, notes, shopping or files."""
    return bool(_TOPIC.search(' '.join(str(text).lower().split())))


def writes(text):
    """True when the utterance asks to create an entry, not to read."""
    return bool(_WRITE.search(' '.join(str(text).lower().split())))


def _events(raw):
    """JSON-RPC messages from a JSON body or a server-sent-events stream."""
    text = raw.decode('utf-8', 'replace').strip()
    if not text:
        return []
    if text[0] in '{[':
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    out = []
    for block in text.split('\n\n'):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith('data:')]
        if lines:
            out.append(json.loads('\n'.join(lines)))
    return out


class Client:
    """One MCP session against ``url``; reconnects once when the session expired."""

    def __init__(self, url, timeout=8.0, opener=None, clock=time.monotonic):
        self.url = url
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen
        self.clock = clock
        self.session = None
        self._next = 0
        self._tools = None
        self._tools_at = 0.0

    def _post(self, payload):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream'}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode('utf-8'),
                                         method='POST', headers=headers)
        try:
            with self.opener(request, timeout=self.timeout) as response:
                session = response.headers.get('Mcp-Session-Id')
                if session:
                    self.session = session
                return _events(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and self.session:
                self.session = None              # expired: start a new one
                raise _Expired() from exc
            raise MCPError(f'MCP HTTP {exc.code}') from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise MCPError(f'MCP unreachable: {exc}') from exc

    def _rpc(self, method, params=None):
        self._next += 1
        payload = {'jsonrpc': '2.0', 'id': self._next, 'method': method, 'params': params or {}}
        for message in self._post(payload):
            if isinstance(message, dict) and message.get('id') == self._next:
                if 'error' in message:
                    raise MCPError(str(message['error'].get('message', 'MCP error')))
                return message.get('result') or {}
        raise MCPError(f'no MCP reply to {method}')

    def _open(self):
        self._rpc('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                 'clientInfo': {'name': 'servitor-proximus', 'version': '1'}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def _call(self, method, params=None):
        for attempt in (1, 2):
            try:
                if not self.session:
                    self._open()
                return self._rpc(method, params)
            except _Expired:
                if attempt == 2:
                    raise MCPError('MCP session expired twice') from None
        raise MCPError('unreachable')   # pragma: no cover

    def tools(self):
        """The server's tool list, cached for TOOLS_TTL seconds."""
        if self._tools is None or self.clock() - self._tools_at > TOOLS_TTL:
            self._tools = self._call('tools/list').get('tools') or []
            self._tools_at = self.clock()
        return self._tools

    def call(self, name, arguments):
        """Run one tool; the text of its result (MCPError on a tool error)."""
        result = self._call('tools/call', {'name': name, 'arguments': arguments or {}})
        text = '\n'.join(part.get('text', '') for part in result.get('content') or []
                         if isinstance(part, dict) and part.get('type') == 'text')
        if result.get('isError'):
            raise MCPError(text[:300] or f'{name} failed')
        return text


class _Expired(Exception):
    pass


def allowed(name):
    return name in READ_TOOLS or name in WRITE_TOOLS


def tool_specs(tools):
    """OpenAI-style definitions of the allowed tools among ``tools`` (MCP list)."""
    specs = []
    for tool in tools:
        name = tool.get('name') if isinstance(tool, dict) else None
        if not name or not allowed(name):
            continue
        schema = dict(tool.get('inputSchema') or {'type': 'object', 'properties': {}})
        hidden = FORCED_ARGS.get(name, {})
        if hidden:
            schema['properties'] = {k: v for k, v in (schema.get('properties') or {}).items()
                                    if k not in hidden}
            schema['required'] = [k for k in schema.get('required') or [] if k not in hidden]
        description = (tool.get('description') or '').strip().split('\n\n')[0][:600]
        if name in WRITE_TOOLS:
            description += ' (Wird erst nach Bestätigung des Bedieners ausgeführt.)'
        specs.append({'type': 'function',
                      'function': {'name': name, 'description': description,
                                   'parameters': schema}})
    return specs


class Proposal:
    """A write the model asked for; runs only after consent."""

    def __init__(self, name, arguments):
        self.name = name
        self.arguments = dict(arguments or {})

    def question(self):
        args = self.arguments
        kind = WRITE_TOOLS.get(self.name, 'Eintrag')
        if self.name == 'nc_calendar_create_event':
            when = _spoken_time(args.get('start_datetime'))
            place = f" in {args['calendar_name']}" if args.get('calendar_name') else ''
            what = f"„{args.get('title', 'ohne Titel')}“{' am ' + when if when else ''}"
            return f"Soll ich den Termin {what}{place} eintragen? Sag ja oder nein."
        if self.name == 'nc_calendar_create_todo':
            return f"Soll ich die Aufgabe „{args.get('summary', '')}“ anlegen? Sag ja oder nein."
        if self.name == 'nc_notes_create_note':
            return f"Soll ich die Notiz „{args.get('title', '')}“ anlegen? Sag ja oder nein."
        if self.name == 'nc_shopping_list_add_items':
            items = [i.get('name') if isinstance(i, dict) else str(i)
                     for i in args.get('items') or []]
            names = ', '.join(n for n in items if n) or 'die Artikel'
            return f"Soll ich {names} auf die Einkaufsliste setzen? Sag ja oder nein."
        return f"Soll ich den Eintrag ({kind}) anlegen? Sag ja oder nein."

    def done_text(self):
        return {'nc_calendar_create_event': "Termin eingetragen.",
                'nc_calendar_create_todo': "Aufgabe angelegt.",
                'nc_notes_create_note': "Notiz angelegt.",
                'nc_shopping_list_add_items': "Auf die Einkaufsliste gesetzt."}.get(
                    self.name, "Erledigt.")


def _spoken_time(value):
    match = re.match(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?', str(value or ''))
    if not match:
        return ''
    year, month, day, hour, minute = match.groups()
    date = f"{int(day)}.{int(month)}."
    return f"{date} um {int(hour)}:{minute} Uhr" if hour else date


def _arguments(call):
    raw = (call.get('function') or {}).get('arguments') or '{}'
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def run(messages, chat, client, max_rounds=MAX_ROUNDS, local=()):
    """Tool loop. ``chat(messages, specs)`` returns the assistant message dict
    (``content``, optional ``tool_calls``). Reading tools run, the first writing
    tool ends the loop as a Proposal, anything else is refused to the model.
    ``client`` may be None; ``local``: read-only tools of the server itself
    (``name``, ``spec``, ``call(arguments)``), such as systemwache.StatusTool."""
    local = {tool.name: tool for tool in local}
    specs = tool_specs(client.tools()) if client is not None else []
    if client is not None and not specs:
        raise MCPError('no allowed Nextcloud tools offered')
    specs += [tool.spec for tool in local.values()]
    if not specs:
        raise MCPError('no tools offered')
    messages = list(messages)
    for _ in range(max_rounds):
        message = chat(messages, specs) or {}
        calls = [c for c in message.get('tool_calls') or [] if isinstance(c, dict)]
        if not calls:
            return 'answer', (message.get('content') or '').strip()
        messages.append({'role': 'assistant', 'content': message.get('content') or '',
                         'tool_calls': calls})
        for call in calls:
            name = (call.get('function') or {}).get('name', '')
            args = _arguments(call)
            if name in local and args is not None:
                try:
                    result = local[name].call(args)
                except Exception as exc:  # noqa: BLE001 - the model hears it failed
                    result = f'Fehler, keine Daten erhalten: {type(exc).__name__}'
            elif client is not None and name in WRITE_TOOLS and args is not None:
                return 'propose', Proposal(name, args)
            elif client is None or name not in READ_TOOLS or args is None:
                result = 'Nicht erlaubt.' if args is not None else 'Ungültige Argumente.'
            else:
                try:
                    result = client.call(name, _arguments_for(name, args))
                except MCPError as exc:
                    result = f'Fehler, keine Daten erhalten: {exc}'
            # Name and outcome only: arguments and results are the operator's data.
            print(json.dumps(dict(event='tool_call', tool=name[:60],
                                  ok=not result.startswith(('Fehler', 'Nicht erlaubt',
                                                            'Ungültige')))), flush=True)
            messages.append({'role': 'tool', 'tool_call_id': call.get('id', ''),
                             'content': result[:MAX_RESULT_CHARS]})
    raise MCPError('too many tool rounds')


class Pending:
    """At most one open proposal, for one voice, for CONFIRM_SECONDS."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.item = None

    def offer(self, proposal, speaker):
        self.item = (proposal, speaker, self.clock() + CONFIRM_SECONDS)

    def open_for(self):
        """The speaker the open proposal belongs to, or None."""
        if self.item and self.clock() <= self.item[2]:
            return self.item[1]
        self.item = None
        return None

    def take(self, speaker):
        """The proposal if it is still open and ``speaker`` owns it; always closes it."""
        item, self.item = self.item, None
        if not item or self.clock() > item[2] or not speaker or speaker != item[1]:
            return None
        return item[0]
