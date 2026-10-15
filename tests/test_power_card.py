"""Charger connected / pulled: fast detection and the short pictogram."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import power  # noqa: E402


class Capture:
    def image(self, image, rotation=0):
        self.picture = image


class Bus:
    def __init__(self, ctr1=0x80, fail=False):
        self.ctr1, self.fail, self.reads, self.closed = ctr1, fail, [], False

    def read_byte_data(self, address, register):
        self.reads.append(register)
        if self.fail:
            raise OSError(121, 'Remote I/O error')
        return self.ctr1

    def close(self):
        self.closed = True


class PluggedBitTests(unittest.TestCase):
    def test_reads_only_the_status_register(self):
        bus = Bus(0xC0)
        self.assertTrue(power.Battery(bus_factory=lambda: bus).plugged())
        self.assertEqual(bus.reads, [0x02])
        self.assertTrue(bus.closed)
        self.assertFalse(power.Battery(bus_factory=lambda: Bus(0x40)).plugged())
        self.assertIsNone(power.Battery(bus_factory=lambda: Bus(fail=True)).plugged())

    def test_change_only_between_two_readings(self):
        self.assertIsNone(display.plug_change(None, True))     # first reading
        self.assertIsNone(display.plug_change(True, None))     # bus hiccup
        self.assertIsNone(display.plug_change(True, True))
        self.assertIs(display.plug_change(False, True), True)
        self.assertIs(display.plug_change(True, False), False)


class CardTests(unittest.TestCase):
    def card(self, plugged, battery):
        out = Capture()
        display.render_power_card(out, plugged, True, dict(battery=battery, clock='12:00'))
        return out.picture

    @staticmethod
    def count(image, color):
        data = image.convert('RGB').tobytes()
        return sum(1 for pixel in zip(data[0::3], data[1::3], data[2::3]) if pixel == color)

    def test_connected_and_pulled_look_different(self):
        plugged = self.card(True, dict(percent=80, mv=4000, plugged=True, charging=True))
        pulled = self.card(False, dict(percent=64, mv=3880, plugged=False, charging=False))
        middle = (20, 60, 220, 140)
        self.assertGreater(self.count(plugged.crop(middle), display.PLUG_COLOR), 500)
        self.assertGreater(self.count(pulled.crop(middle), display.UNPLUG_COLOR), 500)
        self.assertEqual(self.count(pulled.crop(middle), display.PLUG_COLOR), 0)
        low = self.card(False, dict(percent=9, mv=3400, plugged=False, charging=False))
        self.assertGreater(self.count(low.crop(middle), (255, 70, 70)), 100)
        # Without battery data it still renders.
        self.assertEqual(self.card(True, None).size, (display.WIDTH, display.HEIGHT))


if __name__ == '__main__':
    unittest.main()
