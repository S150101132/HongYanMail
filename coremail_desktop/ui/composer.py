from dataclasses import replace
from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QKeySequence,QShortcut
from PySide6.QtWidgets import QDialog,QVBoxLayout,QFormLayout,QHBoxLayout,QLineEdit,QPlainTextEdit,QLabel,QPushButton,QCheckBox,QMessageBox

class Composer(QDialog):
    send_requested=Signal(object); save_requested=Signal(object)
    def __init__(self,draft,autosave=None,parent=None):
        super().__init__(parent); self.draft=draft; self.autosave=autosave; self.dirty=False; self.sending=False
        self.setWindowTitle('写邮件 · 纯文本'); self.resize(760,680); box=QVBoxLayout(self); form=QFormLayout()
        self.sender=QLineEdit(draft.from_identity); self.sender.setPlaceholderText('请明确填写完整发件邮箱地址')
        self.to=QLineEdit(', '.join(draft.to)); self.cc=QLineEdit(', '.join(draft.cc)); self.bcc=QLineEdit(', '.join(draft.bcc)); self.subject=QLineEdit(draft.subject)
        for label,field in [('发件人',self.sender),('收件人',self.to),('抄送',self.cc),('密送',self.bcc),('主题',self.subject)]: form.addRow(label,field)
        box.addLayout(form); self.body=QPlainTextEdit(draft.body); self.body.setPlaceholderText('输入纯文本正文…'); box.addWidget(self.body,1)
        self.encryption=QCheckBox('要求证书加密（缺少证书时阻止发送）'); self.encryption.setChecked(draft.encryption_required); box.addWidget(self.encryption)
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
    def request_send(self):
        if self.sending: return
        draft=self.snapshot()
        if draft.encryption_required:
            self.state_label.setText('加密发送接口尚未完成真实互通验证，已阻止发送。草稿会保留加密要求。'); self.save(); return
        try:
            from ..compose.recipients import normalize_recipients
            normalize_recipients((draft.from_identity,)); normalize_recipients(draft.to+draft.cc+draft.bcc)
        except Exception: self.state_label.setText('请填写有效的完整发件人和收件人邮箱地址。'); return
        if not draft.to and not draft.cc and not draft.bcc: self.state_label.setText('请填写至少一个收件人。'); return
        if not draft.subject.strip() and QMessageBox.question(self,'空主题','主题为空，仍要发送？')!=QMessageBox.StandardButton.Yes: return
        self.save(); self.sending=True; self.set_send_state('Preparing'); self.send_requested.emit(draft)
    def set_send_state(self,state):
        state=getattr(state,'value',state); self.sending=state in ('Preparing','Submitting','Unknown','Reconciling','Accepted')
        self.send_button.setEnabled(not self.sending)
        for field in (self.sender,self.to,self.cc,self.bcc,self.subject,self.body): field.setReadOnly(self.sending)
        self.encryption.setEnabled(not self.sending)
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
