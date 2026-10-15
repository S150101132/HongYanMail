"""Windows current-user DPAPI. No plaintext fallback."""
import ctypes
import os
from ctypes import wintypes
from coremail_desktop.errors import AppError

class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

class DataProtector:
    def _transform(self, data, decrypt):
        if os.name != 'nt':
            raise AppError('DPAPI_UNAVAILABLE')
        buffer = ctypes.create_string_buffer(data)
        source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
            raise AppError('PROTECTION_FAILED' if not decrypt else 'UNPROTECT_FAILED')
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            kernel.LocalFree(result.pbData)
    def protect(self, plaintext: bytes) -> bytes:
        return self._transform(plaintext, False)
    def unprotect(self, ciphertext: bytes) -> bytes:
        return self._transform(ciphertext, True)
