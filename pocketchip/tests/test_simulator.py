"""Simulator keyboard integration against actual Tk widgets."""
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest

from proximus_os.shell import Shell
from simulator.pocketchip import KEY_ROWS, inject_key


@unittest.skipUnless(os.environ.get("DISPLAY"), "Needs Xvfb/X11")
class SimulatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.shell = Shell(self.root, Path(self.temp.name) / "archive",
                           Path(self.temp.name) / "missing.json")
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.temp.cleanup()

    def test_keyboard_has_digits_and_letters(self):
        keys = "".join("".join(row) for row in KEY_ROWS)
        self.assertTrue(all(key in keys for key in "123456abcdefghijklmnopqrstuvwxyz"))

    def test_navigation_and_text_insertion(self):
        inject_key(self.shell, "1")
        self.assertEqual(self.shell.page, "Vox")
        entry = next(child for frame in self.shell.body.winfo_children()
                     for child in frame.winfo_children() if isinstance(child, tk.Entry))
        self.shell._sim_focus = entry
        inject_key(self.shell, "h")
        inject_key(self.shell, "i")
        inject_key(self.shell, "SPACE")
        inject_key(self.shell, "BACK")
        self.assertEqual(entry.get(), "hi")
        inject_key(self.shell, "HOME")
        self.assertEqual(self.shell.page, "Home")
        inject_key(self.shell, "3")
        self.assertEqual(self.shell.page, "Archive")


if __name__ == "__main__":
    unittest.main()
