import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import face  # noqa: E402
import llm  # noqa: E402
from mood import HALF_LIFE, Mood, emergency, prompt_section, split_tag  # noqa: E402


class MoodTests(unittest.TestCase):
    def test_feelings_fade_to_neutral(self):
        mood = Mood()
        mood.feel('freudig', 0.8, 100)
        self.assertEqual(mood.current(100), ('freudig', 0.8))
        self.assertAlmostEqual(mood.current(100 + HALF_LIFE)[1], 0.4)
        self.assertEqual(mood.current(100 + 4 * HALF_LIFE), ('neutral', 0.0))

    def test_a_weaker_feeling_only_dents_a_stronger_one(self):
        mood = Mood()
        mood.feel('gereizt', 0.6, 0)
        mood.feel('zufrieden', 0.2, 0)
        self.assertEqual(mood.current(0)[0], 'gereizt')
        self.assertAlmostEqual(mood.current(0)[1], 0.5)
        mood.feel('zufrieden', 0.7, 0)
        self.assertEqual(mood.current(0), ('zufrieden', 0.7))

    def test_off_means_always_neutral(self):
        mood = Mood(enabled=False)
        mood.hear('du blöde blechdose', 0)
        self.assertEqual(mood.current(0), ('neutral', 0.0))
        self.assertEqual(mood.turn('x', 0), dict(emotion='neutral', level=0, refuse=False))

    def test_what_the_operator_says(self):
        mood = Mood()
        mood.hear('Danke, gut gemacht!', 0)
        self.assertEqual(mood.current(0)[0], 'zufrieden')
        mood.hear('super', 1)
        self.assertEqual(mood.current(1)[0], 'freudig')
        mood = Mood()
        mood.hear('warum ist der himmel blau', 0)
        self.assertEqual(mood.current(0)[0], 'neugierig')
        mood.hear('warum ist der himmel blau', 30)        # asked once more: not understood
        self.assertNotEqual(mood.current(30)[0], 'gereizt')
        mood.hear('warum ist der himmel blau', 40)        # again and again: that annoys
        self.assertEqual(mood.current(40)[0], 'gereizt')
        mood = Mood()
        for second, text in enumerate(('nochmal', 'nochmal', 'nochmal bitte', 'wie bitte')):
            mood.hear(text, second)                       # asking to hear it again never does
        self.assertNotEqual(mood.current(5)[0], 'gereizt')
        mood = Mood()
        mood.hear('du nutzloser schrotthaufen', 0)
        self.assertEqual((mood.current(0)[0], mood.cause), ('gereizt', 'user'))

    def test_what_the_unit_feels(self):
        mood = Mood()
        for second in range(0, 60, 10):
            mood.sense(dict(battery_pct=8), second)
        self.assertEqual(mood.current(60)[0], 'müde')
        mood = Mood()
        for second in range(0, 60, 10):
            mood.sense(dict(battery_pct=8, battery_plugged=True, server='down'), second)
        self.assertEqual(mood.current(60)[0], 'besorgt')
        mood = Mood()
        mood.woke_up(7200, 0)
        self.assertEqual(mood.current(0)[0], 'gelangweilt')

    def test_restart_baseline_from_newest_turns(self):
        now = 100000
        history = [dict(q='a', a='b', at=now - 20 * 3600, mood='freudig:0.9'),   # too old
                   dict(q='a', a='b', at=now - 600, mood='gereizt:0.8'),
                   dict(q='a', a='b', at=now - 60, mood='gereizt:0.9'),
                   dict(q='a', a='b', at=now - 30)]                              # no mood
        mood = Mood()
        mood.baseline(history, now)
        emotion, level = mood.current(now)
        self.assertEqual(emotion, 'gereizt')
        self.assertLessEqual(level, 0.3)
        mood = Mood()
        mood.baseline(history[:1], now)
        self.assertEqual(mood.current(now), ('neutral', 0.0))

    def test_refusal_needs_user_made_irritation_and_alternates(self):
        mood = Mood()
        mood.hear('du dummer idiot', 0)
        mood.hear('du dummer idiot, nutzlos', 1)
        self.assertGreaterEqual(mood.current(1)[1], 0.7)
        self.assertTrue(mood.turn('wie weit ist der mond weg', 2)['refuse'])
        self.assertFalse(mood.turn('wie weit ist der mond weg', 3)['refuse'])   # asked again
        self.assertTrue(mood.turn('und jetzt', 4)['refuse'])
        self.assertFalse(mood.turn('hilfe es brennt', 5)['refuse'])
        heat = Mood()
        heat.feel('gereizt', 0.9, 0, cause='system')
        self.assertFalse(heat.turn('hallo', 0)['refuse'])
        self.assertTrue(emergency('Ruf einen Krankenwagen'))
        self.assertFalse(emergency('wie spät ist es'))


