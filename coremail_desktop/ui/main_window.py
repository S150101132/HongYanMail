from PySide6.QtCore import Qt,QSettings,Signal,QSaveFile,QIODevice
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QStackedWidget,QSplitter,QTreeWidget,QTreeWidgetItem,QToolBar,QInputDialog,QMessageBox,QToolButton,QMenu,QFileDialog
from .login import LoginPane
from .mail_list import MailListPane,value
from .composer import Composer
from .branding import APP_NAME,app_icon

class MainWindow(QMainWindow):
    certificate_requested=Signal(); lock_requested=Signal()
    def __init__(self,controller,reader_factory=None,autosave_factory=None,demo=False):
        super().__init__(); self.controller=controller; self.autosave_factory=autosave_factory; self.demo=demo; self.pending={}; self.generation=0; self.folder_id=None; self.document=None; self.identity=''; self.identities=set(); self.composer=None; self.settings=QSettings('CoremailPreview','Desktop')
        self.folder_generation = 0
        self.search_pattern=None; self.view_changes={}; self.next_offset=None; self.removed_count=0; self.folder_rows=[]
        self.task_composers = {}
        self.setWindowTitle(APP_NAME+' · 工作邮箱'+(' · 演示数据' if demo else '')); self.setWindowIcon(app_icon()); self.resize(1280,800); self.setMinimumSize(1000,680)
        toolbar=QToolBar(); toolbar.setMovable(False); self.addToolBar(toolbar)
        self.new_button=QPushButton('写邮件'); self.new_button.setProperty('primary',True); self.new_button.clicked.connect(lambda:self.compose('new')); toolbar.addWidget(self.new_button)
        brand=QLabel('  '+APP_NAME+'  /  工作邮箱'); brand.setProperty('heading',True); toolbar.addWidget(brand)
        spacer=QWidget(); spacer.setSizePolicy(__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding,__import__('PySide6.QtWidgets',fromlist=['QSizePolicy']).QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)
        folders_action=toolbar.addAction('文件夹'); folders_action.triggered.connect(lambda:self.folders.setVisible(not self.folders.isVisible()))
        drafts=toolbar.addAction('本机草稿'); drafts.triggered.connect(lambda:self.submit('list_drafts',{},0))
        settings_button=QToolButton(); settings_button.setText('设置与工具'); settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(settings_button); settings_button.setMenu(menu); toolbar.addWidget(settings_button)
        login_action=menu.addAction('登录邮箱'); login_action.triggered.connect(self.show_login)
        menu.addSeparator()
        cert=menu.addAction('管理证书'); cert.triggered.connect(self.certificate_requested)
        lock=menu.addAction('锁定证书'); lock.triggered.connect(self._lock)
        dark=menu.addAction('深色模式'); dark.setCheckable(True); dark.setChecked(self.settings.value('dark',False,type=bool)); dark.toggled.connect(self._theme)
        menu.addSeparator()
        reconcile=menu.addAction('核对发送结果'); reconcile.triggered.connect(lambda:self.submit('pending_for_review',{},0))
        self.stack=QStackedWidget(); self.setCentralWidget(self.stack); self.login=LoginPane(); self.login.requested.connect(self._login); self.stack.addWidget(self.login)
        self.splitter=QSplitter(); self.stack.addWidget(self.splitter); self.folders=QTreeWidget(); self.folders.setHeaderHidden(True); self.folders.setMinimumWidth(170); self.folders.setAccessibleName('邮箱文件夹'); self.folders.currentItemChanged.connect(lambda current,previous:self._folder_selected(current,0) if current is not None else None); self.splitter.addWidget(self.folders)
        self.mail_list=MailListPane(); self.mail_list.setMinimumWidth(300); self.mail_list.selected.connect(self._select_message); self.mail_list.refresh_requested.connect(self.refresh); self.mail_list.more_requested.connect(self.load_more); self.splitter.addWidget(self.mail_list)
        self.mail_list.search_requested.connect(self.search_mail); self.mail_list.search_closed.connect(self.close_search)
        pane=QWidget(); pane.setObjectName('readingPane'); box=QVBoxLayout(pane); box.setContentsMargins(26,24,26,20); box.setSpacing(16); self.subject=QLabel('选择邮件开始阅读'); self.subject.setTextFormat(Qt.TextFormat.PlainText); self.subject.setWordWrap(True); self.subject.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.subject.setProperty('heading',True); box.addWidget(self.subject); self.headers=QLabel(''); self.headers.setWordWrap(True); self.headers.setTextFormat(Qt.TextFormat.PlainText); self.headers.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); self.headers.setProperty('muted',True); box.addWidget(self.headers)
        actions=QHBoxLayout(); self.reply_buttons=[]
        for title,mode in [('回复','reply'),('回复全部','reply_all'),('转发正文','forward')]:
            button=QPushButton(title); button.clicked.connect(lambda checked=False,m=mode:self.compose(m)); actions.addWidget(button); self.reply_buttons.append(button)
        actions.addStretch()
        self.attachment_button=QToolButton(); self.attachment_button.setText('保存附件'); self.attachment_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.attachment_menu=QMenu(self.attachment_button); self.attachment_button.setMenu(self.attachment_menu); self.attachment_button.setEnabled(False); actions.addWidget(self.attachment_button); box.addLayout(actions)
        self.message_tools=QToolButton(); self.message_tools.setText('更多'); self.message_tools.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        message_menu=QMenu(self.message_tools); self.message_tools.setMenu(message_menu); self.message_tools.setEnabled(False)
        for label,flags in [('标为已读',{'read':True}),('标为未读',{'read':False}),('添加星标',{'flagged':True}),('取消星标',{'flagged':False})]:
            action=message_menu.addAction(label); action.triggered.connect(lambda checked=False,f=flags:self.set_message_flags(f))
        message_menu.addSeparator()
        message_menu.addAction('移动到文件夹…',self.choose_move_folder)
        message_menu.addAction('移入废纸篓',self.trash_message)
        actions.addWidget(self.message_tools)
        if reader_factory is None:
            from ..reader.view import ReaderView
            reader_factory=ReaderView
        self.reader=reader_factory(); box.addWidget(self.reader,1); self.notice=QLabel('远程图片已阻止 · 附件可通过“保存附件”另存为'); self.notice.setWordWrap(True); self.notice.setProperty('muted',True); box.addWidget(self.notice); self.attachments=QLabel(''); self.attachments.setTextFormat(Qt.TextFormat.PlainText); self.attachments.setWordWrap(True); box.addWidget(self.attachments); self.splitter.addWidget(pane); self.splitter.setSizes([210,370,700])
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
        self.mail_list.search_button.setEnabled(enabled); self.mail_list.search.setEnabled(enabled)
        for button in self.reply_buttons: button.setEnabled(False)
    def show_login(self): self.stack.setCurrentWidget(self.login); self.login.button.setEnabled(True)
    def _login(self,params):
        from ..models import AccountConfig
        self.generation += 1
        self.folder_generation += 1
        self.identity=params['username']; self.submit('login',{'account':AccountConfig('primary',params['base_url'],params['username']),'password':params['password']},0)
    def submit(self,operation,params,generation=None):
        generation=self.generation if generation is None else generation
        task=self.controller.submit(operation,params,generation)
        # Keep only response-routing metadata, never passwords, raw MIME or drafts.
        context={key:params[key] for key in ('folder_id','offset','message_id') if key in params}
        if operation in ('list_messages','search_messages','set_message_flags','move_message'):
            context['_folder_generation']=self.folder_generation
            context['folder_id']=self.folder_id
            if operation in ('list_messages','search_messages'): context['_removed_count']=self.removed_count
        if operation in ('save_draft','request_send'): self.task_composers[task]=self.composer
        self.pending[task]=(operation,context)
        self._update_list_busy()
        self.statusBar().showMessage({'login':'正在登录…','request_send':'正在处理发送…','fetch_mime':'正在读取邮件…'}.get(operation,'正在读取…')); return task
    def _current_result(self,operation,params,generation):
        if operation in ('list_messages','search_messages','set_message_flags','move_message') and '_folder_generation' in params:
            return self.authenticated and params['_folder_generation']==self.folder_generation and params.get('folder_id')==self.folder_id
        return generation==self.generation
    def _update_list_busy(self):
        busy=self._list_request_pending()
        self.mail_list.refresh.setEnabled(self.authenticated and not busy)
        self.mail_list.more.setEnabled(self.authenticated and not busy and getattr(self,'has_more',False))
        self.mail_list.refresh.setText('正在加载…' if busy else '刷新')
        flag_busy=self.document is not None and self._message_write_pending(self.document.remote_id)
        self.message_tools.setEnabled(self.authenticated and self.document is not None and not flag_busy)
    def _completed(self,task,generation,result):
        operation,params=self.pending.pop(task,('',{}))
        target=self.task_composers.pop(task,self.composer)
        self._update_list_busy()
        if operation not in ('login','request_send','save_draft','list_drafts','restore_draft','reconcile','get_account_identities','recover_pending','pending_for_review') and not self._current_result(operation,params,generation): return
        if operation=='login':
            self.login.set_error(''); self._set_authenticated(True); self.stack.setCurrentWidget(self.splitter); self.submit('list_folders',{}); self.submit('get_account_identities',{},0); self.submit('recover_pending',{},0)
        elif operation=='get_account_identities':
            self.identities=set(result)
            self.identity=next((address for address in self.identities if address.casefold()==self.identity.casefold()),sorted(self.identities)[0] if self.identities else self.identity)
        elif operation=='list_folders':
            self.folder_rows=list(result)
            self.folders.clear()
            for folder in result:
                item=QTreeWidgetItem([f"{value(folder,'name')}  {value(folder,'counts',{}).get('total','')}"]); item.setData(0,Qt.ItemDataRole.UserRole,value(folder,'remote_id')); self.folders.addTopLevelItem(item)
            if self.folders.topLevelItemCount():
                inbox=next((n for n,folder in enumerate(result) if value(folder,'role')=='inbox'),0)
                self.folders.setCurrentItem(self.folders.topLevelItem(inbox))
        elif operation in ('list_messages','search_messages'):
            rows=result['messages'] if operation=='search_messages' else result
            self.next_offset=max(0,params.get('offset',0)+len(rows)-(self.removed_count-params.get('_removed_count',0)))
            self.mail_list.set_messages(self._apply_view_changes(rows),append=params.get('offset',0)>0)
            self.has_more=self.next_offset<result['total'] and bool(rows) if operation=='search_messages' else len(rows)==50
            self._update_list_busy()
            if operation=='search_messages': self.statusBar().showMessage(f'服务器搜索：共 {result["total"]} 封 · 已加载 {len(self.mail_list.loaded)} 封'); return
        elif operation=='set_message_flags':
            changes=self.view_changes.setdefault(params['message_id'],{})
            changes['flags']={**changes.get('flags',{}),**value(result,'flags',{})}
            self.mail_list.update_message_flags(params['message_id'],value(result,'flags',{}))
            self.statusBar().showMessage('邮件状态已更新。'); return
        elif operation=='move_message':
            message_id=params['message_id']
            leaves_view=(self.search_pattern is None and result['folder_id']!=self.folder_id) or (self.search_pattern is not None and result['folder_id'] in self._trash_ids())
            if leaves_view and any(value(row,'remote_id')==message_id for row in self.mail_list.loaded):
                self.removed_count+=1
                if self.next_offset is not None: self.next_offset=max(0,self.next_offset-1)
            self.view_changes.setdefault(message_id,{})['folder_id']=result['folder_id']
            self.mail_list.set_messages(self._apply_view_changes(self.mail_list.loaded))
            if self.document is not None and self.document.remote_id==message_id and leaves_view:
                self.generation+=1; self._clear_message()
            self.statusBar().showMessage('邮件已移动，可在目标文件夹查看或移回。'); return
        elif operation=='fetch_mime': self.submit('parse_message',{'raw':result,'message_id':params['message_id']},generation)
        elif operation=='parse_message': self._show_document(result)
        elif operation=='request_send':
            if target: target.set_send_state(value(result,'state','Accepted'))
            if value(result,'state')=='Failed':
                from .messages import user_message
                message=user_message(value(result,'failure_code') or 'SEND_REJECTED')
                if target: target.state_label.setText('未发送。'+message)
                self.statusBar().showMessage(message); return
        elif operation=='save_draft':
            if target: target._saved(result if isinstance(result,int) else target.draft.revision)
        elif operation=='list_drafts':
            rows=list(result)
            if not rows: QMessageBox.information(self,'本机草稿','没有可恢复的本机草稿。')
            else:
                ids=[value(row,'local_id') if isinstance(row,dict) else str(row) for row in rows]
                labels=[f'{n+1}. {value(row,"subject") or "（无主题）"}  ·  {value(row,"modified")}' if isinstance(row,dict) else str(row) for n,row in enumerate(rows)]
                selected,ok=QInputDialog.getItem(self,'恢复本机草稿','选择本机草稿（按最近修改排序）',labels,0,False)
                if ok and selected in labels: self.submit('restore_draft',{'local_id':ids[labels.index(selected)]},0)
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
        self.statusBar().showMessage('演示模式 · 所有邮件均为合成示例' if self.demo else f'最近成功操作 {datetime.now():%H:%M:%S} · 已连接')
    def _failed(self,task,generation,error):
        op,params=self.pending.pop(task,('',{}))
        target=self.task_composers.pop(task,self.composer)
        self._update_list_busy()
        from .messages import user_message
        code=getattr(error,'code','INTERNAL_ERROR')
        message=user_message(code)
        if op=='login':
            from .messages import login_message
            message=login_message(code,getattr(error,'category',''))
            self.login.set_error(message)
        elif op=='request_send' and target:
            category=str(getattr(error,'category','')).lower(); target.set_send_state('Unknown' if 'unknown' in category else 'Failed'); target.state_label.setText(target.state_label.text()+' '+message)
        elif op=='save_draft' and target: target._save_failed(error)
        elif not self._current_result(op,params,generation): return
        self.statusBar().showMessage(message)
    def _clear_message(self):
        self.document=None; self.reader.clear(); self.subject.setText('选择邮件开始阅读'); self.headers.clear(); self.attachments.clear()
        self.attachment_menu.clear(); self.attachment_button.setEnabled(False)
        self.message_tools.setEnabled(False)
        for button in self.reply_buttons: button.setEnabled(False)
    def _folder_selected(self,item,_column):
        self.search_pattern=None; self.view_changes={}; self.next_offset=None; self.removed_count=0; self.mail_list.search_back.hide()
        self.generation+=1; self.folder_generation+=1; self.folder_id=item.data(0,Qt.ItemDataRole.UserRole); self.mail_list.title.setText(item.text(0)); self.mail_list.filter.clear(); self.mail_list.set_messages([]); self.has_more=False; self._clear_message()
        self.refresh()
    def _list_request_pending(self):
        return any(op in ('list_messages','search_messages','move_message') and p.get('_folder_generation')==self.folder_generation for op,p in self.pending.values())
    def refresh(self):
        if self.authenticated and not self._list_request_pending():
            # No old page is outstanding: a deliberate refresh adopts server
            # state, including changes from another client.
            self.view_changes={}; self.removed_count=0; self._request_page(0)
    def load_more(self):
        if self.authenticated and not self._list_request_pending(): self._request_page(self.next_offset if self.next_offset is not None else len(self.mail_list.loaded))
    def _request_page(self,offset):
        if self.search_pattern is not None: self.submit('search_messages',{'pattern':self.search_pattern,'offset':offset,'limit':50})
        elif self.folder_id is not None: self.submit('list_messages',{'folder_id':self.folder_id,'offset':offset,'limit':50})
    def search_mail(self):
        pattern=self.mail_list.search.text().strip()
        if not self.authenticated: return
        if not pattern or len(pattern)>1000:
            from .messages import user_message
            self.statusBar().showMessage(user_message('INVALID_SEARCH')); return
        self.generation+=1; self.folder_generation+=1; self.search_pattern=pattern; self.view_changes={}; self.next_offset=0
        self.mail_list.title.setText('整个邮箱 · 搜索结果'); self.mail_list.search_back.show(); self.mail_list.filter.clear(); self.mail_list.set_messages([]); self.has_more=False; self._clear_message(); self.refresh()
    def close_search(self):
        item=self.folders.currentItem()
        if item is not None: self._folder_selected(item,0)
    def _apply_view_changes(self,rows):
        from dataclasses import replace
        result=[]
        for row in rows:
            changes=self.view_changes.get(value(row,'remote_id'),{})
            if changes:
                attrs=dict(changes)
                if 'flags' in attrs: attrs['flags']={**dict(value(row,'flags',{})),**attrs['flags']}
                row={**row,**attrs} if isinstance(row,dict) else replace(row,**attrs)
            if (self.search_pattern is not None and value(row,'folder_id') not in self._trash_ids()) or (self.search_pattern is None and value(row,'folder_id')==self.folder_id): result.append(row)
        return result
    def _trash_ids(self):
        return {value(folder,'remote_id') for folder in self.folder_rows if value(folder,'role')=='trash'}
    def _message_write_pending(self,message_id):
        return any(op in ('set_message_flags','move_message') and p.get('message_id')==message_id for op,p in self.pending.values())
    def move_message(self,folder_id):
        if not self.authenticated or self.document is None or self._message_write_pending(self.document.remote_id): return
        message_id=self.document.remote_id
        current=next((value(row,'folder_id') for row in self.mail_list.loaded if value(row,'remote_id')==message_id),None)
        if current==folder_id: self.statusBar().showMessage('邮件已在此文件夹。'); return
        self.submit('move_message',{'message_id':message_id,'folder_id':folder_id})
    def choose_move_folder(self):
        document=self.document
        if document is None: return
        rows=list(self.folder_rows); labels=[f'{n+1}. {value(folder,"name")}' for n,folder in enumerate(rows)]
        selected,ok=QInputDialog.getItem(self,'移动邮件','选择目标文件夹；可用相同操作移回',labels,0,False)
        if ok and selected in labels and self.document is document: self.move_message(value(rows[labels.index(selected)],'remote_id'))
    def trash_message(self):
        folder=next((folder for folder in self.folder_rows if value(folder,'role')=='trash'),None)
        if folder is None: self.statusBar().showMessage('尚未识别废纸篓，请使用“移动到文件夹”选择。'); return
        self.move_message(value(folder,'remote_id'))
    def _select_message(self,summary):
        self.generation+=1; self._clear_message(); self.subject.setText(value(summary,'subject') or '（无主题）'); self.headers.setText(value(summary,'sender'))
        for button in self.reply_buttons: button.setEnabled(False)
        self.submit('fetch_mime',{'message_id':value(summary,'remote_id')})
    def _show_document(self,document):
        self.document=document; headers=value(document,'headers',{}); self.subject.setText(', '.join(headers.get('subject',())) or self.subject.text()); self.headers.setText('\n'.join(f'{name}：{", ".join(headers.get(key,()))}' for name,key in [('发件人','from'),('收件人','to'),('日期','date')]))
        setter=getattr(self.reader,'set_document',None) or self.reader.show_document; setter(document)
        self.attachment_menu.clear()
        payloads=getattr(document,'attachment_payloads',{})
        for attachment in document.attachments:
            action=self.attachment_menu.addAction(attachment.name.replace('&','&&'))
            action.setEnabled(attachment.part_id in payloads)
            action.triggered.connect(lambda checked=False,doc=document,part=attachment:self.save_attachment(doc,part))
        self.attachment_button.setEnabled(bool(payloads))
        visible=document.attachments[:3]
        self.attachments.setText('\n'.join(f'附件：{a.name} · {a.size if a.size is not None else "未知"} 字节' for a in visible)+(f'\n共 {len(document.attachments)} 个附件，完整列表见“保存附件”。' if len(document.attachments)>3 else ''))
        for n,button in enumerate(self.reply_buttons): button.setEnabled(n==2 or getattr(self.controller,'supports_reply_headers',False))
        if not getattr(self.controller,'supports_reply_headers',False): self.reply_buttons[0].setToolTip('回复引用头协议尚未完成真实验证'); self.reply_buttons[1].setToolTip('回复引用头协议尚未完成真实验证')
        self._update_list_busy()
    def set_message_flags(self,flags):
        if not self.authenticated or self.document is None: return
        message_id=self.document.remote_id
        if self._message_write_pending(message_id): return
        self.submit('set_message_flags',{'message_id':message_id,'flags':dict(flags)})
    def save_attachment(self,document,attachment):
        if document is not self.document: return
        payload=getattr(document,'attachment_payloads',{}).get(attachment.part_id)
        if payload is None: return
        path,_=QFileDialog.getSaveFileName(self,'保存附件',attachment.name)
        if not path or document is not self.document: return
        file=QSaveFile(path)
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            self.statusBar().showMessage('附件保存失败，请检查保存位置和权限。'); return
        if file.write(payload)!=len(payload):
            file.cancelWriting(); self.statusBar().showMessage('附件保存失败，未覆盖原文件。'); return
        if not file.commit(): self.statusBar().showMessage('附件保存失败，未覆盖原文件。'); return
        self.statusBar().showMessage('附件已保存。')
    def compose(self,mode):
        if not self.authenticated: return
        if self.composer and self.composer.isVisible(): self.composer.raise_(); self.composer.activateWindow(); return
        from ..compose.service import ComposeService,build_reply,build_forward
        if mode=='new': draft=ComposeService().new_draft(self.identity)
        elif self.document is None: return
        elif mode=='forward': draft=build_forward(self.document,self.identity)
        else: draft=build_reply(self.document,self.identities,mode=='reply_all',self.identity)
        self.open_composer(draft)
    def open_composer(self,draft):
        if self.composer and self.composer.isVisible():
            self.composer.raise_(); self.composer.activateWindow(); self.statusBar().showMessage('请先完成或关闭当前写信窗口，再恢复另一份草稿。'); return
        self.composer=Composer(draft,self.autosave_factory() if self.autosave_factory else None,self); self.composer.save_requested.connect(lambda d:self.submit('save_draft',{'draft':d},0)); self.composer.send_requested.connect(lambda d:self.submit('request_send',{'draft':d},0)); self.composer.show()
    def _auth_required(self):
        self.generation+=1; self.folder_generation+=1; self._clear_message(); self._set_authenticated(False); self._update_list_busy(); self.show_login(); self.login.set_error('会话已失效，请重新登录。编辑中的草稿仍保留。')
    def _lock(self):
        self.generation+=1; self._clear_message(); self.lock_requested.emit(); self.statusBar().showMessage('证书已锁定，正文已清除。')
        for button in self.reply_buttons: button.setEnabled(False)
    def find_body(self):
        text,ok=QInputDialog.getText(self,'正文内查找','查找文字')
        if ok and text:
            fallback=getattr(self.reader,'fallback',None)
            view=fallback if fallback is not None and not fallback.isHidden() else (getattr(self.reader,'web_view',None) or self.reader)
            if hasattr(view,'findText'): view.findText(text)
            elif isinstance(view,__import__('PySide6.QtWidgets',fromlist=['QTextEdit']).QTextEdit):
                if not view.find(text):
                    cursor=view.textCursor(); cursor.movePosition(cursor.MoveOperation.Start); view.setTextCursor(cursor); view.find(text)
    def closeEvent(self,event):
        if self.composer and self.composer.isVisible() and not self.composer.close():
            event.ignore(); return
        self.settings.setValue('splitter',self.splitter.saveState()); self.lock_requested.emit(); event.accept()

