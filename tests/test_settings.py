import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from remote_turn import RemoteCapableSpeech  # noqa: E402
from settings import Settings  # noqa: E402


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'settings.json'

    def test_save_merges_and_load_reads_back(self):
        store = Settings(self.path)
        self.assertEqual(store.load(), {})
        self.assertTrue(store.save(persona='mensch'))
        self.assertTrue(store.save(lore='full'))
        self.assertEqual(store.load(), {'persona': 'mensch', 'lore': 'full'})
        self.assertEqual(json.loads(self.path.read_text()), {'lore': 'full', 'persona': 'mensch'})
        self.assertFalse(self.path.with_name('settings.json.tmp').exists())

    def test_broken_or_foreign_content_reads_as_empty(self):
        for text in ('{kaputt', '[1, 2]', ''):
            self.path.write_text(text)
            self.assertEqual(Settings(self.path).load(), {})

    def test_missing_state_directory_is_not_created(self):
        store = Settings(Path(self.tmp.name) / 'fehlt' / 'settings.json')
        self.assertFalse(store.save(persona='mensch'))
        self.assertFalse((Path(self.tmp.name) / 'fehlt').exists())


class RemoteSpeechEffectTests(unittest.TestCase):
    def test_effect_reaches_the_local_output(self):
        local = Mock()
        local.effect = 'servitor'
        speech = RemoteCapableSpeech(local, 'device')
        speech.effect = 'natural'
        self.assertEqual((speech.effect, local.effect), ('natural', 'natural'))

    def test_output_without_effect_is_left_alone(self):
        local = Mock(spec=['start', 'stop'])
        speech = RemoteCapableSpeech(local, 'device')
        speech.effect = 'natural'
        self.assertIsNone(speech.effect)
        self.assertFalse(hasattr(local, 'effect'))


if __name__ == '__main__':
    unittest.main()
