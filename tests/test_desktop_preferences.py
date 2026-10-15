import tempfile
import unittest
from coremail_desktop.storage.preferences import Preferences
class PreferenceTests(unittest.TestCase):
    def test_preferences_allow_only_nonmail_ui_state(self):
        with tempfile.TemporaryDirectory() as folder:
            prefs = Preferences(folder)
            prefs.set('window_width',1280)
            self.assertEqual(Preferences(folder).get('window_width'),1280)
            with self.assertRaises(ValueError): prefs.set('password','secret')
