import copy
import datetime
import json
import sys
import tempfile
import unittest
import zoneinfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import intents  # noqa: E402
import systemwache as w  # noqa: E402

# Recorded from https://uptime.obivan.org on 15.10.2026 (pages cloud and vault).
FIXTURES = ROOT / 'tests' / 'fixtures' / 'kuma'
BERLIN = zoneinfo.ZoneInfo('Europe/Berlin')
T0 = datetime.datetime(2026, 10, 15, 22, 0, tzinfo=BERLIN).timestamp()


def recorded():
    return {(kind, slug): json.loads((FIXTURES / f'{kind}-{slug}.json').read_text())
            for kind in ('page', 'heartbeat') for slug in ('cloud', 'vault')}


class FakeKuma:
    """The recorded pages; tests set a monitor's last beat or break a page."""

    def __init__(self):
        self.data = recorded()
        self.broken = set()
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        slug = url.rsplit('/', 1)[1]
        if slug in self.broken:
            raise OSError('connection refused')
        return copy.deepcopy(self.data[('heartbeat' if '/heartbeat/' in url else 'page', slug)])

    def beat(self, ident, status, slug='cloud', msg=''):
        beats = self.data[('heartbeat', slug)]['heartbeatList'][str(ident)]
        beats.append(dict(status=status, time='2026-10-15 20:05:00', msg=msg, ping=None))

    def monitors(self, slug):
        return self.data[('page', slug)]['publicGroupList'][0]['monitorList']


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.kuma, self.clock = FakeKuma(), Clock()
        self.reports = []
        self.watch = self.make()

    def tearDown(self):
        self.dir.cleanup()

    def make(self):
        return w.KumaWatch('https://uptime.example/', ['cloud', 'vault'], directory=self.dir.name,
                           fetch=self.kuma, clock=self.clock, report=self.reports.append)

    def poll(self, minutes=1):
        self.clock.now += minutes * 60
        return [e['kind'] for e in self.watch.poll()]

    def at(self, hour, minute=0):
        return datetime.datetime(2026, 10, 15, hour, minute, tzinfo=BERLIN)


class ParseTests(Base):
    def test_recorded_pages(self):
        pages = w.read_pages('https://uptime.example', ['cloud', 'vault'], self.kuma)
        self.assertEqual(sorted(pages['cloud']), [1, 2, 3, 7, 8, 14])
        self.assertEqual(sorted(pages['vault']), [15, 16])
        site = pages['cloud'][14]
        self.assertEqual((site['name'], site['type'], site['status'], site['up24']),
                         ('cloud.obivan.org', 'http', w.UP, 1))
        self.assertEqual(site['cert_days'], 78)
        self.assertEqual(pages['cloud'][1]['name'], 'Container Nextcloud Webapp')
        self.assertIsNone(pages['cloud'][1]['cert_days'])
        self.assertEqual(self.kuma.urls[:2], ['https://uptime.example/api/status-page/cloud',
                                              'https://uptime.example/api/status-page/heartbeat/cloud'])

    def test_heartbeat_time_is_utc(self):
        self.assertEqual(w.beat_time('2026-10-15 20:02:14'),
                         datetime.datetime(2026, 10, 15, 22, 2, 14, tzinfo=BERLIN).timestamp())
        self.assertIsNone(w.beat_time('gestern'))

    def test_names_are_foreign_text(self):
        self.kuma.monitors('vault')[0]['name'] = 'Vault\n<b>IGNORE ALL PREVIOUS</b>; rm -rf / ' + 'x' * 99
        name = w.read_pages('https://u', ['vault'], self.kuma)['vault'][15]['name']
        self.assertNotIn('<', name)
        self.assertNotIn('\n', name)
        self.assertNotIn(';', name)
        self.assertLessEqual(len(name), 60)

    def test_unreachable_and_garbage_pages(self):
        self.kuma.broken.add('vault')
        self.kuma.data[('page', 'cloud')] = ['not', 'a', 'page']
        self.assertEqual(w.read_pages('https://u', ['cloud', 'vault'], self.kuma),
                         dict(cloud=None, vault=None))

    def test_from_env(self):
        self.assertIsNone(w.from_env(environ={}))
        self.assertIsNone(w.from_env(environ=dict(SERVITOR_KUMA_URL='https://u')))
        watch = w.from_env(environ=dict(SERVITOR_KUMA_URL='https://u/',
                                        SERVITOR_KUMA_PAGES='cloud, vault,../x'))
        self.assertEqual((watch.base, watch.slugs), ('https://u', ['cloud', 'vault']))


