#!/usr/bin/env python3
"""Render identical PCM through PR #26 and the PCM-clocked DSP; no playback."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from voice_effects import SERVITOR_FILTER_GRAPH

parser = argparse.ArgumentParser()
parser.add_argument('repo')
parser.add_argument('audio')
parser.add_argument('output')
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
source = subprocess.check_output(['git', '-C', args.repo, 'show', '6d95499:src/voice_effects.py'], text=True)
namespace = {}
exec(compile(source, 'PR26/voice_effects.py', 'exec'), namespace)
report = {}
for label, graph in [('pr26', namespace['SERVITOR_FILTER_GRAPH']), ('pcm-clocked', SERVITOR_FILTER_GRAPH)]:
    target = output / (label + '.wav')
    started = time.monotonic()
    result = subprocess.run(['/usr/bin/ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin',
        '-filter_complex_threads', '1', '-i', args.audio, '-filter_complex', graph,
        '-map', '[out]', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', '-y', str(target)],
        capture_output=True, text=True, timeout=60, check=True)
    with wave.open(str(target), 'rb') as audio:
        report[label] = dict(render_ms=round((time.monotonic()-started)*1000),
                            frames=audio.getnframes(), rate=audio.getframerate())
import numpy as np
with wave.open(str(output/'pr26.wav'), 'rb') as audio:
    first = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float64)
with wave.open(str(output/'pcm-clocked.wav'), 'rb') as audio:
    second = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float64)
length = min(len(first),len(second))
report['comparison'] = dict(samples_compared=length,
    peak_delta=int(np.max(np.abs(first[:length]-second[:length]))),
    rms_delta=float(np.sqrt(np.mean((first[:length]-second[:length])**2))),
    correlation=float(np.corrcoef(first[:length], second[:length])[0,1]))
(output/'comparison.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
