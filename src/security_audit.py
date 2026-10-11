"""Read-only installed inventory and known-vulnerability matching; no AI or upgrades."""
import codecs
import datetime
import gzip
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from nightwatch import atomic_json, clean, finding, source_url

DEBIAN = 'https://security-tracker.debian.org/tracker/data/json'
OSV = 'https://api.osv.dev/v1/querybatch'
MAX_PACKAGES = 3000
PUBLIC_HOSTS = {'security-tracker.debian.org', 'api.osv.dev', 'www.raspberrypi.com',
                'github.com', 'api.github.com', 'docs.python.org', 'alphacephei.com',
                'huggingface.co', 'google.github.io', 'forum.proxmox.com',
                'www.cisa.gov', 'raw.githubusercontent.com'}
KEV_SOURCES = (
    'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json',
    'https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json',
)


def inventory(system, run=subprocess.run, os_release=Path('/etc/os-release')):
    packages, gaps = [], []
    release = {}
    try:
        for line in Path(os_release).read_text().splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                release[key] = value.strip('"')
    except OSError:
        gaps.append(f'{system}: Betriebssystemausgabe nicht lesbar')
    try:
        result = run(['dpkg-query', '-W', '-f',
                      '${db:Status-Status}\t${binary:Package}\t${Version}\t${source:Package}\t${source:Version}\n'],
                     capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise OSError('dpkg-query failed')
        for line in result.stdout.splitlines():
            row = line.split('\t')
            if len(row) == 5 and row[0] == 'installed':
                packages.append(dict(ecosystem='Debian', name=row[1].split(':')[0],
                                     version=row[2], source=row[3], source_version=row[4]))
    except (OSError, subprocess.SubprocessError):
        gaps.append(f'{system}: Debian-Paketbestand nicht lesbar')
    for package in importlib.metadata.distributions():
        name = package.metadata.get('Name', '')
        if name and package.version:
            packages.append(dict(ecosystem='PyPI', name=name, version=package.version))
    # The Pi service uses system Python, while Piper/openWakeWord live in a
    # separate environment. Query its installed metadata without importing
    # any model or pulling package installers into the running service.
    piper_python = Path('/opt/pi-voice-assistant/.venv/bin/python')
    if system == 'pi' and piper_python.is_file() and Path(sys.prefix) != piper_python.parent.parent:
        try:
            code = ('import importlib.metadata as m,json; print(json.dumps(['
                    '{"name":p.metadata.get("Name",""),"version":p.version}'
                    ' for p in m.distributions()]))')
            result = run([str(piper_python), '-I', '-c', code], capture_output=True,
                         text=True, timeout=5, check=False)
            if result.returncode or len(result.stdout) > 512 * 1024:
                raise ValueError('invalid Piper metadata')
            for item in json.loads(result.stdout):
                packages.append(dict(ecosystem='PyPI', name=item['name'],
                                     version=item['version'], environment='piper'))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            gaps.append('pi: Piper-Pythonpakete nicht vollständig erfasst')
    # Binaries installed outside apt/pip require their own provenance; OSV can
    # match public git commits. Never infer deployed versions from install scripts.
    binary = Path('/opt/servitor-voice/llm/bin/llama-server')
    if system == 'server' and binary.is_file():
        try:
            result = run([str(binary), '--version'], capture_output=True, text=True, timeout=3)
            text = result.stdout + result.stderr
            commit = re.search(r'\b([a-f0-9]{40})\b', text)
            if not commit:
                checkout = binary.parent.parent / 'src'
                revision = run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'],
                               capture_output=True, text=True, timeout=3, check=False).stdout.strip()
                if re.fullmatch('[a-f0-9]{40}', revision) and re.search(
                        r'\b' + revision[:7] + r'[a-f0-9]{0,33}\b', text):
                    commit = (revision, revision)
            if not commit:
                provenance = binary.parent.parent / 'provenance.json'
                if provenance.is_file() and provenance.stat().st_size < 4096:
                    data = json.loads(provenance.read_text())
                    revision = data.get('commit', '')
                    with binary.open('rb') as source:
                        digest = hashlib.file_digest(source, 'sha256').hexdigest()
                    if re.fullmatch('[a-f0-9]{40}', revision) and digest == data.get('binary_sha256') \
                            and re.search(r'\b' + revision[:7] + r'[a-f0-9]{0,33}\b', text):
                        commit = (revision, revision)
            if commit:
                packages.append(dict(ecosystem='GIT', name='ggml-org/llama.cpp',
                                     version=commit[1], commit=commit[1]))
            else:
                gaps.append('server: llama.cpp-Commit nicht eindeutig erfasst')
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            gaps.append('server: llama.cpp-Version nicht lesbar')
    if len(packages) > MAX_PACKAGES:
        gaps.append(f'{system}: Paketbestand abgeschnitten')
    return dict(system=system, release=release.get('VERSION_CODENAME', ''),
                distro=release.get('ID', ''), kernel=platform.release(),
                packages=packages[:MAX_PACKAGES], gaps=gaps, at=time.time())


