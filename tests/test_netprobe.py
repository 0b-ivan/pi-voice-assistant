import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import netprobe  # noqa: E402


class ServerProbeTests(unittest.TestCase):
    def probe(self, answers):
        answers = iter(answers)
        return netprobe.ServerProbe(interval=15.0, probe=lambda: next(answers))

    def test_one_late_answer_is_no_outage(self):
        probe = self.probe(['ok', 'down', 'ok'])
        self.assertEqual(probe.step(), 15.0)
        self.assertEqual(probe.step(), netprobe.SERVER_PROBE_RETRY_SECONDS)  # retried soon
        self.assertEqual(probe.state, 'ok')
        self.assertEqual(probe.step(), 15.0)
        self.assertEqual(probe.state, 'ok')

    def test_two_misses_in_a_row_are_down_and_recovery_is_at_once(self):
        probe = self.probe(['ok', 'down', 'down', 'down', 'ok'])
        probe.step()
        probe.step()
        self.assertEqual(probe.step(), 15.0)
        self.assertEqual(probe.state, 'down')
        probe.step()                       # already down: stays down, no extra retry
        self.assertEqual(probe.state, 'down')
        probe.step()
        self.assertEqual(probe.state, 'ok')

    def test_unreachable_at_start_is_down_at_once(self):
        probe = self.probe(['down'])
        probe.step()
        self.assertEqual(probe.state, 'down')



class EndpointHealthTests(unittest.TestCase):
    URLS = 'http://172.22.9.107:8765, https://proximus.obivan.org'
    ENV = {'ASSISTANT_BASE_URL': URLS}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size):
            return self.payload

    def test_no_endpoints_is_off(self):
        with patch('urllib.request.urlopen') as request:
            self.assertEqual(netprobe.server_state({'ASSISTANT_BASE_URL': ' , '}), 'off')
            request.assert_not_called()

    def test_healthy_lan_does_not_probe_cloudflare(self):
        with patch('urllib.request.urlopen',
                   return_value=self.Response(b'{"ready": true}')) as opener:
            self.assertEqual(netprobe.server_state(self.ENV), 'ok')
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(opener.call_args.args[0].full_url,
                             'http://172.22.9.107:8765/health')

    def test_lan_timeout_cloudflare_healthy(self):
        requests = []

        def respond(request, timeout):
            requests.append((request, timeout))
            if request.full_url.startswith('http://'):
                raise TimeoutError('LAN unavailable')
            return self.Response(b'{"ok": true, "ready": true}')

        with patch('urllib.request.urlopen', side_effect=respond):
            self.assertEqual(netprobe.server_state(self.ENV), 'ok')
        self.assertEqual([r.full_url for r, _ in requests], [
            'http://172.22.9.107:8765/health',
            'https://proximus.obivan.org/health'])
        self.assertTrue(all(timeout == netprobe.SERVER_PROBE_TIMEOUT_SECONDS
                            for _, timeout in requests))
        for request, _ in requests:
            self.assertEqual(request.get_header('User-agent'), netprobe.HEALTH_USER_AGENT)
            self.assertIsNone(request.get_header('Authorization'))

    def test_lan_unready_cloudflare_healthy(self):
        with patch('urllib.request.urlopen', side_effect=[
                self.Response(b'{"ready": false}'),
                self.Response(b'{"ready": true}')]) as opener:
            self.assertEqual(netprobe.server_state(self.ENV), 'ok')
            self.assertEqual(opener.call_count, 2)

    def test_invalid_lan_response_cloudflare_healthy(self):
        with patch('urllib.request.urlopen', side_effect=[
                self.Response(b'bad JSON'), self.Response(b'{"ready": true}')]):
            self.assertEqual(netprobe.server_state(self.ENV), 'ok')

    def test_all_endpoints_down(self):
        with patch('urllib.request.urlopen', side_effect=OSError('unreachable')) as opener:
            self.assertEqual(netprobe.server_state(self.ENV), 'down')
            self.assertEqual(opener.call_count, 2)

    def test_all_endpoints_unready(self):
        with patch('urllib.request.urlopen',
                   return_value=self.Response(b'{"ready": false}')) as opener:
            self.assertEqual(netprobe.server_state(self.ENV), 'down')
            self.assertEqual(opener.call_count, 2)


if __name__ == '__main__':
    unittest.main()
