#!/usr/bin/env python3
"""Bounded Linux benchmark: fresh Piper CLI processes versus one loaded voice.

Standard-library coordinator, Piper only in the existing venv worker. No playback,
downloads, mixer changes or service restarts. Temporary generated audio is removed.
"""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import resource
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import wave

DEFAULT_MODEL = '/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx'
DEFAULT_PYTHON = '/opt/pi-voice-assistant/.venv/bin/python'
MIB = 1024 * 1024


def proc_memory(path='/proc/self/status'):
    try:
        fields = {}
        for line in Path(path).read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('VmRSS', 'VmHWM', 'VmSwap'):
                fields[key] = int(value.split()[0]) / 1024
        return dict(rss_mib=fields.get('VmRSS'),
                    peak_rss_mib=fields.get('VmHWM'), swap_mib=fields.get('VmSwap'))
    except (OSError, ValueError):
        return dict(rss_mib=None, peak_rss_mib=None, swap_mib=None)


def system_memory():
    fields = {}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            if key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
                fields[key] = int(value.split()[0]) / 1024
    except (OSError, ValueError):
        pass
    result = {'total_mib': fields.get('MemTotal'),
              'available_mib': fields.get('MemAvailable'),
              'swap_total_mib': fields.get('SwapTotal'),
              'swap_used_mib': None}
    if 'SwapTotal' in fields and 'SwapFree' in fields:
        result['swap_used_mib'] = fields['SwapTotal'] - fields['SwapFree']
    try:
        counters = dict(line.split() for line in Path('/proc/vmstat').read_text().splitlines())
        page_mib = os.sysconf('SC_PAGE_SIZE') / MIB
        result['swap_in_mib'] = int(counters['pswpin']) * page_mib
        result['swap_out_mib'] = int(counters['pswpout']) * page_mib
    except (OSError, ValueError, KeyError):
        pass
    return result


def service_snapshot():
    try:
        result = subprocess.run(['systemctl', 'show', 'pi-ptt.service',
                                 '--property=MainPID', '--value'],
                                capture_output=True, text=True, timeout=2, check=True)
        pid = int(result.stdout.strip())
        if pid <= 0:
            return None
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return dict(pid=pid, start_ticks=int(stat[19]),
                    cpu_seconds=(int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK'),
                    **proc_memory(f'/proc/{pid}/status'))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def pi_snapshot():
    values = {}
    for name, path, divisor in [
        ('temperature_c', '/sys/class/thermal/thermal_zone0/temp', 1000),
        ('cpu_frequency_mhz', '/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', 1000),
    ]:
        try:
            values[name] = int(Path(path).read_text().strip()) / divisor
        except (OSError, ValueError):
            values[name] = None
    try:
        result = subprocess.run(['vcgencmd', 'get_throttled'], capture_output=True,
                                text=True, timeout=2, check=True)
        values['throttled_flags'] = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        values['throttled_flags'] = None
    return values


def audio_duration(path):
    with wave.open(str(path), 'rb') as audio:
        frames, rate = audio.getnframes(), audio.getframerate()
        if not frames or rate <= 0:
            raise RuntimeError('Piper produced empty audio')
        return frames / rate


def emit(record):
    print(json.dumps(record, ensure_ascii=False), flush=True)


def measured_phase(label, operation, audio=None, child=False):
    who = resource.RUSAGE_CHILDREN if child else resource.RUSAGE_SELF
    before = resource.getrusage(who)
    start = time.monotonic()
    operation()
    elapsed = time.monotonic() - start
    after = resource.getrusage(who)
    cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
    record = dict(phase=label, elapsed_seconds=elapsed, cpu_seconds=cpu,
                  cpu_pct_one_core=100 * cpu / elapsed if elapsed else None,
                  peak_rss_mib=after.ru_maxrss / 1024)
    if child:
        # One dedicated coordinator worker per CLI run: CHILDREN high-water
        # mark belongs to that single CLI, not earlier benchmark processes.
        record['memory_scope'] = 'Piper CLI child; lifetime high-water RSS'
    else:
        record['memory_scope'] = 'loaded-voice worker; cumulative lifetime high-water RSS'
        memory = proc_memory()
        record['rss_mib'] = memory['rss_mib']
        record['swap_mib'] = memory['swap_mib']
    if audio is not None:
        duration = audio_duration(audio)
        record['audio_seconds'] = duration
        record['real_time_factor'] = elapsed / duration
    emit(record)


def worker(args):
    if args.worker == 'cli':
        output = Path(args.directory) / 'cli.wav'
        def synthesize():
            subprocess.run([sys.executable, '-m', 'piper', '-m', args.model,
                            '-f', str(output)],
                           input=args.text, text=True, stdout=subprocess.DEVNULL,
                           check=True)
        measured_phase('fresh_process', synthesize, audio=output, child=True)
        return
    voice = None
    def load():
        nonlocal voice
        from piper import PiperVoice
        voice = PiperVoice.load(args.model)
    try:
        version = importlib.metadata.version('piper-tts')
    except importlib.metadata.PackageNotFoundError:
        version = None
    emit(dict(phase='environment', piper_version=version, python=sys.version,
              cpu_count=os.cpu_count()))
    measured_phase('load_once', load)
    for index in range(args.repeats):
        output = Path(args.directory) / 'warm.wav'
        def synthesize():
            with wave.open(str(output), 'wb') as audio:
                voice.synthesize_wav(args.text, audio)
        measured_phase(f'resident_{index + 1}', synthesize, audio=output)
    measured_phase('resident_idle', lambda: time.sleep(args.idle_seconds))


def stop_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=2)


