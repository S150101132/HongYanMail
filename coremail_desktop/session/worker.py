from PySide6.QtCore import QObject, Signal, Slot
from coremail_desktop.errors import AppError
from coremail_desktop.crypto.models import CryptoError

CRYPTO_ERROR_CODES = frozenset({'CERT_LOCKED','CERT_UNLOCK_FAILED','CERT_FILE_UNAVAILABLE','CERT_FILE_TOO_LARGE','CERT_FORMAT_UNSUPPORTED','CERT_KEY_MISMATCH','CERT_PRIVATE_KEY_MISSING','CERT_ALGORITHM_UNSUPPORTED','DECRYPT_FAILED','ENCRYPT_FAILED','INVALID_ENCRYPTED_MESSAGE','MESSAGE_TOO_LARGE','RECIPIENT_CERT_EXPIRED','RECIPIENT_CERT_MISSING','RECIPIENT_CERT_UNTRUSTED','RECIPIENT_CERT_USAGE','UNSUPPORTED_ENCRYPTION_FORMAT','NESTED_CRYPTO_UNSUPPORTED'})

class SessionWorker(QObject):
    completed = Signal(str,int,object)
    failed = Signal(str,int,object)
    auth_required = Signal()
    ADAPTER_OPERATIONS = frozenset({'login','list_folders','list_messages','fetch_mime','get_account_identities'})
    def __init__(self,adapter_factory,services=None):
        super().__init__()
        self.factory, self.service_factory = adapter_factory,services
        self.adapter = None
        self.services = {}
        self._auth_prompted = False
    def _initialize(self):
        if self.adapter is None:
            self.adapter = self.factory()
            self.services = self.service_factory(self.adapter) if callable(self.service_factory) else (self.service_factory or {})
    @Slot(str,str,object,int)
    def execute(self,task_id,operation,params,generation):
        try:
            self._initialize()
            if operation in self.ADAPTER_OPERATIONS:
                result = getattr(self.adapter,operation)(**params)
            elif operation in self.services:
                result = self.services[operation](**params)
            else: raise AppError('OPERATION_NOT_ALLOWED')
            if operation == 'login': self._auth_prompted = False
        except Exception as exception:
            code = exception.code if isinstance(exception,AppError) else 'INTERNAL_ERROR'
            if isinstance(exception,CryptoError) and exception.code in CRYPTO_ERROR_CODES: code = exception.code
            error = AppError(code,task_id,getattr(exception,'category','') if isinstance(exception,AppError) else '')
            if code in ('NOT_AUTHENTICATED','SESSION_EXPIRED') and not self._auth_prompted:
                self._auth_prompted = True
                self.auth_required.emit()
            self.failed.emit(task_id,generation,error)
        else:
            self.completed.emit(task_id,generation,result)
    @Slot()
    def shutdown(self):
        if self.adapter is not None: self.adapter.close()
