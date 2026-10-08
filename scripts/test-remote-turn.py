#!/usr/bin/env python3
"""Drive one Servitor server turn from the Pi without pressing PTT.

Streams a 16 kHz mono s16le WAV in real time through the same uplink/job
classes pi-ptt uses, prints the server events with times relative to the end
of the upload (the "button release") and optionally plays the reply on the
WM8960. Settings come from /etc/pi-voice-assistant.env; the token is never
printed.

  python3 scripts/test-remote-turn.py question.wav --play
  python3 scripts/test-remote-turn.py question.wav --base-url http://127.0.0.1:9
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import time
import wave

ENV_FILE = '/etc/pi-voice-assistant.env'


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('wav', help='16 kHz mono 16-bit WAV with the spoken question')
    parser.add_argument('--src', default='/opt/pi-voice-assistant/src')
    parser.add_argument('--env', default=ENV_FILE)
    parser.add_argument('--base-url', help='override ASSISTANT_BASE_URL (e.g. to test failures)')
    parser.add_argument('--format', choices=('wav', 'opus'))
    parser.add_argument('--play', action='store_true', help='play the reply with aplay')
    parser.add_argument('--device', default=None, help='ALSA device, default TTS_AUDIO_DEVICE')
    args = parser.parse_args()

    sys.path.insert(0, args.src)
    from remote_turn import RemoteTurnJob, RemoteTurnUplink, load_remote_config

    env = read_env(args.env)
    if args.base_url:
        env['ASSISTANT_BASE_URL'] = args.base_url
    if args.format:
        env['ASSISTANT_AUDIO_FORMAT'] = args.format
    config = load_remote_config(env)
    if config is None:
        parser.error('ASSISTANT_BASE_URL is empty')

    with wave.open(args.wav, 'rb') as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            parser.error('WAV must be 16 kHz, mono, 16-bit')
        pcm = audio.readframes(audio.getnframes())
    print(f'hosts={config.hosts} format={config.audio_format} audio={len(pcm) / 32000:.2f}s',
          flush=True)

    from power import Battery, throttled_flags
    from system_status import collect_snapshot
    status = collect_snapshot(battery=Battery().read(), throttled=throttled_flags(), server='ok')
    print(f'status snapshot: {status}', flush=True)
    uplink = RemoteTurnUplink(config, status=status)
    started = time.monotonic()
    for offset in range(0, len(pcm), 3200):  # 0.1 s blocks, paced like arecord
        uplink.accept_pcm(pcm[offset:offset + 3200])
        time.sleep(max(0.0, started + (offset + 3200) / 32000 - time.monotonic()))
    uplink.finish()
    released = time.monotonic()

    with tempfile.TemporaryDirectory() as directory:
        job = RemoteTurnJob(uplink, directory)
        while not job.done.wait(0.05):
            for item in job.drain():
                print(f'{time.monotonic() - released:6.3f}s {item}', flush=True)
        for item in job.drain():
            print(f'{time.monotonic() - released:6.3f}s {item}', flush=True)
        ready = time.monotonic() - released
        if job.error is not None:
            print(f'{ready:6.3f}s ERROR stage={job.error_stage} code={job.error_code} '
                  f'fallback_allowed={job.fallback_allowed} transcript={job.transcript!r} '
                  f'message={job.error}', flush=True)
            return 1
        print(f'{ready:6.3f}s audio ready from {job.result["host"]}', flush=True)
        if args.play:
            import subprocess
            device = args.device or env.get('TTS_AUDIO_DEVICE',
                                            'plughw:CARD=wm8960soundcard,DEV=0')
            print(f'{time.monotonic() - released:6.3f}s playback start', flush=True)
            subprocess.run(['/usr/bin/aplay', '-q', '-D', device, str(job.result['audio'])],
                           check=True, timeout=120)
            print(f'{time.monotonic() - released:6.3f}s playback end', flush=True)
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    sys.exit(main())
