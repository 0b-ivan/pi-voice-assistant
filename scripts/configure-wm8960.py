#!/usr/bin/env python3
"""Enable the WM8960 headphone/AUX output without replacing the audio profile."""
import argparse
import os
import re
import subprocess
from pathlib import Path

CARD = 'wm8960soundcard'
# Analog Headphone range 0..127; 0 mutes, 89 is -32 dB. Begin quietly.
HEADPHONE_INITIAL_RAW = 89


def configure(run=subprocess.run):
    def mixer(*args):
        return run(['amixer', '-c', CARD, *args], check=True, timeout=5,
                   stdin=subprocess.DEVNULL, capture_output=True, text=True)

    output = mixer('sget', 'Headphone').stdout
    channels = re.findall(r'^\s*Front (?:Left|Right): Playback (\d+)\b',
                          output, re.MULTILINE)
    if len(channels) != 2 or any(not 0 <= int(value) <= 127 for value in channels):
        raise ValueError('Cannot read the two WM8960 Headphone levels')
    # Preserve each nonzero analog level, including deliberately low levels.
    levels = [int(value) or HEADPHONE_INITIAL_RAW for value in channels]
    if levels != [int(value) for value in channels]:
        mixer('sset', 'Headphone', ','.join(map(str, levels)))
    mixer('sset', 'Left Output Mixer PCM', 'on')
    mixer('sset', 'Right Output Mixer PCM', 'on')
    run(['alsactl', 'store', CARD], check=True, timeout=5,
        stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return levels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--if-present', action='store_true',
                        help='Skip hosts without the WM8960 card')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, 'Run with sudo to save the ALSA state.\n')
    if args.if_present and not Path('/proc/asound/' + CARD).exists():
        print('WM8960 absent; headphone/AUX configuration skipped.')
        return
    try:
        levels = configure()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'WM8960 headphone/AUX configuration failed: {exc}\n')
    print(f'WM8960 headphone/AUX enabled and saved (raw levels {levels}).')


if __name__ == '__main__':
    main()
