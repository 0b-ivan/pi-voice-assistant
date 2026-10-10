import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import intents  # noqa: E402
import logwatch  # noqa: E402
from system_status import sanitize_snapshot  # noqa: E402


def line(message, unit=None, priority=6, kernel=False, systemd_unit=None, at=1760000000.0):
    data = dict(MESSAGE=message, PRIORITY=str(priority),
                __REALTIME_TIMESTAMP=str(int(at * 1e6)))
    if unit:
        data['UNIT'] = unit
    if systemd_unit:
        data['_SYSTEMD_UNIT'] = systemd_unit
    if kernel:
        data['_TRANSPORT'] = 'kernel'
    return json.dumps(data)


def own(name, **fields):
    return logwatch.parse_entry(line(json.dumps(dict(version=1, event=name, **fields)),
                                     systemd_unit='pi-ptt.service'))


def pi(entries):
    return logwatch.analyse(entries, logwatch.PI_RULES, logwatch.PI_UNITS)


class ParseTests(unittest.TestCase):
    def test_fields(self):
        entry = logwatch.parse_entry(line('hwmon hwmon1: Undervoltage detected!', priority=2,
                                          kernel=True))
        self.assertEqual(entry['priority'], 2)
        self.assertTrue(entry['kernel'])
        self.assertEqual(entry['at'], 1760000000.0)
        self.assertIsNone(entry['event'])
        self.assertEqual(own('stt_error', message='x')['event'], 'stt_error')

    def test_byte_array_message_and_garbage(self):
        entry = logwatch.parse_entry(json.dumps(dict(MESSAGE=list(b'caf\xc3\xa9 \xff'))))
        self.assertTrue(entry['text'].startswith('café'))
        self.assertIsNone(logwatch.parse_entry('not json'))
        self.assertIsNone(logwatch.parse_entry('[1, 2]'))

    def test_silence_is_not_an_error(self):
        self.assertEqual(own('stt_error', message='Vosk returned no transcript')['event'], 'benign')
        self.assertEqual(own('remote_error', stage='recognize', code='no_speech')['event'], 'benign')


class AnalyseTests(unittest.TestCase):
    def test_thresholds_keep_single_hiccups_out(self):
        self.assertEqual(pi([own('stt_error', message='boom')] * 2), {})
        self.assertEqual(pi([own('stt_error', message='boom')] * 3), {'stt': 3})
        self.assertEqual(pi([own('remote_error', stage='stream', code='network')] * 4), {})

    def test_crash_and_kernel_faults_count_at_once(self):
        entries = [logwatch.parse_entry(line(
                       "pi-display.service: Failed with result 'exit-code'.", priority=4,
                       unit='pi-display.service', systemd_unit='init.scope')),
                   logwatch.parse_entry(line('hwmon hwmon1: Undervoltage detected!',
                                             priority=2, kernel=True)),
                   logwatch.parse_entry(line('EXT4-fs error (device sda1): bad block',
                                             priority=2, kernel=True))]
        self.assertEqual(pi(entries), {'crash_display': 1, 'undervoltage': 1, 'disk_io': 1})

    def test_slow_stop_is_no_crash(self):
        entry = logwatch.parse_entry(line("pi-ptt.service: Failed with result 'timeout'.",
                                          priority=4, unit='pi-ptt.service'))
        self.assertEqual(pi([entry]), {})

    def test_transcripts_and_answers_never_match_patterns(self):
        entries = [logwatch.parse_entry(line('SERVITOR: out of memory, sagte der Kogitator',
                                             systemd_unit='pi-ptt.service', priority=3))] * 9
        self.assertEqual(pi(entries), {})

    def test_known_noise_and_other_errors(self):
        noise = logwatch.parse_entry(line('brcmfmac: brcmf_set_channel: set chanspec fail',
                                          priority=3, kernel=True))
        other = logwatch.parse_entry(line('foo.service: something broke', priority=3))
        self.assertEqual(pi([noise] * 20), {})
        boot = [logwatch.parse_entry(line(text, priority=3)) for text in (
            'nl80211: kernel reports: Registration to specific type not supported',
            'bgscan simple: Failed to enable signal strength monitoring',
            '/usr/lib/udev/rules.d/90-alsa-restore.rules:18 GOTO="alsa_restore_std" '
            'has no matching label, ignoring.')]
        self.assertEqual(pi(boot * 5), {})
        self.assertEqual(pi([other] * 4), {})
        self.assertEqual(pi([other] * 5), {'errors': 5})
        ignore = logwatch.compile_ignore('something broke')
        self.assertEqual(logwatch.analyse([other] * 5, logwatch.PI_RULES, logwatch.PI_UNITS,
                                          ignore), {})

    def test_stick_pull_is_not_a_usb_fault(self):
        entry = logwatch.parse_entry(line('usb 1-1.2: USB disconnect, device number 4',
                                          priority=4, kernel=True))
        self.assertEqual(pi([entry] * 5), {})


    def test_restart_for_new_code_is_no_crash(self):
        def systemd(message):
            return logwatch.parse_entry(line(message, priority=4, unit='pi-display.service',
                                             systemd_unit='init.scope'))
        restart = [systemd('pi-display.service: Main process exited, code=exited, '
                           'status=75/TEMPFAIL'),
                   systemd("pi-display.service: Failed with result 'exit-code'.")]
        self.assertEqual(pi(restart), {})
        crash = [systemd('pi-display.service: Main process exited, code=exited, '
                         'status=1/FAILURE'),
                 systemd("pi-display.service: Failed with result 'exit-code'.")]
        self.assertEqual(pi(restart + crash), {'crash_display': 1})

    def test_pulled_stick_writes_are_no_disk_fault(self):
        def kernel(message, at, priority=3):
            return logwatch.parse_entry(line(message, priority=priority, kernel=True, at=at))
        t = 1760000000.0
        pulled = [kernel('device offline error, dev sda, sector 2056 op 0x1:(WRITE)', t),
                  kernel('Buffer I/O error on dev sda1, logical block 1, lost async page write',
                         t + 0.1),
                  kernel('EXT4-fs (sda1): shut down requested (2)', t + 0.2, priority=1),
                  kernel('Aborting journal on device sda1-8.', t + 0.2),
                  kernel('JBD2: I/O error when updating journal superblock for sda1-8.', t + 0.3)]
        self.assertEqual(pi(pulled * 3), {})
        # The same errors later, or without "device offline", are a failing medium.
        later = kernel('Buffer I/O error on dev sda1, logical block 9, lost async page write',
                       t + 60)
        self.assertEqual(pi(pulled + [later]), {'disk_io': 1})
        sd_card = kernel('Buffer I/O error on dev mmcblk0p2, logical block 9', t + 0.1)
        self.assertEqual(pi(pulled + [sd_card]), {'disk_io': 1})


