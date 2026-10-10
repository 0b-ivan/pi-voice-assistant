import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ptt_config import ConfigError, PttConfig  # noqa: E402


class PttConfigTests(unittest.TestCase):
    def test_empty_environment_gives_the_defaults(self):
        self.assertEqual(PttConfig.from_env({}), PttConfig())

    def test_values_are_parsed(self):
        config = PttConfig.from_env({
            'PTT_GPIO_LINE': '27', 'PTT_ACTIVE_LOW': '0', 'PTT_DEBOUNCE_MS': '20',
            'PTT_BUTTON_SHIM': '1', 'PTT_PITFT_BUTTONS': '', 'PTT_RUNTIME_DIR': '/tmp/x',
            'PTT_MEMORY_MODE': 'hybrid', 'STT_PROVIDER': ' Vosk ', 'PTT_WAKE_WORD': ' proximus ',
            'PTT_WAKE_SHADOW': 'a, ,b', 'PTT_LLM_MODE': ' LOCAL ', 'PTT_SLEEP_WLAN': 'off',
            'PTT_ALARMS': '0', 'PTT_CUE': '0', 'PTT_BLUETOOTH': '0', 'PTT_WLAN': ' Off',
            'PTT_PERSONA': 'mensch'})
        self.assertEqual(config.gpio_line, 27)
        self.assertFalse(config.active_low)
        self.assertAlmostEqual(config.debounce, 0.02)
        self.assertTrue(config.button_shim)
        self.assertEqual(config.pitft_buttons, ())
        self.assertEqual(config.runtime_dir, Path('/tmp/x'))
        self.assertTrue(config.hybrid and config.isolated_capture)
        self.assertEqual(config.stt_provider, 'vosk')
        self.assertEqual(config.wake_word, 'proximus')
        self.assertEqual(config.wake_shadow, ('a', 'b'))
        self.assertEqual(config.llm_mode, 'local')
        self.assertTrue(config.sleep_wlan_off)
        self.assertFalse(config.alarms or config.cue or config.bluetooth)
        self.assertEqual(config.wlan, 'off')
        self.assertEqual(config.persona, 'mensch')
        self.assertIsNone(config.lore)

    def test_unknown_llm_mode_falls_back_to_auto(self):
        self.assertEqual(PttConfig.from_env({'PTT_LLM_MODE': 'cloud'}).llm_mode, 'auto')

    def test_invalid_values_name_the_variable(self):
        cases = {
            'PTT_ACTIVE_LOW': ('yes', 'PTT_ACTIVE_LOW must be 0 or 1'),
            'PTT_BUTTON_SHIM': ('2', 'PTT_BUTTON_SHIM must be 0 or 1'),
            'PTT_MAX_SECONDS': ('nan', 'PTT_MAX_SECONDS must be finite, between 1 and 120'),
            'PTT_DEBOUNCE_MS': ('5', 'PTT_DEBOUNCE_MS must be finite, between 10 and 500'),
            'PTT_MEMORY_MODE': ('tiny', 'PTT_MEMORY_MODE must be resident, isolated or hybrid'),
            'PTT_PITFT_BUTTONS': ('17,24', 'PTT_PITFT_BUTTONS must be two GPIO lines'),
            'PTT_GPIO_LINE': ('x', 'PTT_GPIO_LINE must be a number'),
            'PTT_REST_SECONDS': ('soon', 'PTT_REST_SECONDS must be a number'),
        }
        for name, (value, message) in cases.items():
            with self.subTest(name=name), self.assertRaisesRegex(ConfigError, message):
                PttConfig.from_env({name: value})

    def test_pitft_buttons_need_two_lines(self):
        with self.assertRaises(ConfigError):
            PttConfig.from_env({'PTT_PITFT_BUTTONS': '23'})
        with self.assertRaises(ConfigError):
            PttConfig.from_env({'PTT_PITFT_BUTTONS': '23,b'})


if __name__ == '__main__':
    unittest.main()
