import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import audio_output  # noqa: E402
import bluetooth  # noqa: E402

SPEAKER = "AA:BB:CC:DD:EE:01"
PHONE = "AA:BB:CC:DD:EE:02"


class FakeCtl:
    """bluetoothctl with one speaker and one phone in range."""

    def __init__(self):
        self.paired, self.connected, self.calls = set(), set(), []

    def __call__(self, *args, timeout=20):
        self.calls.append(args)
        command = args[0] if args[0] != '--timeout' else args[2]
        if command == 'list':
            return "Controller B8:27:EB:82:C7:DB pi-assistent [default]\n"
        if command == 'devices':
            known = [(SPEAKER, "JBL Flip"), (PHONE, "Telefon")]
            if 'Paired' in args:
                known = [d for d in known if d[0] in self.paired]
            return ''.join(f"Device {mac} {name}\n" for mac, name in known)
        if command == 'info':
            mac = args[1]
            uuid = bluetooth.AUDIO_SINK if mac == SPEAKER else "0000110a-0000-1000-8000-00805f9b34fb"
            return (f"Device {mac}\n\tPaired: {'yes' if mac in self.paired else 'no'}\n"
                    f"\tTrusted: {'yes' if mac in self.paired else 'no'}\n"
                    f"\tConnected: {'yes' if mac in self.connected else 'no'}\n\tUUID: x ({uuid})\n")
        if command == 'pair':
            self.paired.add(args[1])
        if command == 'connect' and args[1] in self.paired:
            self.connected.add(args[1])
        if command == 'disconnect':
            self.connected.discard(args[1])
        if command == 'remove':
            self.paired.discard(args[1])
            return "Device has been removed\n"
        return ''


class BluetoothTests(unittest.TestCase):
    def scan(self, bt, updates=None):
        now = [0.0]
        bt.clock = lambda: now[0]
        process = Mock()
        process.poll.return_value = None

        def sleep(seconds):
            now[0] += seconds
        return bt.scan(seconds=4, on_update=updates, popen=lambda *a, **k: process, sleep=sleep)

    def test_live_scan_lists_speakers_and_pairing_connects(self):
        ctl = FakeCtl()
        bt = bluetooth.Bluetooth(run=ctl)
        self.assertTrue(bt.available())
        updates = []
        self.assertEqual(self.scan(bt, updates.append), [(SPEAKER, "JBL Flip", False)])
        self.assertEqual(len(updates), 3)                    # every 2 s of 4 s, plus the end
        self.assertTrue(bt.pair(SPEAKER, "JBL Flip"))
        self.assertEqual(bt.connected, (SPEAKER, "JBL Flip"))
        self.assertEqual(self.scan(bluetooth.Bluetooth(run=ctl)), [(SPEAKER, "JBL Flip", True)])
        bt.disconnect()
        ctl.calls.clear()
        self.assertTrue(bt.pair(SPEAKER))                    # already paired: just connects
        self.assertNotIn(('pair', SPEAKER), ctl.calls)
        self.assertFalse(bt.pair("not a mac"))

    def test_refresh_reconnects_a_trusted_speaker_and_forget_removes_it(self):
        ctl = FakeCtl()
        ctl.paired.add(SPEAKER)
        bt = bluetooth.Bluetooth(run=ctl)
        self.assertEqual(bt.refresh(reconnect=False), None)
        self.assertEqual(bt.refresh(), (SPEAKER, "JBL Flip"))
        self.assertTrue(bt.forget(SPEAKER))
        self.assertIsNone(bt.connected)

    def test_output_follows_the_speaker(self):
        try:
            audio_output.set_bluetooth(SPEAKER)
            self.assertEqual(audio_output.current("plughw:x"), f"bluealsa:DEV={SPEAKER},PROFILE=a2dp")
        finally:
            audio_output.set_bluetooth(None)
        self.assertEqual(audio_output.current("plughw:x"), "plughw:x")


class ControllerTests(unittest.TestCase):
    def setUp(self):
        import ptt
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "PTT_DISPLAY_STATUS_PATH": str(Path(self.tmp.name) / "status.json"),
            "PTT_DISPLAY_EVENT_PATH": str(Path(self.tmp.name) / "event.json"),
            "PTT_RUNTIME_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        ptt._display_status.clear()
        context = redirect_stdout(StringIO())
        context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        recorder, speech = Mock(), Mock()
        recorder.process = None
        speech.active = False
        self.c = ptt.VoiceController(recorder, speech, .04, 30)
        self.ctl = FakeCtl()
        self.c.bt = bluetooth.Bluetooth(run=self.ctl)
        self.said = []
        self.c._say = self.said.append
        self.c._bt_job = lambda work: work()               # run jobs inline
        self.addCleanup(audio_output.set_bluetooth, None)

    def view(self):
        return json.loads((Path(self.tmp.name) / "display-people.json").read_text())

    def test_scan_pick_pair_and_output_switch(self):
        process = Mock()
        process.poll.return_value = 0                        # scan finished at once
        scan = self.c.bt.scan
        self.c.bt.scan = lambda on_update=None: scan(seconds=0, on_update=on_update,
                                                     popen=lambda *a, **k: process)
        self.c._bluetooth_item("bt_scan")
        self.assertEqual(self.view()["items"], ["JBL Flip", "Zurück"])
        self.c._picker_buttons(confirm=True)
        self.assertEqual(audio_output.bluetooth(), SPEAKER)
        self.assertEqual(self.c.bt_notes, [bluetooth.CONNECTED])
        self.c._bluetooth_item("bt_speaker")                # connected: E disconnects
        self.assertIsNone(audio_output.bluetooth())
        self.assertFalse(self.c.bt_wanted)

    def test_manual_connect_from_the_paired_list(self):
        self.ctl.paired.add(SPEAKER)
        self.c._bluetooth_item("bt_connect")
        self.assertEqual(self.view()["title"], "VERBINDEN")
        self.c._picker_buttons(confirm=True)
        self.assertEqual(audio_output.bluetooth(), SPEAKER)
        self.assertEqual(self.said[-1], bluetooth.CONNECTING)

    def test_forget_needs_a_paired_speaker(self):
        self.c._bluetooth_item("bt_forget")
        self.assertEqual(self.said, [bluetooth.NONE_PAIRED])
        self.ctl.paired.add(SPEAKER)
        self.c._bluetooth_item("bt_forget")
        self.c._picker_buttons(confirm=True)
        self.assertNotIn(SPEAKER, self.ctl.paired)


if __name__ == "__main__":
    unittest.main()
