#!/usr/bin/env python3
"""Bounded full-system boot acceptance; markers must come from inside the ARM guest."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
mode = sys.argv[1] if len(sys.argv) > 1 else 'smoke'
if mode not in ('smoke', 'xorg-smoke'):
    sys.exit('Usage: boot-test.py [smoke|xorg-smoke]')
log = root / ('build/xorg-boot.log' if mode == 'xorg-smoke' else 'build/boot.log')
with log.open('w') as stream:
    try:
        result = subprocess.run([str(root / 'image/run-qemu.sh'), mode],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    except subprocess.TimeoutExpired:
        print(log.read_text()[-16000:])
        sys.exit('ARM boot timed out; see build/boot.log')
text = log.read_text()
print(text[-16000:])
marker = 'PROXIMUS_XORG_BOOT_OK' if mode == 'xorg-smoke' else 'PROXIMUS_BOOT_OK'
if result.returncode or 'PROXIMUS_GUI_OK' not in text or marker not in text:
    sys.exit('ARM boot acceptance failed')
