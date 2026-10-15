import time
import unittest
from coremail_desktop.compose.service import ComposeService
from coremail_desktop.drafts.autosave import AutoSaveController
from dataclasses import replace

class AutosaveTests(unittest.TestCase):
    def test_failed_save_keeps_latest_draft_for_retry(self):
        class Store:
            fail = True
            def save(self,draft):
                if self.fail: raise OSError('disk')
                self.draft = draft
                return draft.revision
        store = Store()
        auto = AutoSaveController(store,delay_seconds=60)
        draft = replace(ComposeService.new_draft('me@example.test'),body='editable text',revision=2)
        auto.update(draft)
        with self.assertRaises(OSError): auto.flush()
        store.fail = False
        self.assertEqual(auto.flush(),2)
        self.assertEqual(store.draft.body,'editable text')
        auto.close()
    def test_debounce_flush_latest_and_failure_retains_text(self):
        class Store:
            saved = []
            def save(self,draft): self.saved.append(draft); return draft.revision
            def load(self,local_id): return self.saved[-1]
        store = Store()
        auto = AutoSaveController(store,delay_seconds=.04)
        draft = ComposeService.new_draft('me@example.test')
        auto.update(draft)
        auto.update(replace(draft,revision=1,body='latest'))
        time.sleep(.08)
        self.assertEqual(len(store.saved),1)
        self.assertEqual(store.saved[0].body,'latest')
        auto.update(replace(draft,revision=2,body='flush'))
        self.assertEqual(auto.flush(),2)
        auto.close()
