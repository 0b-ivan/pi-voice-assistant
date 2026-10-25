#!/usr/bin/env python3
"""Bounded full-system boot acceptance; markers must come from inside the ARM guest."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
log = root / 'build/boot.log'
with log.open('w') as stream:
    try:
        result = subprocess.run([str(root / 'image/run-qemu.sh'), 'smoke'],
                                stdout=stream, stderr=subprocess.STDOUT, timeout=240)
    except subprocess.TimeoutExpired:
        print(log.read_text()[-16000:])
        sys.exit('ARM boot timed out; see build/boot.log')
text = log.read_text()
print(text[-16000:])
if result.returncode or 'PROXIMUS_GUI_OK' not in text or 'PROXIMUS_BOOT_OK' not in text:
    sys.exit('ARM boot acceptance failed')
