import datetime
import io
import sys
import threading
import unittest
import zoneinfo
from pathlib import Path
import os
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import agenda  # noqa: E402

TZ = zoneinfo.ZoneInfo('Europe/Berlin')
NOW = datetime.datetime(2026, 10, 9, 7, 5, tzinfo=TZ)

# What Nextcloud (sabre/dav) answers to the expanded calendar-query.
MULTISTATUS = b'''<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
 <d:response><d:href>/remote.php/dav/calendars/ivan/personal/a.ics</d:href>
  <d:propstat><d:prop><cal:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:a
DTSTART:20261009T073000Z
DTEND:20261009T083000Z
SUMMARY:Zahnarzt
END:VEVENT
END:VCALENDAR
</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
 <d:response><d:href>/remote.php/dav/calendars/ivan/personal/b.ics</d:href>
  <d:propstat><d:prop><cal:calendar-data>BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:b
DTSTART;VALUE=DATE:20261009
DTEND;VALUE=DATE:20261010
SUMMARY:Geburtstag
  Anna
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART:20261009T130000Z
SUMMARY:Abgesagt
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
</cal:calendar-data></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
 </d:response>
</d:multistatus>'''


def event(summary, start, end=None, all_day=False):
    return dict(summary=summary, start=start, end=end, all_day=all_day)


class ConfigTests(unittest.TestCase):
    def test_needs_url_user_and_password(self):
        self.assertIsNone(agenda.Config.from_env({}))
        self.assertIsNone(agenda.Config.from_env({'CALDAV_URLS': 'https://x/', 'CALDAV_USER': 'u'}))
        config = agenda.Config.from_env({'CALDAV_URLS': 'https://x/a/, https://x/b/',
                                         'CALDAV_USER': 'u', 'CALDAV_PASSWORD': 'p'})
        self.assertEqual(config.urls, ['https://x/a/', 'https://x/b/'])


class ParseTests(unittest.TestCase):
    def test_multistatus_from_nextcloud(self):
        events = agenda.parse_multistatus(MULTISTATUS, TZ)
        self.assertEqual([e['summary'] for e in events], ['Zahnarzt', 'Geburtstag Anna'])
        self.assertEqual(events[0]['start'], datetime.datetime(2026, 10, 9, 9, 30, tzinfo=TZ))
        self.assertEqual((events[1]['start'], events[1]['all_day']),
                         (datetime.date(2026, 10, 9), True))

    def test_tzid_floating_and_escapes(self):
        text = ('BEGIN:VEVENT\nDTSTART;TZID=Europe/London:20261009T090000\n'
                'SUMMARY:Team\\, Sync\; Raum 2\nEND:VEVENT\n'
                'BEGIN:VEVENT\nDTSTART:20261009T180000\nSUMMARY:\nEND:VEVENT\n')
        first, second = agenda.parse_ics(text, TZ)
        self.assertEqual(first['start'], datetime.datetime(2026, 10, 9, 10, 0, tzinfo=TZ))
        self.assertEqual(first['summary'], 'Team, Sync; Raum 2')
        self.assertEqual(second['start'], datetime.datetime(2026, 10, 9, 18, 0, tzinfo=TZ))
        self.assertEqual(second['summary'], 'Termin ohne Titel')

    def test_request(self):
        seen = {}

        def opener(request, timeout):
            seen.update(method=request.get_method(), headers=dict(request.header_items()),
                        body=request.data, timeout=timeout)
            return io.BytesIO(MULTISTATUS)
        config = agenda.Config(['https://cloud/dav/'], 'ivan', 'secret')
        start, end = agenda.day_bounds(NOW)
        events = agenda.fetch(config, start, end, opener=opener)
        self.assertEqual(len(events), 2)
        self.assertEqual((seen['method'], seen['headers']['Depth']), ('REPORT', '1'))
        self.assertTrue(seen['headers']['Authorization'].startswith('Basic '))
        self.assertIn(b'<c:expand start="20261008T220000Z" end="20261009T220000Z"/>', seen['body'])


class UpcomingTests(unittest.TestCase):
    def test_only_what_is_still_ahead_today(self):
        today = NOW.date()
        events = [
            event('Später', NOW.replace(hour=15)),
            event('Läuft', NOW.replace(hour=7), NOW.replace(hour=8)),
            event('Vorbei', NOW.replace(hour=6), NOW.replace(hour=7, minute=0)),
            event('Endet jetzt', NOW.replace(hour=6), NOW),
            event('Morgen', NOW + datetime.timedelta(days=1)),
            event('Urlaub', today - datetime.timedelta(days=2), today + datetime.timedelta(days=1),
                  all_day=True),
            event('Gestern', today - datetime.timedelta(days=1), today, all_day=True),
            event('Doppelt', NOW.replace(hour=15)), event('Doppelt', NOW.replace(hour=15)),
        ]
        self.assertEqual([e['summary'] for e in agenda.upcoming(events, NOW)],
                         ['Urlaub', 'Läuft', 'Später', 'Doppelt'])


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.fetcher = Mock(return_value=[event('Zahnarzt', NOW.replace(hour=9, minute=30))])
        self.agenda = agenda.Agenda(agenda.Config(['u'], 'u', 'p'), fetcher=self.fetcher,
                                    now=lambda: self.now)

    def test_not_configured(self):
        with patch.dict(os.environ, {'CALDAV_URLS': ''}):
            calendar = agenda.Agenda(fetcher=self.fetcher)
        self.assertFalse(calendar.start().configured)   # no thread, no request
        self.assertIsNone(calendar.today(NOW))
        self.fetcher.assert_not_called()

    def test_today_never_fetches_itself(self):
        self.assertIsNone(self.agenda.today())          # nothing fetched yet
        self.fetcher.assert_not_called()
        self.assertTrue(self.agenda.refresh())
        self.assertEqual([e['summary'] for e in self.agenda.today()], ['Zahnarzt'])
        self.assertEqual(self.agenda.today(NOW.replace(hour=11)), [])   # it is over

    def test_failure_keeps_today_but_not_yesterday(self):
        self.agenda.refresh()
        self.fetcher.side_effect = OSError('nextcloud down')
        self.assertFalse(self.agenda.refresh())
        self.assertEqual(len(self.agenda.today()), 1)
        self.assertEqual(self.agenda.error, 'nextcloud down')
        self.now = NOW + datetime.timedelta(days=1)
        self.agenda.refresh()
        self.assertIsNone(self.agenda.today())

    def test_background_thread(self):
        self.agenda.start()
        self.addCleanup(self.agenda.stop)
        for _ in range(100):
            if self.agenda.today() is not None:
                break
            threading.Event().wait(0.01)
        self.assertEqual(len(self.agenda.today()), 1)


