#!/usr/bin/env python3
"""Optional, operator-only boot overclock for Raspberry Pi Zero 2 W.

No automatic reboot; never exposed to the voice/UI systemd or Polkit rules.
Preview is read-only. Enable requires explicit --accept-risk on local shell.
The managed block is board-scoped [pi02] and can be removed without touching
other config.txt sections. A dated config backup is created first.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import sys

CONFIG = Path('/boot/firmware/config.txt')
MODEL = Path('/proc/device-tree/model')
BEGIN = '# BEGIN PROXIMUS ZERO 2 W OVERCLOCK'
END = '# END PROXIMUS ZERO 2 W OVERCLOCK'
MANAGED = (
    BEGIN + '\n[pi02]\n'
    '# Experimental 1.2 GHz / +50mV max; requires manual stability tests.\n'
    'arm_freq=1200\n'
    'over_voltage=2\n'
    'force_turbo=0\n'
    '[all]\n' + END + '\n'
)
DANGEROUS = re.compile(r'^\s*(?:arm_freq|arm_boost|force_turbo|over_voltage(?:_\w+)?)\s*=', re.M)


def verify_model(model_path=MODEL):
    try:
        model = model_path.read_bytes().replace(b'\x00', b'').decode('ascii')
    except (OSError, UnicodeError) as exc:
        raise RuntimeError('cannot verify target Pi board') from exc
    if 'Raspberry Pi Zero 2 W' not in model:
        raise RuntimeError('only Raspberry Pi Zero 2 W is supported')


def current(config=CONFIG):
    return config.read_text(encoding='utf-8')


def prepare_enable(existing):
    if BEGIN in existing or END in existing:
        raise ValueError('Proximus overclock already present; disable first')
    if DANGEROUS.search(existing):
        raise ValueError('existing clock/voltage overrides: manual review required')
    # No silent editing if another config is included.
    if re.search(r'^\s*include\s+\S+', existing, re.M):
        raise ValueError('included configs require manual review')
    return existing.rstrip('\n') + '\n\n' + MANAGED


def prepare_disable(existing):
    if existing.count(BEGIN) != 1 or existing.count(END) != 1:
        raise ValueError('cannot identify exactly one managed overclock block')
    start = existing.index(BEGIN)
    end = existing.index(END, start) + len(END)
    block = existing[start:end] + '\n'
    if block != MANAGED:
        raise ValueError('managed block changed; manual review required')
    return existing[:start].rstrip('\n') + '\n' + existing[end:].lstrip('\n')


def write_preserving_backup(config, modified):
    backup = config.with_name(config.name + '.before-proximus-' +
                              datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    if backup.exists():
        raise RuntimeError('backup filename already exists')
    shutil.copy2(config, backup)
    tmp = config.with_name(config.name + '.proximus-tmp')
    if tmp.exists():
        raise RuntimeError('temporary config file already exists')
    try:
        tmp.write_text(modified, encoding='utf-8')
        shutil.copymode(config, tmp)
        tmp.replace(config)
    finally:
        tmp.unlink(missing_ok=True)
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['preview', 'enable', 'disable'])
    parser.add_argument('--accept-risk', action='store_true',
                        help='explicitly allow config.txt changes')
    args = parser.parse_args(argv)
    verify_model()
    original = current()
    if args.action == 'preview':
        print(MANAGED)
        print('Read-only preview. No current CPU/voltage changes.')
        return 0
    if os.geteuid() != 0 or not args.accept_risk:
        parser.error('root and --accept-risk required for writes')
    modified = (prepare_enable(original) if args.action == 'enable'
                else prepare_disable(original))
    backup = write_preserving_backup(CONFIG, modified)
    print(f'Backup: {backup}')
    print('config.txt updated. No reboot performed. Physical recovery required if it cannot boot.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Proximus overclock: {exc}', file=sys.stderr)
        sys.exit(1)
