from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QFormLayout,QLabel,QLineEdit,QPushButton

class LoginPane(QWidget):
    requested=Signal(dict)
    def __init__(self,parent=None):
        super().__init__(parent)
        outer=QVBoxLayout(self); outer.addStretch()
        card=QWidget(); card.setMaximumWidth(480)
        box=QVBoxLayout(card); title=QLabel('登录工作邮箱'); title.setProperty('heading',True); box.addWidget(title)
        note=QLabel('独立连接公司 Coremail · 密码仅用于本次登录'); note.setProperty('muted',True); box.addWidget(note)
        form=QFormLayout(); self.server=QLineEdit('https://newmail.inspur.com/coremail/'); self.account=QLineEdit(); self.password=QLineEdit(); self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.account.setPlaceholderText('邮箱账号'); self.password.setPlaceholderText('请输入邮箱密码')
        for label,field in [('服务器',self.server),('账号',self.account),('密码',self.password)]: form.addRow(label,field)
        box.addLayout(form); self.error=QLabel(''); self.error.setWordWrap(True); box.addWidget(self.error)
        self.button=QPushButton('登录'); self.button.setProperty('primary',True); self.button.clicked.connect(self._request); self.password.returnPressed.connect(self._request); box.addWidget(self.button)
        outer.addWidget(card,0,__import__('PySide6.QtCore',fromlist=['Qt']).Qt.AlignmentFlag.AlignHCenter); outer.addStretch()
    def _request(self):
        if not self.account.text().strip() or not self.password.text(): self.error.setText('请填写账号和密码。'); return
        self.button.setEnabled(False); self.error.setText('正在建立独立会话…')
        self.requested.emit({'base_url':self.server.text().strip(),'username':self.account.text().strip(),'password':self.password.text()}); self.password.clear()
    def set_error(self,text): self.button.setEnabled(True); self.error.setText(text)
