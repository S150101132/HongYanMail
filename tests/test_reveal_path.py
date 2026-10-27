import os
import unittest
from pathlib import Path
from unittest import mock

from coremail_desktop.app import backend


class RevealPathTest(unittest.TestCase):
    def test_folder_opens_file_is_selected_missing_is_refused(self):
        here = Path(__file__).resolve()
        with mock.patch('subprocess.Popen') as popen:
            self.assertTrue(backend.shell_reveal(str(here.parent)))
            self.assertTrue(backend.shell_reveal(str(here)))
            self.assertFalse(backend.shell_reveal(str(here.parent / 'no-such-file.pst')))
            self.assertFalse(backend.shell_reveal(''))
        calls = [c.args[0] for c in popen.call_args_list]
        self.assertEqual(len(calls), 2)
        if os.name == 'nt':
            self.assertEqual(calls[0][1:], [str(here.parent)])
            self.assertEqual(calls[1][1:], ['/select,' + str(here)])


if __name__ == '__main__':
    unittest.main()