class HeaderTests(unittest.TestCase):
    def test_round_trip(self):
        events = [event('Geburtstag Anna', NOW.date(), NOW.date() + datetime.timedelta(days=1),
                        all_day=True),
                  event('Zahnarzt', NOW.replace(hour=9, minute=30), NOW.replace(hour=10))]
        value = agenda.encode_header(events)
        self.assertEqual(agenda.decode_header(value, TZ), events)
        self.assertEqual(agenda.decode_header(agenda.encode_header([]), TZ), [])
        self.assertIsNone(agenda.encode_header(None))
        self.assertIsNone(agenda.decode_header('', TZ))

    def test_server_converts_to_its_clock(self):
        utc = datetime.datetime(2026, 10, 9, 7, 30, tzinfo=datetime.timezone.utc)
        decoded = agenda.decode_header(agenda.encode_header([event('X', utc)]), TZ)
        self.assertEqual(decoded[0]['start'], datetime.datetime(2026, 10, 9, 9, 30, tzinfo=TZ))

    def test_rejects_garbage(self):
        import base64
        import json

        def header(items):
            return base64.b64encode(json.dumps(items).encode()).decode()
        self.assertIsNone(agenda.decode_header('not base64!', TZ))
        self.assertIsNone(agenda.decode_header(header({'t': 'x'}), TZ))
        self.assertEqual(agenda.decode_header(header([
            {'t': 'naive', 's': '2026-10-09T09:00:00', 'a': False},
            {'t': 'bad', 's': 'gestern', 'a': False},
            {'t': 5, 's': '2026-10-09', 'a': True},
            {'t': 'Lang ' * 40, 's': '2026-10-09', 'a': True},
        ]), TZ), [event(('Lang ' * 40)[:agenda.MAX_TITLE].strip(), datetime.date(2026, 10, 9),
                        all_day=True)])
        self.assertIsNone(agenda.decode_header('A' * (agenda.HEADER_LIMIT + 4), TZ))
        many = [event(f'T{i}', NOW.replace(hour=9)) for i in range(30)]
        self.assertEqual(len(agenda.decode_header(agenda.encode_header(many), TZ)),
                         agenda.MAX_SENT)


class SentenceTests(unittest.TestCase):
    def test_sentences(self):
        self.assertIsNone(agenda.sentence(None))
        self.assertEqual(agenda.sentence([]), "Keine weiteren Termine heute.")
        self.assertIn("Bediener", agenda.sentence(agenda.DENIED))
        text = agenda.sentence([event('Geburtstag Anna', NOW.date(), all_day=True),
                                event('Team, Sync.', NOW.replace(hour=15, minute=0))])
        self.assertEqual(text, "Termine heute. Ganztägig: Geburtstag Anna. 15 Uhr: Team, Sync.")
        many = [event(f'T{i}', NOW.replace(hour=10 + i, minute=15)) for i in range(7)]
        text = agenda.sentence(many, 'full')
        self.assertTrue(text.startswith("Direktiven des Tages. 10 Uhr 15: T0."))
        self.assertTrue(text.endswith("Und 2 weitere."))


if __name__ == '__main__':
    unittest.main()


class DiscoveryTests(unittest.TestCase):
    def test_root_url_finds_event_calendars_and_sends_user_agent(self):
        import io
        from types import SimpleNamespace
        pages = {
            "https://cloud.example/remote.php/dav": b'<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop><d:current-user-principal><d:href>/remote.php/dav/principals/users/ivan/</d:href></d:current-user-principal></d:prop></d:propstat></d:response></d:multistatus>',
            "https://cloud.example/remote.php/dav/principals/users/ivan/": b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:response><d:propstat><d:prop><c:calendar-home-set><d:href>/remote.php/dav/calendars/ivan/</d:href></c:calendar-home-set></d:prop></d:propstat></d:response></d:multistatus>',
            "https://cloud.example/remote.php/dav/calendars/ivan/": b'''<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:response><d:href>/remote.php/dav/calendars/ivan/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/calendars/ivan/personal/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/><c:calendar/></d:resourcetype><c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response>
<d:response><d:href>/remote.php/dav/calendars/ivan/tasks/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/><c:calendar/></d:resourcetype><c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response>
</d:multistatus>''',
        }
        agents = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout):
            agents.append(request.get_header("User-agent"))
            return Response(pages[request.full_url])

        found = agenda.discover("https://cloud.example/remote.php/dav", "x", opener)
        self.assertEqual(found, ["https://cloud.example/remote.php/dav/calendars/ivan/personal/"])
        self.assertEqual(set(agents), {"pi-voice-assistant/1"})
