"""Billy's face for the PiTFT (persona "mensch"): the Doom status-bar face.

The sprites (id Software) ship as assets/display/doom-faces.png and are
installed to PI_DISPLAY_FACE on the Pi: the usual sheet with five rows
(health 100 % ... 0 %) of eight faces, plus "god" after the first row and
"dead" after the last, on a transparent background. Sprites are found by
their transparent gaps, scaled by an integer factor only (crisp pixels) and
prepared once; a frame only pastes one finished image.

Which face is shown is decided by ``choose()`` from what the unit is doing:
the battery picks the row (a full battery is a clean face), the state and
the speech loudness pick the column. Without the sheet the skull stays.
"""
import os
from pathlib import Path

FACE_FILE = Path(os.environ.get('PI_DISPLAY_FACE',
                                '/opt/pi-voice-assistant/models/display/doom-faces.png'))
# Columns of every row, in the order of the original status bar sprites.
COLUMNS = ('look', 'look_a', 'look_b', 'turn_a', 'turn_b', 'ouch', 'grin', 'teeth')
ROWS = 5
BOX = (150, 136)    # the space between header and status line


def _segments(flags):
    """(start, end) of the runs of True."""
    runs, start = [], None
    for index, flag in enumerate(list(flags) + [False]):
        if flag and start is None:
            start = index
        elif not flag and start is not None:
            runs.append((start, index))
            start = None
    return runs


def slice_sheet(image):
    """{(row, column) or 'god'/'dead': RGBA sprite} from the sheet; raises
    ValueError when the layout is not the expected one."""
    image = image.convert('RGBA')
    alpha = image.getchannel('A')
    width, height = image.size
    data = alpha.load()
    rows = _segments(any(data[x, y] for x in range(width)) for y in range(height))
    if len(rows) != ROWS:
        raise ValueError(f'expected {ROWS} rows of faces, found {len(rows)}')
    sprites = {}
    for row, (top, bottom) in enumerate(rows):
        columns = _segments(any(data[x, y] for y in range(top, bottom)) for x in range(width))
        if len(columns) < len(COLUMNS):
            raise ValueError(f'row {row}: expected {len(COLUMNS)} faces, found {len(columns)}')
        for column, (left, right) in enumerate(columns):
            box = image.crop((left, top, right, bottom))
            box = box.crop(box.getbbox())
            if column < len(COLUMNS):
                sprites[(row, COLUMNS[column])] = box
            elif row == 0:
                sprites['god'] = box
            elif row == ROWS - 1:
                sprites['dead'] = box
    return sprites


# Frames made from the sheet's own pixels for smoother animation. Work is done
# at Doom's pixel size: eyes are where the straight faces differ (they only
# glance), the mouth opens at the calm face's lip line.
DERIVED = ('blink', 'squint', 'talk_e', 'talk_half', 'talk_open', 'talk_round', 'wide')


