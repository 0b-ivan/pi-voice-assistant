import importlib.util
import math
import sys
import unittest
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import whisper_stt  # noqa: E402
from transcribe import NoSpeechError  # noqa: E402


class FakeVosk:
    def __init__(self, text):
        self.text, self.pcm = text, b''

    def accept_pcm(self, pcm):
        self.pcm += pcm

    def finish(self):
        if not self.text:
            raise NoSpeechError('nothing')
        return self.text


class HybridTests(unittest.TestCase):
    def hybrid(self, vosk_text, whisper_text=None, quick=None, error=None):
        calls = []

        def transcribe(pcm):
            calls.append(pcm)
            if error:
                raise error
            return whisper_text

        logged = []
        recognizer = whisper_stt.HybridRecognizer(
            quick=quick, vosk=FakeVosk(vosk_text), transcribe=transcribe,
            log=lambda line, flush=False: logged.append(line))
        recognizer.accept_pcm(b'\1\0' * 10)
        recognizer.accept_pcm(b'\2\0' * 10)
        return recognizer, calls, logged

    def test_whisper_text_replaces_vosk_and_both_are_logged(self):
        recognizer, calls, logged = self.hybrid('still einen keine', 'Stell einen Timer.')
        self.assertEqual(recognizer.finish(), 'Stell einen Timer.')
        self.assertEqual(calls, [b'\1\0' * 10 + b'\2\0' * 10])   # whole take
        self.assertIn('stt_compare', logged[0])
        self.assertIn('still einen keine', logged[0])

    def test_quick_vosk_text_skips_whisper(self):
        recognizer, calls, _ = self.hybrid('wie spät ist es', 'Wie spät ist es?',
                                           quick=lambda text: True)
        self.assertEqual(recognizer.finish(), 'wie spät ist es')
        self.assertEqual(calls, [])

    def test_vosk_only_mode_for_passphrases(self):
        recognizer, calls, _ = self.hybrid('omnissiah segne', 'Omnissiah, segne!')
        recognizer.whisper = False
        self.assertEqual(recognizer.finish(), 'omnissiah segne')
        self.assertEqual(calls, [])

    def test_vosk_hearing_nothing_ends_the_turn_without_whisper(self):
        recognizer, calls, _ = self.hybrid('', 'Untertitel im Auftrag des ZDF')
        with self.assertRaises(NoSpeechError):
            recognizer.finish()
        self.assertEqual(calls, [])

    def test_whisper_failure_or_nothing_usable_keeps_vosk(self):
        recognizer, _, logged = self.hybrid('wie hoch ist der eiffelturm',
                                            error=RuntimeError('boom'))
        self.assertEqual(recognizer.finish(), 'wie hoch ist der eiffelturm')
        self.assertIn('whisper_error', logged[0])
        recognizer, _, _ = self.hybrid('wie hoch ist der eiffelturm', '')
        self.assertEqual(recognizer.finish(), 'wie hoch ist der eiffelturm')

    def test_inventions_and_prompt_echo_are_dropped(self):
        self.assertEqual(whisper_stt.usable(' Untertitel der Amara.org-Community '), '')
        self.assertEqual(whisper_stt.usable('Vielen Dank fürs Zuschauen!'), '')
        self.assertEqual(whisper_stt.usable('Proximus, Servitor.'), '')
        self.assertEqual(whisper_stt.usable(' ... '), '')
        self.assertEqual(whisper_stt.usable(' Proximus,  wie spät ist es? '),
                         'Proximus, wie spät ist es?')

    def test_enabled_only_with_setting(self):
        self.assertFalse(whisper_stt.enabled({}))
        self.assertTrue(whisper_stt.enabled({'SERVITOR_STT': ' Whisper '}))


def load_mic_check():
    spec = importlib.util.spec_from_file_location('mic_check', ROOT / 'scripts' / 'mic-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tone(seconds, amplitude, rate=16000):
    return [int(amplitude * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate))]


class MicCheckTests(unittest.TestCase):
    def setUp(self):
        self.mic = load_mic_check()

    def test_good_level(self):
        samples = array('h', tone(2, 30) + tone(4, 6000))
        result = self.mic.analyze(samples, quiet_seconds=2)
        self.assertAlmostEqual(result['speech'], 20 * math.log10(6000 / math.sqrt(2) / 32768), delta=1)
        self.assertGreater(result['snr'], 40)
        self.assertEqual(result['clipped'], 0)
        self.assertIn('Pegel ok', self.mic.verdict(result)[0])

    def test_clipping_and_quiet_are_reported(self):
        loud = self.mic.analyze(array('h', tone(2, 30) + tone(4, 32767)),
                                quiet_seconds=2)
        self.assertIn('ZU LAUT', self.mic.verdict(loud)[0])
        quiet = self.mic.analyze(array('h', tone(2, 30) + tone(4, 300)), quiet_seconds=2)
        self.assertIn('ZU LEISE', self.mic.verdict(quiet)[0])
        noisy = self.mic.analyze(array('h', tone(2, 2000) + tone(4, 4000)), quiet_seconds=2)
        self.assertTrue(any('Rauschabstand knapp' in tip for tip in self.mic.verdict(noisy)))

    def test_take_without_known_quiet_part(self):
        result = self.mic.analyze(array('h', tone(1, 30) + tone(1, 6000) + tone(3, 30)))
        self.assertGreater(result['snr'], 40)


if __name__ == '__main__':
    unittest.main()
