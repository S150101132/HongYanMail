from PySide6.QtCore import QAbstractListModel, Qt
from coremail_desktop.mailbox.service import MailboxService

class MessageListModel(QAbstractListModel):
    SummaryRole = Qt.ItemDataRole.UserRole + 1
    def __init__(self,service=None,parent=None):
        super().__init__(parent)
        self.service = service or MailboxService()
        self.folder_id, self.filter_text = '', ''
    def rowCount(self,parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.service.messages(self.folder_id,self.filter_text))
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        items = self.service.messages(self.folder_id,self.filter_text)
        if not index.isValid() or index.row() >= len(items): return None
        item = items[index.row()]
        if role == self.SummaryRole: return item
        if role == Qt.ItemDataRole.DisplayRole: return item.subject + '\n' + item.sender + '  ' + item.date_text
        return None
    def set_folder(self,folder_id):
        self.beginResetModel(); self.folder_id = folder_id; self.endResetModel()
    def set_filter(self,text):
        self.beginResetModel(); self.filter_text = text; self.endResetModel()
    def merge_page(self,folder_id,summaries,replace=False):
        self.beginResetModel(); self.service.merge_page(folder_id,summaries,replace); self.endResetModel()
    def select_message(self,remote_id): self.service.select_message(self.folder_id,remote_id)
    def selected_message_id(self): return self.service.selected_message_id(self.folder_id)
