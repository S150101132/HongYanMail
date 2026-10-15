"""Synthetic data available only through the explicit --demo switch."""
from PySide6.QtCore import QObject,Signal,QTimer
from ..models import Folder,MessageSummary,MessageDocument

class DemoController(QObject):
    completed=Signal(str,int,object); failed=Signal(str,int,object); auth_required=Signal()
    supports_reply_headers=False
    def __init__(self): super().__init__(); self.counter=0
    def submit(self,operation,params,view_generation=0):
        self.counter+=1; task=str(self.counter)
        def finish():
            result=[]
            if operation=='list_folders': result=[Folder('inbox','收件箱','inbox',{'total':1000}),Folder('sent','已发送','sent',{}),Folder('archive','归档 · 合成示例',None,{})]
            elif operation=='get_account_identities': result={'demo@example.invalid'}
            elif operation=='list_messages':
                titles=['项目进展与本周计划','关于设计评审的会议纪要','欢迎使用桌面预览版','研发周报 · 第 42 周','文档更新通知']
                result=[MessageSummary(str(i),params['folder_id'],titles[i%5]+'（演示）',['产品协作组','研发团队','桌面预览'][i%3],'2026-10-15 09:30',{'read':i%3!=0}) for i in range(params['offset'],min(params['offset']+50,1000))]
            elif operation=='fetch_mime': result=b'From: demo@example.invalid\r\nSubject: Synthetic demo\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nSynthetic demo data only.'
            elif operation=='parse_message':
                from ..mime.parser import parse_mime
                result=parse_mime(raw=params['raw'],message_id=params['message_id'])
            elif operation in ('save_draft','request_send'):
                from ..errors import AppError
                self.failed.emit(task,view_generation,AppError('DEMO_WRITE_DISABLED')); return
            self.completed.emit(task,view_generation,result)
        QTimer.singleShot(15,finish); return task
    def populate(self,window):
        window.identity='demo@example.invalid'; window.identities={window.identity}; window._set_authenticated(True); window.stack.setCurrentWidget(window.splitter); window.submit('list_folders',{})
    def close(self): pass
