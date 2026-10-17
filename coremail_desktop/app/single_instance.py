"""One running Hongyan per user session.

A named mutex decides ownership (no race between two quick launches); a local
socket lets a second launch ask the owner to show its window. The socket only
accepts the fixed message b'activate' and carries no data. Both names are
per-user: the mutex lives in the session namespace, and the socket name carries
a hash of the Windows account and is restricted to that account.
"""
import hashlib
import os
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

MUTEX_NAME = 'Hongyan.SingleInstance'  # Also named in packaging/hongyan.iss (AppMutex).
MESSAGE = b'activate'
_ERROR_ALREADY_EXISTS = 183


def _user_tag():
    who = '%s\\%s' % (os.environ.get('USERDOMAIN', ''), os.environ.get('USERNAME', '') or os.getlogin())
    return hashlib.sha256(who.casefold().encode('utf-8')).hexdigest()[:16]


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, variant='', parent=None):
        super().__init__(parent)
        suffix = ('.' + variant) if variant else ''
        self.mutex_name = MUTEX_NAME + suffix
        self.server_name = 'hongyan-%s%s' % (_user_tag(), suffix)
        self._mutex = None
        self._server = None

    def acquire(self):
        """True when this process is the only one; it then listens for activations."""
        if sys.platform == 'win32':
            import ctypes
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, False, self.mutex_name)
            if handle and ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
            self._mutex = handle
        self.listen()
        return True

    def listen(self):
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.setMaxPendingConnections(4)
        server.newConnection.connect(self._accept)
        if not server.listen(self.server_name):
            QLocalServer.removeServer(self.server_name)  # Stale socket file (non-Windows).
            server.listen(self.server_name)
        self._server = server

    def _accept(self):
        while self._server is not None and self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            buffer = bytearray()

            def read(socket=socket, buffer=buffer):
                buffer += bytes(socket.read(64))
                if len(buffer) >= len(MESSAGE) or len(buffer) > 64:
                    if bytes(buffer[:len(MESSAGE) + 1]).strip() == MESSAGE:
                        self.activated.emit()
                    socket.disconnectFromServer()

            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                read()

    def activate_existing(self, timeout_ms=3000):
        """Ask the running instance to show itself. False if nobody answered."""
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY: let the owner take focus.
        import time
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            socket = QLocalSocket()
            socket.connectToServer(self.server_name)
            if socket.waitForConnected(300):
                socket.write(MESSAGE)
                socket.flush()
                if socket.bytesToWrite():
                    socket.waitForBytesWritten(1000)
                sent = not socket.bytesToWrite()
                socket.disconnectFromServer()
                return sent
            if time.monotonic() >= deadline:  # The owner may still be starting up.
                return False
            time.sleep(0.2)

    def release(self):
        """Give up ownership, e.g. right before handing over to a relaunched copy."""
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._mutex:
            import ctypes
            ctypes.WinDLL('kernel32').CloseHandle(ctypes.c_void_p(self._mutex))
            self._mutex = None
