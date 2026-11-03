"""T2 表格 (web/table/types.js, structure.js, layout.js): cell and column types, row roles and
column widths, run in node when it is installed — no page needed."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

TABLE = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web' / 'table'
NODE = shutil.which('node')

LOAD = r'''
const vm = require('vm'), fs = require('fs'), path = require('path');
const box = {}; vm.createContext(box);
for (const f of ['model', 'types', 'structure', 'layout']) vm.runInContext(fs.readFileSync(path.join(process.argv[2], f + '.js'), 'utf8'), box);
const run = (code) => vm.runInContext(code, box);
const CASES = JSON.parse(fs.readFileSync(0, 'utf8'));
const analyse = (rows) => run(`(() => { const p = tbPlain(${JSON.stringify(rows)}); const ct = tbCellTypes(p); const s = tbRoles(p, ct);
  const cols = tbColumnTypes(p, ct, s.roles); const m = (t, b) => tbVisualLength(t) * 7;
  const w = tbWidths(p, s.roles, cols.map((c) => ({ wrap: tbColumnWraps(c) })), m, 640, tbPickTheme(p, 'auto'));
  return { roles: s.roles.join(' '), types: cols.map((c) => c.type), align: cols.map((c) => tbColumnAlign(c)), widths: w.widths, total: w.total, overflow: w.overflow }; })()`);
console.log(JSON.stringify({
  cells: CASES.cells.map(([text, hint]) => run(`tbCellType(${JSON.stringify(text)}, ${JSON.stringify(hint || {})})`)),
  formats: CASES.formats.map((f) => run(`tbFormatKind(${JSON.stringify(f)})`)),
  tables: Object.fromEntries(Object.entries(CASES.tables).map(([k, rows]) => [k, analyse(rows)])),
}));
'''

CELLS = [('', 'empty'), ('N/A', 'missing'), ('—', 'missing'), ('待定', 'missing'), ('1,234.50', 'decimal'), ('-20', 'int'), ('(300)', 'int'),
         ('15%', 'percent'), ('SAR 2,500', 'money'), ('¥1,200', 'money'), ('3,250 元', 'money'), ('23 台', 'quantity'), ('7台', 'quantity'),
         ('2026/10/12', 'date'), ('2026-11-30', 'date'), ('10月12日', 'date'), ('12 Mar 2026', 'date'), ('14:30', 'time'), ('2026-10-12 14:30', 'datetime'),
         ('00123', 'id'), ('138 0013 8001', 'id'), ('+966 50 123 4567', 'id'), ('NE40E-X8', 'id'), ('INC-2026-0412', 'id'), ('20260712345', 'id'),
         ('1234.5', 'decimal'), ('处理中', 'text'), ('现场工程师使用 VPN 接入客户网络后约 20 分钟自动断开，重新拨号后恢复，需要排查。', 'longtext'),
         ('45000', 'date', {'num': True, 'fmt': 'yyyy\\/m\\/d'}), ('00123', 'id', {'fmt': '@'}), ('0.15', 'percent', {'num': True, 'fmt': '0%'}),
         ('123456789', 'int', {'num': True, 'fmt': '#,##0'})]
FORMATS = [('General', ''), ('@', 'text'), ('0%', 'percent'), ('"\\#\\,\\#\\#0\\.00"', 'number'), ('yyyy\\/m\\/d', 'date'), ('Short Date', 'date'),
           ('[$¥-804]#,##0.00', 'money'), ('h:mm', 'time'), ('Percent', 'percent')]
TABLES = {
    'fees': [['No.', '费用日期', '费用（SAR）', '费用（USD）', '费用（RMB）'], ['1', '2026/10/22', '53.91', '', '97.038'], ['2', '2026/10/22', '56.42', '', '101.556'],
             ['3', '2026/10/26', '90', '', '162'], ['4', '2026/10/17', '', '23', '161'], ['5', '', '', '', ''], ['', '', '200.33', '23', '521.594']],
    'noheader': [['张三', '北京', '销售部'], ['李四', '上海', '市场部'], ['王五', '广州', '技术部']],
    'grouped': [[{'text': '2026 年 10 月费用汇总', 'cs': 5}], [{'text': '序号', 'rs': 2}, {'text': '项目', 'rs': 2}, {'text': '费用', 'cs': 2}, {'text': '备注', 'rs': 2}],
                ['SAR', 'USD'], ['1', '住宿', 'SAR 2,500', 'N/A', '客户现场附近酒店'], ['2', '机票', 'SAR 1,200', '320', ''], ['合计', '', 'SAR 3,700', '320', ''],
                [{'text': '注：汇率按 10 月 31 日计算', 'cs': 5}]],
    'sections': [['区域', '城市', '销售额', '占比'], ['华东', '', '', ''], ['', '上海', '1,234.50', '30%'], ['', '杭州', '980.00', '20%'],
                 ['华南', '', '', ''], ['', '广州', '2,000.00', '50%'], ['总计', '', '4,214.50', '100%']],
    'status': [['设备编号', '数量', '完成率', '电话', '状态'], ['00123', '23 台', '15%', '138 1234 5678', '正常'], ['00124', '5 台', '100%', '139-1111-2222', '异常'],
               ['00125', '12 台', 'N/A', '+966 50 123 4567', '正常'], ['00126', '7台', '80%', '0551-1234567', '待处理']],
    'long': [['问题', '描述', '负责人'], ['登录失败', '用户在使用 VPN 连接后无法登录系统，提示证书错误，已经尝试重新安装客户端但问题依旧存在，需要进一步排查网关配置和证书链。', '张三'],
             ['速度慢', '晚高峰时段访问速度明显下降。', '李四'], ['数据丢失', '部分历史记录在迁移后无法查到，怀疑迁移脚本遗漏了 2023 年之前的数据。', '王五']],
    'wide': [[f'Column header {i + 1}' for i in range(14)]] + [[str((r + 1) * (i + 1) * 1234.5) for i in range(14)] for r in range(5)],
    'empty': [['', ''], ['', '']],
}


@unittest.skipUnless(NODE, 'node is not installed')
class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cases = {'cells': [[c[0], c[2] if len(c) > 2 else {}] for c in CELLS], 'formats': [f for f, _ in FORMATS], 'tables': TABLES}
        out = subprocess.run([NODE, '-e', LOAD, '-', str(TABLE)], input=json.dumps(cases, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8', check=True)
        cls.r = json.loads(out.stdout)

    def test_cell_types(self):
        self.assertEqual(self.r['cells'], [c[1] for c in CELLS])

    def test_excel_number_formats(self):
        self.assertEqual(self.r['formats'], [k for _, k in FORMATS])

    def test_roles(self):
        t = self.r['tables']
        self.assertEqual(t['fees']['roles'], 'head data data data data data total')  # The unlabelled last row with sums.
        self.assertEqual(t['noheader']['roles'], 'data data data')  # Names over names: no header.
        self.assertEqual(t['grouped']['roles'], 'title head head data data total note')
        self.assertEqual(t['sections']['roles'], 'head group data data group data total')
        self.assertEqual(t['long']['roles'], 'head data data data')  # Short words over sentences.

    def test_column_types_and_alignment(self):
        t = self.r['tables']
        self.assertEqual(t['fees']['types'], ['index', 'date', 'money', 'money', 'money'])
        self.assertEqual(t['fees']['align'], ['center', 'center', 'right', 'right', 'right'])
        self.assertEqual(t['grouped']['types'], ['index', 'text', 'money', 'money', 'text'])  # N/A does not break the column; USD under 费用 is money.
        self.assertEqual(t['status']['types'], ['id', 'quantity', 'percent', 'id', 'status'])  # Leading zeros and phones are not numbers.
        self.assertEqual(t['noheader']['types'], ['text', 'text', 'text'])  # Distinct names are not a status.
        self.assertEqual(t['long']['types'], ['text', 'longtext', 'text'])

    def test_widths(self):
        t = self.r['tables']
        for name in ('fees', 'grouped', 'sections', 'status', 'long'):
            self.assertLessEqual(t[name]['total'], 640, name)
            self.assertFalse(t[name]['overflow'], name)
        long = t['long']['widths']
        self.assertLessEqual(long[1], 360)  # The long column is capped, so it cannot swallow the table,
        self.assertGreater(long[1], max(long[0], long[2]))  # but it gets the most room.
        self.assertTrue(t['wide']['overflow'])  # Too many columns: kept readable, not squeezed.
        self.assertGreater(t['wide']['total'], 640)
        self.assertIsNone(t['empty']['widths'])  # Nothing to measure: no widths.
