"""Manual (PiSugar) standby: background probes pause so WLAN and CPU can doze."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import netprobe  # noqa: E402
import ptt  # noqa: E402
import sysmon  # noqa: E402


class Stop(Exception):
    pass


def run_until(target, sleeps, quiet_pattern):
    """Run ``target._run`` with time.sleep patched; ``quiet_pattern`` gives
    the standby state per loop. Returns the sleep durations."""
    pattern = iter(quiet_pattern)
    state = {'quiet': next(pattern)}
    target.quiet = lambda: state['quiet']
    durations = []

    def sleep(seconds):
        durations.append(seconds)
        if len(durations) >= sleeps:
            raise Stop
        state['quiet'] = next(pattern, state['quiet'])

    with patch('time.sleep', sleep):
        try:
            target._run()
        except Stop:
            pass
    return durations


class ProbeTests(unittest.TestCase):
    def test_server_probe_pauses_in_standby_and_resumes_at_once(self):
        calls = []
        probe = netprobe.ServerProbe(interval=15.0, probe=lambda: calls.append(1) or 'ok')
        durations = run_until(probe, 6, [True, True, True, False, False, False])
        self.assertEqual(durations[:3], [netprobe.QUIET_POLL_SECONDS] * 3)
        self.assertEqual(durations[3:], [15.0] * 3)
        self.assertEqual(len(calls), 3)              # only after waking up
        self.assertEqual(probe.state, 'ok')

    def test_watch_pauses_in_standby(self):
        calls = []
        watch = sysmon.Watch(lambda: calls.append(1) or {'ok': True}, 30.0)
        durations = run_until(watch, 5, [False, False, True, True, False])
        # first_delay, one measurement, two quiet polls, then measuring again
        self.assertEqual(durations, [0.0, 30.0, sysmon.QUIET_POLL_SECONDS,
                                     sysmon.QUIET_POLL_SECONDS, 30.0])
        self.assertEqual(len(calls), 2)

    def test_without_quiet_nothing_changes(self):
        probe = netprobe.ServerProbe(interval=15.0, probe=lambda: 'ok')
        probe.quiet = None
        with patch('time.sleep', side_effect=Stop):
            with self.assertRaises(Stop):
                probe._run()
        self.assertEqual(probe.state, 'ok')

    def test_main_loop_slows_down_only_in_manual_standby(self):
        self.assertGreaterEqual(ptt.STANDBY_LOOP_SECONDS, 0.1)
        self.assertLessEqual(ptt.STANDBY_LOOP_SECONDS, 0.5)  # the PiSugar tap stays responsive


if __name__ == '__main__':
    unittest.main()
