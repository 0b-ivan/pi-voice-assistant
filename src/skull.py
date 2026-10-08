"""Servo skull for the PiTFT: idle and speaking screens with a glowing eye.

The artwork is not part of this public repository (third-party pixel art);
it is read from PI_DISPLAY_SKULL on the Pi. The red eye is found
automatically (red pixels around the brightest red core) and rendered in
precomputed brightness steps, so a frame only pastes the small eye patch.
Without the file a simple drawn skull with a red eye is used.
"""
import math
import os
from pathlib import Path

SKULL_FILE = Path(os.environ.get('PI_DISPLAY_SKULL',
                                 '/opt/pi-voice-assistant/models/display/servo-skull.png'))
SIZE = 136          # square on the 240 x 240 screen
STEPS = 12          # precomputed eye brightness levels


def _square(image):
    width, height = image.size
    side = min(width, height)
    left, top = (width - side) // 2, (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _eye_mask(image):
    """Indices of red pixels around the brightest red core, including its
    bright center. Pillow only: the display venv has no numpy."""
    width, height = image.size
    pixels = list(image.getdata())
    core = [i for i, (r, g, b) in enumerate(pixels) if r > 200 and g < 80 and b < 80]
    if not core:
        return set()
    cx = sum(i % width for i in core) / len(core)
    cy = sum(i // width for i in core) / len(core)
    radius = 0.11 * height
    mask = set()
    for i, (r, g, b) in enumerate(pixels):
        dx, dy = i % width - cx, i // width - cy
        distance = dx * dx + dy * dy
        if distance >= radius * radius:
            continue
        red = r > 120 and r > g * 1.8 and r > b * 1.8
        highlight = r > 180 and distance < (radius * 0.25) ** 2
        if red or highlight:
            mask.add(i)
    return mask


def _drawn_skull():
    """Fallback: a plain skull with one red eye (original drawing)."""
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (SIZE, SIZE), 'black')
    draw = ImageDraw.Draw(image)
    bone, dark = (196, 182, 150), (40, 30, 26)
    draw.ellipse((14, 6, SIZE - 14, SIZE - 30), fill=bone)
    draw.rounded_rectangle((38, SIZE - 52, SIZE - 38, SIZE - 12), radius=8, fill=bone)
    for x in range(44, SIZE - 40, 10):
        draw.line((x, SIZE - 30, x, SIZE - 14), fill=dark, width=2)
    draw.ellipse((28, 46, 60, 78), fill=dark)
    draw.ellipse((SIZE - 60, 46, SIZE - 28, 78), fill=(60, 10, 10))
    draw.ellipse((SIZE - 52, 54, SIZE - 36, 70), fill=(255, 20, 20))
    draw.polygon(((SIZE // 2, 80), (SIZE // 2 - 8, 96), (SIZE // 2 + 8, 96)), fill=dark)
    return image


class Skull:
    def __init__(self, path=SKULL_FILE, size=SIZE):
        from PIL import Image
        try:
            source = _square(Image.open(path).convert('RGB'))
            self.source = 'file'
        except (OSError, ValueError):
            source = _drawn_skull()
            self.source = 'drawn'
        self.base = source.resize((size, size), Image.LANCZOS)
        mask = _eye_mask(self.base)
        self.box, self.frames = None, []
        if not mask:
            return
        xs = [i % size for i in mask]
        ys = [i // size for i in mask]
        self.box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        x0, y0, x1, y1 = self.box
        inside = [(i % size - x0, i // size - y0) for i in mask]
        for step in range(STEPS):
            level = step / (STEPS - 1)              # 0 dim ... 1 brightest
            gain = 0.3 + 0.9 * level
            hot = max(0.0, level - 0.6)
            patch = self.base.crop(self.box)
            data = patch.load()
            for x, y in inside:
                r, g, b = data[x, y]
                data[x, y] = (min(255, int(r * gain + 70 * hot)),   # deeper red when hot
                              min(255, int(g * gain * (1 - 0.35 * hot))),
                              min(255, int(b * gain * (1 - 0.35 * hot))))
            self.frames.append(patch)

    def frame(self, level):
        """Skull image with the eye at level 0..1 (copy of the base)."""
        image = self.base.copy()
        if self.frames:
            step = max(0, min(STEPS - 1, round(level * (STEPS - 1))))
            image.paste(self.frames[step], self.box[:2])
        return image


def idle_level(now):
    """Slow breathing: about one cycle every 3.2 s."""
    return 0.45 + 0.3 * math.sin(2 * math.pi * now / 3.2)


def speaking_level(now, envelope=None):
    """Follow the speech envelope when known, else a faster generic pulse."""
    if envelope:
        index = int((now - envelope['start']) / envelope['step'])
        if 0 <= index < len(envelope['levels']):
            return min(1.0, 0.55 + 0.45 * envelope['levels'][index] / 100)
    return 0.7 + 0.3 * math.sin(2 * math.pi * now / 0.5)
