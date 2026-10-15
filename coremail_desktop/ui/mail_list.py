from PySide6.QtCore import Qt,Signal,QAbstractListModel,QModelIndex
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QListView,QPushButton

def value(obj,name,default=''):
    if name=='message_id': name='remote_id'
    return obj.get(name,default) if isinstance(obj,dict) else getattr(obj,name,default)

class SummaryModel(QAbstractListModel):
    def __init__(self,parent=None): super().__init__(parent); self.rows=[]
    def rowCount(self,parent=QModelIndex()): return 0 if parent.isValid() else len(self.rows)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        m=self.rows[index.row()]
        if role==Qt.ItemDataRole.UserRole: return m
        if role==Qt.ItemDataRole.DisplayRole:
            sender=value(m,'sender') or value(m,'from_address') or '未知发件人'
            flags=value(m,'flags',{}); status='未读 · ' if flags.get('unread') else ''
            attachment=' · 含附件' if flags.get('attached') or flags.get('attachment') else ''
            return f"{status}{sender}    {str(value(m,'date_text'))[:16]}\n{value(m,'subject') or '（无主题）'}{attachment}\n{value(m,'snippet') or value(m,'preview')}"
    def replace(self,rows): self.beginResetModel(); self.rows=list(rows); self.endResetModel()

class MailListPane(QWidget):
    selected=Signal(object); refresh_requested=Signal(); more_requested=Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.loaded=[]; box=QVBoxLayout(self); box.setContentsMargins(14,14,14,10)
        self.title=QLabel('邮件'); self.title.setTextFormat(Qt.TextFormat.PlainText); self.title.setProperty('heading',True); box.addWidget(self.title)
        self.filter=QLineEdit(); self.filter.setPlaceholderText('筛选当前已加载邮件  Ctrl+K'); self.filter.setClearButtonEnabled(True); self.filter.textChanged.connect(self._filter); box.addWidget(self.filter)
        self.scope=QLabel(); self.scope.setProperty('muted',True); box.addWidget(self.scope)
        self.view=QListView(); self.model=SummaryModel(self); self.view.setModel(self.model); self.view.setUniformItemSizes(True); self.view.clicked.connect(lambda i:self.selected.emit(i.data(Qt.ItemDataRole.UserRole))); self.view.activated.connect(lambda i:self.selected.emit(i.data(Qt.ItemDataRole.UserRole))); box.addWidget(self.view,1)
        self.empty=QLabel('登录后读取邮件'); self.empty.setWordWrap(True); box.addWidget(self.empty)
        row=QHBoxLayout(); self.refresh=QPushButton('刷新'); self.more=QPushButton('继续加载 50 封'); self.refresh.clicked.connect(self.refresh_requested); self.more.clicked.connect(self.more_requested); row.addWidget(self.refresh); row.addWidget(self.more); box.addLayout(row); self._filter()
    def set_messages(self,rows,append=False):
        selected=self.view.currentIndex().data(Qt.ItemDataRole.UserRole); scroll=self.view.verticalScrollBar().value()
        merged=list(self.loaded) if append else []; ids={value(m,'message_id') for m in merged}
        for m in rows:
            if value(m,'message_id') not in ids: merged.append(m); ids.add(value(m,'message_id'))
        self.loaded=merged; self._filter()
        if selected:
            for n,m in enumerate(self.model.rows):
                if value(m,'message_id')==value(selected,'message_id'): self.view.setCurrentIndex(self.model.index(n)); break
        self.view.verticalScrollBar().setValue(scroll)
    def _filter(self):
        q=self.filter.text().casefold(); rows=[m for m in self.loaded if q in ' '.join(str(value(m,k)) for k in ('subject','sender','from_address','snippet','preview')).casefold()]
        self.model.replace(rows); self.scope.setText(f'筛选已加载的 {len(self.loaded)} 封 · 显示 {len(rows)} 封')
        self.empty.setText('已加载邮件中无匹配' if q and not rows else ('当前页暂无邮件' if not rows else ''))
