"""Animated weather pictograms for the PiTFT, in the unit's pixel style.

Drawn on a 24x24 grid and scaled up with nearest-neighbour, so they look like
the skull's pixel art; the big icon gets dark scanlines like an old cogitator
screen. Colours come from the display's palette (amber like the status light,
the server cyan for water). ``frame`` drives the animation (about 8 per
second): sun rays pulse, clouds drift, rain and snow fall, lightning flashes.
Pillow only (the display venv has no numpy).
"""
import math

GRID = 24
SUN = (255, 176, 0)
SUN_CORE = (255, 214, 96)
CLOUD = (150, 156, 168)
CLOUD_DARK = (92, 98, 110)
WATER = (80, 170, 255)
SNOW = (232, 238, 248)
BOLT = (255, 232, 60)
FOG = (128, 134, 142)

KINDS = ('clear', 'partly', 'cloudy', 'fog', 'drizzle', 'rain', 'snow', 'thunder')


def kind(code):
    """WMO weather code -> pictogram kind."""
    if code in (0, 1):
        return 'clear'
    if code == 2:
        return 'partly'
    if code == 3:
        return 'cloudy'
    if code in (45, 48):
        return 'fog'
    if code in (51, 53, 55, 56, 57):
        return 'drizzle'
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return 'rain'
    if code in (71, 73, 75, 77, 85, 86):
        return 'snow'
    if code in (95, 96, 99):
        return 'thunder'
    return 'cloudy'


LABELS = {'clear': 'KLAR', 'partly': 'WOLKIG', 'cloudy': 'BEDECKT', 'fog': 'NEBEL',
          'drizzle': 'NIESELN', 'rain': 'REGEN', 'snow': 'SCHNEE', 'thunder': 'GEWITTER'}


def _sun(draw, cx, cy, radius, frame):
    # Eight rays; long and short swap every two frames, which reads as turning.
    for i in range(8):
        angle = i * math.pi / 4
        long_ray = (i + frame // 2) % 2 == 0
        inner, outer = radius + 2, radius + (5 if long_ray else 3)
        x0, y0 = cx + inner * math.cos(angle), cy + inner * math.sin(angle)
        x1, y1 = cx + outer * math.cos(angle), cy + outer * math.sin(angle)
        draw.line((round(x0), round(y0), round(x1), round(y1)), fill=SUN)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=SUN)
    draw.ellipse((cx - radius + 2, cy - radius + 2, cx + radius - 3, cy + radius - 3),
                 fill=SUN_CORE)


def _cloud(draw, x, y, color, shade):
    """A cloud with its top-left near (x, y), about 18 x 9 pixels."""
    draw.ellipse((x + 4, y, x + 12, y + 8), fill=color)
    draw.ellipse((x + 9, y + 2, x + 17, y + 9), fill=color)
    draw.ellipse((x, y + 3, x + 7, y + 9), fill=color)
    draw.rectangle((x + 3, y + 6, x + 15, y + 9), fill=color)
    draw.line((x + 2, y + 9, x + 15, y + 9), fill=shade)


def _drift(frame):
    return (0, 0, 1, 1, 1, 0, 0, -1)[(frame // 2) % 8]


def grid(name, frame=0):
    """The pictogram on its 24x24 grid (RGB, black background)."""
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (GRID, GRID), 'black')
    draw = ImageDraw.Draw(image)
    drift = _drift(frame)
    if name == 'clear':
        _sun(draw, 12, 12, 5, frame)
    elif name == 'partly':
        _sun(draw, 8, 8, 4, frame)
        _cloud(draw, 5 + drift, 11, CLOUD, CLOUD_DARK)
    elif name == 'cloudy':
        _cloud(draw, 1 - drift, 4, CLOUD_DARK, (60, 64, 72))
        _cloud(draw, 5 + drift, 10, CLOUD, CLOUD_DARK)
    elif name == 'fog':
        for row, y in enumerate((6, 10, 14, 18)):
            shift = (frame + row * 3) % 6
            for x in range(-6 + shift, GRID, 6):
                draw.line((x, y, x + 3, y), fill=FOG)
    elif name in ('drizzle', 'rain', 'thunder'):
        _cloud(draw, 3 + drift, 2, CLOUD_DARK if name == 'thunder' else CLOUD, CLOUD_DARK)
        length = 1 if name == 'drizzle' else 2
        for i, x in enumerate((6, 10, 14, 18)):
            y = 13 + (frame * 2 + i * 3) % 10
            if y + length < GRID:
                draw.line((x - (y - 13) // 4, y, x - (y - 13) // 4, y + length), fill=WATER)
        if name == 'thunder' and frame % 12 in (0, 1, 3):
            draw.polygon(((13, 11), (9, 17), (12, 17), (10, 23), (16, 15), (13, 15), (15, 11)),
                         fill=BOLT)
    elif name == 'snow':
        _cloud(draw, 3 + drift, 2, CLOUD, CLOUD_DARK)
        for i, x in enumerate((6, 11, 16, 19)):
            y = 13 + (frame + i * 4) % 10
            wiggle = (0, 1, 0, -1)[(frame // 2 + i) % 4]
            x += wiggle   # a small cross per flake
            draw.point(((x, y), (x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)), fill=SNOW)
    return image


def icon(name, frame=0, scale=4, scanlines=True):
    """The pictogram scaled up (nearest-neighbour), optionally with scanlines."""
    from PIL import Image
    image = grid(name, frame).resize((GRID * scale, GRID * scale), Image.NEAREST)
    if scanlines and scale >= 3:
        dark = Image.new('RGB', image.size, 'black')
        lines = Image.new('L', image.size, 0)
        for y in range(scale - 1, image.height, scale):
            lines.paste(150, (0, y, image.width, y + 1))
        image = Image.composite(dark, image, lines)
    return image
