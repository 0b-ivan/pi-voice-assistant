import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('journal_cursor', ROOT / 'deploy/journal-forward/seed-cursor.py')
cursor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cursor)


class JournalForwardTest(unittest.TestCase):
    def test_initial_upload_starts_with_recent_entries_and_keeps_resume_position(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            command = folder / 'journalctl'
            command.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"__CURSOR":"recent-start"}\' '
                               '\'{"__CURSOR":"recent-end"}\'\n')
            command.chmod(0o755)
            state = folder / 'state'
            with patch.dict(os.environ, PATH=str(folder) + os.pathsep + os.environ['PATH']):
                cursor.seed(state)
                self.assertEqual(state.read_text(), 'LAST_CURSOR=recent-start\n')
                state.write_text('LAST_CURSOR=uploaded-later\n')
                command.unlink()   # an existing cursor needs no new journal query
                cursor.seed(state)
                self.assertEqual(state.read_text(), 'LAST_CURSOR=uploaded-later\n')
