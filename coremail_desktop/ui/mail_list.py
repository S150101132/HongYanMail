from PySide6.QtCore import Qt,Signal,QAbstractListModel,QModelIndex,QSize,QRect
from PySide6.QtGui import QPalette,QFont,QFontMetrics,QPen
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QListView,QPushButton,QStyledItemDelegate,QStyle

def value(obj,name,default=''):
    if name=='message_id': name='remote_id'
    return obj.get(name,default) if isinstance(obj,dict) else getattr(obj,name,default)

def is_mapping(value):
    """True for anything mapping-like.

    The models freeze mappings with MappingProxyType, so isinstance(value, dict) is
    False even though the value supports .get() and `in`. Testing for dict here
    silently disabled the unread marker for every real message.
    """
    return hasattr(value, 'get') and hasattr(value, '__contains__')


def is_unread(flags):
    """The server marks read messages with flags.read.

    A missing read flag means unread, so the default must be False: defaulting to
    True made every message without the key render as read.
    """
    if not is_mapping(flags):
        return False
    if 'unread' in flags:
        return bool(flags.get('unread'))
    return not flags.get('read', False)


class SummaryModel(QAbstractListModel):
    def __init__(self,parent=None): super().__init__(parent); self.rows=[]
    def rowCount(self,parent=QModelIndex()): return 0 if parent.isValid() else len(self.rows)
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid(): return None
        m=self.rows[index.row()]
        if role==Qt.ItemDataRole.UserRole: return m
        if role==Qt.ItemDataRole.DisplayRole:
            sender=value(m,'sender') or value(m,'from_address') or '未知发件人'
            flags=value(m,'flags',{})
            status='未读 · ' if is_unread(flags) else ''
            attachment=' · 含附件' if is_mapping(flags) and (flags.get('attached') or flags.get('attachment')) else ''
            if is_mapping(flags) and flags.get('flagged'): status+='星标 · '
            return f"{status}{sender}    {str(value(m,'date_text'))[:16]}\n{value(m,'subject') or '（无主题）'}{attachment}\n{value(m,'snippet') or value(m,'preview')}"
    def replace(self,rows): self.beginResetModel(); self.rows=list(rows); self.endResetModel()

