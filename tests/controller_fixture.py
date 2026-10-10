"""A VoiceController with fake recorder and speech for controller tests.

Display status, display events and settings go to a temporary directory; the
configuration is a PttConfig, not the environment, so tests stay independent of
the shell they run in.
"""
import os
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import ptt  # noqa: E402
from ptt_config import PttConfig  # noqa: E402

BUTTONS = 'ABCDE'


def make_controller(test, **config):
    """Build a controller for ``test`` (a TestCase); cleanup is registered on it."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    env = patch.dict(os.environ, {
        'PTT_DISPLAY_STATUS_PATH': str(root / 'status.json'),
        'PTT_DISPLAY_EVENT_PATH': str(root / 'event.json'),
        'PTT_SETTINGS_FILE': str(root / 'settings.json')})
    env.start()
    test.addCleanup(env.stop)
    out = redirect_stdout(StringIO())
    out.__enter__()
    test.addCleanup(out.__exit__, None, None, None)
    ptt._display_status.clear()
    recorder, speech = Mock(), Mock()
    recorder.process = None
    recorder.take_live_transcript.return_value = None
    speech.active = False
    speech.synthesizing = False
    speech.poll.return_value = None
    config = PttConfig(runtime_dir=root, **config)
    return ptt.VoiceController(recorder, speech, .04, 30, config=config)


class Presser:
    """Drive tick() with debounced SHIM presses on a monotonic clock."""

    def __init__(self, controller):
        self.c, self.now = controller, 0.0
        self.idle()

    def tick(self, names=''):
        self.now += .1
        self.c.tick(False, tuple(name in names for name in BUTTONS), self.now)

    def idle(self):
        self.tick()
        self.tick()

    def press(self, names):
        """Press ``names`` together (e.g. 'BE') and release them again."""
        self.tick(names)
        self.tick(names)
        self.idle()
