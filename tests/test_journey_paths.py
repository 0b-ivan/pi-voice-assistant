"""Journeys through both speech paths: server -> NDJSON without audio -> Pi
controller, the local fallback, and the buttons."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import journeys  # noqa: E402
import servitor_server as ss  # noqa: E402
import transit  # noqa: E402
from controller_fixture import Presser, make_controller  # noqa: E402
from remote_turn import RemoteTurnJob  # noqa: E402
from test_remote_turn import LiveServerCase  # noqa: E402
from transit_fixture import FakeClient, tram_journey, utc  # noqa: E402


class ServerPathTests(LiveServerCase):
    def journey_turn(self, transcript, status):
        self.pipeline.transcript = transcript
        return self.turn(status=status)

    def test_follow_up_is_handed_over_structured_without_audio(self):
        job = self.journey_turn('die zweite', {'trip': 'offers'})
        self.assertIsNone(job.error)
        self.assertIsNone(job.result['audio'])
        self.assertEqual(job.result['journey'],
                         dict(request=dict(type='select', index=1), speaker=None, guest=False,
                              proposal=None))
        self.assertIsNone(job.reply)
        self.assertFalse(hasattr(self.pipeline, 'lore'))          # no LLM call

    def test_long_question_beats_the_calendar_intent_and_the_llm(self):
        job = self.journey_turn('wann fährt heute noch der nächste zug ab würzburg '
                                'hauptbahnhof nach nürnberg hauptbahnhof bitte', {'trip': 'on'})
        request = job.result['journey']['request']
        self.assertEqual((request['type'], request['mode']), ('query', 'train'))
        job = self.journey_turn('trag die fahrt in meinen kalender ein', {'trip': 'offers'})
        self.assertEqual(job.result['journey']['request']['type'], 'action')

    def test_older_pi_without_journeys_gets_the_usual_answer(self):
        job = self.journey_turn('wann fährt die nächste straßenbahn in die stadt', {})
        self.assertIsNone(job.error)
        self.assertIsNotNone(job.result['audio'])
        self.assertIsNone(job.result.get('journey'))

    def test_consent_carries_proposal_and_voice(self):
        job = self.journey_turn('ja', {'trip': 'ask', 'trip_id': '0123456789abcdef'})
        self.assertEqual(job.result['journey']['request'], dict(type='answer', answer='confirm'))
        self.assertEqual(job.result['journey']['proposal'], '0123456789abcdef')
        job = self.journey_turn('ja', {'trip': 'ask', 'trip_id': 'nonsense'})
        self.assertIsNone(job.result['journey']['proposal'])     # sanitized away
        guest = dict(facts=[], directives=[], history=[],
                     voiceprints=[dict(name='Ivan', print='AAAA')])
        with patch.object(ss.Service, '_identify',
                          lambda self, audio, copy, emit: dict(copy, speaker='unknown')), \
                patch.object(ss.Handler, '_memory', lambda handler, device: (guest, None)):
            job = self.journey_turn('ja', {'trip': 'ask', 'trip_id': '0123456789abcdef',
                                           'memory': 'on'})
        self.assertEqual((job.result['journey']['guest'], job.result['journey']['speaker']),
                         (True, None))

    def test_abbrechen_with_an_open_question_is_a_journey_cancel(self):
        job = self.journey_turn('abbrechen', {'trip': 'ask', 'trip_id': '0123456789abcdef'})
        self.assertEqual(job.result['journey']['request'], dict(type='cancel'))
        job = self.journey_turn('abbrechen', {'trip': 'on'})
        self.assertTrue(job.result['stop'])                       # plain stop word as before


class FakeUplink:
    def __init__(self, events):
        self.events = events
        self.config = SimpleNamespace(response_timeout=5)
        self.host = 'test'

    def wait_uploaded(self, timeout):
        pass

    def responses(self):
        yield from self.events

    def cancel(self):
        pass

    def close(self):
        pass


class StreamTests(unittest.TestCase):
    def job(self, events):
        job = RemoteTurnJob(FakeUplink(events), '/tmp')
        self.assertTrue(job.done.wait(5))
        return job

    def test_no_action_from_a_partial_stream(self):
        journey = dict(event='journey', request=dict(type='select', index=0), speaker=None,
                       guest=False, proposal=None)
        job = self.job([dict(event='transcript', text='die erste'), journey])
        self.assertEqual(job.error_code, 'incomplete')
        self.assertIsNone(job.result)
        self.assertTrue(job.fallback_allowed)       # the Pi may still answer it locally
        job = self.job([dict(event='transcript', text='die erste'), journey, journey,
                        dict(event='done', timings={})])
        self.assertEqual(job.error_code, 'protocol')
        job = self.job([dict(event='transcript', text='die erste'), journey,
                        dict(event='done', timings={})])
        self.assertIsNone(job.error)
        self.assertEqual(job.result['journey']['request'], dict(type='select', index=0))


def install(controller, journeys_list):
    now = [utc(12, 0).astimezone(transit.TZ)]
    controller.journeys = journeys.Journeys(
        FakeClient(journeys_list), transit.Config(home=(50.0, 10.0)), None,
        now=lambda: now[0], log=lambda name, **fields: None)
    return controller.journeys


def wait(controller, presser):
    if controller.job is not None:
        assert controller.job.done.wait(5)
    presser.tick()


class ControllerPathTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller(self)
        self.p = Presser(self.c)
        self.j = install(self.c, [tram_journey(utc(12, 32)), tram_journey(utc(12, 47))])

    def spoken(self):
        return self.c.speech.start.call_args[0][0]

    def test_local_path_queries_in_the_background_and_speaks(self):
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        self.assertEqual(self.c.job_stage, 'journey')
        wait(self.c, self.p)
        self.assertIn('2 Verbindungen nach Nürnberg', self.spoken())
        self.assertEqual(self.c.status_snapshot()['trip'], 'offers')

    def propose(self):
        self.c._start_llm('wie komme ich von zu hause nach nürnberg')
        wait(self.c, self.p)
        self.c._start_llm('die erste erinnere mich')
        self.assertIn('hier am Gerät erinnern', self.spoken())
        snapshot = self.c.status_snapshot()
        self.assertEqual(snapshot['trip'], 'ask')
        return snapshot['trip_id']

    def test_remote_hand_over_acts_only_on_the_matching_proposal(self):
        proposal_id = self.propose()
        self.c.turn_trip = self.j.take()            # what submit() does
        job = SimpleNamespace(drain=lambda: [], cancelled=False, transcript='ja', error=None,
                              parts=0, result=dict(audio=None, host='test', journey=dict(
                                  request=dict(type='answer', answer='confirm'), speaker=None,
                                  guest=False, proposal=proposal_id)))
        self.c._finish_remote(job)
        wait(self.c, self.p)
        self.assertIn('gesetzt', self.spoken())
        self.assertEqual(len(self.j.reminders), 1)
        # The same event again (or the local fallback) finds no question any more.
        self.c._finish_remote(job)
        self.assertIn('Keine offene Bestätigung', self.spoken())
        self.assertEqual(len(self.j.reminders), 1)

    def test_failed_remote_turn_is_answered_locally_once(self):
        self.propose()
        self.c.turn_trip = self.j.take()
        job = SimpleNamespace(drain=lambda: [], cancelled=False, transcript='ja',
                              error='reply ended', error_stage='stream', error_code='incomplete',
                              fallback_allowed=True, parts=0, reply=None, model=None,
                              result=None)
        self.c.remote_capture = None
        self.c._finish_remote(job)
        wait(self.c, self.p)
        self.assertIn('gesetzt', self.spoken())
        self.assertEqual(len(self.j.reminders), 1)

    def test_other_answer_drops_the_question(self):
        self.propose()
        self.c.turn_trip = self.j.take()
        self.c._start_llm('wie spät ist es')
        self.assertIsNone(self.c.turn_trip)
        self.assertEqual(self.j.state(), 'offers')
        self.assertEqual(self.j.reminders, [])

    def test_button_e_confirms_and_b_cancels(self):
        self.propose()
        self.p.press('E')
        wait(self.c, self.p)
        self.assertIn('gesetzt', self.spoken())
        self.assertEqual(len(self.j.reminders), 1)
        self.propose()
        self.p.press('B')
        self.assertIsNone(self.j.proposal)
        self.assertEqual(len(self.j.reminders), 1)

    def test_submit_takes_the_question_with_the_turn(self):
        self.propose()
        self.c.recorder.finish.return_value = Path('/tmp/capture.wav')
        self.c.recorder.take_live_transcript.return_value = ('ja', 'vosk')
        self.c.submit('release')
        self.assertIsNotNone(self.c.turn_trip)
        self.assertIsNone(self.j.proposal)          # E or another turn cannot use it now

    def test_reminder_is_spoken_through_the_alarm_queue_once(self):
        self.propose()
        self.c.turn_trip = self.j.take()
        self.c._start_llm('ja')
        wait(self.c, self.p)
        self.assertEqual(len(self.j.reminders), 1)
        with patch.object(self.c, '_say_alarm') as say:
            self.j.now = lambda: utc(12, 20).astimezone(transit.TZ)
            self.p.tick()
            self.p.tick()
        texts = [call.args[0] for call in say.call_args_list]
        self.assertEqual(len(texts), 1)
        self.assertIn('Zeit zu gehen', texts[0][0])


if __name__ == '__main__':
    unittest.main()
