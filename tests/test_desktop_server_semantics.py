"""Pin the response semantics observed on the real deployment.

These values come from read-only inspection of the live server (records in
analysis/real-folder-shape.json, real-message-shape.json and
real-folder-parameters.json), so a future edit cannot silently reintroduce the
bugs they exposed:

  * mbox:getAllFolders exposes no role and no counts for any parameter variant, so
    roles must not be invented;
  * the folder total arrives on the mbox:listMessages envelope, not per folder;
  * message readiness is flags.read, so a missing read flag means unread.
"""
import unittest

from coremail_desktop.models import MessageSummary
from coremail_desktop.protocol.coremail import CoremailAdapter

FOLDERS = {'code': 'S_OK', 'var': [
    {'id': 1, 'name': '收件箱', 'flags': {'system': True}, 'keepPeriod': -1},
    {'id': 2, 'name': '已发送', 'flags': {'system': True}, 'keepPeriod': 14},
    {'id': 975276, 'name': '自定义', 'flags': {'system': True}, 'keepPeriod': 7},
]}
MESSAGES = {'code': 'S_OK', 'midoffset': 0, 'total': 321, 'var': [
    {'id': 'm1', 'subject': 's', 'from': 'f@example.test', 'fid': 1,
     'receivedDate': '2026-10-15 10:00:00',
     'flags': {'read': True, 'attached': True, 'replied': False}},
    {'id': 'm2', 'subject': 's', 'from': 'f@example.test', 'fid': 1,
     'receivedDate': '2026-10-15 11:00:00',
     'flags': {'read': False}},
]}


class FolderSchemaTests(unittest.TestCase):
    def adapter(self, folders=FOLDERS, messages=MESSAGES):
        adapter = CoremailAdapter()
        adapter._session = type('Session', (), {
            'call': staticmethod(lambda func, payload: folders if func == 'mbox:getAllFolders' else messages),
            'close': staticmethod(lambda: None),
        })()
        return adapter

    def test_only_the_inbox_is_classified_and_no_role_is_invented(self):
        folders = self.adapter().list_folders()
        self.assertEqual(folders[0].role, 'inbox')
        self.assertEqual([folder.role for folder in folders[1:]], [None, None],
                         'the server exposes no role, so none may be guessed')
        self.assertEqual([folder.remote_id for folder in folders], ['1', '2', '975276'])

    def test_folder_counts_are_not_invented(self):
        folders = self.adapter().list_folders()
        self.assertTrue(all(folder.counts == {} for folder in folders),
                        'the folder payload carries no counts, so none may be shown')

    def test_the_folder_total_is_taken_from_the_list_envelope(self):
        adapter = self.adapter()
        adapter.list_messages('1', 0, 50)
        self.assertEqual(adapter.last_folder_total, 321)
        self.assertEqual(adapter.last_folder_id, '1')

    def test_message_flags_are_passed_through_unchanged(self):
        summaries = self.adapter().list_messages('1', 0, 50)
        self.assertEqual(summaries[0].flags.get('read'), True)
        self.assertEqual(summaries[1].flags.get('read'), False)
        self.assertEqual(summaries[0].flags.get('attached'), True)


class ReadStateRenderingTests(unittest.TestCase):
    """flags.read must be rendered, and its absence means unread."""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def render(self, flags):
        from PySide6.QtCore import Qt
        from coremail_desktop.ui.mail_list import MailListPane
        pane = MailListPane()
        message = MessageSummary('1', '1', '主题', 'sender@example.test', '2026-10-15', flags)
        pane.model.replace([message])
        text = pane.model.data(pane.model.index(0), Qt.ItemDataRole.DisplayRole)
        pane.close()
        return text or ''

    def test_the_model_freezes_flags_so_a_plain_dict_check_would_be_wrong(self):
        message = MessageSummary('1', '1', 's', 'f', 'd', {'read': False})
        self.assertNotIsInstance(message.flags, dict,
                                 'flags are frozen mappings; code must not test isinstance(dict)')
        self.assertTrue(hasattr(message.flags, 'get'))
        self.assertIn('read', message.flags)

    def test_unread_message_is_labelled_unread(self):
        self.assertIn('未读', self.render({'read': False}))

    def test_read_message_is_not_labelled_unread(self):
        self.assertNotIn('未读', self.render({'read': True}))

    def test_missing_read_flag_counts_as_unread(self):
        self.assertIn('未读', self.render({}))

    def test_legacy_unread_key_still_honoured(self):
        self.assertIn('未读', self.render({'unread': True}))
        self.assertNotIn('未读', self.render({'unread': False}))

    def test_attachment_marker_uses_the_server_flag(self):
        self.assertIn('含附件', self.render({'read': True, 'attached': True}))
        self.assertNotIn('含附件', self.render({'read': True}))


if __name__ == '__main__':
    unittest.main()
