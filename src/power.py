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
                # Optional configuration status bits. A failed auxiliary read
                # must never suppress the core battery measurement.
                optional = {}
                for key, register, mask in (
                    ('soft_poweroff', 0x03, 0x10),
                    ('watchdog', 0x06, 0x80),
                    ('charge_protection', 0x20, 0x80),
                ):
                    try:
                        optional[key] = bool(bus.read_byte_data(PISUGAR3_ADDRESS, register) & mask)
                    except OSError:
                        pass
                mv = (bus.read_byte_data(PISUGAR3_ADDRESS, 0x22) << 8) | bus.read_byte_data(
                    PISUGAR3_ADDRESS, 0x23)
                percent = bus.read_byte_data(PISUGAR3_ADDRESS, 0x2A)
                ctr1 = bus.read_byte_data(PISUGAR3_ADDRESS, 0x02)
                board = bus.read_byte_data(PISUGAR3_ADDRESS, 0x04) - 40
                # Vendor PiSugar-3 driver: 0x26/0x27 report output current
                # in milliamps (intensity = raw / 1000 amperes).
                # Some PiSugar 3 units report 0 mA even while the Pi runs
                # on battery (observed on real hardware). Treat such a raw
                # zero as an unavailable measurement, never as zero usage.
                # Other unavailable/unsupported firmware must not suppress
                # the core battery status.
                try:
                    output_ma = (bus.read_byte_data(PISUGAR3_ADDRESS, 0x26) << 8) | bus.read_byte_data(
                        PISUGAR3_ADDRESS, 0x27)
                    if 0 < output_ma <= 5000:
                        optional['output_current_ma'] = output_ma
                except (OSError, ValueError):
                    pass
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
                    board_c=board, auto_power_on=bool(ctr1 & 0x10), **optional)

    def plugged(self):
        """Only the "power plugged" bit (one register): cheap enough to poll
        every second, so the display notices the charger at once. None when
        the PiSugar cannot be read."""
        try:
            bus = self._bus()
            try:
                return bool(bus.read_byte_data(PISUGAR3_ADDRESS, 0x02) & 0x80)
            finally:
                bus.close()
        except (ImportError, OSError):
            return None


def throttled_flags(run=subprocess.run):
    """Firmware power flags (vcgencmd get_throttled), None if unavailable."""
    try:
        result = run(['vcgencmd', 'get_throttled'], capture_output=True, text=True,
                     timeout=2, check=False)
        return int(result.stdout.strip().split('=', 1)[1], 16)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None
