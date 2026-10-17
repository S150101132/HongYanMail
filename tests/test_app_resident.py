"""Resident-app behaviour: 开机自动启动 (Run key, mocked winreg), its preference, single instance."""
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from coremail_desktop.app import autostart
from coremail_desktop.app.autostart import Autostart, RUN_KEY, VALUE_NAME, launch_command
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from test_app_backend import XorProtector, local_services_stub


class FakeWinreg:
    """Just the winreg surface autostart uses, over a dict of {(root, path): {name: value}}."""
    HKEY_CURRENT_USER = 'HKCU'
    KEY_READ, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self, deny_writes=False):
        self.keys = {}
        self.deny_writes = deny_writes
        self.calls = []

    class _Key:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def OpenKey(self, root, path, reserved=0, access=1):
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return self._Key(self.keys[(root, path)])

    def CreateKeyEx(self, root, path, reserved=0, access=2):
        if self.deny_writes:
            raise PermissionError('policy')
        return self._Key(self.keys.setdefault((root, path), {}))

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.calls.append(('set', name, value))
        key.values[name] = value

    def DeleteValue(self, key, name):
        if self.deny_writes:
            raise PermissionError('policy')
        self.calls.append(('delete', name))
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]

    def run_values(self):
        return self.keys.get(('HKCU', RUN_KEY), {})


class LaunchCommandTest(unittest.TestCase):
    def test_packaged_exe_is_quoted_and_minimized(self):
        command = launch_command(frozen=True, executable=r'D:\Programs\Hongyan\Hongyan.exe')
        self.assertEqual(command, r'"D:\Programs\Hongyan\Hongyan.exe" --minimized')

    def test_source_checkout_uses_run_desktop_cmd(self):
        command = launch_command(frozen=False, source_root=r'D:\work\hongyan')
        self.assertEqual(command, '"%s" --minimized' % (Path(r'D:\work\hongyan') / 'run_desktop.cmd'))

    def test_default_source_root_is_the_repository(self):
        root = Path(autostart.__file__).resolve().parents[2]
        self.assertTrue((root / 'run_desktop.cmd').is_file())
        self.assertIn(str(root / 'run_desktop.cmd'), launch_command(frozen=False))


class RunKeyTest(unittest.TestCase):
    def setUp(self):
        self.reg = FakeWinreg()

    def test_enable_writes_value_under_hkcu_run(self):
        self.assertTrue(autostart.set_enabled(True, self.reg, '"C:\\x\\Hongyan.exe" --minimized'))
        self.assertEqual(self.reg.run_values(), {VALUE_NAME: '"C:\\x\\Hongyan.exe" --minimized'})
        self.assertTrue(autostart.is_enabled(self.reg))

    def test_disable_removes_only_our_value(self):
        self.reg.keys[('HKCU', RUN_KEY)] = {VALUE_NAME: 'x', 'Other': 'y'}
        self.assertFalse(autostart.set_enabled(False, self.reg))
        self.assertEqual(self.reg.run_values(), {'Other': 'y'})
        self.assertFalse(autostart.is_enabled(self.reg))

    def test_disable_when_absent_is_quiet(self):
        self.assertFalse(autostart.set_enabled(False, self.reg))
        self.reg.keys[('HKCU', RUN_KEY)] = {}
        self.assertFalse(autostart.set_enabled(False, self.reg))

    def test_denied_write_reports_the_real_state(self):
        denied = FakeWinreg(deny_writes=True)
        self.assertFalse(autostart.set_enabled(True, denied, 'cmd'))
        denied.keys[('HKCU', RUN_KEY)] = {VALUE_NAME: 'cmd'}
        self.assertTrue(autostart.set_enabled(False, denied))

    def test_refresh_repoints_a_moved_program_but_never_enables(self):
        autostart.refresh(self.reg, 'new')
        self.assertEqual(self.reg.run_values(), {})
        self.reg.keys[('HKCU', RUN_KEY)] = {VALUE_NAME: 'old'}
        autostart.refresh(self.reg, 'new')
        self.assertEqual(self.reg.run_values(), {VALUE_NAME: 'new'})
        self.reg.calls.clear()
        autostart.refresh(self.reg, 'new')
        self.assertEqual(self.reg.calls, [])


class AutostartPreferenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(),
                                   services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)
        self.reg = FakeWinreg()

    def test_default_is_off_and_demo_never_touches_the_registry(self):
        self.assertIs(self.backend.prefs()['autostart'], False)
        self.assertTrue(self.backend.set_prefs({'autostart': True})['autostart'])  # Stored only.
        self.assertEqual(self.reg.run_values(), {})

    def test_setting_writes_and_removes_the_run_key(self):
        self.backend.autostart = Autostart(self.reg, '"C:\\x\\Hongyan.exe" --minimized')
        self.assertTrue(self.backend.set_prefs({'autostart': True})['autostart'])
        self.assertEqual(self.reg.run_values()[VALUE_NAME], '"C:\\x\\Hongyan.exe" --minimized')
        self.assertTrue(self.backend.prefs()['autostart'])
        self.assertFalse(self.backend.set_prefs({'autostart': False})['autostart'])
        self.assertNotIn(VALUE_NAME, self.reg.run_values())

    def test_registry_is_the_source_of_truth(self):
        self.backend.autostart = Autostart(self.reg, 'cmd')
        self.reg.keys[('HKCU', RUN_KEY)] = {VALUE_NAME: 'cmd'}  # Enabled by another mail account.
        self.assertTrue(self.backend.prefs()['autostart'])

    def test_other_settings_leave_the_run_key_alone(self):
        self.backend.autostart = Autostart(self.reg, 'cmd')
        self.backend.set_prefs({'theme': 'dark', 'autostart': 'yes'})
        self.assertEqual(self.reg.calls, [])

    def test_policy_refusal_is_reported_back(self):
        self.backend.autostart = Autostart(FakeWinreg(deny_writes=True), 'cmd')
        self.assertFalse(self.backend.set_prefs({'autostart': True})['autostart'])


class SingleInstanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A full QApplication: later widget tests in the same run reuse this instance.
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Registered first, so it runs last: after every owner and socket is released,
        # let Qt finish deferred deletions here instead of inside a later module's event
        # loop (which crashed the full suite at random in the WebEngine reader tests).
        self.addCleanup(self._settle)

    def _settle(self):
        import gc
        from PySide6.QtCore import QCoreApplication, QEvent
        for _ in range(3):
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            gc.collect()

    def _pump(self, condition, seconds=3):
        import time
        deadline = time.monotonic() + seconds
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_second_copy_activates_the_first_and_handover_works(self):
        import subprocess
        import sys
        from coremail_desktop.app.single_instance import SingleInstance
        variant = 'test-' + uuid.uuid4().hex[:8]
        first = SingleInstance(variant)
        self.addCleanup(first.deleteLater)
        self.addCleanup(first.release)
        hits = []
        first.activated.connect(lambda: hits.append(1))
        self.assertTrue(first.acquire())
        rival = SingleInstance(variant)
        self.addCleanup(rival.deleteLater)
        self.assertFalse(rival.acquire())
        # A real second launch is another process; the owner keeps running its event loop.
        child = subprocess.Popen([sys.executable, '-c', (
            'from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]); '
            'from coremail_desktop.app.single_instance import SingleInstance; '
            's = SingleInstance(%r); print(s.acquire(), s.activate_existing(3000))' % variant)],
            cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.PIPE, text=True)
        self._pump(lambda: hits and child.poll() is not None, 20)
        self.assertEqual(child.communicate(timeout=10)[0].split(), ['False', 'True'])
        self.assertEqual(hits, [1])
        first.release()  # e.g. before the sandbox relaunch
        successor = SingleInstance(variant)
        self.addCleanup(successor.deleteLater)
        self.addCleanup(successor.release)
        self.assertTrue(successor.acquire())

    def test_only_the_fixed_message_activates(self):
        from PySide6.QtNetwork import QLocalSocket
        from coremail_desktop.app.single_instance import SingleInstance
        owner = SingleInstance('test-' + uuid.uuid4().hex[:8])
        self.addCleanup(owner.deleteLater)
        self.addCleanup(owner.release)
        hits = []
        owner.activated.connect(lambda: hits.append(1))
        self.assertTrue(owner.acquire())
        socket = QLocalSocket()
        self.addCleanup(socket.deleteLater)
        self.addCleanup(socket.abort)
        socket.connectToServer(owner.server_name)
        self.assertTrue(socket.waitForConnected(1000))
        socket.write(b'open C:\\secret.eml')
        socket.waitForBytesWritten(1000)
        self._pump(lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState, 1)
        self.assertEqual(hits, [])

    def test_variants_and_users_do_not_collide(self):
        from coremail_desktop.app.single_instance import SingleInstance
        self.assertNotEqual(SingleInstance('').server_name, SingleInstance('demo').server_name)
        self.assertNotEqual(SingleInstance('').mutex_name, SingleInstance('demo').mutex_name)


if __name__ == '__main__':
    unittest.main()
