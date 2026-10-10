import io
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import weather  # noqa: E402

DATES = ['2026-10-10', '2026-10-11', '2026-10-12', '2026-10-13', '2026-10-14']
PAYLOAD = {
    'current': {'temperature_2m': 11.6, 'weather_code': 3},
    'daily': {'time': DATES, 'weather_code': [3, 61, 0, 71, 95],
              'temperature_2m_max': [14.2, 9.0, 16.4, 2.0, 18.0],
              'temperature_2m_min': [6.4, 4.0, 5.0, -3.4, 10.0],
              'precipitation_probability_max': [40, 80, 0, 50, 90]},
}
DAYS = [dict(date='2026-10-10', code=3, high=14, low=6, rain=40),
        dict(date='2026-10-11', code=61, high=9, low=4, rain=80),
        dict(date='2026-10-12', code=0, high=16, low=5, rain=0),
        dict(date='2026-10-13', code=71, high=2, low=-3, rain=50),
        dict(date='2026-10-14', code=95, high=18, low=10, rain=90)]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(weather.parse(PAYLOAD),
                         dict(now=12, code=3, high=14, low=6, rain=40, days=DAYS))
        self.assertIsNone(weather.parse({}))
        self.assertIsNone(weather.parse({'current': {}, 'daily': {}}))

    def test_fetch_builds_query(self):
        seen = {}

        def opener(url, timeout):
            seen['url'], seen['timeout'] = url.full_url, timeout
            seen['agent'] = url.get_header('User-agent')
            return io.BytesIO(json.dumps(PAYLOAD).encode())
        self.assertEqual(weather.fetch(52.5, 13.4, opener=opener)['now'], 12)
        self.assertIn('latitude=52.5', seen['url'])
        self.assertIn('longitude=13.4', seen['url'])
        self.assertEqual(seen['agent'], 'pi-voice-assistant/1')
        self.assertEqual(seen['timeout'], weather.TIMEOUT_SECONDS)

    def test_location(self):
        self.assertEqual(weather.location({'WEATHER_LAT': '52.5', 'WEATHER_LON': '13.4'}),
                         (52.5, 13.4))
        self.assertIsNone(weather.location({}))
        self.assertIsNone(weather.location({'WEATHER_LAT': 'x', 'WEATHER_LON': '1'}))
        self.assertIsNone(weather.location({'WEATHER_LAT': '95', 'WEATHER_LON': '1'}))


class ForecastTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.calls = 0
        self.result = dict(now=12, code=3, high=14, low=6, rain=40)

    def fetcher(self, lat, lon):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def forecast(self, place=(52.5, 13.4)):
        return weather.Forecast(place, fetcher=self.fetcher, clock=self.clock)

    def test_without_place_never_fetches(self):
        forecast = weather.Forecast(place=None, fetcher=self.fetcher, clock=self.clock)
        forecast.place = None
        self.assertIsNone(forecast.get())
        self.assertIsNone(forecast.cached())
        self.assertEqual(self.calls, 0)

    def test_get_caches(self):
        forecast = self.forecast()
        self.assertEqual(forecast.get()['now'], 12)
        self.clock.now += 60
        forecast.get()
        self.assertEqual(self.calls, 1)
        self.clock.now += weather.CACHE_SECONDS
        forecast.get()
        self.assertEqual(self.calls, 2)

    def test_failure_keeps_old_data_and_retries_later(self):
        forecast = self.forecast()
        forecast.get()
        self.clock.now += weather.CACHE_SECONDS
        self.result = OSError('offline')
        self.assertEqual(forecast.get()['now'], 12)      # still recent enough
        self.clock.now += 30
        forecast.get()
        self.assertEqual(self.calls, 2)                  # waits RETRY_SECONDS
        self.clock.now += 3 * weather.CACHE_SECONDS
        self.assertIsNone(forecast.get())                # too old to be spoken

    def test_cached_refreshes_in_background(self):
        release = threading.Event()

        def slow(lat, lon):
            release.wait(2)
            return self.fetcher(lat, lon)
        forecast = weather.Forecast((52.5, 13.4), fetcher=slow, clock=self.clock)
        self.assertIsNone(forecast.cached())             # never waits for the request
        self.assertIsNone(forecast.cached())             # one request at a time
        release.set()
        for thread in threading.enumerate():
            if thread.name == 'weather':
                thread.join(2)
        self.assertEqual(forecast.cached()['now'], 12)
        self.assertEqual(self.calls, 1)


