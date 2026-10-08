import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import display


class DisplayStatusTests(unittest.TestCase):
    def ready_states(self, **overrides):
        states = {
            "spi": True,
            "audio": True,
            "network": True,
            "vosk": True,
            "tts": True,
            "voice": True,
        }
        states.update(overrides)
        return states

    def test_network_is_informational_for_ready_gate(self):
        states = self.ready_states(network=False)

        self.assertTrue(display.is_ready(states))
        self.assertEqual(display.footer_status(states)[0], "SYSTEM READY")

    def test_missing_critical_state_keeps_booting(self):
        states = self.ready_states(voice=False)

        self.assertFalse(display.is_ready(states))
        self.assertEqual(display.footer_status(states)[0], "BOOTING ...")

    def test_network_probe_timeout_returns_false(self):
        timeout = subprocess.TimeoutExpired(["ip"], 2)
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", side_effect=timeout),
        ):
            self.assertFalse(display.network_ok())

    def test_network_probe_execution_error_returns_false(self):
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", side_effect=OSError("broken")),
        ):
            self.assertFalse(display.network_ok())

    def test_network_probe_accepts_non_loopback_ipv4(self):
        result = SimpleNamespace(
            stdout=(
                "lo               UNKNOWN        127.0.0.1/8\n"
                "wlan0            UP             172.22.9.128/24\n"
            )
        )
        with (
            patch.object(display.shutil, "which", return_value="/usr/bin/ip"),
            patch.object(display.subprocess, "run", return_value=result),
        ):
            self.assertTrue(display.network_ok())

    def test_voice_probe_timeout_returns_false(self):
        timeout = subprocess.TimeoutExpired(["systemctl"], 2)
        with patch.object(display.subprocess, "run", side_effect=timeout):
            self.assertFalse(display.service_ok())

    def test_voice_probe_execution_error_returns_false(self):
        with patch.object(display.subprocess, "run", side_effect=OSError("broken")):
            self.assertFalse(display.service_ok())

    def test_voice_probe_active_returns_true(self):
        with patch.object(
            display.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0),
        ):
            self.assertTrue(display.service_ok())

    def test_servitor_profile_is_normalized_before_model_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vosk = root / "vosk"
            normal = root / "normal.onnx"
            servitor = root / "servitor.onnx"
            env_file = root / "voice.env"

            vosk.mkdir()
            servitor.write_bytes(b"model")
            env_file.write_text(
                "\n".join(
                    (
                        f"VOSK_MODEL_PATH={vosk}",
                        f"PIPER_MODEL={normal}",
                        f"TTS_SERVITOR_MODEL={servitor}",
                        "TTS_VOICE_PROFILE= SERVITOR ",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(display, "ENV_FILE", env_file):
                self.assertEqual(display.models_ok(), (True, True))

    def test_non_servitor_profile_uses_normal_model_like_voice_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vosk = root / "vosk"
            normal = root / "normal.onnx"
            servitor = root / "servitor.onnx"
            env_file = root / "voice.env"

            vosk.mkdir()
            normal.write_bytes(b"model")
            env_file.write_text(
                "\n".join(
                    (
                        f"VOSK_MODEL_PATH={vosk}",
                        f"PIPER_MODEL={normal}",
                        f"TTS_SERVITOR_MODEL={servitor}",
                        "TTS_VOICE_PROFILE=unexpected",
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(display, "ENV_FILE", env_file):
                self.assertEqual(display.models_ok(), (True, True))



class DisplayVoiceEventTests(unittest.TestCase):
    def test_runtime_event_mapping(self):
        expected = {
            "waiting_for_release": "BEREIT",
            "recording": "ZUHÖREN",
            "processing": "VERSTEHEN",
            "transcript": "DENKEN",
            "llm_start": "DENKEN",
            "llm_response": "SYNTHESE",
            "speech_started": "SYNTHESE",
            "speech_finished": "BEREIT",
            "stt_error": "FEHLER",
            "llm_error": "FEHLER",
        }
        for event_name, state in expected.items():
            with self.subTest(event=event_name):
                self.assertEqual(display.voice_state_for_event(event_name), state)

    def test_loading_events_identify_startup(self):
        self.assertEqual(display.voice_state_for_event("stt_loading"), "STARTET")
        self.assertEqual(display.voice_state_for_event("tts_loading"), "STARTET")

    def test_irrelevant_event_has_no_runtime_state(self):
        self.assertIsNone(display.voice_state_for_event("volume"))

    def test_read_voice_event_accepts_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(
                '{"version":1,"event":"recording","timestamp":12.5}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                display.read_voice_event(path),
                {
                    "event": "recording",
                    "timestamp": 12.5,
                    "error_timestamp": None,
                },
            )

    def test_read_voice_event_rejects_malformed_or_incomplete_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('{"event":"recording"}', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('null', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('[]', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))
            path.write_text('"recording"', encoding="utf-8")
            self.assertIsNone(display.read_voice_event(path))

    def test_read_voice_event_preserves_error_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "event.json"
            path.write_text(
                '{"version":1,"event":"waiting_for_release",'
                '"timestamp":101.0,"error_timestamp":100.0}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                display.read_voice_event(path),
                {
                    "event": "waiting_for_release",
                    "timestamp": 101.0,
                    "error_timestamp": 100.0,
                },
            )


class DetailedProgressTests(unittest.TestCase):
    def test_real_phase_replaces_general_speech_start(self):
        event = dict(event='speech_started', timestamp=10)
        phase = dict(stage='tts', metric='dsp_render', timestamp=11)
        self.assertEqual(display.screen_details('SYNTHESE', event, phase)[:4],
                         ('RENDERN', 'Audioeffekte berechnen', 'sliders', 4))

    def test_later_cancel_or_error_supersedes_stale_phase(self):
        phase = dict(stage='tts', metric='synthesis', timestamp=10)
        event = dict(event='cancelled', timestamp=11)
        self.assertEqual(display.screen_details('BEREIT', event, phase)[0], 'BEREIT')
        phase['timestamp'] = 12
        self.assertEqual(display.screen_details('FEHLER', event, phase)[0], 'FEHLER')

    def test_unknown_or_invalid_progress_is_ignored(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'progress.json'
            for value in ([], None, dict(stage='tts', metric='secret', timestamp=1),
                          dict(stage='tts', metric='synthesis', timestamp=float('nan'))):
                path.write_text(json.dumps(value))
                self.assertIsNone(display.read_progress(path))

    def test_phase_publishes_at_start_and_keeps_no_user_content(self):
        import json
        from runtime_metrics import phase
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PTT_RUNTIME_DIR':tmp}):
            with phase('tts', 'synthesis'):
                payload = json.loads((Path(tmp)/'display-progress.json').read_text())
                self.assertEqual(payload['metric'], 'synthesis')
                self.assertEqual(set(payload), {'version','stage','metric','timestamp'})

    def test_standby_model_load_cannot_replace_recording(self):
        from runtime_metrics import display_progress
        import os
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'PTT_RUNTIME_DIR':tmp}):
            display_progress('stt', 'model_load')
            self.assertFalse((Path(tmp)/'display-progress.json').exists())


class PartialFrameTests(unittest.TestCase):
    def test_partial_frames_reconstruct_rotated_screen_exactly(self):
        from PIL import Image, ImageChops
        class Hardware:
            def __init__(self):
                self.canvas = Image.new('RGB', (240,240))
                self.sizes = []
            def image(self, image, rotation=0, x=0, y=0):
                self.sizes.append(image.size)
                self.canvas.paste(image, (x,y))
        hardware = Hardware()
        output = display.PartialDisplay(hardware)
        first = Image.new('RGB',(240,240),'black')
        output.image(first,180)
        second = first.copy()
        second.paste((255,170,0),(12,80,52,120))
        output.image(second,180)
        self.assertIsNone(ImageChops.difference(hardware.canvas,second.rotate(180)).getbbox())
        self.assertEqual(hardware.sizes,[(240,240),(40,40)])
        output.image(second,180)
        self.assertEqual(len(hardware.sizes),2)

    def test_failed_transfer_is_retried(self):
        from PIL import Image
        from unittest.mock import Mock
        hardware = Mock()
        hardware.image.side_effect = [OSError('SPI busy'),None]
        output = display.PartialDisplay(hardware)
        frame = Image.new('RGB',(240,240),'black')
        with self.assertRaises(OSError):output.image(frame,180)
        output.image(frame,180)
        self.assertEqual(hardware.image.call_count,2)


if __name__ == "__main__":
    unittest.main()


class StatusInfoTests(unittest.TestCase):
    def test_read_status_keeps_only_whitelisted_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text('{"route":"server","last_route":"pi","last_llm":"offline",'
                            '"last_latency_ms":1530,"text":"geheim","route_x":1}')
            self.assertEqual(display.read_status(path), {
                "route": "server", "last_route": "pi", "last_llm": "offline",
                "last_latency_ms": 1530})
            path.write_text('{"route":"mars","last_latency_ms":true}')
            self.assertEqual(display.read_status(path), {})
            path.write_text("kaputt")
            self.assertEqual(display.read_status(path), {})

    def test_last_answer_text_names_source(self):
        self.assertIsNone(display.last_answer_text({}))
        cases = (
            ({"last_route": "server", "last_llm": "openrouter"}, "Server"),
            ({"last_route": "server", "last_llm": "offline"}, "Offline-LLM"),
            ({"last_route": "pi", "last_llm": "openrouter"}, "Pi lokal"),
        )
        for status, source in cases:
            self.assertEqual(display.last_answer_text(dict(status, last_latency_ms=1530)),
                             f"Zuletzt 1,5 s · {source}")

    def test_server_state(self):
        class Response:
            def __init__(self, body):
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, _n):
                return self.body

        env = {"ASSISTANT_BASE_URL": "http://172.22.9.107:8765, https://x.example"}
        self.assertEqual(display.server_state({}), "off")
        with patch("urllib.request.urlopen", return_value=Response(b'{"ready": true}')) as get:
            self.assertEqual(display.server_state(env), "ok")
        self.assertEqual(get.call_args.args[0], "http://172.22.9.107:8765/health")
        with patch("urllib.request.urlopen", return_value=Response(b'{"ready": false}')):
            self.assertEqual(display.server_state(env), "down")
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            self.assertEqual(display.server_state(env), "down")

    def test_wifi_and_temperature_probes(self):
        with tempfile.TemporaryDirectory() as tmp:
            wireless = Path(tmp) / "wireless"
            wireless.write_text(
                "Inter-| sta-|   Quality        |\n"
                " face | tus | link level noise |\n"
                " wlan0: 0000   50.  -60.  -256        0\n")
            self.assertEqual(display.wifi_dbm(wireless), -60)
            wireless.write_text("header\nheader\n")
            self.assertIsNone(display.wifi_dbm(wireless))
            temp = Path(tmp) / "temp"
            temp.write_text("44008\n")
            self.assertEqual(display.cpu_temp_c(temp), 44)
            self.assertIsNone(display.cpu_temp_c(Path(tmp) / "missing"))

    def test_render_voice_with_and_without_info(self):
        class Capture:
            def image(self, image, rotation=0):
                self.frame = image

        for state, info in (
            ("BEREIT", None),
            ("BEREIT", {"server": "down", "last": "Zuletzt 9,8 s · Pi lokal",
                        "wifi_dbm": -81, "temp_c": 71, "clock": "23:41"}),
            ("DENKEN", {"server": "ok", "route": "server", "wifi_dbm": -60}),
        ):
            capture = Capture()
            display.render_voice(capture, state, True, info=info)
            self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))


class ServerProbeTests(unittest.TestCase):
    def test_probe_runs_in_background_and_updates_state(self):
        import threading
        called = threading.Event()

        def probe():
            called.set()
            return "ok"

        server = display.ServerProbe(interval=60, probe=probe)
        self.assertIsNone(server.state)
        server.start()
        self.assertTrue(called.wait(2))
        for _ in range(100):
            if server.state == "ok":
                break
            threading.Event().wait(0.01)
        self.assertEqual(server.state, "ok")


class FakeBus:
    def __init__(self, registers):
        self.registers = registers
        self.closed = False
        self.writes = []

    def read_byte_data(self, address, register):
        assert address == 0x57
        return self.registers[register]

    def write_byte_data(self, *args):  # must never be called: read-only
        self.writes.append(args)

    def close(self):
        self.closed = True


def pisugar(mv, percent, ctr1=0xF4, temp=75):
    return {0x22: mv >> 8, 0x23: mv & 0xFF, 0x2A: percent, 0x02: ctr1, 0x04: temp}


class BatteryTests(unittest.TestCase):
    def test_reads_pisugar3_registers_read_only(self):
        bus = FakeBus(pisugar(3834, 80))
        battery = display.Battery(bus_factory=lambda: bus).read()
        self.assertEqual(battery, dict(percent=80, mv=3834, plugged=True, charging=True,
                                       board_c=35))
        self.assertTrue(bus.closed)
        self.assertEqual(bus.writes, [])

    def test_jumpy_percent_is_averaged(self):
        values = iter([(3861, 83), (3969, 91), (3855, 83), (3852, 82)])
        monitor = display.Battery(bus_factory=lambda: FakeBus(pisugar(*next(values))))
        for _ in range(4):
            battery = monitor.read()
        self.assertEqual((battery['percent'], battery['mv']), (85, 3884))

    def test_power_states(self):
        on_battery = display.Battery(bus_factory=lambda: FakeBus(pisugar(3780, 64, 0x40))).read()
        self.assertEqual((on_battery['plugged'], on_battery['charging']), (False, False))
        full = display.Battery(bus_factory=lambda: FakeBus(pisugar(4180, 100, 0xC0))).read()
        self.assertEqual((full['plugged'], full['charging']), (True, False))
        disabled = display.Battery(bus_factory=lambda: FakeBus(pisugar(3900, 70, 0x80))).read()
        self.assertEqual((disabled['plugged'], disabled['charging']), (True, False))

    def test_missing_board_or_implausible_values(self):
        def broken():
            raise OSError("no device")
        self.assertIsNone(display.Battery(bus_factory=broken).read())
        self.assertIsNone(display.Battery(bus_factory=lambda: FakeBus(pisugar(9000, 80))).read())
        self.assertIsNone(display.Battery(bus_factory=lambda: FakeBus(pisugar(3800, 180))).read())

    def test_throttled_flags(self):
        ok = SimpleNamespace(stdout="throttled=0x50005\n")
        self.assertEqual(display.throttled_flags(run=lambda *a, **k: ok), 0x50005)
        self.assertIsNone(display.throttled_flags(run=lambda *a, **k: SimpleNamespace(stdout="?")))

        def missing(*args, **kwargs):
            raise OSError("no vcgencmd")
        self.assertIsNone(display.throttled_flags(run=missing))

    def test_power_line_priorities(self):
        charging = dict(percent=83, mv=3861, plugged=True, charging=True, board_c=35)
        low = dict(percent=12, mv=3480, plugged=False, charging=False, board_c=31)
        self.assertEqual(display.power_line(charging, 0)[0], "Akku 83 % · 3,86 V · lädt")
        self.assertEqual(display.power_line(low, 0), ("Akku 12 % · 3,48 V · Akkubetrieb",
                                                      (255, 90, 90)))
        self.assertEqual(display.power_line(dict(charging, charging=False, percent=100), 0)[0],
                         "Akku 100 % · 3,86 V · Netz · voll")
        self.assertEqual(display.power_line(charging, 0x50005)[0], "UNTERSPANNUNG!")
        self.assertEqual(display.power_line(charging, 0x4)[0], "CPU gedrosselt")
        self.assertEqual(display.power_line(charging, 0x10000)[0], "Unterspannung seit Start")
        self.assertIsNone(display.power_line(None, None))

    def test_render_with_battery(self):
        class Capture:
            def image(self, image, rotation=0):
                self.frame = image

        for battery in (dict(percent=83, mv=3861, plugged=True, charging=True, board_c=35),
                        dict(percent=5, mv=3300, plugged=False, charging=False, board_c=30)):
            capture = Capture()
            display.render_voice(capture, "BEREIT", True,
                                 info=dict(battery=battery, throttled=0, temp_c=47))
            self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))


class BatteryViewTests(unittest.TestCase):
    def test_view_ignores_invisible_millivolt_changes(self):
        a = dict(percent=83, mv=3861, plugged=True, charging=True, board_c=35)
        b = dict(a, mv=3863, board_c=36)
        self.assertEqual(display.battery_view(a), display.battery_view(b))
        self.assertNotEqual(display.battery_view(a), display.battery_view(dict(a, mv=3874)))
        self.assertNotEqual(display.battery_view(a), display.battery_view(dict(a, percent=84)))
        self.assertIsNone(display.battery_view(None))



class VolumeOverlayTests(unittest.TestCase):
    def test_status_and_overlay_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "status.json"
            path.write_text('{"volume": 35, "volume_at": 1000.0, "volume_limit": "max"}')
            status = display.read_status(path)
            self.assertEqual(display.volume_overlay(status, now=1001.0), (35, "max"))
            self.assertIsNone(display.volume_overlay(status, now=1003.0))
            path.write_text('{"volume": 150, "volume_at": 1000.0}')
            self.assertIsNone(display.volume_overlay(display.read_status(path), now=1000.5))

    def test_render_volume_overlay(self):
        class Capture:
            def image(self, image, rotation=0):
                self.frame = image

        for volume in ((35, None), (100, "max"), (0, "min")):
            capture = Capture()
            display.render_voice(capture, "BEREIT", True, info=dict(volume=volume))
            self.assertEqual(capture.frame.size, (display.WIDTH, display.HEIGHT))
