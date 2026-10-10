"""Journeys on the display: pictograms, the connection screen, show/hide in pi-ptt."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import display  # noqa: E402
import journeys  # noqa: E402
import ptt  # noqa: E402
import transit  # noqa: E402
import transit_icons  # noqa: E402
from controller_fixture import Presser, make_controller  # noqa: E402
from test_journey_paths import install, wait  # noqa: E402
from transit_fixture import departure, rail_journey, tram_journey, utc  # noqa: E402

NOW = utc(12, 0).astimezone(transit.TZ)


class Capture:
    def image(self, image, rotation=0):
        self.frame = image


def trip_view(*items, selected=None, asking=False, target='Nürnberg Hbf'):
    options = transit.trip_options(transit.parse_trips(dict(journeys=list(items))), NOW, 2)
    return journeys.screen_view(options, dict(kind='trip', destination_name=target), selected,
                                asking, NOW)


class IconTests(unittest.TestCase):
    def test_every_kind_has_a_distinct_pictogram(self):
        seen = set()
        for kind in transit_icons.KINDS:
            image = transit_icons.icon(kind, scale=2)
            self.assertEqual(image.size, (32, 32))
            seen.add(image.tobytes())
        self.assertGreaterEqual(len(seen), 6)       # 'train' and 'other' may share one
        self.assertEqual(transit_icons.icon('unbekannt', scale=1).size, (16, 16))
        self.assertEqual(transit_icons.walker().size, (8, 8))


class ViewTests(unittest.TestCase):
    def test_rows_carry_what_is_said(self):
        view = trip_view(tram_journey(utc(12, 32)), tram_journey(utc(12, 42), delay=3),
                         tram_journey(utc(12, 52), realtime=False), tram_journey(utc(13, 2)),
                         target='Rathaus')
        self.assertEqual(view['title'], 'FAHRTEN → Rathaus')
        self.assertEqual(len(view['rows']), 3)
        first, late, planned = view['rows']
        self.assertEqual((first['icon'], first['label'], first['direction']),
                         ('tram', '4', 'Sanderau'))
        self.assertEqual((first['dep'], first['arr'], first['leave'], first['walk']),
                         ('14:32', '14:45', '14:20', 10))
        self.assertEqual((late['dep'], late['delay'], late['live']), ('14:45', 3, True))
        self.assertFalse(planned['live'])

    def test_rail_row_shows_the_main_train(self):
        view = trip_view(rail_journey(utc(12, 32), utc(12, 50)), selected=0, asking=True)
        row = view['rows'][0]
        self.assertEqual((row['icon'], row['label'], row['platform'], row['transfers']),
                         ('long', 'ICE 623', '4', 1))
        self.assertEqual(row['dep'], '14:32')        # the journey starts with the tram
        self.assertEqual((view['selected'], view['ask']), (0, True))

    def test_next_day_is_marked(self):
        view = journeys.screen_view(
            transit.trip_options(transit.parse_trips(dict(journeys=[tram_journey(utc(22, 12))])),
                                 NOW, 2),
            dict(kind='trip', destination_name='Rathaus'), None, False, NOW)
        self.assertEqual(view['rows'][0]['dep'], '00:12 +1')


class ScreenTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'display-journeys.json'

    def test_read_validates_the_file(self):
        self.assertIsNone(display.read_journeys(self.path))
        self.path.write_text('kaputt')
        self.assertIsNone(display.read_journeys(self.path))
        view = trip_view(tram_journey(utc(12, 32)), rail_journey(utc(12, 47), utc(13, 5)))
        bad = dict(view, selected=7, rows=view['rows'] + [dict(dep='<script>'), 'x'])
        bad['rows'][0] = dict(bad['rows'][0], icon='rocket', label='x' * 50, walk=-3)
        self.path.write_text(json.dumps(bad))
        read = display.read_journeys(self.path)
        self.assertEqual(len(read['rows']), 2)
        self.assertEqual(read['rows'][0]['icon'], 'other')
        self.assertEqual(len(read['rows'][0]['label']), 10)
        self.assertIsNone(read['rows'][0]['walk'])
        self.assertIsNone(read['selected'])

    def test_render_trips_and_departures(self):
        views = [trip_view(tram_journey(utc(12, 32)), tram_journey(utc(12, 42), delay=3),
                           tram_journey(utc(12, 52), realtime=False), target='Rathaus'),
                 trip_view(rail_journey(utc(12, 32), utc(12, 50), delay=4),
                           rail_journey(utc(12, 47), utc(13, 20)), selected=1, asking=True)]
        board = transit.departure_options(transit.parse_departures(dict(stopEvents=[
            departure(utc(12, 10), '623', 'ICE', 16, 'München Hbf', '4', delay=4),
            departure(utc(12, 12), '58012', 'RB', 13, 'Schweinfurt Hbf', '2', realtime=False),
        ])), NOW)
        views.append(journeys.screen_view(board, dict(kind='departures',
                                                      origin_name='Würzburg Hbf'),
                                          None, False, NOW))
        for view in views:
            self.path.write_text(json.dumps(view))
            capture = Capture()
            display.render_journeys(capture, display.read_journeys(self.path), True,
                                    dict(clock='14:00'))
            self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))
            self.assertIsNotNone(capture.frame.getbbox())

    def test_status_field(self):
        status = Path(self.path.parent) / 'status.json'
        status.write_text(json.dumps(dict(journeys='on')))
        self.assertEqual(display.read_status(status).get('journeys'), 'on')
        status.write_text(json.dumps(dict(journeys='yes')))
        self.assertNotIn('journeys', display.read_status(status))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller(self)
        self.p = Presser(self.c)
        self.j = install(self.c, [tram_journey(utc(12, 32)), tram_journey(utc(12, 47))])
        self.file = self.c.config.runtime_dir / 'display-journeys.json'

    def test_answer_shows_the_connections_until_hold_or_button(self):
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        wait(self.c, self.p)
        self.assertTrue(self.c.journey_shown)
        self.assertEqual(ptt._display_status.get('journeys'), 'on')
        shown = json.loads(self.file.read_text())
        self.assertEqual(len(shown['rows']), 2)
        self.c._start_llm('die zweite')
        self.assertEqual(json.loads(self.file.read_text())['selected'], 1)
        self.c.speech.poll.return_value = 0          # the answer has been said
        self.p.tick()
        self.c.speech.poll.return_value = None
        self.assertIsNotNone(self.c.journey_hide_at)
        self.c.journey_hide_at = 0.0
        self.p.tick()
        self.assertFalse(self.c.journey_shown)
        self.assertNotIn('journeys', ptt._display_status)
        self.assertFalse(self.file.exists())

    def test_failed_query_hides_old_connections(self):
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        wait(self.c, self.p)
        self.j.client.error = transit.TransitError('network', 'timeout')
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        wait(self.c, self.p)
        self.assertFalse(self.c.journey_shown)

    def test_button_b_hides(self):
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        wait(self.c, self.p)
        self.p.press('B')
        self.assertFalse(self.c.journey_shown)


if __name__ == '__main__':
    unittest.main()
