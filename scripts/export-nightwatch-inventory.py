#!/usr/bin/env python3
"""Read-only inventory of an explicitly selected extra infrastructure machine."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from nightwatch import atomic_json
from security_audit import inventory, sanitize_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--system', required=True, help='e.g. proxmox or cloudflared')
    parser.add_argument('--output', type=Path, help='JSON file; default: stdout')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', args.system) or args.system in ('pi', 'server'):
        parser.error('choose an extra system name, other than pi/server')
    data = inventory(args.system)
    sanitize_inventory(data, args.system)
    if args.output:
        atomic_json(args.output, data)
    else:
        print(json.dumps(data, ensure_ascii=False))


if __name__ == '__main__':
    main()
