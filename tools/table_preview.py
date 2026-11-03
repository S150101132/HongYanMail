"""T2 表格: a compose editor page with the real table scripts, for tests and for looking.

TablePage loads web/table/*.js and the app's style.css next to a #cmp-editor 680px wide (640px
for the table), with the app's other globals stubbed. main() renders every sample in
tests/fixtures/tables/ before (as the spreadsheet showed it) and after (as the mail will), side
by side, into OUTDIR (default build/table-preview/):

    python -m tools.table_preview [OUTDIR]
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'coremail_desktop' / 'app' / 'web'
FIXTURES = ROOT / 'tests' / 'fixtures' / 'tables'
SCRIPTS = ('model', 'types', 'structure', 'layout', 'source', 'render', 'mail', 'controller', 'resize')
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')  # Synthetic samples only; the sandbox fails on this machine.

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="style.css">
<style>body{{margin:0;padding:16px;background:#fff;color:#1f2328}} .cmp-editor{{width:680px;padding:0 20px;box-sizing:border-box}}
.cmp-editor table td,.cmp-editor table th{{outline:none}}</style></head>
<body><div id="cmp-editor" class="cmp-editor" contenteditable="true"></div>
<script>
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const S = {{ prefs: {prefs} }}, C = {{}}, toasts = [], toast = (text, opts) => toasts.push({{ text, action: opts && opts.action }}), fail = () => {{}}, api = async () => ({{}});
</script>
{scripts}
</body></html>'''


class TablePage:
    """A Qt WebEngine page with the table scripts loaded; run(js) -> the expression's value."""

    def __init__(self, prefs=None, show=False):
        from PySide6.QtCore import QUrl
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        self.view = QWebEngineView()
        self.view.resize(760, 900)
        if show:
            self.view.show()
        scripts = ''.join(f'<script src="table/{name}.js"></script>' for name in SCRIPTS)
        html = PAGE.format(prefs=json.dumps(prefs or {}), scripts=scripts)
        self._wait(lambda done: (self.view.loadFinished.connect(lambda ok: done(ok)), self.view.setHtml(html, QUrl.fromLocalFile(str(WEB) + '/'))))

    def _wait(self, start, timeout=20000):
        from PySide6.QtCore import QEventLoop, QTimer
        loop, out = QEventLoop(), []
        start(lambda value: (out.append(value), loop.quit()))
        QTimer.singleShot(timeout, loop.quit)
        loop.exec()
        if not out:
            raise TimeoutError('page did not answer')
        return out[0]

    def run(self, js):
        """js: an expression; its JSON-able value comes back."""
        text = self._wait(lambda done: self.view.page().runJavaScript(f'JSON.stringify((() => {{ return ({js}); }})())', 0, done))
        return json.loads(text) if text else None

    def grab(self, path, height):
        from PySide6.QtCore import QEventLoop, QTimer
        self.view.resize(760, max(200, min(height + 40, 4000)))
        loop = QEventLoop()
        QTimer.singleShot(400, loop.quit)
        loop.exec()
        self.view.grab().save(str(path))


# Paste a sample the way the editor does; what came in and what went out, for checks and pictures.
PASTE = '''(() => {
  const html = %s, manual = %s, ed = $('#cmp-editor');
  const shape = (root) => $$('table', root).filter((t) => !t.parentElement.closest('table'))
    .map((t) => Array.from(t.rows, (tr) => Array.from(tr.cells, (c) => [c.rowSpan, c.colSpan, c.textContent.replace(/\\s+/g, ' ').trim()])));
  const before = shape(new DOMParser().parseFromString(html, 'text/html'));
  ed.innerHTML = ''; ed.focus();
  const t0 = performance.now();
  let pasted = null;
  if (manual) { ed.innerHTML = html; tbFormat(ed.querySelector('table')); }
  else { pasted = tbPaste(html); document.execCommand('insertHTML', false, pasted.html); }
  const ms = performance.now() - t0;
  const table = ed.querySelector('table'), an = tbAnalyze(table, tbPlainFromTable(table));
  const again = manual ? '' : tbPaste(html).html;
  return { before, after: shape(ed), roles: Array.from(table.rows, (tr) => tr.dataset.hyRow).join(' '), types: an.cols.map((c) => c.type),
    theme: an.theme, overflow: !!table.closest('[data-hy-scroll]'), width: table.offsetWidth, height: ed.scrollHeight, ms,
    stable: manual || again.replace(/data-hy-paste="[^"]*"/g, '') === pasted.html.replace(/data-hy-paste="[^"]*"/g, ''), html: ed.innerHTML, mail: tbMailHtml(ed.innerHTML), id: pasted ? pasted.id : '', raw: pasted ? pasted.raw : '' };
})()'''


def paste(page, name):
    html = (FIXTURES / f'{name}.html').read_text(encoding='utf-8')
    manual = json.loads((FIXTURES / 'expected.json').read_text(encoding='utf-8'))[name].get('manual', False)
    return page.run(PASTE % (json.dumps(html), json.dumps(manual)))


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'build' / 'table-preview'
    out.mkdir(parents=True, exist_ok=True)
    from PySide6.QtCore import QUrl
    page = TablePage(show=True)
    names = sorted(p.stem for p in FIXTURES.glob('*.html'))
    for name in names:
        result = paste(page, name)
        page.grab(out / f'{name}-after.png', result['height'] + 32)
        html = (FIXTURES / f'{name}.html').read_text(encoding='utf-8')
        page._wait(lambda done: (page.view.loadFinished.connect(lambda ok: done(ok)), page.view.setHtml(html, QUrl('about:blank'))))
        page.grab(out / f'{name}-before.png', page.run('document.body.scrollHeight'))
        page.view.loadFinished.disconnect()
        page = TablePage(show=True)
        print(f'{name}: {result["roles"]} | {" ".join(result["types"])} | {result["theme"]} | {result["width"]}px | {result["ms"]:.0f} ms')
    print(f'pictures in {out}')


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
