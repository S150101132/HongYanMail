import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from email.message import EmailMessage
from PySide6.QtWidgets import QApplication, QTextBrowser
from coremail_desktop.mime.parser import parse_mime
from coremail_desktop.ui.main_window import MainWindow
from test_desktop_usability import Controller


def sample():
    message = EmailMessage()
    message['From'] = 'a@example.test'
    message['To'] = 'b@example.test'
    message['Subject'] = 'Synthetic attachment'
    message.set_content('body')
    message.add_attachment(b'\x00\x01\xffpayload', maintype='application', subtype='octet-stream', filename='../../测试.bin')
    return parse_mime(message.as_bytes(), 'mail')


class AttachmentSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_parsed_attachment_has_exact_owned_bytes_and_safe_name(self):
        document = sample()
        attachment = document.attachments[0]
        self.assertEqual(attachment.name, '测试.bin')
        self.assertEqual(getattr(document, 'attachment_payloads', {}).get(attachment.part_id), b'\x00\x01\xffpayload')
        with self.assertRaises(TypeError): document.attachment_payloads['other'] = b'bad'

    def test_save_uses_user_chosen_path_and_binary_payload(self):
        window = MainWindow(Controller(), reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window.reader.set_document = lambda document: None
        window._show_document(sample())
        with tempfile.TemporaryDirectory(prefix='attachment-save-') as directory:
            path = Path(directory) / 'chosen.bin'
            with patch('coremail_desktop.ui.main_window.QFileDialog.getSaveFileName', return_value=(str(path), '')):
                window.attachment_menu.actions()[0].trigger()
            self.assertTrue(path.exists())
            self.assertEqual(path.read_bytes(), b'\x00\x01\xffpayload')

    def test_switching_message_removes_attachment_actions(self):
        window = MainWindow(Controller(), reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window.reader.set_document = lambda document: None
        window._show_document(sample())
        self.assertTrue(window.attachment_button.isEnabled())
        window._clear_message()
        self.assertFalse(window.attachment_button.isEnabled())
        self.assertEqual(window.attachment_menu.actions(), [])

    def test_attached_eml_preserves_original_header_octets_and_newlines(self):
        original=b'From: a@example.test\nSubject: '+b'A'*100+b'\n\noriginal body\n'
        raw=b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nbody\r\n--b\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment; filename="mail.eml"\r\n\r\n'+original+b'\r\n--b--\r\n'
        document=parse_mime(raw,'x')
        attachment=document.attachments[0]
        self.assertEqual(document.attachment_payloads[attachment.part_id],original)

    def test_base64_attached_eml_is_decoded_before_saving(self):
        import base64
        original=b'From: a@example.test\r\nSubject: attached\r\n\r\nbody\r\n'
        raw=b'Content-Type: message/rfc822\r\nContent-Transfer-Encoding: base64\r\nContent-Disposition: attachment; filename="mail.eml"\r\n\r\n'+base64.encodebytes(original)
        document=parse_mime(raw,'x')
        self.assertEqual(document.attachment_payloads[document.attachments[0].part_id],original)

    def test_digest_attachment_with_empty_wrapper_keeps_message_headers(self):
        original=b'From: a@example.test\r\nSubject: example\r\n\r\nbody\r\n'
        raw=b'Content-Type: multipart/digest; boundary="b"\r\n\r\n--b\r\n\r\n'+original+b'\r\n--b--\r\n'
        document=parse_mime(raw,'x')
        self.assertEqual(document.attachment_payloads[document.attachments[0].part_id],original)

    def test_cancel_save_creates_no_file(self):
        window=MainWindow(Controller(),reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        document=sample(); window.document=document
        with tempfile.TemporaryDirectory(prefix='attachment-cancel-') as directory:
            with patch('coremail_desktop.ui.main_window.QFileDialog.getSaveFileName',return_value=('', '')):
                window.save_attachment(document,document.attachments[0])
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_failed_save_keeps_existing_data_and_reports_failure(self):
        window=MainWindow(Controller(),reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        document=sample(); window.document=document
        with tempfile.TemporaryDirectory(prefix='attachment-failure-') as directory:
            marker=Path(directory)/'keep.bin'; marker.write_bytes(b'existing data')
            # A directory cannot be replaced by an attachment file.
            with patch('coremail_desktop.ui.main_window.QFileDialog.getSaveFileName',return_value=(directory, '')):
                window.save_attachment(document,document.attachments[0])
            self.assertEqual(marker.read_bytes(),b'existing data')
            self.assertIn('失败',window.statusBar().currentMessage())


if __name__ == '__main__': unittest.main()
