#!/usr/bin/env python3
"""Compare finite PCM streaming variants without models, ALSA or mixer changes."""
import argparse
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voice_effects import build_stream_playback_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg', default=os.environ.get('TTS_FFMPEG_BIN', '/usr/bin/ffmpeg'))
    args = parser.parse_args()
    version = subprocess.run([args.ffmpeg, '-version'], capture_output=True,
                             text=True, check=True, timeout=10)
    print(version.stdout.splitlines()[0], flush=True)
    pcm = b''.join(struct.pack('<h', int(4000 * math.sin(2 * math.pi * 440 * i / 16000)))
                   for i in range(16000))
    base = build_stream_playback_command(16000, 1, 'pipe:1', ffmpeg_bin=args.ffmpeg)
    base[-2] = 's16le'
    for label, input_args, flush, bypass in (
        ('current', [], False, False),
        ('flush_output', [], True, False),
        ('small_pipe_reads', ['-blocksize', '4096'], True, False),
        ('direct_input', ['-avioflags', 'direct', '-blocksize', '4096'], True, False),
        ('no_dsp', [], True, True),
    ):
        command = base.copy()
        index = command.index('-i')
        command[index:index] = input_args
        if bypass:
            index = command.index('-filter_complex')
            command[index + 1] = '[0:a]aresample=48000[out]'
        if flush:
            command[-3:-3] = ['-flush_packets', '1']
        # A regular temporary sink prevents output-pipe backpressure from
        # confounding the input/DSP diagnosis. The input remains a live pipe.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE,
                                    stdout=output, stderr=errors)
            try:
                started = time.monotonic()
                proc.stdin.write(pcm)
                proc.stdin.flush()
                deadline = time.monotonic() + 5
                while os.fstat(output.fileno()).st_size == 0 and proc.poll() is None:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(.02)
                before_eof = os.fstat(output.fileno()).st_size
                waited = time.monotonic() - started
                proc.stdin.close()
                proc.wait(timeout=20)
                errors.seek(0)
                print(json.dumps(dict(variant=label, bytes_before_eof=before_eof,
                                      wait_ms=round(waited * 1000), returncode=proc.returncode,
                                      total_bytes=os.fstat(output.fileno()).st_size,
                                      stderr=errors.read().decode(errors='replace')[-2000:])), flush=True)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
                if proc.stdin is not None and not proc.stdin.closed:
                    proc.stdin.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
