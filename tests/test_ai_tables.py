"""AI 优化表格 (T2, ai/tables.py): the page's structure summary goes out, a checked semantic plan
comes back, answers are cached per table so the same table gets the same layout."""
import json
import unittest

from coremail_desktop.ai import tables
from coremail_desktop.app.backend import MailBackend
from coremail_desktop.app.demo import DemoAdapter, DemoAiClient
from coremail_desktop.errors import AppError
from coremail_desktop.mime.sanitizer import html_to_text
from tests.test_app_backend import XorProtector, local_services_stub
from tests.test_compose_v05 import scratch


def summary(rows=4, cols=3, head='费用'):
    return {'rows': rows, 'cols': cols, 'signatures': [f'{r}:{"head" if r == 0 else "data"}:s{"n" * (cols - 1)}' for r in range(rows)],
            'columns': [{'i': c, 'type': 'text' if c == 0 else 'number', 'conf': 0.9, 'head': head if c else '项目'} for c in range(cols)],
            'sample': [{'i': 0, 'role': 'head', 'cells': [{'c': 0, 't': '项目'}, {'c': 1, 't': head}]},
                       {'i': rows - 1, 'role': 'data', 'cells': [{'c': 0, 't': '合计'}, {'c': 1, 't': '110.33'}]}]}


class FakeAi:
    model = 'fake-model'

    def __init__(self, content):
        self.calls, self.content = [], content

    def chat(self, messages, max_tokens=800, json_mode=False, temperature=0.3):
        self.calls.append({'messages': messages, 'temperature': temperature, 'json': json_mode})
        return self.content, 10


class MaterialTest(unittest.TestCase):
    def test_runs_of_rows_collapse(self):
        t = tables.check_tables([summary(rows=500)])[0]
        material = json.loads(tables.table_material([t]))
        self.assertEqual(material['tables'][0]['signatures'], ['0:head:snn', '1-499:data:snn'])

    def test_marks_wrap_the_material_and_cells_are_data(self):
        messages = tables.table_messages(tables.check_tables([summary()]))
        system, user = messages[0]['content'], messages[1]['content']
        open_mark = user.splitlines()[0]
        self.assertTrue(open_mark.startswith('<<<MAIL-'))
        self.assertIn(open_mark, system)
        self.assertIn('你不改任何内容', system)

    def test_bad_input(self):
        good = summary()
        for bad in ([], 'x', [good] * 6, [{**good, 'rows': 0}], [{**good, 'signatures': ['0:x'] * 3}], [{**good, 'columns': []}],
                    [{**good, 'rows': 2001}], [{**good, 'sample': 'x'}]):
            with self.assertRaises(AppError):
                tables.check_tables(bad)


class PlanTest(unittest.TestCase):
    def test_plan_is_checked(self):
        t = tables.check_tables([summary()])
        content = json.dumps({'tables': [{'purpose': 'expense', 'theme': 'report', 'emphasis': [1, 2, 9],
                                          'rows': [{'i': 3, 'role': 'total', 'conf': 0.95}, {'i': 9, 'role': 'total'}, {'i': 1, 'role': 'boss'}],
                                          'cols': [{'i': 1, 'type': 'money', 'conf': 2}, {'i': 7, 'type': 'money'}, {'i': 2, 'type': 'cash'}]}]})
        plan = tables.parse_plans(content, t)[0]
        self.assertEqual(plan['rows'], [{'i': 3, 'role': 'total', 'conf': 0.95}])
        self.assertEqual(plan['cols'], [{'i': 1, 'type': 'money', 'conf': 1.0}])
        self.assertEqual((plan['purpose'], plan['purposeLabel'], plan['theme'], plan['emphasis']), ('expense', '费用表', 'report', [1]))
        odd = tables.parse_plans('{"tables": [{"purpose": "x", "theme": "neon"}]}', t)[0]
        self.assertEqual((odd['purpose'], odd['theme'], odd['rows']), ('other', '', []))

    def test_wrong_count_or_no_json(self):
        t = tables.check_tables([summary()])
        for content in ('{"tables": []}', 'no json', '{"tables": "x"}'):
            with self.assertRaises(AppError):
                tables.parse_plans(content, t)


class BackendTest(unittest.TestCase):
    def setUp(self):
        self.backend = MailBackend(scratch(self), adapter_factory=DemoAdapter, protector=XorProtector(), services_factory=local_services_stub)
        self.backend.login('https://demo.invalid/coremail/', 'demo@example.invalid', 'pw', remember=True)
        self.addCleanup(self.backend.logout)

    def test_one_call_at_temperature_zero_then_cached(self):
        ai = self.backend.ai_client = FakeAi('{"tables": [{"purpose": "expense", "rows": [{"i": 3, "role": "total", "conf": 0.9}]}]}')
        first = self.backend.style_tables([summary()])
        self.assertEqual(first['plans'][0]['rows'], [{'i': 3, 'role': 'total', 'conf': 0.9}])
        self.assertFalse(first['cached'])
        self.assertEqual((ai.calls[0]['temperature'], ai.calls[0]['json']), (0, True))
        self.assertIn('110.33', ai.calls[0]['messages'][1]['content'])
        second = self.backend.style_tables([summary()])
        self.assertTrue(second['cached'])
        self.assertEqual(second['plans'], first['plans'])
        self.assertEqual(len(ai.calls), 1)  # The same table: the same answer, no new call.
        self.backend.style_tables([summary()], again=True)
        self.assertEqual(len(ai.calls), 2)  # 让 AI 重新考虑.
        self.backend.style_tables([summary(), summary(head='金额')])
        self.assertEqual(len(ai.calls[2]['messages'][1]['content'].split('金额')), 4)  # Only the new table is asked about.

    def test_bad_input_sends_nothing(self):
        ai = self.backend.ai_client = FakeAi('{}')
        with self.assertRaises(AppError):
            self.backend.style_tables('x')
        self.assertEqual(ai.calls, [])

    def test_demo(self):
        self.backend.ai_client = DemoAiClient()
        plan = self.backend.style_tables([summary()])['plans'][0]
        self.assertEqual(plan['purpose'], 'expense')

    def test_table_prefs_are_checked(self):
        prefs = self.backend.set_prefs({'tables': {'theme': 'report', 'headAlign': 'center', 'dateAlign': 'up', 'keepColors': 'yes', 'x': 1}})
        self.assertEqual(prefs['tables'], {'theme': 'report', 'headAlign': 'center'})
        self.assertEqual(self.backend.set_prefs({'tables': {}})['tables'], {})  # 恢复默认.


class PlainTextTest(unittest.TestCase):
    def test_cells_are_separated(self):
        html = '<p>Hi</p><table><tr><td>No.</td><td>Amount</td></tr><tr><td>1</td><td><p>53.91</p></td></tr></table><p>Bye</p>'
        self.assertEqual(html_to_text(html), 'Hi\nNo.\tAmount\n1\t53.91\nBye')  # Was 'No.Amount' and '153.91'.
        # Paragraphs inside one cell keep their line break; only the cell's first one goes without.
        self.assertEqual(html_to_text('<table><tr><td><p>First</p><p>Second</p></td><td>x</td></tr></table>'), 'First\nSecond\tx')


class WiringTest(unittest.TestCase):
    def test_bridge(self):
        from coremail_desktop.app import shell
        self.assertEqual(shell.BACKEND_METHODS['styleTables'], 'style_tables')
        self.assertIn('styleTables', shell.AI_METHODS)
