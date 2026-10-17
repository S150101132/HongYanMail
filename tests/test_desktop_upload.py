"""Binary attachment snapshots, protected persistence and verified upload binding."""
import os,tempfile,unittest,json
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
from pathlib import Path
from PySide6.QtWidgets import QApplication
from coremail_desktop import models
from coremail_desktop.compose.service import ComposeService
from coremail_desktop.drafts.store import DraftStore
from coremail_desktop.outbox.journal import SendJournal
from coremail_desktop.outbox.service import OutboxService
from coremail_desktop.protocol.coremail import CoremailAdapter,DesktopSession
from coremail_desktop.ui.composer import Composer


class UploadSession(DesktopSession):
    def __init__(self):
        super().__init__('https://example.test/coremail/'); self.sid='test'; self.requests=[]; self.calls=[]; self.upload_size=4
    def call(self,op,params):
        self.calls.append((op,params))
        if op=='upload:prepare': return {'code':'S_OK','var':{'attachmentId':7,'fileName':'文件.bin','size':4,'contentType':'application/octet-stream'}}
        return {'code':'S_OK','var':{'id':'compose'}}
    def _fetch(self,request):
        self.requests.append(request)
        return json.dumps({'code':'S_OK','var':{'attachmentId':7,'size':self.upload_size,'actualSize':self.upload_size}}).encode(),request.full_url


class UploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def attachment(self,payload=b'\x00\xff\r\n',name='文件.bin'):
        self.assertTrue(hasattr(models,'DraftAttachment'))
        return models.DraftAttachment(name,'application/octet-stream',payload)
    def draft(self):
        return replace(ComposeService.new_draft('me@example.test'),to=('me@example.test',),subject='test',attachments=(self.attachment(),))

    def test_binary_attachments_roundtrip_only_inside_protected_draft_and_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            draft=self.draft(); store=DraftStore(Path(tmp)/'drafts'); store.save(draft)
            self.assertEqual(store.load(draft.local_id),draft)
            from coremail_desktop.models import SendJob
            journal=SendJournal(Path(tmp)/'outbox'); journal.persist(SendJob('job',draft))
            self.assertEqual(journal.load('job').draft.attachments[0].payload,b'\x00\xff\r\n')
            for path in Path(tmp).rglob('*.bin'): self.assertNotIn('文件.bin'.encode(),path.read_bytes())

    def test_upload_keeps_exact_bytes_and_delivery_references_uploaded_id(self):
        a=CoremailAdapter(); a._session=UploadSession(); draft=self.draft()
        compose=a.create_compose(draft); a.deliver(compose,draft)
        self.assertEqual(len(a._session.requests),1)
        request=a._session.requests[0]
        from urllib.parse import urlsplit,parse_qs
        self.assertEqual(urlsplit(request.full_url).path,'/coremail/XT/jsp/upload.jsp')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),{'func':['directData'],'sid':['test'],'composeId':['compose'],'attachmentId':['7'],'offset':['0']})
        self.assertIn(b'\r\n\r\n\x00\xff\r\n\r\n--',request.data)
        self.assertEqual(a._session.calls[-1][1]['attrs']['attachments'],[{'id':7,'type':'upload','name':'文件.bin','size':4}])
        self.assertIn('upload:prepare',DesktopSession.ALLOWED)

    def test_partial_upload_fails_before_delivery_and_manual_retry_uses_new_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=CoremailAdapter(); a._session=UploadSession(); a._session.upload_size=3
            journal=SendJournal(tmp); outbox=OutboxService(a,journal); draft=self.draft()
            job=journal.load(outbox.request_send(draft)); self.assertEqual(job.state,'Failed')
            self.assertFalse(any(p.get('action')=='deliver' for op,p in a._session.calls))
            a._session.upload_size=4; job=journal.load(outbox.request_send(draft))
            self.assertEqual(job.state,'Accepted')
            self.assertEqual(sum(p.get('action')=='deliver' for op,p in a._session.calls),1)

    def test_large_upload_uses_sequential_two_mib_chunks_with_correct_offsets(self):
        from urllib.parse import parse_qs,urlsplit
        from email import policy
        from email.parser import BytesParser
        class ChunkSession(UploadSession):
            def _fetch(self,request):
                self.requests.append(request)
                message=BytesParser(policy=policy.default).parsebytes(('Content-Type: '+request.get_header('Content-type')+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+request.data)
                payload=next(message.iter_parts()).get_payload(decode=True)
                offset=int(parse_qs(urlsplit(request.full_url).query)['offset'][0])
                return json.dumps({'code':'S_OK','var':{'attachmentId':7,'size':3145728,'actualSize':offset+len(payload)}}).encode(),request.full_url
        a=CoremailAdapter(); a._session=ChunkSession()
        attachment=self.attachment(bytes(range(256))*12288)
        draft=replace(self.draft(),attachments=(attachment,))
        a.create_compose(draft)
        self.assertEqual([parse_qs(urlsplit(r.full_url).query)['offset'][0] for r in a._session.requests],['0','2097152'])

    def test_unsafe_names_and_oversize_are_rejected_before_creating_remote_compose(self):
        a=CoremailAdapter(); a._session=UploadSession(); base=self.draft()
        for attachment in (self.attachment(name='bad\r\nX: injected'),self.attachment(name='../file.bin'),self.attachment(b'x'*(10*1024*1024+1))):
            with self.assertRaises(Exception): a.create_compose(replace(base,attachments=(attachment,)))
        self.assertEqual(a._session.calls,[])

    def test_composer_add_remove_snapshots_bytes_and_prevents_changes_during_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sample.bin'; path.write_bytes(b'\x00\xff\r\n')
            draft=ComposeService.new_draft('me@example.test'); composer=Composer(draft)
            import shiboken6
            self.addCleanup(lambda:shiboken6.delete(composer))
            self.assertTrue(callable(getattr(composer,'add_attachments',None)))
            composer.add_attachments([str(path)]); path.write_bytes(b'changed')
            self.assertEqual(composer.snapshot().attachments[0].payload,b'\x00\xff\r\n')
            composer.set_send_state('Submitting'); composer.remove_attachment(0)
            self.assertEqual(len(composer.snapshot().attachments),1)
            composer.set_send_state('Failed'); composer.remove_attachment(0)
            self.assertEqual(composer.snapshot().attachments,())

    def test_allowed_twenty_mib_attachment_message_can_be_read_back(self):
        from email.message import EmailMessage
        from email import policy
        from coremail_desktop.mime.parser import parse_mime
        message=EmailMessage(); message['From']='me@example.test'; message['To']='me@example.test'; message['Subject']='large synthetic'
        message.set_content('large attachment roundtrip')
        for name in ('one.bin','two.bin'): message.add_attachment(b'x'*(10*1024*1024),maintype='application',subtype='octet-stream',filename=name)
        document=parse_mime(message.as_bytes(policy=policy.SMTP),'large')
        self.assertIn('large attachment roundtrip',document.text)
        self.assertEqual(len(document.attachments),2)
        self.assertEqual([len(document.attachment_payloads[a.part_id]) for a in document.attachments],[10485760,10485760])


if __name__=='__main__':unittest.main()
