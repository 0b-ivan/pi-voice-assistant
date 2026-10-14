"""Opt-in PiSugar 3 custom-function button for Proximus software standby.

The PiSugar 3 tap register (0x08) latches 1=single, 2=double, 3=long.
The official driver acknowledges by writing the same byte with the low
two bits cleared. On firmware with write protection this requires briefly
unlocking register 0x0B and relocking it. NEVER touch control/power registers.

Only the voice service may poll this button; do not enable a second PiSugar
Power Manager tap handler concurrently. Disabled by default.
"""
import time


ADDRESS = 0x57
TAP_REGISTER = 0x08
WRITE_ENABLE = 0x0B
UNLOCK = 0x29
SINGLE = 1
MIN_INTERVAL_SECONDS = 0.5


class TapReader:
    """Acknowledge a latched event, returning its code only after success.

    Each transaction uses its own bus. A failure to acknowledge must not
    toggle standby, since the same latched event could otherwise toggle again.
    """

    def __init__(self, bus_factory=None, min_interval=MIN_INTERVAL_SECONDS):
        self.bus_factory = bus_factory
        self.min_interval = min_interval
        self.last_event_at = float('-inf')

    def _bus(self):
        if self.bus_factory is not None:
            return self.bus_factory()
        from smbus2 import SMBus
        return SMBus(1)

    def poll(self, now=None):
        """Read/ack one event. Return 0 for none, duplicate or other gestures.

        I2C failures propagate so the caller can disable this opt-in feature
        without disrupting recorder or battery monitoring.
        """
        now = time.monotonic() if now is None else now
        bus = self._bus()
        try:
            register = bus.read_byte_data(ADDRESS, TAP_REGISTER)
            gesture = register & 0x03
            if not gesture:
                return 0
            # The vendor reset_tap() writes original register value & 0xFC.
            bus.write_byte_data(ADDRESS, WRITE_ENABLE, UNLOCK)
            try:
                bus.write_byte_data(ADDRESS, TAP_REGISTER, register & 0xFC)
            finally:
                bus.write_byte_data(ADDRESS, WRITE_ENABLE, 0)
            # Do not react to an event if it wasn't actually acknowledged.
            if bus.read_byte_data(ADDRESS, TAP_REGISTER) & 0x03:
                raise OSError('PiSugar tap event remains latched after acknowledge')
            if now - self.last_event_at < self.min_interval:
                return 0
            self.last_event_at = now
            return gesture
        finally:
            bus.close()