class MailDelegate(QStyledItemDelegate):
    """Render bounded text rows without creating a widget for every message."""
    def sizeHint(self,option,index):
        height=QFontMetrics(option.font).height()
        return QSize(260,height*3+36)

    def paint(self,painter,option,index):
        message=index.data(Qt.ItemDataRole.UserRole)
        if message is None: return
        painter.save()
        palette=option.palette
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.fillRect(option.rect,palette.highlight() if selected else (palette.alternateBase() if hovered else palette.base()))
        ink=palette.highlightedText().color() if selected else palette.text().color()
        muted=ink if selected else palette.color(QPalette.ColorRole.PlaceholderText)
        accent=palette.highlight().color()
        rect=option.rect.adjusted(18,12,-16,-12)
        flags=value(message,'flags',{})
        unread=is_unread(flags)
        line=QFontMetrics(option.font).height()+4
        date=str(value(message,'date_text'))[:16]
        font=QFont(option.font); font.setPointSizeF(max(8,option.font.pointSizeF()-1))
        painter.setFont(font); fm=QFontMetrics(font)
        date_width=min(fm.horizontalAdvance(date),max(0,rect.width()//2))
        painter.setPen(muted)
        painter.drawText(QRect(rect.right()-date_width,rect.top(),date_width,line),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,fm.elidedText(date,Qt.TextElideMode.ElideRight,date_width))
        sender=value(message,'sender') or '未知发件人'
        font.setBold(unread); painter.setFont(font); painter.setPen(ink)
        painter.drawText(QRect(rect.left(),rect.top(),max(0,rect.width()-date_width-12),line),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(sender,Qt.TextElideMode.ElideRight,max(0,rect.width()-date_width-12)))
        font=QFont(option.font); font.setBold(unread); painter.setFont(font)
        subject=value(message,'subject') or '（无主题）'
        painter.drawText(QRect(rect.left(),rect.top()+line,rect.width(),line),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(subject,Qt.TextElideMode.ElideRight,rect.width()))
        font.setBold(False); font.setPointSizeF(max(8,font.pointSizeF()-1)); painter.setFont(font); painter.setPen(muted)
        details=('未读' if unread else '已读')+('  ·  含附件' if flags.get('attached') or flags.get('attachment') else '')
        if flags.get('flagged'): details+='  ·  星标'
        painter.drawText(QRect(rect.left(),rect.top()+line*2,rect.width(),line),Qt.AlignmentFlag.AlignVCenter,details)
        if unread:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(ink if selected else accent); painter.drawEllipse(option.rect.left()+6,rect.top()+line+6,5,5)
        painter.setPen(QPen(palette.mid().color(),1)); painter.drawLine(option.rect.bottomLeft(),option.rect.bottomRight())
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(ink if selected else accent,2)); painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawRect(option.rect.adjusted(1,1,-2,-2))
        painter.restore()

class MailListPane(QWidget):
    selected=Signal(object); refresh_requested=Signal(); more_requested=Signal(); search_requested=Signal(); search_closed=Signal()
    def __init__(self,parent=None):
        super().__init__(parent); self.loaded=[]; box=QVBoxLayout(self); box.setContentsMargins(14,14,14,10)
        self.title=QLabel('邮件'); self.title.setTextFormat(Qt.TextFormat.PlainText); self.title.setProperty('heading',True); box.addWidget(self.title)
        search_row=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('搜索邮箱（不含废纸篓）'); self.search.setAccessibleName('服务器邮箱搜索，不含废纸篓'); self.search.setToolTip('使用服务器默认搜索范围；废纸篓请进入对应文件夹查看或筛选。'); self.search.setClearButtonEnabled(True); self.search.returnPressed.connect(self.search_requested); search_row.addWidget(self.search,1)
        self.search_button=QPushButton('搜索'); self.search_button.clicked.connect(self.search_requested); search_row.addWidget(self.search_button); box.addLayout(search_row)
        self.search_back=QPushButton('返回文件夹'); self.search_back.clicked.connect(self.search_closed); self.search_back.hide(); box.addWidget(self.search_back)
        self.filter=QLineEdit(); self.filter.setPlaceholderText('筛选当前已加载邮件  Ctrl+K'); self.filter.setClearButtonEnabled(True); self.filter.textChanged.connect(self._filter); box.addWidget(self.filter)
        scope_row=QHBoxLayout(); self.scope=QLabel(); self.scope.setProperty('muted',True); scope_row.addWidget(self.scope,1)
        self.unread_only=QPushButton('仅未读'); self.unread_only.setCheckable(True); self.unread_only.setAccessibleName('仅筛选已加载的未读邮件'); self.unread_only.toggled.connect(self._filter); scope_row.addWidget(self.unread_only); box.addLayout(scope_row)
        self.view=QListView(); self.view.setObjectName('mailList'); self.view.setAccessibleName('邮件列表'); self.view.setMouseTracking(True); self.view.setItemDelegate(MailDelegate(self.view)); self.model=SummaryModel(self); self.view.setModel(self.model); self.view.setUniformItemSizes(True); self.view.clicked.connect(lambda i:self.selected.emit(i.data(Qt.ItemDataRole.UserRole))); self.view.activated.connect(lambda i:self.selected.emit(i.data(Qt.ItemDataRole.UserRole))); box.addWidget(self.view,1)
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
    def update_message_flags(self,message_id,flags):
        from dataclasses import replace
        rows=[]
        for message in self.loaded:
            if value(message,'remote_id')==message_id:
                updated={**dict(value(message,'flags',{})),**flags}
                message={**message,'flags':updated} if isinstance(message,dict) else replace(message,flags=updated)
            rows.append(message)
        self.set_messages(rows)
    def _filter(self):
        q=self.filter.text().casefold(); rows=[m for m in self.loaded if (not self.unread_only.isChecked() or is_unread(value(m,'flags',{}))) and q in ' '.join(str(value(m,k)) for k in ('subject','sender','from_address','snippet','preview')).casefold()]
        self.model.replace(rows); self.scope.setText(f'筛选已加载的 {len(self.loaded)} 封 · 显示 {len(rows)} 封')
        self.empty.setText('已加载邮件中无匹配' if (q or self.unread_only.isChecked()) and not rows else ('当前页暂无邮件' if not rows else ''))
