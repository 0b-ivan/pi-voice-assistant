#!/usr/bin/env python3
"""Review measured candidate/baseline reports without changing active models."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from nightwatch import MAX_REPORT, atomic_json
from nightwatch_training import assess_candidate


def read(path):
    if path.stat().st_size > MAX_REPORT:
        raise ValueError('report too large')
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError('report must be an object')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = assess_candidate(read(args.baseline), read(args.candidate), read(args.manifest))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.error(str(error))
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
