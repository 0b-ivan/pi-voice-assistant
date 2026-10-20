import datetime
import http.server
import json
import os
import sys
import threading
import unittest
import zoneinfo
from unittest.mock import patch
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
        # Only the calendar list is read to pick a real calendar; nothing is written.
        self.assertEqual([name for name, _ in client.calls], ['nc_calendar_list_calendars'])
        self.assertEqual(proposal.question(),
                         'Soll ich den Termin „Zahnarzt“ am 21.10. um 10:00 Uhr im Kalender '
                         'privat eintragen? Sag ja oder nein.')

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


class CalendarArgumentTests(unittest.TestCase):
    def test_events_without_a_calendar_search_all_calendars(self):
        # The server refuses list_events without a name unless all calendars are
        # searched; the model then said "keine Termine" (web test 20.10.2026).
        client = FakeClient()
        calls = [tool_call('nc_calendar_list_events',
                           {'start_date': '2026-10-20', 'end_date': '2027-01-20'}, 'c1'),
                 tool_call('nc_calendar_list_events',
                           {'calendar_name': 'privat', 'start_date': '2026-10-20'}, 'c2')]
        replies = iter([{'content': '', 'tool_calls': calls}, {'content': 'Annika feiert.'}])
        nc.run([], lambda messages, specs: next(replies), client)
        self.assertEqual([c for c in client.calls if c[0] != 'nc_calendar_list_calendars'], [
            ('nc_calendar_list_events', {'start_date': '2026-10-20', 'end_date': '2027-01-20',
                                         'search_all_calendars': True}),
            ('nc_calendar_list_events', {'calendar_name': 'privat',
                                         'start_date': '2026-10-20'})])
        self.assertIn('alle durchsuchen', nc.TOOL_HINT)
        self.assertIn('nie, es gäbe keine Einträge', nc.TOOL_HINT)
        self.assertIn('in welchem Kalender er steht', nc.TOOL_HINT)
        import llm
        self.assertEqual(llm.speech_text('Theon Impfen, im Kalender Luca&Ivan.'),
                         'Theon Impfen, im Kalender Luca und Ivan.')


