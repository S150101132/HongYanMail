"""术语统一 (L1): the code says what GLOSSARY.md says — the old names of 知会 / 要我做 / 在等别人 are
gone from the source, and the AI's 在等别人 tool speaks of the waiting mail, not of 催办."""
import json
import unittest
from pathlib import Path

from coremail_desktop.ai.tools import AssistantTools

SOURCE = Path(__file__).resolve().parent.parent / 'coremail_desktop'
OLD = ('要我知道', '待我处理', '等回复')  # GLOSSARY.md _避免_ words that never mean anything else
KEEP = '旧叫法也能搜到'  # a line marked so (the 命令框's search aliases) may keep them


class GlossaryTest(unittest.TestCase):
    def test_the_old_names_are_gone_from_the_source(self):
        found = []
        for path in sorted(SOURCE.rglob('*')):
            if path.suffix not in ('.py', '.js', '.html', '.css'):
                continue
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if KEEP not in line:
                    found += [f'{path.relative_to(SOURCE)}:{n} {word}' for word in OLD if word in line]
        self.assertEqual(found, [])

    def test_the_waiting_mail_tool_says_waiting_on_others(self):
        row = {'id': 's1', 'subject': '报价', 'toNames': '张三', 'date': '2026-11-01 09:00', 'waiting': 4}
        tools = AssistantTools(lambda *a: ([], 0), lambda *a: None, lambda n: [], {}, tasks=lambda v, q: [], task=None,
                               mine=lambda t: True, followups=lambda: [row])
        spec = next(s['function'] for s in tools.specs if s['function']['name'] == 'followups')
        self.assertTrue(spec['description'].startswith('在等别人'))
        result, label = tools.run('followups', json.dumps({}))
        self.assertEqual((result['total'], label), (1, '在等别人：1 封'))


if __name__ == '__main__':
    unittest.main()
