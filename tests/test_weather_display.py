"""Weather on the display: pictograms, the forecast screen, show/hide in pi-ptt."""
import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import ptt  # noqa: E402
import weather  # noqa: E402
import weather_icons  # noqa: E402

TODAY = datetime.date.today()
DAYS = [dict(date=(TODAY + datetime.timedelta(days=i)).isoformat(), code=code,
             high=high, low=low, rain=rain)
        for i, (code, high, low, rain) in enumerate(
            [(61, 9, 4, 80), (2, 14, 6, 20), (0, 16, 5, 0), (71, 2, -3, 50), (95, 18, 10, 90)])]
STORED = dict(now=7, code=61, high=9, low=4, rain=80, days=DAYS, updated=time.time())


class IconTests(unittest.TestCase):
    def test_codes(self):
        self.assertEqual([weather_icons.kind(c) for c in (0, 2, 3, 45, 53, 63, 73, 95, 999)],
                         ['clear', 'partly', 'cloudy', 'fog', 'drizzle', 'rain', 'snow',
                          'thunder', 'cloudy'])

    def test_sizes_and_animation(self):
        for name in weather_icons.KINDS:
            with self.subTest(name=name):
                self.assertEqual(weather_icons.icon(name, 0, scale=3).size, (72, 72))
                self.assertEqual(weather_icons.icon(name, 0, scale=1).size, (24, 24))
                frames = {weather_icons.grid(name, f).tobytes() for f in range(12)}
                self.assertGreater(len(frames), 1)       # every pictogram moves


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'display-weather.json'

    def test_read_weather(self):
        self.assertIsNone(display.read_weather(self.path))
        self.path.write_text(json.dumps(STORED))
        view = display.read_weather(self.path)
        self.assertEqual((view['now'], len(view['days'])), (7, 5))
        self.path.write_text('kaputt')
        self.assertIsNone(display.read_weather(self.path))

    def test_render_every_day_and_style(self):
        class Capture:
            def image(self, image, rotation=0):
                self.frame = image
        view = dict(weather.view(STORED), updated=STORED['updated'])
        for day in range(5):
            for persona in ('servitor', 'mensch'):
                capture = Capture()
                display.render_weather(capture, view, day, 3, True, dict(clock='07:00'), persona)
                self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))
        stale = dict(view, now=None, updated=time.time() - 2 * 86400)
        display.render_weather(Capture(), stale, 0, 0)

    def test_status_field(self):
        status = Path(self.tmp.name) / 'status.json'
        status.write_text(json.dumps(dict(weather_day='2')))
        self.assertEqual(display.read_status(status)['weather_day'], '2')
        status.write_text(json.dumps(dict(weather_day='9')))
        self.assertNotIn('weather_day', display.read_status(status))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.speech.active = False
        self.speech.synthesizing = False
        self.speech.poll.return_value = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.status_path = Path(self.tmp.name) / 'status.json'
        env = patch.dict(os.environ, {
            'PTT_DISPLAY_STATUS_PATH': str(self.status_path),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json'),
            'PTT_SETTINGS_FILE': str(Path(self.tmp.name) / 'settings.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        out = redirect_stdout(StringIO())
        out.__enter__()
        self.addCleanup(out.__exit__, None, None, None)
        self.c = ptt.VoiceController(self.recorder, self.speech, .04, 30, remote=True)
        self.c.weather = Mock()
        self.c.weather.today.return_value = weather.view(STORED)
        self.spoken = []
        self.c._start_speech = lambda text, **fields: self.spoken.append(text)

    def shown(self):
        return json.loads(self.status_path.read_text()).get('weather_day')

    def test_local_answer_shows_the_asked_day_then_hides(self):
        self.c._start_llm('wie wird das wetter morgen')
        self.assertTrue(self.spoken[-1].startswith('Morgen: 6 bis 14 Grad'))
        self.assertEqual(self.shown(), '1')
        self.speech.poll.return_value = 0                     # answer finished
        self.c.tick(False, (False,) * 5, 1.0)
        self.assertEqual(self.shown(), '1')                   # still a moment
        self.speech.poll.return_value = None
        self.c.weather_hide_at = time.monotonic() - 1
        self.c.tick(False, (False,) * 5, 1.1)
        self.assertIsNone(self.shown())

    def test_briefing_shows_today_or_tomorrow_by_hour_and_ptt_hides(self):
        for hour, day in ((7, '0'), (17, '0'), (18, '1'), (21, '1')):
            moment = datetime.datetime.combine(TODAY, datetime.time(hour))
            with self.subTest(hour=hour), patch('ptt.datetime.datetime', wraps=datetime.datetime) as clock:
                clock.now.return_value = moment
                self.c._start_llm('morgenbericht')
            self.assertEqual(self.shown(), day)
            self.assertIn('Morgen: 6 bis 14 Grad' if day == '1' else 'Außentemperatur 7 Grad',
                          self.spoken[-1])
            self.c.cancel(False, 1.0)
            self.assertIsNone(self.shown())

    def test_nothing_shown_without_stored_weather(self):
        self.c.weather.today.return_value = None
        self.c._start_llm('wie ist das wetter')
        self.assertFalse(self.c.weather_shown)

    def test_server_show_event_waits_for_the_answer(self):
        self.c._remote_progress(dict(event='show', screen='weather', day=2))
        self.assertEqual(self.c.turn_weather_day, 2)
        self.assertFalse(self.c.weather_shown)                # not while thinking
        self.c._remote_progress(dict(event='show', screen='weather', day=9))
        self.assertEqual(self.c.turn_weather_day, 2)          # out of range: ignored


if __name__ == '__main__':
    unittest.main()
