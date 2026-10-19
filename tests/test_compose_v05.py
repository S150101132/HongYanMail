"""v0.5 compose and send: undo send, offline outbox, server drafts, body images, tables,
forward as attachment and opening .eml files. Demo adapter and fake sessions only."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

import base64
import shutil
import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, demo_certificate
from coremail_desktop.compose.inline import extract_inline, fill_inline
from coremail_desktop.compose.smime import inner_entity
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftAttachment, DraftDocument
from coremail_desktop.protocol.coremail import CoremailAdapter
from tests.test_app_backend import XorProtector, local_services_stub

BUILD = Path(__file__).resolve().parents[1] / 'build'
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
       b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa7\xc1\x9b\xb7\x00\x00\x00\x00IEND\xaeB`\x82')
IMG = '<img src="data:image/png;base64,' + base64.b64encode(PNG).decode('ascii') + '" style="max-width:100%">'
TABLE = ('<table style="border-collapse:collapse"><tbody><tr><td style="border:1px solid #98a2b3">项目</td>'
         '<td style="border:1px solid #98a2b3" colspan="2">金额</td></tr><tr><td>设备</td><td>12</td><td>万</td></tr></tbody></table>')


def scratch(test):
    BUILD.mkdir(exist_ok=True)
    directory = tempfile.mkdtemp(prefix='hongyan-test-', dir=str(BUILD))
    test.addCleanup(shutil.rmtree, directory, True)
    return directory


def parse(raw):
    return BytesParser(policy=policy.default).parsebytes(raw)


class Base(unittest.TestCase):
    crypto = None

    def setUp(self):
        self.directory = scratch(self)
        kwargs = {'crypto': self.crypto()} if self.crypto else {}
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub, **kwargs)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)
        self.adapter = self.backend.adapter

    def draft(self, **extra):
        value = self.backend.compose('new')
        value.update({'to': ['zhangxm@example.invalid'], 'subject': '周报', 'html': '<p>正文</p>', **extra})
        return value


class UndoSendTest(Base):
    def test_held_send_submits_nothing_until_released(self):
        res = self.backend.send(self.draft(receipt=True), hold=True)
        self.assertEqual((res['state'], res['delayMs']), ('Held', 10000))  # Default 10 s.
        self.assertEqual(self.adapter.sent, [])
        self.assertEqual(len(self.backend.list_drafts()), 1)  # Durable before the delay starts.
        done = self.backend.release_send(res['localId'], res['revision'])
        self.assertEqual(done['state'], 'Accepted')
        self.assertTrue(self.adapter.sent[0].request_receipt)  # Receipts survive the hold.
        self.assertEqual(self.backend.list_drafts(), [])
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Cancelled')  # Never twice.
        self.assertEqual(len(self.adapter.sent), 1)

    def test_undo_returns_the_draft_intact_and_the_release_does_nothing(self):
        value = self.draft(cc=['lihua@example.invalid'], bcc=['hr@example.invalid'], html='<p>表格</p>' + TABLE + IMG, receipt=True,
                           attachments=[{'name': 'a.txt', 'type': 'text/plain', 'size': 2, 'data': base64.b64encode(b'hi').decode()}])
        res = self.backend.send(value, hold=True)
        back = self.backend.cancel_send(res['localId'])
        for key in ('to', 'cc', 'bcc', 'subject', 'html', 'receipt', 'localId'):
            self.assertEqual(back[key], value[key], key)
        self.assertEqual(back['attachments'][0]['data'], value['attachments'][0]['data'])
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Cancelled')
        self.assertEqual(self.adapter.sent, [])
        with self.assertRaises(AppError) as raised:
            self.backend.cancel_send(res['localId'])
        self.assertEqual(raised.exception.code, 'SEND_NOT_HELD')
        # Sent again from the reopened composer: a new hold, released normally.
        again = self.backend.send(back, hold=True)
        self.assertEqual(self.backend.release_send(again['localId'], again['revision'])['state'], 'Accepted')

    def test_undo_after_release_is_refused(self):
        res = self.backend.send(self.draft(), hold=True)
        self.backend.release_send(res['localId'], res['revision'])
        with self.assertRaises(AppError):
            self.backend.cancel_send(res['localId'])
        self.assertEqual(len(self.adapter.sent), 1)

    def test_quitting_during_the_delay_never_sends_and_keeps_the_draft(self):
        res = self.backend.send(self.draft(subject='没发出的'), hold=True)
        self.assertEqual(self.backend.abandon_held(), 1)  # What the shell does when the app quits.
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Cancelled')
        self.assertEqual(self.adapter.sent, [])
        self.assertEqual([d['local_id'] for d in self.backend.list_drafts()], [res['localId']])
        box = self.backend.outbox()
        self.assertEqual([a['subject'] for a in box['abandoned']], ['没发出的'])
        self.assertEqual(self.backend.outbox()['abandoned'], [])  # Reported once.

    def test_logout_during_the_delay_also_abandons(self):
        res = self.backend.send(self.draft(), hold=True)
        self.backend.logout()
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Cancelled')
        self.assertEqual(len(self.backend.outbox()['abandoned']), 1)

    def test_scheduled_and_disabled_sends_are_not_held(self):
        import time
        at = int((time.time() + 3600) * 1000)
        self.assertEqual(self.backend.send(self.draft(scheduleAt=at), hold=True)['state'], 'Accepted')  # Cancellable in 定时发送.
        self.backend.set_prefs({'undoSeconds': 0})
        self.assertEqual(self.backend.send(self.draft(), hold=True)['state'], 'Accepted')
        self.assertEqual(self.backend.set_prefs({'undoSeconds': 7})['undoSeconds'], 10)  # Only the offered choices.
        self.assertEqual(self.backend.set_prefs({'undoSeconds': 30})['undoSeconds'], 30)

    def test_certificate_questions_come_before_the_hold(self):
        res = self.backend.send(self.draft(to=['partner@external.example'], encrypt=True), hold=True)
        self.assertEqual(res['state'], 'NeedsCertificates')
        self.assertEqual(self.backend.held_sends(), [])


class EncryptedUndoTest(Base):
    from coremail_desktop.crypto.service import CryptoService as crypto

    def test_encrypted_mail_is_built_when_released(self):
        res = self.backend.send(self.draft(encrypt=True, html='<p>密</p>' + IMG), hold=True)
        self.assertEqual(res['state'], 'Held')
        self.assertEqual(self.adapter.sent_payloads, [])
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Accepted')
        envelope = self.adapter.sent_payloads[0]
        self.assertEqual(parse(envelope).get_content_type(), 'application/pkcs7-mime')
        self.assertNotIn(base64.b64encode(PNG)[:40], envelope)  # The picture is inside the envelope only.
        # Read back with the sender's key: the picture is a related part of the body.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization import pkcs12
        _der, key, cert = demo_certificate('demo@example.invalid')
        pfx = Path(self.directory) / 'me.pfx'
        pfx.write_bytes(pkcs12.serialize_key_and_certificates(b'me', key, cert, None, serialization.BestAvailableEncryption(b'pw')))
        self.backend.unlock_certificate(str(pfx), 'pw')
        mid = self.backend.messages('1', 0, 1)['rows'][0]['id']
        self.backend.cache.put_body(mid, envelope)
        view = self.backend.open_message(mid, mark_read=False)
        self.assertEqual(view['encryption'], 'decrypted')
        self.assertIn('data:image/png;base64,', view['html'])
        self.assertEqual(view['attachments'], [])


class OfflineOutboxTest(Base):
    def test_network_failure_before_submission_queues_and_sends_when_back(self):
        self.adapter.offline = True
        res = self.backend.send(self.draft(subject='离线'))
        self.assertEqual(res['state'], 'Queued')
        box = self.backend.outbox()
        self.assertEqual([q['subject'] for q in box['queued']], ['离线'])
        self.assertEqual(box['review'], [])  # Nothing was submitted, so nothing to check.
        self.assertEqual(self.backend.flush_outbox()['results'][0]['state'], 'Queued')  # Still offline.
        self.adapter.offline = False
        result = self.backend.flush_outbox()
        self.assertEqual([r['state'] for r in result['results']], ['Accepted'])
        self.assertEqual((result['queued'], len(self.adapter.sent)), (0, 1))
        self.assertEqual(self.backend.list_drafts(), [])
        self.assertEqual(self.backend.flush_outbox()['results'], [])

    def test_held_release_while_offline_goes_to_the_outbox(self):
        res = self.backend.send(self.draft(), hold=True)
        self.adapter.offline = True
        self.assertEqual(self.backend.release_send(res['localId'], res['revision'])['state'], 'Queued')

    def test_sign_in_failing_offline_queues_too(self):
        self.backend._signed_in = False  # Next call signs in again first.
        login = self.adapter.login

        def offline(*a):
            raise AppError('NETWORK_ERROR')
        self.adapter.login = offline
        self.assertEqual(self.backend.send(self.draft())['state'], 'Queued')
        self.adapter.login = login
        self.assertEqual(self.backend.flush_outbox()['results'][0]['state'], 'Accepted')

    def test_unknown_outcome_is_listed_for_review_and_never_resent(self):
        def lost(compose_id, draft):
            raise AppError('NETWORK_ERROR')  # The submission itself may have reached the server.
        self.adapter.deliver = lost
        res = self.backend.send(self.draft(subject='不确定'))
        self.assertEqual(res['state'], 'Unknown')
        box = self.backend.outbox()
        self.assertEqual(box['queued'], [])
        self.assertEqual([r['subject'] for r in box['review']], ['不确定'])
        self.assertEqual(self.backend.flush_outbox()['results'], [])
        self.assertEqual(len(self.backend.list_drafts()), 1)  # Kept for the user to decide.
        self.backend.dismiss_review(box['review'][0]['id'])
        self.assertEqual(self.backend.outbox()['review'], [])

    def test_removing_from_the_queue_keeps_the_draft(self):
        self.adapter.offline = True
        res = self.backend.send(self.draft())
        self.assertEqual(self.backend.outbox_remove(res['localId'])['queued'], [])
        self.adapter.offline = False
        self.assertEqual(self.backend.flush_outbox()['results'], [])
        self.assertEqual(len(self.backend.list_drafts()), 1)


class EncryptedOfflineTest(Base):
    from coremail_desktop.crypto.service import CryptoService as crypto

    def test_offline_encrypted_mail_is_queued_and_sent_encrypted(self):
        self.adapter.offline = True
        res = self.backend.send(self.draft(encrypt=True))  # The certificate check cannot run offline.
        self.assertEqual(res['state'], 'Queued')
        self.adapter.offline = False
        self.assertEqual(self.backend.flush_outbox()['results'][0]['state'], 'Accepted')
        self.assertEqual(parse(self.adapter.sent_payloads[0]).get_content_type(), 'application/pkcs7-mime')

    def test_offline_encrypted_mail_to_someone_without_a_certificate_fails_not_plaintext(self):
        self.adapter.offline = True
        self.backend.send(self.draft(encrypt=True, to=['partner@external.example']))
        self.adapter.offline = False
        result = self.backend.flush_outbox()['results'][0]
        self.assertEqual((result['state'], result['failure']), ('Failed', 'RECIPIENT_CERT_MISSING'))
        self.assertEqual(self.adapter.sent, [])


class ServerDraftTest(Base):
    def setUp(self):
        super().setUp()
        self.backend.folders()
        self.backend.messages('2', 0, 50)

    def test_reader_marks_server_drafts_and_editing_loads_everything(self):
        self.assertTrue(self.backend.open_message('demo-draft0', mark_read=False)['serverDraft'])
        self.assertNotIn('serverDraft', self.backend.open_message('demo-sched', mark_read=False))
        value = self.backend.edit_server_draft('demo-draft0')
        self.assertEqual(value['serverDraftId'], 'demo-draft0')
        self.assertEqual(value['subject'], '季度预算草案（演示服务器草稿）')
        self.assertEqual(value['to'], ['"研发团队" <rd@example.invalid>'])
        self.assertIn('<table', value['html'])
        self.assertEqual([a['name'] for a in value['attachments']], ['预算明细.txt'])
        with self.assertRaises(AppError) as raised:
            self.backend.edit_server_draft('demo-sched')
        self.assertEqual(raised.exception.code, 'DRAFT_SCHEDULED')
        with self.assertRaises(AppError):
            self.backend.edit_server_draft('demo0')  # Not in the drafts folder.

    def test_saving_replaces_the_server_draft_and_drops_the_local_copy(self):
        value = self.backend.edit_server_draft('demo-draft0')
        value['subject'] = '季度预算草案 v2'
        res = self.backend.save_draft(value, server=True)
        self.assertTrue(res['serverDraftId'] and res['serverDraftId'] != 'demo-draft0')
        self.assertEqual(self.adapter.rows['demo-draft0']['fid'], '4')  # Moved to the trash, not deleted.
        saved = self.adapter.rows[res['serverDraftId']]
        self.assertEqual((saved['fid'], saved['subject'], len(saved['files'])), ('2', '季度预算草案 v2', 1))
        self.assertEqual(self.backend.list_drafts(), [])  # The server copy is the record now.
        # Autosaves in between stay local; only an explicit save writes to the server.
        value.update(serverDraftId=res['serverDraftId'], revision=res['revision'])
        self.assertNotIn('serverDraftId', self.backend.save_draft(value))
        self.assertEqual(len(self.adapter.saved_drafts), 1)

    def test_encrypted_drafts_never_reach_the_server(self):
        value = self.backend.edit_server_draft('demo-draft1')
        res = self.backend.save_draft({**value, 'encrypt': True}, server=True)
        self.assertEqual(res['serverErrorCode'], 'DRAFT_ENCRYPTED_LOCAL')
        self.assertEqual(self.adapter.saved_drafts, [])
        self.assertEqual(len(self.backend.list_drafts()), 1)  # Kept locally, encrypted at rest.

    def test_sending_consumes_the_server_draft(self):
        value = self.backend.edit_server_draft('demo-draft1')
        res = self.backend.send(value)
        self.assertEqual((res['state'], res['serverDraft']), ('Accepted', True))
        self.assertEqual(self.adapter.rows['demo-draft1']['fid'], '4')
        self.assertEqual(self.adapter.sent[0].server_draft_id, 'demo-draft1')

    def test_local_restore_keeps_the_link(self):
        value = self.backend.edit_server_draft('demo-draft1')
        self.backend.save_draft(value)
        self.assertEqual(self.backend.restore_draft(value['localId'])['serverDraftId'], 'demo-draft1')


class FakeSession:
    """Answers like the deployment did in analysis/compose-v05-shape-2026-10-18.json."""
    base_url, sid = 'https://mail.example.test/coremail/', 'SID'

    def __init__(self):
        self.calls, self.uploads = [], []

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': [{'id': 2, 'name': 'd'}, {'id': 4, 'name': 't', 'flags': {'system': True}}]}
        if op == 'mbox:updateMessageInfos':
            return {'code': 'S_OK', 'originalMsgInfos': [{'id': i} for i in params['ids']]}
        if params['action'] == 'continue':
            return {'code': 'S_OK', 'var': 'cnew'}
        if params['action'] == 'save':
            return {'code': 'S_OK', 'draftId': '2:saved', 'var': {}}
        return {'code': 'S_OK'}

    def open_draft(self, mid):
        self.calls.append(('open_draft', mid))
        return {'id': 'cdraft', 'subject': 's', 'content': 'c', 'isHtml': True}

    def upload_attachment(self, compose_id, attachment, inlined=False):
        self.uploads.append((compose_id, attachment.name, inlined))
        return {'id': len(self.uploads), 'type': 'upload', 'name': attachment.name, 'size': len(attachment.payload), **({'inlined': True} if inlined else {})}

    def compose_data_url(self, compose_id, attachment_id):
        from coremail_desktop.protocol.coremail import DesktopSession
        return DesktopSession.compose_data_url(self, compose_id, attachment_id)


def doc(**extra):
    base = dict(local_id='d1', revision=1, mode='new', from_identity='me@example.test', to=('you@example.test',), cc=(), bcc=(),
                subject='s', body='<p>看图</p>' + IMG, reference_headers={}, is_html=True)
    return DraftDocument(**{**base, **extra})


class ProtocolTest(unittest.TestCase):
    def adapter(self):
        a = CoremailAdapter()
        a._session = self.session = FakeSession()
        return a

    def test_body_images_upload_inlined_and_point_at_compose_data(self):
        a = self.adapter()
        draft = doc(attachments=(DraftAttachment('a.txt', 'text/plain', b'x'),))
        cid = a.create_compose(draft)
        self.assertEqual(self.session.uploads, [('cnew', 'a.txt', False), ('cnew', 'image1.png', True)])
        a.deliver(cid, draft)
        attrs = self.session.calls[-1][1]['attrs']
        self.assertIn('src="https://mail.example.test/coremail/s/json?sid=SID&func=mbox%3AgetComposeData&composeId=cnew&attachId=2"', attrs['content'])
        self.assertNotIn('data:', attrs['content'])
        self.assertEqual([x.get('inlined', False) for x in attrs['attachments']], [False, True])

    def test_server_draft_is_saved_fresh_and_the_old_one_moves_to_the_trash(self):
        a = self.adapter()
        self.assertEqual(a.save_server_draft(doc(server_draft_id='2:old')), '2:saved')
        ops = [(op, p.get('action')) for op, p in self.session.calls]
        self.assertNotIn(('open_draft', None), [(c[0], None) for c in self.session.calls])  # Reopening doubled attachments.
        self.assertEqual(ops[0], ('%mbox/compose', 'continue'))
        self.assertLess(ops.index(('%mbox/compose', 'save')), ops.index(('mbox:updateMessageInfos', None)))
        move = next(p for op, p in self.session.calls if op == 'mbox:updateMessageInfos')
        self.assertEqual((move['ids'], move['attrs']), (['2:old'], {'fid': 4}))
        self.session.calls.clear()
        a.deliver(a.create_compose(doc(server_draft_id='2:new')), doc(server_draft_id='2:new'))
        self.assertEqual(self.session.calls[-1][1]['ids'], ['2:new'])  # Consumed after the send.
        with self.assertRaises(AppError) as raised:
            a.save_server_draft(doc(server_draft_id='2:old', encryption_required=True))
        self.assertEqual(raised.exception.code, 'DRAFT_ENCRYPTED_LOCAL')

    def test_a_changed_draft_cannot_use_another_drafts_uploads(self):
        a = self.adapter()
        cid = a.create_compose(doc())
        with self.assertRaises(AppError):
            a.deliver(cid, doc(subject='changed'))


class InlineAndTableTest(Base):
    def test_extract_dedupes_and_validates(self):
        template, images = extract_inline('<p>' + IMG + IMG + '<img src="data:text/html;base64,PGI+">' + '</p>')
        self.assertEqual(len(images), 1)
        self.assertEqual(template.count('hy-inline:0'), 2)
        self.assertNotIn('text/html', template)
        self.assertEqual(fill_inline(template, ['cid:x']).count('src="cid:x"'), 2)
        with self.assertRaises(AppError):
            extract_inline('<img src="data:image/png;base64,@@@">')

    def test_body_images_count_toward_the_size_limit(self):
        big = '<img src="data:image/png;base64,' + base64.b64encode(b'\x89PNG' + b'0' * (11 * 1024 * 1024)).decode() + '">'
        with self.assertRaises(AppError) as raised:
            self.backend.send(self.draft(html=big))
        self.assertEqual(raised.exception.code, 'ATTACHMENT_TOO_LARGE')

    def test_smime_body_is_multipart_related_with_resolving_cids(self):
        entity = parse(b'MIME-Version: 1.0\r\n' + inner_entity(doc(body='<p>图</p>' + IMG + TABLE)))
        types = [p.get_content_type() for p in entity.walk()]
        self.assertIn('multipart/related', types)
        html = entity.get_body(preferencelist=('html',)).get_content()
        image = next(p for p in entity.walk() if p.get_content_type() == 'image/png')
        self.assertIn('cid:' + image['Content-ID'].strip('<>'), html)
        self.assertEqual(image.get_payload(decode=True), PNG)
        self.assertIn('colspan="2"', html)

    def test_sent_mail_shows_its_picture_and_table_and_quotes_them_in_a_reply(self):
        self.assertEqual(self.backend.send(self.draft(html='<p>见下表</p>' + TABLE + IMG))['state'], 'Accepted')
        sent = self.backend.messages('3', 0, 1)['rows'][0]
        view = self.backend.open_message(sent['id'], mark_read=False)
        self.assertIn('<table', view['html'])
        self.assertIn('colspan="2"', view['html'])
        self.assertIn('data:image/png;base64,', view['html'])
        self.assertEqual(view['attachments'], [])  # The picture is in the body, not an attachment.
        reply = self.backend.compose('reply', sent['id'])
        self.assertIn('<table', reply['html'])
        self.assertIn('data:image/png;base64,', reply['html'])  # Quoted pictures go out again inline.

    def test_tables_survive_local_drafts(self):
        value = self.draft(html=TABLE)
        self.backend.save_draft(value)
        self.assertEqual(self.backend.restore_draft(value['localId'])['html'], TABLE)


class ForwardAndImportTest(Base):
    def test_forward_as_attachment_carries_the_original_bytes(self):
        mid = self.backend.messages('1', 0, 1)['rows'][0]['id']
        value = self.backend.compose('forward_attach', mid)
        self.assertEqual((value['mode'], value['title'], value['encryptedOriginal']), ('forward', 'forward_attach', False))
        attachment = value['attachments'][0]
        self.assertEqual(attachment['type'], 'message/rfc822')
        self.assertTrue(attachment['name'].endswith('.eml'))
        self.assertEqual(base64.b64decode(attachment['data']), self.backend._raw(mid))
        value['to'] = ['lihua@example.invalid']
        self.assertEqual(self.backend.send(value)['state'], 'Accepted')
        self.assertEqual(self.adapter.sent[0].attachments[0].media_type, 'message/rfc822')

    def test_forward_as_attachment_of_encrypted_mail_keeps_it_encrypted(self):
        mid = self.backend.messages('1', 0, 1)['rows'][0]['id']
        envelope = (b'From: a@example.invalid\r\nTo: demo@example.invalid\r\nSubject: x\r\nMIME-Version: 1.0\r\n'
                    b'Content-Type: application/pkcs7-mime; smime-type=enveloped-data; name=smime.p7m\r\n'
                    b'Content-Transfer-Encoding: base64\r\n\r\nMIAGCSqGSIb3DQEHA6CAMIACAQA=\r\n')
        self.backend.cache.put_body(mid, envelope)
        self.backend._docs.clear()
        value = self.backend.compose('forward_attach', mid)
        self.assertTrue(value['encryptedOriginal'])
        self.assertEqual(base64.b64decode(value['attachments'][0]['data']), envelope)

    def test_smime_attaches_a_message_as_message_rfc822(self):
        inner = b'From: a@example.invalid\r\nSubject: inner\r\nMessage-ID: <x@example.invalid>\r\n\r\nhello\r\n'
        entity = parse(b'MIME-Version: 1.0\r\n' + inner_entity(doc(attachments=(DraftAttachment('o.eml', 'message/rfc822', inner),))))
        part = next(p for p in entity.walk() if p.get_content_type() == 'message/rfc822')
        self.assertNotEqual(str(part.get('Content-Transfer-Encoding', '')).lower(), 'base64')
        self.assertEqual(str(part.get_payload()[0]['Subject']), 'inner')

    def test_imported_eml_opens_read_only_and_can_be_answered(self):
        raw = self.backend._raw(self.backend.messages('1', 0, 1)['rows'][0]['id'])
        mid = self.backend.import_eml(raw)
        self.assertTrue(mid.startswith('eml:'))
        view = self.backend.open_message(mid)
        self.assertTrue(view['imported'])
        self.assertNotIn('markedRead', view)
        reply = self.backend.compose('reply', mid)
        self.assertEqual((reply['mode'], reply['replySourceId'], reply['referenceHeaders'], reply['title']), ('new', None, {}, 'reply'))
        self.assertIn('原始邮件', reply['html'])
        self.assertEqual(self.backend.send({**reply, 'to': ['lihua@example.invalid']})['state'], 'Accepted')
        self.assertEqual(self.backend.compose('forward', mid)['mode'], 'forward')
        self.assertEqual(self.backend.export_eml(mid)[1], raw)

    def test_bad_eml_files_are_refused(self):
        for data, code in ((b'', 'EML_INVALID'), (b'\x00\x01 not mail', 'EML_INVALID'), (b'X' * (40 * 1024 * 1024 + 1), 'EML_TOO_LARGE')):
            with self.assertRaises(AppError) as raised:
                self.backend.import_eml(data)
            self.assertEqual(raised.exception.code, code)


class ShellTest(unittest.TestCase):
    """The undo timer lives in the main window: hiding it to the tray still sends; quitting does not."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        try:
            import qt_test_support  # noqa: F401
        except ImportError:
            from tests import qt_test_support  # noqa: F401
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, ms=5000):
        from PySide6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        timer.start(30)
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        timer.stop()
        return predicate()

    def setUp(self):
        from coremail_desktop.app import shell
        self.shell = shell
        self.backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw')
        self.window = shell.AppShell(self.backend, demo=True)
        self.events = []
        self.window.bridge.event.connect(lambda name, value: self.events.append(name))
        self.addCleanup(lambda: (self.window.bridge.shutdown(), self.window.deleteLater()))

    def held(self):
        value = self.backend.compose('new')
        value.update(to=['zhangxm@example.invalid'], subject='s', html='<p>b</p>')
        return self.backend.send(value, hold=True)

    def test_page_cannot_release_and_undo_does_not_wait_behind_mail_work(self):
        self.assertNotIn('releaseSend', self.shell.BACKEND_METHODS)
        self.assertIn('cancelSend', self.shell.FAST_METHODS)

    def test_hidden_window_still_sends_when_the_delay_ends(self):
        res = self.held()
        self.window.hide()  # Closed to the tray.
        self.window.hold_send(res['localId'], res['revision'], 100)
        self.assertTrue(self.wait(lambda: 'sendResult' in self.events))
        self.assertEqual(len(self.backend.adapter.sent), 1)

    def test_quit_during_the_delay_keeps_the_draft_unsent(self):
        res = self.held()
        self.window.hold_send(res['localId'], res['revision'], 300)

        class NoQuit:
            @staticmethod
            def instance():
                return type('A', (), {'quit': staticmethod(lambda: None)})()
        real, self.shell.QApplication = self.shell.QApplication, NoQuit
        try:
            self.window.quit_app()
        finally:
            self.shell.QApplication = real
        self.wait(lambda: False, 800)
        self.assertEqual(self.backend.adapter.sent, [])
        self.assertEqual(len(self.backend.list_drafts()), 1)

    def test_a_good_sync_flushes_the_outbox(self):
        self.backend.adapter.offline = True
        value = self.backend.compose('new')
        value.update(to=['zhangxm@example.invalid'], subject='s', html='<p>b</p>')
        self.assertEqual(self.backend.send(value)['state'], 'Queued')
        self.backend.adapter.offline = False
        self.window.poll_now()
        self.assertTrue(self.wait(lambda: 'outboxFlushed' in self.events))
        self.assertEqual(len(self.backend.adapter.sent), 1)


if __name__ == '__main__':
    unittest.main()
