import os
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import alarm_audio  # noqa: E402
from alarms import AlarmMonitor  # noqa: E402


def wav_bytes(samples, rate=16000):
    import io
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(array("h", samples).tobytes())
    return buffer.getvalue()


class FragmentTests(unittest.TestCase):
    def test_numbers_are_separate_pieces_with_pauses(self):
        self.assertEqual(
            alarm_audio.fragments("Warnung 1 von 3. Energiespeicher bei 15 Prozent."),
            [("Warnung", 0.0), ("1", alarm_audio.GAP), ("von", alarm_audio.GAP),
             ("3", alarm_audio.GAP), ("Energiespeicher bei", alarm_audio.SENTENCE_GAP),
             ("15", alarm_audio.GAP), ("Prozent.", alarm_audio.GAP)])

    def test_every_possible_alarm_is_covered_by_known_pieces(self):
        pieces = set(alarm_audio.known_pieces())
        monitor = AlarmMonitor()
        spoken = []
        for lore in ("off", "dezent", "full"):
            m = AlarmMonitor()
            for percent, plugged in ((30, True), (30, False), (14, False), (9, False),
                                     (3, False), (3, True)):
                spoken += m.update(dict(battery_pct=percent, battery_plugged=plugged, temp_c=82,
                                        load_pct=97, mem_free_pct=4, throttled=1), 0.0,
                                   network=True, server="down", lore=lore, internet=False)
                spoken += m.update(dict(battery_pct=percent, battery_plugged=plugged), 100.0,
                                   network=True, server="down", lore=lore, internet=False)
            spoken += m.update({}, 200.0, network=True, server="ok", lore=lore, internet=True)
        self.assertTrue(spoken)
        for text in spoken:
            for piece, _ in alarm_audio.fragments(text):
                self.assertIn(piece, pieces, text)
        del monitor


class AssembleTests(unittest.TestCase):
    def test_joins_clips_with_pauses_and_reports_missing_ones(self):
        with tempfile.TemporaryDirectory() as tmp:
            for piece in ("Warnung", "1"):
                alarm_audio._store(wav_bytes([0] * 800 + [5000] * 160 + [0] * 800),
                                   alarm_audio.clip_path(piece, tmp))
            out = Path(tmp) / "alarm.wav"
            self.assertTrue(alarm_audio.assemble(["Warnung 1"], out, tmp))
            with wave.open(str(out)) as joined:
                frames = joined.getnframes()
            trimmed = 160 + 2 * int(alarm_audio.MARGIN * 16000)
            self.assertEqual(frames, 2 * trimmed + int(alarm_audio.GAP * 16000))
            self.assertFalse(alarm_audio.assemble(["Warnung 2"], out, tmp))


class ControllerAlarmTests(unittest.TestCase):
    def test_alarm_uses_clips_when_complete_else_synthesis(self):
        import ptt
        speech = Mock()
        controller = Mock(speech=speech)
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.dict(os.environ, {"PTT_RUNTIME_DIR": tmp}), \
                unittest.mock.patch.object(ptt, "event"), \
                unittest.mock.patch.object(ptt.alarm_audio, "assemble", return_value=True):
            ptt.VoiceController._say_alarm(controller, ["Netzbetrieb."])
            speech.play.assert_called_once_with(Path(tmp) / "alarm.wav")
            speech.start.assert_not_called()
        with unittest.mock.patch.object(ptt, "event"), \
                unittest.mock.patch.object(ptt.alarm_audio, "assemble", return_value=False):
            ptt.VoiceController._say_alarm(controller, ["A.", "B."])
            speech.start.assert_called_once_with("A. B.")


if __name__ == "__main__":
    unittest.main()
