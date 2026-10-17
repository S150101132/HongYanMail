"""Search routing, reversible moves and asynchronous view regressions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from urllib.parse import parse_qs,urlsplit
from PySide6.QtWidgets import QApplication,QTextBrowser
from coremail_desktop.protocol.coremail import CoremailAdapter,DesktopSession
from coremail_desktop.models import MessageSummary,MessageDocument
from coremail_desktop.ui.main_window import MainWindow
from test_desktop_usability import Controller


class SearchSession(DesktopSession):
    def __init__(self): super().__init__('https://example.test/coremail/'); self.sid='test'; self.requests=[]
    def _fetch(self,request):
        self.requests.append(request)
        return b'{"code":"S_OK","total":2,"var":[{"id":"a","fid":4,"subject":"match","from":["a@example.test"],"flags":{}}]}',request.full_url


class MoveSession:
    def __init__(self): self.calls=[]; self.confirm=True
    def call(self,op,params):
        self.calls.append((op,params))
        if op=='mbox:getAllFolders': return {'code':'S_OK','var':[{'id':1,'name':'Inbox'},{'id':4,'name':'Trash','flags':{'system':True}}]}
        return {'code':'S_OK','originalMsgInfos':[{'id':'a','fid':1}] if self.confirm else []}


class SearchMoveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def window(self):
        c=Controller(); w=MainWindow(c,reader_factory=QTextBrowser); w._set_authenticated(True); w.folder_id='1'; self.addCleanup(w.close); return w,c

    def test_search_uses_verified_form_route_and_preserves_result_folder(self):
        a=CoremailAdapter(); a._session=SearchSession()
        self.assertTrue(callable(getattr(a,'search_messages',None)))
        page=a.search_messages('季度 & 项目',0)
        self.assertEqual(page['total'],2); self.assertEqual(page['messages'][0].folder_id,'4')
        request=a._session.requests[0]
        self.assertEqual(urlsplit(request.full_url).path,'/coremail/XT/jsp/mail.jsp')
        self.assertEqual(parse_qs(urlsplit(request.full_url).query),{'func':['searchMessages'],'sid':['test']})
        self.assertEqual(parse_qs(request.data.decode()),{'pattern':['季度 & 项目'],'start':['0'],'limit':['50'],'summaryWindowSize':['0']})
        for query,offset in [('',0),('x',-1)]:
            with self.assertRaises(Exception): a.search_messages(query,offset)
        self.assertEqual(len(a._session.requests),1)

    def test_move_validates_destination_and_requires_affected_message(self):
        a=CoremailAdapter(); a._session=MoveSession()
        self.assertTrue(callable(getattr(a,'move_message',None)))
        result=a.move_message('a','4'); self.assertEqual(result,{'message_id':'a','folder_id':'4'})
        self.assertEqual(a._session.calls[-1],('mbox:updateMessageInfos',{'ids':['a'],'attrs':{'fid':4},'returnOriginalMsgInfos':True}))
        with self.assertRaises(Exception): a.move_message('a','999')
        a._session.confirm=False
        with self.assertRaises(Exception): a.move_message('a','1')
        self.assertEqual(sum(op=='mbox:updateMessageInfos' for op,p in a._session.calls),2)
        self.assertEqual(a.list_folders()[1].role,'trash')

    def test_new_search_discards_old_results_but_selection_keeps_search_page(self):
        w,c=self.window(); self.assertTrue(hasattr(w.mail_list,'search'))
        w.mail_list.search.setText('first'); w.search_mail(); first=c.calls[-1][2]
        w.mail_list.search.setText('second'); w.search_mail(); second=c.calls[-1][2]
        row=MessageSummary('a','4','match','sender','date')
        w._completed('1',first,{'messages':[row],'total':1}); self.assertEqual(w.mail_list.loaded,[])
        w._select_message(row)
        w._completed('2',second,{'messages':[row],'total':1})
        self.assertEqual(w.mail_list.loaded,[row]); self.assertFalse(w.has_more)
        self.assertEqual(c.calls[1][0],'search_messages')

    def test_move_keeps_other_pages_and_stale_list_cannot_reinsert_message(self):
        w,c=self.window(); self.assertTrue(callable(getattr(w,'move_message',None)))
        rows=[MessageSummary(str(i),'1',str(i),'sender','date') for i in range(150)]
        w.mail_list.set_messages(rows); w.document=MessageDocument('110','digest','body',None,(),{})
        w.refresh(); list_generation=c.calls[0][2]
        w.move_message('4'); w.move_message('4'); self.assertEqual(len(c.calls),2)
        w._completed('2',c.calls[1][2],{'message_id':'110','folder_id':'4'})
        self.assertEqual(len(w.mail_list.loaded),149); self.assertIsNone(w.document)
        w._completed('1',list_generation,rows)
        self.assertNotIn('110',[m.remote_id for m in w.mail_list.loaded])
        w.load_more(); self.assertEqual(c.calls[-1][1]['offset'],149,'moving an earlier row shifts the server offset')

    def test_demo_search_and_move_fail_safely_instead_of_emitting_wrong_shape(self):
        from coremail_desktop.ui.demo import DemoController
        from PySide6.QtCore import QEventLoop,QTimer
        c=DemoController(); errors=[]; results=[]; loop=QEventLoop()
        c.failed.connect(lambda task,g,error: errors.append(error.code))
        c.completed.connect(lambda *args: results.append(args))
        c.submit('search_messages',{'pattern':'x','offset':0},0)
        c.submit('move_message',{'message_id':'a','folder_id':'4'},0)
        QTimer.singleShot(60,loop.quit); loop.exec()
        self.assertEqual(len(errors),2); self.assertEqual(results,[])

    def test_move_in_search_updates_source_folder_without_removing_match(self):
        w,c=self.window(); self.assertTrue(hasattr(w.mail_list,'search'))
        w.mail_list.search.setText('match'); w.search_mail()
        row=MessageSummary('a','1','match','sender','date')
        w._completed('1',c.calls[0][2],{'messages':[row],'total':1})
        w.document=MessageDocument('a','digest','body',None,(),{})
        w.move_message('5'); w._completed('2',c.calls[-1][2],{'message_id':'a','folder_id':'5'})
        self.assertEqual(len(w.mail_list.loaded),1); self.assertEqual(w.mail_list.loaded[0].folder_id,'5')

    def test_trashing_search_match_removes_it_from_server_default_search_scope(self):
        from coremail_desktop.models import Folder
        w,c=self.window(); w.folder_rows=[Folder('4','Trash','trash',{})]
        w.mail_list.search.setText('match'); w.search_mail()
        row=MessageSummary('a','1','match','sender','date')
        w._completed('1',c.calls[0][2],{'messages':[row],'total':1})
        w.document=MessageDocument('a','digest','body',None,(),{})
        w.trash_message(); w._completed('2',c.calls[-1][2],{'message_id':'a','folder_id':'4'})
        self.assertEqual(w.mail_list.loaded,[]); self.assertIsNone(w.document)

    def test_explicit_refresh_accepts_external_changes_after_local_write(self):
        w,c=self.window(); row=MessageSummary('a','1','match','sender','date',{'read':False})
        w.mail_list.set_messages([row]); w.document=MessageDocument('a','digest','body',None,(),{})
        w.set_message_flags({'read':True}); w._completed('1',c.calls[0][2],{'flags':{'read':True}})
        w.refresh(); w._completed('2',c.calls[1][2],[row])
        self.assertFalse(w.mail_list.loaded[0].flags['read'],'fresh server state must replace the completed local overlay')

    def test_move_in_flight_pauses_page_requests_until_server_offset_changes(self):
        w,c=self.window(); w.next_offset=50; w.has_more=True
        rows=[MessageSummary(str(i),'1',str(i),'sender','date') for i in range(50)]
        w.mail_list.set_messages(rows); w.document=MessageDocument('0','digest','body',None,(),{})
        w.move_message('4'); w.load_more(); w.refresh()
        self.assertEqual(len(c.calls),1,'page queued after move would use the old server offset')
        self.assertFalse(w.mail_list.more.isEnabled())
        w._completed('1',c.calls[0][2],{'message_id':'0','folder_id':'4'})
        w.load_more(); self.assertEqual(c.calls[-1][1]['offset'],49)

    def test_application_shows_hongyan_name_in_window_and_login(self):
        from PySide6.QtWidgets import QLabel
        w,c=self.window()
        self.assertIn('鸿雁',w.windowTitle())
        self.assertTrue(any('鸿雁' in label.text() for label in w.login.findChildren(QLabel)))


if __name__=='__main__': unittest.main()
