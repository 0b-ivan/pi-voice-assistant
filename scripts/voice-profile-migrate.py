#!/usr/bin/env python3
"""Draft migration of v1 voice profiles to v2, into a copy only.

    voice-profile-migrate.py SOURCE_ROOT TARGET_ROOT

SOURCE_ROOT is a memory root (the directory holding voice/, e.g. a copy of
/mnt/proximus-memory/proximus); it is only read. TARGET_ROOT must be a
different directory outside SOURCE_ROOT: the v2 profiles go to
TARGET_ROOT/voice/profiles-v2/, the id assignment to
TARGET_ROOT/voice/profile-ids.json (atomic, reused on the next run, so ids
stay stable). Every migrated profile has status 'legacy': one legacy center,
no known model, never compared automatically until the voice part is
recorded again (concept stimm-einlernen v2, section 6).

The report on stdout holds counts, ids and v1 file names (slugs) only.
Concept rule: no automatic migration of production data; run this on a copy
and look at the result first.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import voice_profile  # noqa: E402


def v1_profiles(root):
    """[(file name, data)] of the v1 files, first version included."""
    voice = root / 'voice'
    paths = ([voice / 'voiceprint.json'] if (voice / 'voiceprint.json').is_file() else [])
    paths += sorted((voice / 'profiles').glob('*.json'))
    found = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            found.append((path.name, None))
            continue
        found.append((path.name, data if isinstance(data, dict) else None))
    return found


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8') as out:
        json.dump(data, out, indent=1, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def migrate(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target == source or source in target.parents or target in source.parents:
        raise SystemExit('TARGET_ROOT must be a separate copy, outside SOURCE_ROOT')
    ids_path = target / 'voice' / 'profile-ids.json'
    try:
        ids = json.loads(ids_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        ids = {}
    store = voice_profile.ProfileStore(target / 'voice' / 'profiles-v2')
    report = dict(migrated=[], unreadable=[], slug_collisions=[])
    names = []
    for filename, data in v1_profiles(source):
        if data is None or not isinstance(data.get('print'), str):
            report['unreadable'].append(filename)
            continue
        names.append(str(data.get('name') or 'Bediener'))
        data = dict(data, profile_id=data.get('profile_id') or ids.get(filename))
        try:
            profile = voice_profile.migrate_legacy(data)
        except voice_profile.ProfileError:
            report['unreadable'].append(filename)
            continue
        ids[filename] = profile['profile_id']
        write_atomic(ids_path, ids)              # before the profile: the id is fixed first
        saved = store.save(profile)
        report['migrated'].append(dict(file=filename, profile_id=saved['profile_id'],
                                       status=saved['status'], dim=saved['dim'],
                                       revision=saved['revision']))
    report['slug_collisions'] = sorted(voice_profile.slug_collisions(names))
    return report


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    print(json.dumps(migrate(argv[1], argv[2]), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
