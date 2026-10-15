from PySide6.QtCore import Qt,QSettings,Signal
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QStackedWidget,QSplitter,QTreeWidget,QTreeWidgetItem,QToolBar,QInputDialog,QMessageBox
from .login import LoginPane
from .mail_list import MailListPane,value
from .composer import Composer

class MainWindow(QMainWindow):
    certificate_requested=Signal(); lock_requested=Signal()
    def __init__(self,controller,reader_factory=None,autosave_factory=None,demo=False):
        super().__init__(); self.controller=controller; self.autosave_factory=autosave_factory; self.demo=demo; self.pending={}; self.generation=0; self.folder_id=None; self.document=None; self.identity=''; self.identities=set(); self.composer=None; self.settings=QSettings('CoremailPreview','Desktop')
        self.setWindowTitle('Coremail · 桌面预览版'+(' · 演示数据' if demo else '')); self.resize(1280,800); self.setMinimumSize(1000,680)
        toolbar=QToolBar(); toolbar.setMovable(False); self.addToolBar(toolbar)
        self.new_button=QPushButton('写邮件'); self.new_button.setProperty('primary',True); self.new_button.clicked.connect(lambda:self.compose('new')); toolbar.addWidget(self.new_button)
        brand=QLabel('  Coremail  /  工作邮箱'); brand.setProperty('heading',True); toolbar.addWidget(brand)
        spacer=QWidget(); spacer.setSizePolicy(__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding,__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)
        login_action=toolbar.addAction('登录'); login_action.triggered.connect(self.show_login)
        folders_action=toolbar.addAction('文件夹'); folders_action.triggered.connect(lambda:self.folders.setVisible(not self.folders.isVisible()))
        cert=toolbar.addAction('证书'); cert.triggered.connect(self.certificate_requested)
        lock=toolbar.addAction('锁定证书'); lock.triggered.connect(self._lock)
        dark=toolbar.addAction('深色模式'); dark.setCheckable(True); dark.setChecked(self.settings.value('dark',False,type=bool)); dark.toggled.connect(self._theme)
        drafts=toolbar.addAction('恢复本机草稿'); drafts.triggered.connect(lambda:self.submit('list_drafts',{},0))
        reconcile=toolbar.addAction('核对发送结果'); reconcile.triggered.connect(lambda:self.submit('pending_for_review',{},0))
        self.stack=QStackedWidget(); self.setCentralWidget(self.stack); self.login=LoginPane(); self.login.requested.connect(self._login); self.stack.addWidget(self.login)
        self.splitter=QSplitter(); self.stack.addWidget(self.splitter); self.folders=QTreeWidget(); self.folders.setHeaderHidden(True); self.folders.setMinimumWidth(170); self.folders.itemClicked.connect(self._folder_selected); self.splitter.addWidget(self.folders)
        self.mail_list=MailListPane(); self.mail_list.setMinimumWidth(300); self.mail_list.selected.connect(self._select_message); self.mail_list.refresh_requested.connect(self.refresh); self.mail_list.more_requested.connect(self.load_more); self.splitter.addWidget(self.mail_list)
        pane=QWidget(); box=QVBoxLayout(pane); box.setContentsMargins(22,20,22,16); self.subject=QLabel('选择邮件开始阅读'); self.subject.setTextFormat(Qt.TextFormat.PlainText); self.subject.setWordWrap(True); self.subject.setProperty('heading',True); box.addWidget(self.subject); self.headers=QLabel(''); self.headers.setWordWrap(True); self.headers.setTextFormat(Qt.TextFormat.PlainText); self.headers.setProperty('muted',True); box.addWidget(self.headers)
        actions=QHBoxLayout(); self.reply_buttons=[]
        for title,mode in [('回复','reply'),('回复全部','reply_all'),('转发正文','forward')]:
            button=QPushButton(title); button.clicked.connect(lambda checked=False,m=mode:self.compose(m)); actions.addWidget(button); self.reply_buttons.append(button)
        actions.addStretch(); box.addLayout(actions)
        if reader_factory is None:
            from ..reader.view import ReaderView
            reader_factory=ReaderView
        self.reader=reader_factory(); box.addWidget(self.reader,1); self.notice=QLabel('远程图片与外部资源始终阻止 · 附件仅显示信息'); self.notice.setProperty('muted',True); box.addWidget(self.notice); self.attachments=QLabel(''); self.attachments.setTextFormat(Qt.TextFormat.PlainText); self.attachments.setWordWrap(True); box.addWidget(self.attachments); self.splitter.addWidget(pane); self.splitter.setSizes([210,370,700])
        state=self.settings.value('splitter')
        if state: self.splitter.restoreState(state)
        self.statusBar().showMessage('尚未连接 · 启动不会访问邮箱服务器'); self._set_authenticated(False)
        controller.completed.connect(self._completed); controller.failed.connect(self._failed); controller.auth_required.connect(self._auth_required)
        for key,slot in [('Ctrl+N',lambda:self.compose('new')),('Ctrl+S',self.save_shortcut),('Ctrl+K',self.mail_list.filter.setFocus),('F5',self.refresh),('Ctrl+F',self.find_body)]:
            action=QAction(self); action.setShortcut(QKeySequence(key)); action.triggered.connect(slot); self.addAction(action)
    def save_shortcut(self):
        if self.composer and self.composer.isVisible(): self.composer.save()
    def _theme(self,dark):
        from .theme import apply_theme
        from PySide6.QtWidgets import QApplication
        apply_theme(QApplication.instance(),dark); self.settings.setValue('dark',dark)
    def _set_authenticated(self,enabled):
        self.authenticated=enabled; self.new_button.setEnabled(enabled)
        for button in self.reply_buttons: button.setEnabled(False)
    def show_login(self): self.stack.setCurrentWidget(self.login); self.login.button.setEnabled(True)
    def _login(self,params):
        from ..models import AccountConfig
        self.identity=params['username']; self.submit('login',{'account':AccountConfig('primary',params['base_url'],params['username']),'password':params['password']},0)
    def submit(self,operation,params,generation=None):
        generation=self.generation if generation is None else generation
        task=self.controller.submit(operation,params,generation); self.pending[task]=(operation,params); self.statusBar().showMessage({'login':'正在登录…','request_send':'正在处理发送…','fetch_mime':'正在读取邮件…'}.get(operation,'正在读取…')); return task
    def _completed(self,task,generation,result):
        operation,params=self.pending.pop(task,('',{}))
        if operation not in ('login','request_send','save_draft','list_drafts','restore_draft','reconcile','get_account_identities','recover_pending','pending_for_review') and generation!=self.generation: return
        if operation=='login':
            self.login.set_error(''); self._set_authenticated(True); self.stack.setCurrentWidget(self.splitter); self.submit('list_folders',{}); self.submit('get_account_identities',{},0); self.submit('recover_pending',{},0)
        elif operation=='get_account_identities':
            self.identities=set(result); self.identity=next(iter(self.identities),self.identity)
        elif operation=='list_folders':
            self.folders.clear()
            for folder in result:
                item=QTreeWidgetItem([f"{value(folder,'name')}  {value(folder,'counts',{}).get('total','')}"]); item.setData(0,Qt.ItemDataRole.UserRole,value(folder,'remote_id')); self.folders.addTopLevelItem(item)
            if self.folders.topLevelItemCount(): self.folders.setCurrentItem(self.folders.topLevelItem(0)); self._folder_selected(self.folders.topLevelItem(0),0)
        elif operation=='list_messages':
            self.mail_list.set_messages(result,append=params.get('offset',0)>0); self.mail_list.more.setEnabled(len(result)==50)
        elif operation=='fetch_mime': self.submit('parse_message',{'raw':result,'message_id':params['message_id']},generation)
        elif operation=='parse_message': self._show_document(result)
        elif operation=='request_send':
            if self.composer: self.composer.set_send_state(value(result,'state','Accepted'))
        elif operation=='save_draft':
            if self.composer: self.composer._saved(result if isinstance(result,int) else self.composer.draft.revision)
        elif operation=='list_drafts':
            ids=list(result)
            if not ids: QMessageBox.information(self,'本机草稿','没有可恢复的本机草稿。')
            else:
                selected,ok=QInputDialog.getItem(self,'恢复本机草稿','选择本机草稿',ids,0,False)
                if ok: self.submit('restore_draft',{'local_id':selected},0)
        elif operation=='restore_draft': self.open_composer(result)
        elif operation=='pending_for_review':
            ids=[value(job,'local_id') for job in result]
            if not ids: QMessageBox.information(self,'发送记录','没有结果待确认的发送记录。')
            else:
                selected,ok=QInputDialog.getItem(self,'只读核对发送结果','选择本机发送记录；不会重发',ids,0,False)
                if ok: self.submit('reconcile',{'local_id':selected},0)
        elif operation=='reconcile':
            state=value(result,'state')
            QMessageBox.information(self,'发送核对结果','服务器已接受邮件。' if state=='Accepted' else '仍缺少足够的发送证据，结果继续待确认。请到原客户端核对；本应用不会重发。')
        elif operation=='recover_pending':
            unknown=[job for job in result if value(job,'state') in ('Unknown','Submitting','Reconciling')]
            if unknown:
                self.statusBar().showMessage(f'发现 {len(unknown)} 项发送记录待确认；不会自动重发。'); QMessageBox.warning(self,'发送结果待确认','上次发送结果不明。请在服务器已发送中核对，应用不会自动重发。'); return
        from datetime import datetime
        self.statusBar().showMessage('演示模式 · 所有邮件均为合成示例' if self.demo else f'最近成功操作 {datetime.now():%H:%M:%S} · 已读状态不自动回写服务器')
    def _failed(self,task,generation,error):
        op,_=self.pending.pop(task,('',{}))
        from .messages import user_message
        code=getattr(error,'code','INTERNAL_ERROR')
        message=user_message(code)
        if op=='login': self.login.set_error(message)
        elif op=='request_send' and self.composer:
            category=str(getattr(error,'category','')).lower(); self.composer.set_send_state('Unknown' if 'unknown' in category else 'Failed'); self.composer.state_label.setText(self.composer.state_label.text()+' '+message)
        elif op=='save_draft' and self.composer: self.composer._save_failed(error)
        elif generation!=self.generation: return
        self.statusBar().showMessage(message)
    def _folder_selected(self,item,_column):
        self.generation+=1; self.folder_id=item.data(0,Qt.ItemDataRole.UserRole); self.mail_list.title.setText(item.text(0)); self.mail_list.set_messages([]); self.document=None; self.reader.clear()
        for button in self.reply_buttons: button.setEnabled(False)
        self.refresh()
    def refresh(self):
        if self.authenticated and self.folder_id is not None: self.submit('list_messages',{'folder_id':self.folder_id,'offset':0,'limit':50})
    def load_more(self):
        if self.authenticated and self.folder_id is not None: self.submit('list_messages',{'folder_id':self.folder_id,'offset':len(self.mail_list.loaded),'limit':50})
    def _select_message(self,summary):
        self.generation+=1; self.document=None; self.subject.setText(value(summary,'subject') or '（无主题）'); self.headers.setText(value(summary,'sender')); self.reader.clear()
        for button in self.reply_buttons: button.setEnabled(False)
        self.submit('fetch_mime',{'message_id':value(summary,'remote_id')})
    def _show_document(self,document):
        self.document=document; headers=value(document,'headers',{}); self.subject.setText(', '.join(headers.get('subject',())) or self.subject.text()); self.headers.setText('\n'.join(f'{name}：{", ".join(headers.get(key,()))}' for name,key in [('发件人','from'),('收件人','to'),('日期','date')]))
        setter=getattr(self.reader,'set_document',None) or self.reader.show_document; setter(document)
        self.attachments.setText('\n'.join(f'附件：{a.name} · {a.media_type} · {a.size if a.size is not None else "未知"} 字节（V0.1 不提供下载）' for a in document.attachments))
        for n,button in enumerate(self.reply_buttons): button.setEnabled(n==2 or getattr(self.controller,'supports_reply_headers',False))
        if not getattr(self.controller,'supports_reply_headers',False): self.reply_buttons[0].setToolTip('回复引用头协议尚未完成真实验证'); self.reply_buttons[1].setToolTip('回复引用头协议尚未完成真实验证')
    def compose(self,mode):
        if not self.authenticated: return
        if self.composer and self.composer.isVisible(): self.composer.raise_(); self.composer.activateWindow(); return
        from ..compose.service import ComposeService,build_reply,build_forward
        if mode=='new': draft=ComposeService().new_draft(self.identity)
        elif self.document is None: return
        elif mode=='forward': draft=build_forward(self.document,self.identity)
        else: draft=build_reply(self.document,self.identities,mode=='reply_all')
        self.open_composer(draft)
    def open_composer(self,draft):
        self.composer=Composer(draft,self.autosave_factory() if self.autosave_factory else None,self); self.composer.save_requested.connect(lambda d:self.submit('save_draft',{'draft':d},0)); self.composer.send_requested.connect(lambda d:self.submit('request_send',{'draft':d},0)); self.composer.show()
    def _auth_required(self): self._set_authenticated(False); self.show_login(); self.login.set_error('会话已失效，请重新登录。编辑中的草稿仍保留。')
    def _lock(self):
        self.generation+=1; self.reader.clear(); self.document=None; self.lock_requested.emit(); self.statusBar().showMessage('证书已锁定，正文已清除。')
        for button in self.reply_buttons: button.setEnabled(False)
    def find_body(self):
        text,ok=QInputDialog.getText(self,'正文内查找','查找文字')
        if ok and text:
            view=getattr(self.reader,'view',self.reader)
            if hasattr(view,'findText'): view.findText(text)
            elif hasattr(view,'find'): view.find(text)
    def closeEvent(self,event):
        if self.composer and self.composer.isVisible() and not self.composer.close():
            event.ignore(); return
        self.settings.setValue('splitter',self.splitter.saveState()); self.lock_requested.emit(); event.accept()

