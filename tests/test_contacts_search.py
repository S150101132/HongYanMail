"""v0.3: read-only contacts view (org directory, personal contacts) and advanced search filters."""
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from coremail_desktop.app.backend import MailBackend, filter_match, search_filters
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession
from test_app_backend import XorProtector, local_services_stub

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'


class FormSession(DesktopSession):
    def __init__(self):
        super().__init__('https://example.test/coremail/'); self.sid = 'test'; self.requests = []

    def _fetch(self, request):
        self.requests.append(request)
        return b'{"code":"S_OK","total":120,"var":[{"id":"a","fid":1,"subject":"s","from":"x@example.test","flags":{"attached":true}}]}', request.full_url


class DirectorySession:
    """The verified oab shapes: one root, nested ou, listEx paging with total."""
    def __init__(self, people=5):
        self.calls = []
        self.people = [{'@id': f'u{i}', '@type': 'U', 'true_name': f'P{i}', 'email': f'p{i}@example.test', 'duty': 'eng',
                        '@location': 'Dept/Org'} for i in range(people)] + [{'@id': 'nomail', 'true_name': 'X'}]

    def call(self, func, params):
        self.calls.append((func, params))
        if func == 'oab:getDirectories':
            return {'code': 'S_OK', 'var': [{'id': 'a', 'name': 'Org', 'ou': [
                {'id': '10', 'name': 'Dept', 'ou': [{'id': '11', 'name': 'Team', 'ou': []}]}, {'id': 'bad/id', 'name': 'x'}]}]}
        if func == 'oab:listEx':
            start, limit = params['start'], params['limit']
            return {'code': 'S_OK', 'total': len(self.people), 'var': self.people[start:start + limit]}
        if func == 'pab:searchContacts':
            return {'code': 'S_OK', 'var': [{'id': '1', 'FN': 'Friend', 'EMAIL;PREF': 'f@outside.test', 'groups': []}, {'id': '2', 'FN': 'No mail'}]}
        raise AssertionError(func)

    def close(self):
        pass


class ProtocolTests(unittest.TestCase):
    def test_new_read_only_rpcs_are_allowed(self):
        for func in ('oab:getDirectories', 'oab:listEx', 'pab:searchContacts', 'oab:autoMatch'):
            self.assertIn(func, DesktopSession.ALLOWED)

    def test_filters_map_to_the_verified_webmail_form_fields(self):
        a = CoremailAdapter(); a._session = FormSession()
        page = a.search_messages('会议', 0, 50, filters={'from': 'zhang', 'to': 'li', 'since': '2026-09-01', 'until': '2026-09-30',
                                                        'folder': '3', 'attachment': True, 'unread': True, 'flagged': True})
        self.assertEqual(page['total'], 120)
        form = parse_qs(a._session.requests[0].data.decode())
        self.assertEqual(form, {'pattern': ['会议'], 'from': ['zhang'], 'to': ['li'], 'sentDate': ['2026-09-01:2026-09-30 23:59:59'],
                                'fid': ['3'], 'attachType': ['attached'], 'read': ['false'], 'flagType': ['flagged'],
                                'start': ['0'], 'limit': ['50'], 'summaryWindowSize': ['0']})
        self.assertEqual(parse_qs(urlsplit(a._session.requests[0].full_url).query), {'func': ['searchMessages'], 'sid': ['test']})

    def test_subject_scope_open_dates_and_dealings(self):
        self.assertEqual(CoremailAdapter.search_fields('预算', {'scope': 'subject'}), ('', {'subject': '预算'}))
        self.assertEqual(CoremailAdapter.search_fields('', {'since': '2026-01-01'})[1], {'sentDate': '2026-01-01:'})
        self.assertEqual(CoremailAdapter.search_fields('', {'until': '2026-01-31'})[1], {'sentDate': '1970-01-01:2026-01-31 23:59:59'})
        self.assertEqual(CoremailAdapter.search_fields('', {'with': 'p@example.test'})[1], {'dealings': 'p@example.test'})

    def test_filters_alone_search_but_nothing_or_unknown_is_refused(self):
        a = CoremailAdapter(); a._session = FormSession()
        a.search_messages('', 0, 50, filters={'attachment': True})
        self.assertNotIn('pattern', parse_qs(a._session.requests[0].data.decode()))
        for pattern, filters in (('', {}), ('', None), ('x', {'evil': 1})):
            with self.assertRaises(AppError):
                a.search_messages(pattern, 0, 50, filters=filters)
        self.assertEqual(len(a._session.requests), 1)

    def test_directory_tree_builds_root_slash_id_at_every_depth(self):
        a = CoremailAdapter(); a._session = DirectorySession()
        tree = a.directory_tree()
        self.assertEqual(tree[0]['id'], 'a')
        dept = tree[0]['children'][0]
        self.assertEqual((dept['id'], dept['name']), ('a/10', 'Dept'))
        self.assertEqual(dept['children'][0]['id'], 'a/11')
        self.assertEqual(len(tree[0]['children']), 1, 'an id with a slash cannot form a dn')

    def test_members_page_through_and_show_the_department_root_first(self):
        a = CoremailAdapter(); a._session = DirectorySession(people=7); a.DIRECTORY_PAGE = 3
        result = a.directory_members('a/10')
        self.assertEqual(result['total'], 8)
        self.assertEqual([r['email'] for r in result['rows']], [f'p{i}@example.test' for i in range(7)])
        self.assertEqual(result['rows'][0], {'name': 'P0', 'email': 'p0@example.test', 'title': 'eng', 'dept': 'Org / Dept'})
        pages = [p for f, p in a._session.calls if f == 'oab:listEx']
        self.assertEqual([p['start'] for p in pages], [0, 3, 6])
        self.assertTrue(all(p['dn'] == 'a/10' for p in pages))
        with self.assertRaises(AppError):
            a.directory_members('')

    def test_members_stop_at_the_cap(self):
        a = CoremailAdapter(); a._session = DirectorySession(people=50); a.DIRECTORY_PAGE = 10; a.DIRECTORY_CAP = 20
        result = a.directory_members('a/10')
        self.assertEqual((len(result['rows']), result['total']), (20, 51))

    def test_personal_contacts_keep_only_addresses(self):
        a = CoremailAdapter(); a._session = DirectorySession()
        self.assertEqual(a.personal_contacts(), [{'name': 'Friend', 'email': 'f@outside.test'}])


