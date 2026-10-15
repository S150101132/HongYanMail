import threading
from PySide6.QtCore import QObject, Signal

class AutoSaveController(QObject):
    saved = Signal(int)
    failed = Signal(object)
    def __init__(self,store,delay_seconds=1.0,parent=None):
        super().__init__(parent)
        self.store, self.delay = store,delay_seconds
        self._lock = threading.RLock()
        self._pending = None
        self._timer = None
    def update(self,draft):
        with self._lock:
            if self._timer: self._timer.cancel()
            self._pending = draft
            self._timer = threading.Timer(self.delay,self._background_flush)
            self._timer.daemon = True
            self._timer.start()
    def _background_flush(self):
        try: self.flush()
        except Exception as error: self.failed.emit(error)
    def flush(self):
        with self._lock:
            if self._timer: self._timer.cancel(); self._timer = None
            if self._pending is None: return -1
            draft = self._pending
            revision = self.store.save(draft)
            self._pending = None
            self.saved.emit(revision)
            return revision
    def restore(self,local_id): return self.store.load(local_id)
    def close(self):
        return self.flush()
