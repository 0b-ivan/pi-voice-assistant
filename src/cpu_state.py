"""Read-only view of the CPU state that is really in effect, for the PiTFT.

``cpu_modes`` stores what the user *chose*; this module reports what the
kernel and firmware *do*: the active governor, the clock limits and whether
the Pi actually runs above its stock clock. The display shows the result, so
it must never claim an overclock it cannot prove: a line ``arm_freq=1200``
in config.txt is not evidence (the firmware may have ignored it), only a
kernel clock limit above the board's stock clock is. Anything unclear is
reported as 'unknown' instead.

Nothing here writes anywhere; reads are a few small sysfs files plus
``vcgencmd measure_clock arm`` only when an overclock is plausible.
"""
import os
from pathlib import Path
import subprocess

CPUFREQ = Path('/sys/devices/system/cpu/cpufreq/policy0')
MODEL = Path('/proc/device-tree/model')
# Stock maximum per board (kHz). Only boards listed here can show an overclock.
STOCK_KHZ = {'Raspberry Pi Zero 2 W': 1_000_000}
MARGIN_KHZ = 20_000            # clock limits wobble by a step on some kernels

GOVERNOR_PROFILE = {'powersave': 'eco', 'ondemand': 'balanced', 'schedutil': 'balanced',
                    'conservative': 'balanced', 'performance': 'power'}
PROFILES = ('eco', 'balanced', 'power', 'overclock', 'unknown')

# vcgencmd get_throttled, bits that hold *now* (not "since boot").
UNDERVOLT_NOW = 0x1
CAPPED_NOW = 0x2
THROTTLED_NOW = 0x4
SOFT_TEMP_NOW = 0x8
LIMITED_NOW = UNDERVOLT_NOW | CAPPED_NOW | THROTTLED_NOW | SOFT_TEMP_NOW


def _read(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def _khz(path):
    value = _read(path)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 10_000_000 else None


def stock_khz(model_path=MODEL, env=None):
    """The board's stock maximum in kHz, or None when the board is unknown."""
    env = os.environ if env is None else env
    override = env.get('PI_DISPLAY_CPU_STOCK_KHZ', '')
    if override.isdigit() and int(override) > 0:
        return int(override)
    try:
        model = model_path.read_bytes().replace(b'\x00', b'').decode('ascii', 'replace')
    except OSError:
        return None
    for name, khz in STOCK_KHZ.items():
        if name in model:
            return khz
    return None


def arm_clock_khz(run=subprocess.run):
    """``vcgencmd measure_clock arm`` in kHz, None if unavailable."""
    try:
        result = run(['vcgencmd', 'measure_clock', 'arm'], capture_output=True, text=True,
                     timeout=2, check=False)
        hz = int(result.stdout.strip().split('=', 1)[1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
    return hz // 1000 if hz > 0 else None


def read(root=CPUFREQ, stock=None, throttled=None, run=subprocess.run):
    """Raw effective values: governor, cur/max kHz, measured arm clock, flags.

    ``throttled`` is the get_throttled value the caller already sampled (the
    display reads it for its power line), so it is not queried twice.
    """
    governor = _read(root / 'scaling_governor')
    hardware_max = _khz(root / 'cpuinfo_max_freq')
    policy_max = _khz(root / 'scaling_max_freq')
    limit = min(v for v in (hardware_max, policy_max) if v) if hardware_max or policy_max else None
    state = dict(governor=governor, cur_khz=_khz(root / 'scaling_cur_freq'),
                 max_khz=limit, stock_khz=stock, arm_khz=None, throttled=throttled)
    if stock and limit and limit > stock + MARGIN_KHZ:
        state['arm_khz'] = arm_clock_khz(run)    # only worth a subprocess here
    return state


def classify(state):
    """Effective profile for the display.

    Returns dict(profile, base, warning, mhz): ``profile`` one of PROFILES,
    ``base`` the runtime profile of the governor alone (what the menu sets),
    ``warning`` 'undervolt'/'throttled' when the firmware limits the CPU now
    (an overclock is never shown then), ``mhz`` the clock limit in effect.
    """
    governor = state.get('governor')
    profile = base = GOVERNOR_PROFILE.get(governor, 'unknown')
    flags = state.get('throttled')
    warning = None
    if flags is not None:
        if flags & UNDERVOLT_NOW:
            warning = 'undervolt'
        elif flags & (CAPPED_NOW | THROTTLED_NOW | SOFT_TEMP_NOW):
            warning = 'throttled'
    limit, stock = state.get('max_khz'), state.get('stock_khz')
    mhz = limit // 1000 if limit else None
    above = bool(limit and stock and limit > stock + MARGIN_KHZ)
    if above and profile in ('balanced', 'power') and warning is None and flags is not None:
        measured = state.get('arm_khz')
        # Under "performance" the core must really run above stock; a lower
        # measurement means the firmware holds it back, so no gold.
        if not (profile == 'power' and measured is not None and measured <= stock + MARGIN_KHZ):
            profile = 'overclock'
    elif above and flags is None and profile in ('balanced', 'power'):
        # Above stock but the firmware flags are unreadable: can't rule out
        # under-voltage, so neither claim the overclock nor hide it.
        profile = 'unknown'
    return dict(profile=profile, base=base, warning=warning, mhz=mhz)


def effective(throttled=None, root=CPUFREQ, model_path=MODEL, run=subprocess.run):
    """classify(read(...)) for the running Pi."""
    return classify(read(root, stock_khz(model_path), throttled, run))
