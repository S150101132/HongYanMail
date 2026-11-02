"""粘贴 Markdown (web/markdown.js): detection and conversion run in node when it is installed;
the compose wiring is checked from the sources."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / 'coremail_desktop' / 'app' / 'web'
NODE = shutil.which('node')

SAMPLE = '''# 周报

本周 **进展** 和 *风险*，见 [看板](https://example.com/a_b?x=1&y=2)。

## 事项
- 完成 `LDAP` 对接
  - 子项 snake_case_name
- [x] 已提交
1. 第一
2. 第二

| 模块 | 负责人 | 进度 |
| :--- | :---: | ---: |
| 网关 | 张三 | 80% |

> 引用一行
> 第二行

```python
if a < b:
    print("<hi>")
```

---
https://example.com/plain 结尾'''

CASES = r'''
const vm = require('vm'), fs = require('fs');
const box = {};
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8') + ';this.mdLooksLike = mdLooksLike; this.mdToHtml = mdToHtml;', box);
const sample = SAMPLE;
console.log(JSON.stringify({
  html: box.mdToHtml(sample),
  looks: [sample, '1. 报价\n2. 交期\n3. 付款', '普通的一句话', '请看 **这里**', '- a\n- b', 'x = `y`', '<script>alert(1)</script>'].map(box.mdLooksLike),
  xss: box.mdToHtml('[x](javascript:alert(1)) <img src=x onerror=1> **<b>**'),
}));
'''


@unittest.skipUnless(NODE, 'node is not installed')
class ConvertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = 'const SAMPLE = ' + json.dumps(SAMPLE) + ';' + CASES
        out = subprocess.run([NODE, '-', str(WEB / 'markdown.js')], input=code, capture_output=True, text=True, encoding='utf-8', check=True)
        cls.r = json.loads(out.stdout)

    def test_detection(self):
        self.assertEqual(self.r['looks'], [True, False, False, True, False, True, False])

    def test_blocks(self):
        h = self.r['html']
        self.assertIn('>周报</h1>', h)
        self.assertIn('<b>进展</b>', h)
        self.assertIn('<i>风险</i>', h)
        self.assertIn('<a href="https://example.com/a_b?x=1&amp;y=2">看板</a>', h)
        self.assertIn('<code style=', h)
        self.assertRegex(h, r'<ul[^>]*><li>完成 <code[^>]*>LDAP</code> 对接<ul[^>]*><li>子项 snake_case_name</li></ul></li><li>☑ 已提交</li></ul><ol[^>]*><li>第一</li><li>第二</li></ol>')
        self.assertRegex(h, r'<th style="[^"]*text-align:center[^"]*">负责人</th>')
        self.assertRegex(h, r'<td style="[^"]*text-align:right">80%</td>')
        self.assertRegex(h, r'<blockquote[^>]*><p[^>]*>引用一行<br>第二行</p></blockquote>')
        self.assertIn('if a &lt; b:\n    print(&quot;&lt;hi&gt;&quot;)</code></pre>', h)
        self.assertIn('<hr style=', h)
        self.assertIn('<a href="https://example.com/plain">https://example.com/plain</a> 结尾', h)

    def test_styles_are_inline_for_the_recipient(self):
        # No classes: the recipient's mail client has none of our CSS.
        self.assertNotIn('class=', self.r['html'])
        self.assertIn('border:1px solid', self.r['html'])

    def test_no_markup_or_script_links_get_through(self):
        x = self.r['xss']
        self.assertNotIn('<img', x)
        self.assertNotIn('href="javascript', x)
        self.assertIn('&lt;b&gt;', x)


class WiringTest(unittest.TestCase):
    def test_compose_paste_uses_it(self):
        app = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn('mdLooksLike(plain)', app)
        self.assertIn("insertHTML', false, mdToHtml(plain)", app)
        self.assertIn("types.includes('vscode-editor-data')", app)
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertLess(html.index('markdown.js'), html.index('src="app.js"'))
