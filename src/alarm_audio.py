"""Alarm announcements from prerecorded clips: no synthesis when it matters.

Alarm sentences are cut at numbers into fragments ("Warnung", "1", "von",
"3", "Energiespeicher bei", "15", "Prozent. Netzteil anschließen.").
``python src/alarm_audio.py build`` has the Servitor server render every
fragment of every alarm sentence and the numbers 0-100 once, in the same
voice as normal replies, into VOICE_DIR. pi-ptt only reads them: an alarm
is assembled by joining WAV frames and played with aplay, so it works
offline, without the server and when the Pi is short of CPU or memory.
Clips are trimmed when stored; at alarm time nothing is computed. If a
fragment is missing, pi-ptt falls back to live synthesis.
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import wave
from array import array
from pathlib import Path

VOICE_DIR = Path(os.environ.get('PTT_ALARM_VOICE_DIR',
                                '/opt/pi-voice-assistant/models/alarm-voice'))
NUMBERS = range(101)
GAP = 0.06              # seconds between fragments of one sentence
SENTENCE_GAP = 0.35     # after a full stop and between alarm sentences
QUIET = 300             # int16 level treated as silence when trimming clips
MARGIN = 0.015          # seconds of silence kept at each clip edge
PACE = 6.0              # seconds between renders: half the server's rate limit stays free


def fragments(text):
    """[(piece, pause before it in seconds)], digits as pieces of their own."""
    out, pause = [], 0.0
    for piece in re.split(r'(\d+)', text):
        piece = piece.strip()
        lead = re.match(r'[.,;:!?]+', piece)
        if lead:
            pause = SENTENCE_GAP if set(lead.group()) & set('.!?') else max(pause, GAP * 2)
            piece = piece[lead.end():].strip()
        if not piece:
            continue
        out.append((piece, pause if out else 0.0))
        pause = SENTENCE_GAP if piece[-1] in '.!?' else GAP
    return out


def clip_path(piece, directory=None):
    name = hashlib.sha1(piece.encode('utf-8')).hexdigest()[:16]
    return Path(directory or VOICE_DIR) / f'{name}.wav'


def known_pieces():
    """Every fragment an alarm can consist of, plus the numbers."""
    from alarms import (ALARMS, BATTERY_STAGES, SHUTDOWN_CANCELLED, SHUTDOWN_FAILED,
                        SHUTDOWN_NOW, WAKE_PHRASES, _battery_phrase, _phrase,
                        AlarmMonitor, memory_phrase, power_source_phrase)
    snapshot = dict(battery_pct=1, temp_c=1, load_pct=1)
    texts = [SHUTDOWN_NOW, SHUTDOWN_CANCELLED, SHUTDOWN_FAILED, *WAKE_PHRASES.values()]
    for lore in ('off', 'full'):  # alarm wording only knows full lore or not
        texts += [_phrase(key, snapshot, lore) for key in ALARMS]
        texts += [_phrase(key, snapshot, lore, recovered=True)
                  for key in ('internet', 'network', 'server')]
        texts += [_battery_phrase(stage, 1, lore) for stage in range(1, len(BATTERY_STAGES) + 1)]
        texts += [power_source_phrase(plugged, percent, lore)
                  for plugged in (True, False) for percent in (1, None)]
        texts += [memory_phrase(present, facts, lore)
                  for present in (True, False) for facts in (1, 0, 101)]
        texts += [_phrase(key, snapshot, lore, recovered=True)
                  for key in ('dns', 'wifi_weak', 'latency')]
        texts += [AlarmMonitor().updates_notice(dict(updates=pi, updates_security=ps,
                                                     server_updates=sv,
                                                     server_updates_security=ss), 0, lore)
                  for pi in (0, 1, 2) for ps in (0, 1) for sv in (0, 1, 2) for ss in (0, 1)
                  if (pi or not ps) and (sv or not ss) and (pi or sv)]
    pieces = {piece for text in texts for piece, _ in fragments(text)}
    return sorted(pieces | {str(n) for n in NUMBERS})


def _trim(frames, width, channels, rate):
    """Cut leading/trailing silence so fragments join without long gaps."""
    if width != 2:
        return frames
    samples = array('h', frames)
    if sys.byteorder == 'big':
        samples.byteswap()
    loud = [i for i in range(0, len(samples), channels) if abs(samples[i]) > QUIET]
    if not loud:
        return frames
    margin = int(MARGIN * rate) * channels
    start = max(0, loud[0] - margin)
    end = min(len(samples), loud[-1] + channels + margin)
    return frames[start * 2:end * 2]


def _store(audio, path):
    """Save a rendered WAV with its silent edges cut off (done once here,
    so assembling an alarm is only joining bytes)."""
    import io
    with wave.open(io.BytesIO(audio), 'rb') as clip:
        channels, width, rate = clip.getnchannels(), clip.getsampwidth(), clip.getframerate()
        frames = _trim(clip.readframes(clip.getnframes()), width, channels, rate)
    temporary = path.with_suffix('.tmp')
    with wave.open(str(temporary), 'wb') as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames)
    temporary.replace(path)


def assemble(texts, out_path, directory=None):
    """Write one WAV for the alarm sentences; False if a clip is missing."""
    parts, params = [], None
    for index, text in enumerate(texts):
        for number, (piece, pause) in enumerate(fragments(text)):
            if index and not number:
                pause = SENTENCE_GAP
            try:
                with wave.open(str(clip_path(piece, directory)), 'rb') as clip:
                    clip_params = (clip.getnchannels(), clip.getsampwidth(), clip.getframerate())
                    frames = clip.readframes(clip.getnframes())
            except (OSError, EOFError, wave.Error):
                return False
            if params is None:
                params = clip_params
            elif clip_params != params:
                return False
            channels, width, rate = params
            silence = bytes(int(pause * rate) * channels * width)
            parts.append(silence + frames)
    if params is None:
        return False
    out_path = Path(out_path)
    temporary = out_path.with_suffix('.tmp')
    with wave.open(str(temporary), 'wb') as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(b''.join(parts))
    temporary.replace(out_path)
    return True


def _render(url, token, piece, user_agent):
    request = urllib.request.Request(
        f'{url}/v1/speak?format=wav', data=json.dumps({'text': piece}).encode(), method='POST',
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
                 'User-Agent': user_agent})
    with urllib.request.urlopen(request, timeout=60) as response:
        for line in response:
            if line.strip():
                item = json.loads(line)
                if item.get('event') == 'audio' and item.get('format') == 'wav':
                    return base64.b64decode(item['data'])
                if item.get('event') == 'error':
                    raise RuntimeError(item.get('message', 'server error'))
    raise RuntimeError('no audio in reply')


def build(directory=None, force=False, prune=False, out=None, pace=PACE):
    """Render missing clips via the Servitor server (honours its rate limit)."""
    out = out or (lambda message: print(message, flush=True))
    from remote_turn import USER_AGENT, load_remote_config
    config = load_remote_config()
    if config is None:
        raise SystemExit('ASSISTANT_BASE_URL is not set')
    directory = Path(directory or VOICE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    pieces = known_pieces()
    todo = [p for p in pieces if force or not clip_path(p, directory).is_file()]
    out(f'{len(pieces)} fragments, {len(todo)} to render')
    for done, piece in enumerate(todo, 1):
        for attempt in range(20):
            url = config.base_urls[attempt % len(config.base_urls)]
            try:
                audio = _render(url, config.token, piece, USER_AGENT)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise
                time.sleep(float(exc.headers.get('Retry-After') or 30))
            except (OSError, RuntimeError):
                time.sleep(2)
        else:
            raise SystemExit(f'could not render fragment {done}')
        _store(audio, clip_path(piece, directory))
        if done < len(todo):
            time.sleep(pace)
        if done % 20 == 0 or done == len(todo):
            out(f'{done}/{len(todo)}')
    if prune:
        keep = {clip_path(p, directory).name for p in pieces}
        stale = [f for f in directory.glob('*.wav') if f.name not in keep]
        for f in stale:
            f.unlink()
        out(f'removed {len(stale)} stale clips')


if __name__ == '__main__':
    if sys.argv[1:2] != ['build']:
        raise SystemExit('usage: alarm_audio.py build [--force] [--prune]')
    build(force='--force' in sys.argv, prune='--prune' in sys.argv)