class WatchTests(unittest.TestCase):
    def make(self, entries=(), broken=None, requester=None, limited=False):
        self.now = [100000.0]
        self.reports = []
        state = dict(broken=False if broken is None else broken)
        self.state = state
        self.requested = []

        def request(action):
            self.requested.append(action)
            return True if requester is None else requester(action)

        repairs = (logwatch.Repair('display', 'restart-display', lambda: state['broken'],
                                   'display_down'),)
        return logwatch.LogWatch(logwatch.PI_RULES, logwatch.PI_UNITS, repairs=repairs,
                                 reader=lambda since, units: (list(entries), limited),
                                 clock=lambda: self.now[0], sleep=lambda s: None,
                                 requester=request, report=self.reports.append)

    def test_clean_run_and_change_reports(self):
        watch = self.make()
        self.assertEqual(watch.check(), dict(at=100000.0, findings={}, repairs=[]))
        watch.check()
        self.assertEqual(len(self.reports), 1)  # unchanged results are not logged again
        self.assertEqual(watch.snapshot_fields(), dict(log_findings={}, log_repairs=[]))

    def test_successful_repair_is_reported(self):
        watch = self.make(broken=True)

        def fixed(action):
            self.state['broken'] = False
            return True
        watch.requester = lambda action: (self.requested.append(action), fixed(action))[1]
        result = watch.check()
        self.assertEqual(self.requested, ['restart-display'])
        self.assertEqual(result['repairs'], ['display'])
        self.assertEqual(result['findings'], {})

    def test_failed_repair_and_rate_limit(self):
        watch = self.make(broken=True)
        self.assertEqual(watch.check()['findings'], {'display_down': 1})
        watch.check()  # within the cooldown: no second request
        self.assertEqual(self.requested, ['restart-display'])
        for _ in range(5):
            self.now[0] += logwatch.REPAIR_COOLDOWN
            watch.check()
        self.assertEqual(len(self.requested), logwatch.REPAIRS_PER_DAY)

    def test_missing_worker_is_a_finding(self):
        watch = self.make(broken=True, requester=lambda action: False)
        self.assertEqual(watch.check()['findings'], {'display_down': 1})

    def test_unreadable_journal_is_said(self):
        watch = self.make(limited=True)
        self.assertEqual(watch.check()['findings'], {'limited': 1})

        def fail(since, units):
            raise OSError('no journalctl')
        watch.reader = fail
        self.assertEqual(watch.check()['findings'], {'limited': 1})

    def test_logsync_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'logsync.json'
            self.assertTrue(logwatch.logsync_stalled(path, now=1000))
            path.write_text(json.dumps(dict(at=900, stick=True, ok=True, ok_at=900)))
            self.assertFalse(logwatch.logsync_stalled(path, now=1000))
            path.write_text(json.dumps(dict(at=900, stick=False, ok=False, ok_at=0)))
            self.assertFalse(logwatch.logsync_stalled(path, now=1000))
            path.write_text(json.dumps(dict(at=900, stick=True, ok=False, ok_at=0)))
            self.assertTrue(logwatch.logsync_stalled(path, now=1000))
            path.write_text(json.dumps(dict(at=900, stick=True, ok=True, ok_at=900)))
            self.assertTrue(logwatch.logsync_stalled(path, now=900 + logwatch.LOGSYNC_STALE + 1))

    def test_requests_only_fixed_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(logwatch.request('logsync', tmp))
            self.assertTrue((Path(tmp) / 'logsync').exists())
            with self.assertRaises(ValueError):
                logwatch.request('../../etc/passwd', tmp)
            self.assertFalse(logwatch.request('logsync', Path(tmp) / 'missing'))

    def test_pi_watch_skips_the_log_copy_without_stick(self):
        with tempfile.TemporaryDirectory() as tmp:
            watch = logwatch.pi_watch(lambda: False, requests=tmp, state=Path(tmp) / 'none')
            watch.reader = lambda since, units: ([], False)
            watch.sleep = lambda s: None
            watch.repairs = watch.repairs[1:]  # leave systemctl alone
            self.assertEqual(watch.check()['findings'], {})
            self.assertFalse((Path(tmp) / 'logsync').exists())


