"""Reply contracts measured against the deployment, exercised offline here."""
import unittest
from dataclasses import replace
from coremail_desktop.models import AccountConfig, DraftDocument, MessageDocument
from coremail_desktop.compose.service import build_reply
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession


class ReplySession:
    def __init__(self, origin): self.calls=[]
    def login(self,*args): pass
    def close(self): pass
    def call(self,op,params):
        self.calls.append((op,params))
        return {'code':'S_OK','var':[] if op=='mbox:getAllFolders' else {'id':'new-compose'}}
    def prepare_reply(self,source_id,reply_all=False):
        self.calls.append(('prepare_reply',{'source_id':source_id,'reply_all':reply_all}))
        return {'id':'reply-compose','account':'me@example.test'}
    def account_identities(self): return {'me@example.test','alias@example.test'}
    def get_message(self,mid): return b'From: sender@example.test\r\nSubject: Topic\r\nMessage-ID: <parent@example.test>\r\n\r\nbody'


class ReplyWorkflowTests(unittest.TestCase):
    def adapter(self):
        adapter=CoremailAdapter(ReplySession)
        adapter.login(AccountConfig('primary','https://example.test/coremail/','me@example.test'),'synthetic')
        return adapter

    def original(self, **headers):
        values={'from':('sender@example.test',),'to':('me@example.test',),'message-id':('<parent@example.test>',),'subject':('Topic',)}
        values.update(headers)
        return MessageDocument('remote-parent','digest','quoted body',None,(),values)

    def test_reply_draft_retains_remote_source_through_local_storage_encoding(self):
        from coremail_desktop.drafts.store import draft_dict,decode_draft
        draft=build_reply(self.original(),{'me@example.test'})
        self.assertEqual(getattr(draft,'reply_source_id',None),'remote-parent')
        self.assertEqual(decode_draft(draft_dict(draft)).reply_source_id,'remote-parent')

    def test_quote_includes_original_context_without_bcc(self):
        original=self.original(**{'date':('2026-10-16 10:00',),'bcc':('hidden@example.test',)})
        draft=build_reply(original,{'me@example.test'})
        self.assertIn('发件人：sender@example.test',draft.body)
        self.assertIn('主题：Topic',draft.body)
        self.assertIn('2026-10-16 10:00',draft.body)
        self.assertNotIn('hidden@example.test',draft.body)

    def test_reply_uses_server_preparation_before_existing_durable_delivery_route(self):
        adapter=self.adapter()
        for all_recipients in (False,True):
            draft=build_reply(self.original(),{'me@example.test'},all_recipients)
            cid=adapter.create_compose(draft)
            self.assertEqual(cid,'reply-compose')
            self.assertEqual(adapter._session.calls[-1],('prepare_reply',{'source_id':'remote-parent','reply_all':all_recipients}))
            adapter.deliver(cid,draft)
            op,params=adapter._session.calls[-1]
            self.assertEqual((op,params['action'],params['id']),('%mbox/compose','deliver','reply-compose'))
            self.assertNotIn('extraHeaders',params['attrs'])

    def test_reply_source_cannot_be_rebound_to_different_message(self):
        adapter=self.adapter()
        draft=build_reply(self.original(**{'message-id':('<wrong@example.test>',)}),{'me@example.test'})
        with self.assertRaises(Exception): adapter.create_compose(draft)
        self.assertFalse(any(op=='prepare_reply' for op,_ in adapter._session.calls))

    def test_real_identities_are_returned_for_alias_exclusion(self):
        self.assertEqual(self.adapter().get_account_identities(),{'me@example.test','alias@example.test'})

    def test_reply_to_own_sent_mail_uses_original_recipients(self):
        draft=build_reply(self.original(**{'from':('me@example.test',),'to':('other@example.test',)}),{'me@example.test'})
        self.assertEqual(draft.to,('other@example.test',))

    def test_reply_to_self_only_remains_sendable(self):
        draft=build_reply(self.original(**{'from':('me@example.test',)}),{'me@example.test'})
        self.assertEqual(draft.to,('me@example.test',))

    def test_explicit_reply_to_self_never_expands_ordinary_reply_to_other_recipients(self):
        original=self.original(**{'reply-to':('me@example.test',),'to':('me@example.test, colleague@example.test',)})
        draft=build_reply(original,{'me@example.test'})
        self.assertEqual(draft.to,('me@example.test',))
        self.assertEqual(draft.cc,())

    def test_source_without_message_id_cannot_pass_rebinding_check(self):
        adapter=self.adapter()
        adapter._session.get_message=lambda mid:b'From: sender@example.test\r\nSubject: no identifier\r\n\r\nbody'
        draft=build_reply(self.original(**{'message-id':()}),{'me@example.test'})
        with self.assertRaises(Exception): adapter.create_compose(draft)
        self.assertFalse(any(op=='prepare_reply' for op,_ in adapter._session.calls))

    def test_reply_initialization_uses_fixed_form_route(self):
        from urllib.parse import parse_qs,urlsplit
        session=DesktopSession('https://example.test/coremail/'); session.sid='synthetic'
        requests=[]
        def fetch(request):
            requests.append(request)
            return b'{"code":"S_OK","var":{"id":"prepared"}}',request.full_url
        session._fetch=fetch
        self.assertTrue(callable(getattr(session,'prepare_reply',None)))
        result=session.prepare_reply('source&mid',True)
        self.assertEqual(result['id'],'prepared')
        self.assertEqual(urlsplit(requests[0].full_url).path,'/coremail/XT/jsp/compose.jsp')
        self.assertEqual(parse_qs(requests[0].data.decode()),{'ctype':['replyall'],'mid':['source&mid'],'isHtml':['false']})


if __name__=='__main__': unittest.main()
