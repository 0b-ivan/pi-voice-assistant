"""Who gets B and E in VoiceController.tick() while several things are open.

The order is enrollment (B only), then - with the menu closed - Bluetooth
picker, people browser, pending device question, maintenance mode; then the
menu (B goes up a level, E confirms); otherwise B cancels and E speaks the
status. Each layer consumes B/E so the next one never sees the same press.
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from controller_fixture import Presser, make_controller  # noqa: E402

HANDLERS = ('_picker_buttons', '_people_buttons', '_maintenance_buttons', '_menu_confirm',
            '_speak_status', 'cancel', '_device_confirmed', '_say')


class TickDispatchTests(unittest.TestCase):
    def setUp(self):
        self.c = make_controller(self)
        for name in HANDLERS:
            setattr(self.c, name, Mock(name=name))
        self.p = Presser(self.c)

    def called(self):
        return {name for name in HANDLERS if getattr(self.c, name).called}

    def open_picker(self):
        self.c.picker = dict(title='KOPPELN', items=[('00:11', 'Box')], index=0, action='pair')

    def ask_device(self, op='reboot'):
        self.c.device_pending = op
        self.c.device_pending_until = time.monotonic() + 60

    def test_idle_e_speaks_status_and_b_cancels(self):
        self.p.press('E')
        self.assertEqual(self.called(), {'_speak_status'})
        self.p.press('B')
        self.assertEqual(self.called(), {'_speak_status', 'cancel'})

    def test_b_and_e_together_only_cancel(self):
        self.p.press('BE')
        self.assertEqual(self.called(), {'cancel'})

    def test_running_enrollment_takes_b_and_ignores_e(self):
        self.c.enroll = Mock(running=True)
        self.p.press('E')
        self.p.press('B')
        self.c.enroll.cancel.assert_called_once()
        self.assertEqual(self.called(), set())

    def test_picker_comes_before_people_device_and_maintenance(self):
        self.open_picker()
        self.c.people.active = True
        self.c.maint.active = True
        self.ask_device()
        self.p.press('E')
        self.c._picker_buttons.assert_called_once_with(True)
        self.assertEqual(self.called(), {'_picker_buttons'})
        self.assertEqual(self.c.device_pending, 'reboot')

    def test_people_comes_before_device_and_maintenance(self):
        self.c.people.active = True
        self.c.maint.active = True
        self.ask_device()
        self.p.press('B')
        self.c._people_buttons.assert_called_once()
        self.assertEqual(self.c._people_buttons.call_args.args[:2], (False, True))
        self.assertEqual(self.called(), {'_people_buttons'})
        self.assertEqual(self.c.device_pending, 'reboot')

    def test_pending_device_question_comes_before_maintenance(self):
        self.c.maint.active = True
        self.ask_device('shutdown')
        self.p.press('E')
        self.c._device_confirmed.assert_called_once_with('shutdown', speak=True)
        self.assertEqual(self.called(), {'_device_confirmed'})
        self.assertIsNone(self.c.device_pending)

    def test_b_on_pending_device_question_cancels_only_the_question(self):
        self.ask_device()
        self.p.press('B')
        self.assertEqual(self.called(), {'_say'})
        self.assertIsNone(self.c.device_pending)

    def test_b_and_e_on_pending_device_question_cancel_it(self):
        self.ask_device()
        self.p.press('BE')
        self.assertEqual(self.called(), {'_say'})

    def test_maintenance_gets_b_and_e_when_nothing_else_is_open(self):
        self.c.maint.active = True
        self.p.press('E')
        self.c._maintenance_buttons.assert_called_once_with(True, False)
        self.assertEqual(self.called(), {'_maintenance_buttons'})

    def test_open_menu_comes_before_picker_people_device_and_maintenance(self):
        self.open_picker()
        self.c.people.active = True
        self.c.maint.active = True
        self.ask_device()
        self.c.menu.show(self.p.now)
        self.p.press('E')
        self.assertEqual(self.called(), {'_menu_confirm'})
        self.assertEqual(self.c.device_pending, 'reboot')

    def test_b_in_open_menu_goes_back_instead_of_cancelling(self):
        self.c.menu.show(self.p.now)
        self.p.press('B')
        self.assertFalse(self.c.menu.open)
        self.assertEqual(self.called(), set())
        self.p.press('B')
        self.assertEqual(self.called(), {'cancel'})


if __name__ == '__main__':
    unittest.main()
