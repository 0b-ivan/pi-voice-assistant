"""Voice profiles v2: stable IDs, three centers, calibration, migration.

Concept stimm-einlernen v2, section 6 (Etappe 3). Pure functions plus a small
store; nothing here touches the live profiles in voice/profiles/ (those stay
v1 until a migration is run on purpose, see scripts/voice-profile-migrate.py).

Profile (dict, JSON on the stick under voice/profiles-v2/<profile_id>.json):

    schema_version 2, profile_id (uuid hex, never derived from name or
    vector), name (display only), model {name, sha256}, preprocessing
    (voice_quality.VERSION), dim, codec 'f32le-b64', status, revision, at,
    centers {near, far, quiet} (or legacy_center for a migrated v1 print),
    anchors [{condition, vector}] <= 18, checks [...] <= 6 (held out, never
    in a center), extras [...] <= 24, calibration {tau, epsilon, delta, ...},
    legacy {print, count, at} (the v1 data, kept unchanged).

Vectors are float32 little endian in base64 and are normalized, checked for
NaN, zero norm and dimension whenever they are read. Calibration works on
the stored and reloaded vectors, so it sees exactly what a later comparison
sees.

Comparison: s_p(x) = max cosine over the profile's centers. decide() names a
person only for a calibrated (or validated) profile whose s_p reaches its own
tau, with at least delta to the second best profile; it never falls back to
the runner-up. Scores and thresholds are cosines, not probabilities.
"""
import base64
import json
import math
import os
import struct
import time
import uuid
from pathlib import Path

SCHEMA = 2
CODEC = 'f32le-b64'
CONDITIONS = ('near', 'far', 'quiet')
STATUSES = ('legacy', 'provisional', 'calibrated', 'validated')
TRUSTED = ('calibrated', 'validated')
MAX_ANCHORS, MAX_CHECKS, MAX_EXTRAS = 18, 6, 24
DIM_MIN, DIM_MAX = 16, 1024
NAME_MAX = 40
EPSILON = 0.03          # start value: tau above the highest impostor score
DELTA = 0.05            # start value: lead over the second best profile
GENUINE_SHARE = 0.95    # LOO scores >= tau; with 18 clips that means 18/18
IMPOSTOR_MIN, IMPOSTOR_PEOPLE_MIN = 20, 5
CAMPPLUS = dict(name='campplus-voxceleb-16k',
                sha256='357a834f702b80161e5b981182c038e18553c1f2ca752ed6cec2052365d4129b')

# decide() outcomes (speaker.decide uses the same words for v1).
MATCHED, UNKNOWN, AMBIGUOUS, UNAVAILABLE, UNVERIFIED = (
    'matched', 'unknown', 'ambiguous', 'unavailable', 'unverified')


class ProfileError(ValueError):
    pass


# --- vectors -------------------------------------------------------------

def normalize(vector):
    """Unit vector, or None for empty, non-finite or zero-norm input."""
    if not vector or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
        return None
    norm = math.sqrt(sum(v * v for v in vector))
    if not norm or not math.isfinite(norm):
        return None
    return [v / norm for v in vector]


def encode(vector):
    unit = normalize(vector)
    if unit is None:
        raise ProfileError('vector is empty, not finite or zero')
    return base64.b64encode(struct.pack(f'<{len(unit)}f', *unit)).decode('ascii')


def decode(text, dim=None):
    """base64 float32 LE -> unit vector; ProfileError if anything is off."""
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError):
        raise ProfileError('vector is not base64') from None
    if len(raw) % 4 or not DIM_MIN <= len(raw) // 4 <= DIM_MAX:
        raise ProfileError('vector has an invalid size')
    if dim is not None and len(raw) // 4 != dim:
        raise ProfileError(f'vector has {len(raw) // 4} values, profile says {dim}')
    unit = normalize(list(struct.unpack(f'<{len(raw) // 4}f', raw)))
    if unit is None:
        raise ProfileError('vector is not finite or zero')
    return unit


