"""任务关系与甘特图 (P1): 上级 / 依赖 #N read from 阻塞与依赖, loops refused, the parent's
roll-up, the Gantt rows and 解除受阻 after a predecessor is done."""
import unittest

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.tasks import plan
from tests.test_app_backend import XorProtector
from tests.test_compose_v05 import scratch
from tests.test_tasks import Clock, folder_with, task_row


def tasks_of(*rows):
    return {r['id']: r for r in rows}


class RelationTest(unittest.TestCase):
    def test_parsed_from_the_blockers_text(self):
        self.assertEqual(plan.links(task_row(5, blockers='上级 #2；依赖 #3、#4 和 #5，另见 #9')), (2, [3, 4]))
        self.assertEqual(plan.links(task_row(5, blockers='前置：#7\n属于#1')), (1, [7]))
        tasks = tasks_of(task_row(1), task_row(2, blockers='属于 #1'), task_row(3, '已完成', blockers='上级 #1'),
                         task_row(4, '已取消', blockers='上级 #1'), task_row(5, '受阻', blockers='上级 #2 依赖 #3 依赖 #99'))
        rel = plan.relations(tasks)
        self.assertEqual((rel[1]['children'], rel[3]['next'], rel[5]['missing']), ([2, 3, 4], [5], [99]))
        self.assertEqual(plan.rollup(tasks, rel, 1), {'done': 1, 'total': 3})  # 2, 3 and 5 below it; 4 is canceled.
        view = plan.view(tasks, 5)
        self.assertEqual((view['parent']['id'], [d['id'] for d in view['deps']], view['waiting'], view['rollup']), (2, [3], [], None))

    def test_loops_are_refused(self):
        tasks = tasks_of(task_row(1, blockers='依赖 #2'), task_row(2, blockers='上级 #3'), task_row(3))
        self.assertTrue(plan.cycle(tasks, 2, '依赖 #1'))
        self.assertTrue(plan.cycle(tasks, 3, '属于 #2'))
        self.assertFalse(plan.cycle(tasks, 3, '依赖 #1'))
        with self.assertRaises(AppError) as raised:
            plan.check(tasks, 2, '依赖 #1')
        self.assertEqual(raised.exception.code, 'TASK_RELATION_CYCLE')

    def test_unblocked_when_every_predecessor_is_done(self):
        tasks = tasks_of(task_row(1, '已完成'), task_row(2, '进行中'), task_row(3, '受阻', blockers='依赖 #1'),
                         task_row(4, '受阻', blockers='依赖 #1 依赖 #2'), task_row(5, '进行中', blockers='依赖 #1'))
        self.assertEqual([u['id'] for u in plan.unblocked(tasks, [1])], [3])

    def test_gantt_rows(self):
        tasks = tasks_of(task_row(1, '进行中', start='2026-10-01', due='2026-10-30'),
                         task_row(2, '进行中', start='2026-10-05', due='2026-10-10', blockers='上级 #1'),
                         task_row(3, '未开始', due='2026-10-25', blockers='依赖 #2 上级 #1'),
                         task_row(4, '已完成', start='2026-09-20', end='2026-09-25'), task_row(5))
        g = plan.gantt(tasks, '2026-10-20')
        self.assertEqual([(r['id'], r['depth']) for r in g['rows']], [(4, 0), (1, 0), (2, 1), (3, 1)])
        row = {r['id']: r for r in g['rows']}
        self.assertEqual((row[2]['late'], row[3]['start'], row[3]['deps'], row[3]['waiting'], row[1]['rollup']),
                         (True, '2026-10-25', [2], True, {'done': 0, 'total': 2}))
        self.assertEqual((g['from'], g['to']), ('2026-09-20', '2026-10-30'))
        self.assertEqual([r['id'] for r in plan.gantt(tasks, '2026-10-20', {2, 3})['rows']], [2, 3])  # Only the listed ones.


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.folder = folder_with(self, open_rows=[task_row(302, '进行中', start='2026-10-01', due='2026-10-30'),
                                                   task_row(330, '受阻', blockers='依赖 #302'), task_row(333, blockers='上级 #302')])
        self.backend = b = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector())
        self.addCleanup(lambda: b.tasks and b.tasks.close())
        b.tasks_set_folder(str(self.folder))
        b.tasks_import('live')
        b.tasks.clock = Clock()

    def test_relations_gantt_loop_check_and_unblock(self):
        b = self.backend
        rel = b.task_get(302)['relations']
        self.assertEqual(([c['id'] for c in rel['children']], [n['id'] for n in rel['next']], rel['rollup']), ([333], [330], {'done': 0, 'total': 1}))
        self.assertEqual([r['id'] for r in b.tasks_gantt()['rows']], [302])
        with self.assertRaises(AppError) as raised:
            b.task_update(302, b.task_get(302)['rev'], {'blockers': '依赖 #330'})
        self.assertEqual(raised.exception.code, 'TASK_RELATION_CYCLE')
        result = b.task_complete(302, b.task_get(302)['rev'])
        self.assertEqual([(u['id'], u['rev']) for u in result['unblocked']], [(330, b.task_get(330)['rev'])])


if __name__ == '__main__':
    unittest.main()
