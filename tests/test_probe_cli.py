import io
import json
import tempfile
import unittest
import warnings
import getpass
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from probe.cli import main
from probe.coremail_transport import LoginChallenge


class ProbeCliTests(unittest.TestCase):
    def test_no_echo_failure_stops_before_reading_or_submitting_credentials(self):
        def unavailable(*args):
            warnings.warn('Echo cannot be disabled', getpass.GetPassWarning)
            raise AssertionError('Fallback must never read an echoed password')
        with tempfile.TemporaryDirectory() as directory:
            with patch('probe.cli.CoremailSession') as factory, patch('probe.cli.getpass.getpass', side_effect=unavailable):
                with redirect_stdout(io.StringIO()):
                    code = main(['--report', str(Path(directory)/'result.json')])
                self.assertEqual(code, 2)
                factory.return_value.login.assert_not_called()
                self.assertEqual(json.loads((Path(directory)/'result.json').read_text())['error'], 'PASSWORD_INPUT_UNAVAILABLE')

    def test_auth_challenge_is_reported_without_password_or_remote_details(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'result.json'
            output = io.StringIO()
            with patch('probe.cli.CoremailSession') as factory, patch('probe.cli.getpass.getpass', return_value='secret-never-print'):
                factory.return_value.login.side_effect = LoginChallenge()
                with redirect_stdout(output):
                    code = main(['--account', 'wangbiao@inspur.com', '--report', str(report)])
            self.assertEqual(code, 2)
            self.assertNotIn('secret-never-print', output.getvalue() + report.read_text())
            self.assertEqual(json.loads(report.read_text())['error'], 'AUTH_INTERACTION_REQUIRED')
            factory.return_value.close.assert_called_once()

    def test_default_does_not_send_mail(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('probe.cli.CoremailSession'), patch('probe.cli.getpass.getpass', return_value='secret'), patch('probe.cli.verify_read', return_value={'read': {'status': 'passed'}}), patch('probe.cli.verify_roundtrip') as send:
                with redirect_stdout(io.StringIO()):
                    code = main(['--account', 'wangbiao@inspur.com', '--report', str(Path(directory)/'result.json')])
                self.assertEqual(code, 0)
                send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
