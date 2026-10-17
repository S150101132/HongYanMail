"""“开机自动启动”: the per-user Run key, the only place Windows reads it from.

The registry is the source of truth, so the setting follows the Windows account
rather than the mail account. Qt-free; winreg is injectable for tests.
"""
import sys
from pathlib import Path

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'Hongyan'  # Also removed by packaging/hongyan.iss on uninstall.


def _winreg(module):
    if module is not None:
        return module
    import winreg
    return winreg


def launch_command(frozen=None, executable=None, source_root=None):
    """The packaged exe, or run_desktop.cmd when running from source; always minimized."""
    frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    if frozen:
        target = Path(executable or sys.executable)
    else:
        target = Path(source_root or Path(__file__).resolve().parents[2]) / 'run_desktop.cmd'
    return '"%s" --minimized' % target


def current_value(winreg=None):
    winreg = _winreg(winreg)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except OSError:
        return None


def is_enabled(winreg=None):
    return current_value(winreg) is not None


def set_enabled(enabled, winreg=None, command=None):
    """Write or remove the Run value. Returns the resulting state."""
    winreg = _winreg(winreg)
    try:
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command or launch_command())
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass  # Nothing to remove.
    except OSError:
        return is_enabled(winreg)  # Policy may forbid the write; report what is really there.
    return bool(enabled)


def refresh(winreg=None, command=None):
    """Keep an enabled entry pointing at this copy (the program may have moved)."""
    command = command or launch_command()
    value = current_value(winreg)
    if value is not None and value != command:
        set_enabled(True, winreg, command)


class Autostart:
    """What the backend talks to; None in demo mode, so demos never touch the registry."""

    def __init__(self, winreg=None, command=None):
        self._winreg = winreg
        self._command = command

    def is_enabled(self):
        return is_enabled(self._winreg)

    def set_enabled(self, enabled):
        return set_enabled(enabled, self._winreg, self._command)

    def refresh(self):
        refresh(self._winreg, self._command)
