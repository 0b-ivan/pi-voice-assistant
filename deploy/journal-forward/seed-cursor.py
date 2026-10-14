#!/usr/bin/python3
"""Begin with recent entries after boot, keep the upload cursor in RAM.

The native uploader resumes this cursor on restarts. After a reboot only the
last ~80 entries precede the live stream, rather than a host's entire archive.
"""
import json
from pathlib import Path
import subprocess


def seed(state=Path('/run/proximus-journal-upload/state')):
    if state.exists():
        return
    result = subprocess.run(['journalctl', '-n', '80', '-o', 'json', '--no-pager',
                             '--output-fields=__CURSOR'], check=True, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        cursor = json.loads(line).get('__CURSOR')
        if isinstance(cursor, str) and cursor and '\n' not in cursor:
            state.write_text(f'LAST_CURSOR={cursor}\n')
            return


if __name__ == '__main__':
    seed()
