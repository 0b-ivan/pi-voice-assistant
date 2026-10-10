"""Explicitly long stories: detection, planning, transport, playback and stopping.

Real audio is simulated: section texts of the requested length and part
durations from a speaking rate, so a 20-minute story runs in milliseconds.
"""
from contextlib import redirect_stdout
import http.client
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'server'))

import llm  # noqa: E402
import story  # noqa: E402
import servitor_server as ss  # noqa: E402
import ptt  # noqa: E402
from ptt import VoiceController  # noqa: E402

TOKEN = 'x' * 40
WORDS = ('Die Leitung summte leise, während Ilse den Kanal prüfte und Billy auf das '
         'nächste Zeichen wartete').split()


def section_text(words, seed=0):
    """A section of about ``words`` words in full sentences, with a note."""
    out, sentence, n = [], [], 0
    while n < words:
        sentence.append(WORDS[(n + seed) % len(WORDS)])
        n += 1
        if len(sentence) == 12 or n == words:
            out.append(' '.join(sentence).capitalize() + '.')
            sentence = []
    return ' '.join(out) + f'\nNOTIZ: Abschnitt {seed} erzählt.'


class DetectionTests(unittest.TestCase):
    def test_explicitly_long_requests(self):
        cases = {
            'Erzähl mir eine lange Geschichte': 10.0,
            'erzähl ausführlich von deinem trupp': 10.0,
            'Erzähl mir etwa fünfzehn Minuten von Phobos': 15.0,
            'erzähl zwanzig minuten über deine familie und den wolfsjäger': 20.0,
            'Erzähl die alte Höllenläufer-Sage zehn Minuten lang': 10.0,
            'erzähl mir 12 minuten eine geschichte': 12.0,
            'erzähl eine halbe stunde lang': 30.0,
            'etc mir immer zehn minuten lang über deine drop': 10.0,   # real STT output
            'erzähl mir eine fünfminütige geschichte aus deiner vergangenheit': 5.0,
            'erzähl mir eine zehnminütige geschichte': 10.0,
            'erzähl eine 15 minütige geschichte': 15.0,
        }
        for text, minutes in cases.items():
            self.assertEqual(story.request(text)['minutes'], minutes, text)

    def test_ordinary_requests_stay_short(self):
        for text in ('Erzähl eine Geschichte', 'Erzähl mir einen Witz',
                     'Erklär das ausführlich', 'erkläre ausführlich die geschichte des internets',
                     'Wie lange dauert das?', 'Was hast du gesagt?', 'erzähl weiter',
                     'stell einen timer auf zehn minuten', 'wie spät ist es in zehn minuten'):
            self.assertIsNone(story.request(text), text)

    def test_default_and_maximum_are_configurable(self):
        with patch.dict(os.environ, {'STORY_DEFAULT_MINUTES': '12', 'STORY_MAX_MINUTES': '25'}):
            self.assertEqual(story.request('erzähl mir eine lange geschichte')['minutes'], 12.0)
            self.assertEqual(story.request('erzähl mir 40 minuten etwas')['minutes'], 25.0)


