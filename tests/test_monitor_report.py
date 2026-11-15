"""服务器巡检邮件（H1, web/monitor.js）: parsing and the status view run in node when it is installed;
the reading-pane wiring is checked from the sources. Sample text is the synthetic demo mail."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from coremail_desktop.app.demo import MONITOR_TEXT

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
NODE = shutil.which('node')

CASES = r'''
const vm = require('vm'), fs = require('fs');
const box = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8') + ';this.monParse = monParse; this.monitorReport = monitorReport;', box);
const subj = '[OT-MON] CHECK WARNING 2026-10-17 10:20:06';
const allOk = 'CURRENT STATUS\nCHECK   LEVEL   MESSAGE\ngr_members      OK      fine\nmonitor_self    OK      checker healthy\n';
console.log(JSON.stringify({
  parsed: box.monParse(subj, TEXT),
  html: box.monitorReport(subj, TEXT),
  ok: box.monitorReport('[OT-MON] CHECK OK 2026-10-18 10:00:00', allOk),
  other: [box.monitorReport('周报', TEXT), box.monitorReport(subj, '普通的一封信'), box.monitorReport(subj, '')],
  xss: box.monitorReport(subj, 'CURRENT STATUS\nweird<img/src=x/onerror=1>   CRITICAL   <script>alert(1)</script>\n'),
}));
'''


@unittest.skipUnless(NODE, 'node is not installed')
class MonitorReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = 'const TEXT = ' + json.dumps(MONITOR_TEXT) + ';' + CASES
        out = subprocess.run([NODE, '-', str(WEB / 'monitor.js')], input=code, capture_output=True, text=True, encoding='utf-8', check=True)
        cls.r = json.loads(out.stdout)

    def test_parse(self):
        p = self.r['parsed']
        self.assertEqual(p['level'], 'WARNING')
        self.assertEqual(p['changes'], [{'check': 'job_logs:minio_dr', 'level': 'UNKNOWN', 'message': 'no matching result line'}])
        self.assertEqual(len(p['rows']), 12)  # Disclaimer footer is not a row.
        self.assertIn({'check': 'remote_disk:root@10.0.1.12', 'level': 'WARNING', 'message': '/opt/oss 86%'}, p['rows'])

    def test_problems_first_and_plain_words(self):
        h = self.r['html']
        self.assertIn('2 项需要看', h)
        self.assertIn('共 12 项 · 正常 10 · 未知 1 · 警告 1', h)
        need = h[h.index('>需要看<'):h.index('>全部检查项<')]
        self.assertLess(need.index('远程磁盘'), need.index('MinIO 灾备同步'))  # Worse level first.
        self.assertIn('日志里没找到这次任务的结果', need)
        self.assertIn('和上一封相比有变化', h)
        self.assertIn('<b>86%</b>', h)
        self.assertIn('看原文', h)
        self.assertNotIn('Disclaimer', h)

    def test_groups_fold_when_all_ok(self):
        h = self.r['html']
        self.assertRegex(h, r'<details style="[^"]*"><summary[^>]*>.*?<b>数据库集群</b>')
        self.assertRegex(h, r'<details open style="[^"]*"><summary[^>]*>.*?<b>磁盘</b>')

    def test_all_ok(self):
        self.assertIn('全部正常', self.r['ok'])
        self.assertNotIn('>需要看<', self.r['ok'])

    def test_only_this_shape(self):
        self.assertEqual(self.r['other'], [None, None, None])

    def test_escapes(self):
        x = self.r['xss']
        self.assertNotIn('<script', x)
        self.assertNotIn('<img', x)
        self.assertIn('&lt;script&gt;', x)

    def test_reading_pane_uses_it(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn('monitorReport(m.subject, m.text)', app)
        self.assertIn('<script src="monitor.js"></script>', (WEB / 'index.html').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