class PromptTests(unittest.TestCase):
    def test_tag_is_removed(self):
        self.assertEqual(split_tag('[stimmung:gereizt] Nein.'), ('Nein.', 'gereizt'))
        self.assertEqual(split_tag('Ja. [Stimmung: Freudig]'), ('Ja.', 'freudig'))
        self.assertEqual(split_tag('[stimmung:wütend] Nein.'), ('Nein.', None))
        self.assertEqual(split_tag('Nur Text.'), ('Nur Text.', None))

    def test_sections_per_persona(self):
        self.assertIsNone(prompt_section('servitor', None))
        neutral = prompt_section('mensch', dict(emotion='neutral', level=0, refuse=False))
        self.assertIn('[stimmung:freudig]', neutral)
        self.assertNotIn('Aktuelle Stimmung', neutral)
        billy = prompt_section('mensch', dict(emotion='gereizt', level=80, refuse=True))
        self.assertIn('sehr gereizt', billy)
        self.assertIn('ablehnen', billy)
        servitor = prompt_section('servitor', dict(emotion='freudig', level=50, refuse=False))
        self.assertIn('kein Engramm-Durchbruch', servitor)   # only when lore grants one
        self.assertNotIn('ablehnen', servitor)
        granted = prompt_section('servitor', dict(emotion='besorgt', level=50, refuse=False),
                                 dict(fragment='Dace hätte darüber gelacht.'))
        self.assertIn('Dace hätte darüber gelacht.', granted)
        self.assertIn('Rückkehr zur mechanischen Ausgabe', granted)
        prompt = llm.system_prompt('off', persona='mensch',
                                   mood=dict(emotion='müde', level=30, refuse=False))
        self.assertIn('leicht müde', prompt)
        self.assertTrue(prompt.rstrip().split('\n')[-1].startswith('Aktueller Zeitpunkt'))
        self.assertNotIn('stimmung:', llm.system_prompt('off', persona='mensch'))


class FaceAndDisplayTests(unittest.TestCase):
    def test_billy_shows_the_mood_when_idle(self):
        self.assertEqual(face.choose('BEREIT', 0.1, mood=('gereizt', 0.8)), (0, 'teeth'))
        self.assertEqual(face.choose('BEREIT', 1.0, mood=('freudig', 0.6)), (0, 'grin'))
        self.assertEqual(face.choose('BEREIT', 3.0, mood=('müde', 0.6)), (0, 'squint'))
        self.assertIn(face.choose('BEREIT', 3.0, mood=('besorgt', 0.6))[1], ('look_a', 'look_b'))
        weak = face.choose('BEREIT', 3.0, mood=('gereizt', 0.1))
        self.assertNotEqual(weak[1], 'teeth')
        self.assertEqual(face.choose('SPRECHEN', 0, 0.9, mood=('gereizt', 0.9)), (0, 'ouch'))

    def test_servitor_glitch_only_when_strong(self):
        strong = dict(mood='gereizt', mood_level=70)
        self.assertEqual(display.mood_now(strong), ('gereizt', 0.7))
        self.assertTrue(display.engram_error(strong, 30.2))
        self.assertFalse(display.engram_error(strong, 31.0))
        self.assertFalse(display.engram_error(dict(mood='gereizt', mood_level=30), 30.2))
        self.assertFalse(display.engram_error(dict(strong, opt_emotions='off'), 30.2))
        self.assertIsNone(display.mood_now(dict(mood='neutral')))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text(json.dumps(dict(mood='gereizt', mood_level=70, opt_emotions='on')))
            self.assertEqual({k: v for k, v in display.read_status(path).items()
                              if k.startswith(('mood', 'opt_emotions'))},
                             dict(mood='gereizt', mood_level=70, opt_emotions='on'))


class ControllerMoodTests(unittest.TestCase):
    def setUp(self):
        import ptt
        self.ptt = ptt
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict('os.environ', {
            'PTT_DISPLAY_STATUS_PATH': str(Path(self.tmp.name) / 'status.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json'),
            'PTT_SETTINGS_FILE': str(Path(self.tmp.name) / 'settings.json')})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        recorder, speech = Mock(), Mock()
        recorder.process = None
        recorder.take_live_transcript.return_value = None
        speech.active = False
        speech.poll.return_value = None
        self.c = ptt.VoiceController(recorder, speech, .04, 30)

    def test_local_reply_tag_is_stripped_and_felt(self):
        self.c.memory = Mock()
        self.c.job, self.c.job_stage = Mock(cancelled=False, error=None,
                                            result=('[stimmung:freudig] Gern.', 'm')), 'llm'
        self.c.job.done.is_set.return_value = True
        with patch.object(self.ptt, 'print'):
            self.c.tick(False, (False,) * 5, 1.0)
        self.c.speech.start.assert_called_once_with('Gern.')
        self.assertEqual(self.c.mood.current(__import__('time').time())[0], 'freudig')
        self.assertTrue(self.c.memory.remember_turn.call_args.kwargs['mood']
                        .startswith('freudig:'))
        self.assertEqual(self.ptt._display_status.get('mood'), 'freudig')

    def test_turn_snapshot_and_menu_switch(self):
        self.c.mood.hear('du dummer nutzloser idiot', __import__('time').time())
        self.c.mood.hear('idiot', __import__('time').time())
        snapshot = self.c.turn_snapshot()
        self.assertEqual((snapshot['mood'], snapshot['mood_refuse']), ('gereizt', 'on'))
        self.c.menu.select('emotions')
        self.c._menu_confirm(0)
        self.assertFalse(self.c.mood.enabled)
        snapshot = self.c.turn_snapshot()
        self.assertNotIn('mood', snapshot)
        self.assertEqual(self.ptt._display_status.get('opt_emotions'), 'off')
        self.assertEqual(json.loads((Path(self.tmp.name) / 'settings.json').read_text())
                         ['emotions'], 'off')


if __name__ == '__main__':
    unittest.main()
