"""v0.7 compose: signature pictures (B5), recipient groups (F1), @mentions (F2).
Demo adapter only; nothing reaches a server."""
import base64
import re
import unittest
from pathlib import Path

from coremail_desktop.app.demo import demo_certificate
from coremail_desktop.errors import AppError
from tests.test_compose_v05 import IMG, PNG, Base, parse

LOGO = '<img src="data:image/png;base64,' + base64.b64encode(PNG).decode('ascii') + '" width="240" style="width: 240px; height: auto;">'
WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


def signature_with(pictures):
    return '<b>演示用户</b><br>' + ''.join(pictures)


class SignatureTest(Base):
    def test_signature_with_a_logo_is_stored_and_copied_into_new_mail(self):
        prefs = self.backend.set_prefs({'signature': signature_with([LOGO])})
        self.assertIn('data:image/png;base64,', prefs['signature'])
        for value in (self.backend.compose('new'), self.backend.compose('reply', 'demo1'), self.backend.compose('forward', 'demo1')):
            self.assertIn('width="240"', value['html'])
            self.assertIn('class="hy-signature"', value['html'])

    def test_limits_are_a_fixed_code_and_keep_the_old_signature(self):
        self.backend.set_prefs({'signature': '<p>旧签名</p>'})
        big = '<img src="data:image/png;base64,' + base64.b64encode(b'\x89PNG' + b'0' * (1024 * 1024)).decode() + '">'
        many = ['<img src="data:image/png;base64,' + base64.b64encode(PNG + bytes([i])).decode() + '">' for i in range(6)]
        for signature in (signature_with([big]), signature_with(many), 'x' * 20001):
            with self.assertRaises(AppError) as raised:
                self.backend.set_prefs({'signature': signature})
            self.assertEqual(raised.exception.code, 'SIGNATURE_TOO_LARGE')
        with self.assertRaises(AppError) as raised:
            self.backend.set_prefs({'signature': '<img src="data:image/png;base64,@@@">'})
        self.assertEqual(raised.exception.code, 'INVALID_SIGNATURE')
        self.assertEqual(self.backend.prefs()['signature'], '<p>旧签名</p>')
        # The text limit does not count the pictures: a logo plus 19 000 characters is fine.
        self.backend.set_prefs({'signature': signature_with([LOGO]) + 'x' * 19000})

    def test_codes_have_messages(self):
        from coremail_desktop.ui.messages import MESSAGES
        for code in ('SIGNATURE_TOO_LARGE', 'INVALID_SIGNATURE', 'INVALID_GROUP', 'GROUP_LIMIT', 'GROUP_EXISTS', 'GROUP_NOT_FOUND'):
            self.assertIn(code, MESSAGES)

    def test_plain_mail_carries_the_logo_as_an_inline_part(self):
        self.backend.set_prefs({'signature': signature_with([LOGO])})
        value = self.backend.compose('new')
        value.update(to=['partner@external.example'], subject='带签名')
        self.assertEqual(self.backend.send(value)['state'], 'Accepted')
        sent = self.backend.messages('3', 0, 1)['rows'][0]
        view = self.backend.open_message(sent['id'], mark_read=False)
        self.assertIn('data:image/png;base64,', view['html'])  # Resolved from cid:, no network picture.
        self.assertEqual(view['attachments'], [])