class SentenceTests(unittest.TestCase):
    def test_nothing_notable_says_nothing(self):
        self.assertIsNone(logwatch.briefing_sentence({}))
        self.assertIsNone(logwatch.briefing_sentence(dict(log_findings={}, log_repairs=[])))

    def test_most_severe_first_at_most_three(self):
        snapshot = dict(log_findings={'stt': 4, 'undervoltage': 2, 'buttons': 3, 'errors': 7},
                        log_repairs=['display'], server_log_findings={'crash_llm': 1})
        text = logwatch.briefing_sentence(snapshot)
        self.assertEqual(text, "Selbsttest: Pi: Unterspannung, zweimal. Pi: Spracherkennung "
                               "gestört, 4 mal. Server: Lokaler Sprachkern abgestürzt, einmal. "
                               "Und 3 weitere Punkte.")
        self.assertTrue(logwatch.briefing_sentence(snapshot, 'billy').startswith('In den Logs:'))

    def test_selftest_answer(self):
        self.assertIn('läuft', logwatch.selftest_text({}))
        self.assertIn('Keine Auffälligkeiten',
                      logwatch.selftest_text(dict(log_findings={}, log_repairs=[])))
        self.assertIn('Anzeige neu gestartet',
                      logwatch.selftest_text(dict(log_findings={}, log_repairs=['display'])))

    def test_snapshot_keeps_only_known_codes(self):
        clean = sanitize_snapshot(dict(log_findings={'stt': 3, 'rm -rf': 1, 'oom': True,
                                                     'usb': -1},
                                       log_repairs=['display', 'evil']))
        self.assertEqual(clean['log_findings'], {'stt': 3})
        self.assertEqual(clean['log_repairs'], ['display'])
        self.assertNotIn('log_findings', sanitize_snapshot(dict(log_findings='x')))


class IntentTests(unittest.TestCase):
    def test_selftest_phrases(self):
        for text in ('selbsttest', 'mach einen selbsttest', 'prüfe deine logs',
                     'logs prüfen', 'systemdiagnose bitte', 'analysiere die protokolle'):
            self.assertEqual(intents.match(text), 'selftest', text)
        for text in ('was ist die diagnose bei grippe', 'wie ist dein status'):
            self.assertNotEqual(intents.match(text), 'selftest', text)

    def test_briefing_names_findings_only_when_notable(self):
        import datetime
        now = datetime.datetime(2026, 10, 10, 7, 30)
        quiet = intents.answer('briefing', now, dict(log_findings={}, log_repairs=[]))
        self.assertNotIn('Selbsttest', quiet)
        loud = intents.answer('briefing', now, dict(log_findings={'undervoltage': 3}))
        self.assertIn('Selbsttest: Pi: Unterspannung, dreimal.', loud)
        self.assertTrue(loud.endswith('Bericht Ende.'))


class ScriptTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for path in ('deploy/logsync/proximus-logsync', 'deploy/logsync/install.sh',
                     'deploy/maintenance/proximus-maintenance', 'deploy/maintenance/install.sh'):
            subprocess.run(['sh', '-n', str(ROOT / path)], check=True)

    def test_worker_refuses_unknown_actions(self):
        result = subprocess.run(['sh', str(ROOT / 'deploy/maintenance/proximus-maintenance'),
                                 'rm'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)

    def test_repair_paths_match_worker_actions(self):
        worker = (ROOT / 'deploy/maintenance/proximus-maintenance').read_text()
        for action in ('restart-display', 'restart-llm'):
            self.assertIn(action, worker)
            unit = 'display' if action == 'restart-display' else 'llm'
            path = (ROOT / f'deploy/maintenance/proximus-repair-{unit}.path').read_text()
            self.assertIn(f'requests/{action}', path)
            self.assertIn(f'proximus-repair@{action}.service', path)
        self.assertIn('requests/logsync', (ROOT / 'deploy/logsync/proximus-logsync.path').read_text())
        self.assertEqual(set(logwatch.REPAIR_ACTIONS), {'restart-display', 'restart-llm', 'logsync'})

    def test_services_may_read_the_journal(self):
        for path in ('deploy/pi-ptt.service', 'server/servitor-voice.service'):
            self.assertIn('systemd-journal', (ROOT / path).read_text(), path)


if __name__ == '__main__':
    unittest.main()