def sanitize_inventory(data, system):
    if not isinstance(data, dict) or not isinstance(data.get('packages'), list):
        raise ValueError('invalid inventory')
    if len(data['packages']) > MAX_PACKAGES:
        raise ValueError('inventory too large')
    packages = []
    for item in data['packages']:
        if not isinstance(item, dict) or item.get('ecosystem') not in ('Debian', 'PyPI', 'GIT'):
            raise ValueError('invalid package')
        entry = {key: clean(item.get(key), 160) for key in
                 ('name', 'version', 'source', 'source_version', 'commit', 'environment')}
        if not re.fullmatch(r'[\w.+/-]{1,160}', entry['name']) or not entry['version']:
            raise ValueError('invalid package name or version')
        if item['ecosystem'] == 'GIT' and not re.fullmatch('[a-f0-9]{40}', entry['commit']):
            raise ValueError('invalid commit')
        packages.append(dict(entry, ecosystem=item['ecosystem']))
    gaps = data.get('gaps', [])
    if not isinstance(gaps, list):
        raise ValueError('invalid gaps')
    return dict(system=system, release=clean(data.get('release'), 40),
                distro=clean(data.get('distro'), 40), kernel=clean(data.get('kernel'), 120),
                packages=packages, gaps=[clean(s, 160) for s in gaps[:12]])


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('redirects are not permitted')


