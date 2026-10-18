"""v0.4: a colleague's directory details in the contacts card and the reader popover."""
import base64
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend, _image_uri
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession
from test_app_backend import XorProtector, local_services_stub

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 40


def person_key(backend):
    db = sqlite3.connect(str(backend.cache.path))
    try:
        return db.execute("SELECT k FROM kv WHERE k LIKE 'person:%'").fetchone()[0]
    finally:
        db.close()


class PersonSession:
    """oab:get answers by email; head images come back as bytes or a JSON refusal."""
    def __init__(self, image=PNG):
        self.calls, self.image = [], image

    def call(self, func, params):
        self.calls.append((func, params))
        if func == 'oab:getDirectories':
            return {'code': 'S_OK', 'var': [{'id': 'a', 'name': 'Org', 'ou': []}]}
        if func == 'oab:get':
            return {'code': 'S_OK', 'var': [{'@id': 'u7', 'email': 'P@example.test', 'true_name': 'P', 'duty': 'eng', '@location': 'Team/Dept/Org',
                                             'mobile_number': '100', 'company_phone': '', 'address': None, 'alias_emails': ['x']}]
                    if params['list'] == ['p@example.test'] else []}
        raise AssertionError(func)

    def get_raw(self, func, **params):
        self.calls.append((func, params))
        return self.image

    def close(self):
        pass


class ProtocolTests(unittest.TestCase):
    def adapter(self, image=PNG):
        a = CoremailAdapter(); a._session = PersonSession(image)
        return a

    def test_read_only_rpcs_are_allowed(self):
        for func in ('oab:get', 'oab:getHeadImageData'):
            self.assertIn(func, DesktopSession.ALLOWED)

    def test_details_map_directory_attributes(self):
        a = self.adapter()
        p = a.person_details('p@example.test')
        self.assertEqual(p, {'name': 'P', 'title': 'eng', 'mobile': '100', 'phone': '', 'fax': '', 'address': '',
                             'email': 'P@example.test', 'dept': 'Org / Dept / Team', 'uid': 'u7'})
        func, params = a._session.calls[0]
        self.assertEqual((func, params['list'], params['forEmail']), ('oab:get', ['p@example.test'], True))
        # One attribute the deployment does not expose fails the whole call (FA_FORBIDDEN).
        verified = {'@id', '@type', 'true_name', 'email', 'duty', '@location', '@ou', 'gender', 'nick_name', 'alias_emails', 'mobile_number',
                    'company_phone', 'home_phone', 'fax_number', 'address', 'zipcode', 'province', 'city', 'country', 'remarks'}
        self.assertLessEqual(set(params['returnAttrs']), verified)
        self.assertIsNone(a.person_details('nobody@example.test'))
        with self.assertRaises(AppError):
            a.person_details('not an address')

    def test_head_image_bytes_or_none(self):
        self.assertEqual(self.adapter().head_image('u7'), PNG)
        self.assertIsNone(self.adapter(b'{"code":"FA_NOT_FOUND"}').head_image('u7'))
        self.assertIsNone(self.adapter().head_image(''))
        a = self.adapter(); a.head_image('u7')
        self.assertEqual(a._session.calls[-1], ('oab:getHeadImageData', {'uid': 'u7'}))


class CountingAdapter(DemoAdapter):
    calls = 0

    def person_details(self, email):
        CountingAdapter.calls += 1
        return super().person_details(email)


