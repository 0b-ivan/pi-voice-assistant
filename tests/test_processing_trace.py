"""Execution trace: real routing, source selection, failures and privacy."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'server'), str(ROOT / 'tests')]
import llm
import processing_trace as trace
import servitor_server as server
import test_servitor_server as fixtures
from test_llm import FakeResponse


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = server.Config({'SERVITOR_API_TOKEN': 'secret-api-key-' * 4,
                                'SERVITOR_WORKDIR': self.tmp.name})
        self.pipeline = fixtures.FakePipeline(self.tmp.name)
        self.service = server.Service(config, self.pipeline)

    def events(self):
        return [json.loads(e['text']) for e in self.service.journal.since(0)['lines']]

    def test_real_turn_records_operations_without_question_or_answer(self):
        sent = []
        self.service.run_turn([], sent.append, 'wav', typed='private-user-question')
        events = self.events()
        self.assertEqual(events[0]['state'], 'start')
        self.assertEqual(events[-1]['state'], 'done')
        self.assertEqual(len({e['turn'] for e in events}), 1)
        self.assertTrue(any(e['step'] == 'operation' and e['name'] == 'synthesis'
                            and e['state'] == 'done' and e['ms'] >= 0 for e in events))
        self.assertTrue(any(e['step'] == 'answer' for e in events))
        self.assertTrue(any(e.get('event') == 'audio' for e in sent))
        raw = json.dumps(events)
        for secret in ('private-user-question', 'Antwort auf', 'secret-api-key'):
            self.assertNotIn(secret, raw)
        trace.record('outside-turn')
        self.assertEqual(self.events(), events)

    def test_fixed_status_uses_rule_not_llm(self):
        with patch.object(self.pipeline, 'reply', side_effect=AssertionError('no LLM')):
            self.service.run_turn([], lambda e: None, 'wav', typed='system status?')
        self.assertTrue(any(e['step'] == 'route' and e['name'] == 'status' for e in self.events()))
        self.assertFalse(any(e['step'] == 'operation' and e['name'] == 'llm' for e in self.events()))

    def test_failed_turn_resets_context_and_has_no_raw_error(self):
        self.pipeline.fail_llm = True
        with self.assertRaises(server.TurnError):
            self.service.run_turn([], lambda e: None, 'wav', typed='private-user-question')
        events = self.events()
        self.assertEqual(events[-1]['state'], 'failed')
        self.assertNotIn('OpenRouter request failed', json.dumps(events))
        trace.record('outside-turn')
        self.assertEqual(self.events(), events)

    def test_source_ids_match_selected_archive_without_private_memory(self):
        events = []
        token = trace._SINK.set(events.append)
        self.addCleanup(trace._SINK.reset, token)
        llm.system_prompt('full', memory={'history': [], 'facts': ['private-memory']},
                          persona='mensch', query='Kann dein Vox einen Astropathen ersetzen?')
        sources = [e for e in events if e['step'] == 'sources']
        self.assertEqual(len(sources), 1)
        self.assertIn('E29', sources[0]['ids'])
        self.assertNotIn('private-memory', json.dumps(events))

    def test_provider_records_real_usage_but_no_prompt_or_credentials(self):
        events = []
        token = trace._SINK.set(events.append)
        self.addCleanup(trace._SINK.reset, token)
        payload = {'choices': [{'message': {'content': 'private-answer'}}],
                   'usage': {'total_tokens': 24, 'prompt_tokens': 20,
                             'completion_tokens': 4, 'secret': 'private-usage'}}
        with patch('llm.urllib.request.urlopen', return_value=FakeResponse(payload)):
            answer = llm._chat('https://example.invalid', 'private-prompt', 'test/model', 1,
                               {}, {'Authorization': 'Bearer private-key'}, 'OpenRouter',
                               messages=[{'role': 'user', 'content': 'private-prompt'}])
        self.assertEqual(answer, 'private-answer')
        self.assertEqual(events[-1]['total_tokens'], 24)
        for private in ('private-answer', 'private-prompt', 'private-key', 'private-usage'):
            self.assertNotIn(private, json.dumps(events))

    def test_provider_failure_records_class_without_raw_remote_error(self):
        events = []
        token = trace._SINK.set(events.append)
        self.addCleanup(trace._SINK.reset, token)
        with patch('llm.urllib.request.urlopen', side_effect=TimeoutError('private-url-key')):
            with self.assertRaises(llm.LLMError):
                llm._chat('https://example.invalid', 'private', 'test/model', 1, {}, {},
                          'OpenRouter', messages=[])
        self.assertEqual(events[-1]['state'], 'failed')
        self.assertEqual(events[-1]['error'], 'TimeoutError')
        self.assertNotIn('private-url-key', json.dumps(events))

    def test_broken_observer_does_not_break_answer(self):
        with patch.object(self.service.journal, 'add', side_effect=RuntimeError('broken trace')):
            self.service.run_turn([], lambda e: None, 'wav', typed='system status?')
