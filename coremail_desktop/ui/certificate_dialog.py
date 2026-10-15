"""Local password dialog; slow PFX KDF runs in a background worker."""
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QFileDialog)
from coremail_desktop.crypto.models import CryptoError


class _Signals(QObject):
    success = Signal(object)
    failure = Signal(str)


class _Unlock(QRunnable):
    def __init__(self, service, path, password):
        super().__init__()
        self.service, self.path, self.password = service, path, password
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.success.emit(self.service.unlock(self.path, self.password))
        except CryptoError as error:
            self.signals.failure.emit(error.code)
        except Exception:
            self.signals.failure.emit('CERT_UNLOCK_FAILED')
        finally:
            self.password = None


class CertificateDialog(QDialog):
    certificate_unlocked = Signal(object)
    certificate_locked = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._worker = None
        self.setWindowTitle('个人邮件证书')
        self.setMinimumWidth(530)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('选择本机 PFX 证书，用于解密邮件。私钥不会上传。'))
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText('选择 .pfx / .p12 文件')
        self.choose_button = QPushButton('选择证书…')
        self.choose_button.clicked.connect(self._choose)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.choose_button)
        layout.addLayout(row)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText('证书密码（仅本次使用）')
        self.password_edit.returnPressed.connect(self._unlock)
        layout.addWidget(self.password_edit)
        self.status_label = QLabel('证书尚未解锁' if service.certificate_info is None else '证书已解锁')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QHBoxLayout()
        self.unlock_button = QPushButton('解锁证书')
        self.unlock_button.clicked.connect(self._unlock)
        self.lock_button = QPushButton('锁定并清除解密视图')
        self.lock_button.clicked.connect(self._lock)
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.unlock_button)
        buttons.addWidget(self.lock_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _choose(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择个人邮件证书', '', '个人证书 (*.pfx *.p12)')
        if path:
            self.path_edit.setText(path)

    def _busy(self, active):
        for widget in (self.choose_button, self.path_edit, self.password_edit, self.unlock_button,
                       self.lock_button, self.close_button):
            widget.setEnabled(not active)

    def _unlock(self):
        if self._worker is not None:
            return
        if not self.path_edit.text().strip():
            self.status_label.setText('请先选择证书文件。')
            return
        self._worker = _Unlock(self.service, self.path_edit.text().strip(), self.password_edit.text())
        self.password_edit.clear()
        self._worker.signals.success.connect(self._success)
        self._worker.signals.failure.connect(self._failure)
        self._busy(True)
        self.status_label.setText('正在本机解锁证书…')
        QThreadPool.globalInstance().start(self._worker)

    def _success(self, info):
        self._worker = None
        self._busy(False)
        self.status_label.setText('已解锁：' + ', '.join(info.emails) + '\n有效期至：' +
                                  info.not_after.strftime('%Y-%m-%d') +
                                  '\n仅确认持有私钥，尚未验证证书链或吊销状态。')
        self.certificate_unlocked.emit(info)

    def _failure(self, code):
        self._worker = None
        self._busy(False)
        messages = {'CERT_UNLOCK_FAILED': '无法解锁：请检查密码或证书格式。',
                    'CERT_ALGORITHM_UNSUPPORTED': '当前证书算法尚未支持，需要继续兼容研究。',
                    'CERT_PRIVATE_KEY_MISSING': '证书不包含私钥，不能用于解密。'}
        self.status_label.setText(messages.get(code, '无法读取证书：' + code))
        self.certificate_locked.emit()

    def _lock(self):
        self.service.lock()
        self.password_edit.clear()
        self.status_label.setText('证书已锁定。')
        self.certificate_locked.emit()

    def reject(self):
        if self._worker is None:
            self.password_edit.clear()
            super().reject()
