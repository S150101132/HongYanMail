import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QTextBrowser
from coremail_desktop.models import DraftDocument
from coremail_desktop.drafts.store import DraftStore
from coremail_desktop.ui.main_window import MainWindow
from test_desktop_usability import Controller


def draft(key='draft'):
    return DraftDocument(key, 0, 'new', 'a@example.test', ('b@example.test',), (), (), 'Meeting notes', 'body', {})


class DraftPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_summary_is_derived_from_encrypted_record_without_plaintext_index(self):
        with tempfile.TemporaryDirectory(prefix='draft-picker-') as directory:
            store = DraftStore(directory)
            store.save(draft())
            self.assertTrue(callable(getattr(store, 'list_summaries', None)))
            rows = store.list_summaries()
            self.assertEqual(rows[0]['local_id'], 'draft')
            self.assertEqual(rows[0]['subject'], 'Meeting notes')
            from pathlib import Path
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertNotIn(b'Meeting notes', next(Path(directory).iterdir()).read_bytes())

    def test_picker_displays_subject_and_restores_underlying_id(self):
        controller = Controller()
        window = MainWindow(controller, reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window.pending['drafts'] = ('list_drafts', {})
        rows = [{'local_id': 'opaque-id', 'subject': 'Meeting notes', 'modified': '2026-10-16 10:20'}]
        labels = []
        def pick(parent, title, label, items, *args):
            labels.extend(items)
            return items[0], True
        with patch('coremail_desktop.ui.main_window.QInputDialog.getItem', side_effect=pick):
            window._completed('drafts', 0, rows)
        self.assertIn('Meeting notes', labels[0])
        self.assertEqual(controller.calls[-1][1], {'local_id': 'opaque-id'})

    def test_restore_never_replaces_an_open_editor(self):
        window = MainWindow(Controller(), reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window.open_composer(draft('old'))
        original = window.composer
        window.open_composer(draft('new'))
        self.assertIs(window.composer, original)


if __name__ == '__main__': unittest.main()
