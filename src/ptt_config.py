"""Settings of the voice service, read once from the environment (config/ptt.env).

``PttConfig.from_env()`` parses and checks every PTT_*/TTS_* value that ptt.py
uses, so main() and VoiceController never read os.environ themselves. Invalid
values raise ConfigError with the text main() hands to argparse.

Lore level, persona, voice effect and emotions are only defaults: menu choices
saved in settings.json win, so they are kept as the raw text (None when unset)
and normalised by the controller.
"""
from dataclasses import dataclass
import math
import os
from pathlib import Path

DEFAULT_AUDIO_DEVICE = 'plughw:CARD=wm8960soundcard,DEV=0'
MEMORY_MODES = ('resident', 'isolated', 'hybrid')
LLM_MODES = ('auto', 'free', 'local')  # OpenRouter default, low-restriction, local only
REST_SECONDS = 30.0
SLEEP_SECONDS = 600.0


class ConfigError(ValueError):
    """An environment value ptt.py cannot run with."""


@dataclass(frozen=True)
class PttConfig:
    # Buttons
    gpio_chip: str = '/dev/gpiochip0'
    gpio_line: int = 17
    active_low: bool = True
    debounce: float = 0.04              # seconds (PTT_DEBOUNCE_MS / 1000)
    max_seconds: float = 30.0
    button_shim: bool = False
    pitft_buttons: tuple = (23, 24)     # upper, lower; () disables them
    # Audio and processing
    runtime_dir: Path = Path('/run/pi-ptt')
    memory_mode: str = 'resident'
    stt_provider: str = 'vosk'
    capture_device: str = DEFAULT_AUDIO_DEVICE
    output_device: str = DEFAULT_AUDIO_DEVICE
    speak_command: str = '/usr/bin/python3 /opt/pi-voice-assistant/src/speak.py'
    voice_profile: str = 'normal'
    piper_model: str = '/opt/pi-voice-assistant/tts/de_DE-thorsten-low.onnx'
    servitor_model: str = '/opt/pi-voice-assistant/tts/de_DE-thorsten_emotional-medium.onnx'
    piper_venv: Path = Path('/opt/pi-voice-assistant/.venv')
    cue: bool = True
    # Wake word
    wake_word: str = ''
    wake_model_dir: Path = Path('/opt/pi-voice-assistant/models/wakeword')
    wake_threshold: float = 0.5
    wake_shadow: tuple = ('proximus',)
    # Defaults for menu choices (see module docstring)
    lore: str = None
    persona: str = None
    voice_effect: str = None
    emotions: str = 'on'
    # Behaviour
    llm_mode: str = 'auto'
    alarms: bool = True
    rest_seconds: float = REST_SECONDS
    sleep_seconds: float = SLEEP_SECONDS
    sleep_wlan_off: bool = False
    wlan: str = ''                      # 'on' | 'off' at start; '' leaves it as is
    bluetooth: bool = True

    @property
    def hybrid(self):
        return self.memory_mode == 'hybrid'

    @property
    def isolated_capture(self):
        """Capture and STT run in child processes (isolated, hybrid)."""
        return self.memory_mode in ('isolated', 'hybrid')

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def get(name, default=''):
            return env.get(name, default)

        def number(name, default, kind=float):
            try:
                value = kind(get(name, default))
            except ValueError:
                raise ConfigError(f'{name} must be a number') from None
            return value

        def flag(name, default):
            value = get(name, default)
            if value not in ('0', '1'):
                raise ConfigError(f'{name} must be 0 or 1')
            return value == '1'

        gpio_line = number('PTT_GPIO_LINE', '17', int)
        max_seconds = number('PTT_MAX_SECONDS', '30')
        if not math.isfinite(max_seconds) or not 1 <= max_seconds <= 120:
            raise ConfigError('PTT_MAX_SECONDS must be finite, between 1 and 120')
        debounce = number('PTT_DEBOUNCE_MS', '40') / 1000
        if not math.isfinite(debounce) or not 0.01 <= debounce <= 0.5:
            raise ConfigError('PTT_DEBOUNCE_MS must be finite, between 10 and 500')
        memory_mode = get('PTT_MEMORY_MODE', 'resident')
        if memory_mode not in MEMORY_MODES:
            raise ConfigError('PTT_MEMORY_MODE must be resident, isolated or hybrid')
        try:
            pitft = tuple(int(value) for value in
                          get('PTT_PITFT_BUTTONS', '23,24').split(',') if value.strip())
        except ValueError:
            pitft = None
        if pitft is None or len(pitft) not in (0, 2) or gpio_line in pitft:
            raise ConfigError('PTT_PITFT_BUTTONS must be two GPIO lines other than '
                              'PTT_GPIO_LINE, or empty')
        llm_mode = get('PTT_LLM_MODE', 'auto').strip().lower()
        return cls(
            gpio_chip=get('PTT_GPIO_CHIP', '/dev/gpiochip0'),
            gpio_line=gpio_line,
            active_low=flag('PTT_ACTIVE_LOW', '1'),
            debounce=debounce,
            max_seconds=max_seconds,
            button_shim=flag('PTT_BUTTON_SHIM', '0'),
            pitft_buttons=pitft,
            runtime_dir=Path(get('PTT_RUNTIME_DIR', '/run/pi-ptt')),
            memory_mode=memory_mode,
            stt_provider=get('STT_PROVIDER', 'vosk').strip().lower(),
            capture_device=get('PTT_AUDIO_DEVICE', DEFAULT_AUDIO_DEVICE),
            output_device=get('TTS_AUDIO_DEVICE', DEFAULT_AUDIO_DEVICE),
            speak_command=get('PTT_SPEAK_COMMAND', cls.speak_command),
            voice_profile=get('TTS_VOICE_PROFILE', 'normal'),
            piper_model=get('PIPER_MODEL', cls.piper_model),
            servitor_model=get('TTS_SERVITOR_MODEL', cls.servitor_model),
            piper_venv=Path(get('PIPER_VENV', str(cls.piper_venv))),
            cue=get('PTT_CUE', '1') != '0',
            wake_word=get('PTT_WAKE_WORD').strip(),
            wake_model_dir=Path(get('PTT_WAKE_MODEL_DIR', str(cls.wake_model_dir))),
            wake_threshold=number('PTT_WAKE_THRESHOLD', '0.5'),
            wake_shadow=tuple(name.strip() for name in
                              get('PTT_WAKE_SHADOW', 'proximus').split(',') if name.strip()),
            lore=env.get('PTT_LORE_LEVEL'),
            persona=env.get('PTT_PERSONA'),
            voice_effect=env.get('PTT_VOICE_EFFECT'),
            emotions=get('PTT_EMOTIONS', 'on'),
            llm_mode=llm_mode if llm_mode in LLM_MODES else 'auto',
            alarms=get('PTT_ALARMS', '1') != '0',
            rest_seconds=number('PTT_REST_SECONDS', REST_SECONDS),
            sleep_seconds=number('PTT_SLEEP_SECONDS', SLEEP_SECONDS),
            sleep_wlan_off=get('PTT_SLEEP_WLAN', 'keep') == 'off',
            wlan=get('PTT_WLAN').strip().lower(),
            bluetooth=get('PTT_BLUETOOTH', '1') != '0',
        )
