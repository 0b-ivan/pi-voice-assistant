"""Effective CPU state and how the PiTFT shows it (ECO/NORMAL/POWER/overclock)."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import cpu_state  # noqa: E402
import display  # noqa: E402
import face  # noqa: E402
import skull  # noqa: E402

SHEET = ROOT / 'assets' / 'display' / 'doom-faces.png'
STOCK = 1_000_000


def policy(tmp, governor='ondemand', hw_max=STOCK, max_freq=None, cur=600_000):
    root = Path(tmp) / 'policy0'
    root.mkdir(exist_ok=True)
    (root / 'scaling_governor').write_text(governor + '\n')
    (root / 'cpuinfo_max_freq').write_text(f'{hw_max}\n')
    (root / 'scaling_max_freq').write_text(f'{max_freq or hw_max}\n')
    (root / 'scaling_cur_freq').write_text(f'{cur}\n')
    return root


def vcgencmd(arm_hz):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return Mock(stdout=f'frequency(48)={arm_hz}\n', returncode=0)
    return run, calls


class EffectiveStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def effective(self, throttled=0, arm_hz=1_200_000_000, stock=STOCK, **kw):
        run, calls = vcgencmd(arm_hz)
        state = cpu_state.read(policy(self.tmp.name, **kw), stock, throttled, run)
        return cpu_state.classify(state), calls

    def test_governors_map_to_the_three_profiles(self):
        for governor, profile in (('powersave', 'eco'), ('ondemand', 'balanced'),
                                  ('schedutil', 'balanced'), ('performance', 'power')):
            result, calls = self.effective(governor=governor)
            self.assertEqual(result['profile'], profile, governor)
            self.assertEqual(result['base'], profile)
            self.assertEqual(calls, [])      # stock clock: no vcgencmd call at all

    def test_unreadable_or_odd_governor_is_unknown(self):
        result, _ = self.effective(governor='userspace')
        self.assertEqual(result['profile'], 'unknown')
        state = cpu_state.read(Path(self.tmp.name) / 'missing', STOCK, 0)
        self.assertEqual(cpu_state.classify(state)['profile'], 'unknown')

    def test_performance_alone_is_not_an_overclock(self):
        result, _ = self.effective(governor='performance', hw_max=STOCK)
        self.assertEqual(result['profile'], 'power')
        self.assertEqual(result['mhz'], 1000)

    def test_kernel_limit_above_stock_is_an_overclock(self):
        for governor, base in (('ondemand', 'balanced'), ('performance', 'power')):
            result, calls = self.effective(governor=governor, hw_max=1_200_000)
            self.assertEqual(result, dict(profile='overclock', base=base, warning=None,
                                          mhz=1200))
            self.assertEqual(calls, [['vcgencmd', 'measure_clock', 'arm']])

    def test_powersave_on_an_overclocked_pi_is_eco(self):
        result, _ = self.effective(governor='powersave', hw_max=1_200_000)
        self.assertEqual(result['profile'], 'eco')

    def test_policy_cap_at_stock_is_no_overclock(self):
        result, _ = self.effective(hw_max=1_200_000, max_freq=STOCK)
        self.assertEqual(result['profile'], 'balanced')

    def test_performance_measured_at_stock_gets_no_gold(self):
        result, _ = self.effective(governor='performance', hw_max=1_200_000,
                                   arm_hz=1_000_000_000)
        self.assertEqual(result['profile'], 'power')

    def test_firmware_limits_suppress_the_overclock(self):
        for flags, warning in ((0x1, 'undervolt'), (0x50005, 'undervolt'),
                               (0x4, 'throttled'), (0x8, 'throttled'), (0x2, 'throttled')):
            result, _ = self.effective(hw_max=1_200_000, throttled=flags)
            self.assertEqual(result['warning'], warning, hex(flags))
            self.assertEqual(result['profile'], 'balanced')
        # Only "since boot" bits: no limit now, the overclock stays visible.
        result, _ = self.effective(hw_max=1_200_000, throttled=0x50000)
        self.assertEqual((result['profile'], result['warning']), ('overclock', None))

    def test_unknown_flags_or_board_never_claim_an_overclock(self):
        result, _ = self.effective(hw_max=1_200_000, throttled=None)
        self.assertEqual(result['profile'], 'unknown')
        result, calls = self.effective(hw_max=1_200_000, stock=None)
        self.assertEqual(result['profile'], 'balanced')
        self.assertEqual(calls, [])

    def test_stock_clock_from_board_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / 'model'
            model.write_bytes(b'Raspberry Pi Zero 2 W Rev 1.0\x00')
            self.assertEqual(cpu_state.stock_khz(model, env={}), STOCK)
            model.write_bytes(b'Raspberry Pi 4 Model B Rev 1.5\x00')
            self.assertIsNone(cpu_state.stock_khz(model, env={}))
            self.assertEqual(cpu_state.stock_khz(model, env={'PI_DISPLAY_CPU_STOCK_KHZ':
                                                             '1500000'}), 1_500_000)
            self.assertIsNone(cpu_state.stock_khz(Path(tmp) / 'none', env={}))

    def test_measure_clock_failure_is_none(self):
        self.assertIsNone(cpu_state.arm_clock_khz(Mock(side_effect=OSError)))
        self.assertIsNone(cpu_state.arm_clock_khz(Mock(return_value=Mock(stdout='error'))))
        self.assertEqual(cpu_state.arm_clock_khz(vcgencmd(1_200_000_000)[0]), 1_200_000)


def cpu(profile, base=None, warning=None, mhz=1000):
    return dict(profile=profile, base=base or profile, warning=warning, mhz=mhz)


class BadgeTests(unittest.TestCase):
    def test_tags_follow_the_effective_profile(self):
        self.assertEqual(display.cpu_badge(cpu('eco'))[0], 'ECO')
        self.assertIsNone(display.cpu_badge(cpu('balanced')))      # the usual look
        self.assertEqual(display.cpu_badge(cpu('power'))[0], 'PWR')
        self.assertEqual(display.cpu_badge(cpu('unknown'))[0], '?')
        self.assertIsNone(display.cpu_badge(None))
        self.assertEqual(display.cpu_badge(cpu('overclock', 'balanced', mhz=1200)),
                         ('OC', display.CPU_GOLD, display.CPU_GOLD))

    def test_warnings_replace_the_profile_and_drop_gold(self):
        text, color, outline = display.cpu_badge(cpu('overclock', 'balanced', 'undervolt'))
        self.assertEqual(text, 'UV')
        self.assertNotEqual(outline, display.CPU_GOLD)
        self.assertEqual(display.cpu_badge(cpu('power', warning='throttled'))[0], 'DRS')

    def test_choice_differing_from_reality_is_marked(self):
        # Chose POWER, battery standby left powersave running.
        self.assertEqual(display.cpu_badge(cpu('eco'), 'power'),
                         ('ECO', display.CPU_TAG['eco'][1], display.CPU_MISMATCH))
        self.assertEqual(display.cpu_badge(cpu('balanced'), 'eco')[::2],
                         ('NRM', display.CPU_MISMATCH))
        self.assertEqual(display.cpu_badge(cpu('eco'), 'eco')[2], display.CPU_TAG['eco'][1])
        self.assertEqual(display.cpu_badge(cpu('overclock', 'power'), 'power')[2],
                         display.CPU_GOLD)

    def header(self, **kw):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (display.WIDTH, 41), 'black')
        display._draw_header(ImageDraw.Draw(image), info(temp_c=48, **kw))
        return image

    def test_tag_sits_between_name_and_temperature(self):
        # Widest case: memory mark and a full battery.
        full = dict(percent=100, mv=4200, plugged=True, charging=False)
        for profile in ('eco', 'power', 'overclock'):
            plain = self.header(memory='on', battery=full)
            tagged = self.header(memory='on', battery=full, cpu=cpu(profile))
            changed = [x for x in range(display.WIDTH)
                       if plain.crop((x, 0, x + 1, 41)).tobytes()
                       != tagged.crop((x, 0, x + 1, 41)).tobytes()]
            self.assertTrue(changed, profile)
            self.assertGreater(min(changed), 110, profile)  # right of the memory mark
            self.assertLess(max(changed), 135, profile)     # left of "48°C"

    def test_skull_look_per_profile(self):
        self.assertEqual(display.cpu_look(None), display.SKULL_LOOK['balanced'])
        self.assertLess(display.cpu_look(cpu('eco'))['gain'], 1.0)
        self.assertLess(display.cpu_look(cpu('eco'))['fps'], display.cpu_look(cpu('power'))['fps'])
        self.assertGreater(display.cpu_look(cpu('power'))['low'],
                           display.cpu_look(cpu('balanced'))['low'])
        self.assertEqual(display.cpu_look(cpu('overclock', warning='throttled')),
                         display.SKULL_LOOK['balanced'])


class Capture:
    def image(self, image, rotation=0):
        self.picture = image


def info(**kw):
    value = dict(server='ok', clock='12:34', battery=dict(percent=80, mv=4000, plugged=False,
                                                          charging=False))
    value.update(kw)
    return value


def rgb(image):
    data = image.convert('RGB').tobytes()
    return zip(data[0::3], data[1::3], data[2::3])


def warm_pixels(image):
    """Gold at any brightness (the rain fades), not green, red or grey."""
    return sum(1 for r, g, b in rgb(image) if r > 30 and 0.6 * r < g < r and b < 0.5 * g)


def gold_pixels(image, box=None):
    image = image.crop(box) if box else image
    return sum(1 for r, g, b in rgb(image) if r > 150 and 90 < g < r and b < 0.55 * g)


class SkullOverclockTests(unittest.TestCase):
    def setUp(self):
        self.skull = skull.Skull('/does/not/exist')

    def test_gold_eye_frames_are_made_once(self):
        gilded = self.skull.blessed()
        self.assertIs(self.skull.blessed(), gilded)
        box, frames = gilded
        self.assertEqual(len(frames), skull.STEPS)
        eye = self.skull.box                      # the gold box holds the red eye's
        self.assertTrue(box[0] <= eye[0] and box[1] <= eye[1]
                        and box[2] >= eye[2] and box[3] >= eye[3])
        red = self.skull.frame(0.8).crop(box)
        gold = self.skull.frame(0.8, gold=True).crop(box)
        self.assertEqual(gold_pixels(red), 0)
        self.assertGreater(gold_pixels(gold), 20)
        # Only the eye changes: everything outside its box stays.
        plain, golden = self.skull.frame(0.8), self.skull.frame(0.8, gold=True)
        golden.paste(plain.crop(box), box[:2])
        self.assertEqual(plain.tobytes(), golden.tobytes())

    def test_dimmed_variant_is_cached(self):
        dim = self.skull.frame(0.5, gain=0.7)
        normal = self.skull.frame(0.5)
        self.assertLess(sum(dim.convert('L').tobytes()), sum(normal.convert('L').tobytes()))
        self.assertIn(0.7, self.skull._variants)

    def test_repository_artwork_is_the_default_fallback(self):
        self.assertEqual(skull.REPO_SKULL, ROOT / 'assets' / 'display' / 'servo-skull.png')
        self.assertEqual(self.skull.source, 'drawn')    # explicit path: no fallback

    @unittest.skipUnless(skull.REPO_SKULL.exists(), 'skull artwork not in checkout')
    def test_real_artwork_gets_a_gold_eye_without_a_red_ring(self):
        real = skull.Skull(skull.REPO_SKULL)
        self.assertEqual(real.source, 'file')
        self.assertIsNotNone(real.box)              # the red eye is found
        box, _ = real.blessed()
        for level in (0.3, 1.0):
            red_eye = real.frame(level).crop(box)
            gold_eye = real.frame(level, gold=True).crop(box)
            def strong_red(image):
                return sum(1 for r, g, b in rgb(image) if r > 120 and r > 2.5 * max(g, b))
            # Red lights of the artwork beside the socket may stay; the eye
            # and its glowing rim (hundreds of pixels) must not.
            self.assertGreater(strong_red(red_eye), 200, level)
            self.assertLess(strong_red(gold_eye), 25, level)
            self.assertGreater(warm_pixels(gold_eye), warm_pixels(red_eye) + 100, level)

    def render(self, state='BEREIT', blessed=False, **kw):
        out = Capture()
        display.render_skull(out, self.skull, state, True, 0.6, info(**kw), 3, blessed=blessed)
        return out.picture

    def test_gold_only_for_a_blessed_skull(self):
        self.assertEqual(gold_pixels(self.render(cpu=cpu('power')), (0, 41, 240, 179)), 0)
        golden = self.render(cpu=cpu('overclock', mhz=1200), blessed=True)
        self.assertGreater(warm_pixels(golden.crop((0, 41, 50, 179))), 10)  # gold litanies
        # Listening keeps the status colour in the rain, the eye stays gold.
        listening = self.render('ZUHÖREN', cpu=cpu('overclock', mhz=1200), blessed=True)
        self.assertEqual(warm_pixels(listening.crop((0, 41, 50, 179))), 0)
        self.assertGreater(gold_pixels(listening.crop((52, 42, 188, 178))), 10)

    def test_balanced_is_the_previous_look(self):
        self.assertEqual(self.render(cpu=None).tobytes(),
                         self.render(cpu=cpu('balanced')).tobytes())

    def test_undervolt_stays_visible(self):
        picture = self.render(cpu=cpu('overclock', warning='undervolt'), throttled=0x1)
        line = picture.crop((0, 181, 240, 195))
        self.assertTrue(any(r > 200 and g < 100 for r, g, b in rgb(line)))


@unittest.skipUnless(SHEET.exists(), 'Doom face sheet not in checkout')
class BillyGodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.face = face.Face(SHEET)

    def test_god_mode_keys_keep_the_mimic(self):
        self.assertEqual(face.god_key((3, 'look')), 'god')
        self.assertEqual(face.god_key((0, 'blink')), 'god')
        self.assertEqual(face.god_key((2, 'talk_open')), ('god', 'talk_open'))
        for kept in ((0, 'ouch'), (1, 'grin'), (0, 'teeth'), (0, 'turn_a'), 'dead', 'god'):
            self.assertEqual(face.god_key(kept), kept)

    def test_god_face_talks(self):
        for mouth in face.TALK:
            talking = self.face.frame(('god', mouth))
            self.assertIsNot(talking, self.face.frame('god'), mouth)
            self.assertEqual(talking.size, self.face.size)
            # Upper face (the golden eyes) is the god face's own.
            upper = (0, 0, self.face.size[0], self.face.size[1] // 2)
            self.assertEqual(talking.crop(upper).tobytes(),
                             self.face.frame('god').crop(upper).tobytes(), mouth)
        self.assertIs(self.face.frame(('god', 'nothing')), self.face.frame('god'))

    def test_pulse_brightens_only_the_eyes(self):
        eyes = self.face._god_eyes()
        self.assertGreater(len(eyes), 20)
        self.assertIs(self.face.godlike((0, 'look'), 0), self.face.frame('god'))
        bright = self.face.godlike((0, 'look'), face.GOLD_STEPS - 1)
        self.assertIs(self.face.godlike((4, 'look'), face.GOLD_STEPS - 1), bright)
        plain = self.face.frame('god').load()
        changed = {(x, y) for y in range(bright.height) for x in range(bright.width)
                   if bright.getpixel((x, y)) != plain[x, y]}
        self.assertTrue(changed)
        self.assertLessEqual(changed, eyes)
        self.assertIs(self.face.godlike((0, 'ouch'), 2), self.face.frame((0, 'ouch')))
        self.assertEqual({face.gold_step(t / 10) for t in range(30)},
                         set(range(face.GOLD_STEPS)))

    def test_panel_turns_gold_with_glow(self):
        out = Capture()
        display.render_face(out, self.face.godlike((0, 'look'), 2), 'BEREIT', True,
                            info(cpu=cpu('overclock', mhz=1200)), glow=1.0)
        x0 = (display.WIDTH - self.face.size[0]) // 2 - 4
        self.assertEqual(out.picture.getpixel((x0 + 2, display.FACE_TOP)),
                         display.PANEL_GOLD[1][0])


class BillyChoiceTests(unittest.TestCase):
    def idle(self, profile):
        return {face.choose('BEREIT', t / 10, cpu=profile)[1] for t in range(600)}

    def test_eco_is_calm_power_is_alert(self):
        self.assertEqual(self.idle('eco'), {'look', 'blink'})
        self.assertIn('wide', self.idle('power'))
        self.assertEqual(self.idle('overclock'), {'look'})     # the god stare
        self.assertNotIn('wide', self.idle('balanced'))
        self.assertEqual(self.idle(None), self.idle('balanced'))

    def test_charger_god_flash_and_speech_still_win(self):
        self.assertEqual(face.choose('BEREIT', 10.5, plugged_at=10.0, cpu='eco'), 'god')
        self.assertEqual(face.choose('SPRECHEN', 10.0, viseme='A', cpu='eco'),
                         (0, 'talk_open'))


if __name__ == '__main__':
    unittest.main()
