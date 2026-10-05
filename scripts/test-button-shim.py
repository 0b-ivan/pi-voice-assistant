#!/usr/bin/env python3
"""Lesender, zeitlich begrenzter Test für Button SHIM an I²C 0x3f."""

import argparse
import sys
import time


def positive_seconds(value):
    seconds = float(value)
    if not 0 < seconds <= 600:
        raise argparse.ArgumentTypeError("Dauer muss zwischen 0 und 600 Sekunden liegen.")
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=positive_seconds, default=60)
    args = parser.parse_args()
    try:
        import smbus
    except ImportError:
        print("Fehlt: sudo apt install python3-smbus", file=sys.stderr)
        return 2

    pressed = set()
    completed = set()
    try:
        bus = smbus.SMBus(1)
        try:
            # TCA9554A: Input 0x00, Polarity 0x02, Configuration 0x03.
            # Keine Registeränderung und keine LED-Ansteuerung.
            if bus.read_byte_data(0x3F, 0x03) & 0x1F != 0x1F:
                raise RuntimeError("Tastenpins sind nicht alle als Eingänge konfiguriert.")
            polarity = bus.read_byte_data(0x3F, 0x02) & 0x1F

            def read_buttons():
                return (bus.read_byte_data(0x3F, 0x00) ^ polarity) & 0x1F

            stable = read_buttons()
            if stable != 0x1F:
                raise RuntimeError("Alle Tasten loslassen und Test erneut starten.")
            candidate = stable
            changed_at = time.monotonic()
            deadline = changed_at + args.seconds
            print("I²C 0x3f antwortet. A–E einzeln drücken und loslassen.", flush=True)
            print("Abbruch: Ctrl+C. LED und PTT-Dienst bleiben unberührt.", flush=True)
            try:
                while time.monotonic() < deadline and len(completed) < 5:
                    current = read_buttons()
                    now = time.monotonic()
                    if current != candidate:
                        candidate = current
                        changed_at = now
                    if candidate != stable and now - changed_at >= 0.03:
                        previous = stable
                        stable = candidate
                        for index, name in enumerate("ABCDE"):
                            mask = 1 << index
                            if (previous ^ stable) & mask:
                                down = not bool(stable & mask)
                                if down:
                                    pressed.add(name)
                                elif name in pressed:
                                    completed.add(name)
                                action = "GEDRÜCKT" if down else "LOSGELASSEN"
                                print(f"{name}: {action}", flush=True)
                    time.sleep(0.01)
            except KeyboardInterrupt:
                print("\nTest beendet.")
        finally:
            bus.close()
    except (OSError, RuntimeError) as exc:
        print(f"Button-SHIM-Test fehlgeschlagen: {exc}", file=sys.stderr)
        print("I²C-Bus, Adresse 0x3f, Kontakte und i2c-Gruppenrechte prüfen.", file=sys.stderr)
        return 2

    for name in "ABCDE":
        print(f"{name}: {'PASS' if name in completed else 'OFFEN'}")
    return 0 if len(completed) == 5 else 1


if __name__ == "__main__":
    sys.exit(main())
