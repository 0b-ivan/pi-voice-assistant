"""Billy's face for the PiTFT (persona "mensch"): the Doom status-bar face.

The artwork is not part of this public repository (id Software's sprites);
it is read from PI_DISPLAY_FACE on the Pi: the usual sheet with five rows
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


def _factor(width, height, box=BOX):
    """Integer up- or downscaling so the largest sprite fits the box."""
    if width <= box[0] and height <= box[1]:
        return max(1, min(box[0] // width, box[1] // height)), 1
    return 1, max(-(-width // box[0]), -(-height // box[1]))


class Face:
    def __init__(self, path=FACE_FILE, backdrop=(0, 0, 0)):
        from PIL import Image
        sprites = slice_sheet(Image.open(path))
        width = max(sprite.width for sprite in sprites.values())
        height = max(sprite.height for sprite in sprites.values())
        up, down = _factor(width, height)
        self.size = (width * up // down, height * up // down)
        self.frames = {}
        for key, sprite in sprites.items():
            sprite = sprite.resize((sprite.width * up // down, sprite.height * up // down),
                                   Image.NEAREST)
            canvas = Image.new('RGB', self.size, backdrop)
            # Bottom-aligned and centred: the neck stays put when the head turns.
            canvas.paste(sprite, ((self.size[0] - sprite.width) // 2,
                                  self.size[1] - sprite.height), sprite)
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


def choose(state, now, level=0.0, battery=None, alarm=False, hushed_at=None):
    """The face to show: (row, column) or 'god'/'dead'.

    state: the display state (BEREIT, ZUHÖREN, DENKEN, SPRECHEN ...);
    level: speech loudness 0..1 while speaking; battery: power.Battery
    reading; alarm: a critical alarm is active; hushed_at: time.time() of
    the last cancel ("Stop", "Klappe halten", B).
    """
    percent = (battery or {}).get('percent')
    if percent is not None and percent <= 3 and not (battery or {}).get('plugged'):
        return 'dead'
    row = health_row(percent)
    if state in SPEAKING:
        loud = max(0.0, min(1.0, level))
        return (row, 'ouch' if loud > 0.6 else 'teeth' if loud > 0.25 else 'look')
    if hushed_at is not None and 0 <= now - hushed_at < HUSH_SECONDS:
        return (row, 'ouch' if now - hushed_at < 0.4 else 'turn_a')
    if state in THINKING:
        return (row, ('turn_a', 'look', 'turn_b', 'look')[int(now / 0.6) % 4])
    if state == 'ZUHÖREN':
        return (row, 'look')
    if alarm:
        return (row, 'ouch' if int(now) % 2 == 0 else 'look')
    if (battery or {}).get('charging') and int(now) % 8 == 0:
        return 'god'      # charging: a short flash of invulnerability now and then
    return (row, _glance(now))