class Fetcher:
    def __init__(self, guard, opener=None):
        self.guard = guard
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def json(self, url, body=None, limit=2 * 1024 * 1024, packages=None):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'https' or parts.hostname not in PUBLIC_HOSTS or parts.username \
                or parts.port not in (None, 443):
            raise ValueError('unapproved research endpoint')
        self.guard.check()
        raw = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(url, data=raw, headers={
            'User-Agent': 'Proximus-Nightwatch/1', 'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            **({'Content-Type': 'application/json'} if raw is not None else {})})
        with self.opener.open(request, timeout=5) as response:
            stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
            if packages is not None and url == DEBIAN:
                return self._packages(stream, set(packages), limit)
            chunks, size = [], 0
            while True:
                self.guard.check()
                chunk = stream.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ValueError('research response too large')
                chunks.append(chunk)
        return json.loads(b''.join(chunks))

    def _packages(self, stream, wanted, limit):
        """Decode one top-level package at a time; keep only installed sources.
        The complete tracker is much larger than the memory budget on the Pi.
        This runs on CT107, with a bounded buffer even for discarded records."""
        buffer, total, eof = '', 0, False
        decoder = json.JSONDecoder()
        utf8 = codecs.getincrementaldecoder('utf-8')()

        def fill():
            nonlocal buffer, total, eof
            self.guard.check()
            chunk = stream.read(65536)
            total += len(chunk)
            if total > limit:
                raise ValueError('research response too large')
            eof = not chunk
            buffer += utf8.decode(chunk, final=eof)
            if len(buffer) > 32 * 1024 * 1024:
                raise ValueError('package record too large')

        def symbol(expected):
            nonlocal buffer
            while not buffer.strip() and not eof:
                fill()
            buffer = buffer.lstrip()
            if not buffer.startswith(expected):
                raise ValueError('invalid tracker structure')
            buffer = buffer[1:]

        def value():
            nonlocal buffer
            while True:
                buffer = buffer.lstrip()
                try:
                    result, end = decoder.raw_decode(buffer)
                    buffer = buffer[end:]
                    return result
                except ValueError:
                    if eof:
                        raise ValueError('incomplete tracker response')
                    fill()

        symbol('{')
        result = {}
        entries = 0
        while True:
            while not buffer.strip() and not eof:
                fill()
            if buffer.lstrip().startswith('}'):
                symbol('}')
                break
            name = value()
            if not isinstance(name, str):
                raise ValueError('invalid package key')
            symbol(':')
            records = value()
            entries += 1
            if not isinstance(records, dict):
                raise ValueError('invalid package records')
            if name in wanted:
                result[name] = records
            while not buffer.strip() and not eof:
                fill()
            if buffer.lstrip().startswith(','):
                symbol(',')
            else:
                symbol('}')
                break
        # Verify the response really ended rather than accepting a truncated
        # or concatenated feed as evidence that old findings are resolved.
        while not eof:
            fill()
        if buffer.strip() or not entries:
            raise ValueError('unexpected tracker suffix')
        return result


class Audit:
    def __init__(self, cache, fetch, compare=None, clock=time.time):
        self.cache, self.fetch, self.clock = Path(cache), fetch, clock
        self.compare = compare or self._compare

    @staticmethod
    def _compare(version, fixed):
        result = subprocess.run(['dpkg', '--compare-versions', version, 'ge', fixed],
                                timeout=2, capture_output=True, check=False)
        if result.returncode not in (0, 1):
            raise ValueError('invalid Debian version')
        return result.returncode == 0

    def debian_data(self, names):
        path = self.cache / 'debian.json'
        names = sorted(set(names))
        try:
            if path.stat().st_size <= 64 * 1024 * 1024 \
                    and 0 <= self.clock() - path.stat().st_mtime < 6 * 3600:
                data = json.loads(path.read_text())
                if isinstance(data, dict) and data.get('names') == names and isinstance(data.get('data'), dict):
                    return data['data']
        except (OSError, ValueError):
            pass
        data = self.fetch.json(DEBIAN, limit=256 * 1024 * 1024, packages=names)
        if not isinstance(data, dict):
            raise ValueError('invalid Debian data')
        try:
            atomic_json(path, dict(names=names, data=data))
        except OSError:
            pass  # fresh data is usable even if its cache cannot be written
        return data

    def scan(self, inventories):
        findings, checked, gaps = [], [], []
        debian = None
        try:
            names = [p.get('source') or p['name'] for inv in inventories
                     for p in inv['packages'] if p['ecosystem'] == 'Debian']
            debian = self.debian_data(names)
        except (OSError, ValueError):
            gaps.append('Debian-Sicherheitsdaten nicht aktuell abrufbar')
        for inv in inventories:
            system = inv['system']
            gaps.extend(inv['gaps'])
            distro_ok = inv['distro'] == 'debian'
            scope = f'security:{system}:debian'
            if debian is not None and inv['release'] and inv['packages']:
                covered = distro_ok and not inv['gaps']
                for package in inv['packages']:
                    self.fetch.guard.check()
                    if package['ecosystem'] != 'Debian':
                        continue
                    name = package.get('source') or package['name']
                    version = package.get('source_version') or package['version']
                    vendor = bool(re.search(r'(?:rpt|rpi|raspi|pve|proxmox)', version + ' ' + name, re.I))
                    if vendor:
                        covered = False
                        gaps.append(f'{system}: Herstellerpakete zusätzlich anhand der Herstellerhinweise prüfen')
                    for cve, record in debian.get(name, {}).items():
                        releases = record.get('releases', {})
                        state = releases.get(inv['release'])
                        if not isinstance(state, dict):
                            continue  # release not tracked by this record
                        fixed = state.get('fixed_version', '')
                        if fixed == '0':
                            continue  # Debian explicitly marks the release unaffected
                        if state.get('status') not in ('open', 'resolved', 'undetermined'):
                            covered = False
                            continue
                        try:
                            if distro_ok and not vendor and fixed and self.compare(version, fixed):
                                continue  # includes Debian backported security fixes
                        except (OSError, ValueError, subprocess.SubprocessError):
                            gaps.append(f'{system}: Versionsvergleich für {name} nicht möglich')
                            covered = False
                            continue
                        if state.get('status') == 'resolved' and not fixed:
                            covered = False
                            continue
                        uncertain = not distro_ok or vendor or state.get('status') == 'undetermined'
                        urgency = state.get('urgency', record.get('urgency', ''))
                        priority = 0 if str(urgency).startswith('high') else 1
                        advice = (f'Korrigierte Paketversion {fixed} prüfen.' if fixed else
                                  'Herstellerhinweise und verfügbare Abhilfe prüfen.')
                        if uncertain:
                            advice += ' Betroffenheit für diese Distribution noch klären.'
                        findings.append(finding(scope, f'{name}:{cve}',
                            f'{system}: {name}, bekannte Lücke {cve}', advice, 'security', priority,
                            [f'https://security-tracker.debian.org/tracker/{urllib.parse.quote(cve)}'],
                            version=version, fixed=fixed,
                            assessment='possible' if uncertain else 'affected'))
                if covered:
                    checked.append(scope)
                if not distro_ok:
                    gaps.append(f'{system}: Herstellerkorrekturen der Debian-Ableitung zusätzlich prüfen')
            else:
                gaps.append(f'{system}: Debian-Abgleich unvollständig')
            other = [p for p in inv['packages'] if p['ecosystem'] in ('PyPI', 'GIT')]
            complete = not inv['gaps']
            for offset in range(0, len(other), 100):
                batch = other[offset:offset + 100]
                try:
                    queries = [dict(commit=p['commit']) if p['ecosystem'] == 'GIT' else dict(
                        package=dict(name=p['name'], ecosystem='PyPI'), version=p['version']) for p in batch]
                    data = self.fetch.json(OSV, dict(queries=queries))
                    results = data.get('results')
                    if not isinstance(results, list) or len(results) != len(batch):
                        raise ValueError('invalid OSV results')
                    for package, result in zip(batch, results):
                        if not isinstance(result, dict) or result.get('next_page_token'):
                            raise ValueError('incomplete OSV results')
                        for issue in result.get('vulns', []):
                            ident = clean(issue.get('id'), 100)
                            if not re.fullmatch(r'[\w.-]{1,100}', ident):
                                raise ValueError('invalid advisory id')
                            # Details include aliases, withdrawn status and fixed ranges.
                            detail = self.fetch.json('https://api.osv.dev/v1/vulns/' + ident)
                            if not isinstance(detail, dict) or detail.get('id') != ident:
                                raise ValueError('invalid OSV advisory')
                            if detail.get('withdrawn'):
                                continue
                            aliases = [a for a in detail.get('aliases', []) if isinstance(a, str)]
                            canonical = next((a for a in aliases if a.startswith('CVE-')), ident)
                            fixes = []
                            for affected in detail.get('affected', []):
                                target = affected.get('package', {})
                                if package['ecosystem'] == 'PyPI' and (
                                        target.get('ecosystem') != 'PyPI' or
                                        re.sub(r'[-_.]+', '-', target.get('name', '')).lower() !=
                                        re.sub(r'[-_.]+', '-', package['name']).lower()):
                                    continue
                                for span in affected.get('ranges', []):
                                    fixes.extend(e['fixed'] for e in span.get('events', []) if 'fixed' in e)
                            sources = [source_url(ref.get('url')) for ref in detail.get('references', [])]
                            sources = [s for s in sources if s][:4]
                            sources.append(f'https://osv.dev/vulnerability/{ident}')
                            findings.append(finding(f'security:{system}:osv',
                                f"{package['name']}:{package.get('environment') or 'service'}:{canonical}",
                                f"{system}: {package['name']}, bekannte Lücke {canonical}",
                                'Passende korrigierte Version und Voraussetzungen im Herstellerhinweis prüfen.',
                                'security', 1, sources, version=package['version'],
                                fixed=', '.join(fixes)[:120], assessment='version-match'))
                except (OSError, ValueError, TypeError, AttributeError):
                    complete = False
                    gaps.append(f'{system}: OSV-Abgleich unvollständig')
            if complete and other:
                checked.append(f'security:{system}:osv')
        if len(findings) > 100:
            gaps.append('Mehr als 100 Sicherheitsbefunde; Abgleich nicht vollständig abgelegt')
            checked = []  # never close anything from a capped report
        return findings[:100], checked, list(dict.fromkeys(gaps))


class ExploitPriority:
    """Prioritise already matched CVEs; KEV is not an installed-version scan."""
    def __init__(self, cache, fetch, clock=time.time):
        self.cache, self.fetch, self.clock = Path(cache), fetch, clock

    def _validate(self, data):
        if not isinstance(data, dict) or not isinstance(data.get('vulnerabilities'), list) \
                or type(data.get('count')) is not int \
                or not 1 <= data['count'] == len(data['vulnerabilities']) <= 20000 \
                or not isinstance(data.get('catalogVersion'), str) or not data['catalogVersion']:
            raise ValueError('invalid KEV catalog')
        released = datetime.datetime.fromisoformat(data.get('dateReleased', '').replace('Z', '+00:00'))
        if released.tzinfo is None or released.timestamp() > self.clock() + 120:
            raise ValueError('invalid KEV release date')
        ids = [item.get('cveID', '') for item in data['vulnerabilities'] if isinstance(item, dict)]
        if len(ids) != data['count'] or any(
                not isinstance(cve, str) or not re.fullmatch(r'CVE-\d{4}-\d{4,19}', cve) for cve in ids) \
                or len(set(ids)) != len(ids):
            raise ValueError('invalid KEV ids')
        return set(ids)

    def data(self):
        path = self.cache / 'kev.json'
        try:
            if path.stat().st_size <= 8 * 1024 * 1024 and 0 <= self.clock() - path.stat().st_mtime < 6 * 3600:
                data = json.loads(path.read_text())
                if data.get('source') in KEV_SOURCES:
                    return self._validate(data['catalog']), data['source']
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass
        for source in KEV_SOURCES:
            try:
                data = self.fetch.json(source, limit=8 * 1024 * 1024)
                ids = self._validate(data)
                try:
                    atomic_json(path, dict(source=source, catalog=data))
                except OSError:
                    pass
                return ids, source
            except (OSError, ValueError, TypeError, AttributeError, EOFError):
                continue
        raise OSError('KEV priority source unavailable')

    def enrich(self, findings):
        matched = [(item, re.search(r'\bCVE-\d{4}-\d{4,19}\b', item['key']))
                   for item in findings if item['category'] == 'security']
        matched = [(item, match[0]) for item, match in matched if match]
        if not matched:
            return []  # no CVE to prioritise: no needless extra request
        try:
            ids, source = self.data()
        except OSError:
            return ['Ausnutzungspriorität ungeprüft: CISA-KEV-Katalog nicht erreichbar']
        for item, cve in matched:
            if cve in ids:
                item['priority'] = 0
                item['known_exploited'] = True
                item['note'] = 'CISA meldet bekannte Ausnutzung dieser CVE; kein Nachweis eines Angriffs auf dieses Gerät.'
                item['advice'] = clean('Bekannt ausgenutzt: zuerst Betroffenheit und Abhilfe prüfen. ' + item['advice'], 500)
                item['sources'] = list(dict.fromkeys([source] + item.get('sources', [])))[:5]
        return []