def stored(vector):
    """The vector as a comparison will see it: encoded and read back."""
    return decode(encode(vector))


def cosine(a, b):
    if a is None or b is None or len(a) != len(b):
        return None
    return sum(x * y for x, y in zip(a, b))


def mean(vectors):
    vectors = [v for v in vectors if v]
    if not vectors:
        return None
    dim = len(vectors[0])
    if any(len(v) != dim for v in vectors):
        raise ProfileError('vectors of different dimensions')
    return normalize([sum(v[i] for v in vectors) / len(vectors) for i in range(dim)])


# --- centers and scores ----------------------------------------------------

def centers_of(anchors):
    """{condition: unit mean} of anchors [{condition, vector}] (equal weights)."""
    found = {}
    for condition in CONDITIONS:
        center = mean([a['vector'] for a in anchors if a['condition'] == condition])
        if center is not None:
            found[condition] = stored(center)
    return found


def score(vector, centers):
    """s_p(x): the best cosine over the centers, or None."""
    values = [cosine(vector, c) for c in centers.values()]
    values = [v for v in values if v is not None]
    return max(values) if values else None


def loo_scores(anchors):
    """Leave-one-out genuine scores: each anchor against centers rebuilt
    from all *other* anchors, with the full comparison (max over centers)."""
    scores = []
    for index, anchor in enumerate(anchors):
        rest = anchors[:index] + anchors[index + 1:]
        scores.append(dict(condition=anchor['condition'],
                           score=score(anchor['vector'], centers_of(rest))))
    return scores


# --- calibration -------------------------------------------------------------

def calibrate(anchors, checks, impostors, epsilon=EPSILON, delta=DELTA):
    """Personal threshold from LOO genuine scores, impostor scores and held-out
    checks (concept, section 6). ``anchors``/``checks``: [{condition, vector}],
    ``impostors``: [{source, vector}] of other real people (and, separately
    labelled, attacks). Returns {status, reason, tau, ...numbers}; status is
    'calibrated' only when every rule holds, else 'provisional'."""
    anchors = [dict(a, vector=stored(a['vector'])) for a in anchors]
    checks = [dict(c, vector=stored(c['vector'])) for c in checks]
    centers = centers_of(anchors)
    genuine = loo_scores(anchors)
    impostor_scores = [score(stored(i['vector']), centers) for i in impostors]
    sources = {i.get('source') for i in impostors if i.get('source')}
    result = dict(status='provisional', reason=None, tau=None, epsilon=epsilon, delta=delta,
                  genuine=[round(g['score'], 4) for g in genuine if g['score'] is not None],
                  impostor_count=len(impostors), impostor_sources=len(sources),
                  impostor_max=None, checks=[], loo_ok=False, checks_ok=False)
    if any(g['score'] is None for g in genuine) or not anchors:
        result['reason'] = 'anchors_missing'
        return result
    if len(impostors) < IMPOSTOR_MIN or len(sources) < IMPOSTOR_PEOPLE_MIN:
        result['reason'] = 'too_few_impostors'
        return result
    worst = max(impostor_scores)
    tau = worst + epsilon
    result.update(impostor_max=round(worst, 4), tau=round(tau, 4))
    if tau > 1.0:
        result['reason'] = 'impostor_too_close'
        return result
    needed = math.ceil(GENUINE_SHARE * len(genuine))
    passing = sum(g['score'] >= tau for g in genuine)
    result['loo_ok'] = passing >= needed
    if not result['loo_ok']:
        result['reason'] = 'not_separable'
        return result
    check_scores = [score(c['vector'], centers) for c in checks]
    result['checks'] = [round(s, 4) for s in check_scores]
    result['checks_ok'] = len(checks) == MAX_CHECKS and all(s >= tau for s in check_scores)
    if not result['checks_ok']:
        result['reason'] = 'checks_failed' if len(checks) == MAX_CHECKS else 'checks_missing'
        return result
    result.update(status='calibrated', reason=None)
    return result