class SignedSignatureTest(Base):
    from coremail_desktop.crypto.service import CryptoService as crypto

    def unlock(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        _der, key, cert = demo_certificate('demo@example.invalid')
        pfx = Path(self.directory) / 'me.pfx'
        pfx.write_bytes(pkcs12.serialize_key_and_certificates(b'me', key, cert, None, serialization.BestAvailableEncryption(b'pw')))
        self.backend.unlock_certificate(str(pfx), 'pw')

    def sent_inner(self, **extra):
        self.backend.set_prefs({'signature': signature_with([LOGO])})
        value = self.backend.compose('new')
        value.update(to=['zhangxm@example.invalid'], subject='签名图片', **extra)
        self.assertEqual(self.backend.send(value)['state'], 'Accepted')
        return self.adapter.sent_payloads[-1]

    def related_logo(self, entity):
        html = entity.get_body(preferencelist=('html',)).get_content()
        image = next(p for p in entity.walk() if p.get_content_type() == 'image/png')
        self.assertIn('cid:' + image['Content-ID'].strip('<>'), html)
        self.assertNotIn('data:image', html)
        self.assertEqual(image.get_payload(decode=True), PNG)

    def test_encrypted_mail_carries_the_logo_inside_the_envelope(self):
        envelope = self.sent_inner(encrypt=True, sign=False)
        self.assertEqual(parse(envelope).get_content_type(), 'application/pkcs7-mime')
        self.assertNotIn(base64.b64encode(PNG)[:40], envelope)
        self.unlock()
        inner = self.backend.crypto.decrypt(envelope, 'demo@example.invalid').mime_bytes
        self.related_logo(parse(b'MIME-Version: 1.0\r\n' + inner if not inner.lower().startswith(b'mime') else inner))

    def test_signed_mail_carries_the_logo_as_a_related_part(self):
        self.unlock()
        signed = parse(self.sent_inner(encrypt=False, sign=True))
        self.assertEqual(signed.get_content_type(), 'multipart/signed')
        self.related_logo(signed.get_payload()[0])


class RecipientGroupTest(Base):
    def group(self, name='项目组', members=None):
        return self.backend.save_recipient_group({'name': name, 'members': members or [
            {'name': '张晓明', 'email': 'zhangxm@example.invalid', 'field': 'to'},
            {'name': '', 'email': 'lihua@example.invalid', 'field': 'cc'}]})

    def test_create_edit_delete(self):
        g = self.group()
        self.assertEqual([m['field'] for m in g['members']], ['to', 'cc'])
        self.assertEqual(self.backend.recipient_groups(), [g])
        edited = self.backend.save_recipient_group({'id': g['id'], 'name': '项目组（新）', 'members': [
            {'email': '"王芳" <wangfang@example.invalid>', 'field': 'cc'}]})
        self.assertEqual((edited['id'], edited['members']), (g['id'], [{'name': '王芳', 'email': 'wangfang@example.invalid', 'field': 'cc'}]))
        self.assertEqual(len(self.backend.recipient_groups()), 1)
        self.assertEqual(self.backend.delete_recipient_group(g['id']), [])

    def test_stored_in_the_encrypted_cache_only(self):
        self.group()
        self.assertEqual(self.backend.cache.get('recipient_groups')[0]['name'], '项目组')
        self.assertFalse(any('group' in str(call).lower() for call in getattr(self.adapter, 'calls', [])))
        for path in Path(self.directory).rglob('*'):
            if path.is_file():
                self.assertNotIn('zhangxm@example.invalid'.encode(), path.read_bytes())

    def test_members_are_checked_and_deduplicated_to_winning(self):
        g = self.group(members=[{'email': 'a@example.invalid', 'field': 'cc'}, {'email': 'A@example.invalid', 'field': 'to', 'name': '甲'},
                                {'email': 'b@example.invalid', 'field': 'cc'}])
        self.assertEqual(g['members'], [{'name': '甲', 'email': 'a@example.invalid', 'field': 'to'},
                                        {'name': '', 'email': 'b@example.invalid', 'field': 'cc'}])
        for bad in ([], [{'email': 'not-an-address', 'field': 'to'}], [{'email': 'a@example.invalid', 'field': 'bcc'}],
                    [{'email': f'p{i}@example.invalid'} for i in range(201)]):
            with self.assertRaises(AppError) as raised:
                self.backend.save_recipient_group({'name': '坏', 'members': bad})
            self.assertEqual(raised.exception.code, 'INVALID_GROUP')
        for name in ('', ' ', 'x' * 41):
            with self.assertRaises(AppError):
                self.group(name)

    def test_names_are_unique_and_the_count_is_limited(self):
        self.group('周会')
        with self.assertRaises(AppError) as raised:
            self.group('周会')
        self.assertEqual(raised.exception.code, 'GROUP_EXISTS')
        with self.assertRaises(AppError) as raised:
            self.backend.save_recipient_group({'id': 'gone', 'name': '新', 'members': [{'email': 'a@example.invalid'}]})
        self.assertEqual(raised.exception.code, 'GROUP_NOT_FOUND')
        self.backend.cache.set('recipient_groups', [{'id': str(i), 'name': f'g{i}', 'members': []} for i in range(100)])
        with self.assertRaises(AppError) as raised:
            self.group('第 101 个')
        self.assertEqual(raised.exception.code, 'GROUP_LIMIT')

    def test_applying_appends_by_field_and_never_repeats_an_address(self):
        one = self.group('一', [{'name': '张晓明', 'email': 'zhangxm@example.invalid', 'field': 'to'},
                               {'name': '李华', 'email': 'lihua@example.invalid', 'field': 'cc'},
                               {'email': 'hr@example.invalid', 'field': 'cc'}])
        two = self.group('二', [{'email': 'ZhangXM@example.invalid', 'field': 'cc'}, {'email': 'wangfang@example.invalid', 'field': 'to'},
                               {'email': 'secret@example.invalid', 'field': 'to'}])
        first = self.backend.apply_recipient_groups([one['id']], ['"已有" <hr@example.invalid>'], [], [])
        self.assertEqual(first, {'to': ['"已有" <hr@example.invalid>', '"张晓明" <zhangxm@example.invalid>'],
                                 'cc': ['"李华" <lihua@example.invalid>'], 'added': 2})
        second = self.backend.apply_recipient_groups([two['id']], first['to'], first['cc'], ['secret@example.invalid'])
        # 张晓明 is already in 主送 (not added again to 抄送); a 密送 address is left where it is.
        self.assertEqual(second['to'], first['to'] + ['wangfang@example.invalid'])
        self.assertEqual(second['cc'], first['cc'])
        self.assertEqual(second['added'], 1)
        both = self.backend.apply_recipient_groups([one['id'], two['id']])
        self.assertEqual(len(both['to']) + len(both['cc']), both['added'])
        self.assertEqual(len({re.sub(r'.*<|>', '', a).lower() for a in both['to'] + both['cc']}), both['added'])
        with self.assertRaises(AppError) as raised:
            self.backend.apply_recipient_groups(['nope'])
        self.assertEqual(raised.exception.code, 'GROUP_NOT_FOUND')

    def test_applying_sends_nothing_and_encryption_follows_the_recipients(self):
        g = self.group('外部', [{'email': 'partner@external.example', 'field': 'to'}])
        before = len(self.adapter.sent_payloads)
        res = self.backend.apply_recipient_groups([g['id']], ['zhangxm@example.invalid'])
        self.assertEqual(len(self.adapter.sent_payloads), before)
        self.assertEqual(self.backend.list_drafts(), [])
        # The page encrypts by default only when every recipient is in internalDomains.
        domains = self.backend.internal_domains()
        self.assertFalse(all(a.rpartition('@')[2].casefold() in domains for a in res['to'] + res['cc']))
        self.assertIn('autoEncrypt()', (WEB / 'app.js').read_text(encoding='utf-8'))

    def test_a_message_recipients_become_a_group(self):
        self.assertEqual(self.backend.send(self.draft(to=['"张晓明" <zhangxm@example.invalid>', 'demo@example.invalid'],
                                                      cc=['"李华" <lihua@example.invalid>', 'zhangxm@example.invalid']))['state'], 'Accepted')
        sent = self.backend.messages('3', 0, 1)['rows'][0]
        members = self.backend.message_recipients(sent['id'])
        self.assertEqual(members, [{'name': '张晓明', 'email': 'zhangxm@example.invalid', 'field': 'to'},
                                   {'name': '李华', 'email': 'lihua@example.invalid', 'field': 'cc'}])  # Not the user, once each.
        saved = self.backend.save_recipient_group({'name': '来自邮件', 'members': members})
        self.assertEqual(len(saved['members']), 2)

    def test_bridge_exposes_the_group_calls(self):
        from coremail_desktop.app.shell import BACKEND_METHODS
        for name in ('recipientGroups', 'saveRecipientGroup', 'deleteRecipientGroup', 'applyRecipientGroups', 'messageRecipients'):
            self.assertTrue(hasattr(self.backend, BACKEND_METHODS[name]))


class MentionTest(Base):
    MENTION = ('<p>请 <span class="hy-mention" style="color:#1d4ed8;background:#e8effd;border-radius:3px;padding:0 2px;'
               'font-weight:600">@李华</span>&nbsp;确认</p>')

    def test_a_mention_goes_out_as_plain_highlighted_text(self):
        self.assertEqual(self.backend.send(self.draft(to=['lihua@example.invalid'], html=self.MENTION))['state'], 'Accepted')
        sent = self.backend.messages('3', 0, 1)['rows'][0]
        view = self.backend.open_message(sent['id'], mark_read=False)
        self.assertIn('@李华', view['html'])
        self.assertIn('color:#1d4ed8', view['html'].replace(' ', ''))

    def test_page_strips_editing_attributes_before_sending(self):
        source = (WEB / 'mention.js').read_text(encoding='utf-8')
        self.assertIn('contenteditable="false"', source)
        self.assertIn("outgoingHtml(html)", (WEB / 'app.js').read_text(encoding='utf-8'))
        self.assertIn('collectDraft(true)', (WEB / 'app.js').read_text(encoding='utf-8'))
        for name in ('signature.js', 'groups.js', 'mention.js'):
            self.assertIn(f'<script src="{name}"></script>', (WEB / 'index.html').read_text(encoding='utf-8'))


class SpellingTest(unittest.TestCase):
    def test_composer_is_spellchecked_with_the_bundled_dictionary(self):
        import os
        from coremail_desktop.app import shell
        folder = Path(os.environ['QTWEBENGINE_DICTIONARIES_PATH'])
        self.assertEqual(folder, WEB.parent / 'dictionaries')
        self.assertEqual((folder / 'en-US.bdic').read_bytes()[:4], b'BDic')
        self.assertTrue(hasattr(shell, '_spelling_menu'))
        page = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('id="cmp-editor" class="cmp-editor" contenteditable="true" spellcheck="true"', page)
        self.assertIn("api('replaceMisspelled'", (WEB / 'compose.js').read_text(encoding='utf-8'))
        self.assertIn("'coremail_desktop/app/dictionaries'", (WEB.parents[2] / 'packaging' / 'desktop.spec').read_text(encoding='utf-8'))

    def test_pictures_in_mail_bodies_can_be_copied(self):
        # Right-click a picture (reader, its window, attached mail) → 复制图片; a selected picture
        # copies as itself, not as the whole mail.
        from coremail_desktop.app import shell
        self.assertTrue(hasattr(shell.ReaderWindow, 'native_copyImage') and hasattr(shell.AppShell, 'native_copyImage'))
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn("on('imageMenu'", app)
        self.assertIn("api('copyImage')", app)
        self.assertIn('sel && !sel.isCollapsed', app)


if __name__ == '__main__':
    unittest.main()
