"""Optional real DSP tests; FFMPEG_TEST_BIN can select an installed FFmpeg."""
import math
import os
from pathlib import Path
import selectors
import shutil
import struct
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voice_effects import build_stream_playback_command

FFMPEG = os.environ.get('FFMPEG_TEST_BIN') or shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'FFmpeg is not installed')
class FFmpegStreamTests(unittest.TestCase):
    def test_dsp_outputs_audio_before_input_eof_and_preserves_pcm(self):
        pcm = b''.join(struct.pack('<h', int(4000 * math.sin(2 * math.pi * 440 * i / 16000)))
                       for i in range(16000))
        command = build_stream_playback_command(16000, 1, 'pipe:1', ffmpeg_bin=FFMPEG)
        # Exercise the production input and graph, replacing only ALSA sink.
        command[-2] = 's16le'
        proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        selector = selectors.DefaultSelector()
        try:
            proc.stdin.write(pcm)
            proc.stdin.flush()
            selector.register(proc.stdout, selectors.EVENT_READ)
            ready = selector.select(2)
            first = os.read(proc.stdout.fileno(), 4096) if ready else b''
            proc.stdin.close()
            proc.stdin = None
            rest, errors = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, errors.decode(errors='replace'))
            self.assertTrue(first, 'DSP waited for EOF instead of streaming')
            output = first + rest
            self.assertEqual(len(output) % 2, 0)
            self.assertGreater(len(output), 48000 * 2)
            # Probe reduction must not discard the first packet or change sound.
            baseline = command.copy()
            index = baseline.index('-probesize')
            del baseline[index:index + 4]
            reference = subprocess.run(baseline, input=pcm, capture_output=True,
                                       check=True, timeout=10)
            self.assertEqual(output, reference.stdout)
        finally:
            selector.close()
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
