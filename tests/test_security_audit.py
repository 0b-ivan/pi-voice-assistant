import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from security_audit import Audit, ExploitPriority, Fetcher, DEBIAN, OSV, KEV_SOURCES, sanitize_inventory
from nightwatch import finding
from nightwatch_server import Guard, Interrupted


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fetch = Mock()
        self.fetch.guard = Guard()
        self.audit = Audit(self.tmp.name, self.fetch, compare=lambda installed, fixed: installed == fixed)
        self.inv = dict(system='pi', distro='debian', release='trixie', gaps=[], packages=[dict(
            ecosystem='Debian', name='libfoo1', version='1.0-1', source='foo', source_version='1.0-1')])
        self.data = dict(foo={'CVE-TEST-1': dict(releases=dict(trixie=dict(
            status='resolved', fixed_version='1.0-1+deb13u1', urgency='high')))})

    def test_backported_fix_uses_source_package_and_release(self):
        self.fetch.json.return_value = self.data
        issues, checked, gaps = self.audit.scan([self.inv])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['priority'], 0)
        self.assertEqual(issues[0]['fixed'], '1.0-1+deb13u1')
        self.assertIn('security:pi:debian', checked)
        self.assertEqual(gaps, [])
        self.inv['packages'][0]['source_version'] = '1.0-1+deb13u1'
        issues, checked, _ = self.audit.scan([self.inv])
        self.assertEqual(issues, [])
        self.assertIn('security:pi:debian', checked)

    def test_unaffected_release_and_unknown_distribution(self):
        self.data['foo']['CVE-TEST-1']['releases']['trixie']['fixed_version'] = '0'
        self.fetch.json.return_value = self.data
        issues, _, _ = self.audit.scan([self.inv])
        self.assertEqual(issues, [])
        self.data['foo']['CVE-TEST-1']['releases']['trixie']['fixed_version'] = ''
        self.data['foo']['CVE-TEST-1']['releases']['trixie']['status'] = 'open'
        self.inv['distro'] = 'raspbian'
        (Path(self.tmp.name) / 'debian.json').unlink()
        issues, checked, gaps = self.audit.scan([self.inv])
        self.assertEqual(issues[0]['assessment'], 'possible')
        self.assertNotIn('security:pi:debian', checked)
        self.assertTrue(gaps)

    def test_failed_sources_do_not_close_old_security_findings(self):
        self.fetch.json.side_effect = OSError('offline')
        issues, checked, gaps = self.audit.scan([self.inv])
        self.assertEqual(issues, [])
        self.assertEqual(checked, [])
        self.assertTrue(gaps)

    def test_osv_detail_and_withdrawn_advisory(self):
        self.inv['packages'].append(dict(ecosystem='PyPI', name='sample', version='1.0'))
        def fetch(url, body=None, **kwargs):
            if url == DEBIAN:
                return {}
            if url == OSV:
                self.assertEqual(body['queries'][0]['package']['ecosystem'], 'PyPI')
                return dict(results=[dict(vulns=[dict(id='GHSA-TEST')])])
            return dict(id='GHSA-TEST', aliases=['CVE-TEST'], affected=[], references=[], **self.detail)
        self.detail = {}
        self.fetch.json.side_effect = fetch
        issues, checked, _ = self.audit.scan([self.inv])
        self.assertEqual(issues[0]['key'], 'sample:service:CVE-TEST')
        self.assertIn('security:pi:osv', checked)
        self.detail = dict(withdrawn='2026-10-01')
        issues, _, _ = self.audit.scan([self.inv])
        self.assertEqual(issues, [])

    def test_osv_pagination_is_reported_as_incomplete(self):
        self.inv['packages'] = [dict(ecosystem='PyPI', name='sample', version='1')]
        self.fetch.json.side_effect = [{}, dict(results=[dict(next_page_token='more')])]
        issues, checked, gaps = self.audit.scan([self.inv])
        self.assertNotIn('security:pi:osv', checked)
        self.assertTrue(gaps)

    def test_remote_inventory_validation_and_research_hosts(self):
        bad = dict(packages=[dict(ecosystem='GIT', name='project', version='main', commit='main')])
        with self.assertRaises(ValueError):
            sanitize_inventory(bad, 'pi')
        fetch = Fetcher(Guard(), opener=Mock())
        for url in ('https://openrouter.ai/api/v1/chat/completions', 'http://127.0.0.1/',
                    'https://api.osv.dev:444/v1/querybatch', 'https://user@api.osv.dev/'):
            with self.assertRaises(ValueError):
                fetch.json(url)
        fetch.opener.open.assert_not_called()
        fetch.guard.cancel()
        with self.assertRaises(Interrupted):
            fetch.json(DEBIAN)

    def test_streaming_feed_filters_sources_and_rejects_truncated_input(self):
        fetch = Fetcher(Guard())
        raw = json.dumps(dict(ignored={'description': 'ä' * 70000}, foo=self.data['foo']),
                         ensure_ascii=False).encode()
        data = fetch._packages(io.BytesIO(raw), {'foo'}, len(raw))
        self.assertEqual(data, self.data)
        for invalid in (raw[:-3], raw + b' garbage', b'[]', b'{}'):
            with self.assertRaises(ValueError):
                fetch._packages(io.BytesIO(invalid), {'foo'}, len(invalid))
        with self.assertRaises(ValueError):
            fetch._packages(io.BytesIO(raw), {'foo'}, 100)

    def test_vendor_suffix_is_not_proof_that_a_debian_fix_is_installed(self):
        self.fetch.json.return_value = self.data
        self.inv['packages'][0]['source_version'] = '1.0-1+rpt1'
        self.audit.compare = lambda *args: True
        issues, checked, gaps = self.audit.scan([self.inv])
        self.assertEqual(issues[0]['assessment'], 'possible')
        self.assertNotIn('security:pi:debian', checked)
        self.assertTrue(gaps)

    def test_osv_fix_versions_are_for_the_matched_package(self):
        self.inv['packages'] = [dict(ecosystem='PyPI', name='Sample_Pkg', version='1')]
        self.fetch.json.side_effect = [{}, dict(results=[dict(vulns=[dict(id='GHSA-TEST')])]),
            dict(id='GHSA-TEST', affected=[dict(package=dict(name='another', ecosystem='PyPI'),
                                ranges=[dict(events=[dict(fixed='99')])]),
                           dict(package=dict(name='sample-pkg', ecosystem='PyPI'),
                                ranges=[dict(events=[dict(fixed='2')])])])]
        issues, _, _ = self.audit.scan([self.inv])
        self.assertEqual(issues[0]['fixed'], '2')


