"""本机只读 MCP 服务 (X1): the JSON-RPC handler, the backend's read-only tools behind it,
and the relay talking to the app over a real named pipe. Demo data only."""
import io
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter
from coremail_desktop.mcp import __main__ as relay
from coremail_desktop.mcp.server import Host, handle, mcp_specs
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch


def rpc(mid, method, **params):
    return {'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params}


class HandlerTest(unittest.TestCase):
    def test_protocol(self):
        specs = lambda: [{'name': 'search_mail'}]
        call = lambda name, args: ({'echo': name, **args}, name == 'bad')
        hello = handle(rpc(1, 'initialize', protocolVersion='2025-03-26'), specs, call, '1.0')['result']
        self.assertEqual((hello['protocolVersion'], hello['capabilities'], hello['serverInfo']['name']), ('2025-03-26', {'tools': {}}, 'hongyan'))
        self.assertEqual(handle(rpc(1, 'initialize', protocolVersion='1999-01-01'), specs, call)['result']['protocolVersion'], '2025-06-18')
        self.assertIsNone(handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, specs, call))
        self.assertEqual(handle(rpc(2, 'tools/list'), specs, call)['result'], {'tools': [{'name': 'search_mail'}]})
        result = handle(rpc(3, 'tools/call', name='search_mail', arguments={'keywords': '周报'}), specs, call)['result']
        self.assertEqual(json.loads(result['content'][0]['text']), {'echo': 'search_mail', 'keywords': '周报'})
        self.assertFalse(result['isError'])
        self.assertTrue(handle(rpc(4, 'tools/call', name='bad'), specs, call)['result']['isError'])
        self.assertEqual(handle(rpc(5, 'resources/list'), specs, call)['error']['code'], -32601)
        self.assertEqual(handle(rpc(6, 'tools/call', name=3), specs, call)['error']['code'], -32602)
        self.assertEqual(handle([], specs, call)['error']['code'], -32600)
        tool = mcp_specs([{'function': {'name': 'a', 'description': 'b', 'parameters': {'type': 'object'}}}])[0]
        self.assertEqual(tool, {'name': 'a', 'description': 'b', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}})


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.directory = scratch(self)
        self.backend = MailBackend(self.directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def call(self, name, **arguments):
        result = self.backend.mcp_answer(rpc(1, 'tools/call', name=name, arguments=arguments))['result']
        return json.loads(result['content'][0]['text']), result['isError']

    def test_off_by_default_and_only_read_only_tools(self):
        status = self.backend.mcp_status()
        self.assertFalse(status['enabled'])
        self.assertIn('-m coremail_desktop.mcp', status['command'])
        self.assertEqual(self.call('search_mail', keywords='周报')[0]['code'], 'MCP_OFF')
        self.assertTrue(self.backend.set_mcp(True)['enabled'])
        record = self.backend.mcp_record()
        self.assertTrue(record['pipe'].startswith('\\\\.\\pipe\\hongyan-mcp-') and len(record['key']) == 64)
        self.assertNotIn(record['key'].encode(), (Path(self.directory) / 'mcp.key').read_bytes())  # Kept protected.
        names = [t['name'] for t in self.backend.mcp_answer(rpc(1, 'tools/list'))['result']['tools']]
        self.assertEqual(names, ['search_mail', 'read_mail', 'find_contact', 'list_tasks', 'read_task'])
        for name in ('plan_tasks', 'plan_mail', 'draft_reply', 'pending_mail', 'send_mail'):
            self.assertEqual(self.call(name), ({'error': '没有这个工具'}, True))
        self.backend.set_mcp(True)
        self.assertEqual(self.backend.mcp_record(), record)  # Turning it on again keeps the key.
        self.assertFalse(self.backend.set_mcp(False)['enabled'])
        self.backend.set_mcp(True)
        self.assertNotEqual(self.backend.mcp_record()['key'], record['key'])  # Off and on: a new key.

    def test_search_then_read(self):
        self.backend.set_mcp(True)
        found, failed = self.call('search_mail', keywords='周')
        self.assertFalse(failed, found)
        self.assertGreater(found['total'], 0)
        first = found['rows'][0]
        self.assertNotIn('@', json.dumps(found, ensure_ascii=False).replace('@example', ''))  # Names and domains only.
        self.backend.open_message(self.backend._mcp_tools.mails[first['ref']]['id'], mark_read=False)
        read, failed = self.call('read_mail', ref=first['ref'])
        self.assertFalse(failed, read)
        self.assertIn('text', read)
        self.assertEqual(self.backend.mcp_status()['calls'], 2)


class PipeTest(unittest.TestCase):
    def test_relay_reaches_the_app_and_says_when_it_is_off(self):
        directory = scratch(self)
        backend = MailBackend(directory, adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(backend.logout)
        lines = '\n'.join(json.dumps(m) for m in (rpc(1, 'initialize', protocolVersion='2025-06-18'),
                                                  {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                                                  rpc(2, 'tools/list'))) + '\n'
        run = lambda: (relay.main(io.StringIO(lines), out := io.StringIO(), XorProtector()), [json.loads(l) for l in out.getvalue().splitlines()])[1]
        with mock.patch.dict(os.environ, {'HONGYAN_DATA_DIR': str(directory)}):
            off = run()
            self.assertEqual([r['id'] for r in off], [1, 2])
            self.assertIn('允许本机 AI 工具读取邮箱', off[0]['error']['message'])
            host = Host(backend.mcp_answer, lambda fn: fn())
            self.addCleanup(host.stop)
            backend.set_mcp(True)
            record = backend.mcp_record()
            host.start(record['pipe'], record['key'].encode('ascii'))
            on = run()
            self.assertEqual([r['id'] for r in on], [1, 2])  # The notification gets no answer.
            self.assertEqual(on[0]['result']['serverInfo']['name'], 'hongyan')
            self.assertIn('read_mail', [t['name'] for t in on[1]['result']['tools']])
            host.stop()
            self.assertIn('error', run()[0])


if __name__ == '__main__':
    unittest.main()