# --- profiles ----------------------------------------------------------------

def new_profile(name, anchors, checks=(), model=None, dim=None, now=None):
    """A provisional v2 profile with a fresh stable id; calibrate() decides more."""
    anchors, checks = list(anchors), list(checks)
    if len(anchors) > MAX_ANCHORS or len(checks) > MAX_CHECKS:
        raise ProfileError('too many anchors or checks')
    dim = dim or (len(anchors[0]['vector']) if anchors else None)
    profile = dict(schema_version=SCHEMA, profile_id=uuid.uuid4().hex,
                   name=_clean_name(name), model=model, preprocessing=_preprocessing(),
                   dim=dim, codec=CODEC, status='provisional', revision=0,
                   at=int(now if now is not None else time.time()),
                   anchors=[_item(a, dim) for a in anchors],
                   checks=[_item(c, dim) for c in checks], extras=[], calibration=None)
    profile['centers'] = {k: encode(v) for k, v in
                          centers_of([dict(a, vector=decode(a['vector'], dim))
                                      for a in profile['anchors']]).items()}
    return profile


def apply_calibration(profile, result):
    profile = dict(profile, calibration=result)
    profile['status'] = 'calibrated' if result.get('status') == 'calibrated' else 'provisional'
    return profile


def _clean_name(name):
    return ' '.join(str(name or '').split())[:NAME_MAX] or 'Bediener'


def _preprocessing():
    try:
        import voice_quality
        return voice_quality.VERSION
    except ImportError:
        return None


def _item(item, dim):
    condition = item.get('condition')
    if condition not in CONDITIONS:
        raise ProfileError(f'unknown condition {condition!r}')
    vector = item['vector']
    text = vector if isinstance(vector, str) else encode(vector)
    decode(text, dim)
    return dict(condition=condition, vector=text)


def validate(profile):
    """A loaded v2 profile, checked: ProfileError on anything unexpected."""
    if not isinstance(profile, dict) or profile.get('schema_version') != SCHEMA:
        raise ProfileError('not a v2 profile')
    ident = profile.get('profile_id')
    if not isinstance(ident, str) or len(ident) != 32 or not all(c in '0123456789abcdef'
                                                                  for c in ident):
        raise ProfileError('profile_id missing or malformed')
    if profile.get('codec') != CODEC or profile.get('status') not in STATUSES:
        raise ProfileError('unknown codec or status')
    dim = profile.get('dim')
    if not isinstance(dim, int) or not DIM_MIN <= dim <= DIM_MAX:
        raise ProfileError('dimension missing or out of range')
    limits = dict(anchors=MAX_ANCHORS, checks=MAX_CHECKS, extras=MAX_EXTRAS)
    for key, limit in limits.items():
        items = profile.get(key) or []
        if not isinstance(items, list) or len(items) > limit:
            raise ProfileError(f'{key}: more than {limit}')
        for item in items:
            _item(item, dim)
    centers = profile.get('centers') or {}
    if not isinstance(centers, dict) or set(centers) - set(CONDITIONS):
        raise ProfileError('unknown center')
    for text in centers.values():
        decode(text, dim)
    if profile.get('legacy_center') is not None:
        decode(profile['legacy_center'], dim)
    if not centers and profile.get('legacy_center') is None:
        raise ProfileError('no center')
    if profile['status'] == 'legacy' and centers:
        raise ProfileError('a legacy profile has only its legacy center')
    return profile


def comparison_centers(profile):
    """{name: unit vector} the profile is compared with."""
    dim = profile['dim']
    found = {k: decode(v, dim) for k, v in (profile.get('centers') or {}).items()}
    if not found and profile.get('legacy_center') is not None:
        found['legacy'] = decode(profile['legacy_center'], dim)
    return found