class BackendTests(unittest.TestCase):
    def make(self, factory=DemoAdapter):
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        backend = MailBackend(directory, adapter_factory=factory, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        return backend

    def test_details_carry_every_known_field_and_the_head_image(self):
        p = self.make().person('ZhangXM@example.invalid')
        self.assertTrue(p['found'])
        self.assertEqual((p['name'], p['title'], p['dept']), ('张晓明', '前端工程师', '演示公司 / 研发中心 / 前端组'))
        for key in ('mobile', 'phone', 'address'):
            self.assertTrue(p[key], key)
        self.assertTrue(p['avatar'].startswith('data:image/png;base64,'))
        self.assertEqual(self.make().person('wangfang@example.invalid')['avatar'], '')  # Initials stand in.

    def test_unknown_people_are_not_found(self):
        backend = self.make()
        self.assertFalse(backend.person('partner@external.example')['found'])
        for bad in ('', 'no-at-sign', 'x' * 300 + '@a.b'):
            with self.assertRaises(AppError):
                backend.person(bad)

    def test_details_come_from_the_encrypted_cache_until_a_day_old(self):
        CountingAdapter.calls = 0
        backend = self.make(CountingAdapter)
        backend.person('lihua@example.invalid')
        again = backend.person('lihua@example.invalid')
        self.assertTrue(again['cached'])
        self.assertEqual(CountingAdapter.calls, 1)
        key = person_key(backend)
        backend.cache.set(key, {**backend.cache.get(key), 'at': again['at'] - MailBackend.PERSON_MAX_AGE - 1})
        self.assertFalse(backend.person('lihua@example.invalid')['cached'])
        self.assertEqual(CountingAdapter.calls, 2)
        backend.person('lihua@example.invalid', refresh=True)
        self.assertEqual(CountingAdapter.calls, 3)

    def test_stale_details_survive_going_offline(self):
        backend = self.make()
        backend.person('lihua@example.invalid')
        key = person_key(backend)
        backend.cache.set(key, {**backend.cache.get(key), 'at': 0})

        def offline(email):
            raise AppError('NETWORK_ERROR')
        backend.adapter.person_details = offline
        p = backend.person('lihua@example.invalid')
        self.assertTrue(p['cached'] and p['found'])

    def test_cache_holds_no_plain_address_or_details(self):
        backend = self.make()
        backend.person('lihua@example.invalid')
        db = sqlite3.connect(str(backend.cache.path))
        keys = [k for (k,) in db.execute('SELECT k FROM kv')]
        blobs = b''.join(bytes(b) for (b,) in db.execute('SELECT blob FROM kv'))
        db.close()
        self.assertTrue(any(k.startswith('person:') for k in keys))
        self.assertFalse(any('lihua' in k for k in keys))
        self.assertNotIn('架构师'.encode('utf-8'), blobs)

    def test_a_failing_head_image_keeps_the_details(self):
        backend = self.make()

        def broken(uid):
            raise AppError('NETWORK_ERROR')
        backend.adapter.head_image = broken
        p = backend.person('zhangxm@example.invalid')
        self.assertTrue(p['found'])
        self.assertEqual(p['avatar'], '')

    def test_only_real_small_images_become_data_uris(self):
        self.assertEqual(_image_uri(PNG), 'data:image/png;base64,' + base64.b64encode(PNG).decode())
        self.assertTrue(_image_uri(b'\xff\xd8\xff\xe0' + b'\x00' * 8).startswith('data:image/jpeg;'))
        for bad in (None, b'', b'<svg onload=alert(1)>', b'{"code":"S_OK"}', PNG + b'\x00' * 300_000, 'iVBOR'):
            self.assertEqual(_image_uri(bad), '')


class WebTests(unittest.TestCase):
    def test_bridge_and_page_wire_the_details(self):
        from coremail_desktop.app.shell import BACKEND_METHODS, FAST_METHODS
        self.assertEqual(BACKEND_METHODS['person'], 'person')
        self.assertIn('person', FAST_METHODS)
        script = (WEB / 'contacts.js').read_text(encoding='utf-8')
        for label in ('姓名', '邮箱', '部门', '职位', '手机', '办公电话', '工号', '办公地点', '上级'):
            self.assertIn(f"'{label}'", script)
        self.assertIn("api('person', { email, refresh })", script)
        self.assertIn('function personPopover(', script)
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn('personPopover(a,', app)  # Reader names open the popover; 写邮件 is inside it.
        self.assertNotIn('console.log', script)


if __name__ == '__main__':
    unittest.main()
