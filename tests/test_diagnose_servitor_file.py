import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave


class FileDiagnosticTests(unittest.TestCase):
    def test_real_ffmpeg_reports_all_variants_without_modifying_input(self):
        ffmpeg = os.environ.get('FFMPEG_TEST_BIN') or shutil.which('ffmpeg')
        if not ffmpeg:
            self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'speech.wav'
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                audio.writeframes(b'\x00\x00' * 16000)
            before = source.read_bytes()
            script = Path(__file__).resolve().parents[1] / 'scripts/diagnose-servitor-file.py'
            result = subprocess.run([sys.executable, str(script), str(source), '--ffmpeg', ffmpeg],
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            records = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(records[0]['audio_duration_ms'], 1000)
            self.assertEqual([r['variant'] for r in records[1:]],
                             ['no_dsp', 'servitor_auto', 'servitor_one_thread'])
            self.assertTrue(all(r['benchmark'] for r in records[1:]))
            self.assertEqual(records[2]['pcm_sha256'], records[3]['pcm_sha256'])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(Path(directory).iterdir()), [source])
