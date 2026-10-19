"""A department with sub-departments lists everyone beneath it; empty departments say so.

The server lists only a department's own people and many departments hold nobody
directly (analysis/contacts-subtree-shape{,2}-2026-10-18.json)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from test_app_backend import XorProtector, local_services_stub

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


class SubtreeTests(unittest.TestCase):
    def make(self, tree=True):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        backend = MailBackend(directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        if tree:
            backend.directory()  # The view always opens the tree first.
        return backend

    def test_parent_without_own_people_lists_its_leaves(self):
        page = self.make().members('d/sales')
        self.assertEqual((page['direct'], page['departments'], page['total'], page['partial']), (0, 3, 5, False))
        self.assertEqual([(r['name'], r['unit']) for r in page['rows']],
                         [('马超', '华北区'), ('黄蕾', '华北区'), ('林峰', '华南区'), ('何静', '华南区'), ('郭明', '华南区')])

    def test_own_people_come_first_then_every_level_below(self):
        rows = self.make().members('d/rd')['rows']
        self.assertEqual((rows[0]['name'], rows[0].get('unit')), ('研发团队', None))
        self.assertEqual({r['unit'] for r in rows[1:]}, {'前端组', '后端组'})
        self.assertEqual(len(rows), 7)
        root = self.make().members('d')  # The whole group is searched, not gathered.
        self.assertEqual((root['departments'], root['rows']), (0, []))

    def test_leaf_and_empty_leaf(self):
        backend = self.make()
        leaf = backend.members('d/rd-fe')
        self.assertEqual((leaf['departments'], len(leaf['rows'])), (0, 3))
        self.assertFalse(any('unit' in r for r in leaf['rows']))
        empty = backend.members('d/sales-w')
        self.assertEqual((empty['rows'], empty['total'], empty['departments']), ([], 0, 0))

    def test_without_the_tree_only_direct_people_show(self):
        page = self.make(tree=False).members('d/sales')
        self.assertEqual((page['rows'], page['departments']), ([], 0))

    def test_gathered_listings_come_from_the_cache(self):
        backend = self.make()
        first = backend.members('d/sales')
        self.assertFalse(first['cached'])
        backend.adapter.directory_members = lambda dn: self.fail('cached listings need no network call')
        again = backend.members('d/sales')
        self.assertTrue(again['cached'])
        self.assertEqual(again['rows'], first['rows'])

    def test_a_failing_sub_department_marks_the_list_partial(self):
        backend = self.make()
        real = backend.adapter.directory_members

        def flaky(dn):
            if dn == 'd/sales-n':
                raise AppError('NETWORK_ERROR')
            return real(dn)
        backend.adapter.directory_members = flaky
        page = backend.members('d/sales', refresh=True)
        self.assertTrue(page['partial'])
        self.assertEqual({r['unit'] for r in page['rows']}, {'华南区'})

        def expired(dn):
            raise AppError('SESSION_EXPIRED')
        backend.adapter.directory_members = lambda dn: real(dn) if dn == 'd/sales' else expired(dn)
        with self.assertRaises(AppError):
            backend.members('d/sales', refresh=True)

    def test_department_and_people_caps(self):
        backend = self.make()
        backend.SUBTREE_DEPARTMENTS = 1
        page = backend.members('d/sales')
        self.assertTrue(page['partial'])
        self.assertEqual({r['unit'] for r in page['rows']}, {'华北区'})
        backend.SUBTREE_DEPARTMENTS, backend.SUBTREE_PEOPLE = 25, 3
        self.assertEqual(len(backend.members('d/sales')['rows']), 3)

    def test_page_names_units_and_explains_empty_departments(self):
        script = (WEB / 'contacts.js').read_text(encoding='utf-8')
        for marker in ('ct-unit', '含 ${page.departments} 个子部门', '组织通讯录中这个部门没有登记成员', "['unit', '子部门']"):
            self.assertIn(marker, script)
        self.assertNotIn('CT.rows.indexOf(p)', script)  # O(n²) for thousands of people.


if __name__ == '__main__':
    unittest.main()
