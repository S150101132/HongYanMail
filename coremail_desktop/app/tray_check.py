"""Whether Windows really shows 鸿雁's tray icon.

Qt reports a tray icon as visible once it asked for one, even when Windows refused it
(security software on some company PCs blocks Shell_NotifyIcon for unknown programs).
Windows 11 records every notification-area icon it accepted under
HKCU\\Control Panel\\NotifyIconSettings with the program's path, so a missing entry
means the icon is not there. Read-only; nothing is written to the registry.
"""
import os
import sys

_KEY = r'Control Panel\NotifyIconSettings'


def _same_program(recorded, exe):
    recorded = os.path.normcase(str(recorded or ''))
    if recorded.startswith('{') and '}' in recorded:
        # Known-folder form: '{GUID}\relative\path.exe' (Program Files and similar).
        return exe.endswith(os.path.normcase(recorded.split('}', 1)[1]))
    return recorded == exe


def tray_registered(executable=None):
    """True or False on Windows 11; None where Windows keeps no such record."""
    if sys.platform != 'win32':
        return None
    import winreg
    exe = os.path.normcase(os.path.abspath(executable or sys.executable))
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY)
    except OSError:
        return None
    with root:
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                return False
            index += 1
            try:
                with winreg.OpenKey(root, name) as key:
                    if _same_program(winreg.QueryValueEx(key, 'ExecutablePath')[0], exe):
                        return True
            except OSError:
                continue
