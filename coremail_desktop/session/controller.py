import uuid
from PySide6.QtCore import QObject, QThread, Signal, Qt, QMetaObject
from coremail_desktop.protocol.coremail import CoremailAdapter
from coremail_desktop.session.worker import SessionWorker
from coremail_desktop.errors import AppError

class SessionController(QObject):
    completed = Signal(str,int,object)
    failed = Signal(str,int,object)
    auth_required = Signal()
    _dispatch = Signal(str,str,object,int)
    def __init__(self,adapter_factory=CoremailAdapter,services=None,parent=None):
        super().__init__(parent)
        self._closed = False
        self._thread = QThread(self)
        self._worker = SessionWorker(adapter_factory,services)
        self._worker.moveToThread(self._thread)
        self._dispatch.connect(self._worker.execute,Qt.ConnectionType.QueuedConnection)
        self._worker.completed.connect(self.completed)
        self._worker.failed.connect(self.failed)
        self._worker.auth_required.connect(self.auth_required)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()
    def submit(self,operation,params,view_generation):
        if self._closed: raise AppError('SESSION_CLOSED')
        task_id = uuid.uuid4().hex
        self._dispatch.emit(task_id,operation,dict(params),view_generation)
        return task_id
    def close(self):
        if self._closed: return
        self._closed = True
        # Queued requests finish before shutdown; session is closed on owner thread.
        QMetaObject.invokeMethod(self._worker,'shutdown',Qt.ConnectionType.BlockingQueuedConnection)
        self._thread.quit()
        self._thread.wait()
