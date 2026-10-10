#!/usr/bin/env python3
"""Microphone level check and real-voice recordings for the STT comparison.

Records exactly like PTT (arecord, 16 kHz mono, PTT_AUDIO_DEVICE). The mic
must be free: stop the service first (the wake-word listener holds it).

  sudo systemctl stop pi-ptt
  python3 scripts/mic-check.py level              # 2 s quiet, then 4 s speech
  python3 scripts/mic-check.py record ~/stt-clips  # 20 sentences, one per Enter
  sudo systemctl start pi-ptt

"level" prints noise floor, speech level, peak, clipping and SNR (dBFS) with
a verdict for the WM8960 "Capture" control. "record" stores NN.wav plus
references.json for server/bench-stt.py --clips (copy the folder to CT 107).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

RATE = 16000
FRAME = RATE // 50                 # 20 ms
CLIP = 32700
DEVICE = os.environ.get('PTT_AUDIO_DEVICE', 'plughw:CARD=wm8960soundcard,DEV=0')
CARD = 'wm8960soundcard'

# Everyday requests of this device plus the synthetic benchmark sentences.
SENTENCES = (
    'wie spät ist es',
    'morgenbericht',
    'wie wird das wetter morgen',
    'wie ist der status',
    'wie ist das netzwerk',
    'gibt es updates',
    'wer bist du',
    'merke dir dass ich gerne kaffee trinke',
    'erzähl mir einen witz',
    'mach das wlan aus',
    'wie hoch ist der eiffelturm',
    'was ist die hauptstadt von australien',
    'wie spät ist es in tokio',
    'wie viele minuten hat ein tag',
    'wie viel ist siebzehn mal dreiundzwanzig',
    'erkläre kurz was ein raspberry pi ist',
    'stell einen timer auf zehn minuten',
    'wie wird das wetter morgen in berlin',
    'was ist der unterschied zwischen wetter und klima',
    'nenne drei planeten unseres sonnensystems',
)


def record(path, seconds, device=DEVICE):
    run = subprocess.run(['arecord', '-q', '-D', device, '-t', 'wav', '-f', 'S16_LE',
                          '-r', str(RATE), '-c', '1', '-d', str(seconds), str(path)],
                         capture_output=True, text=True)
    if run.returncode:
        sys.exit(f'arecord failed ({run.stderr.strip()}). Is pi-ptt still running? '
                 'sudo systemctl stop pi-ptt')


def samples_of(path):
    with wave.open(str(path), 'rb') as wav:
        data = array('h', wav.readframes(wav.getnframes()))
    if sys.byteorder == 'big':
        data.byteswap()
    return data


def dbfs(value):
    return 20 * math.log10(max(value, 1e-9) / 32768)


def frame_levels(samples):
    return [math.sqrt(sum(s * s for s in samples[i:i + FRAME]) / FRAME)
            for i in range(0, len(samples) - FRAME + 1, FRAME)]


def analyze(samples, quiet_seconds=None):
    """Levels in dBFS. Without a known quiet part, the quietest fifth of the
    frames is the noise floor and the loudest tenth the speech."""
    levels = frame_levels(samples)
    if not levels:
        return None
    if quiet_seconds:
        skip = int(0.3 * 50)                        # arecord start-up click
        quiet = sorted(levels[skip:int(quiet_seconds * 50)]) or [0.0]
        speech = sorted(levels[int(quiet_seconds * 50):]) or [0.0]
    else:
        quiet = speech = sorted(levels)
        quiet = quiet[:max(1, len(quiet) // 5)]
    noise = quiet[len(quiet) // 2]
    loud = speech[int(len(speech) * 0.9):] or speech[-1:]
    voice = sum(loud) / len(loud)
    peak = max(abs(s) for s in samples)
    clipped = sum(1 for s in samples if abs(s) >= CLIP) / len(samples)
    return dict(noise=dbfs(noise), speech=dbfs(voice), peak=dbfs(peak),
                clipped=clipped * 100, snr=dbfs(voice) - dbfs(noise),
                dc=sum(samples) / len(samples))


def verdict(result):
    tips = []
    if result['clipped'] > 0.05 or result['peak'] > -1:
        tips.append('ZU LAUT: Übersteuerung. Capture um 3–6 Schritte senken '
                    '(oder Input Boost von 3 auf 2).')
    elif result['speech'] < -32:
        tips.append('ZU LEISE: Capture um 3–6 Schritte anheben (1 Schritt = 0,75 dB).')
    elif result['speech'] > -10:
        tips.append('Sehr laut: knapp vor der Übersteuerung, Capture etwas senken.')
    else:
        tips.append('Pegel ok (Sprache −32 … −10 dBFS, keine Übersteuerung).')
    if result['snr'] < 15:
        tips.append('Rauschabstand knapp (< 15 dB): Störquelle? Näher sprechen; '
                    'mehr Verstärkung hilft hier nicht.')
    elif result['snr'] < 25:
        tips.append('Rauschabstand mittel (15–25 dB): Erkennung leidet leicht.')
    else:
        tips.append('Rauschabstand gut (≥ 25 dB).')
    if abs(result['dc']) > 300:
        tips.append(f"Gleichanteil {result['dc']:.0f}: ADC High Pass Filter einschalten.")
    return tips


def show(result):
    print(f"  Rauschen  {result['noise']:6.1f} dBFS")
    print(f"  Sprache   {result['speech']:6.1f} dBFS   (Ziel −32 … −10)")
    print(f"  Spitze    {result['peak']:6.1f} dBFS   übersteuert {result['clipped']:.2f} %")
    print(f"  Abstand   {result['snr']:6.1f} dB      (Ziel ≥ 25)")


def mixer():
    for control in ('Capture', 'Left Input Boost Mixer LINPUT1', 'ADC PCM'):
        run = subprocess.run(['amixer', '-c', CARD, 'sget', control],
                             capture_output=True, text=True)
        lines = [line.strip() for line in run.stdout.splitlines() if 'Limits' in line
                 or line.strip().startswith(('Front Left', 'Mono'))]
        if lines:
            print(f'  {control}: ' + ' | '.join(lines))


def level(args):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'level.wav'
        print('Gleich 2 s RUHE, dann 4 s normal sprechen, wie zu Proximus (Alltagsabstand).')
        input('Enter zum Start … ')
        record(path, 6, args.device)
        result = analyze(samples_of(path), quiet_seconds=2)
    print('Ergebnis:')
    show(result)
    for tip in verdict(result):
        print('  →', tip)
    print('Mixer:')
    mixer()
    print('Ändern: amixer -c wm8960soundcard sset Capture 36   '
          '(danach erneut messen; sudo alsactl store wm8960soundcard)')


def record_set(args):
    folder = Path(args.folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    references = []
    print(f'{len(SENTENCES)} Sätze. Nach Enter {args.seconds} s Aufnahme: '
          'Satz normal sprechen, wie im Alltag. "w" + Enter wiederholt den letzten.')
    index = 0
    while index < len(SENTENCES):
        sentence = SENTENCES[index]
        answer = input(f'[{index + 1}/{len(SENTENCES)}] Sag: „{sentence}“ – Enter … ')
        if answer.strip().lower() == 'w' and index:
            index -= 1
            references.pop()
            continue
        path = folder / f'{index + 1:02d}.wav'
        record(path, args.seconds, args.device)
        result = analyze(samples_of(path))
        note = ' ÜBERSTEUERT' if result['clipped'] > 0.05 else ''
        print(f"    Sprache {result['speech']:.0f} dBFS, Abstand {result['snr']:.0f} dB{note}")
        references.append(dict(file=path.name, text=sentence))
        index += 1
    (folder / 'references.json').write_text(
        json.dumps(references, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'Fertig: {folder}. Auf CT 107 kopieren und vergleichen:')
    print(f'  scp -r {folder} root@ct107:/root/stt-clips')
    print('  /opt/servitor-voice/.venv/bin/python /opt/servitor-voice/repo/server/bench-stt.py '
          '--clips /root/stt-clips vosk:/opt/servitor-voice/models/vosk-model-small-de-0.15 '
          'whisper:small')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--device', default=DEVICE)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('level', help='noise, speech level, clipping')
    rec = sub.add_parser('record', help='record the sentences for bench-stt.py --clips')
    rec.add_argument('folder')
    rec.add_argument('--seconds', type=int, default=5)
    args = parser.parse_args()
    (level if args.command == 'level' else record_set)(args)


if __name__ == '__main__':
    main()
