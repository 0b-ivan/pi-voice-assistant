import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import cue  # noqa: E402


class SoundTests(unittest.TestCase):
    def test_short_quiet_and_clipped(self):
        samples = cue.samples()
        self.assertLess(len(samples) / cue.RATE, 0.5)   # never delays the answer much
        self.assertLessEqual(max(abs(s) for s in samples), cue.PEAK)
        self.assertEqual(samples[0], 0)                  # fades in, no click

    def test_wav_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cue.wav'
            cue.write_wav(path)
            with wave.open(str(path), 'rb') as clip:
                self.assertEqual((clip.getnchannels(), clip.getframerate()), (1, cue.RATE))
                self.assertGreater(clip.getnframes(), 0)


class CueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.process = Mock()
        self.process.poll.return_value = None
        self.popen = Mock(return_value=self.process)
        self.cue = cue.Cue(Path(self.tmp.name) / 'cue.wav', 'plughw:test', popen=self.popen)

    def test_play_starts_aplay_in_background(self):
        self.assertTrue(self.cue.play())
        args = self.popen.call_args.args[0]
        self.assertEqual(args[:4], ['/usr/bin/aplay', '-q', '-D', 'plughw:test'])
        self.assertTrue(Path(args[4]).exists())
        self.process.wait.assert_not_called()

    def test_switched_off_plays_nothing(self):
        self.cue.enabled = False
        self.assertFalse(self.cue.play())
        self.popen.assert_not_called()

    def test_missing_player_is_not_fatal(self):
        self.popen.side_effect = OSError('no aplay')
        self.assertFalse(self.cue.play())
        self.cue.settle()

    def test_settle_waits_and_kills_a_hung_player(self):
        self.cue.play()
        self.cue.settle()
        self.process.wait.assert_called_once()
        self.process.kill.assert_not_called()
        self.process.wait.side_effect = subprocess.TimeoutExpired('aplay', 0.6)
        self.cue.play()
        self.cue.settle()
        self.process.kill.assert_called_once()
        self.cue.settle()   # nothing left to wait for

    def test_play_again_stops_the_previous_cue(self):
        self.cue.play()
        self.cue.play()
        self.process.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
