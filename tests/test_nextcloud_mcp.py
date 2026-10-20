import http.server
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import nextcloud_mcp as nc


TOOLS = [
    {'name': 'nc_calendar_list_events', 'description': 'List events.\n\nLong details.',
     'inputSchema': {'type': 'object', 'properties': {'start_date': {'type': 'string'}}}},
    {'name': 'nc_calendar_create_event', 'description': 'Create an event.',
     'inputSchema': {'type': 'object', 'properties': {'title': {'type': 'string'}},
                     'required': ['title']}},
    {'name': 'nc_contacts_search_contacts', 'description': 'Search contacts.',
     'inputSchema': {'type': 'object',
                     'properties': {'query': {'type': 'string'},
                                    'include_photos': {'type': 'boolean'}},
                     'required': ['query', 'include_photos']}},
    {'name': 'nc_mail_send_message', 'description': 'Send mail.', 'inputSchema': {}},
    {'name': 'nc_calendar_delete_event', 'description': 'Delete.', 'inputSchema': {}},
]


class FakeMCP(http.server.BaseHTTPRequestHandler):
    """Streamable-HTTP MCP server: SSE replies, a session header, one expiry."""
    calls = []
    sessions = 0
    expire_next = False

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        cls = type(self)
        if body.get('method') == 'initialize':
            cls.sessions += 1
            self._reply(body, {'protocolVersion': nc.PROTOCOL}, session=f's{cls.sessions}')
            return
        if 'id' not in body:                      # notification
            self.send_response(202)
            self.end_headers()
            return
        if self.headers.get('Mcp-Session-Id') != f's{cls.sessions}' or cls.expire_next:
            cls.expire_next = False
            self.send_response(404)
            self.end_headers()
            return
        if body['method'] == 'tools/list':
            self._reply(body, {'tools': TOOLS})
        elif body['method'] == 'tools/call':
            cls.calls.append(body['params'])
            if body['params']['name'] == 'nc_calendar_list_events':
                self._reply(body, {'content': [{'type': 'text', 'text': 'Zahnarzt 10 Uhr'}]})
            else:
                self._reply(body, {'content': [{'type': 'text', 'text': 'kaputt'}],
                                   'isError': True})

    def _reply(self, body, result, session=None):
        data = json.dumps({'jsonrpc': '2.0', 'id': body['id'], 'result': result})
        raw = f'event: message\ndata: {data}\n\n'.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        if session:
            self.send_header('Mcp-Session-Id', session)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ClientTests(unittest.TestCase):
    def setUp(self):
        FakeMCP.calls, FakeMCP.sessions, FakeMCP.expire_next = [], 0, False
        self.server = http.server.HTTPServer(('127.0.0.1', 0), FakeMCP)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = nc.Client(f'http://127.0.0.1:{self.server.server_address[1]}/mcp')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_lists_and_calls_tools_over_one_session(self):
        self.assertEqual(len(self.client.tools()), len(TOOLS))
        self.assertEqual(self.client.call('nc_calendar_list_events', {}), 'Zahnarzt 10 Uhr')
        self.assertEqual(FakeMCP.sessions, 1)

    def test_an_expired_session_is_reopened_once(self):
        self.client.tools()
        FakeMCP.expire_next = True
        self.assertEqual(self.client.call('nc_calendar_list_events', {}), 'Zahnarzt 10 Uhr')
        self.assertEqual(FakeMCP.sessions, 2)

    def test_a_tool_error_raises(self):
        with self.assertRaises(nc.MCPError):
            self.client.call('nc_calendar_create_event', {'title': 'x'})

    def test_unreachable_server_raises(self):
        with self.assertRaises(nc.MCPError):
            nc.Client('http://127.0.0.1:9/mcp', timeout=1).tools()


