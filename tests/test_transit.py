import datetime
from pathlib import Path
import sys
import unittest
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import transit  # noqa: E402
from transit_fixture import (FakeResponse, departure, rail_journey, ride, tram_journey,  # noqa: E402
                             trips, utc, walk)

NOW = utc(12, 0)          # 14:00 in Würzburg (CEST)
CONFIG = transit.Config(buffer_minutes=2)


def local(hour, minute, day=10):
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=transit.TZ)


def options(*journeys, mode=None, now=NOW, buffer_minutes=2):
    return transit.trip_options(transit.parse_trips(trips(*journeys)), now, buffer_minutes, mode)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_private_values_only_from_the_environment(self):
        config = transit.Config.from_env({})
        self.assertIsNone(config.home)
        self.assertIsNone(config.city_stop)
        self.assertEqual(config.version, '11.0.6.72')
        self.assertEqual(config.buffer_minutes, 2)
        config = transit.Config.from_env({
            'TRANSIT_HOME': '50.1, 10.2', 'TRANSIT_CITY_STOP': '3700315',
            'TRANSIT_BUFFER_MINUTES': '4', 'TRANSIT_EFA_VERSION': '11.0.7.1',
            'TRANSIT_PLACES': 'Rathaus=3700315, Würzburg Hbf=80001152'})
        self.assertEqual(config.home, (50.1, 10.2))
        self.assertEqual(config.version, '11.0.7.1')
        self.assertEqual(config.places, {'rathaus': '3700315',
                                         'würzburg hauptbahnhof': '80001152'})
        self.assertIsNone(transit.Config.from_env({'TRANSIT_ENABLED': 'off'}))
        self.assertIsNone(transit.Config.from_env({'TRANSIT_HOME': 'irgendwo'}).home)


