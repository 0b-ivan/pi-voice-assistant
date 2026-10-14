"""Menu structure and how the PiTFT draws it (icons, values, hints, pages)."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import display  # noqa: E402
import menu  # noqa: E402


class Capture:
    def image(self, image, rotation=0):
        self.picture = image


# Every option with its longest value, so overlaps would show.
LONG = dict(opt_cpu='balanced', opt_power_notices='off', opt_alarms='on', opt_led='on',
            opt_wlan='off', opt_server='none', opt_llm='local', opt_wake='none',
            opt_cue='none', opt_persona='servitor', opt_voice='natural', opt_lore='light',
            opt_emotions='on', opt_bt='none', menu_page='list')


class StructureTests(unittest.TestCase):
    def test_top_level_fits_without_scrolling(self):
        self.assertLessEqual(len(menu.TOP), display.MENU_ROWS)
        for group, items in menu.GROUPS.items():
            self.assertEqual(items[-1], 'back', group)
            self.assertLessEqual(len(items), display.MENU_ROWS, group)

    def test_every_item_has_a_label_and_every_group_a_hint(self):
        for item in menu.ITEMS + tuple(menu.GROUPS):
            self.assertIn(item, menu.LABELS)
        for item in menu.TOP:
            self.assertTrue(menu.hint(item), item)
        self.assertEqual(menu.hint('persona', 'persona'), menu.HINTS['persona_item'])

    def test_energy_status_opens_a_page_and_any_key_returns(self):
        m = menu.Menu()
        m.select('energy_status')
        self.assertEqual(m.confirm(1), 'energy_status')
        self.assertTrue(m.open)
        self.assertEqual(m.page, 'energy')
        m.move(1, 2)                                   # a button leaves the page ...
        self.assertEqual((m.page, m.items[m.index]), ('list', 'energy_status'))  # ... keeps the row
        m.confirm(3)
        self.assertFalse(m.back(4))                    # B leaves the page too
        self.assertEqual(m.page, 'list')

    def test_device_settings_moved_to_system(self):
        for item in ('screen', 'led', 'alarms'):
            self.assertIn(item, menu.GROUPS['system'])
        self.assertNotIn('device', menu.GROUPS)


class StatusFileTests(unittest.TestCase):
    def test_power_notices_and_energy_page_reach_the_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text(json.dumps(dict(opt_power_notices='on', menu_index=0,
                                            menu_group='energy', menu_page='energy')))
            status = display.read_status(path)
        self.assertEqual(status['opt_power_notices'], 'on')
        self.assertEqual(status['menu_page'], 'energy')
        self.assertEqual(display._menu_value('power_notices', status), 'AN')


class DrawTests(unittest.TestCase):
    def render(self, **status):
        out = Capture()
        display.render_menu(out, dict(LONG, **status),
                            dict(battery=dict(percent=100, mv=4200, plugged=True,
                                              charging=False)))
        return out.picture

    def test_labels_never_run_into_values(self):
        from PIL import Image, ImageDraw
        draw = ImageDraw.Draw(Image.new('RGB', (1, 1)))
        for group in (None, *menu.GROUPS):
            for item in menu.entries(group):
                value = (display._menu_summary(item, LONG) if group is None
                         else display._menu_value(item, LONG))
                label = display._fit(draw, menu.label(item, group), 14, 200)
                x = 34 if group is None else 16
                end = 226 - (10 if menu.is_group(item, group) else 0)
                if value:
                    end -= draw.textlength(value, font=display.font(11 if group is None else 12)) + 6
                fitted = display._fit(draw, menu.label(item, group), 14, end - x)
                self.assertLessEqual(x + draw.textlength(fitted, font=display.font(14)), end,
                                     (group, item))
                if group is None:
                    self.assertEqual(fitted, label, item)        # top level: never cut

    def test_every_screen_renders_and_scrolls_long_lists(self):
        for group in (None, *menu.GROUPS):
            for index in range(len(menu.entries(group))):
                picture = self.render(menu_group=group, menu_index=index) if group else \
                    self.render(menu_index=index)
                self.assertEqual(picture.size, (display.WIDTH, display.HEIGHT))

    def test_selected_danger_row_is_marked(self):
        index = menu.GROUPS['energy'].index('energy_shutdown')
        picture = self.render(menu_group='energy', menu_index=index)
        footer = picture.crop((12, 200, 228, 215)).convert('RGB').tobytes()
        pixels = zip(footer[0::3], footer[1::3], footer[2::3])
        self.assertTrue(any(r > 200 and g < 140 for r, g, b in pixels))

    def test_energy_page_rows(self):
        battery = dict(percent=80, mv=4020, plugged=True, charging=True,
                       output_current_ma=350, board_c=38)
        cpu = dict(profile='eco', base='eco', warning=None, mhz=1000)
        rows = display.energy_rows(battery, cpu, 47, 0, 'power')
        labels = [label for label, _, _ in rows]
        self.assertEqual(labels, ['Akku', 'Versorgung', 'Verbrauch', 'Akkumodul', 'CPU',
                                  'Gewählt', 'CPU-Temp.', 'Drosselung'])
        self.assertIn(('Versorgung', 'Netz · lädt', display.MENU_TEXT), rows)
        self.assertEqual(dict((l, v) for l, v, _ in rows)['Gewählt'], 'POWER')
        rows = display.energy_rows(None, None, None, 0x1)
        self.assertEqual(rows[0][:2], ('Akku', 'keine Daten'))
        self.assertEqual(rows[-1][:2], ('Drosselung', 'UNTERSPANNUNG!'))
        out = Capture()
        display.render_energy(out, display.energy_rows(battery, cpu, 47, 0, 'eco'))
        self.assertEqual(out.picture.size, (display.WIDTH, display.HEIGHT))


if __name__ == '__main__':
    unittest.main()