class FakeClient:
    def __init__(self, tools=TOOLS):
        self._tools, self.calls = tools, []

    def tools(self):
        return self._tools

    def call(self, name, args):
        self.calls.append((name, args))
        return 'Zahnarzt morgen 10 Uhr'


def tool_call(name, args, call_id='c1'):
    return {'id': call_id, 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(args)}}


class LoopTests(unittest.TestCase):
    def test_only_allowed_tools_are_offered_and_hidden_arguments_removed(self):
        specs = {s['function']['name']: s['function'] for s in nc.tool_specs(TOOLS)}
        self.assertEqual(set(specs), {'nc_calendar_list_events', 'nc_calendar_create_event',
                                      'nc_contacts_search_contacts'})
        contact = specs['nc_contacts_search_contacts']['parameters']
        self.assertNotIn('include_photos', contact['properties'])
        self.assertEqual(contact['required'], ['query'])
        self.assertEqual(specs['nc_calendar_list_events']['description'], 'List events.')
        self.assertIn('Bestätigung', specs['nc_calendar_create_event']['description'])

    def test_reading_tools_run_and_their_result_reaches_the_model(self):
        client, seen = FakeClient(), []

        def chat(messages, specs):
            seen.append(messages)
            if len(seen) == 1:
                return {'content': '', 'tool_calls': [tool_call('nc_contacts_search_contacts',
                                                                {'query': 'Luca'})]}
            return {'content': 'Luca hat morgen frei.'}

        self.assertEqual(nc.run([{'role': 'user', 'content': 'q'}], chat, client),
                         ('answer', 'Luca hat morgen frei.'))
        self.assertEqual(client.calls, [('nc_contacts_search_contacts',
                                         {'query': 'Luca', 'include_photos': False})])
        self.assertEqual(seen[1][-1]['role'], 'tool')

    def test_a_write_becomes_a_proposal_and_never_runs(self):
        client = FakeClient()
        chat = lambda messages, specs: {'tool_calls': [tool_call(
            'nc_calendar_create_event', {'title': 'Zahnarzt', 'calendar_name': 'privat',
                                         'start_datetime': '2026-10-21T10:00:00'})]}
        kind, proposal = nc.run([], chat, client)
        self.assertEqual(kind, 'propose')
        self.assertEqual(client.calls, [])
        self.assertEqual(proposal.question(),
                         'Soll ich den Termin „Zahnarzt“ am 21.10. um 10:00 Uhr in privat '
                         'eintragen? Sag ja oder nein.')

    def test_forbidden_tools_are_refused_to_the_model(self):
        client, rounds = FakeClient(), []

        def chat(messages, specs):
            rounds.append(messages)
            if len(rounds) == 1:
                return {'tool_calls': [tool_call('nc_mail_send_message', {'to': 'x'})]}
            return {'content': 'Das kann ich nicht.'}

        self.assertEqual(nc.run([], chat, client), ('answer', 'Das kann ich nicht.'))
        self.assertEqual(client.calls, [])
        self.assertEqual(rounds[1][-1]['content'], 'Nicht erlaubt.')

    def test_endless_tool_rounds_and_missing_tools_raise(self):
        chat = lambda messages, specs: {'tool_calls': [tool_call('nc_calendar_list_events', {})]}
        with self.assertRaises(nc.MCPError):
            nc.run([], chat, FakeClient())
        with self.assertRaises(nc.MCPError):
            nc.run([], chat, FakeClient(tools=[TOOLS[3]]))


