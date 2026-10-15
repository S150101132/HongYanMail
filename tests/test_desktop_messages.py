"""Every error code the application can raise must have a fixed, safe message."""
import re
import unittest
from pathlib import Path

from coremail_desktop.errors import AppError
from coremail_desktop.ui.messages import GENERIC, MESSAGES, user_message

PACKAGE = Path(__file__).resolve().parent.parent / 'coremail_desktop'
RAISED = re.compile(r"(?:AppError|CryptoError)\('([A-Z][A-Z0-9_]+)'")


def raised_codes():
    codes = set()
    for path in PACKAGE.rglob('*.py'):
        codes.update(RAISED.findall(path.read_text(encoding='utf-8')))
    return codes


class MessageTests(unittest.TestCase):
    def test_every_raised_code_has_a_specific_message(self):
        missing = sorted(code for code in raised_codes() if code not in MESSAGES)
        self.assertEqual(missing, [], f'codes without a fixed message: {missing}')
        for code in sorted(raised_codes()):
            message = user_message(code)
            self.assertNotEqual(message, GENERIC, f'{code} fell back to the generic message')
            self.assertTrue(message.endswith(('。', '！', '？')), message)

    def test_unknown_and_non_string_codes_use_one_safe_fallback(self):
        self.assertEqual(user_message('NOT_A_REAL_CODE'), GENERIC)
        self.assertEqual(user_message(None), GENERIC)
        self.assertEqual(user_message(42), GENERIC)
        self.assertEqual(user_message(''), GENERIC)

    def test_error_object_text_never_reaches_the_displayed_message(self):
        error = AppError('AUTH_REJECTED', 'op-1',
                         'password=secret-password sid=secret-sid https://newmail.inspur.com/coremail/x.jsp')
        displayed = user_message(getattr(error, 'code', 'INTERNAL_ERROR'))
        for secret in ('secret-password', 'secret-sid', 'newmail.inspur.com', 'coremail'):
            self.assertNotIn(secret, displayed)
        self.assertNotIn('op-1', displayed)

    def test_internal_error_code_is_also_safe(self):
        self.assertEqual(user_message('INTERNAL_ERROR'), GENERIC)


class ErrorModelTests(unittest.TestCase):
    def test_app_error_carries_only_a_code_and_operation(self):
        error = AppError('NETWORK_ERROR', 'op-9', 'category')
        self.assertEqual(error.code, 'NETWORK_ERROR')
        self.assertEqual(error.operation_id, 'op-9')
        self.assertNotIn('password', str(error).lower())
        self.assertNotIn('http', str(error).lower())


if __name__ == '__main__':
    unittest.main()