class SentenceTests(unittest.TestCase):
    def test_sentence(self):
        self.assertIsNone(weather.sentence(None))
        self.assertEqual(weather.sentence(dict(now=12, code=3, high=14, low=6, rain=10)),
                         "Außentemperatur 12 Grad, bedeckt. Heute 6 bis 14 Grad.")

    def test_frost_and_rain(self):
        text = weather.sentence(dict(now=-2, code=61, high=3, low=-4, rain=70), 'full')
        self.assertIn("minus 2 Grad, Regen.", text)
        self.assertIn("Heute minus 4 bis 3 Grad.", text)
        self.assertIn("Niederschlag wahrscheinlich, 70 Prozent.", text)
        self.assertIn("Feuchtigkeit", text)
        self.assertIn("Niederschlag möglich",
                      weather.sentence(dict(now=5, code=None, high=None, low=None, rain=40)))

    def test_tomorrow_and_weekday(self):
        data = dict(now=12, code=3, high=14, low=6, rain=40, days=DAYS)
        self.assertEqual(weather.day_sentence(data, 1),
                         "Morgen: 4 bis 9 Grad, Regen. Niederschlag wahrscheinlich, 80 Prozent.")
        self.assertEqual(weather.day_sentence(data, 3),
                         "Dienstag: minus 3 bis 2 Grad, Schnee. Niederschlag möglich, 50 Prozent.")
        self.assertIsNone(weather.day_sentence(data, 7))
        self.assertTrue(weather.day_sentence(data, 0).startswith("Außentemperatur 12 Grad"))


class KeeperTests(unittest.TestCase):
    """Pi: five days kept on the memory stick, read offline by date."""
    TODAY = __import__('datetime').date(2026, 10, 10)

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.stick = Path(self.tmp.name) / 'stick'
        self.stick.mkdir()
        self.plugged = True
        self.now = 1_000_000.0
        self.result = weather.parse(PAYLOAD)
        self.display = Path(self.tmp.name) / 'display-weather.json'

    def keeper(self):
        def fetcher(lat, lon):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result
        return weather.Keeper((52.5, 13.4), fetcher=fetcher,
                              stores=lambda: [self.stick / 'weather.json'] if self.plugged else [],
                              clock=lambda: self.now, display=lambda: self.display)

    def test_refresh_stores_on_the_stick_only(self):
        keeper = self.keeper()
        self.assertTrue(keeper.refresh())
        stored = json.loads((self.stick / 'weather.json').read_text())
        self.assertEqual((stored['updated'], len(stored['days'])), (self.now, 5))
        self.assertEqual(json.loads(self.display.read_text())['days'], DAYS)
        self.assertEqual(keeper.today(self.TODAY)['now'], 12)

    def test_without_stick_only_in_memory(self):
        self.plugged = False
        keeper = self.keeper()
        keeper.refresh()
        self.assertFalse((self.stick / 'weather.json').exists())
        self.assertEqual(keeper.today(self.TODAY)['high'], 14)

    def test_offline_after_restart_matches_the_date(self):
        self.keeper().refresh()
        self.result = OSError('offline')
        self.now += 2 * 86400                         # two days later, no network
        keeper = self.keeper()
        self.assertFalse(keeper.refresh())
        keeper.load()
        later = __import__('datetime').date(2026, 10, 12)
        today = keeper.today(later)
        self.assertEqual((today['now'], today['code'], today['high']), (None, 0, 16))
        self.assertEqual([d['date'] for d in today['days']], DATES[2:])
        self.assertTrue(weather.sentence(today).startswith("Heute 5 bis 16 Grad, klar."))
        self.assertIsNone(keeper.today(__import__('datetime').date(2026, 10, 20)))  # too old

    def test_current_temperature_only_while_fresh(self):
        keeper = self.keeper()
        keeper.refresh()
        self.now += weather.CURRENT_FRESH_SECONDS + 1
        self.assertIsNone(keeper.today(self.TODAY)['now'])

    def test_not_configured_and_broken_files(self):
        keeper = weather.Keeper(place=None, stores=lambda: [], display=lambda: self.display)
        keeper.place = None
        self.assertFalse(keeper.start().configured)
        (self.stick / 'weather.json').write_text('{"days": "kaputt"}')
        self.assertIsNone(self.keeper().load())


if __name__ == '__main__':
    unittest.main()