def compatible(profile, model, dim):
    """Same embedding model and dimension: only then may vectors be compared.
    A profile without a known model (legacy) is never compatible."""
    return (profile.get('dim') == dim and isinstance(profile.get('model'), dict)
            and isinstance(model, dict) and profile['model'].get('sha256') == model.get('sha256')
            and profile.get('preprocessing') == _preprocessing())


def decide(vector, profiles, model, delta=DELTA):
    """Structured v2 identity: {status, profile_id, best, second, tau}.

    Incompatible profiles are skipped. Only a trusted (calibrated/validated)
    best profile reaching its own tau with delta to the second best gives
    MATCHED; an untrusted best profile gives UNVERIFIED, never a name."""
    vector = normalize(vector)
    result = dict(status=UNAVAILABLE, profile_id=None, best=None, second=None, tau=None)
    if vector is None:
        return result
    scored = []
    for profile in profiles or []:
        if not compatible(profile, model, len(vector)):
            continue
        value = score(vector, comparison_centers(profile))
        if value is not None:
            scored.append((value, profile))
    if not scored:
        return result
    scored.sort(key=lambda item: item[0], reverse=True)
    best, profile = scored[0]
    second = scored[1][0] if len(scored) > 1 else None
    tau = (profile.get('calibration') or {}).get('tau')
    result.update(best=best, second=second, tau=tau)
    if profile.get('status') not in TRUSTED or not isinstance(tau, (int, float)):
        return dict(result, status=UNVERIFIED)
    if best < tau:
        return dict(result, status=UNKNOWN)
    if second is not None and best - second < delta:
        return dict(result, status=AMBIGUOUS)
    return dict(result, status=MATCHED, profile_id=profile['profile_id'])


def cross_check(profiles, samples, model, delta=DELTA):
    """The whole decision path over several people: samples [{profile_id,
    vector}] of known origin (None: a stranger). Returns the numbers of right
    decisions, wrong names, refusals of a known person and stranger matches;
    a new person or profile revision must leave wrong and stranger at 0."""
    counts = dict(right=0, wrong=0, refused=0, stranger_matched=0)
    for sample in samples:
        result = decide(sample['vector'], profiles, model, delta)
        truth = sample.get('profile_id')
        if result['status'] != MATCHED:
            counts['refused' if truth else 'right'] += 1
        elif truth is None:
            counts['stranger_matched'] += 1
        elif result['profile_id'] == truth:
            counts['right'] += 1
        else:
            counts['wrong'] += 1
    return counts


# --- transport (Pi -> server, request body, RAM session) ---------------------

CORE_PROFILES_MAX = 5
CORE_BYTES_MAX = 2 * 1024 * 1024
_CORE_FIELDS = ('schema_version', 'profile_id', 'name', 'model', 'preprocessing', 'dim',
                'codec', 'status', 'revision', 'centers')


def voice_core(profiles):
    """What the server needs for everyday comparison: trusted profiles only,
    centers and threshold, no anchors, checks or raw samples."""
    compact = []
    for profile in profiles:
        if profile.get('status') not in TRUSTED:
            continue
        tau = (profile.get('calibration') or {}).get('tau')
        if not isinstance(tau, (int, float)):
            continue
        item = {k: profile[k] for k in _CORE_FIELDS if k in profile}
        item['calibration'] = dict(tau=tau)
        compact.append(item)
    return dict(profiles=compact[:CORE_PROFILES_MAX]) if compact else None


def read_core(data):
    """Server side: the validated profiles of a voice core, ProfileError if
    anything is off (size, count, schema, a single bad vector)."""
    if not isinstance(data, dict) or not isinstance(data.get('profiles'), list):
        raise ProfileError('expected {"profiles": [...]}')
    if not 0 < len(data['profiles']) <= CORE_PROFILES_MAX:
        raise ProfileError(f'1 to {CORE_PROFILES_MAX} profiles')
    profiles = []
    for item in data['profiles']:
        if not isinstance(item, dict) or set(item) - set(_CORE_FIELDS) - {'calibration'}:
            raise ProfileError('unexpected fields in a core profile')
        profile = validate(dict(item))
        tau = (profile.get('calibration') or {}).get('tau')
        if (profile['status'] not in TRUSTED or not isinstance(tau, (int, float))
                or not -1.0 <= tau <= 1.0):
            raise ProfileError('core profiles must be calibrated, with a threshold')
        profiles.append(profile)
    return profiles


