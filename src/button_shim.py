"""Synchronous Button SHIM I/O; pin mapping follows Pimoroni buttonshim 0.0.2.

Only bus 1 / address 0x3f is used. No callback threads or global bus scanning.
Colour changes can be handed to LedWriter, which owns a single daemon thread.
"""
import threading
import time


class ButtonShim:
    ADDRESS = 0x3f

    def __init__(self, bus=None):
        if bus is None:
            import smbus
            bus = smbus.SMBus(1)
        self.bus = bus
        self.color = None
        # Button reads (main loop) and LED bit-banging (LedWriter thread) share
        # the bus; lock per transaction so reads slot in between LED writes.
        self._lock = threading.Lock()
        try:
            bus.write_byte_data(self.ADDRESS, 3, 0x1f)  # A-E inputs, LED outputs
            bus.write_byte_data(self.ADDRESS, 2, 0)     # active-low inputs
            bus.write_byte_data(self.ADDRESS, 1, 0)
            self.set_color((0, 0, 0))
        except Exception:
            bus.close()
            raise

    def read(self):
        with self._lock:
            bits = self.bus.read_byte_data(self.ADDRESS, 0)
        return tuple(not bool(bits & (1 << i)) for i in range(5))

    def set_color(self, color):
        if color == self.color:
            return
        r, g, b = color
        # APA102: start frame, low global brightness (4/31), BGR, end frame.
        # Clock is expander bit 6, data bit 7, MSB first. One register write
        # per edge avoids depending on SMBus block-write register behavior.
        for byte in (0, 0, 0, 0, 0xe4, b, g, r, 0xff, 0xff, 0xff, 0xff):
            for shift in range(7, -1, -1):
                data = 0x80 if byte & (1 << shift) else 0
                with self._lock:
                    self.bus.write_byte_data(self.ADDRESS, 1, data)
                    self.bus.write_byte_data(self.ADDRESS, 1, data | 0x40)
        with self._lock:
            self.bus.write_byte_data(self.ADDRESS, 1, 0)
        self.color = color

    def close(self):
        try:
            self.set_color((0, 0, 0))
        finally:
            self.bus.close()


class LedWriter:
    """Applies the most recently requested colour in a daemon thread.

    One SHIM colour change is ~190 I2C writes (50-90 ms on the Pi Zero 2 W);
    done inline it stalled button polling. Changes are coalesced (only the
    latest colour is written) and spaced by at least min_interval seconds.
    """

    def __init__(self, shim, min_interval=0.1):
        self.shim = shim
        self.min_interval = min_interval
        self.error = None
        self._wanted = shim.color  # nothing to write until a new colour is requested
        self._stopped = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._run, name='shim-led', daemon=True)
        self._thread.start()

    def request(self, color):
        with self._condition:
            if color != self._wanted:
                self._wanted = color
                self._condition.notify()

    def _run(self):
        last_write = 0.0
        while True:
            with self._condition:
                while not self._stopped and (self._wanted is None
                                             or self._wanted == self.shim.color):
                    self._condition.wait()
                if self._stopped:
                    return
            delay = last_write + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            with self._condition:
                color = self._wanted
            try:
                self.shim.set_color(color)
            except Exception as exc:  # reported to the main loop via .error
                self.error = exc if isinstance(exc, OSError) else OSError(str(exc))
                return
            last_write = time.monotonic()

    def close(self, timeout=1.0):
        with self._condition:
            self._stopped = True
            self._condition.notify()
        self._thread.join(timeout)