class TidyEventTests(unittest.TestCase):
    TZ = zoneinfo.ZoneInfo('Europe/Berlin')

    def tidy(self, events, region='BY'):
        return json.loads(nc.tidy_events(json.dumps({'events': events}), self.TZ, region))

    def test_local_time_all_day_and_order(self):
        # Recorded 20.10.2026: Musikwiege is stored as 10:00Z, its title says 10.00 Uhr.
        data = self.tidy([
            dict(summary='Musikwiege M04/26, Montag, 10.00 Uhr', start='2026-10-26T10:00:00+00:00',
                 end='2026-10-26T10:45:00+00:00', calendar_display_name='Luca&Ivan'),
            dict(summary='Einweihungsparty  Annika', start='2026-10-31', end='2026-11-01',
                 all_day=True, calendar_display_name='Privat'),
            dict(summary='Urlaub', start='2026-10-28', end='2026-11-02', all_day=True,
                 calendar_display_name='Privat', description='Koffer\n packen'),
            dict(summary='Zahnarzt', start='2026-10-28T09:30:00+01:00',
                 end='2026-10-28T10:30:00+01:00', calendar_display_name='Privat',
                 location='Praxis Dr. H\nHuttenstraße 24A'),
            dict(summary='Telefonat', start='2026-10-28T18:00:00', calendar_name='privat')])
        self.assertEqual(data['termine'], [
            dict(datum='Mo 26.10.2026', titel='Musikwiege M04/26, Montag, 10.00 Uhr',
                 zeit='11:00 bis 11:45', kalender='Luca&Ivan'),
            dict(datum='Mi 28.10.2026', titel='Urlaub', zeit='ganztägig bis So 1.11.2026',
                 kalender='Privat', notiz='Koffer packen'),
            dict(datum='Mi 28.10.2026', titel='Zahnarzt', zeit='09:30 bis 10:30',
                 kalender='Privat', ort='Praxis Dr. H'),
            dict(datum='Mi 28.10.2026', titel='Telefonat', zeit='18:00', kalender='privat'),
            dict(datum='Sa 31.10.2026', titel='Einweihungsparty Annika', zeit='ganztägig',
                 kalender='Privat')])
        self.assertIn('es gilt', data['hinweis'])

    def test_holidays_only_where_they_apply(self):
        holidays = [dict(summary=title, start=day, end=day, all_day=True,
                         calendar_display_name='Feiertage', description='Lange Erklärung')
                    for title, day in (('Reformationstag  (BB, MVP, SN)', '2026-10-31'),
                                       ('Allerheiligen  (BW, BY, NRW)', '2026-11-01'),
                                       ('Bußtag  (Saxony)', '2026-11-18'),
                                       ('Weihnachten ', '2026-12-25'))]
        titles = [t['titel'] for t in self.tidy(holidays)['termine']]
        self.assertEqual(titles, ['Allerheiligen', 'Weihnachten'])
        self.assertNotIn('notiz', self.tidy(holidays)['termine'][0])
        self.assertIn('Reformationstag', [t['titel'] for t in self.tidy(holidays, 'BB')['termine']])

    def test_single_event_other_results_and_the_size_limit(self):
        one = json.loads(nc.tidy_events(json.dumps(dict(
            title='Theon U5', start_datetime='2026-11-23T09:00:00+01:00',
            end_datetime='2026-11-23T10:00:00+01:00')), self.TZ))
        self.assertEqual(one['termine'][0]['zeit'], '09:00 bis 10:00')
        for text in ('kein json', '{"calendars": []}', '[1, 2]'):
            self.assertEqual(nc.tidy_events(text, self.TZ), text)
        many = [dict(summary='Termin ' + 'x' * 80, start=f'2026-11-{d:02d}T09:00:00+01:00',
                     calendar_display_name='Privat') for d in range(1, 29)] * 3
        text = nc.tidy_events(json.dumps({'events': many}), self.TZ)
        self.assertLessEqual(len(text), nc.MAX_RESULT_CHARS)
        self.assertGreater(json.loads(text)['weitere_termine_danach'], 0)


class AgendaEventTests(unittest.TestCase):
    def test_events_for_the_briefing_in_the_pi_format(self):
        tz = zoneinfo.ZoneInfo('Europe/Berlin')
        reply = json.dumps({'events': [
            dict(summary='Musikwiege', start='2026-10-26T10:00:00+00:00',
                 end='2026-10-26T10:45:00+00:00', calendar_display_name='Luca&Ivan'),
            dict(summary='Einweihungsparty  Annika', start='2026-10-31', end='2026-11-01',
                 all_day=True, calendar_display_name='Privat'),
            dict(summary='Reformationstag  (BB, SN)', start='2026-10-31', end='2026-11-01',
                 all_day=True, calendar_display_name='Feiertage')]})

        class Client(FakeClient):
            def call(self, name, args):
                self.calls.append((name, args))
                return reply

        client = Client()
        start = datetime.datetime(2026, 10, 26, tzinfo=tz)
        events = nc.agenda_events(client, start, tz=tz)
        self.assertEqual(client.calls[0][1], {'start_date': '2026-10-26', 'end_date': '2026-10-28',
                                              'limit': 100, 'search_all_calendars': True})
        self.assertEqual(events[0], dict(summary='Musikwiege', all_day=False,
                                         start=datetime.datetime(2026, 10, 26, 11, tzinfo=tz),
                                         end=datetime.datetime(2026, 10, 26, 11, 45, tzinfo=tz)))
        self.assertEqual(events[1], dict(summary='Einweihungsparty Annika', all_day=True,
                                         start=datetime.date(2026, 10, 31),
                                         end=datetime.date(2026, 11, 1)))
        self.assertEqual(len(events), 2)                         # no Reformationstag in BY
        import agenda
        self.assertEqual(agenda.sentence(agenda.upcoming(events, start.replace(hour=7)))
                         , 'Termine heute. 11 Uhr: Musikwiege.')


