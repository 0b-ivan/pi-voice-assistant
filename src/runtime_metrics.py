"""Small phase timers; no audio, text or credentials are included in metrics."""
from contextlib import contextmanager
import json
import resource
from pathlib import Path
import os
import time
import sys
import threading


DISPLAY_PHASES = {
    ('tts', 'import'), ('tts', 'model_load'), ('tts', 'warmup'),
    ('tts', 'synthesis'), ('tts', 'dsp_render'), ('tts', 'playback'),
    ('tts', 'worker_total'), ('stt', 'live_finalize'), ('stt', 'recognition'),
}


def display_progress(stage, metric):
    """Publish only a fixed phase identifier, never speech or response content."""
    if (stage, metric) not in DISPLAY_PHASES:
        return
    runtime = Path(os.environ.get('PTT_RUNTIME_DIR', '/run/pi-ptt'))
    path = Path(os.environ.get('PI_DISPLAY_PROGRESS_FILE', str(runtime / 'display-progress.json')))
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        payload = dict(version=1, stage=stage, metric=metric, timestamp=time.time())
        temporary.write_text(json.dumps(payload) + '\n', encoding='utf-8')
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def phase(stage, metric, stream=None):
    display_progress(stage, metric)
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    try:
        yield
    finally:
        after = resource.getrusage(resource.RUSAGE_SELF)
        record = dict(version=1, event='latency', stage=stage, metric=metric,
                      latency_ms=round((time.monotonic() - started) * 1000),
                      cpu_ms=round((after.ru_utime + after.ru_stime
                                    - before.ru_utime - before.ru_stime) * 1000))
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                name, _, value = line.partition(':')
                if name in ('VmRSS', 'VmSwap'):
                    record['rss_kib' if name == 'VmRSS' else 'swap_kib'] = int(value.split()[0])
        except (OSError, ValueError):
            pass
        print(json.dumps(record), file=stream or sys.stdout, flush=True)


def process_ready(stage):
    value = os.environ.get('VOICE_WORKER_STARTED_AT')
    if value:
        print(json.dumps(dict(version=1, event='latency', stage=stage,
                              metric='worker_start',
                              latency_ms=round((time.monotonic() - float(value)) * 1000))),
              file=sys.stderr, flush=True)