class PlanTests(unittest.TestCase):
    def tell(self, minutes, real_wpm=118):
        """Tell a whole story with a fake model and simulated audio: returns
        (state, number of sections, largest prompt, all spoken text)."""
        state = story.start(dict(minutes=minutes, topic='Erzähl von Phobos'), 'mensch',
                            'light', ['E15', 'E29', 'E14', 'E16', 'E21'])
        sections, largest, spoken = 0, 0, []
        while not state['done']:
            step = story.plan(state)
            messages = story.messages(state)
            largest = max(largest, sum(len(m['content']) for m in messages))
            self.assertNotIn('weitererzählen?', messages[1]['content'])
            text, note = story.split_note(section_text(step['words'], sections))
            state = story.advance(state, story.trim_incomplete(text), note)
            for part in story.split_parts(text):
                state['ms'] += int(len(part.split()) * 60000 / real_wpm)
            spoken.append(text)
            sections += 1
            self.assertLessEqual(sections, state['max_seg'])
        return state, sections, largest, spoken

    def test_10_15_20_minutes_reach_their_target(self):
        for minutes in (10, 15, 20):
            state, sections, largest, _ = self.tell(minutes)
            spoken_minutes = state['ms'] / 60000
            self.assertGreater(spoken_minutes, minutes * 0.85, minutes)
            self.assertLess(spoken_minutes, minutes * 1.2, minutes)
            self.assertGreaterEqual(sections, 3)
            self.assertLess(largest, 16000)    # context does not grow with the monologue
            self.assertLessEqual(len(state['told']), story.MAX_TOLD)

    def test_continuation_sends_state_not_monologue(self):
        state, _, _, spoken = self.tell(15)
        early = story.messages(dict(state, done=False, seg=1, told=state['told'][:1]))
        late = story.messages(dict(state, done=False, seg=state['seg'] - 1))
        size = lambda messages: sum(len(m['content']) for m in messages)   # noqa: E731
        self.assertLess(size(late) - size(early), 2500)     # bounded, not the whole monologue
        self.assertNotIn(spoken[0][:200], late[0]['content'] + late[1]['content'])
        self.assertIn('Schon erzählt', late[0]['content'])
        self.assertIn('ohne ihn zu wiederholen', late[1]['content'])

    def test_story_tokens_are_not_the_everyday_limit(self):
        state = story.start(dict(minutes=20, topic='x'), 'servitor', 'off', [])
        state['seg'] = 1
        self.assertGreater(story.max_tokens(story.plan(state)['words'], 1600), 180)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'k'}):
            with patch('llm._chat', return_value='Text') as chat:
                llm.generate_reply('x', story=state)
        self.assertGreater(chat.call_args.args[4]['max_completion_tokens'], 600)
        self.assertGreaterEqual(chat.call_args.args[3], 60)

    def test_parts_respect_the_synthesis_limit_and_lose_nothing(self):
        text = ' '.join(['Ein sehr langer Satz ohne Punkt'] * 80) + '. Kurz. ' + section_text(400)
        parts = story.split_parts(text, max_chars=300)
        self.assertTrue(all(len(p) <= 300 for p in parts))
        self.assertEqual(' '.join(' '.join(parts).split()), ' '.join(text.split()))
        self.assertLessEqual(len(story.split_parts(section_text(300))[0]), 320)

    def test_notes_and_cut_sentences(self):
        self.assertEqual(story.split_note('Er ging. NOTIZ: Weg.'), ('Er ging.', 'Weg.'))
        self.assertEqual(story.trim_incomplete('Erster Satz. Zweiter Satz. Und dann der'),
                         'Erster Satz. Zweiter Satz.')

    def test_billy_story_keeps_the_secret_and_servitor_the_names(self):
        billy = story.messages(story.start(dict(minutes=20, topic='Phobos'), 'mensch', 'full',
                                           ['E15', 'E29', 'E14', 'E16']))
        self.assertIn('weißt du nicht', billy[0]['content'])
        servitor = story.start(dict(minutes=20, topic='Phobos'), 'servitor', 'full',
                               ['E15', 'E29', 'E16'])
        for seg in range(servitor['planned']):
            text = story.messages(dict(servitor, seg=seg))[0]['content']
            for name in ('Dace', 'Ilse', 'Jonah', 'Mara'):
                self.assertNotIn(name, text.split('Erzählstand')[0].split('Grundregeln')[1])

    def test_a_model_that_writes_too_little_ends_with_a_note(self):
        state = story.start(dict(minutes=20, topic='x'), 'mensch', 'light', [])
        while not state['done']:
            state = story.advance(state, section_text(40), '')   # far too short
        self.assertLessEqual(state['seg'], state['max_seg'])
        self.assertTrue(state['short'])
        self.assertTrue(story.sanitize(state)['short'])

    def test_free_story_is_not_told_as_own_memory(self):
        free = story.messages(story.start(dict(minutes=10, topic='irgendwas'), 'mensch',
                                          'light', []))
        self.assertIn('nicht als deine eigene Erinnerung', free[0]['content'])
        self.assertNotIn('Du erzählst als Billy in der Ich-Form', free[0]['content'])
        own = story.messages(story.start(dict(minutes=10, topic='Trupp'), 'mensch', 'light',
                                         ['E12']))
        self.assertIn('Du erzählst als Billy in der Ich-Form', own[0]['content'])

    def test_sanitize(self):
        state = story.start(dict(minutes=10, topic='x'), 'mensch', 'light', ['E01'])
        self.assertEqual(story.sanitize(json.loads(json.dumps(state)))['ids'], ['E01'])
        for broken in (None, {}, dict(state, persona='x'), dict(state, words=-1),
                       dict(state, minutes=600), dict(state, v=2)):
            self.assertIsNone(story.sanitize(broken))
        self.assertEqual(story.sanitize(dict(state, ids=['E01', '../x']))['ids'], ['E01'])


