"""Power readings shared by the display and the voice service (no GUI code).

PiSugar 3 battery over I2C (read-only) and the Pi firmware's power flags.
"""
import collections
import subprocess


PISUGAR3_ADDRESS = 0x57
BATTERY_SAMPLE_SECONDS = 5.0


class Battery:
    """PiSugar 3 on I²C bus 1, read-only, registers as in the vendor driver
    (pisugar-power-manager-rs, pisugar3.rs): 0x22/0x23 voltage in mV, 0x2A
    percent, 0x02 bit 7 power plugged, bit 6 charging allowed, 0x04 board
    temperature + 40. The percent register follows the momentary voltage and
    jumps with charge pulses, so values are averaged over the last minute."""

    def __init__(self, bus_factory=None, window=12):
        self.bus_factory = bus_factory
        self.samples = collections.deque(maxlen=window)

    def _bus(self):
        if self.bus_factory is not None:
            return self.bus_factory()
        from smbus2 import SMBus
        return SMBus(1)

    def read(self):
        try:
            bus = self._bus()
            try:
                mv = (bus.read_byte_data(PISUGAR3_ADDRESS, 0x22) << 8) | bus.read_byte_data(
                    PISUGAR3_ADDRESS, 0x23)
                percent = bus.read_byte_data(PISUGAR3_ADDRESS, 0x2A)
                ctr1 = bus.read_byte_data(PISUGAR3_ADDRESS, 0x02)
                board = bus.read_byte_data(PISUGAR3_ADDRESS, 0x04) - 40
            finally:
                bus.close()
        except (ImportError, OSError):
            return None
        if not (2500 <= mv <= 4500 and 0 <= percent <= 100):
            return None
        self.samples.append((percent, mv))
        avg_percent = round(sum(p for p, _ in self.samples) / len(self.samples))
        avg_mv = round(sum(v for _, v in self.samples) / len(self.samples))
        plugged = bool(ctr1 & 0x80)
        return dict(percent=avg_percent, mv=avg_mv, plugged=plugged,
                    charging=plugged and bool(ctr1 & 0x40) and avg_percent < 100,
                    board_c=board)


def throttled_flags(run=subprocess.run):
    """Firmware power flags (vcgencmd get_throttled), None if unavailable."""
    try:
        result = run(['vcgencmd', 'get_throttled'], capture_output=True, text=True,
                     timeout=2, check=False)
        return int(result.stdout.strip().split('=', 1)[1], 16)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
