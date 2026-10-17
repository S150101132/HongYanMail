import unittest
from coremail_desktop.models import AccountConfig, DraftDocument
from coremail_desktop.protocol.coremail import CoremailAdapter

class FakeSession:
    def __init__(self, origin): self.calls = []; self.closed = False
    def login(self, uid, password): pass
    def call(self, operation, params):
        self.calls.append((operation, params))
        if operation == 'mbox:getAllFolders': return {'code':'S_OK','var':[{'id':0,'name':'Inbox','stats':{'messageCount':5}}]}
        if operation == 'mbox:listMessages': return {'code':'S_OK','var':[{'id':'m','subject':'subject','from':'sender','date':'today','flags':{'read':False}}]}
        if operation == '%mbox/compose': return {'code':'S_OK','var':{'id':'compose'}}
        return {'code':'S_OK','var':{}}
    def close(self): self.closed = True
    def get_message(self, mid): return b'From: sender@example.test\r\nSubject: hello\r\n\r\nbody'

class ProtocolTests(unittest.TestCase):
    def test_production_transport_distinguishes_tls_network_and_expired_session(self):
        import io
        import ssl
        from urllib.error import URLError
        from urllib.request import Request
        from coremail_desktop.protocol.coremail import DesktopSession
        from coremail_desktop.errors import AppError
        session = DesktopSession('https://example.test/coremail/')
        session.sid = 'private'
        for reason,expected in ((ssl.SSLCertVerificationError('private'), 'TLS_ERROR'),(ConnectionError('private'),'NETWORK_ERROR')):
            def fail(*args,**kwargs): raise URLError(reason)
            session._opener.open = fail
            with self.assertRaises(AppError) as raised: session._fetch(Request('https://example.test/coremail/'))
            self.assertEqual(raised.exception.code,expected)
        class Response(io.BytesIO):
            def geturl(self): return 'https://example.test/coremail/s/json'
        session._opener.open = lambda *args,**kwargs:Response(b'{"code":"FA_UNAUTHORIZED"}')
        with self.assertRaises(AppError) as raised: session.call('mbox:getAllFolders',{})
        self.assertEqual(raised.exception.code,'SESSION_EXPIRED')
    def test_read_retries_but_deliver_never_retries(self):
        from probe.coremail_transport import CoremailError
        class Flaky(FakeSession):
            fail = False
            attempts = 0
            def call(self,operation,params):
                if self.fail:
                    self.attempts += 1
                    raise CoremailError('TRANSPORT_ERROR')
                return super().call(operation,params)
        adapter = CoremailAdapter(Flaky)
        adapter.login(AccountConfig('a','https://example.test/coremail/','uid'),'pw')
        adapter._session.fail = True
        with self.assertRaises(Exception): adapter.list_folders()
        self.assertEqual(adapter._session.attempts,3)
        adapter._session.attempts = 0
        draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
        with self.assertRaises(Exception): adapter.deliver('cid',draft)
        self.assertEqual(adapter._session.attempts,1)
    def test_login_requires_authenticated_api(self):
        adapter = CoremailAdapter(FakeSession)
        adapter.login(AccountConfig('a','https://example.test/coremail/','uid'), 'password')
        self.assertEqual(adapter.list_folders()[0].remote_id, '0')
        self.assertEqual(adapter.list_messages('0',0)[0].flags['read'], False)
        self.assertEqual(adapter._session.calls[0][0], 'mbox:getAllFolders')
        self.assertEqual(adapter.get_account_identities(), set())
    def test_compose_continue_before_deliver_and_reply_blocked(self):
        adapter = CoremailAdapter(FakeSession)
        adapter.login(AccountConfig('a','https://example.test/coremail/','uid'), 'password')
        draft = DraftDocument('d',1,'new','me@example.test',('to@example.test',),(),(),'s','body',{})
        cid = adapter.create_compose(draft)
        adapter.deliver(cid,draft)
        calls = [params for op,params in adapter._session.calls if op == '%mbox/compose']
        self.assertEqual([p['action'] for p in calls], ['continue','deliver'])
        self.assertTrue(adapter.supports_reply_headers)
        from dataclasses import replace
        with self.assertRaises(Exception): adapter.create_compose(replace(draft, reference_headers={'In-Reply-To':'<a>'}))