class StoryPipeline:
    """Server pipeline double: sections of the requested length, short WAVs."""

    def __init__(self, workdir):
        self.workdir, self.calls = workdir, []

    def reply(self, text, lore=None, mode=None, memory=None, persona=None, mood=None,
              story=None):
        self.calls.append(story)
        if story is None:
            return 'Kurze Antwort.', 'test/model'
        return section_text(sys.modules['story'].plan(story)['words'], story['seg']), 'test/model'

    def _wav(self):
        fd, name = tempfile.mkstemp(suffix='.wav', dir=self.workdir)
        os.close(fd)
        with wave.open(name, 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(8000)
            out.writeframes(bytes(800))
        return Path(name)

    def recognizer(self):
        raise AssertionError('text turns only')

    def synthesize(self, text, voice='servitor'):
        return self._wav()

    def render(self, source, voice='servitor'):
        return self._wav()

    def encode(self, source, fmt):
        return Path(source).read_bytes()


class ServerStoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = ss.Config({'SERVITOR_API_TOKEN': TOKEN, 'SERVITOR_BIND': '127.0.0.1',
                            'SERVITOR_PORT': '0', 'SERVITOR_WORKDIR': self.tmp.name,
                            'SERVITOR_MAX_TEXT_CHARS': '400'})
        self.pipeline = StoryPipeline(self.tmp.name)
        self.service = ss.Service(config, self.pipeline)
        self.service.ready = True
        self.server = ss.Server(('127.0.0.1', 0), ss.Handler)
        self.server.service = self.service
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)

    def post(self, path, body, status):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=10)
        conn.request('POST', path, body=json.dumps(body).encode(), headers={
            'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json',
            'X-Servitor-Status': json.dumps(status)})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response.status, [json.loads(line) for line in data.splitlines() if line.strip()]

    def first_section(self, text, status):
        events = []

        class Recognizer:
            def accept_pcm(self, pcm):
                pass

            def finish(self):
                return text
        self.pipeline.recognizer = lambda: Recognizer()
        self.service.run_turn(iter([b'\1' * 16000]), events.append, 'wav', device=status)
        return events

    def test_long_story_turn_and_continuation(self):
        status = dict(persona='mensch', lore='light', story='on', voice='natural')
        events = self.first_section('erzähl mir zwanzig minuten über phobos', status)
        kinds = [e['event'] for e in events]
        self.assertEqual(kinds[-1], 'done')
        parts = [e for e in events if e['event'] == 'audio']
        self.assertGreaterEqual(len(parts), 2)                # spoken early, in parts
        self.assertTrue(all(len(p['text']) <= 400 for p in parts))
        self.assertEqual([p['part'] for p in parts], list(range(len(parts))))
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertNotIn('NOTIZ', reply['text'])
        state = next(e for e in events if e['event'] == 'story')['state']
        self.assertEqual((state['minutes'], state['seg'], state['done']), (20.0, 1, False))
        self.assertIn('E16', state['ids'])
        sections = 1
        while not state['done']:
            code, events = self.post('/v1/story?format=wav', dict(story=state), status)
            self.assertEqual(code, 200)
            self.assertEqual(events[-1]['event'], 'done', events[-1])
            state = next(e for e in events if e['event'] == 'story')['state']
            sections += 1
            self.assertLessEqual(sections, state['max_seg'])
        self.assertGreaterEqual(sections, 4)
        code, events = self.post('/v1/story?format=wav', dict(story=state), status)
        self.assertEqual(events[-1]['event'], 'error')       # a finished story stays finished

    def test_client_uplink_and_job_receive_parts_and_state(self):
        from remote_turn import RemoteConfig, RemoteStoryUplink, RemoteTurnJob
        config = RemoteConfig(base_urls=(f'http://127.0.0.1:{self.server.server_address[1]}',),
                              token=TOKEN)
        state = story.start(dict(minutes=10, topic='Erzähl von Phobos'), 'mensch', 'light',
                            ['E16'])
        state['seg'], state['words'] = 1, 140
        uplink = RemoteStoryUplink(config, state, status=dict(story='on', persona='mensch'),
                                   timeout=10)
        job = RemoteTurnJob(uplink, self.tmp.name, idle_timeout=10)
        self.assertTrue(job.done.wait(10))
        self.assertIsNone(job.error)
        self.assertTrue(job.result['story'])
        self.assertEqual(job.story['seg'], 2)
        paths = [item['path'] for item in job.drain() if item.get('path')]
        self.assertEqual(len(paths), job.parts)
        self.assertEqual(len(set(paths)), len(paths))           # one file per part
        self.assertTrue(all(Path(p).exists() for p in paths))

    def test_older_pi_gets_an_honest_short_answer(self):
        events = self.first_section('erzähl mir eine lange geschichte',
                                    dict(persona='servitor', lore='light'))
        reply = next(e for e in events if e['event'] == 'reply')
        self.assertEqual(reply['text'], story.OLD_DEVICE['servitor'])
        self.assertEqual(len([e for e in events if e['event'] == 'audio']), 1)
        self.assertEqual(self.pipeline.calls, [])            # no story generated at all

    def test_everyday_turn_is_unchanged(self):
        events = self.first_section('wie hoch ist der eiffelturm', dict(story='on'))
        self.assertEqual([e for e in events if e['event'] == 'story'], [])
        self.assertEqual(self.pipeline.calls, [None])

    def test_bad_story_state_is_refused(self):
        code, _ = self.post('/v1/story', dict(story=dict(v=1, persona='x')), {})
        self.assertEqual(code, 400)