class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fetch = Mock()
        self.fetch.json.return_value = dict(catalogVersion='2026.10.10',
            dateReleased='2026-10-10T00:00:00Z', count=1,
            vulnerabilities=[dict(cveID='CVE-2026-10001')])
        self.priority = ExploitPriority(self.tmp.name, self.fetch)
        self.issue = finding('security:pi:osv', 'sample:CVE-2026-10001', 'Eine bekannte CVE',
                             'Abhilfe prüfen.', 'security', 1)

    def test_kev_enriches_only_an_existing_version_match_and_is_cached(self):
        self.assertEqual(self.priority.enrich([self.issue]), [])
        self.assertTrue(self.issue['known_exploited'])
        self.assertEqual(self.issue['priority'], 0)
        self.assertIn('kein Nachweis', self.issue['note'])
        self.assertEqual(self.priority.enrich([self.issue]), [])
        self.fetch.json.assert_called_once()
        harmless = finding('operation:pi', 'CVE-2026-10001', 'Logtext', '', 'operation', 2)
        self.priority.enrich([harmless])
        self.assertNotIn('known_exploited', harmless)

    def test_official_mirror_is_used_on_source_failure(self):
        data = self.fetch.json.return_value
        self.fetch.json.side_effect = [OSError('403'), data]
        self.assertEqual(self.priority.enrich([self.issue]), [])
        self.assertEqual(self.issue['sources'][0], KEV_SOURCES[1])

    def test_offline_priority_is_unknown_and_does_not_invent_exploitation(self):
        self.fetch.json.side_effect = OSError('offline')
        self.assertTrue(self.priority.enrich([self.issue]))
        self.assertNotIn('known_exploited', self.issue)
        self.assertEqual(self.issue['priority'], 1)

    def test_bad_schema_does_not_claim_a_successful_priority_check(self):
        for broken in ({}, dict(self.fetch.json.return_value, count=2),
                       dict(self.fetch.json.return_value, dateReleased='2099-01-01T00:00:00Z'),
                       dict(self.fetch.json.return_value, vulnerabilities=[dict(cveID=[])])):
            with self.subTest(data=broken):
                self.fetch.json.return_value = broken
                self.assertTrue(self.priority.enrich([self.issue]))


if __name__ == '__main__':
    unittest.main()
