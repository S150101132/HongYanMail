import unittest
from email.message import EmailMessage
from unittest.mock import patch

from coremail_desktop.mime.parser import parse_mime


class MimeTests(unittest.TestCase):
    def test_chinese_encodings_and_duplicate_headers(self):
        raw = b'Message-ID: <one>\r\nMessage-ID: <two>\r\nContent-Type: text/plain; charset=gbk\r\n\r\n' + '中文正文'.encode('gbk')
        doc = parse_mime(raw, 'remote')
        self.assertEqual(doc.text, '中文正文')
        self.assertEqual(doc.remote_id, 'remote')
        self.assertEqual(doc.headers['message-id'], ('<one>', '<two>'))

    def test_alternative_and_nested_attachment_are_not_body(self):
        msg = EmailMessage()
        msg.set_content('真正正文')
        msg.add_alternative('<p>真正正文</p>', subtype='html')
        attached = EmailMessage()
        attached.set_content('附件秘密不能当正文')
        msg.add_attachment(attached, filename='../secret.eml')
        doc = parse_mime(msg.as_bytes(), 'remote')
        self.assertIn('真正正文', doc.text)
        self.assertNotIn('附件秘密', doc.text)
        self.assertIn('真正正文', doc.html)
        self.assertEqual(len(doc.attachments), 1)
        self.assertEqual(doc.attachments[0].name, 'secret.eml')

    def test_html_only_has_plain_text_fallback_and_no_message_id_required(self):
        doc = parse_mime(b'Content-Type: text/html; charset=utf-8\r\n\r\n<p>Hello<br>world</p>', 'x')
        self.assertIn('Hello', doc.text)
        self.assertIn('world', doc.text)

    def test_cid_images_belong_to_document_and_svg_is_not_loaded(self):
        msg = EmailMessage()
        msg.set_content('<img src="cid:picture"><img src="cid:vector">', subtype='html')
        msg.add_related(b'\x89PNG\r\n\x1a\nabc', maintype='image', subtype='png', cid='<picture>')
        msg.add_related(b'<svg/>', maintype='image', subtype='svg+xml', cid='<vector>')
        doc = parse_mime(msg.as_bytes(), 'x')
        self.assertIn('picture', doc.cid_resources)
        self.assertNotIn('vector', doc.cid_resources)
        self.assertNotIn('cid:vector', doc.html)

    def test_inline_image_filename_keeps_owned_cid(self):
        msg = EmailMessage()
        msg.set_content('<img src="cid:picture">', subtype='html')
        msg.add_related(b'\x89PNG\r\n\x1a\nabc', maintype='image', subtype='png', cid='<picture>', filename='logo.png', disposition='inline')
        doc = parse_mime(msg.as_bytes(), 'x')
        self.assertIn('picture', doc.cid_resources)
        self.assertIn('cid:picture', doc.html)

    def test_signature_images_shown_inline_are_not_listed_as_attachments(self):
        png = b'\x89PNG\r\n\x1a\n' + b'0' * 32
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = 'a@example.test', 'b@example.test', 's'
        msg.set_content('text')
        msg.add_alternative('<p>hi</p><img src="cid:image001.png@01DC">', subtype='html')
        html = msg.get_payload()[1]
        html.add_related(png, maintype='image', subtype='png', cid='<image001.png@01DC>', filename='image001.png', disposition='inline')
        html.add_related(png, maintype='image', subtype='png', cid='<image002.png@01DC>', filename='image002.png', disposition='inline')
        msg.add_attachment(png, maintype='image', subtype='png', filename='photo.png')
        doc = parse_mime(bytes(msg), 'remote', rich=True)
        names = [a.name for a in doc.attachments]
        self.assertNotIn('image001.png', names)        # Shown in the body via cid:.
        self.assertIn('image002.png', names)           # Inline but not shown: still saveable.
        self.assertIn('photo.png', names)              # A real attachment.
        self.assertIn('image001.png@01DC', doc.cid_resources)

    def test_generic_attachment_names_get_the_extension_their_content_shows(self):
        msg = EmailMessage()
        msg.set_content('正文')
        msg.add_attachment(b'\x89PNG\r\n\x1a\nabc', maintype='application', subtype='octet-stream', filename='reply_1_95_att4.blob')
        msg.add_attachment(b'\xff\xd8\xffabc', maintype='application', subtype='octet-stream', filename='att5')
        msg.add_attachment(b'%PDF-1.4 x', maintype='application', subtype='octet-stream', filename='data.BIN')
        msg.add_attachment(b'plain bytes', maintype='application', subtype='octet-stream', filename='keep.blob')
        msg.add_attachment(b'\x89PNG\r\n\x1a\nabc', maintype='text', subtype='plain', filename='notes.txt')
        found = [(a.name, a.media_type) for a in parse_mime(msg.as_bytes(), 'x').attachments]
        self.assertEqual(found, [('reply_1_95_att4.png', 'image/png'), ('att5.jpg', 'image/jpeg'), ('data.pdf', 'application/pdf'),
                                 ('keep.blob', 'application/octet-stream'), ('notes.txt', 'text/plain')])

    def test_limits_return_visible_reason_and_never_partial_body(self):
        with patch('coremail_desktop.mime.parser.MAX_RAW_BYTES', 8):
            doc = parse_mime(b'Subject: Summary\r\n\r\nSecret body', 'x')
        self.assertIn('超出', doc.text)
        self.assertNotIn('Secret body', doc.text)
        with patch('coremail_desktop.mime.parser.MAX_DECODED_BYTES', 3):
            self.assertIn('超出', parse_mime(b'Content-Type: text/plain\r\n\r\n1234', 'x').text)

    def test_depth_limit_and_encrypted_payload_are_visible(self):
        msg = EmailMessage()
        msg.set_content('hidden')
        for _ in range(32):
            outer = EmailMessage()
            outer.make_mixed()
            outer.attach(msg)
            msg = outer
        self.assertIn('超出', parse_mime(msg.as_bytes(), 'x').text)
        doc = parse_mime(b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data\r\n\r\nsecret', 'x')
        self.assertIn('加密', doc.text)
        self.assertNotIn('secret', doc.text)