class FakeStoryJob:
    """Stands in for RemoteTurnJob on /v1/story: one section, finished at once."""
    made = []
    fail_at = None

    def __init__(self, state, directory, idle_timeout=None):
        FakeStoryJob.made.append(self)
        self.cancelled = False
        self.done = threading.Event()
        self.error = self.error_stage = self.error_code = None
        self.story = None
        self._items = []
        if FakeStoryJob.fail_at is not None and state['seg'] >= FakeStoryJob.fail_at:
            self.error, self.error_stage, self.error_code = 'timeout', 'think', 'llm'
        else:
            self._items, self.story = make_section(state, directory)
        self.done.set()

    def drain(self):
        items, self._items = self._items, []
        return items

    def cancel(self):
        self.cancelled = True


def make_section(state, directory, wpm=118):
    """Events of one server section with WAV files and simulated durations."""
    words = story.plan(state)['words']
    text, note = story.split_note(section_text(words, state['seg']))
    state = story.advance(state, text, note)
    items = [dict(event='reply', text=text, model='test')]
    for index, part in enumerate(story.split_parts(text)):
        path = Path(directory) / f'part-{state["seg"]}-{index}.wav'
        path.write_bytes(b'RIFF')
        ms = int(len(part.split()) * 60000 / wpm)
        state['ms'] += ms
        items.append(dict(event='audio', part=index, path=str(path), text=part, duration_ms=ms))
    return items, state


class ControllerStoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            'PI_DISPLAY_PROGRESS_FILE': str(Path(self.tmp.name) / 'progress.json'),
            'PTT_DISPLAY_EVENT_PATH': str(Path(self.tmp.name) / 'event.json'),
            'PTT_RUNTIME_DIR': self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.recorder, self.speech = Mock(), Mock()
        self.recorder.process = None
        self.speech.active = False
        self.speech.synthesizing = False
        self.speech.poll.return_value = None
        self.c = VoiceController(self.recorder, self.speech, .04, 30, remote=True)
        self.c.story_uplink_factory = lambda state: state
        self.output = StringIO()
        context = redirect_stdout(self.output)
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        FakeStoryJob.made, FakeStoryJob.fail_at = [], None
        job = patch.object(ptt, 'RemoteTurnJob', FakeStoryJob)
        job.start()
        self.addCleanup(job.stop)

    def events(self, name):
        return [json.loads(line) for line in self.output.getvalue().splitlines()
                if line.startswith('{') and json.loads(line).get('event') == name]

    def start_story(self, minutes):
        state = story.start(dict(minutes=minutes, topic='Erzähl von Phobos'), 'mensch',
                            'light', ['E15', 'E16'])
        items, state = make_section(state, self.tmp.name)
        for item in items:
            self.c._remote_progress(item)
        self.c._remote_progress(dict(event='story', state=state))
        return state

    def run_ticks(self, limit=400):
        """Each tick ends the part that was playing (simulated playback)."""
        for _ in range(limit):
            self.c._story_tick()
            if self.c.story is None:
                return
        self.fail('story did not finish')

    def test_10_15_20_minutes_play_through_without_asking(self):
        for minutes in (10, 15, 20):
            self.output.truncate(0)
            self.output.seek(0)
            self.speech.reset_mock()
            self.start_story(minutes)
            self.run_ticks()
            finished = self.events('story_finished')[-1]
            self.assertFalse(finished['failed'])
            self.assertGreater(finished['played_ms'], minutes * 60000 * 0.85, minutes)
            self.assertLess(finished['played_ms'], minutes * 60000 * 1.2, minutes)
            self.speech.start.assert_not_called()             # no "weiter?" question
            played = [c.args[0] for c in self.speech.play.call_args_list]
            self.assertEqual(len(played), len(set(played)))    # no part twice
            self.assertEqual(list(Path(self.tmp.name).glob('part-*')), [])  # all cleaned up

    def test_queue_stays_bounded(self):
        self.start_story(20)
        self.speech.active = True                             # first part keeps playing
        for _ in range(50):
            self.c._story_tick()
        self.assertLessEqual(len(self.c.story.queue), story.MAX_PARTS)
        self.assertLessEqual(len(FakeStoryJob.made), 2)       # no fetching far ahead

    def test_button_b_stops_generation_synthesis_and_playback(self):
        self.start_story(20)
        for _ in range(5):
            self.c._story_tick()
        self.c.cancel(False, 1.0)
        self.assertIsNone(self.c.story)
        self.assertTrue(all(job.cancelled or job.done.is_set() for job in FakeStoryJob.made))
        made = len(FakeStoryJob.made)
        plays = self.speech.play.call_count
        for _ in range(20):
            self.c._story_tick()
        self.assertEqual((len(FakeStoryJob.made), self.speech.play.call_count), (made, plays))
        self.assertEqual(list(Path(self.tmp.name).glob('part-*')), [])
        self.assertEqual(self.events('story_stopped')[-1]['reason'], 'button')

    def test_push_to_talk_replaces_the_story(self):
        self.start_story(10)
        self.c._story_tick()
        self.c.tick(False, (False,) * 5, 4.0)
        self.c.tick(False, (False,) * 5, 4.5)
        self.c.tick(True, (False,) * 5, 5.0)                  # PTT pressed
        self.c.tick(True, (False,) * 5, 5.2)
        self.assertIsNone(self.c.story)
        self.assertEqual(self.events('story_stopped')[-1]['reason'], 'new_turn')

    def test_failure_is_reported_once_and_never_restarts(self):
        FakeStoryJob.fail_at = 2
        self.start_story(20)
        self.run_ticks()
        notices = [c.args[0] for c in self.speech.start.call_args_list]
        self.assertEqual(notices, [story.ABORTED['mensch']])
        self.assertTrue(self.events('story_finished')[-1]['failed'])
        self.assertEqual(len(FakeStoryJob.made), 2)           # no retry from the start

    def test_alarm_waits_for_a_part_boundary_then_goes_first(self):
        self.start_story(10)
        self.c._story_tick()                                  # first part plays
        plays = self.speech.play.call_count
        self.c.alarm_queue = ['Warnung. Akku bei 9 Prozent.']
        with patch.object(self.c, '_speak_alarms'):
            self.c._story_tick()
        self.assertEqual(self.speech.play.call_count, plays)   # the story waits

    def test_local_route_tells_the_story_with_local_speech(self):
        state = story.start(dict(minutes=10, topic='x'), 'servitor', 'off', [])
        self.c._story_state(state, remote=False)
        with patch.object(ptt, 'generate_reply',
                          side_effect=lambda topic, **kw: (section_text(
                              story.plan(kw['story'])['words'], kw['story']['seg']), 'm')):
            for _ in range(200):
                self.c._story_tick()
                if self.c.story is not None and self.c.story.job is not None:
                    self.c.story.job.thread.join(2)
                if self.c.story is None:
                    break
        self.assertIsNone(self.c.story)
        self.assertGreater(self.speech.start.call_count, 5)
        self.assertNotIn('NOTIZ', ' '.join(c.args[0] for c in self.speech.start.call_args_list))


if __name__ == '__main__':
    unittest.main()
