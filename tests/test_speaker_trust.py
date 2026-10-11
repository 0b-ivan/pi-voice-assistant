from pathlib import Path
import sys
import unittest
import unittest.mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'server'))
import servitor_server as ss  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import memory  # noqa: E402
import protocol  # noqa: E402
import speaker  # noqa: E402
import voice_profile  # noqa: E402

PRINTS = [dict(name='Ivan', print='AAAA')]


class TrustTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.trust = memory.Trust(clock=lambda: self.now, minutes=10, max_minutes=60)

    def test_window_slides_with_the_conversation_up_to_the_cap(self):
        self.assertIsNone(self.trust.current())
        self.trust.extend()                       # nothing to extend
        self.assertIsNone(self.trust.current())
        self.trust.confirm('Ivan')
        for _ in range(6):                        # a carried turn every 9 minutes
            self.now += 9 * 60
            self.assertEqual(self.trust.current(), 'Ivan')
            self.trust.extend()
        self.now += 9 * 60                        # 63 min after the sure one
        self.assertIsNone(self.trust.current())

    def test_a_pause_or_an_unknown_voice_ends_it(self):
        self.trust.confirm('Ivan')
        self.now += 10 * 60 + 1
        self.assertIsNone(self.trust.current())
        self.trust.confirm('Ivan')
        self.trust.clear()
        self.assertIsNone(self.trust.current())

    def test_switched_off_with_zero_minutes(self):
        trust = memory.Trust(clock=lambda: 0.0, minutes=0)
        trust.confirm('Ivan')
        self.assertIsNone(trust.current())


class TransportTests(unittest.TestCase):
    def test_trusted_needs_a_known_voiceprint_and_travels_beside_the_core(self):
        context = dict(facts=['x'], directives=[], history=[], voiceprints=PRINTS,
                       trusted='Ivan')
        self.assertEqual(memory.sanitize(context)['trusted'], 'Ivan')
        self.assertNotIn('trusted', memory.sanitize(dict(context, trusted='Mallory')))
        self.assertNotIn('trusted', memory.sanitize(dict(context, voiceprints=[])))
        core, _ = protocol.split_memory(context)
        self.assertNotIn('trusted', core)         # the digest stays stable
        session = protocol.ClientSession()
        session.start('s1')
        session.core = protocol.digest(core)
        slim = session.memory_payload(context)
        self.assertEqual(slim['trusted'], 'Ivan')
        server = protocol.Sessions()
        ident = server.open('pi')
        server.remember(ident, context)
        self.assertEqual(server.restore(ident, dict(slim, history=[]))['trusted'], 'Ivan')
        self.assertNotIn('trusted', server.restore(ident, dict(slim, trusted=None)))


class IdentifyTests(unittest.TestCase):
    def setUp(self):
        self.service = ss.Service.__new__(ss.Service)
        self.service.embedder = unittest.mock.Mock(available=True,
                                                   embed=lambda pcm: [1.0, 0.0])
        self.events = []

    def identify(self, copy, result):
        name, score = result
        decided = (dict(status=speaker.MATCHED, name=name, best=score, second=None) if name
                   else dict(status=speaker.UNKNOWN, name=None, best=score, second=None))
        with unittest.mock.patch.object(speaker, 'decide', return_value=decided), \
                unittest.mock.patch.object(speaker, 'speech_seconds', return_value=2.0), \
                unittest.mock.patch.object(speaker, 'decode', return_value=[1.0, 0.0]):
            return self.service._identify(b'\0' * 64000, copy, self.events.append)

    def test_short_question_of_a_vouched_person_counts_as_operator(self):
        copy = dict(facts=['Hund heißt Rex'], directives=[], history=[], voiceprints=PRINTS,
                    trusted='Ivan')
        result = self.identify(copy, (None, 0.38))
        self.assertEqual(result['speaker'], 'Ivan')
        self.assertEqual(result['facts'], ['Hund heißt Rex'])
        self.assertEqual(self.events[-1], dict(event='speaker', status=speaker.UNKNOWN,
                                               known=False, name=None, carried=True,
                                               score=0.38))

    def test_without_trust_an_unknown_voice_stays_a_guest(self):
        copy = dict(facts=['Hund heißt Rex'], directives=[], history=[], voiceprints=PRINTS)
        result = self.identify(copy, (None, 0.38))
        self.assertTrue(memory.unknown_speaker(result))
        self.assertEqual(result['facts'], [])
        self.assertFalse(self.events[-1]['carried'])

    def test_a_sure_match_names_the_person(self):
        copy = dict(facts=[], directives=[], history=[], voiceprints=PRINTS)
        self.assertEqual(self.identify(copy, ('Ivan', 0.61))['speaker'], 'Ivan')
        self.assertEqual(self.events[-1]['name'], 'Ivan')


    def test_v2_profiles_carry_the_profile_id(self):
        self.service.embedder.model_info = lambda: {}
        voice = [dict(profile_id='p-ivan', name='Ivan')]
        copy = dict(facts=['Hund heißt Rex'], directives=[], history=[], voiceprints=PRINTS,
                    trusted='Ivan')
        with unittest.mock.patch.object(voice_profile, 'decide', return_value=dict(
                status=voice_profile.UNKNOWN, best=0.3, profile_id=None)), \
                unittest.mock.patch.object(speaker, 'speech_seconds', return_value=2.0):
            result = self.service._identify(b'\0' * 64000, copy, self.events.append, voice)
        self.assertEqual((result['speaker'], result['speaker_id']), ('Ivan', 'p-ivan'))
        self.assertTrue(self.events[-1]['carried'])
        with unittest.mock.patch.object(voice_profile, 'decide', return_value=dict(
                status=voice_profile.UNKNOWN, best=0.3, profile_id=None)), \
                unittest.mock.patch.object(speaker, 'speech_seconds', return_value=2.0):
            result = self.service._identify(b'\0' * 64000, dict(copy, trusted=None),
                                            self.events.append, voice)
        self.assertTrue(memory.unknown_speaker(result))

if __name__ == '__main__':
    unittest.main()