class RequestTests(unittest.TestCase):
    def test_trip_parameters_from_a_coordinate(self):
        params = transit.trip_params(CONFIG, ('coord', 50.5, 10.25), ('stop', '80001020'), NOW)
        self.assertEqual(params['type_origin'], 'coord')
        self.assertEqual(params['name_origin'], '10.250000:50.500000:WGS84[dd.ddddd]')
        self.assertEqual(params['type_destination'], 'stopID')
        self.assertEqual(params['name_destination'], '80001020')
        self.assertEqual((params['itdDate'], params['itdTime']), ('20261010', '1400'))
        for key, value in dict(outputFormat='rapidJSON', version='11.0.6.72', language='de',
                               sl3plusTripMacro='1', itdTripDateTimeDepArr='dep',
                               useRealtime='1', locationServerActive='1',
                               calcNumberOfTrips='3', ptOptionsActive='1', itOptionsActive='1',
                               allInterchangesAsLegs='1',
                               coordOutputFormat='WGS84[dd.ddddd]').items():
            self.assertEqual(params[key], value, key)

    def test_time_parameters_are_local_across_midnight_and_dst(self):
        # 25.10.2026 00:30 in Würzburg is still summer time (UTC+2) ...
        params = transit.trip_params(CONFIG, ('stop', '80001152'), ('stop', '80001020'),
                                     utc(22, 30, day=24))
        self.assertEqual((params['itdDate'], params['itdTime']), ('20261025', '0030'))
        self.assertEqual(params['type_origin'], 'stopID')
        # ... and after 3:00 it is winter time (UTC+1).
        params = transit.departure_params(CONFIG, '80001152',
                                          datetime.datetime(2026, 10, 25, 5, 0,
                                                            tzinfo=datetime.timezone.utc))
        self.assertEqual((params['itdDate'], params['itdTime']), ('20261025', '0600'))

    def test_fetch_sends_the_agent_and_reports_failures(self):
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, request.get_header('User-agent'), timeout))
            return FakeResponse(trips())
        transit.fetch_json('https://efa.example/XML_TRIP_REQUEST2', {'a': 'b c'}, opener, 3)
        self.assertEqual(seen, [('https://efa.example/XML_TRIP_REQUEST2?a=b+c',
                                 'pi-voice-assistant/1', 3)])

        def broken(request, timeout):
            raise urllib.error.URLError('timed out')
        with self.assertRaises(transit.TransitError) as caught:
            transit.fetch_json('https://efa.example/x', {}, broken)
        self.assertEqual(caught.exception.kind, 'network')
        with self.assertRaises(transit.TransitError) as caught:
            transit.fetch_json('https://efa.example/x', {},
                               lambda request, timeout: FakeResponse(b'<html>'))
        self.assertEqual(caught.exception.kind, 'provider')

    def test_client_caches_briefly(self):
        calls = []
        clock = [0.0]

        def opener(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(trips(tram_journey(utc(12, 32))))
        client = transit.Client(CONFIG, opener=opener, clock=lambda: clock[0])
        client.trips(('stop', '1'), ('stop', '2'), NOW)
        client.trips(('stop', '1'), ('stop', '2'), NOW)
        self.assertEqual(len(calls), 1)
        clock[0] = transit.CACHE_SECONDS + 1
        client.trips(('stop', '1'), ('stop', '2'), NOW)
        self.assertEqual(len(calls), 2)


class ParseTests(unittest.TestCase):
    def test_direct_tram_leave_time_is_departure_minus_walk_minus_buffer(self):
        [option] = options(tram_journey(utc(12, 32)))
        self.assertEqual(option['dep'], local(14, 32))
        self.assertEqual(option['arr'], local(14, 45))
        self.assertEqual(option['walk_minutes'], 10)
        self.assertEqual(option['leave'], local(14, 20))
        self.assertFalse(option['rail'])
        self.assertTrue(option['realtime'])
        first = option['vehicles'][0]
        self.assertEqual((first['label'], first['direction'], first['kind']),
                         ('Straßenbahn 4', 'Sanderau', 'tram'))
        self.assertEqual(option['legs'][0]['distance'], 518)

    def test_walk_is_rounded_up_and_waiting_is_no_walking(self):
        journey = tram_journey(utc(12, 32))
        journey['legs'][0]['duration'] = 541           # 9 min 1 s -> 10 minutes
        journey['legs'][0]['origin']['departureTimePlanned'] = '2026-10-10T12:15:00Z'
        [option] = options(journey)
        self.assertEqual(option['walk_minutes'], 10)
        self.assertEqual(option['leave'], local(14, 20))   # not 14:13: the rest is waiting

    def test_realtime_delay_is_used_only_when_realtime_controlled(self):
        [option] = options(tram_journey(utc(12, 32), delay=3))
        self.assertEqual(option['dep'], local(14, 35))
        self.assertEqual(option['leave'], local(14, 23))
        [option] = options(tram_journey(utc(12, 32), delay=3, realtime=False))
        self.assertEqual(option['dep'], local(14, 32))      # "Estimated" alone proves nothing
        self.assertFalse(option['realtime'])

    def test_rail_journey_starts_with_the_whole_routed_chain(self):
        [option] = options(rail_journey(utc(12, 32), utc(12, 50)))
        self.assertTrue(option['rail'])
        self.assertEqual(option['transfers'], 1)
        self.assertEqual(option['walk_minutes'], 10)
        self.assertEqual(option['leave'], local(14, 20))     # chain start 14:22 - 2
        self.assertEqual([leg['kind'] for leg in option['legs']],
                         ['walk', 'tram', 'walk', 'long'])
        ice = option['vehicles'][1]
        self.assertEqual((ice['label'], ice['platform_dep'], ice['operator']),
                         ('ICE 623', '4', 'DB'))
        self.assertEqual(option['legs'][-1]['platform_arr'], '8')
        self.assertEqual(option['legs'][2]['duration'], 420)  # transfer walk kept

    def test_products_by_train_type_before_class(self):
        self.assertEqual(transit.classify(dict(product={'class': 0, 'name': 'Zug'},
                                               properties=dict(trainType='ICE'))), 'long')
        self.assertEqual(transit.classify(dict(product={'class': 13, 'name': 'Zug'},
                                               properties=dict(trainType='RE'))), 'regional')
        self.assertEqual(transit.classify(dict(product={'class': 0, 'name': 'Zug'})), 'train')
        self.assertEqual(transit.classify(dict(product={'class': 99, 'name': 'Fussweg'})),
                         'walk')
        self.assertEqual(transit.classify(dict(product={'class': 4})), 'tram')

    def test_unusable_journeys_are_dropped(self):
        footpath = dict(legs=[walk(utc(12, 5), 25, to='Rathaus')])
        cancelled = tram_journey(utc(12, 40), cancelled=True)
        missed = tram_journey(utc(12, 8))                     # leave 13:56 < 14:00
        good = tram_journey(utc(12, 32))
        found = options(footpath, cancelled, missed, good, good)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['dep'], local(14, 32))

    def test_at_most_three_by_time_to_leave(self):
        found = options(*[tram_journey(utc(12, 50 - 5 * i)) for i in range(5)])
        self.assertEqual([o['dep'] for o in found], [local(14, 30), local(14, 35), local(14, 40)])

    def test_mode_filters(self):
        bus = tram_journey(utc(12, 30), line='14', cls=5, product='Bus')
        tram = tram_journey(utc(12, 40))
        self.assertEqual([o['vehicles'][0]['label'] for o in options(bus, tram, mode='tram')],
                         ['Straßenbahn 4'])
        regional = dict(legs=[ride(utc(12, 30), 60, line='4612', cls=13, product='Regionalzug',
                                   train='RE', number='4612', origin='Würzburg Hbf',
                                   to='Nürnberg Hbf')])
        unknown = dict(legs=[ride(utc(12, 35), 60, line='1', cls=0, product='Zug',
                                  origin='Würzburg Hbf', to='Nürnberg Hbf')])
        ice = rail_journey(utc(12, 32), utc(12, 50))
        found = options(regional, unknown, ice, tram, mode='regional')
        self.assertEqual([o['vehicles'][0]['label'] for o in found], ['RE 4612'])
        found = options(regional, ice, tram, mode='train')
        self.assertEqual(len(found), 2)

    def test_missing_walk_duration_is_not_invented(self):
        journey = tram_journey(utc(12, 32))
        del journey['legs'][0]['duration']
        del journey['legs'][0]['origin']['departureTimePlanned']
        [option] = options(journey)
        self.assertIsNone(option['walk_minutes'])
        self.assertIsNone(option['leave'])
        journey = tram_journey(utc(12, 32))
        del journey['legs'][1]['destination']['arrivalTimePlanned']
        del journey['legs'][1]['destination']['arrivalTimeEstimated']
        self.assertIsNone(options(journey)[0]['arr'])

    def test_provider_errors_and_warnings(self):
        warning = [dict(type='warning', code=-8011, text='Hinweis', module='BROKER')]
        self.assertEqual(len(transit.parse_trips(trips(tram_journey(utc(12, 32)),
                                                       messages=warning))), 1)
        with self.assertRaises(transit.TransitError) as caught:
            transit.parse_trips(trips(messages=[dict(type='error', code=-4000, text='')]))
        self.assertEqual(caught.exception.kind, 'no_result')
        with self.assertRaises(transit.TransitError) as caught:
            transit.parse_trips(trips(messages=[dict(type='error', code=-1, text='kaputt')]))
        self.assertEqual(caught.exception.kind, 'provider')
        self.assertEqual(transit.parse_trips(trips()), [])

    def test_departures(self):
        board = dict(stopEvents=[
            departure(utc(11, 55), '4610', 'RE', 13, 'Nürnberg Hbf', '5'),       # gone
            departure(utc(12, 10), '623', 'ICE', 16, 'München Hbf', '4', delay=4),
            departure(utc(12, 12), '58012', 'RB', 13, 'Schweinfurt', '2', realtime=False,
                      delay=9),
            departure(utc(12, 20), '4612', 'RE', 13, 'Nürnberg Hbf', '5'),
        ])
        found = transit.departure_options(transit.parse_departures(board), NOW, 'train')
        # By effective departure: the RB without realtime counts as planned (14:12).
        self.assertEqual([o['label'] for o in found], ['RB 58012', 'ICE 623', 'RE 4612'])
        self.assertEqual(found[0]['dep'], local(14, 12))
        self.assertEqual(found[1]['dep'], local(14, 14))
        self.assertEqual(found[1]['platform_dep'], '4')
        found = transit.departure_options(transit.parse_departures(board), NOW, 'regional')
        self.assertEqual([o['label'] for o in found], ['RB 58012', 'RE 4612'])

    def test_stop_finder_prefers_stops_served_by_the_vehicle(self):
        payload = dict(locations=[
            dict(type='stop', name='Würzburg, Hauptbahnhof West', isBest=True, matchQuality=900,
                 productClasses=[4, 5], properties=dict(stopId='80029080')),
            dict(type='stop', name='Würzburg Hbf', matchQuality=880, productClasses=[0, 13, 16],
                 properties=dict(stopId='80001152')),
            dict(type='street', name='Bahnhofstraße')])
        client = transit.Client(CONFIG, opener=lambda request, timeout: FakeResponse(payload))
        self.assertEqual(client.find_stop('würzburg hauptbahnhof', 'train')['id'], '80001152')
        self.assertEqual(client.find_stop('würzburg hauptbahnhof', 'tram')['id'], '80029080')
        alias = transit.Client(transit.Config(places={'rathaus': '3700315'}),
                               opener=None)
        self.assertEqual(alias.find_stop('Rathaus')['id'], '3700315')



class SpelledLetterTests(unittest.TestCase):
    def test_vosk_spelled_abbreviations_are_joined(self):
        self.assertEqual(transit.join_letters('d j k sportzentrum'), 'djk sportzentrum')
        self.assertEqual(transit.join_letters('s bahn'), 's bahn')
        params = transit.stopfinder_params(transit.Config(), 'd j k sportzentrum')
        self.assertEqual(params['name_sf'], 'djk sportzentrum')
        self.assertEqual(transit.normalize_place('D J K Sportzentrum'), 'djk sportzentrum')


if __name__ == '__main__':
    unittest.main()
