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
    def test_groups_navigation_and_back(self):
        menu = Menu(timeout=15)
        self.assertFalse(menu.open)
        menu.move(1, 0)  # any button opens at the first group
        self.assertEqual((menu.index, menu.group, menu.page), (0, None, 'list'))
        menu.move(-1, 1)
        self.assertEqual(menu.items[menu.index], 'close')
        menu.move(1, 2)
        menu.move(-1, 2)
        menu.move(-1, 2)                               # 'system'
        self.assertIsNone(menu.confirm(3))             # opens the group
        self.assertEqual((menu.group, menu.items[menu.index]), ('system', 'info'))
        self.assertEqual(menu.confirm(3), 'info')
        self.assertEqual(menu.page, 'info')
        menu.move(1, 4)  # leaves the info page, keeps the selection
        self.assertEqual((menu.page, menu.items[menu.index]), ('list', 'info'))
        self.assertFalse(menu.back(5))                 # group -> top level
        self.assertEqual((menu.group, menu.items[menu.index]), (None, 'system'))
        self.assertTrue(menu.back(6))                  # top level -> closed
        self.assertFalse(menu.open)
        menu.select('close')
        self.assertEqual(menu.confirm(7), 'close')
        self.assertFalse(menu.open)

    def test_back_item_returns_to_top(self):
        menu = Menu()
        menu.select('screen')
        menu.index = len(menu.items) - 1               # 'Zurück'
        self.assertIsNone(menu.confirm(0))
        self.assertEqual((menu.group, menu.items[menu.index]), (None, 'system'))

    def test_toggles_stay_open_and_timeout_closes(self):
        menu = Menu(timeout=15)
        menu.select('led')
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
        self.settings_path = Path(self.tmp.name) / 'settings.json'
        env = patch.dict(os.environ, {'PTT_DISPLAY_STATUS_PATH': str(self.status_path),
                                      'PTT_DISPLAY_EVENT_PATH':
                                          str(Path(self.tmp.name) / 'event.json'),
                                      'PTT_SETTINGS_FILE': str(self.settings_path)})
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
        self.press(pitft='D')                          # 'Persönlichkeit' -> 'Sprache'
        self.press(down='E')                           # into 'Sprache'
        self.assertEqual(self.status()['menu_group'], 'voice')
        for _ in range(2):
            self.press(pitft='D')
        self.assertEqual(self.c.menu.items[self.status()['menu_index']], 'server')
        self.press(down='E')
        self.assertFalse(self.c.remote_enabled)
        self.assertFalse(self.recorder.uplink_enabled)
        self.assertEqual(self.status()['opt_server'], 'off')
        self.speech.start.assert_not_called()  # E confirmed, no status speech
        self.press(down='E')
        self.assertTrue(self.recorder.uplink_enabled)

    def test_network_group_navigation(self):
        self.c.menu.select('wifi_scan')
        self.assertEqual(self.c.menu.group, 'network')
        self.assertEqual(self.c.menu.items[self.c.menu.index], 'wifi_scan')
        self.assertTrue('wifi_saved' in self.c.menu.items)
        self.c.menu.back(self.now)
        self.assertEqual(self.c.menu.group, None)

    def test_network_info_opens_refreshes_and_closes_without_network_changes(self):
        self.c.menu.select('net_info')
        initial = ["LAN: verbunden", "LAN-IP: 192.168.1.38/24",
                   "WLAN: getrennt", "DNS: 192.168.1.1"]
        with patch('ptt.network_info.rows', return_value=initial) as collector, \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()):
            self.press(down='E')
            self.assertFalse(self.c.menu.open)
            self.assertEqual(self.c.picker['action'], 'network_info')
            self.assertEqual(self.c.picker['title'], 'NETZWERKINFO')
            self.assertIn('LAN-IP: 192.168.1.38/24',
                          [x[1] for x in self.c.picker['items']])
            self.press(down='E')
            self.assertEqual(collector.call_count, 2)
            self.press(down='B')
            self.assertIsNone(self.c.picker)
        self.assertNotIn('192.168.1.38', self.status_path.read_text())

    def test_network_info_closed_during_async_fetch_does_not_reopen(self):
        self.c.picker = dict(action='network_info', title='NETZWERKINFO',
                             index=0, items=[('loading', 'Lade Netzwerkdaten...')])
        tasks = []
        with patch.object(self.c, '_wifi_job',
                          side_effect=lambda fn: tasks.append(fn)):
            self.c._network_info_refresh(self.c.picker)
        self.c._picker_buttons(confirm=False)
        self.assertIsNone(self.c.picker)
        with patch('ptt.network_info.rows', return_value=['LAN: verbunden']):
            tasks[0]()
        self.assertIsNone(self.c.picker)

    def test_access_point_submenu_navigation_and_back(self):
        menu = Menu()
        menu.select('hotspot')
        self.assertEqual(menu.group, 'network')
        self.assertEqual(menu.confirm(1), None)
        self.assertEqual((menu.group, menu.items[menu.index]), ('hotspot', 'wifi_hotspot_start'))
        menu.back(2)
        self.assertEqual((menu.group, menu.items[menu.index]), ('network', 'hotspot'))
        self.assertEqual(menu.confirm(3), None)
        self.assertEqual(menu.group, 'hotspot')
        menu.index = len(menu.items) - 1
        self.assertIsNone(menu.confirm(4))
        self.assertEqual(menu.group, 'network')
        menu.back(5)
        self.assertIsNone(menu.group)
        self.assertEqual(menu.items[menu.index], 'network')
        for name in ('people', 'persona'):
            menu.select(name)
            self.assertEqual(menu.confirm(6), name)  # group also names an action

    def test_wifi_scan_does_not_block_and_only_saved_can_connect(self):
        with patch.object(self.c.wifi, 'available', return_value=True), \
             patch.object(self.c.wifi, 'saved', return_value=[
                dict(uuid='11111111-2222-3333-4444-555555555555',
                     ssid='Home', name='Home')]), \
             patch.object(self.c.wifi, 'scan', return_value=[
                ('Home', 88), ('Unknown', 50)]):
            self.c.menu.select('wifi_scan')
            self.press(down='E')
            import time
            deadline = time.monotonic() + 2
            while self.c.wifi_busy and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(self.c.picker['items'][0][0], 'Home')
            self.assertIn('Unknown', [x[0] for x in self.c.picker['items']])
            self.c.picker['index'] = 1
            with patch.object(self.c.wifi, 'connect') as connect:
                self.press(down='E')
                connect.assert_not_called()
                self.assertIn('Hotspot', self.c.wifi_notes[-1])

    def test_hotspot_menu_starts_worker_without_root_in_voice_loop(self):
        self.c.menu.select('wifi_hotspot_start')
        with patch.object(self.c, '_wifi_job', return_value=True) as work:
            self.press(down='E')
        work.assert_called_once()
        self.assertEqual(self.c.picker['action'], 'wifi_setup')
        self.assertNotIn('menu_index', self.status())

    def test_show_running_hotspot_displays_password_without_logging(self):
        self.c.menu.select('wifi_hotspot_show')
        with patch('ptt.hotspot_control.is_active', return_value=True), \
             patch('ptt.hotspot_control.read_info', return_value={
                 'ssid': 'Proximus-Setup', 'password': 'random-setup-1234',
                 'url': 'http://192.168.77.1/',
                 'expires_at': time.time() + 900}), \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()) as work, \
             patch('ptt.people.write_view') as show:
            self.press(down='E')
        work.assert_called_once()
        self.assertEqual(self.c.picker['title'], 'HOTSPOT BEREIT')
        self.assertEqual(self.c.picker['index'], 1)
        self.assertIn('random-setup-1234', self.c.picker['items'][1][1])
        self.assertEqual(show.call_args.args[1]['page'], 'wifi_qr')
        self.assertEqual(show.call_args.args[1]['ssid'], 'Proximus-Setup')
        self.assertEqual(show.call_args.args[1]['password'], 'random-setup-1234')
        self.assertNotIn('random-setup-1234', self.status_path.read_text())
        self.assertNotIn('random-setup-1234', self.output.getvalue())

    def test_hotspot_e_toggles_wifi_qr_and_text_b_clears_private_file(self):
        from dataclasses import replace
        self.c.config = replace(self.c.config, runtime_dir=Path(self.tmp.name))
        self.c.menu.select('wifi_hotspot_show')
        viewpath = self.c.config.runtime_dir / 'display-people.json'
        with patch('ptt.hotspot_control.is_active', return_value=True), \
             patch('ptt.hotspot_control.read_info', return_value={
                 'ssid': 'Proximus-Setup', 'password': 'testPassword12345',
                 'url': 'http://192.168.77.1/',
                 'expires_at': time.time() + 900}), \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()):
            self.press(down='E')
        self.assertEqual(self.c.picker['page'], 'wifi_qr')
        self.assertEqual(json.loads(viewpath.read_text())['page'], 'wifi_qr')
        self.press(down='E')
        self.assertEqual(self.c.picker['page'], 'list')
        self.assertIn('Code: testPassword12345', viewpath.read_text())
        self.press(down='E')
        self.assertEqual(self.c.picker['page'], 'wifi_qr')
        self.press(down='B')
        self.assertIsNone(self.c.picker)
        self.assertFalse(viewpath.exists())
        self.assertNotIn('testPassword12345', self.status_path.read_text())
        self.assertNotIn('testPassword12345', self.output.getvalue())

    def test_show_off_hotspot_does_not_start_unit(self):
        self.c.menu.select('wifi_hotspot_show')
        with patch('ptt.hotspot_control.is_active', return_value=False), \
             patch('ptt.hotspot_control.control') as ctl, \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()):
            self.press(down='E')
        ctl.assert_not_called()
        self.assertEqual(self.c.picker['title'], 'ACCESS POINT AUS')
        self.assertIn('Erst AP starten', self.c.picker['items'][0][1])

    def test_rotate_hotspot_stops_then_starts_before_showing_code(self):
        self.c.menu.select('wifi_hotspot_rotate')
        with patch('ptt.hotspot_control.is_active', return_value=True), \
             patch('ptt.hotspot_control.control', return_value=True) as ctl, \
             patch('ptt.hotspot_control.read_info', return_value={
                 'ssid': 'Proximus-Setup', 'password': 'new-password123',
                 'url': 'http://192.168.77.1/',
                 'expires_at': time.time() + 900}), \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()):
            self.press(down='E')
        self.assertEqual([x.args[0] for x in ctl.call_args_list], ['stop', 'start'])
        self.assertEqual(self.c.picker['index'], 1)
        self.assertIn('new-password123', self.c.picker['items'][1][1])
        self.assertNotIn('new-password123', self.output.getvalue())

    def test_show_only_rejects_stopped_service_and_does_not_reuse_old_key(self):
        self.c.menu.select('wifi_hotspot_show')
        with patch('ptt.hotspot_control.is_active', return_value=False), \
             patch('ptt.hotspot_control.read_info') as private, \
             patch.object(self.c, '_wifi_job', side_effect=lambda fn: fn()):
            self.press(down='E')
        private.assert_not_called()

    def test_setup_screen_expiration_unlinks_secret_when_ap_times_out(self):
        from dataclasses import replace
        self.c.config = replace(self.c.config, runtime_dir=Path(self.tmp.name))
        self.c.picker = dict(action='wifi_setup', title='HOTSPOT BEREIT',
                             page='wifi_qr', ssid='Proximus-Setup',
                             password='ExpiredCode1234', expires_at=time.time() + 10,
                             index=0, items=[('pw', 'Code: ExpiredCode1234')])
        self.c._publish_picker()
        private = self.c.config.runtime_dir / 'display-people.json'
        self.assertTrue(private.exists())
        with patch('ptt.hotspot_control.read_info', return_value=None):
            self.c._check_hotspot_picker(10.0)
        self.assertIsNone(self.c.picker)
        self.assertFalse(private.exists())
        self.assertNotIn('ExpiredCode1234', self.status_path.read_text())
        self.assertNotIn('ExpiredCode1234', self.output.getvalue())

    def test_setup_screen_checks_live_password_only_once_per_second(self):
        self.c.picker = dict(action='wifi_setup', title='HOTSPOT BEREIT',
                             page='wifi_qr', ssid='Proximus-Setup',
                             password='LiveCode1234567', expires_at=1234,
                             index=0, items=[])
        with patch('ptt.hotspot_control.read_info', return_value={
                'password': 'LiveCode1234567', 'expires_at': 1234}) as read:
            self.c._check_hotspot_picker(2.0)
            self.c._check_hotspot_picker(2.2)
            self.c._check_hotspot_picker(2.99)
            read.assert_called_once()
            self.c._check_hotspot_picker(3.0)
            self.assertEqual(read.call_count, 2)
        self.assertIsNotNone(self.c.picker)

    def test_hotspot_menu_refuses_start_if_wlan_disabled(self):
        self.c.wlan_on = False
        self.c.menu.select('wifi_hotspot_start')
        with patch.object(self.c, '_wifi_job') as work:
            self.press(down='E')
        work.assert_not_called()
        self.assertIn('WLAN', self.c.wifi_notes[-1])

    def test_hotspot_display_can_be_closed_without_stopping_service(self):
        self.c.picker = dict(title='HOTSPOT BEREIT', action='wifi_setup',
                             items=[('pw', 'Code: fake-only-test')], index=0)
        self.c._publish_picker()
        with patch.object(self.c, '_wifi_hotspot_item') as work:
            self.press(down='B')
        work.assert_not_called()
        self.assertIsNone(self.c.picker)

    def test_b_closes_menu_without_cancelling(self):
        self.press(pitft='U')
        self.press(down='B')
        self.assertNotIn('menu_index', self.status())
        self.recorder.finish.assert_not_called()
        self.speech.stop.assert_not_called()

    def test_screen_off_and_wake(self):
        self.press(pitft='D')
        self.c.menu.select('screen')
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
        self.c.menu.select('lore')
        self.assertEqual(self.c.lore, 'light')
        self.press(down='E')
        self.assertEqual((self.c.lore, self.status()['opt_lore']), ('full', 'full'))
        self.assertEqual(self.c.status_snapshot()['lore'], 'full')
        self.press(down='E')
        self.press(down='E')
        self.assertEqual(self.c.lore, 'light')

    def test_persona_and_voice_items_toggle_and_reach_snapshot(self):
        self.speech.effect = 'servitor'
        self.press(pitft='D')
        self.c.menu.select('persona')
        self.assertEqual(self.c.menu.group, 'persona')
        self.press(down='E')
        self.assertEqual((self.c.persona, self.status()['opt_persona']), ('mensch', 'mensch'))
        self.c.menu.select('voice_fx')
        self.press(down='E')
        self.assertEqual((self.c.voice, self.status()['opt_voice']), ('natural', 'natural'))
        self.assertEqual(self.speech.effect, 'natural')
        snapshot = self.c.status_snapshot()
        self.assertEqual((snapshot['persona'], snapshot['voice']), ('mensch', 'natural'))
        with patch('ptt.TranscriptionJob') as job:
            self.c._start_llm('wie hoch ist der eiffelturm')
        self.assertEqual(job.call_args.args[0].keywords['persona'], 'mensch')

    def test_human_shortcut_switches_both_and_keeps_lore(self):
        self.press(pitft='D')
        self.c.menu.select('human')
        self.press(down='E')
        self.assertEqual((self.c.persona, self.c.voice, self.c.lore),
                         ('mensch', 'natural', 'light'))
        self.press(down='E')
        self.assertEqual((self.c.persona, self.c.voice, self.c.lore),
                         ('servitor', 'servitor', 'light'))
        self.c.persona = 'mensch'                       # only half human: switch both on
        self.press(down='E')
        self.assertEqual((self.c.persona, self.c.voice), ('mensch', 'natural'))

    def test_personality_survives_a_restart(self):
        self.press(pitft='D')
        self.c.menu.select('persona')
        self.press(down='E')
        self.c.menu.select('lore')
        self.press(down='E')
        self.assertEqual(json.loads(self.settings_path.read_text()),
                         {'emotions': 'on', 'lore': 'full', 'persona': 'mensch',
                          'voice': 'servitor'})
        with patch.dict(os.environ, {'PTT_LORE_LEVEL': 'off', 'PTT_PERSONA': 'servitor'}):
            restarted = VoiceController(Mock(), Mock(), .04, 30)
        self.assertEqual((restarted.persona, restarted.lore, restarted.voice),
                         ('mensch', 'full', 'servitor'))

    def test_broken_settings_fall_back_to_environment(self):
        self.settings_path.write_text('{"persona": 7, "lore": "laut", "voice": "natural"')
        with patch.dict(os.environ, {'PTT_PERSONA': 'mensch', 'PTT_LORE_LEVEL': 'off'}):
            c = VoiceController(Mock(), Mock(), .04, 30)
        self.assertEqual((c.persona, c.lore, c.voice), ('mensch', 'off', 'servitor'))
        self.settings_path.write_text('{"persona": 7, "lore": "laut", "voice": "natural"}')
        with patch.dict(os.environ, {'PTT_PERSONA': 'mensch'}):
            c = VoiceController(Mock(), Mock(), .04, 30)
        self.assertEqual((c.persona, c.lore, c.voice), ('mensch', 'light', 'natural'))

    def test_local_llm_gets_lore_level(self):
        with patch('ptt.TranscriptionJob') as job:
            self.c._start_llm('wie hoch ist der eiffelturm')
        function = job.call_args.args[0]
        self.assertEqual(function.keywords, {
            'lore': 'light', 'memory': None, 'model': None, 'persona': 'servitor',
            'mood': {'emotion': 'neutral', 'level': 0, 'refuse': False}})

    def test_local_fallback_uses_the_pis_own_calendar(self):
        import datetime
        now = datetime.datetime.now().astimezone()
        later = now + datetime.timedelta(minutes=1)
        self.c.agenda = Mock()
        self.c.agenda.today.return_value = (
            [dict(summary='Zahnarzt', start=later, end=None, all_day=False)]
            if later.date() == now.date() else [])
        with patch.object(self.c, '_start_speech') as speech:
            self.c._start_llm('was steht heute an')
        text = speech.call_args.args[0]
        self.assertTrue(text.startswith('Termine heute.') or text.startswith('Keine weiteren'))

    def test_cue_item_toggles_the_acknowledgement_sound(self):
        self.press(pitft='D')
        self.assertEqual(self.status()['opt_cue'], 'none')   # no sound configured
        self.c.cue = Mock(enabled=True)
        self.c.menu.select('cue')
        self.press(down='E')
        self.assertFalse(self.c.cue.enabled)
        self.assertEqual(self.status()['opt_cue'], 'off')
        self.press(down='E')
        self.assertEqual(self.status()['opt_cue'], 'on')

    def test_submit_plays_cue_and_speech_waits_for_it(self):
        order = Mock()
        self.c.cue = order.cue
        self.c.speech = order.speech
        self.recorder.finish.return_value = Path(self.tmp.name) / 'turn.wav'
        self.recorder.take_uplink.return_value = None
        with patch.object(self.c, '_start_local_stt') as stt:
            self.c.submit('release')
        stt.assert_called_once()
        self.c.cue.play.assert_called_once()
        self.c._start_speech('Antwort.', source='assistant')
        names = [name for name, *_ in order.mock_calls if name in ('cue.settle', 'speech.start')]
        self.assertEqual(names, ['cue.settle', 'speech.start'])

    def test_menu_status_item_speaks(self):
        self.press(pitft='D')
        self.c.menu.select('status')
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