class LocalToolTests(unittest.TestCase):
    class Tool:
        name = 'kuma_status'
        spec = {'type': 'function', 'function': {'name': 'kuma_status', 'parameters': {}}}

        def __init__(self, fail=False):
            self.fail, self.calls = fail, []

        def call(self, args):
            self.calls.append(args)
            if self.fail:
                raise ValueError('broken')
            return '{"dienste": []}'

    def chat_calling(self, name, seen):
        def chat(messages, specs):
            seen.append((messages, specs))
            if len(seen) == 1:
                return {'content': '', 'tool_calls': [tool_call(name, {'stunden': 24})]}
            return {'content': 'Alles erreichbar.'}
        return chat

    def test_local_tools_run_without_a_nextcloud_client(self):
        tool, seen = self.Tool(), []
        self.assertEqual(nc.run([], self.chat_calling('kuma_status', seen), None, local=[tool]),
                         ('answer', 'Alles erreichbar.'))
        self.assertEqual(tool.calls, [{'stunden': 24}])
        self.assertEqual([s['function']['name'] for s in seen[0][1]], ['kuma_status'])
        self.assertEqual(seen[1][0][-1]['content'], '{"dienste": []}')

    def test_local_tools_next_to_nextcloud_and_failures(self):
        client, seen = FakeClient(), []
        nc.run([], self.chat_calling('kuma_status', seen), client, local=[self.Tool()])
        self.assertEqual({s['function']['name'] for s in seen[0][1]},
                         {'nc_calendar_list_events', 'nc_calendar_create_event',
                          'nc_contacts_search_contacts', 'kuma_status'})
        seen = []
        nc.run([], self.chat_calling('kuma_status', seen), None, local=[self.Tool(fail=True)])
        self.assertEqual(seen[1][0][-1]['content'], 'Fehler: ValueError')
        seen = []   # without a client no Nextcloud tool runs, not even a write
        nc.run([], self.chat_calling('nc_calendar_create_event', seen), None,
               local=[self.Tool()])
        self.assertEqual(seen[1][0][-1]['content'], 'Nicht erlaubt.')
        with self.assertRaises(nc.MCPError):
            nc.run([], self.chat_calling('kuma_status', []), None)


class ConsentTests(unittest.TestCase):
    def test_topic_gate(self):
        for text in ('was steht morgen in meinem kalender', 'trag einen termin ein',
                     'wie ist die telefonnummer von luca', 'setz milch auf die einkaufsliste',
                     'notier dir das'):
            self.assertTrue(nc.wants(text), text)
        for text in ('wie hoch ist der eiffelturm', 'erzähl einen witz'):
            self.assertFalse(nc.wants(text), text)

    def test_write_requests_are_told_from_questions(self):
        for text in ('trag morgen um zehn den zahnarzt ein', 'leg eine aufgabe müll an',
                     'setz milch auf die einkaufsliste', 'plan das meeting für freitag ein'):
            self.assertTrue(nc.writes(text), text)
        for text in ('was steht nächste woche im kalender', 'wann ist der termin beim arzt'):
            self.assertFalse(nc.writes(text), text)

    def test_confirmation_words(self):
        for text in ('Ja.', 'bestätigt', 'ja bitte eintragen', 'Ja, trag es ein'):
            self.assertEqual(nc.confirmation(text), 'confirm', text)
        for text in ('nein', 'abbrechen'):
            self.assertEqual(nc.confirmation(text), 'cancel', text)
        self.assertIsNone(nc.confirmation('wie wird das wetter'))

    def test_pending_belongs_to_one_voice_runs_once_and_expires(self):
        now = [0.0]
        pending = nc.Pending(clock=lambda: now[0])
        proposal = nc.Proposal('nc_calendar_create_todo', {'summary': 'Müll'})
        pending.offer(proposal, 'Ivan')
        self.assertEqual(pending.open_for(), 'Ivan')
        self.assertIs(pending.take('Ivan'), proposal)
        self.assertIsNone(pending.take('Ivan'))            # at most once
        pending.offer(proposal, 'Ivan')
        self.assertIsNone(pending.take('Luca'))            # other voice: dropped
        self.assertIsNone(pending.open_for())
        pending.offer(proposal, 'Ivan')
        now[0] = nc.CONFIRM_SECONDS + 1
        self.assertIsNone(pending.open_for())              # timed out


if __name__ == '__main__':
    unittest.main()