# --- migration ---------------------------------------------------------------

def slug(name):
    import re
    return re.sub(r'[^a-z0-9]+', '-', str(name).lower()).strip('-') or 'bediener'


def slug_collisions(names):
    """{slug: [names]} for display names that share one v1 file name."""
    groups = {}
    for name in names:
        groups.setdefault(slug(name), []).append(name)
    return {key: sorted(set(value)) for key, value in groups.items() if len(set(value)) > 1}


def migrate_legacy(old):
    """A v1 profile {name, print (int8 base64), count, at, profile_id?} as a v2
    'legacy' profile: the print becomes the one legacy center (never three
    copies), the v1 data is kept unchanged, an existing id is kept and only a
    missing one is created. The model is unknown, so the profile is never
    compatible for automatic comparison until the voice part is re-recorded."""
    import speaker
    vector = speaker.decode(old.get('print') or '')
    if vector is None:
        raise ProfileError('v1 print unreadable')
    ident = old.get('profile_id')
    if not (isinstance(ident, str) and len(ident) == 32):
        ident = uuid.uuid4().hex
    legacy = {k: old[k] for k in ('print', 'count', 'at', 'passphrase', 'last_score') if k in old}
    return dict(schema_version=SCHEMA, profile_id=ident, name=_clean_name(old.get('name')),
                model=None, preprocessing=None, dim=len(vector), codec=CODEC, status='legacy',
                revision=0, at=int(old.get('at') or 0), anchors=[], checks=[], extras=[],
                centers={}, legacy_center=encode(vector), calibration=None, legacy=legacy)


# --- store -------------------------------------------------------------------

class ProfileStore:
    """v2 profiles in one directory, one file per profile_id; every write is
    atomic (temporary file, fsync, rename) and the previous revision is kept
    as <id>.prev.json, read when the current file is damaged."""

    def __init__(self, folder, clock=time.time):
        self.folder, self.clock = Path(folder), clock

    def _path(self, ident, previous=False):
        return self.folder / (f'{ident}.prev.json' if previous else f'{ident}.json')

    def load(self, ident):
        for path in (self._path(ident), self._path(ident, previous=True)):
            try:
                profile = validate(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
            if profile['profile_id'] == ident:
                return profile
        return None

    def all(self):
        idents = sorted({p.name.split('.')[0] for p in self.folder.glob('*.json')})
        return [p for p in (self.load(i) for i in idents) if p is not None]

    def save(self, profile):
        profile = validate(dict(profile))
        current = self.load(profile['profile_id'])
        profile['revision'] = (current['revision'] + 1) if current else 0
        profile['at'] = int(self.clock())
        validate(profile)
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(profile['profile_id'])
        temporary = path.with_suffix('.tmp')
        with open(temporary, 'w', encoding='utf-8') as out:
            json.dump(profile, out)
            out.flush()
            os.fsync(out.fileno())
        if path.is_file():
            path.replace(self._path(profile['profile_id'], previous=True))
        temporary.replace(path)
        return profile

    def rename(self, ident, name):
        """A new display name; the id stays."""
        profile = self.load(ident)
        if profile is None:
            return None
        return self.save(dict(profile, name=_clean_name(name)))

    def by_name(self, name):
        """Every profile with this display name (names need not be unique)."""
        return [p for p in self.all() if p['name'] == _clean_name(name)]

    def delete(self, ident):
        """The profile with all its samples and its previous revision."""
        found = False
        for path in (self._path(ident), self._path(ident, previous=True),
                     self._path(ident).with_suffix('.tmp')):
            if path.exists():
                path.unlink()
                found = True
        return found
