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
        self.box, self.frames, self.lens = None, [], []
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
        self.lens = sorted(_lens_mask(self.base, self.box))

    def frame(self, level, lens_color=None):
        """Skull with the red eye at level 0..1 and the other eye glowing in
        lens_color (the status LED colour), if given."""
        image = self.base.copy()
        if self.frames:
            step = max(0, min(STEPS - 1, round(level * (STEPS - 1))))
            image.paste(self.frames[step], self.box[:2])
        if lens_color and self.lens:
            pixels = image.load()
            for x, y in self.lens:
                r, g, b = pixels[x, y]
                pixels[x, y] = (min(255, r // 2 + lens_color[0] * 2 // 3),
                                min(255, g // 2 + lens_color[1] * 2 // 3),
                                min(255, b // 2 + lens_color[2] * 2 // 3))
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


# --- Thought streams beside the skull ------------------------------------

LITANIES = {
    'idle': ('AVE DEUS MECHANICUS', 'CREDO OMNISSIAH', 'LOB DEM MASCHINENGEIST',
             'DAS FLEISCH IST SCHWACH', 'SANCTUS MACHINA', 'GLORIA OMNISSIAH'),
    'think': ('KOGITATION', '0110 1001 1110', 'NOOSPHAERE', 'DATEN HEILIG', 'A7 3F E2 09',
              'FRAGE ERFASST', '1011 0010', 'ANALYSE LAEUFT', 'OMNISSIAH LEITE MICH'),
    'speak': ('LOB DEM MASCHINENGEIST', 'ANTWORT GESENDET', 'VOX MECHANICUS',
              '1100 0111', 'DIE MASCHINE SPRICHT', 'AVE OMNISSIAH'),
}
# rows per frame and trail length per mode
RAIN_SPEED = {'idle': 0.35, 'think': 1.2, 'speak': 0.8}
RAIN_TRAIL = {'idle': 8, 'think': 11, 'speak': 10}


class Rain:
    """Columns of litany letters falling down with a fading trail.

    Deterministic per frame index, so frames can be tested and previewed.
    Glyphs are rendered once as masks and only pasted per frame.
    """

    def __init__(self, columns_x, top, rows, cell=11, size=10, seed=40):
        import random
        self.columns_x, self.top, self.rows, self.cell = columns_x, top, rows, cell
        self.size = size
        rng = random.Random(seed)
        self.columns = [dict(offset=rng.random() * rows * 3, rate=0.7 + rng.random() * 0.6,
                             text=rng.randrange(1000)) for _ in columns_x]
        self._glyphs = {}

    def _glyph(self, char, font):
        glyph = self._glyphs.get(char)
        if glyph is None:
            from PIL import Image, ImageDraw
            glyph = Image.new('L', (self.cell, self.cell), 0)
            ImageDraw.Draw(glyph).text((1, -1), char, font=font, fill=255)
            self._glyphs[char] = glyph
        return glyph

    def draw(self, image, frame, mode, color, font, gain=1.0):
        texts = LITANIES.get(mode, LITANIES['idle'])
        speed = RAIN_SPEED.get(mode, 0.35)
        trail = RAIN_TRAIL.get(mode, 6)
        period = self.rows + trail + 4
        for x, column in zip(self.columns_x, self.columns):
            position = column['offset'] + frame * speed * column['rate']
            head, cycle = position % period, int(position // period)
            # Each pass down the screen spells the next litany top to bottom.
            stream = texts[(column['text'] + cycle) % len(texts)] + '  '
            for k in range(trail):
                row = int(head) - k
                if not 0 <= row < self.rows:
                    continue
                char = stream[row % len(stream)]
                if char == ' ':
                    continue
                fade = (1.0 - k / trail) ** 1.6 * gain
                if k == 0:
                    tint = tuple(min(255, int(c * 0.5 + 255 * 0.5 * gain)) for c in color)
                else:
                    tint = tuple(int(c * fade) for c in color)
                image.paste(tint, (x, self.top + row * self.cell), self._glyph(char, font))


def _lens_mask(image, eye_box):
    """The other eye: dark lens pixels mirrored from the red eye's position."""
    if eye_box is None:
        return set()
    width, height = image.size
    x0, y0, x1, y1 = eye_box
    cx, cy = width - 1 - (x0 + x1) / 2, (y0 + y1) / 2
    radius = max(x1 - x0, y1 - y0) / 2 + 2
    pixels = image.load()
    mask = set()
    for y in range(max(0, int(cy - radius)), min(height, int(cy + radius) + 1)):
        for x in range(max(0, int(cx - radius)), min(width, int(cx + radius) + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 > radius * radius:
                continue
            r, g, b = pixels[x, y]
            if r + g + b < 260:  # dark glass, not the brass ring or highlights
                mask.add((x, y))
    return mask