class CalendarChoiceTests(unittest.TestCase):
    CALENDARS = json.dumps({'calendars': [
        {'name': 'contact_birthdays', 'display_name': 'Geburtstage'},
        {'name': 'lucaivan', 'display_name': 'Luca&Ivan'},
        {'name': 'privat', 'display_name': 'Privat'},
        {'name': 'x_shared_by_medvidec', 'display_name': 'WG-Kalender'}]})

    class Client(FakeClient):
        def call(self, name, args):
            self.calls.append((name, args))
            return CalendarChoiceTests.CALENDARS

    def test_named_display_default_and_time_zone(self):
        client = self.Client()
        chosen = nc.resolve_calendar(client, {'title': 'T', 'start_datetime': '2026-10-24T10:00:00',
                                              'calendar_name': 'Luca&Ivan'}, 'Europe/Berlin')
        self.assertEqual((chosen['calendar_name'], chosen['_display'], chosen['timezone']),
                         ('lucaivan', 'Luca&Ivan', 'Europe/Berlin'))
        proposal = nc.Proposal('nc_calendar_create_event', chosen)
        self.assertNotIn('_display', proposal.arguments)
        self.assertIn('im Kalender Luca und Ivan eintragen', proposal.question())
        chosen = nc.resolve_calendar(client, {'title': 'T', 'start_datetime': '2026-10-24T10:00:00Z'})
        self.assertEqual(chosen['calendar_name'], 'privat')        # SERVITOR_NEXTCLOUD_CALENDAR
        self.assertNotIn('timezone', chosen)                       # already has an offset
        with patch.dict(os.environ, {'SERVITOR_NEXTCLOUD_CALENDAR': 'gibtsnicht'}):
            chosen = nc.resolve_calendar(client, {'title': 'T', 'start_datetime': '2026-10-24'})
        self.assertEqual(chosen['calendar_name'], 'lucaivan')      # first own calendar
        self.assertNotIn('timezone', chosen)                       # all day

    def test_reading_with_a_display_or_unknown_name(self):
        client = self.Client()
        self.assertEqual(nc.read_calendar(client, 'nc_calendar_list_events',
                                          {'calendar_name': 'Privat'})['calendar_name'], 'privat')
        self.assertEqual(nc.read_calendar(client, 'nc_calendar_list_events',
                                          {'calendar_name': 'Luca&Ivan'})['calendar_name'],
                         'lucaivan')
        unknown = nc.read_calendar(client, 'nc_calendar_list_events',
                                   {'calendar_name': 'personal', 'start_date': '2026-10-26'})
        self.assertEqual(nc._arguments_for('nc_calendar_list_events', unknown),
                         {'start_date': '2026-10-26', 'search_all_calendars': True})
        self.assertEqual(nc.read_calendar(client, 'nc_calendar_get_upcoming_events',
                                          {'calendar_name': 'personal'})['calendar_name'], '')
        self.assertEqual(nc.read_calendar(client, 'nc_calendar_list_events', {}), {})

    def test_a_proposed_event_gets_a_real_calendar(self):
        client = self.Client()
        replies = iter([{'content': '', 'tool_calls': [tool_call(
            'nc_calendar_create_event', {'title': 'Zahnarzt', 'start_datetime': '2026-10-24T10:00:00',
                                         'calendar_name': 'personal'})]}])
        kind, proposal = nc.run([], lambda m, s: next(replies), client)
        self.assertEqual(kind, 'propose')
        self.assertEqual(proposal.arguments['calendar_name'], 'privat')
        self.assertIn('im Kalender Privat', proposal.question())


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
        self.assertEqual(seen[1][0][-1]['content'], 'Fehler, keine Daten erhalten: ValueError')
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
