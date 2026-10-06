#!/usr/bin/env python3
"""Minimal hardware smoke test for the Adafruit mini PiTFT 1.3"."""

import time

import board
import digitalio
from PIL import Image
from adafruit_rgb_display import st7789


def main() -> None:
    spi = board.SPI()
    cs = digitalio.DigitalInOut(board.CE0)
    dc = digitalio.DigitalInOut(board.D25)

    backlight = digitalio.DigitalInOut(board.D22)
    backlight.switch_to_output(value=True)

    display = st7789.ST7789(
        spi,
        cs=cs,
        dc=dc,
        rst=None,
        baudrate=24_000_000,
        width=240,
        height=240,
        x_offset=0,
        y_offset=80,
    )

    colors = (
        ("ROT", (255, 0, 0)),
        ("GRUEN", (0, 255, 0)),
        ("BLAU", (0, 0, 255)),
        ("WEISS", (255, 255, 255)),
        ("SCHWARZ", (0, 0, 0)),
    )

    for name, color in colors:
        print(name)
        image = Image.new("RGB", (240, 240), color)
        display.image(image, 180)
        time.sleep(2)

    print("Display-Test abgeschlossen.")


if __name__ == "__main__":
    main()
