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
import wave
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/profile-piper.py'
spec = importlib.util.spec_from_file_location('profile_piper', SCRIPT)
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class ResourceTests(unittest.TestCase):
    def test_zram_reads_physical_bytes_and_fixed_4k_backing_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / 'zram0'
            device.mkdir()
            (device / 'mm_stat').write_text('174915584 53557438 62042112 0 132694016 35 4845 4488 32454')
            (device / 'bd_stat').write_text('256 512 768')
            (device / 'backing_dev').write_text('/dev/loop0\n')
            snapshot = profile.zram_snapshot(tmp)
            entry = snapshot['devices']['zram0']
            self.assertEqual(snapshot['physical_mib'], 62042112 / 1024**2)
            self.assertEqual(entry['compressed_mib'], 53557438 / 1024**2)
            self.assertEqual(entry['lifetime_peak_physical_mib'], 132694016 / 1024**2)
            self.assertEqual(entry['backing_read_mib'], 2)
            self.assertEqual(entry['backing_written_mib'], 3)
            self.assertEqual(entry['backing_device'], '/dev/loop0')
            (device / 'mm_stat').unlink()
            self.assertIsNone(profile.zram_snapshot(tmp)['physical_mib'])
            self.assertIsNone(profile.zram_snapshot(Path(tmp) / 'missing')['physical_mib'])

    def test_summary_reports_sampled_physical_peak_separately(self):
        samples = [dict(available_mib=40, swap_used_mib=200,
                        zram=dict(physical_mib=59)),
                   dict(available_mib=80, swap_used_mib=150,
                        zram=dict(physical_mib=50))]
        self.assertEqual(profile.summarize_samples(samples)['max_zram_physical_mib'], 59)

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
import json, os
from pathlib import Path
class PiperVoice:
    @classmethod
    def load(cls, model):
        if os.environ.get('TEST_ORDER'):
            with Path(os.environ['TEST_ORDER']).open('a') as log:
                log.write('piper_load\\n')
        with Path(model + '.loads').open('a') as log:
            log.write('load\\n')
        voice = cls()
        voice.model = model
        return voice
    def synthesize_wav(self, text, audio):
        if os.environ.get('TEST_ORDER'):
            with Path(os.environ['TEST_ORDER']).open('a') as log:
                log.write('speak\\n')
        with Path(self.model + '.texts').open('a') as log:
            log.write(json.dumps(text, ensure_ascii=False) + '\\n')
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\\0\\0' * 16000)
''')
        (package / '__main__.py').write_text('''