def summarize_samples(samples):
    available = [x['available_mib'] for x in samples if x.get('available_mib') is not None]
    swap = [x['swap_used_mib'] for x in samples if x.get('swap_used_mib') is not None]
    return dict(min_available_mib=min(available) if available else None,
                max_swap_used_mib=max(swap) if swap else None)


def run_worker(args, mode, directory, records, samples):
    command = [args.piper_python, str(Path(__file__).resolve()), '--worker', mode,
               '--directory', str(directory), '--model', args.model, '--text=' + args.text,
               '--repeats', str(args.repeats), '--idle-seconds', str(args.idle_seconds)]
    with (directory / f'{mode}.stderr').open('wb') as errors:
        proc = None
        selector = selectors.DefaultSelector()
        try:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
            selector.register(proc.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + args.timeout
            pending = b''
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'{mode} worker exceeded {args.timeout:g} seconds')
                samples.append(system_memory())
                for key, _ in selector.select(timeout=.2):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    pending += data
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        record = json.loads(line)
                        records.append(record)
                        if record['phase'] == 'environment':
                            print(f"Piper {record['piper_version']}; {record['cpu_count']} CPUs", flush=True)
                        else:
                            print(f"{record['phase']}: {record['elapsed_seconds']:.2f} s, "
                                  f"CPU {record['cpu_pct_one_core']:.0f} %, "
                                  f"peak RSS {record['peak_rss_mib']:.1f} MiB", flush=True)
            proc.wait(timeout=max(.1, deadline - time.monotonic()))
            if pending.strip():
                raise RuntimeError('Incomplete worker record')
            if proc.returncode:
                errors.flush()
                detail = (directory / f'{mode}.stderr').read_text(errors='replace')[-4000:]
                raise RuntimeError(f'{mode} failed ({proc.returncode}): {detail}')
        finally:
            # Also clean up Piper descendants if a timeout/interrupt occurred.
            try:
                if proc is not None:
                    stop_group(proc)
            finally:
                selector.close()
                if proc is not None:
                    proc.stdout.close()


def save_report(path, report):
    path = Path(path).expanduser().resolve()
    with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix=path.name + '.',
                                     suffix='.tmp', delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--piper-python', default=DEFAULT_PYTHON)
    parser.add_argument('--model', default=os.environ.get('PIPER_MODEL', DEFAULT_MODEL))
    parser.add_argument('--text', default='Hallo Ivan, ich bin bereit.')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--idle-seconds', type=float, default=10)
    parser.add_argument('--timeout', type=float, default=180,
                        help='maximum seconds per fresh CLI run / complete resident worker')
    parser.add_argument('--output', default='/tmp/pi-piper-resources.json')
    parser.add_argument('--worker', choices=['cli', 'resident'], help=argparse.SUPPRESS)
    parser.add_argument('--directory', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 5:
        parser.error('--repeats must be between 1 and 5')
    if not math.isfinite(args.idle_seconds) or not 1 <= args.idle_seconds <= 60:
        parser.error('--idle-seconds must be between 1 and 60')
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 600:
        parser.error('--timeout must be between 1 and 600')
    if not args.text.strip() or len(args.text) > 500:
        parser.error('--text must have 1–500 characters')
    if '\n' in args.text or '\r' in args.text:
        parser.error('--text must be a single line for matching CLI/API input')
    args.text = args.text.strip()
    if args.worker:
        if not args.directory:
            parser.error('worker requires --directory')
        worker(args)
        return 0
    if not Path(args.piper_python).is_file() or not os.access(args.piper_python, os.X_OK):
        parser.error(f'Piper interpreter missing: {args.piper_python}')
    if not Path(args.model).is_file() or not Path(args.model + '.json').is_file():
        parser.error('Model and matching .onnx.json must already exist')
    report = dict(schema_version=1, model=args.model,
                  text=args.text, repeats=args.repeats, idle_seconds=args.idle_seconds,
                  timeout_per_worker_seconds=args.timeout,
                  system_before=None, pi_before=None, voice_service_before=None, records=[])
    samples = []
    code = 0
    finalizing = False
    interruption_seen = False
    previous_handlers = {}
    def interrupted(*_):
        nonlocal interruption_seen
        # A second signal must not interrupt child cleanup or report saving.
        if not finalizing and not interruption_seen:
            interruption_seen = True
            raise KeyboardInterrupt
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous_handlers[sig] = signal.signal(sig, interrupted)
        report.update(time_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      platform=platform.platform(), cpu_count=os.cpu_count(),
                      model_bytes=Path(args.model).stat().st_size)
        report['system_before'] = system_memory()
        report['pi_before'] = pi_snapshot()
        report['voice_service_before'] = service_snapshot()
        print('Keine Wiedergabe. Bitte während des Tests keine Tasten drücken.', flush=True)
        with tempfile.TemporaryDirectory(prefix='pi-piper-profile-') as temporary:
            directory = Path(temporary)
            for index in range(args.repeats):
                print(f'Frischer Piper-Prozess {index + 1}/{args.repeats} …', flush=True)
                run_worker(args, 'cli', directory, report['records'], samples)
            print('Modell einmal laden, mehrfach erzeugen, anschließend in Ruhe halten …', flush=True)
            run_worker(args, 'resident', directory, report['records'], samples)
    except KeyboardInterrupt:
        report['error'] = 'Interrupted'
        code = 130
    except (OSError, RuntimeError, TimeoutError, ValueError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
        print(f'Fehler: {exc}', file=sys.stderr)
        code = 1
    finally:
        finalizing = True
        try:
            report['system_during'] = summarize_samples(samples)
            report['system_after'] = system_memory()
            report['pi_after'] = pi_snapshot()
            report['voice_service_after'] = service_snapshot()
            save_report(args.output, report)
            print(f'Bericht: {args.output}', flush=True)
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
