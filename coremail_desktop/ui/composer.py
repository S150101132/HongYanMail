from dataclasses import replace
from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QKeySequence,QShortcut
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QHBoxLayout,QLineEdit,QPlainTextEdit,QLabel,QPushButton,QCheckBox,QMessageBox,QFileDialog,QListWidget

class Composer(QDialog):
    send_requested=Signal(object); save_requested=Signal(object)
    def __init__(self,draft,autosave=None,parent=None):
        super().__init__(parent); self.draft=draft; self.autosave=autosave; self.dirty=False; self.sending=False
        self.setWindowTitle('写邮件 · 鸿雁'); self.resize(760,680); box=QVBoxLayout(self); form=QFormLayout()
        self.sender=QLineEdit(draft.from_identity); self.sender.setPlaceholderText('请明确填写完整发件邮箱地址')
        self.to=QLineEdit(', '.join(draft.to)); self.cc=QLineEdit(', '.join(draft.cc)); self.bcc=QLineEdit(', '.join(draft.bcc)); self.subject=QLineEdit(draft.subject)
        for label,field in [('发件人',self.sender),('收件人',self.to),('抄送',self.cc),('密送',self.bcc),('主题',self.subject)]: form.addRow(label,field)
        box.addLayout(form); self.body=QPlainTextEdit(draft.body); self.body.setPlaceholderText('输入纯文本正文…'); box.addWidget(self.body,1)
        attachment_row=QHBoxLayout(); self.add_attachment_button=QPushButton('添加附件…'); self.remove_attachment_button=QPushButton('移除选中附件'); attachment_row.addWidget(self.add_attachment_button); attachment_row.addWidget(self.remove_attachment_button); attachment_row.addStretch(); box.addLayout(attachment_row)
        self.attachment_list=QListWidget(); self.attachment_list.setAccessibleName('写信附件'); self.attachment_list.setMaximumHeight(90); box.addWidget(self.attachment_list)
        self.attachment_summary=QLabel(''); box.addWidget(self.attachment_summary); self._show_attachments()
        self.add_attachment_button.clicked.connect(self.choose_attachments); self.remove_attachment_button.clicked.connect(lambda:self.remove_attachment(self.attachment_list.currentRow()))
        self.encryption=QCheckBox('要求证书加密（缺少证书时阻止发送）'); self.encryption.setChecked(draft.encryption_required); box.addWidget(self.encryption)
        self.guard_label=QLabel(''); self.guard_label.setWordWrap(True); self.guard_label.setVisible(False); box.addWidget(self.guard_label)
        self.state_label=QLabel('本机草稿 · 不同步到服务器'); self.state_label.setWordWrap(True); box.addWidget(self.state_label)
        row=QHBoxLayout(); self.save_button=QPushButton('保存到本机  Ctrl+S'); self.send_button=QPushButton('发送  Ctrl+Enter'); self.send_button.setProperty('primary',True); row.addWidget(self.save_button); row.addStretch(); row.addWidget(self.send_button); box.addLayout(row)
        self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(1000); self.timer.timeout.connect(self.save)
        for f in (self.sender,self.to,self.cc,self.bcc,self.subject,self.body): f.textChanged.connect(self._changed)
        self.encryption.toggled.connect(self._changed); self.save_button.clicked.connect(self.save); self.send_button.clicked.connect(self.request_send)
        QShortcut(QKeySequence('Ctrl+S'),self,activated=self.save); QShortcut(QKeySequence('Ctrl+Return'),self,activated=self.request_send)
        if autosave:
            autosave.saved.connect(self._saved); autosave.failed.connect(self._save_failed)
    def snapshot(self):
        addresses=lambda field:tuple(s.strip() for s in field.text().replace(';',',').replace('；',',').split(',') if s.strip())
        return replace(self.draft,from_identity=self.sender.text().strip(),to=addresses(self.to),cc=addresses(self.cc),bcc=addresses(self.bcc),subject=self.subject.text(),body=self.body.toPlainText(),encryption_required=self.encryption.isChecked())
    def _show_attachments(self):
        self.attachment_list.clear()
        for attachment in self.draft.attachments: self.attachment_list.addItem(f'{attachment.name}  ·  {len(attachment.payload):,} 字节')
        self.attachment_list.setVisible(bool(self.draft.attachments))
        total=sum(len(a.payload) for a in self.draft.attachments)
        self.attachment_summary.setText(f'{len(self.draft.attachments)} 个附件 · {total/1024/1024:.2f} MiB / 20 MiB（单个上限 10 MiB）')
    def choose_attachments(self):
        if self.sending: return
        paths,_=QFileDialog.getOpenFileNames(self,'添加附件')
        if paths: self.add_attachments(paths)
    def add_attachments(self,paths):
        if self.sending: return
        from ..compose.attachments import read_attachment,validate_attachments,MAX_ATTACHMENTS
        from ..errors import AppError
        try:
            if len(paths)+len(self.draft.attachments)>MAX_ATTACHMENTS: raise AppError('ATTACHMENT_TOO_LARGE')
            attachments=list(self.draft.attachments)
            for path in paths:
                attachments.append(read_attachment(path)); validate_attachments(attachments)
        except Exception as error:
            from .messages import user_message
            self.state_label.setText(user_message(getattr(error,'code','ATTACHMENT_UNAVAILABLE'))); return
        self.draft=replace(self.draft,attachments=tuple(attachments)); self._show_attachments(); self._changed()
    def remove_attachment(self,index):
        if self.sending or not 0<=index<len(self.draft.attachments): return
        self.draft=replace(self.draft,attachments=self.draft.attachments[:index]+self.draft.attachments[index+1:]); self._show_attachments(); self._changed()
    def _changed(self,*_):
        self.dirty=True; self.draft=replace(self.draft,revision=self.draft.revision+1); self.state_label.setText('尚未保存到本机'); self.timer.start()
    def save(self):
        self.timer.stop(); self.draft=self.snapshot()
        if self.autosave:
            try: self.autosave.update(self.draft); self.autosave.flush()
            except Exception: self._save_failed(None)
        else: self.save_requested.emit(self.draft)
    def _saved(self,revision):
        if revision==self.draft.revision: self.dirty=False; self.state_label.setText('已保存到本机 · 加密草稿')
    def _save_failed(self,_): self.state_label.setText('本机保存失败：内容仍保留在窗口中，请勿关闭。')
    def set_guard(self,text):
        """Persistent blocking explanation that autosave status must not overwrite."""
        self.guard_label.setText(text or ''); self.guard_label.setVisible(bool(text))
    def request_send(self):
        if self.sending: return
        draft=self.snapshot()
        if draft.encryption_required:
            self.set_guard('加密发送接口尚未完成真实互通验证，已阻止发送。草稿保留加密要求，不会改发明文。')
            self.save(); return
        try:
            from ..compose.recipients import normalize_recipients
            normalize_recipients((draft.from_identity,)); normalize_recipients(draft.to+draft.cc+draft.bcc)
        except Exception: self.state_label.setText('请填写有效的完整发件人和收件人邮箱地址。'); return
        if not draft.to and not draft.cc and not draft.bcc: self.state_label.setText('请填写至少一个收件人。'); return
        if not draft.subject.strip() and QMessageBox.question(self,'空主题','主题为空，仍要发送？')!=QMessageBox.StandardButton.Yes: return
        self.set_guard(''); self.save(); self.sending=True; self.set_send_state('Preparing'); self.send_requested.emit(draft)
    def set_send_state(self,state):
        state=getattr(state,'value',state); self.sending=state in ('Preparing','Submitting','Unknown','Reconciling','Accepted')
        self.send_button.setEnabled(not self.sending)
        for field in (self.sender,self.to,self.cc,self.bcc,self.subject,self.body): field.setReadOnly(self.sending)
        self.encryption.setEnabled(not self.sending)
        self.add_attachment_button.setEnabled(not self.sending); self.remove_attachment_button.setEnabled(not self.sending)
        self.state_label.setText({'Preparing':'正在检查发送内容…','Submitting':'正在提交，请勿重复发送…','Accepted':'服务器已接受邮件（不代表收件人已送达）','Unknown':'发送结果待确认：请先核对已发送，不会自动重发。','Reconciling':'正在核对服务器发送记录…','Failed':'服务器未接受邮件。请检查后重试。'}.get(state,str(state)))
    def confirm_unsaved_close(self):
        """Whether to force the window closed although the draft is unsaved.

        Overridden in tests so the blocking prompt is never raised there; the
        product default always refuses to discard an unsaved draft silently.
        """
        return QMessageBox.warning(self,'草稿尚未保存','本机草稿尚未成功保存。要放弃这些修改并关闭吗？',
                                   QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,
                                   QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Discard

    def closeEvent(self,event):
        if self.dirty:
            self.save()
            if self.dirty and not self.confirm_unsaved_close():
                event.ignore(); return
        event.accept()
