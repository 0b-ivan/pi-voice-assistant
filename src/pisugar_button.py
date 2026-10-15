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

        I2C failures propagate; the supervisor retries with bounded backoff,
        so intermittent bus errors do not strand Proximus in manual standby.
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


class TapMonitor:
    """Retry intermittent PiSugar I2C failures without replaying unacked taps.

    First successful read only acknowledges a stale startup event. No tap is
    delivered until acknowledgement has been verified by TapReader. Failures
    slow I2C polling exponentially; repeated errors while manually sleeping
    are reported to the caller so it can fail open to an awake assistant.
    """

    def __init__(self, reader=None, interval=0.25, retry_initial=1.0,
                 retry_max=30.0, failopen_after=3):
        self.reader = reader if reader is not None else TapReader()
        self.interval = interval
        self.retry_initial = retry_initial
        self.retry_max = retry_max
        self.failopen_after = failopen_after
        self.next_at = 0.0
        self.failures = 0
        self.initialized = False

    def poll(self, now=None):
        """Return None if not due, or a structured ready/error/result state."""
        now = time.monotonic() if now is None else now
        if now < self.next_at:
            return None
        try:
            gesture = self.reader.poll(now)
        except (OSError, ImportError) as exc:
            self.failures += 1
            retry = min(self.retry_max, self.retry_initial * (2 ** min(self.failures - 1, 10)))
            self.next_at = now + retry
            return dict(state='error', message=str(exc), failures=self.failures,
                        retry_seconds=retry, should_wake=self.failures >= self.failopen_after)
        recovered = self.failures
        self.failures = 0
        self.next_at = now + self.interval
        if not self.initialized:
            self.initialized = True
            # A leftover tap from the old process is not a new wake/sleep action.
            return dict(state='ready', ignored_startup_gesture=gesture,
                        recovered_after=recovered)
        return dict(state='ok', gesture=gesture, recovered_after=recovered)
