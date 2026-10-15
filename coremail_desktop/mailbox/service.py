"""Loaded pages only; keeps independent selection per folder."""
class MailboxService:
    def __init__(self):
        self._folders, self._selection = {}, {}
    def merge_page(self,folder_id,summaries,replace=False):
        if any(item.folder_id != folder_id for item in summaries): raise ValueError('Folder mismatch')
        values = {} if replace else dict(self._folders.get(folder_id,{}))
        for item in summaries: values[item.remote_id] = item
        self._folders[folder_id] = values
        if self._selection.get(folder_id) not in values: self._selection.pop(folder_id,None)
    def messages(self,folder_id,text=''):
        items = list(self._folders.get(folder_id,{}).values())
        query = text.casefold().strip()
        return [item for item in items if query in (item.subject+' '+item.sender).casefold()] if query else items
    def loaded_count(self,folder_id): return len(self._folders.get(folder_id,{}))
    def select_message(self,folder_id,remote_id):
        if remote_id not in self._folders.get(folder_id,{}): raise ValueError('Message not loaded')
        self._selection[folder_id] = remote_id
    def selected_message_id(self,folder_id): return self._selection.get(folder_id)
