"""Mail rules, holiday auto-reply, tags and meeting-invitation answers (v0.4)."""
import shutil
import tempfile
import unittest
from pathlib import Path

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.errors import AppError
from coremail_desktop.protocol import rules as R
from tests.test_app_backend import XorProtector, local_services_stub

# Shapes as the deployment returned them (analysis/rules-tags-shape2-2026-10-18.json), with synthetic values.
SERVER_RULES = [{'id': 100, 'name': 'n1', 'continue': True,
                 'condictions': [{'extractAddress': True, 'field': 'from', 'forAddress': True, 'ignoreCase': True, 'listOfValues': True,
                                  'operator': '=', 'operand': 'a@example.invalid'}],
                 'actions': [{'target': 975274, 'type': 'move'}]},
                {'id': 102, 'name': 'n2', 'continue': True, 'operator': 'or',
                 'condictions': [{'field': 'subject', 'ignoreCase': True, 'listOfValues': True, 'operator': 'contains', 'operand': 'x'},
                                 {'field': 'securityLevel', 'operator': '=', 'operand': '2'}],
                 'actions': [{'target': 975276, 'type': 'move'}]}]


class CodecTest(unittest.TestCase):
    def test_server_rules_view(self):
        first, second = (R.rule_view(r) for r in SERVER_RULES)
        self.assertEqual(first['conditions'], [{'field': 'from', 'op': '=', 'value': 'a@example.invalid'}])
        self.assertEqual(first['actions'], [{'type': 'move', 'folder': '975274'}])
        self.assertEqual((first['match'], first['stop'], first['enabled'], first['editable']), ('all', False, True, True))
        self.assertEqual(second['match'], 'any')
        self.assertFalse(second['editable'])  # A condition the client does not edit keeps the rule read-only.
        self.assertEqual(second['counts'], {'conditions': 2, 'actions': 1})

    def test_rule_server_validates_and_round_trips(self):
        view = {'name': 'HY', 'match': 'any', 'stop': True, 'enabled': False,
                'conditions': [{'field': 'subject', 'op': 'contains', 'value': 'HY-RULE-TEST'}, {'field': 'from', 'op': '=', 'value': 'a@example.invalid'}],
                'actions': [{'type': 'move', 'folder': '100'}, {'type': 'flag'}]}
        server = R.rule_server(view, {'100'}, set())
        self.assertEqual(server['operator'], 'or')
        self.assertIs(server['continue'], False)
        self.assertIs(server['disabled'], True)
        self.assertEqual(server['actions'][0], {'type': 'move', 'target': 100})
        self.assertTrue(server['conditions'][1]['forAddress'])
        back = R.rule_view(server)
        self.assertEqual((back['conditions'], back['actions'], back['match'], back['stop'], back['enabled']),
                         (view['conditions'], view['actions'], 'any', True, False))
        for bad in ({**view, 'name': ''}, {**view, 'conditions': []}, {**view, 'actions': [{'type': 'move', 'folder': '999'}]},
                    {**view, 'actions': [{'type': 'forward', 'to': 'nobody'}]}, {**view, 'conditions': [{'field': 'body', 'value': 'x'}]},
                    {**view, 'conditions': [{'field': 'size', 'op': '>', 'value': -1}]}):
            with self.assertRaises(AppError):
                R.rule_server(bad, {'100'}, set())

    def test_webmail_editor_shapes(self):
        view = {'name': 'all', 'conditions': [{'field': 'attachment'}, {'field': 'size', 'op': '>', 'value': 2 * 1048576},
                                              {'field': 'to', 'op': 'contains', 'value': 'b@example.invalid'}],
                'actions': [{'type': 'read'}, {'type': 'forward', 'to': 'c@example.invalid'}, {'type': 'tag', 'tag': '19'}]}
        server = R.rule_server(view, set(), {'19'})
        self.assertEqual(server['conditions'][0], {'field': 'flags', 'operator': 'contains', 'operand': ['attached', 'linkAttached']})
        self.assertEqual(server['conditions'][1], {'field': 'size', 'operator': '>', 'operand': 2048})  # KB, as webmail stores it.
        self.assertTrue(server['conditions'][2]['forAddress'])
        self.assertEqual(server['actions'], [{'type': 'flags', 'value': {'read': True}}, {'type': 'forward', 'target': 'c@example.invalid', 'keepLocal': True},
                                             {'type': 'tags', 'value': ['19']}])
        back = R.rule_view(server)
        self.assertTrue(back['editable'])
        self.assertEqual((back['conditions'], back['actions']), (view['conditions'], view['actions']))
        # Webmail's flag with a colour, and its VIP rule, are recognised / left alone.
        self.assertEqual(R.rule_view({'name': 'x', 'condictions': server['conditions'], 'actions': [{'type': 'flags', 'value': {'flagged': True, 'label0': 1}}]})['actions'],
                         [{'type': 'flag'}])
        self.assertFalse(R.rule_view({'name': '##VIP', 'condictions': server['conditions'], 'actions': [{'type': 'flags', 'value': {'vip': True}}]})['editable'])

    def test_tag_names_follow_webmail(self):
        rows = [['19', {'color': '0'}], ['18', {'color': '2'}], ['13', {'color': '3'}], ['11', {'color': '8'}], ['x1', {'name': '项目', 'color': '#123456'}]]
        tags = {t['id']: t for t in R.tags_view(rows)}
        self.assertEqual([tags[i]['name'] for i in ('19', '18', '13', '11')], ['红色标签', '橙色标签', '蓝色标签', '灰色标签'])
        self.assertEqual(tags['18']['color'], '#ee791a')
        self.assertEqual((tags['x1']['name'], tags['x1']['color']), ('项目', '#123456'))
        self.assertEqual(R.stored_color('#30aed5'), '3')
        self.assertEqual(R.stored_color('#123ABC'), '#123abc')
        self.assertEqual(R.new_tag_id({'x' + format(1000, 'x')}, now=1), 'x' + format(1001, 'x'))
        for bad in ('', 'a/b', 'x' * 25, '标签' * 7):
            with self.assertRaises(AppError):
                R.clean_tag_name(bad)
        self.assertEqual(R.clean_tag_name(' 项目 A '), '项目 A')

    def test_autoreply(self):
        attrs = {'autorepmode': 0, 'autoreptext': 't', 'autorep_welcometip': 0,
                 'autorep_condition': {'begintime': '2026-10-20 00:00:00', 'endtime': '2026-10-24 23:59:59', 'instation': 1}}
        view = R.autoreply_view(attrs)
        self.assertEqual((view['enabled'], view['internalOnly'], view['start']), (False, True, '2026-10-20 00:00:00'))
        server = R.autoreply_server(view, '2026-10-18 10:00:00')
        self.assertEqual(server, {**attrs, 'autorep_condition': dict(attrs['autorep_condition'])})
        for bad in ({**view, 'enabled': True, 'text': ''}, {**view, 'enabled': True, 'start': ''}, {**view, 'start': '2026-10-20'},
                    {**view, 'end': '2026-10-19 00:00:00'}):
            with self.assertRaises(AppError):
                R.autoreply_server(bad, '2026-10-18 10:00:00')
        with self.assertRaises(AppError) as caught:
            R.autoreply_server({**view, 'enabled': True}, '2026-10-25 00:00:00')
        self.assertEqual(caught.exception.code, 'AUTOREPLY_ENDED')