def _shrink(sprite, scale):
    from PIL import Image
    return sprite.resize((sprite.width // scale, sprite.height // scale), Image.NEAREST)


def _offset(base, other, reach=3):
    """Where ``other``'s pixel (0, 0) lands on ``base`` when the two match best."""
    from PIL import Image
    pa = base.load()
    best = None
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            at = ((base.width - other.width) // 2 + dx, base.height - other.height + dy)
            canvas = Image.new('RGBA', base.size, (0, 0, 0, 0))
            canvas.paste(other, at)
            pb = canvas.load()
            misses = sum(pa[x, y] != pb[x, y] for y in range(base.height)
                         for x in range(base.width))
            if best is None or misses < best[0]:
                best = (misses, at)
    return best[1]


def _aligned(base, other, reach=2):
    """``other`` moved onto ``base``'s canvas where the two match best."""
    from PIL import Image
    best = None
    pa = base.load()
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            canvas = Image.new('RGBA', base.size, (0, 0, 0, 0))
            canvas.paste(other, ((base.width - other.width) // 2 + dx,
                                 base.height - other.height + dy))
            pb = canvas.load()
            misses = sum(pa[x, y] != pb[x, y] for y in range(base.height)
                         for x in range(base.width))
            if best is None or misses < best[0]:
                best = (misses, canvas)
    return best[1]


def _diff(a, b, top=0.0, bottom=1.0):
    pa, pb = a.load(), b.load()
    width, height = a.size
    return {(x, y) for y in range(int(height * top), int(height * bottom))
            for x in range(width) if pa[x, y] != pb[x, y] and pa[x, y][3] and pb[x, y][3]}


def _columns(mask):
    """{x: (top, bottom)} of a mask."""
    spans = {}
    for x, y in mask:
        top, bottom = spans.get(x, (y, y))
        spans[x] = (min(top, y), max(bottom, y))
    return spans


def _skinlike(pixel):
    r, g, b, a = pixel
    return a and r - b > 50 and g > 40 and r > g      # warm skin, not grey eye or red blood


def _grey(pixel):
    r, g, b, a = pixel
    return a and r >= 50 and abs(r - g) < 16 and abs(g - b) < 16      # eye white


def _dark(pixel):
    return pixel[3] and sum(pixel[:3]) < 200                            # pupil, outline


def _bloodshot(pixel):
    r, g, b, a = pixel
    return a and r >= 100 and g < r * 0.7 and b < r * 0.7 and abs(g - b) < 16   # red white


def _eyelike(pixel):
    return _grey(pixel) or _dark(pixel) or _bloodshot(pixel)


def eye_mask(look, glance):
    """The eyes of the clean face as (eye, lid): ``eye`` are the white and pupil
    pixels (Doom eyes are one or two pixel rows), ``lid`` the dark outline just
    above them. Found where the glance moves the pupils; the brows stay out."""
    pixels = look.load()
    moved = _diff(look, _aligned(look, glance), 0.35, 0.62)
    rows = sorted({y for x, y in moved if _grey(pixels[x, y]) or _dark(pixels[x, y])})
    rows = [y for y in rows if any(_grey(pixels[x, y]) for x in range(look.width))]
    if not moved or not rows:
        return set(), set()
    left = min(x for x, _ in moved) - 1
    right = max(x for x, _ in moved) + 1
    # Dark pixels count only next to an eye white: the nose bridge between the
    # eyes is dark too and must stay.
    whites = {(x, y) for y in rows for x in range(look.width) if _grey(pixels[x, y])}
    eye = {(x, y) for y in rows for x in range(max(0, left), min(look.width, right + 1))
           if (x, y) in whites or (_dark(pixels[x, y])
                                   and any((x + d, y) in whites for d in (-2, -1, 1, 2)))}
    top = min(rows)
    lid = {(x, top - 1) for x, y in eye if y == top and top > 0 and _dark(pixels[x, top - 1])}
    return eye, lid


def _lid(sprite, mask, closed):
    """Closed (blink) or half-closed (squint) eyes in pixel-art style: the dark
    upper outline becomes lid skin, the eye a dark lash line (closed) or a
    dimmed eye (half). Only pixels that are still eye in this row are touched,
    so hair hanging over the eyes stays."""
    from collections import Counter
    eye, lid = mask
    out = sprite.copy()
    pixels = out.load()
    width, height = out.size
    cells = [(x, y) for x, y in eye | lid if x < width and y < height]
    if not cells:
        return out
    top, bottom = min(y for _, y in cells), max(y for _, y in cells)
    left, right = min(x for x, _ in cells), max(x for x, _ in cells)
    tones = Counter(pixels[x, y] for y in range(max(0, top - 3), min(height, bottom + 4))
                    for x in range(left, right + 1) if _skinlike(pixels[x, y]))
    if not tones:
        return out
    skin = tones.most_common(1)[0][0]
    shade = tuple(int(c * 0.75) for c in skin[:3]) + (255,)     # lid in the socket's shadow
    lash = tuple(int(c * 0.35) for c in skin[:3]) + (255,)
    # Eyes can sit a pixel off in a bloodier row: take neighbouring whites too.
    near = {(x + dx, y + dy) for x, y in eye for dx in (-1, 0, 1) for dy in (-1, 0, 1)}
    eye = eye | {(x, y) for x, y in near if 0 <= x < width and 0 <= y < height
                 and (_grey(pixels[x, y]) or _bloodshot(pixels[x, y]))}
    for x, y in lid:
        if x < width and y < height and _dark(pixels[x, y]):
            pixels[x, y] = shade
    for x, y in eye:
        if x >= width or y >= height:
            continue
        pixel = pixels[x, y]
        if not _eyelike(pixel):
            continue
        if closed:
            pixels[x, y] = lash
        elif not _dark(pixel):          # half-closed: the white mostly under the lid
            pixels[x, y] = tuple((c + 3 * d) // 4 for c, d in zip(pixel[:3], lash[:3])) + (255,)
    return out


def _patch(base, source, mask):
    out = base.copy()
    pixels, src = out.load(), source.load()
    for x, y in mask:
        pixels[x, y] = src[x, y]
    return out


def _brightness(pixel):
    return sum(pixel[:3]) if pixel[3] else 765


def _lower_offset(look, clean, reach=(2, 6)):
    """(dx, dy) that moves the clean face's lower half (nose, mouth, chin) onto
    ``look``: the hurt faces have longer hair and blood, their features sit up
    to four pixels lower and the head outline does not show it."""
    pa, pb = clean.load(), look.load()
    best = None
    for dy in range(-reach[0], reach[1] + 1):
        for dx in range(-reach[0], reach[0] + 1):
            cost = count = 0
            for y in range(clean.height * 4 // 7, clean.height):     # below the eyes
                for x in range(clean.width):
                    a = pa[x, y]
                    if not a[3]:
                        continue
                    count += 1
                    if not (0 <= x + dx < look.width and 0 <= y + dy < look.height):
                        cost += 300
                        continue
                    b = pb[x + dx, y + dy]
                    cost += sum(abs(a[i] - b[i]) for i in range(3)) if b[3] else 300
            score = cost / max(1, count)
            if best is None or score < best[0]:
                best = (score, dx, dy)
    return best[1], best[2]


def _seam(look, lips, reach=2):
    """``lips`` moved up or down to this face's own closed mouth: the row near
    the guess that is darkest against the rows above and below (the lip line
    lies between bright lips; nostrils and blood are dark over several rows)."""
    row, left, right = lips
    pixels = look.load()

    def line(y):
        return sum(_brightness(pixels[x, y]) for x in range(left, right + 1))

    def depth(y):
        if not 0 < y < look.height - 2:
            return float('-inf')
        return min(line(y - 1), line(y + 1)) - line(y) - abs(y - row)
    return max(range(row - reach, row + reach + 1), key=depth), left, right


def _lipline(look):
    """(row, left, right) of the closed lips: the orange line in the lower face."""
    pixels = look.load()
    best = None
    for y in range(int(look.height * 0.7), int(look.height * 0.9)):
        xs = [x for x in range(look.width) if _orange(pixels[x, y])]
        if xs and (best is None or len(xs) > len(best[1])):
            best = (y, xs)
    if best is None or len(best[1]) < 3:
        return None
    return best[0], min(best[1]), max(best[1])


JAW = 1                         # Doom pixels the jaw drops (room below every face)
TALK = ('talk_e', 'talk_half', 'talk_open', 'talk_round')


def _orange(pixel):
    r, g, b, a = pixel
    return a and r > 130 and 70 <= g <= 110 and b < 40


# Inside of the mouth in the colours Doom itself uses for "ouch" and "teeth":
# black, grey teeth, a dark red tongue.
INSIDE = {'k': (0, 0, 0), 'd': (47, 47, 47), 'g': (91, 91, 91), 'G': (119, 119, 119),
          'w': (203, 203, 203), 'W': (219, 219, 219), 't': (127, 27, 27),
          'T': (143, 43, 43)}
# Rows of the open mouth from the closed lip line down, across the width of
# the lips. 'L' is the lip line's own pixel, '.' the lower lip's, any other
# letter from INSIDE.
MOUTHS = {
    'talk_half': ('dgGGgd', 'LkkkkL'),              # a (quiet): upper teeth, dark
    'talk_open': ('dgGGgd', 'kkkkkk', 'kTttTk'),    # a (loud): wide open, tongue
    'talk_round': ('.dkkd.', '.LkkL.'),             # o/u: a small round hole
    'talk_e': ('dwWWwd', 'LkkkkL'),                 # e/i: spread, teeth showing
}


def _mouths(look, lips):
    """Talking mouths, as a speaking face moves: everything above the closed
    lip line stays, the mouth opens on that line and the jaw with the lower
    lip drops by JAW pixels. A taller mouth takes the shadow under the lower
    lip instead of stretching the face further (a longer jaw stretches the
    blood on the chin of the hurt faces).

    Frames are JAW pixels taller than the calm face, with the head in the same
    place (the room below is transparent while the jaw is up)."""
    from PIL import Image
    row, left, right = lips
    width, height = look.size
    src = look.load()

    def opened(shape):
        grow = len(shape) - 1                    # rows the mouth is taller than the lip line
        skip = grow - min(grow, JAW)             # rows under the lower lip given up
        lower = look.crop((0, row + 1, width, height))
        if skip:
            rest = look.crop((0, row + 2 + skip, width, height))
            lower = Image.new('RGBA', (width, 1 + rest.height), (0, 0, 0, 0))
            lower.paste(look.crop((0, row + 1, width, row + 2)), (0, 0))
            lower.paste(rest, (0, 1))
        out = Image.new('RGBA', (width, height + JAW), (0, 0, 0, 0))
        out.paste(look.crop((0, 0, width, row + 1)), (0, 0))
        out.paste(lower, (0, row + 1 + grow))
        pixels = out.load()
        for y in range(row + 1, row + 1 + grow):  # the cheeks stretch with the jaw
            for x in range(width):
                pixels[x, y] = src[x, row]
        span = right - left + 1
        for index, letters in enumerate(shape):
            cut = max(0, len(letters) - span) // 2   # narrower lips: the middle
            letters = letters[cut:cut + span].center(span, '.')
            for x, letter in zip(range(left, right + 1), letters):
                if letter == 'L':
                    pixels[x, row + index] = src[x, row]
                elif letter == '.':
                    pixels[x, row + index] = src[x, min(row + 1, height - 1)]
                else:
                    pixels[x, row + index] = INSIDE[letter] + (255,)
        return out

    return {name: opened(shape) for name, shape in MOUTHS.items()}


def derive(sprites, scale):
    """Extra frames per health row: blinking, half-closed eyes, a half and a
    fully open talking mouth with calm eyes, raised eyebrows and wide eyes."""
    from PIL import Image
    out = {}
    if (0, 'look') not in sprites:
        return out
    # The eyes sit in the same place in every row; blood and hair make the
    # bloodier rows unreliable, so the clean face defines them.
    clean = _shrink(sprites[(0, 'look')], scale)
    glance = sprites.get((0, 'look_b')) or sprites.get((0, 'look_a'))
    eyes = eye_mask(clean, _shrink(glance, scale)) if glance is not None else (set(), set())
    lips = _lipline(clean)
    for row in range(ROWS):
        if (row, 'look') not in sprites:
            continue
        look = _shrink(sprites[(row, 'look')], scale)
        made = {}
        if lips:
            dx, dy = _lower_offset(look, clean) if row else (0, 0)
            where = _seam(look, (lips[0] + dy, lips[1] + dx, lips[2] + dx))
        own = sprites.get((row, 'look_b')) or sprites.get((row, 'look_a'))
        mask = eye_mask(look, _shrink(own, scale)) if own is not None and row else eyes
        if not (mask[0] and len(mask[0]) >= len(eyes[0]) * 0.6
                and len({y for _, y in mask[0]}) <= 2):
            # Not found here (bloodshot, hair): the clean face's eyes, moved to
            # where this face sits (longer hair pushes it down).
            dx, dy = _offset(look, clean)
            mask = tuple({(x + dx, y + dy) for x, y in part
                          if 0 <= x + dx < look.width and 0 <= y + dy < look.height}
                         for part in eyes)
        if mask[0]:
            made['blink'] = _lid(look, mask, closed=True)
            made['squint'] = _lid(look, mask, closed=False)
        for name, source, band in (('wide', 'ouch', (0.25, 0.62)),):
            if (row, source) in sprites:
                other = _aligned(look, _shrink(sprites[(row, source)], scale))
                region = _diff(look, other, *band)
                if region:
                    made[name] = _patch(look, other, region)
        if lips:
            made.update(_mouths(look, where))
        for name, image in made.items():
            out[(row, name)] = image.resize((image.width * scale, image.height * scale),
                                            Image.NEAREST)
    return out


def _factor(width, height, box=BOX):
    """Integer up- or downscaling so the largest sprite fits the box."""
    if width <= box[0] and height <= box[1]:
        return max(1, min(box[0] // width, box[1] // height)), 1
    return 1, max(-(-width // box[0]), -(-height // box[1]))


class Face:
    def __init__(self, path=FACE_FILE, backdrop=(0, 0, 0)):
        from PIL import Image
        sprites = slice_sheet(Image.open(path))
        scale = max(1, round(sprites[(0, 'look')].width / 24))   # sheet pixels per Doom pixel
        sprites.update(derive(sprites, scale))
        # Talking frames carry JAW extra rows for the dropped chin; every other
        # face sits that much higher, so the head does not jump while speaking.
        room = JAW * scale

        def extra(key):
            return 0 if isinstance(key, tuple) and key[1] in TALK else room

        width = max(sprite.width for sprite in sprites.values())
        height = max(sprite.height + extra(key) for key, sprite in sprites.items())
        up, down = _factor(width, height)
        self.size = (width * up // down, height * up // down)
        self.frames = {}
        for key, sprite in sprites.items():
            sprite = sprite.resize((sprite.width * up // down, sprite.height * up // down),
                                   Image.NEAREST)
            canvas = Image.new('RGB', self.size, backdrop)
            # Bottom-aligned and centred: the neck stays put when the head turns.
            canvas.paste(sprite, ((self.size[0] - sprite.width) // 2,
                                  self.size[1] - sprite.height - extra(key) * up // down),
                         sprite)
            self.frames[key] = canvas

    def frame(self, key):
        return self.frames.get(key) or self.frames[(key[0], 'look')]


def health_row(battery_percent):
    """Battery as Doom health (same steps as the game): 100-80 % clean ...
    below 20 % bloody."""
    if battery_percent is None:
        return 0
    return max(0, min(ROWS - 1, (100 - int(battery_percent)) * ROWS // 101))


THINKING = ('VERSTEHEN', 'ERKENNEN', 'DENKEN', 'SYNTHESE', 'RENDERN')
SPEAKING = ('AUSGABE', 'SPRECHEN')
HUSH_SECONDS = 2.5    # after "Stop"/"Klappe halten": wince, then look away


def _glance(now):
    """Idle: mostly straight ahead, now and then a short look aside."""
    slot = int(now / 0.7)
    roll = (slot * 2654435761) % 97
    return 'look_a' if roll < 9 else 'look_b' if roll < 18 else 'look'


GOD_SECONDS = 2.0


MOOD_FROM = 0.3       # weaker feelings leave the face alone
BLINK_SECONDS = 0.15


def blinking(now, every=4.0, length=BLINK_SECONDS):
    """True during a blink: once per ``every`` seconds at an irregular moment."""
    slot = int(now / every)
    moment = ((slot * 2654435761) % 1000) / 1000 * (every - length)
    return 0 <= now - slot * every - moment < length


def _mood_face(emotion, level, now):
    """Idle face for a feeling (mood.EMOTIONS), or None to glance as usual."""
    if level < MOOD_FROM:
        return None
    if emotion == 'freudig':
        return 'grin' if int(now / 0.7) % 5 else _glance(now)
    if emotion == 'zufrieden':
        return 'grin' if int(now) % 4 == 0 else _glance(now)
    if emotion == 'gereizt':
        return 'teeth' if level >= 0.5 or int(now / 3) % 2 == 0 else 'look'
    if emotion == 'besorgt':
        return ('look_a', 'wide', 'look_b', 'wide')[int(now / 0.7) % 4]   # eyes darting
    if emotion == 'neugierig':
        return ('turn_a', 'wide', 'turn_b', 'look')[int(now / 2) % 4]
    if emotion == 'gelangweilt':
        return 'turn_b' if int(now / 5) % 3 else 'look'       # looking away
    if emotion == 'müde':
        return 'blink' if blinking(now, every=2.5, length=0.4) else 'squint'   # heavy lids
    return None


# Mouth codes from visemes.track() while speaking.
VISEMES = {'.': 'look', 'a': 'talk_half', 'A': 'talk_open', 'e': 'talk_e',
           'E': 'talk_e', 'o': 'talk_round', 'O': 'talk_round'}


def choose(state, now, level=0.0, battery=None, alarm=False, hushed_at=None, plugged_at=None,
           mood=None, viseme=None):
    """The face to show: (row, column) or 'god'/'dead'.

    state: the display state (BEREIT, ZUHÖREN, DENKEN, SPRECHEN ...);
    level: speech loudness 0..1 while speaking; battery: power.Battery
    reading; alarm: a critical alarm is active; hushed_at: time.time() of
    the last cancel ("Stop", "Klappe halten", B); mood: (emotion, 0..1);
    viseme: the mouth code of this moment of the reply, when known.
    """
    percent = (battery or {}).get('percent')
    if percent is not None and percent <= 3 and not (battery or {}).get('plugged'):
        return 'dead'
    row = health_row(percent)
    if state in SPEAKING:
        loud = max(0.0, min(1.0, level))
        if mood and mood[0] == 'gereizt' and mood[1] >= MOOD_FROM:   # talking angrily
            return (row, 'ouch' if loud > 0.6 else 'teeth' if loud > 0.25 else 'teeth')
        if viseme in VISEMES:          # the vowel being spoken
            mouth = VISEMES[viseme]
            if mouth == 'look' and blinking(now, every=5.0):
                return (row, 'blink')
            return (row, mouth)
        if blinking(now, every=5.0):
            return (row, 'blink')
        return (row, 'talk_open' if loud > 0.55 else 'talk_half' if loud > 0.2 else 'look')
    if hushed_at is not None and 0 <= now - hushed_at < HUSH_SECONDS:
        return (row, 'ouch' if now - hushed_at < 0.4 else 'turn_a')
    if state in THINKING:
        return (row, ('turn_a', 'look', 'turn_b', 'look')[int(now / 0.6) % 4])
    if state == 'ZUHÖREN':
        return (row, 'blink' if blinking(now) else 'look')
    if alarm:
        return (row, 'ouch' if int(now) % 2 == 0 else 'look')
    if plugged_at is not None and 0 <= now - plugged_at < GOD_SECONDS:
        return 'god'      # charger just connected: a short flash of invulnerability
    if mood:
        face = _mood_face(mood[0], mood[1], now)
        if face:
            return (row, face)
    if blinking(now):
        return (row, 'blink')
    return (row, _glance(now))


def resting(battery=None):
    """Dimmed rest screen: Billy dozes with closed eyes."""
    return (health_row((battery or {}).get('percent')), 'blink')
