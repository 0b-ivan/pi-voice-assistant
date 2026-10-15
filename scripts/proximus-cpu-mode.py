#!/usr/bin/env python3
"""Root-owned runtime governor selector for the Proximus energy menu.

Never changes boot clocks, voltages, or battery registers. A governor uses
only CPU frequencies the current kernel/firmware already permits.
"""
from pathlib import Path
import sys

ROOT = Path('/sys/devices/system/cpu/cpufreq')
MODES = {'eco': 'powersave', 'balanced': 'ondemand', 'power': 'performance'}


def apply(mode, root=ROOT):
    if mode not in MODES:
        raise ValueError('expected eco, balanced or power')
    desired = MODES[mode]
    policies = sorted(root.glob('policy[0-9]*'))
    if not policies:
        raise RuntimeError('no cpufreq policies found')
    previous = {}
    for policy in policies:
        active = policy / 'scaling_governor'
        available = (policy / 'scaling_available_governors').read_text().split()
        if desired not in available:
            raise RuntimeError(f'{desired} unsupported on {policy.name}')
        previous[policy] = active.read_text().strip()
    changed = []
    try:
        for policy, old in previous.items():
            if old != desired:
                (policy / 'scaling_governor').write_text(desired + '\n')
                changed.append(policy)
    except OSError:
        for policy in reversed(changed):
            try:
                (policy / 'scaling_governor').write_text(previous[policy] + '\n')
            except OSError:
                pass
        raise
    return {policy.name: previous[policy] for policy in policies}


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2:
            raise ValueError('usage: proximus-cpu-mode.py eco|balanced|power')
        apply(sys.argv[1])
    except (OSError, RuntimeError, ValueError) as exc:
        print(f'Proximus CPU mode: {exc}', file=sys.stderr)
        sys.exit(1)
