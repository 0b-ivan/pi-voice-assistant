import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import protocol  # noqa: E402


class EnvelopeTests(unittest.TestCase):
    def test_round_trip(self):
        item = protocol.message('ping', dict(n=1), ttl=60, ack=True, now=1000)
        back = protocol.decode(protocol.encode(item), now=1010)
        self.assertEqual(back, item)
        self.assertEqual((back['v'], back['type'], back['ts'], back['ttl']), (1, 'ping', 1000, 60))
        self.assertEqual(len(back['id']), 32)

    def test_only_listed_types_within_their_size(self):
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.message('memory.everything', {})
        self.assertEqual(caught.exception.code, 'type')
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.message('ping', dict(text='x' * 300))
        self.assertEqual(caught.exception.code, 'size')

    def test_broken_old_and_future_messages_are_refused(self):
        good = protocol.message('ping', ttl=60, now=1000)
        for change, code in ((dict(v=2), 'version'), (dict(id='a-b'), 'format'),
                             (dict(ts='1000'), 'format'), (dict(prio=9), 'format'),
                             (dict(body=[1]), 'format'), (dict(ttl=0), 'format')):
            with self.assertRaises(protocol.ProtocolError) as caught:
                protocol.validate(dict(good, **change), now=1000)
            self.assertEqual(caught.exception.code, code, change)
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.validate(good, now=1061)
        self.assertEqual(caught.exception.code, 'expired')
        with self.assertRaises(protocol.ProtocolError) as caught:
            protocol.validate(good, now=1000 - protocol.CLOCK_SKEW - 1)
        self.assertEqual(caught.exception.code, 'time')
        for raw in (b'not json', b'[]', b'x' * (protocol.MAX_MESSAGE + 1)):
            with self.assertRaises(protocol.ProtocolError):
                protocol.decode(raw)

    def test_resent_message_is_seen_once(self):
        seen = protocol.Seen(size=2)
        self.assertTrue(seen.first('a1b2c3d4'))
        self.assertFalse(seen.first('a1b2c3d4'))
        seen.first('b' * 8)
        seen.first('c' * 8)                       # oldest one falls out
        self.assertTrue(seen.first('a1b2c3d4'))

    def test_digest_ignores_key_order(self):
        self.assertEqual(protocol.digest(dict(a=1, b='ä')), protocol.digest(dict(b='ä', a=1)))
        self.assertNotEqual(protocol.digest(dict(a=1)), protocol.digest(dict(a=2)))


class SessionTests(unittest.TestCase):
    context = dict(facts=['Bediener heißt Ivan'], directives=[], total_facts=1,
                   history=[dict(q='hallo', a='Gruß.')])

    def test_core_is_sent_once_then_named_by_digest(self):
        client, server = protocol.ClientSession(), protocol.Sessions()
        self.assertEqual(client.memory_payload(self.context), self.context)   # no session yet
        client.start(server.open('pi'))
        self.assertEqual(client.memory_payload(self.context), self.context)   # not confirmed
        client.confirmed(server.remember(client.id, self.context))
        newer = dict(self.context, history=[dict(q='und jetzt', a='Bereit.')])
        slim = client.memory_payload(newer)
        self.assertEqual(set(slim), {'core', 'history'})
        self.assertEqual(server.restore(client.id, slim), newer)
        changed = dict(newer, facts=['Bediener mag Kaffee'])
        self.assertEqual(client.memory_payload(changed), changed)            # core changed

    def test_unknown_session_or_core_cannot_be_restored(self):
        server = protocol.Sessions()
        ident = server.open()
        server.remember(ident, self.context)
        core, _ = protocol.split_memory(self.context)
        self.assertIsNone(server.restore('f' * 32, dict(core=protocol.digest(core))))
        self.assertIsNone(server.restore(ident, dict(core='0' * 16)))
        self.assertIsNone(protocol.ClientSession().memory_payload(None))

    def test_sessions_expire_and_are_bounded(self):
        now = [0.0]
        server = protocol.Sessions(limit=2, idle=100, clock=lambda: now[0])
        first = server.open()
        self.assertTrue(server.known(first))
        now[0] = 101
        self.assertFalse(server.known(first))
        a, b, c = server.open(), server.open(), server.open()
        self.assertEqual([server.known(x) for x in (a, b, c)], [False, True, True])


class StatusHeaderTests(unittest.TestCase):
    ROUTING = dict(memory='on', devctl='on', pending='reboot', story='on', trip='ask',
                   trip_id='0123456789abcdef', llm_mode='local', lore='full', persona='servitor',
                   voice='servitor', wlan='on', maintenance='off', cpu_mode='balanced',
                   mood='neutral', mood_level=40, mood_refuse='off', server='ok')

    def full_snapshot(self):
        # Pi with PiSugar and three self-test findings: 1036+ characters as
        # plain JSON, which the server ignored completely (audit 14.10.2026).
        codes = ('server_link', 'openrouter', 'turn')
        return dict(self.ROUTING, temp_c=54, load_pct=37, mem_free_pct=41, disk_free_pct=72,
                    throttled=0, swap_used_pct=12, uptime_s=123456, battery_pct=87,
                    battery_mv=4012, battery_charging=False, battery_plugged=True,
                    output_current_ma=820, pisugar_chip_c=38, pisugar_soft_poweroff='on',
                    pisugar_watchdog='off', pisugar_charge_protection='on',
                    pisugar_auto_power_on='off', pisugar_rtc_alarm='off', wifi_dbm=-61, net_ms=34,
                    lan_ms=4, dns='ok', updates=12, updates_security=3, server_updates=5,
                    server_updates_security=1, apt_age_days=2, cpu='balanced',
                    charge_restart_pct=80, charge_limit_pct=95, safe_shutdown_pct=5,
                    safe_shutdown_delay_s=30, log_findings={c: 2 for c in codes},
                    log_checked_at=1760450000, log_repairs=['display'],
                    log_last={c: 1760446400.123456 for c in codes},
                    log_resolved={c: 1760440000.5 for c in codes})

    def test_small_snapshot_is_sent_unchanged(self):
        self.assertEqual(json.loads(protocol.status_header(dict(battery_pct=83))),
                         dict(battery_pct=83))
        self.assertIsNone(protocol.status_header({}))
        self.assertIsNone(protocol.status_header(None))

    def test_large_snapshot_fits_and_keeps_what_routes_a_turn(self):
        snapshot = self.full_snapshot()
        self.assertGreater(len(json.dumps(snapshot, separators=(',', ':'))),
                           protocol.STATUS_HEADER_MAX)
        header = protocol.status_header(snapshot)
        self.assertLessEqual(len(header), protocol.STATUS_HEADER_MAX)
        sent = json.loads(header)
        for name, value in self.ROUTING.items():
            with self.subTest(field=name):
                self.assertEqual(sent.get(name), value)
        self.assertIn('battery_pct', sent)          # not expendable
        self.assertNotIn('log_resolved', sent)      # dropped first
        self.assertEqual(snapshot['log_resolved']['turn'], 1760440000.5)   # input untouched

    def test_routing_fields_are_never_expendable(self):
        self.assertFalse(set(self.ROUTING) & set(protocol.STATUS_EXPENDABLE))


if __name__ == '__main__':
    unittest.main()