class PartialAdapter(DemoAdapter):
    """A server that applies only some filters: the rest must be applied locally and reported."""
    SEARCH_FILTERS = frozenset({'folder'})
    seen = None

    def search_messages(self, pattern, offset, limit=50, filters=None):
        PartialAdapter.seen = dict(filters or {})
        return super().search_messages(pattern, offset, limit, filters)


class BackendTests(unittest.TestCase):
    def make(self, factory=DemoAdapter):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        backend = MailBackend(directory, adapter_factory=factory, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        return backend

    def test_filters_are_validated(self):
        self.assertEqual(search_filters({'from': ' zhang ', 'unread': True, 'flagged': False, 'scope': 'all', 'evil': 1, 'since': ''}),
                         {'from': 'zhang', 'unread': True})
        for bad in ({'since': '2026-13-01'}, {'since': '2026-10-02', 'until': '2026-10-01'}, {'from': 'x' * 201}):
            with self.assertRaises(AppError):
                search_filters(bad)

    def test_local_match(self):
        row = {'subject': '季度预算', 'from': '"张" <zhang@example.test>', 'to': 'me@example.test', 'fid': '1', 'ts': 1_790_000_000,
               'flags': {'read': False, 'attached': True}}
        self.assertTrue(filter_match({'from': 'ZHANG', 'attachment': True, 'unread': True, 'folder': '1'})(row))
        self.assertTrue(filter_match({'with': 'me@example'})(row))
        self.assertFalse(filter_match({'flagged': True})(row))
        self.assertTrue(filter_match({'scope': 'subject'}, '预算')(row))
        self.assertFalse(filter_match({'scope': 'subject'}, 'zhang')(row))

    def test_server_applies_every_filter_it_supports(self):
        backend = self.make()
        result = backend.search_server('', filters={'attachment': True, 'folder': '1'})
        self.assertEqual(result['local'], [])
        self.assertEqual(result['server'], ['attachment', 'folder'])
        self.assertTrue(result['rows'] and all(r['flags']['attached'] and r['fid'] == '1' for r in result['rows']))
        self.assertEqual((result['total'], result['more']), (24, False))
        pages = [backend.search_server('', offset, filters={'folder': '1'}) for offset in (0, 50, 100)]
        self.assertEqual([p['more'] for p in pages], [True, True, False])
        self.assertEqual(len({r['id'] for p in pages for r in p['rows']}), 120)

    def test_unsupported_filters_narrow_the_returned_rows_and_are_named(self):
        backend = self.make(PartialAdapter)
        result = backend.search_server('演示', filters={'folder': '1', 'unread': True})
        self.assertEqual(PartialAdapter.seen, {'folder': '1'})
        self.assertEqual((result['server'], result['local']), (['folder'], ['unread']))
        self.assertTrue(result['rows'] and all(not r['flags']['read'] for r in result['rows']))
        with self.assertRaises(AppError):  # Only local filters and no words: nothing to ask the server.
            backend.search_server('', filters={'unread': True})

    def test_mail_with_a_person_uses_dealings(self):
        backend = self.make()
        rows = backend.search_server('', filters={'with': 'zhangxm@example.invalid'})['rows']
        self.assertTrue(rows and all('zhangxm@example.invalid' in r['from'] + r['to'] for r in rows))
        self.assertEqual(backend.search_capabilities()['server'], sorted(DemoAdapter.SEARCH_FILTERS))

    def test_local_search_takes_filters_without_words(self):
        backend = self.make()
        backend.messages('1', 0, 50)
        rows = backend.search_local('', {'flagged': True})
        self.assertTrue(rows and all(r['flags']['flagged'] for r in rows))
        self.assertEqual(backend.search_local(''), [])

    def test_directory_opens_from_the_encrypted_cache(self):
        backend = self.make()
        first = backend.directory()
        self.assertFalse(first['cached'])
        self.assertEqual(first['tree'][0]['children'][0]['name'], '研发中心')
        backend.adapter.directory_tree = lambda: self.fail('the cached tree needs no network call')
        again = backend.directory()
        self.assertTrue(again['cached'])
        self.assertEqual(again['tree'], first['tree'])

    def test_offline_refresh_falls_back_to_the_cache(self):
        backend = self.make()
        backend.members('d/rd-fe')

        def offline(*args):
            raise AppError('NETWORK_ERROR')
        backend.adapter.directory_members = offline
        page = backend.members('d/rd-fe', refresh=True)
        self.assertTrue(page['cached'])
        self.assertEqual([r['name'] for r in page['rows']], ['张晓明', '王芳', '赵磊'])
        self.assertEqual(page['rows'][0]['dept'], '演示公司 / 研发中心 / 前端组')
        with self.assertRaises(AppError):
            backend.members('d/never-seen', refresh=True)

    def test_cache_keys_hold_no_department_ids(self):
        backend = self.make()
        backend.members('d/rd-fe')
        db = sqlite3.connect(str(backend.cache.path))
        keys = [k for (k,) in db.execute('SELECT k FROM kv')]
        db.close()  # An open handle would keep the temp directory from being removed.
        self.assertTrue(any(k.startswith('members:') for k in keys))
        self.assertFalse(any('rd-fe' in k for k in keys))

    def test_personal_contacts_and_company_search(self):
        backend = self.make()
        self.assertEqual(len(backend.personal_contacts()['rows']), 2)
        self.assertIn('lihua@example.invalid', [p['email'] for p in backend.find_people('李')])
        self.assertEqual(backend.find_people('  '), [])


class WebTests(unittest.TestCase):
    def test_bridge_and_page_expose_the_views(self):
        from coremail_desktop.app.shell import BACKEND_METHODS
        for name in ('directory', 'members', 'personalContacts', 'findPeople', 'searchCapabilities'):
            self.assertIn(name, BACKEND_METHODS)
        page = (WEB / 'index.html').read_text(encoding='utf-8')
        for marker in ('id="nav-contacts"', 'id="contacts-view"', 'id="search-panel"', 'id="search-chips"', '<script src="contacts.js">'):
            self.assertIn(marker, page)
        script = (WEB / 'contacts.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML = p.', script)
        self.assertIn("api('searchServer', { query: q, filters })", (WEB / 'app.js').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
