"""Synchronous Button SHIM I/O; pin mapping follows Pimoroni buttonshim 0.0.2.

Only bus 1 / address 0x3f is used. No callback threads or global bus scanning.
"""


class ButtonShim:
    ADDRESS = 0x3f

    def __init__(self, bus=None):
        if bus is None:
            import smbus
            bus = smbus.SMBus(1)
        self.bus = bus
        self.color = None
        try:
            bus.write_byte_data(self.ADDRESS, 3, 0x1f)  # A-E inputs, LED outputs
            bus.write_byte_data(self.ADDRESS, 2, 0)     # active-low inputs
            bus.write_byte_data(self.ADDRESS, 1, 0)
            self.set_color((0, 0, 0))
        except Exception:
            bus.close()
            raise

    def read(self):
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
                self.bus.write_byte_data(self.ADDRESS, 1, data)
                self.bus.write_byte_data(self.ADDRESS, 1, data | 0x40)
        self.bus.write_byte_data(self.ADDRESS, 1, 0)
        self.color = color

    def close(self):
        try:
            self.set_color((0, 0, 0))
        finally:
            self.bus.close()