import argparse, os, signal, sys, time, wave
from pathlib import Path
from piper import PiperVoice
p = argparse.ArgumentParser()
p.add_argument('-m', required=True)
p.add_argument('-f', required=True)
args, unknown = p.parse_known_args()
if os.environ.get('TEST_HANG'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        Path(args.m + '.heartbeat').write_text(str(time.monotonic_ns()))
        time.sleep(.01)
voice = PiperVoice.load(args.m)
with wave.open(args.f, 'wb') as audio:
    # Match Piper 1.8.0's argument/stdin selection and line stripping.
    texts = [' '.join(unknown)] if unknown else sys.stdin
    for text in texts:
        text = text.strip()
        if text:
            voice.synthesize_wav(text, audio)
''')
        self.report = self.root / 'report.json'
        self.env = dict(os.environ, PYTHONPATH=str(self.root))

    def command(self, repeats=2, timeout=20):
        return [sys.executable, str(SCRIPT), '--piper-python', sys.executable,
                '--model', str(self.model), '--output', str(self.report),
                '--repeats', str(repeats), '--idle-seconds', '1',
                '--timeout', str(timeout)]

    def alternating_command(self, timeout=20):
        self.vosk_model = self.root / 'vosk-model'
        self.vosk_model.mkdir(exist_ok=True)
        self.input = self.root / 'capture.wav'
        with wave.open(str(self.input), 'wb') as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b'\x10\x00\x10\x00' * 48000)
        self.env['VOSK_PYTHON_PATH'] = str(self.root)
        self.env['TEST_ORDER'] = str(self.root / 'order')
        self.env['STT_PROVIDER'] = 'openrouter'  # benchmark must stay offline
        (self.root / 'vosk.py').write_text('''
import json, os, signal, time
from pathlib import Path
def log(text):
    with Path(os.environ['TEST_ORDER']).open('a') as output:
        output.write(text + '\\n')
def SetLogLevel(level):
    pass
class Model:
    def __init__(self, path):
        log('vosk_load')
        if os.environ.get('TEST_VOSK_HANG'):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            while True:
                Path(path + '/heartbeat').write_text(str(time.monotonic_ns()))
                time.sleep(.01)
class KaldiRecognizer:
    def __init__(self, model, rate):
        assert rate == 16000
        self.size = 0
    def AcceptWaveform(self, pcm):
        self.size += len(pcm)
        return False
    def FinalResult(self):
        log('recognize:' + str(self.size))
        return json.dumps({'text': 'hallo ivan'})
''')
        binary = self.root / 'bin'
        binary.mkdir(exist_ok=True)
        systemctl = binary / 'systemctl'
        systemctl.write_text('#!' + sys.executable + '\nimport os, sys\n'
                            'print("0" if "--property=MainPID" in sys.argv else os.environ.get("TEST_SERVICE", "inactive"))\n')
        systemctl.chmod(0o755)
        self.env['PATH'] = str(binary) + os.pathsep + os.environ.get('PATH', '')
        return self.command(timeout=timeout) + ['--vosk-audio', str(self.input),
                                               '--vosk-model', str(self.vosk_model)]

    def test_alternation_keeps_models_and_reuses_the_same_pcm_input(self):
        command = self.alternating_command()
        original = self.input.read_bytes()
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(self.report.read_text())
        self.assertEqual(report['mode'], 'alternating')
        self.assertNotIn('error', report)
        self.assertEqual((self.root / 'order').read_text().splitlines(),
                         ['vosk_load', 'recognize:32000', 'recognize:32000', 'piper_load',
                          'recognize:32000', 'speak', 'recognize:32000', 'speak'])
        self.assertEqual(Path(str(self.model) + '.loads').read_text().splitlines(), ['load'])
        records = [r for r in report['records'] if r['phase'] != 'environment']
        self.assertEqual([r['phase'] for r in records],
                         ['vosk_load_once', 'vosk_baseline_1', 'vosk_baseline_2', 'load_once',
                          'vosk_alternating_1', 'resident_1', 'vosk_alternating_2', 'resident_2', 'resident_idle'])
        for record in records:
            self.assertIn('zram', record['system_before'])
            self.assertIn('zram', record['system_after'])
        self.assertEqual([r['transcript'] for r in records if 'transcript' in r], ['hallo ivan'] * 4)
        self.assertEqual(self.input.read_bytes(), original)
        self.assertEqual(list(self.root.rglob('*.wav')), [self.input])

    def test_active_or_unknown_service_prevents_loading_models(self):
        command = self.alternating_command()
        for state in ('active', 'activating', 'unknown'):
            self.env['TEST_SERVICE'] = state
            result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
            self.assertIn('PTT must be inactive', json.loads(self.report.read_text())['error'])
            self.assertFalse((self.root / 'order').exists())

    def test_input_cannot_be_overwritten_by_report(self):
        command = self.alternating_command() + ['--output', str(self.input)]
        original = self.input.read_bytes()
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.input.read_bytes(), original)

    def test_invalid_or_truncated_wav_rejected_before_model_load(self):
        command = self.alternating_command()
        for audio in (b'not wav', self.input.read_bytes()[:-100]):
            self.input.write_bytes(audio)
            result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
            self.assertIn('error', json.loads(self.report.read_text()))
            self.assertFalse((self.root / 'order').exists())

    def test_alternating_timeout_kills_native_worker_and_preserves_report(self):
        command = self.alternating_command(timeout=2)
        self.env['TEST_VOSK_HANG'] = '1'
        result = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIn('exceeded', json.loads(self.report.read_text())['error'])
        heartbeat = self.vosk_model / 'heartbeat'
        self.assertTrue(heartbeat.exists())
        stamp = heartbeat.stat().st_mtime_ns
        time.sleep(.1)
        self.assertEqual(heartbeat.stat().st_mtime_ns, stamp)

    def test_comparison_loads_once_in_resident_worker_and_cleans_audio(self):
        text = '-- Grüße Ivan, ich bin bereit.'
        result = subprocess.run(self.command() + ['--text=  ' + text + '  '], env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(self.report.read_text())
        self.assertNotIn('error', report)
        self.assertEqual(report['text'], text)
        actual_texts = [json.loads(line) for line in
                        Path(str(self.model) + '.texts').read_text().splitlines()]
        self.assertEqual(actual_texts, [text] * 4)
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

    def test_multiline_text_is_rejected_before_workers_start(self):
        result = subprocess.run(self.command() + ['--text', 'Hallo\nIvan'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('single line', result.stderr)
        self.assertFalse(self.report.exists())

    def test_startup_signals_save_partial_report_and_finalization_resists_signals(self):
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig):
                original = {s: signal.getsignal(s) for s in
                            (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
                calls = 0
                def snapshot():
                    nonlocal calls
                    calls += 1
                    os.kill(os.getpid(), sig)
                    # Only finalization reaches this point. Repeated signals
                    # must not discard the report or interrupt child cleanup.
                    for extra in original:
                        os.kill(os.getpid(), extra)
                    return {'snapshot': 'finished'}
                with (patch.object(sys, 'argv', self.command()[1:]),
                      patch.object(profile, 'system_memory', return_value={}),
                      patch.object(profile, 'pi_snapshot', side_effect=snapshot),
                      patch.object(profile, 'service_snapshot', return_value=None),
                      patch.object(profile, 'run_worker') as worker):
                    self.assertEqual(profile.main(), 130)
                worker.assert_not_called()
                report = json.loads(self.report.read_text())
                self.assertEqual(report['error'], 'Interrupted')
                self.assertIsNone(report['pi_before'])
                self.assertEqual(report['pi_after'], {'snapshot': 'finished'})
                self.assertEqual(report['records'], [])
                self.assertEqual(calls, 2)
                for s, handler in original.items():
                    self.assertEqual(signal.getsignal(s), handler)

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
