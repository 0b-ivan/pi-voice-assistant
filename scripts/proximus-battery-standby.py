#!/usr/bin/env python3
"""Root-owned, systemd-only Pi Zero 2 W battery standby policy.

Only the installed root-owned copy in /usr/local/libexec is executed. This
helper never powers off the PiSugar or changes charging/RTC settings.
systemd ExecStop AND ExecStopPost restore original radio/governor state.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CPU_ROOT = Path('/sys/devices/system/cpu/cpufreq')
RFKILL_ROOT = Path('/sys/class/rfkill')
LED_ROOT = Path('/sys/class/leds')
ACT_LEDS = ('ACT', 'led0')      # the green activity LED (name differs by kernel)
TRIGGER = re.compile(r'^[\w-]+$')
STATE = Path('/run/proximus-battery-standby.json')
PISUGAR = 0x57
RFKILL = '/usr/sbin/rfkill'


def battery_plugged():
    """Return None if source cannot be verified. Never guess battery power."""
    try:
        import smbus
        bus = smbus.SMBus(1)
        try:
            return bool(bus.read_byte_data(PISUGAR, 0x02) & 0x80)
        finally:
            bus.close()
    except (ImportError, OSError):
        return None


def command(*argv):
    return subprocess.run(argv, check=True, timeout=5, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def radio_device(kind):
    """Avoid changing multiple adapters without a per-adapter snapshot."""
    radios = []
    for path in sorted(RFKILL_ROOT.glob('rfkill*')):
        try:
            if (path / 'type').read_text().strip() == kind:
                radios.append(path)
        except OSError:
            continue
    return radios[0] if len(radios) == 1 else None


def act_led():
    """The activity LED's sysfs folder, or None."""
    for name in ACT_LEDS:
        path = LED_ROOT / name
        if (path / 'trigger').is_file():
            return path
    return None


def active_trigger(path):
    """The [selected] word of an LED's trigger file."""
    match = re.search(r'\[([\w-]+)\]', (path / 'trigger').read_text())
    return match.group(1) if match else None


def save_state(data):
    STATE.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    try:
        with os.fdopen(os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600),
                       'w', encoding='utf-8') as output:
            json.dump(data, output)
            output.flush()
            os.fsync(output.fileno())
        tmp.replace(STATE)
    finally:
        tmp.unlink(missing_ok=True)


def restore():
    if not STATE.is_file():
        return
    state = json.loads(STATE.read_text(encoding='utf-8'))
    errors = []
    # Restore network first; critical warnings may need remote connectivity.
    for kind in ('wlan', 'bluetooth'):
        if state.get('radios', {}).get(kind) == 'unblocked':
            try:
                command(RFKILL, 'unblock', kind)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f'{kind}: {exc}')
    for name, trigger in state.get('led', {}).items():
        if name not in ACT_LEDS or not TRIGGER.match(str(trigger)):
            errors.append(f'invalid LED state {name!r}')
            continue
        try:
            (LED_ROOT / name / 'trigger').write_text(trigger + '\n')
        except OSError as exc:
            errors.append(f'{name}: {exc}')
    for policy_name, governor in state.get('cpu', {}).items():
        if not policy_name.startswith('policy') or not policy_name[6:].isdigit():
            errors.append(f'invalid policy name {policy_name!r}')
            continue
        if not governor.isidentifier():
            errors.append(f'invalid governor {governor!r}')
            continue
        try:
            (CPU_ROOT / policy_name / 'scaling_governor').write_text(governor + '\n')
        except OSError as exc:
            errors.append(f'{policy_name}: {exc}')
    if errors:
        raise RuntimeError('; '.join(errors))
    STATE.unlink(missing_ok=True)


def enter():
    # If a prior ExecStop failed, restore before touching anything else.
    restore()
    source = battery_plugged()
    if source is not False:
        print('Battery standby skipped: source is mains or unknown')
        return
    modes = {name: os.environ.get('PROXIMUS_SAVE_' + name.upper(), 'keep').lower()
             for name in ('wlan', 'bluetooth')}
    cpu_mode = os.environ.get('PROXIMUS_SAVE_CPU', 'powersave').lower()
    led_mode = os.environ.get('PROXIMUS_SAVE_LED', 'off').lower()
    if led_mode not in ('keep', 'off'):
        raise ValueError('PROXIMUS_SAVE_LED must be keep or off')
    if any(value not in ('keep', 'off') for value in modes.values()):
        raise ValueError('PROXIMUS_SAVE_WLAN/BLUETOOTH must be keep or off')
    if cpu_mode not in ('keep', 'powersave'):
        raise ValueError('PROXIMUS_SAVE_CPU must be keep or powersave')
    if modes['wlan'] == 'off':
        # Provisioning owns wlan0 during setup AP; never switch it off.
        check = subprocess.run(['/usr/bin/systemctl', 'is-active', '--quiet',
                                'proximus-setup.service'], check=False, timeout=5)
        if check.returncode == 0:
            modes['wlan'] = 'keep'
            print('WLAN kept: provisioning AP active')

    state = {'cpu': {}, 'radios': {}, 'led': {}}
    led = act_led() if led_mode == 'off' else None
    if led is not None:
        try:
            trigger = active_trigger(led)
        except OSError:
            trigger = None
        if trigger and trigger != 'none':
            state['led'][led.name] = trigger
    for policy in sorted(CPU_ROOT.glob('policy*')):
        try:
            governor = (policy / 'scaling_governor').read_text().strip()
            available = (policy / 'scaling_available_governors').read_text().split()
        except OSError:
            continue
        if cpu_mode == 'powersave' and 'powersave' in available and governor != 'powersave':
            state['cpu'][policy.name] = governor
    for kind, mode in modes.items():
        if mode != 'off':
            continue
        path = radio_device(kind)
        if path is None:
            print(f'{kind}: no unique radio, keeping current setting')
            continue
        try:
            soft = (path / 'soft').read_text().strip()
        except OSError:
            continue
        if soft == '0':
            state['radios'][kind] = 'unblocked'

    if not state['cpu'] and not state['radios'] and not state['led']:
        return
    # Save before ANY modification. ExecStopPost can recover if ExecStart dies.
    save_state(state)
    try:
        for name in state['cpu']:
            (CPU_ROOT / name / 'scaling_governor').write_text('powersave\n')
        for name in state['led']:        # dark: no SD-card blinking either
            (LED_ROOT / name / 'trigger').write_text('none\n')
            (LED_ROOT / name / 'brightness').write_text('0\n')
        # WLAN last: preserve remote access if an earlier action fails.
        for kind in ('bluetooth', 'wlan'):
            if kind in state['radios']:
                command(RFKILL, 'block', kind)
    except BaseException:
        try:
            restore()
        finally:
            raise
    print('Battery standby applied: ' + ', '.join(
        list(state['cpu']) + list(state['radios']) + list(state['led'])))


if __name__ == '__main__':
    try:
        if len(sys.argv) != 2 or sys.argv[1] not in ('enter', 'leave'):
            raise ValueError('usage: proximus-battery-standby.py enter|leave')
        if sys.argv[1] == 'enter':
            enter()
        else:
            restore()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f'Battery standby: {exc}', file=sys.stderr)
        sys.exit(1)
