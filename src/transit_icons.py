"""Pixel pictograms for the journey screen (display.render_journeys).

Drawn as 16x16 sprites (side views, nose to the right) and scaled up with
nearest-neighbour like the skull and the weather pictograms. One sprite per
kind of vehicle from transit.classify; the colour tells them apart at a
glance: tram amber, bus blue, regional train red, S-Bahn green, ICE white
with the red stripe. A tiny walker marks the walk time. Pillow only.
"""
GRID = 16

TRAM = (
    '................',
    '.....KKKKKK.....',
    '.......KK.......',
    '......KKKK......',
    '.BBBBBBBBBBBBBB.',
    'BBBBBBBBBBBBBBBB',
    'BWWBWWBWWBWWBWWB',
    'BWWBWWBWWBWWBWWB',
    'BWWBWWBWWBWWBWWB',
    'BBBBBBBBBBBBBBBB',
    'BDDDDDDDDDDDDDBL',
    'BBBBBBBBBBBBBBBB',
    '.KKKK......KKKK.',
    '..KK........KK..',
    '................',
    '................',
)
BUS = (
    '................',
    '................',
    '................',
    '.BBBBBBBBBBBBBB.',
    'BBBBBBBBBBBBBBBB',
    'BWWWBWWWBWWWBWWW',
    'BWWWBWWWBWWWBWWW',
    'BWWWBWWWBWWWBWWW',
    'BBBBBBBBBBBBBBBB',
    'BDDDDDDDDDDDDDBL',
    'BBBBBBBBBBBBBBBB',
    '.BBKKBBBBBBKKBB.',
    '..KKKK....KKKK..',
    '...KK......KK...',
    '................',
    '................',
)
TRAIN = (
    '................',
    '................',
    '................',
    'BBBBBBBBBBBB....',
    'BBBBBBBBBBBBBB..',
    'BWWBWWBWWBWWWBB.',
    'BWWBWWBWWBWWWWBB',
    'BWWBWWBWWBWWWWWB',
    'BBBBBBBBBBBBBBBB',
    'SSSSSSSSSSSSSSSL',
    'BBBBBBBBBBBBBBBB',
    '.KKKKKKKKKKKKKK.',
    '.KK.KK....KK.KK.',
    '................',
    '................',
    '................',
)
ICE = (
    '................',
    '................',
    '................',
    '................',
    'SSSSSSSSSSS.....',
    'SSSSSSSSSSSSS...',
    'SWWSWWSWWSWWWSS.',
    'SWWSWWSWWSWWWWSS',
    'SSSSSSSSSSSSSSSS',
    'RRRRRRRRRRRRRRRL',
    'SSSSSSSSSSSSSSS.',
    '.KKKKKKKKKKKKK..',
    '.KK.KK....KK.KK.',
    '................',
    '................',
    '................',
)
WALKER = (
    '..PP....',
    '..PP....',
    '.PPPP...',
    'P.PP.P..',
    '..PP....',
    '.P..P...',
    '.P...P..',
    'P....P..',
)

DARK = (20, 24, 30)
WHEEL = (110, 116, 126)
LIGHT = (255, 232, 140)
WINDOW = (60, 120, 150)

# kind (transit.classify) -> sprite, body, stripe
KINDS = {
    'tram': (TRAM, (255, 176, 0), (150, 96, 0)),
    'bus': (BUS, (80, 170, 255), (40, 90, 150)),
    'regional': (TRAIN, (220, 40, 40), (240, 240, 240)),
    'sbahn': (TRAIN, (40, 170, 90), (240, 240, 240)),
    'train': (TRAIN, (130, 140, 160), (240, 240, 240)),
    'long': (ICE, (236, 238, 242), (220, 30, 30)),
    'other': (TRAIN, (130, 140, 160), (240, 240, 240)),
}
COLORS = {kind: body for kind, (_, body, _) in KINDS.items()}


def _sprite(rows, palette):
    from PIL import Image
    image = Image.new('RGB', (len(rows[0]), len(rows)), 'black')
    pixels = image.load()
    for y, row in enumerate(rows):
        for x, char in enumerate(row):
            if char in palette:
                pixels[x, y] = palette[char]
    return image


def icon(kind, scale=2):
    """The vehicle pictogram for ``kind``, scaled up (nearest-neighbour)."""
    from PIL import Image
    rows, body, stripe = KINDS.get(kind, KINDS['other'])
    palette = dict(B=body, W=WINDOW, K=WHEEL, L=LIGHT, D=stripe, S=stripe if rows is not ICE
                   else body, R=stripe)
    image = _sprite(rows, palette)
    return image.resize((GRID * scale, GRID * scale), Image.NEAREST) if scale != 1 else image


def walker(color=(170, 176, 186)):
    """8x8 pedestrian for the walk time."""
    return _sprite(WALKER, dict(P=color))
