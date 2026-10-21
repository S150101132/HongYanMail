"""统一命令入口 (web/commands.js): the matching and the plain-words search conditions run
in node when it is installed; the page wiring is checked from the sources."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
NODE = shutil.which('node')

CASES = r'''
const vm = require('vm'), fs = require('fs');
const box = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8') + ';this.cmdParse = cmdParse; this.cmdScore = cmdScore; this.cmdIsQuestion = cmdIsQuestion;', box);
const today = new Date(2026, 9, 21);  // A Wednesday.
const parse = (t) => box.cmdParse(t, today);
console.log(JSON.stringify({
  parse: ['张三发的 上周 带附件的报价', '近 3 天 未读', '最近两周来自李四的周报', '上个月 星标', '找一下所有关于预算的邮件', '今天', '仅主题 合同']
    .map((t) => { const p = parse(t); return [p.filters, p.words, p.labels]; }),
  score: [box.cmdScore(['写邮件', '新邮件'], '写邮件'), box.cmdScore(['写邮件'], '写邮'), box.cmdScore(['设置'], '打开设置'),
          box.cmdScore(['导出为 PDF'], 'pdf'), box.cmdScore(['写邮件'], 'xyz'), box.cmdScore(['收信'], '')],
  question: ['预算批了吗', '谁负责 LDAP？', '收信', '张三 上周'].map(box.cmdIsQuestion),
}));
'''


@unittest.skipUnless(NODE, 'node is not installed')
class MatchingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([NODE, '-', str(WEB / 'commands.js')], input=CASES, capture_output=True, text=True, encoding='utf-8', check=True)
        cls.r = json.loads(out.stdout)

    def test_search_conditions_from_plain_words(self):
        p = self.r['parse']
        self.assertEqual(p[0][:2], [{'attachment': True, 'since': '2026-10-12', 'until': '2026-10-18', 'from': '张三'}, '报价'])
        self.assertEqual(p[0][2], ['有附件', '上周', '发件人：张三'])
        self.assertEqual(p[1][:2], [{'unread': True, 'since': '2026-10-18'}, ''])
        self.assertEqual(p[1][2], ['未读', '近 3 天'])
        self.assertEqual(p[2][:2], [{'since': '2026-10-07', 'from': '李四'}, '周报'])
        self.assertEqual(p[3][:2], [{'flagged': True, 'since': '2026-09-01', 'until': '2026-09-30'}, ''])
        self.assertEqual(p[4], [{}, '预算', []])  # Filler words go; nothing is guessed.
        self.assertEqual(p[5][0], {'since': '2026-10-21'})
        self.assertEqual(p[6][:2], [{'scope': 'subject'}, '合同'])

    def test_scores(self):
        exact, prefix, inside, sub, none, empty = self.r['score']
        self.assertEqual(exact, 100)
        self.assertGreaterEqual(prefix, 75)
        self.assertGreaterEqual(inside, 50)  # "打开设置" finds 设置.
        self.assertGreaterEqual(sub, 60)
        self.assertEqual((none, empty), (0, 0))

    def test_questions_go_to_the_ai(self):
        self.assertEqual(self.r['question'], [True, True, False, False])


class WiringTest(unittest.TestCase):
    def test_page(self):
        page = (WEB / 'index.html').read_text(encoding='utf-8')
        source = (WEB / 'commands.js').read_text(encoding='utf-8')
        self.assertIn('<script src="commands.js"></script>', page)
        self.assertLess(page.index('src="tasks.js"'), page.index('src="commands.js"'))  # Uses runCommand, showAsk, MG.
        self.assertIn('id="palette"', page)
        self.assertIn("CP.open()", (WEB / 'app.js').read_text(encoding='utf-8'))  # Ctrl+K
        # The reader's and the selection's buttons become commands as they stand, so every
        # button there has a command without a list to keep in step.
        self.assertIn("#reader-body .reader-toolbar [data-act]", source)
        self.assertIn("#reader-multi [data-batch]", source)
        for name in ('showAsk(q)', 'runSearch(p.words)', 'runCommand()', 'selectFolder(f.id)', 'openSettings(b.dataset.tab)'):
            self.assertIn(name, source)
        for field in ('c.title', 'c.hint', 'c.group', 'c.key'):
            self.assertIn(f'esc({field}', source)  # Folder names and typed text are shown as text.


if __name__ == '__main__':
    unittest.main()
