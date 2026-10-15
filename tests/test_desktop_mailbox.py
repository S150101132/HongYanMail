import unittest
from coremail_desktop.models import MessageSummary
from coremail_desktop.mailbox.service import MailboxService

class MailboxTests(unittest.TestCase):
    def test_merge_deduplicates_preserves_selection_and_filter(self):
        service = MailboxService()
        item = lambda mid,subject:MessageSummary(mid,'0',subject,'sender','today',{})
        service.merge_page('0',[item('a','first'),item('b','second')],True)
        service.select_message('0','b')
        service.merge_page('0',[item('b','updated'),item('c','third')],False)
        self.assertEqual([x.remote_id for x in service.messages('0')],['a','b','c'])
        self.assertEqual(service.selected_message_id('0'),'b')
        self.assertEqual(service.messages('0','updated')[0].remote_id,'b')
        self.assertEqual(service.loaded_count('0'),3)
