import sys
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
