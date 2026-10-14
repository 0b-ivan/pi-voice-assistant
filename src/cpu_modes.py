"""Three safe runtime CPU governor presets for Proximus (no overclock).

The PiTFT and voice paths call systemd over D-Bus with narrowly scoped Polkit
authorization; they never write privileged sysfs nor the boot configuration.
"""
import subprocess

MODES = ('eco', 'balanced', 'power')


def set_mode(mode, run=subprocess.run):
    if mode not in MODES:
        raise ValueError('unknown CPU mode')
    process = run(['/usr/bin/systemctl', 'start',
                   f'proximus-cpu-mode@{mode}.service'],
                  check=False, timeout=12, capture_output=True, text=True)
    return process.returncode == 0
