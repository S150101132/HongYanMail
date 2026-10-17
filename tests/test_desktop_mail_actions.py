import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtWidgets import QApplication,QTextBrowser
from coremail_desktop.protocol.coremail import CoremailAdapter,DesktopSession
from coremail_desktop.models import MessageDocument
from coremail_desktop.ui.main_window import MainWindow
from test_desktop_usability import Controller


class Session:
    def __init__(self): self.calls=[]; self.affected=True
    def call(self,op,params):
        self.calls.append((op,params))
        return {'code':'S_OK','originalMsgInfos':[{'id':'message'}] if self.affected else []}


class MailActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_read_and_star_flags_use_single_verified_write(self):
        adapter=CoremailAdapter(); adapter._session=Session()
        self.assertTrue(callable(getattr(adapter,'set_message_flags',None)))
        for flags in ({'read':False},{'read':True},{'flagged':True},{'flagged':False}):
            adapter.set_message_flags('message',flags)
            self.assertEqual(adapter._session.calls[-1],('mbox:updateMessageInfos',{'ids':['message'],'attrs':{'flags':flags},'returnOriginalMsgInfos':True}))
        self.assertIn('mbox:updateMessageInfos',DesktopSession.ALLOWED)
        self.assertEqual(len(adapter._session.calls),4)

    def test_unsupported_flags_and_partial_success_are_not_reported_as_success(self):
        adapter=CoremailAdapter(); adapter._session=Session()
        self.assertTrue(callable(getattr(adapter,'set_message_flags',None)))
        for flags in ({'read':1},{'deleted':True},{}, {'read':'false'}):
            with self.assertRaises(Exception): adapter.set_message_flags('message',flags)
        self.assertEqual(adapter._session.calls,[])
        adapter._session.affected=False
        with self.assertRaises(Exception): adapter.set_message_flags('message',{'read':True})

    def test_ui_operation_is_bound_to_message_and_deduplicated(self):
        controller=Controller(); window=MainWindow(controller,reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window._set_authenticated(True); window.folder_id='1'
        window.document=MessageDocument('message','digest','body',None,(),{})
        self.assertTrue(callable(getattr(window,'set_message_flags',None)))
        window.set_message_flags({'read':False}); window.set_message_flags({'read':False})
        self.assertEqual(len(controller.calls),1)
        op,params,generation=controller.calls[0]
        self.assertEqual((op,params),('set_message_flags',{'message_id':'message','flags':{'read':False}}))
        window._completed('1',generation,{'message_id':'message','flags':{'read':False}})
        self.assertEqual(len(controller.calls),1)

    def test_star_is_visible_in_list_accessibility_text(self):
        from coremail_desktop.ui.mail_list import SummaryModel
        from coremail_desktop.models import MessageSummary
        model=SummaryModel(); model.replace([MessageSummary('m','1','Topic','sender','date',{'read':True,'flagged':True})])
        self.assertIn('星标',model.index(0).data())

    def test_flag_update_keeps_later_loaded_pages_and_selection(self):
        from coremail_desktop.models import MessageSummary
        controller=Controller(); window=MainWindow(controller,reader_factory=QTextBrowser)
        self.addCleanup(window.close)
        window._set_authenticated(True); window.folder_id='1'
        rows=[MessageSummary(str(i),'1',str(i),'sender','date',{'read':True}) for i in range(150)]
        window.mail_list.set_messages(rows)
        window.mail_list.view.setCurrentIndex(window.mail_list.model.index(110))
        window.document=MessageDocument('110','digest','body',None,(),{})
        window.set_message_flags({'flagged':True})
        generation=controller.calls[0][2]
        window._completed('1',generation,{'message_id':'110','flags':{'flagged':True}})
        self.assertEqual(len(controller.calls),1,'flag update must not collapse the list to page one')
        self.assertEqual(len(window.mail_list.loaded),150)
        self.assertEqual(window.mail_list.view.currentIndex().data(256).remote_id,'110')
        self.assertTrue(window.mail_list.loaded[110].flags.get('flagged'))


if __name__=='__main__':unittest.main()
