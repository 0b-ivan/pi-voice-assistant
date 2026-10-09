import io
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import weather  # noqa: E402

PAYLOAD = {
    'current': {'temperature_2m': 11.6, 'weather_code': 3},
    'daily': {'temperature_2m_max': [14.2], 'temperature_2m_min': [6.4],
              'precipitation_probability_max': [40]},
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(weather.parse(PAYLOAD), dict(now=12, code=3, high=14, low=6, rain=40))
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


if __name__ == '__main__':
    unittest.main()
