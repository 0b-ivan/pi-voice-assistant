import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/profile-piper.py'
spec = importlib.util.spec_from_file_location('profile_piper', SCRIPT)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ResourceTests(unittest.TestCase):
    def test_memory_parser_and_missing_proc(self):
        with patch.object(Path, 'read_text', return_value='VmRSS:\t2048 kB\nVmHWM:\t4096 kB\nVmSwap:\t1024 kB\n'):
            self.assertEqual(profile.proc_memory(), dict(rss_mib=2, peak_rss_mib=4, swap_mib=1))
        with patch.object(Path, 'read_text', side_effect=PermissionError()):
            self.assertIsNone(profile.proc_memory()['rss_mib'])

    def test_summary_uses_min_available_and_max_swap(self):
        samples = [dict(available_mib=100, swap_used_mib=10),
                   dict(available_mib=40, swap_used_mib=25),
                   dict(available_mib=None, swap_used_mib=None)]
        self.assertEqual(profile.summarize_samples(samples),
                         dict(min_available_mib=40, max_swap_used_mib=25))

    def test_report_replacement_is_complete_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.json'
            output.write_text('old')
            profile.save_report(output, {'text': 'Grüße'})
            self.assertEqual(json.loads(output.read_text()), {'text': 'Grüße'})
            self.assertEqual(list(Path(tmp).iterdir()), [output])


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = self.root / 'test.onnx'
        self.model.write_bytes(b'fixture-model')
        Path(str(self.model) + '.json').write_text('{}')
        package = self.root / 'piper'
        package.mkdir()
        (package / '__init__.py').write_text('''
import os
from pathlib import Path
class PiperVoice:
    @classmethod
    def load(cls, model):
        with Path(model + '.loads').open('a') as log:
            log.write('load\\n')
        return cls()
    def synthesize_wav(self, text, audio):
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\\0\\0' * 16000)
''')
        (package / '__main__.py').write_text('''
import argparse, os, signal, time, wave
from pathlib import Path
from piper import PiperVoice
p = argparse.ArgumentParser()
p.add_argument('-m', required=True)
p.add_argument('-f', required=True)
p.add_argument('text', nargs='*')
args = p.parse_args()
if os.environ.get('TEST_HANG'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        Path(args.m + '.heartbeat').write_text(str(time.monotonic_ns()))
        time.sleep(.01)
voice = PiperVoice.load(args.m)
with wave.open(args.f, 'wb') as audio:
    voice.synthesize_wav(' '.join(args.text), audio)
''')
        self.report = self.root / 'report.json'
        self.env = dict(os.environ, PYTHONPATH=str(self.root))

    def command(self, repeats=2, timeout=20):
        return [sys.executable, str(SCRIPT), '--piper-python', sys.executable,
                '--model', str(self.model), '--output', str(self.report),
                '--repeats', str(repeats), '--idle-seconds', '1',
                '--timeout', str(timeout)]

    def test_comparison_loads_once_in_resident_worker_and_cleans_audio(self):
        result = subprocess.run(self.command(), env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(self.report.read_text())
        self.assertNotIn('error', report)
        records = report['records']
        phases = [r['phase'] for r in records]
        self.assertEqual(phases.count('fresh_process'), 2)
        self.assertEqual(phases.count('load_once'), 1)
        self.assertIn('resident_1', phases)
        self.assertIn('resident_2', phases)
        self.assertIn('resident_idle', phases)
        self.assertEqual(Path(str(self.model) + '.loads').read_text().splitlines(),
                         ['load', 'load', 'load'])
        for record in records:
            if 'audio_seconds' in record:
                self.assertEqual(record['audio_seconds'], 1)
                self.assertGreaterEqual(record['cpu_seconds'], 0)
                self.assertGreater(record['elapsed_seconds'], 0)
                self.assertGreater(record['peak_rss_mib'], 0)
        self.assertFalse(list(self.root.rglob('*.wav')))

    def test_timeout_kills_cli_descendant_and_saves_partial_report(self):
        self.env['TEST_HANG'] = '1'
        result = subprocess.run(self.command(repeats=1, timeout=1), env=self.env,
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(self.report.read_text())
        self.assertIn('exceeded', report['error'])
        heartbeat = Path(str(self.model) + '.heartbeat')
        self.assertTrue(heartbeat.exists())
        time.sleep(.05)
        stamp = heartbeat.stat().st_mtime_ns
        time.sleep(.1)
        self.assertEqual(heartbeat.stat().st_mtime_ns, stamp)

    def test_missing_model_is_rejected_before_workers_start(self):
        self.model.unlink()
        result = subprocess.run(self.command(), env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.report.exists())

    def test_sigterm_cleans_worker_and_saves_report(self):
        self.env['TEST_HANG'] = '1'
        proc = subprocess.Popen(self.command(repeats=1, timeout=20), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            heartbeat = Path(str(self.model) + '.heartbeat')
            deadline = time.monotonic() + 5
            while not heartbeat.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(heartbeat.exists())
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=5)
            self.assertEqual(proc.returncode, 130, stderr + stdout)
            self.assertEqual(json.loads(self.report.read_text())['error'], 'Interrupted')
            time.sleep(.05)
            stamp = heartbeat.stat().st_mtime_ns
            time.sleep(.1)
            self.assertEqual(heartbeat.stat().st_mtime_ns, stamp)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == '__main__':
    unittest.main()
