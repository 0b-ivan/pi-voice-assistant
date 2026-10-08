#!/usr/bin/env python3
"""Render PiTFT voice screens to one PNG grid for review (no hardware needed).

Uses src/display.py exactly as the service does, so run it with the display
venv on the Pi to get the real fonts:

  /opt/pi-voice-assistant/.venv-display/bin/python scripts/render-display-preview.py out.png
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import display  # noqa: E402

BASE = dict(server='ok', temp_c=47, wifi_dbm=-60, clock='23:41')
SCREENS = (
    ('BEREIT', None, dict(BASE, last='Zuletzt 1,5 s · Server')),
    ('ZUHÖREN', None, dict(BASE, route='server')),
    ('ERKENNEN', ('Aufnahme auswerten', 'scan', 1), dict(BASE, route='server')),
    ('DENKEN', None, dict(BASE, route='server')),
    ('AUSGABE', ('Audio abspielen', 'speaker', 5), dict(BASE, route='server')),
    ('ERKENNEN', ('Sprache in Text', 'scan', 1), dict(BASE, server='down', route='pi',
                                                       wifi_dbm=-74)),
    ('BEREIT', None, dict(BASE, server='down', wifi_dbm=-81,
                          last='Zuletzt 9,8 s · Pi lokal')),
    ('BEREIT', None, dict(BASE, last='Zuletzt 3,4 s · Offline-LLM', temp_c=71)),
    ('FEHLER', None, dict(BASE, server='off')),
)


class Capture:
    def image(self, image, rotation=0):
        self.frame = image


def main():
    from PIL import Image
    target = Path(sys.argv[1] if len(sys.argv) > 1 else 'display-preview.png')
    columns, gap = 3, 12
    rows = (len(SCREENS) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * (display.WIDTH + gap) + gap,
                              rows * (display.HEIGHT + gap) + gap), (28, 28, 28))
    for index, (state, details, info) in enumerate(SCREENS):
        capture = Capture()
        display.render_voice(capture, state, True, details, tick=3, elapsed=2, info=info)
        x = gap + (index % columns) * (display.WIDTH + gap)
        y = gap + (index // columns) * (display.HEIGHT + gap)
        sheet.paste(capture.frame, (x, y))
    sheet.save(target)
    print(target)


if __name__ == '__main__':
    main()
