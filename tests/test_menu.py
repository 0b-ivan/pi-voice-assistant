import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from button_shim import LedWriter  # noqa: E402
from menu import ITEMS, Menu  # noqa: E402
from ptt import VoiceController  # noqa: E402
import ptt  # noqa: E402


class MenuTests(unittest.TestCase):
    def test_navigation_wraps_and_confirm(self):
        menu = Menu(timeout=15)
        self.assertFalse(menu.open)
        menu.move(1, 0)  # any button opens at the first item
        self.assertEqual((menu.index, menu.page), (0, 'list'))
        menu.move(-1, 1)
        self.assertEqual(ITEMS[menu.index], 'close')
        menu.move(1, 2)
        self.assertEqual(menu.confirm(3), 'info')
        self.assertEqual(menu.page, 'info')
        menu.move(1, 4)  # leaves the info page, keeps the selection
        self.assertEqual((menu.page, ITEMS[menu.index]), ('list', 'info'))
        menu.index = ITEMS.index('close')
        self.assertEqual(menu.confirm(5), 'close')
        self.assertFalse(menu.open)

    def test_toggles_stay_open_and_timeout_closes(self):
        menu = Menu(timeout=15)
        menu.show(0)
        menu.index = ITEMS.index('led')
        self.assertEqual(menu.confirm(1), 'led')
        self.assertTrue(menu.open)
        self.assertFalse(menu.expire(15.9))
        self.assertTrue(menu.expire(16.1))
        self.assertFalse(menu.open)


class ControllerMenuTests(unittest.TestCase):
    def setUp(self):
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.recorder.take_live_transcript.return_value = None
        self.speech.active = False
        self.speech.poll.return_value = None
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.status_path = Path(self.tmp.name) / 'display-status.json'
        env = patch.dict(os.environ, {'PTT_DISPLAY_STATUS_PATH': str(self.status_path),
                                      'PTT_DISPLAY_EVENT_PATH':
                                          str(Path(self.tmp.name) / 'event.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        self.c = VoiceController(self.recorder, self.speech, .04, 30, remote=True)
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.now = 0
        self.tick()

    def tick(self, down='', pitft=''):
        shim = tuple(x in down for x in 'ABCDE')
        buttons = ('U' in pitft, 'D' in pitft)
        for _ in range(2):
            self.now += .1
            self.c.tick(False, shim, self.now, buttons)

    def press(self, down='', pitft=''):
        self.tick(down, pitft)
        self.tick()

    def status(self):
        return json.loads(self.status_path.read_text())

    def test_pitft_opens_menu_and_e_toggles_server(self):
        self.press(pitft='D')
        self.assertEqual((self.status()['menu_index'], self.status()['menu_page']), (0, 'list'))
        self.press(pitft='D')
        self.assertEqual(ITEMS[self.status()['menu_index']], 'server')
        self.press(down='E')
        self.assertFalse(self.c.remote_enabled)
        self.assertFalse(self.recorder.uplink_enabled)
        self.assertEqual(self.status()['opt_server'], 'off')
        self.speech.start.assert_not_called()  # E confirmed, no status speech
        self.press(down='E')
        self.assertTrue(self.recorder.uplink_enabled)

    def test_b_closes_menu_without_cancelling(self):
        self.press(pitft='U')
        self.press(down='B')
        self.assertNotIn('menu_index', self.status())
        self.recorder.finish.assert_not_called()
        self.speech.stop.assert_not_called()

    def test_screen_off_and_wake(self):
        self.press(pitft='D')
        for _ in range(ITEMS.index('screen')):
            self.press(pitft='D')
        self.press(down='E')
        self.assertEqual(self.status()['screen'], 'off')
        self.assertNotIn('menu_index', self.status())
        self.press(pitft='U')  # wakes only
        self.assertEqual(self.status()['screen'], 'on')
        self.assertNotIn('menu_index', self.status())

    def test_ptt_closes_menu(self):
        self.press(pitft='D')
        self.tick(down='A')
        self.assertNotIn('menu_index', self.status())
        self.recorder.start.assert_called_once()

    def test_lore_item_cycles_and_reaches_snapshot(self):
        self.press(pitft='D')
        self.c.menu.index = ITEMS.index('lore')
        self.assertEqual(self.c.lore, 'light')
        self.press(down='E')
        self.assertEqual((self.c.lore, self.status()['opt_lore']), ('full', 'full'))
        self.assertEqual(self.c.status_snapshot()['lore'], 'full')
        self.press(down='E')
        self.press(down='E')
        self.assertEqual(self.c.lore, 'light')

    def test_local_llm_gets_lore_level(self):
        with patch('ptt.TranscriptionJob') as job:
            self.c._start_llm('wie hoch ist der eiffelturm')
        function = job.call_args.args[0]
        self.assertEqual(function.keywords, {'lore': 'light'})

    def test_menu_status_item_speaks(self):
        self.press(pitft='D')
        self.c.menu.index = ITEMS.index('status')
        with patch('ptt.status_text', return_value='STATUS.'):
            self.press(down='E')
        self.speech.start.assert_called_once_with('STATUS.')
        self.assertFalse(self.c.menu.open)


class FakeShim:
    def __init__(self):
        self.color = None
        self.writes = []
        self.lock = threading.Lock()

    def set_color(self, color):
        time.sleep(0.02)  # a real change takes 50-90 ms
        self.writes.append(color)
        self.color = color


class LedWriterTests(unittest.TestCase):
    def test_writes_latest_colour_in_background_with_spacing(self):
        shim = FakeShim()
        writer = LedWriter(shim, min_interval=0.05)
        started = time.monotonic()
        for value in range(20):  # a burst of requests must not block the caller
            writer.request((value, 0, 0))
        self.assertLess(time.monotonic() - started, 0.02)
        for _ in range(100):
            if shim.color == (19, 0, 0):
                break
            time.sleep(0.01)
        writer.close()
        self.assertEqual(shim.color, (19, 0, 0))
        self.assertLessEqual(len(shim.writes), 3)  # coalesced, not 20 writes

    def test_start_with_led_off_writes_nothing_and_survives(self):
        shim = FakeShim()
        shim.color = (0, 0, 0)  # ButtonShim switches the LED off in __init__
        shim.set_color = Mock(side_effect=lambda c: setattr(shim, 'color', c))
        writer = LedWriter(shim, min_interval=0)
        time.sleep(0.05)
        shim.set_color.assert_not_called()
        writer.request((0, 90, 30))
        for _ in range(100):
            if shim.color == (0, 90, 30):
                break
            time.sleep(0.01)
        writer.close()
        self.assertEqual(shim.color, (0, 90, 30))
        self.assertIsNone(writer.error)

    def test_unexpected_exception_is_reported(self):
        shim = FakeShim()
        shim.set_color = Mock(side_effect=TypeError('bad colour'))
        writer = LedWriter(shim, min_interval=0)
        writer.request((1, 2, 3))
        for _ in range(100):
            if writer.error is not None:
                break
            time.sleep(0.01)
        writer.close()
        self.assertIsInstance(writer.error, OSError)

    def test_error_is_reported_not_raised(self):
        class Broken(FakeShim):
            def set_color(self, color):
                raise OSError('i2c gone')
        writer = LedWriter(Broken(), min_interval=0)
        writer.request((1, 2, 3))
        for _ in range(100):
            if writer.error is not None:
                break
            time.sleep(0.01)
        writer.close()
        self.assertIsInstance(writer.error, OSError)


if __name__ == '__main__':
    unittest.main()