class Recording:
    def __init__(self, calendar=None):
        self.calls, self.calendar, self.sid, self.base_url = [], calendar, 's1', 'https://mail.example.invalid/coremail/'

    def call(self, op, params):
        self.calls.append((op, params))
        if op == 'mbox:getAllFolders':
            return {'code': 'S_OK', 'var': [{'id': 1, 'name': 'Inbox'}, {'id': 4, 'name': 'Trash', 'flags': {'system': True}}, {'id': 975274, 'name': 'A'}]}
        if op == 'mbox:listMessages':
            return {'code': 'S_OK', 'var': [{'id': 'm1', 'fid': 1, 'subject': 's', 'from': 'a@example.invalid', 'flags': {}, 'tag': ['19', 7]}], 'total': 1}
        return {'code': 'S_OK', 'var': []}

    def _fetch(self, request):
        self.calls.append(('readMessage', request.data.decode()))
        return b'', request.full_url

    def _decode_response(self, raw):
        return {'code': 'S_OK', 'var': {'mail': {'calendar': self.calendar}}}


class AdapterTest(unittest.TestCase):
    def adapter(self, **kw):
        from coremail_desktop.protocol.coremail import CoremailAdapter
        adapter = CoremailAdapter()
        adapter._session = Recording(**kw)
        return adapter

    def test_allowed(self):
        from coremail_desktop.protocol.coremail import DesktopSession
        for op in ('user:getMailRules', 'user:addMailRules', 'user:updateMailRules', 'user:removeMailRules', 'user:adjustMailRuleOrder',
                   'user:setAttrs', 'mbox:listTags', 'mbox:manageTags', 'mbox:updateMessageTags', 'cal:respond'):
            self.assertIn(op, DesktopSession.ALLOWED)

    def test_wire_shapes(self):
        adapter = self.adapter()
        adapter.add_mail_rule({'name': 'r', 'conditions': [], 'actions': []})
        adapter.manage_tags('add', [['x1', {'name': 'n', 'color': '0'}]])
        adapter.update_message_tags(['m1', 'm1'], add=['x1'])
        adapter.set_auto_reply({'autorepmode': 0})
        adapter.move_mail_rule(104, 102)
        adapter.remove_mail_rule(104)
        page = adapter.tagged_messages('19', 0)
        calls = dict(adapter._session.calls)
        self.assertEqual(calls['user:addMailRules'], {'items': [{'disabled': False, 'name': 'r', 'conditions': [], 'actions': []}]})
        self.assertEqual(calls['mbox:manageTags'], {'add': [['x1', {'name': 'n', 'color': '0'}]]})
        self.assertEqual(calls['mbox:updateMessageTags'], {'ids': ['m1'], 'add': ['x1']})
        self.assertEqual(calls['user:setAttrs'], {'attrs': {'autorepmode': 0}})
        self.assertEqual(calls['user:adjustMailRuleOrder'], {'id': 104, 'ref': 102, 'value': 0})
        self.assertEqual(calls['user:removeMailRules'], {'ids': [104]})
        listed = calls['mbox:listMessages']
        self.assertEqual((listed['fids'], listed['tags'], listed['skipLockedFolders']), ([1, 975274], ['19'], True))
        self.assertEqual(page['messages'][0].tags, ('19', '7'))
        with self.assertRaises(AppError):
            adapter.manage_tags('rename', [['x1', {}]])

    def test_respond_uses_the_server_calendar_record(self):
        adapter = self.adapter(calendar={'calFid': 3, 'calUid': 'u-1', 'meetingRequestMethod': 'request'})
        self.assertEqual(adapter.respond_invitation('m1', 'tentative'), {'answer': 'tentative', 'type': 2})
        calls = adapter._session.calls
        self.assertEqual(calls[0], ('readMessage', 'mid=m1'))
        self.assertEqual(calls[1], ('cal:respond', {'eventId': 'u-1', 'type': 2, 'inform': True, 'fid': 3}))
        self.assertEqual(calls[2][1]['attrs'], {'partStat': 2})
        with self.assertRaises(AppError) as caught:
            self.adapter(calendar=None).respond_invitation('m1', 'accept')
        self.assertEqual(caught.exception.code, 'INVITE_UNAVAILABLE')
        with self.assertRaises(AppError):
            adapter.respond_invitation('m1', 'maybe')


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'x')
        self.backend.folders()
        self.backend.messages('1')

    def tearDown(self):
        self.backend.logout()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_rules_lifecycle(self):
        b = self.backend
        rules = b.rules()['rules']
        self.assertEqual([r['editable'] for r in rules], [True, True, False])
        view = {'name': 'HY-RULE-TEST', 'match': 'all', 'enabled': True, 'stop': False,
                'conditions': [{'field': 'subject', 'op': 'contains', 'value': 'HY-RULE-TEST'}], 'actions': [{'type': 'flag'}]}
        new_id = b.save_rule(view)['saved']
        with self.assertRaises(AppError):
            b.save_rule(view)  # Same name.
        b.save_rule({**view, 'id': new_id, 'actions': [{'type': 'tag', 'tag': '19'}, {'type': 'forward', 'to': 'demo@example.invalid'}]})
        self.assertEqual(next(r for r in b.rules()['rules'] if r['id'] == new_id)['actions'][1], {'type': 'forward', 'to': 'demo@example.invalid'})
        with self.assertRaises(AppError) as caught:
            b.save_rule({**view, 'name': 'x', 'id': 103})
        self.assertEqual(caught.exception.code, 'RULE_NOT_EDITABLE')
        self.assertFalse(next(r for r in b.set_rule_enabled(103, False)['rules'] if r['id'] == 103)['enabled'])
        self.assertEqual([r['id'] for r in b.move_rule(new_id, -1)['rules']], [100, 102, new_id, 103])
        self.assertNotIn(new_id, [r['id'] for r in b.delete_rule(new_id)['rules']])

    def test_unconfirmed_rule_write_is_reported(self):
        self.backend.adapter.add_mail_rule = lambda rule: True  # The server accepts but stores nothing.
        with self.assertRaises(AppError) as caught:
            self.backend.save_rule({'name': 'x', 'conditions': [{'field': 'subject', 'op': 'contains', 'value': 'x'}], 'actions': [{'type': 'read'}]})
        self.assertEqual(caught.exception.code, 'RULE_UNCONFIRMED')

    def test_auto_reply(self):
        b = self.backend
        now = b.auto_reply()
        self.assertEqual(now['zone'], 'Asia/Shanghai')
        saved = b.set_auto_reply({**now, 'enabled': True, 'internalOnly': False})
        self.assertTrue(saved['enabled'])
        self.assertEqual(b.adapter.attrs['autorep_condition']['instation'], 0)
        b.adapter.set_auto_reply = lambda attrs: True
        with self.assertRaises(AppError) as caught:
            b.set_auto_reply({**now, 'text': 'changed'})
        self.assertEqual(caught.exception.code, 'AUTOREPLY_UNCONFIRMED')

    def test_tags(self):
        b = self.backend
        tags = b.tags()['tags']
        self.assertEqual(len(tags), 11)
        row = next(r for r in b.messages('1')['rows'] if r['id'] == 'demo5')
        self.assertEqual(row['tags'], ['x19a1f0c0001', 'x19a1f0c0002'])
        created = b.save_tag('HY-TEST', '#2ea962')['saved']
        self.assertTrue(created.startswith('x'))
        with self.assertRaises(AppError):
            b.save_tag('HY-TEST', '#2ea962')
        b.save_tag('HY-TEST2', '#cc0000', tag_id=created)
        self.assertEqual(next(t for t in b.tags()['tags'] if t['id'] == created)['name'], 'HY-TEST2')
        b.tag_messages(['demo1', 'demo3'], add=[created], remove=['x19a1f0c0001'])
        self.assertEqual(b.cache.summary('demo1')['tags'], [created])
        self.assertEqual({r['id'] for r in b.tag_view(created)['rows']}, {'demo1', 'demo3'})
        self.assertEqual(b.open_message('demo3')['tags'], [created])
        with self.assertRaises(AppError):
            b.tag_messages(['demo1'], add=['no-such-tag'])
        b.shared_mailboxes()
        with self.assertRaises(AppError):
            b.tag_messages(['demo1@rd'], add=[created])  # Shared mailboxes keep their tags in webmail.
        self.assertNotIn(created, [t['id'] for t in b.delete_tag(created)['tags']])

    def test_invitations(self):
        b = self.backend
        m = b.open_message('demo10')
        card = m['invitation']
        self.assertEqual((card['method'], card['summary'], card['canAnswer'], card['answer']), ('REQUEST', '设计评审第二轮', True, None))
        self.assertEqual(len(card['attendees']), 3)
        self.assertEqual(b.respond_invitation('demo10', 'accept')['type'], 3)
        self.assertEqual(b.open_message('demo10')['invitation']['answer'], 'accept')
        cancelled = b.open_message('demo14')['invitation']
        self.assertTrue(cancelled['cancelled'])
        with self.assertRaises(AppError):
            b.respond_invitation('demo14', 'accept')
        name, kind, data = b.attachment('demo10', 'invite.ics')
        self.assertEqual(kind, 'text/calendar')
        self.assertIn(b'METHOD:REQUEST', data)
        self.assertNotIn('invitation', b.open_message('demo1'))
        with self.assertRaises(AppError):
            b.attachment('demo1', 'invite.ics')


class WebAssetsTest(unittest.TestCase):
    WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'

    def test_hooks(self):
        html = (self.WEB / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('contacts.js'), html.index('<script src="rules.js">'))
        self.assertIn('TG.chips(r.tags)', (self.WEB / 'app.js').read_text(encoding='utf-8'))
        script = (self.WEB / 'rules.js').read_text(encoding='utf-8')
        for name in ("api('saveRule'", "api('setAutoReply'", "api('tagMessages'", "api('respondInvitation'", "part: 'invite.ics'"):
            self.assertIn(name, script)
        from coremail_desktop.app.shell import BACKEND_METHODS
        from coremail_desktop.app.backend import MailBackend as B
        for method in ('rules', 'saveRule', 'setRuleEnabled', 'deleteRule', 'moveRule', 'autoReply', 'setAutoReply', 'tags', 'saveTag',
                       'deleteTag', 'tagMessages', 'tagView', 'respondInvitation'):
            self.assertTrue(hasattr(B, BACKEND_METHODS[method]), method)


if __name__ == '__main__':
    unittest.main()
