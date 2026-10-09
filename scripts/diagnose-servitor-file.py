#!/usr/bin/env python3
"""Measure file DSP without models or ALSA; input is never modified."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voice_effects import SERVITOR_FILTER_GRAPH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--ffmpeg', default=os.environ.get('TTS_FFMPEG_BIN', '/usr/bin/ffmpeg'))
    args = parser.parse_args()
    with wave.open(str(args.wav), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    print(json.dumps(dict(event='input', audio_duration_ms=round(duration * 1000))), flush=True)
    failed = False
    with tempfile.TemporaryDirectory(prefix='servitor-dsp-') as directory:
        for variant, graph, threads in (
            ('no_dsp', '[0:a]aresample=48000[out]', None),
            ('servitor_auto', SERVITOR_FILTER_GRAPH, None),
            ('servitor_one_thread', SERVITOR_FILTER_GRAPH, 1),
        ):
            output = Path(directory) / (variant + '.wav')
            command = [args.ffmpeg, '-hide_banner', '-nostdin', '-nostats',
                       '-loglevel', 'info', '-benchmark']
            if threads is not None:
                command += ['-filter_complex_threads', str(threads)]
            command += ['-i', str(args.wav.resolve()), '-filter_complex', graph,
                        '-map', '[out]', '-ac', '1', '-ar', '48000',
                        '-c:a', 'pcm_s16le', str(output)]
            started = time.monotonic()
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                print(json.dumps(dict(variant=variant, error='timeout after 120 seconds')), flush=True)
                failed = True
                continue
            record = dict(variant=variant, elapsed_ms=round((time.monotonic() - started) * 1000),
                          returncode=result.returncode,
                          benchmark=[line for line in result.stderr.splitlines() if line.startswith('bench:')])
            if result.returncode == 0:
                with wave.open(str(output), 'rb') as audio:
                    pcm = audio.readframes(audio.getnframes())
                    record['audio_duration_ms'] = round(audio.getnframes() * 1000 / audio.getframerate())
                    record['pcm_sha256'] = hashlib.sha256(pcm).hexdigest()
            else:
                record['stderr'] = result.stderr[-2000:]
                failed = True
            print(json.dumps(record), flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