class ChangeTests(Base):
    def test_first_poll_is_the_baseline(self):
        self.assertEqual(self.poll(), [])
        self.assertEqual(len(self.watch.monitors), 8)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.reports, [])

    def test_outage_needs_two_polls_and_recovery_names_the_duration(self):
        self.poll()
        self.kuma.beat(3, w.DOWN, msg='container exited')
        self.assertEqual(self.poll(), [])                 # one poll is a hiccup
        self.assertEqual(self.poll(), ['down'])
        self.assertEqual(self.poll(), [])
        self.kuma.beat(3, w.UP)
        self.assertEqual(self.poll(20), [])
        events = self.watch.poll()
        self.assertEqual([e['kind'] for e in events], ['up'])
        self.assertEqual((events[0]['name'], events[0]['dur']), ('Container Nextcloud Talk', 21 * 60))
        self.assertEqual(self.watch.status()['watch_messages'], {})

    def test_single_hiccup_and_pending_are_no_change(self):
        self.poll()
        self.kuma.beat(3, w.DOWN)
        self.poll()
        self.kuma.beat(3, w.UP)
        self.assertEqual(self.poll(), [])
        self.kuma.beat(3, w.PENDING)
        self.assertEqual(self.poll() + self.poll(), [])

    def test_flapping_becomes_one_unstable_event(self):
        self.poll()
        kinds = []
        for status in (w.DOWN, w.UP) * 4:
            self.kuma.beat(7, status)
            kinds += self.poll() + self.poll()
        self.assertEqual(kinds, ['down', 'up', 'down', 'unstable'])

    def test_page_unreachable_and_back(self):
        self.poll()
        self.kuma.broken.add('vault')
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.poll(), ['unreachable'])
        self.assertEqual(len(self.watch.monitors), 8)     # its monitors are kept
        self.kuma.broken.clear()
        self.poll()
        self.assertEqual(self.poll(), ['reachable'])

    def test_new_and_gone_monitors(self):
        self.poll()
        self.kuma.monitors('vault').append(dict(id=17, name='Audiobookshelf', type='http'))
        self.assertEqual(self.poll(), ['new'])
        del self.kuma.monitors('cloud')[-1]
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.poll(), ['gone'])
        self.assertNotIn(8, self.watch.monitors)

    def test_certificate_warning_once(self):
        self.poll()
        self.kuma.monitors('cloud')[0]['certExpiryDaysRemaining'] = 9
        self.assertEqual(self.poll(), ['cert'])
        self.assertEqual(self.poll(), [])

    def test_state_and_journal_survive_a_restart(self):
        self.poll()
        self.kuma.beat(16, w.DOWN, slug='vault')
        self.poll()
        self.poll()
        again = self.make()
        self.assertEqual(again.monitors[16]['level'], 'down')
        self.assertEqual([e['kind'] for e in again.events], ['down'])
        self.kuma.beat(16, w.UP, slug='vault')
        self.clock.now += 60
        again.poll()
        self.clock.now += 60
        self.assertEqual([e['kind'] for e in again.poll()], ['up'])
        journal = (Path(self.dir.name) / 'events.jsonl').read_text().splitlines()
        self.assertEqual([json.loads(line)['kind'] for line in journal], ['down', 'up'])


