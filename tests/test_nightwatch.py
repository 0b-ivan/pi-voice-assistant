import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import intents
import memory
import nightwatch as nw
import nightwatch_client as nc
from remote_turn import RemoteConfig


def report(findings=(), checked=(), state='done', ident='a' * 32, night='2026-10-11'):
    return dict(id=ident, at=1000, night=night, state=state,
                findings=list(findings), checked=list(checked), limits=[])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.device = root / 'device'
        self.device.touch()
        self.core = memory.MemoryCore(root, self.device)
        self.store = nw.Store(self.core)
        self.issue = nw.finding('security:pi:debian', 'lib:CVE-TEST', 'Eine Lücke',
                                'Aktualisierung prüfen.', 'security', 0)

    def test_persistent_dedup_and_resolution_only_for_checked_scopes(self):
        self.assertTrue(self.store.save(report([self.issue])))
        self.assertTrue(self.store.save(report([self.issue], ident='b' * 32)))
        self.assertEqual(nw.Store(self.core).view()['open'], 1)
        self.assertTrue(self.store.save(report(state='partial', ident='c' * 32)))
        self.assertEqual(self.store.view()['open'], 1)
        self.assertTrue(self.store.save(report(checked=['security:pi:debian'], ident='d' * 32)))
        self.assertEqual(self.store.view()['open'], 0)
        self.assertTrue(self.store.save(report([self.issue], ident='e' * 32)))
        self.assertEqual(self.store.view()['open'], 1)
        self.assertEqual(self.core.context()['facts'], [])
        self.assertEqual(self.core.context()['history'], [])

    def test_missing_core_and_corruption_are_not_clean_reports(self):
        self.device.unlink()
        self.assertFalse(self.store.save(report([self.issue])))
        self.assertIn('Speicherkern', self.store.view()['error'])
        self.device.touch()
        self.assertTrue(self.store.save(report([self.issue])))
        path = self.store.root / 'findings.json'
        path.write_text('{broken')
        self.assertFalse(self.store.save(report()))
        self.assertEqual(path.read_text(), '{broken')
        self.assertIn('nicht lesbar', self.store.view()['error'])

    def test_invalid_ids_never_become_paths_and_maximal_view_fits_header(self):
        with self.assertRaises(ValueError):
            self.store.save(report(ident='../bad'))
        items = [nw.finding('security:pi:debian', str(i), 'x' * 220, 'a' * 500,
                            'security', i % 4, ['https://example.com/' + 'b' * 450] * 5,
                            note='z' * 600) for i in range(8)]
        self.assertTrue(self.store.save(report(items)))
        header = nw.encode_view(self.store.view())
        self.assertTrue(header)
        self.assertLessEqual(len(header), nw.HEADER_LIMIT)
        decoded = nw.decode_view(header)
        self.assertEqual(decoded['open'], 8)
        self.assertEqual(len(decoded['findings']), 6)
        self.assertIsNone(nw.decode_view('bad'))

    def test_voice_and_briefing_are_fixed_and_denied_to_guests(self):
        self.assertTrue(self.store.save(report([self.issue])))
        self.assertEqual(intents.match('was ist offen'), 'night_open')
        self.assertEqual(intents.match('welche sicherheitslücken sind offen'), 'night_security')
        self.assertEqual(intents.match('was hast du heute nacht geprüft'), 'night_report')
        self.assertIsNone(intents.match('was ist offen in der mathematik und warum'))
        now = datetime.datetime(2026, 10, 11, 7, 0)
        text = intents.answer('night_security', now, dict(nightwatch=self.store.view()))
        self.assertIn('Eine Lücke', text)
        self.assertIn('Aktualisierung prüfen', text)
        brief = intents.answer('briefing', now, dict(nightwatch=self.store.view()))
        self.assertIn('Eine Lücke', brief)
        denied = intents.answer('night_open', now, dict(nightwatch=dict(denied=True)))
        self.assertIn('nicht als Bediener', denied)
        brief = intents.answer('briefing', now, dict(nightwatch=dict(denied=True)))
        self.assertNotIn('Eine Lücke', brief)

    def test_night_reports_never_enter_personal_history(self):
        from controller_fixture import make_controller
        controller = make_controller(self)
        controller.memory = Mock()
        controller._remember_turn('was ist offen', 'privater Sicherheitsbefund')
        controller._remember_turn('guten morgen', 'privater Nachtbericht')
        controller.memory.remember_turn.assert_not_called()
        controller._remember_turn('wie hoch ist der eiffelturm', '330 Meter')
        controller.memory.remember_turn.assert_called_once()


class ScheduleTests(unittest.TestCase):
    def test_crossing_midnight_and_default_window(self):
        date = datetime.datetime(2026, 10, 11, 3)
        self.assertEqual(nw.night_key(date), '2026-10-11')
        self.assertIsNone(nw.night_key(date.replace(hour=7)))
        self.assertEqual(nw.night_key(date, 22, 5), '2026-10-10')
        self.assertEqual(nw.night_key(date.replace(hour=23), 22, 5), '2026-10-11')

    def test_only_idle_powered_lane_and_one_run_per_night(self):
        core = Mock()
        core.root = Path('/unused')
        core.present.return_value = True
        client = nc.Client(core, RemoteConfig(('http://172.22.2.107:8765',), 'x' * 40))
        client.store.load = Mock(return_value={'last': None})
        now = datetime.datetime(2026, 10, 11, 3)
        with patch.object(nc.threading, 'Thread') as thread:
            client.tick(1500, False, {}, now)
            self.assertTrue(client.abort.is_set())
            thread.assert_not_called()
            client.tick(100, True, {}, now)
            thread.assert_not_called()
            client.last_check = 0
            client.tick(1500, True, {}, now)
            thread.assert_called_once()
            client.thread = None
            client.last_check = 0
            client.tick(1500, True, {}, now)
            thread.assert_called_once()
        public = nc.Client(core, RemoteConfig(('https://example.com',), 'x' * 40))
        self.assertIsNone(public.base)

    def test_storage_failure_never_acknowledges_server_spool(self):
        core = Mock(root=Path('/unused'))
        client = nc.Client(core, RemoteConfig(('http://127.0.0.1',), 'x' * 40))
        client.store.save = Mock(return_value=False)
        client._rpc = Mock()
        self.assertFalse(client._commit(report()))
        client._rpc.assert_not_called()
        client.store.save.return_value = True
        self.assertTrue(client._commit(report()))
        client._rpc.assert_called_once_with(dict(action='ack', id='a' * 32))


if __name__ == '__main__':
    unittest.main()
