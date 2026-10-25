"""Real widget tests under Xvfb, with HTTP text service and no audio stack."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest


@unittest.skipUnless(os.environ.get('DISPLAY'), 'Requires X server (use xvfb-run)')
class GuiTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from proximus_os.shell import Shell
        from proximus_os.text_server import Server
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.server = Server(('127.0.0.1', 0), 't' * 64, lambda text: 'Textantwort: ' + text)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        config = self.directory / 'client.json'
        config.write_text(json.dumps({'url': f'http://127.0.0.1:{self.server.server_port}', 'token': 't' * 64}))
        config.chmod(0o600)
        self.root = tk.Tk()
        self.shell = Shell(self.root, self.directory / 'data', config)
        self.root.update()

    def tearDown(self):
        self.root.destroy()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def wait_job(self):
        deadline = time.monotonic() + 4
        while self.shell.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.02)
        self.assertFalse(self.shell.busy)

    def entry(self):
        import tkinter as tk
        for frame in self.shell.body.winfo_children():
            for child in frame.winfo_children():
                if isinstance(child, tk.Entry):
                    return child
        self.fail('Missing input widget')

    def test_home_geometry_keyboard_touch_and_all_modules(self):
        from proximus_os.shell import MODULES
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (480, 272))
        for button in self.shell.body.winfo_children():
            self.assertGreaterEqual(button.winfo_height(), 40)
            self.assertGreaterEqual(button.winfo_width(), 120)
        for name, _ in MODULES:
            self.shell.show_home()
            self.root.update()
            # Invoke the actual touch/keyboard button handler.
            button = next(b for b in self.shell.body.winfo_children() if f'  {name}\n' in b['text'])
            button.invoke()
            self.root.update()
            self.assertEqual(self.shell.page, name)
            for child in self.shell.body.winfo_children():
                self.assertLessEqual(child.winfo_width(), 464)
            self.wait_job()
        self.shell.show_home()
        self.root.update()
        self.root.event_generate('<KeyPress-1>')
        self.root.update()
        self.assertEqual(self.shell.page, 'Vox')
        self.root.event_generate('<Escape>')
        self.root.update()
        self.assertEqual(self.shell.page, 'Home')

    def test_text_chat_local_note_and_navigation_during_request(self):
        self.shell.open('Vox')
        entry = self.entry()
        entry.insert(0, 'Hallo CT107')
        self.shell.send(entry)
        self.shell.show_home()  # completion must not access destroyed widgets
        self.wait_job()
        self.shell.open('Vox')
        self.root.update()
        self.assertIn('Textantwort: Hallo CT107', self.shell.chat_area.get('1.0', 'end'))
        self.shell.open('Archive')
        entry = self.entry()
        entry.insert(0, 'Offline gespeichert')
        self.shell.save_note(entry)
        self.assertEqual(self.shell.archive.notes()[0]['text'], 'Offline gespeichert')
        self.shell.open('Befehle')
        entry = self.entry()
        entry.insert(0, 'rechne 8 / 2')
        self.shell.execute(entry)
        self.assertEqual(self.shell.command_area.get('1.0', 'end').strip(), '4')

    def test_missing_config_keeps_ui_and_offline_tools_usable(self):
        self.shell.config_path = self.directory / 'missing.json'
        self.shell.open('Vox')
        entry = self.entry()
        entry.insert(0, 'Hallo')
        self.shell.send(entry)
        self.wait_job()
        self.assertIn('Zugang fehlt', self.shell.conversation)
        self.shell.open('Archive')
        self.assertEqual(self.shell.page, 'Archive')


if __name__ == '__main__':
    unittest.main()
