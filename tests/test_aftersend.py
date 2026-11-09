"""F1 发后跟进: once the server takes a reply or forward, the backend marks its 要我做 card 已回复 /
已转发, offers closing the linked tasks and the assistant's progress line, and returns them as
`after` on every path (sent now, after the undo delay, from the offline outbox). Demo adapter only."""
import unittest
from pathlib import Path

from tests import test_assistant as ta
from tests.test_today import act

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
NOTHING = {'how': '', 'answered': None, 'tasks': [], 'plan': None}


class AfterSendTest(ta.TaskCenterCase):
    plan = ta.DraftBackendTest.plan  # A draft plan for task 330 from the assistant.

    def setUp(self):
        super().setUp()
        self.mid = self.backend.messages('1', 0, 50)['rows'][0]['id']
        self.backend.task_link(330, self.mid)
        self.backend.set_understand('on')
        act(self.backend, self.mid)

    def reply(self, mode='reply'):
        value = self.backend.compose(mode, self.mid)
        value.update(sourceMid=self.mid, html='<p>好的</p>', to=value.get('to') or ['zhangxm@example.invalid'])
        return value

    def card(self):
        return self.backend.memory.card(self.mid).get('answered')

    def test_a_reply_marks_the_card_and_offers_the_task(self):
        res = self.backend.send(self.reply())
        self.assertEqual(res['state'], 'Accepted')
        self.assertEqual(res['after']['answered'], {'mid': self.mid, 'how': 'reply'})
        self.assertEqual([t['id'] for t in res['after']['tasks']], [330])
        self.assertEqual(set(res['after']['tasks'][0]), {'id', 'content'})
        self.assertIsNone(res['after']['plan'])
        self.assertEqual(self.card(), 'reply')

    def test_a_forward_marks_it_forwarded(self):
        res = self.backend.send(self.reply('forward'))
        self.assertEqual(res['after']['answered'], {'mid': self.mid, 'how': 'forward'})
        self.assertEqual(self.card(), 'forward')

    def test_after_the_undo_delay(self):
        held = self.backend.send(self.reply(), hold=True)
        self.assertEqual(held['state'], 'Held')
        self.assertNotIn('after', held)
        self.assertIsNone(self.card())  # Not sent yet: nothing marked.
        res = self.backend.release_send(held['localId'], held['revision'])
        self.assertEqual(res['after']['answered'], {'mid': self.mid, 'how': 'reply'})  # The draft remembered its source.

    def test_only_once_the_outbox_sends_it(self):
        value = self.reply()
        self.backend.adapter.offline = True
        queued = self.backend.send(value)
        self.assertEqual(queued['state'], 'Queued')
        self.assertNotIn('after', queued)
        self.assertIsNone(self.card())
        self.backend.adapter.offline = False
        res = self.backend.flush_outbox()['results'][0]
        self.assertEqual((res['state'], res['after']['answered']), ('Accepted', {'mid': self.mid, 'how': 'reply'}))
        self.assertEqual([t['id'] for t in res['after']['tasks']], [330])

    def test_understand_off_still_offers_the_task(self):
        self.backend.set_understand('off')
        res = self.backend.send(self.reply())
        self.assertIsNone(res['after']['answered'])
        self.assertEqual([t['id'] for t in res['after']['tasks']], [330])

    def test_a_new_mail_has_nothing_to_follow(self):
        value = self.backend.compose('new')
        value.update(to=['zhangxm@example.invalid'], subject='s', html='<p>b</p>')
        self.assertEqual(self.backend.send(value)['after'], NOTHING)

    def test_an_assistant_draft_offers_progress_once(self):
        plan = self.plan()
        value = self.backend.assistant_draft_compose(plan['id'], 'reply')
        self.assertEqual(value['assistPlan'], plan['id'])
        value.update(html='<p>好的</p>')
        held = self.backend.send(value, hold=True)  # Its plan survives the stored draft too.
        res = self.backend.release_send(held['localId'], held['revision'])
        self.assertEqual((res['after']['plan']['kind'], res['after']['plan']['preview'][0]['op']), ('tasks', 'progress'))
        self.assertIsNone(self.backend.assistant_sent(plan['id']))  # Offered once.


class NoTaskListTest(unittest.TestCase):
    """An account that never used 任务: a reply does not set it up."""
    def test_a_reply_leaves_tasks_alone(self):
        from coremail_desktop.app.backend import MailBackend
        from coremail_desktop.app.demo import DemoAdapter
        from tests.test_app_backend import XorProtector, local_services_stub
        from tests.test_compose_v05 import scratch
        b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        b.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(b.logout)
        mid = b.messages('1', 0, 50)['rows'][0]['id']
        value = {**b.compose('reply', mid), 'sourceMid': mid, 'html': '<p>好的</p>'}
        self.assertEqual(b.send(value)['after'], {**NOTHING, 'how': 'reply'})
        self.assertIsNone(b.tasks)
        self.assertFalse((b.data_dir / 'tasks').exists())


class PageTest(unittest.TestCase):
    """The page only shows `after`: no module wraps sendOutcome any more."""
    def test_no_wrapped_send_outcome(self):
        for name in ('tasks.js', 'understand.js', 'ai.js'):
            self.assertFalse('sendOutcome = function' in (WEB / name).read_text(encoding='utf-8'), name)
        compose = (WEB / 'compose.js').read_text(encoding='utf-8')
        self.assertTrue('showAfter(r.after)' in compose)  # Outbox results too.
        self.assertFalse('assistDrafts' in (WEB / 'ai.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
