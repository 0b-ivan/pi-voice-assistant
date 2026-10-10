#!/usr/bin/env python3
"""Measure Billy's RVC voice on CT 107 before switching it on.

Synthesizes typical Billy answers with Piper exactly like the voice service
(same model, speaker and settings from /etc/servitor-voice.env), sends each
through the running servitor-rvc worker (server/rvc_worker.py) and prints
the conversion time per second of audio (RTF), the worst turn against
SERVITOR_RVC_TIMEOUT_SECONDS, the worker's memory and the CT's free RAM.
Every combination of --f0 and --pitch is tried without restarting the worker.

The WAVs for listening land in --out: NN-piper.wav (today's natural voice)
and NN-rvc-<f0>-p<pitch>.wav (after RVC), both through the final filter.

  /opt/servitor-voice/.venv/bin/python server/bench-rvc.py [--pitch 0 -2 -4] [--f0 rmvpe pm]
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import wave

SRC = Path(os.environ.get('SERVITOR_SRC', Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(SRC))

ENV_FILE = Path('/etc/servitor-voice.env')
SENTENCES = (
    'Gemerkt.',
    'Es ist sieben Uhr fünfzehn.',
    'Akku bei neun Prozent. Ich brauch Strom, Boss.',
    'Morgen wird es bewölkt, zwölf Grad, am Nachmittag Regen. Nimm besser eine Jacke mit.',
    'Der Eiffelturm ist ungefähr dreihundertdreißig Meter hoch. Gebaut wurde er für die '
    'Weltausstellung achtzehnhundertneunundachtzig, und die Pariser fanden ihn erst hässlich.',
)


def load_env(path=ENV_FILE):
    """The voice service's settings, unless already set in the environment."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.partition('=')
        if sep and not key.startswith('#'):
            os.environ.setdefault(key.strip(), value.strip())


def mem_available_mb():
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'):
                return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return None


def duration(path):
    with wave.open(str(path), 'rb') as audio:
        return audio.getnframes() / audio.getframerate()


def health(url):
    with urllib.request.urlopen(f'{url}/health', timeout=5) as response:
        return json.load(response)


def convert(url, source, target, f0, pitch):
    query = urllib.parse.urlencode(dict(f0=f0, pitch=pitch))
    request = urllib.request.Request(f'{url}/v1/convert?{query}', data=source.read_bytes(),
                                     method='POST', headers={'Content-Type': 'audio/wav'})
    started = time.monotonic()
    with urllib.request.urlopen(request, timeout=300) as response:
        target.write_bytes(response.read())
        worker = float(response.headers.get('X-RVC-Seconds', 'nan'))
    return time.monotonic() - started, worker


def render(source, target, effect):
    from voice_effects import build_render_command
    subprocess.run(build_render_command(source, target, effect=effect), check=True,
                   stdin=subprocess.DEVNULL, capture_output=True)


def synthesize(directory):
    from piper import PiperVoice
    from voice_controls import _synthesize_voice
    voice = PiperVoice.load(os.environ.get(
        'SERVITOR_PIPER_MODEL', '/opt/servitor-voice/tts/de_DE-thorsten_emotional-medium.onnx'))
    clips = []
    for index, sentence in enumerate(SENTENCES, 1):
        path = directory / f'{index:02d}-raw.wav'
        with wave.open(str(path), 'wb') as output:
            _synthesize_voice(voice, sentence, output, 'servitor')  # as the server does
        render(path, directory / f'{index:02d}-piper.wav', 'natural')
        clips.append(path)
    return clips


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', default=os.environ.get('SERVITOR_RVC_URL', 'http://127.0.0.1:8767'))
    parser.add_argument('--out', type=Path, default=Path('/tmp/rvc-bench'))
    parser.add_argument('--f0', nargs='+', default=['rmvpe'])
    parser.add_argument('--pitch', nargs='+', type=int, default=[0])
    args = parser.parse_args()
    load_env()
    url = args.url.rstrip('/')
    timeout = float(os.environ.get('SERVITOR_RVC_TIMEOUT_SECONDS', '10'))

    state = health(url)
    if not state.get('ready'):
        raise SystemExit(f'servitor-rvc is not ready: {state}')
    print(f"model {state['model']}, worker RSS {state['rss_mb']} MB, "
          f"MemAvailable {mem_available_mb()} MB")
    args.out.mkdir(parents=True, exist_ok=True)
    clips = synthesize(args.out)

    results = []
    for f0 in args.f0:
        for pitch in args.pitch:
            rows = []
            for clip in clips:
                target = clip.with_name(clip.name.replace('-raw', f'-rvc-{f0}-p{pitch}-raw'))
                wall, worker = convert(url, clip, target, f0, pitch)
                render(target, target.with_name(target.name.replace('-raw', '')), 'rvc')
                target.unlink()
                rows.append(dict(audio=duration(clip), wall=wall, worker=worker))
            audio = sum(row['audio'] for row in rows)
            worst = max(rows, key=lambda row: row['wall'])
            result = dict(f0=f0, pitch=pitch, rtf=round(sum(r['worker'] for r in rows) / audio, 2),
                          worst_wall_s=round(worst['wall'], 2),
                          worst_audio_s=round(worst['audio'], 1),
                          over_timeout=sum(row['wall'] > timeout for row in rows),
                          rss_mb=health(url)['rss_mb'], mem_available_mb=mem_available_mb())
            results.append(result)
            print(json.dumps(result), flush=True)
    for clip in clips:
        clip.unlink()

    print()
    print(f'Timeout of the voice service: {timeout:g} s (SERVITOR_RVC_TIMEOUT_SECONDS).')
    for result in results:
        verdict = 'fits' if not result['over_timeout'] else \
            f"{result['over_timeout']}/{len(clips)} answers too slow, those keep Piper's voice"
        print(f"  {result['f0']:>6} pitch {result['pitch']:+d}: RTF {result['rtf']} "
              f"(6 s answer ~ {result['rtf'] * 6:.1f} s extra), longest {result['worst_wall_s']} s "
              f"for {result['worst_audio_s']} s audio, {verdict}")
    print(f'Listen: {args.out}/NN-piper.wav vs. NN-rvc-<f0>-p<pitch>.wav')


if __name__ == '__main__':
    main()