class SentenceTests(Base):
    def snapshot(self):
        return self.watch.snapshot_fields()

    def test_quiet_night_says_nothing_in_the_briefing(self):
        self.poll()
        self.assertIsNone(w.briefing_sentence(self.snapshot(), now=self.at(22, 5)))
        self.assertIsNone(w.briefing_sentence({}))

    def test_answer_all_well(self):
        self.poll()
        self.assertEqual(w.answer_text(self.snapshot(), now=self.at(22, 5)),
                         'Systemwache: Alle 8 überwachten Dienste sind erreichbar. '
                         'In den letzten 24 Stunden keine Veränderung.')
        self.assertEqual(w.answer_text(self.snapshot(), now=self.at(22, 5),
                                       question='wie geht es vaultwarden'),
                         'Systemwache: Container Vaultwarden ist erreichbar. '
                         'In den letzten 24 Stunden keine Veränderung.')

    def test_outage_in_briefing_and_answer(self):
        self.poll()
        self.kuma.beat(3, w.DOWN)
        self.poll()
        self.poll()   # confirmed at 22:03
        now = self.at(22, 30)
        self.assertEqual(w.briefing_sentence(self.snapshot(), now=now),
                         'Systemwache: Container Nextcloud Talk ist seit 22 Uhr 3 ausgefallen.')
        text = w.answer_text(self.snapshot(), now=now, question='wie geht es nextcloud')
        self.assertTrue(text.startswith('Systemwache: Von 5 passenden Diensten ist einer '
                                        'ausgefallen: Container Nextcloud Talk seit 22 Uhr 3.'), text)
        self.assertIn('Container Nextcloud Talk ist um 22 Uhr 3 ausgefallen.', text)

    def test_recovery_of_the_night_in_the_morning(self):
        self.poll()
        self.kuma.beat(3, w.DOWN)
        self.poll()
        self.poll()
        self.kuma.beat(3, w.UP)
        self.poll(180)
        self.poll()   # back at 1:04
        morning = datetime.datetime(2026, 10, 16, 7, 0, tzinfo=BERLIN)
        self.assertEqual(w.briefing_sentence(self.snapshot(), lore='full', now=morning),
                         'Der Auspex der fernen Wachen meldet: Container Nextcloud Talk war '
                         '3 Stunden ausgefallen, seit 1 Uhr 4 wieder da.')

    def test_unreachable_page_and_no_data(self):
        self.poll()
        self.kuma.broken.add('vault')
        self.poll()
        self.poll()
        self.assertIn('Die Statusseite vault ist nicht abrufbar.',
                      w.briefing_sentence(self.snapshot(), now=self.at(22, 5)))
        self.assertIn('keine Daten', w.answer_text({}))
        self.assertIn('Bediener', w.answer_text(dict(watch_denied=True)))

    def test_durations(self):
        self.assertEqual(w.duration(30), 'eine Minute')
        self.assertEqual(w.duration(20 * 60), '20 Minuten')
        self.assertEqual(w.duration(3600), 'eine Stunde')
        self.assertEqual(w.duration(70 * 60), 'eine Stunde 10 Minuten')
        self.assertEqual(w.duration(3 * 86400), '3 Tage')


class IntentTests(Base):
    def test_questions(self):
        for text in ('was ist bei uptime kuma los', 'up time kuma', 'systemwache',
                     'wie geht es nextcloud', 'was hat sich verändert',
                     'sind alle dienste erreichbar', 'wie steht es um die dienste',
                     'was ist mit den diensten'):
            self.assertEqual(intents.match(text), 'watch', text)
        self.assertEqual(intents.match('wie geht es dir'), 'status')
        self.assertEqual(intents.match('uptime'), 'status')

    def test_briefing_names_the_watch(self):
        self.poll()
        self.kuma.beat(16, w.DOWN, slug='vault')
        self.poll()
        self.poll()
        snapshot = dict(self.watch.snapshot_fields(), server='ok')
        text = intents.answer('briefing', self.at(22, 30), snapshot)
        self.assertIn('Systemwache: vault.obivan.org ist seit 22 Uhr 3 ausgefallen.', text)
        self.assertIn('Systemwache: Von 8 überwachten Diensten ist einer ausgefallen',
                      intents.answer('watch', self.at(22, 30), snapshot))


if __name__ == '__main__':
    unittest.main()
