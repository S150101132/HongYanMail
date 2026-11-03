'use strict';
/* 鸿雁 · 邮件表格 (T2): clipboard HTML from Excel / WPS / Word / Google Sheets / a web page, made fit
   to insert. Per source, as CKEditor's paste-from-office does: Chromium drops <style> on paste, so
   its rules are inlined first; Excel's number formats (mso-number-format, which CSSOM forgets) and
   x:num are read before that. Then each table cell keeps what the user meant — fills, text colours,
   bold, italic, explicit alignment — as data-hy-src, and the rest of the spreadsheet's formatting
   (fonts, sizes, fixed widths, borders, mso-*) goes; the theme draws those. Cell content stays. */

function tbSourceOf(html) {
  if (/content=["']?Microsoft Excel|urn:schemas-microsoft-com:office:excel|ProgId content=["']?Excel/i.test(html)) return 'excel';
  if (/content=["']?(?:Et\.|WPS|Kingsoft)|ProgId content=["']?Et\b|class=["']?et\d/i.test(html)) return 'wps';
  if (/content=["']?Microsoft Word|urn:schemas-microsoft-com:office:word/i.test(html)) return 'word';
  if (/google-sheets-html-origin|data-sheets-value/i.test(html)) return 'sheets';
  return 'web';
}

// mso-number-format by class, read from the raw <style> text.
function tbNumberFormats(cssText) {
  const formats = new Map();
  for (const [, selectors, body] of cssText.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    const m = body.match(/mso-number-format\s*:\s*(?:"([^"]*)"|'([^']*)'|([^;]+))/i);
    if (!m) continue;
    const fmt = (m[1] ?? m[2] ?? m[3]).trim();
    for (const sel of selectors.split(',')) { const cls = sel.trim().match(/\.([\w-]+)\s*$/); if (cls) formats.set(cls[1], fmt); }
  }
  return formats;
}
const tbStyleFormat = (style) => { const m = String(style || '').match(/mso-number-format\s*:\s*(?:"([^"]*)"|'([^']*)'|([^;]+))/i); return m ? (m[1] ?? m[2] ?? m[3]).trim() : ''; };

// CSS colour -> '#rrggbb', or '' for none / transparent / unreadable.
function tbHex(color) {
  const c = String(color || '').trim().toLowerCase();
  if (!c || c === 'transparent' || c === 'none' || c === 'initial' || c === 'inherit') return '';
  let m = c.match(/^#([0-9a-f]{3})$/);
  if (m) return '#' + m[1].split('').map((x) => x + x).join('');
  if (/^#[0-9a-f]{6}$/.test(c)) return c;
  m = c.match(/^rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+))?/);
  if (m) return m[4] !== undefined && +m[4] === 0 ? '' : '#' + [m[1], m[2], m[3]].map((x) => (+x).toString(16).padStart(2, '0')).join('');
  const named = { black: '#000000', white: '#ffffff', red: '#ff0000', green: '#008000', blue: '#0000ff', yellow: '#ffff00', gray: '#808080', grey: '#808080', silver: '#c0c0c0', windowtext: '#000000' };
  return named[c] || '';
}
// A grey (black, white and the greys between): environment, not meaning.
const tbNeutral = (hex) => { if (!hex) return true; const v = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)); return Math.max(...v) - Math.min(...v) < 26; };

// What a cell's own formatting says, before it is cleared.
function tbReadSrc(el) {
  const st = el.style, src = tbParseSrc(el.dataset.hySrc);
  const bg = tbHex(st.backgroundColor) || tbHex(el.getAttribute('bgcolor'));
  if (bg && bg !== '#ffffff') src.bg = bg;
  const fg = tbHex(st.color);
  if (fg && !tbNeutral(fg)) src.fg = fg;
  const weight = st.fontWeight;
  if (weight === 'bold' || +weight >= 600 || (el.children.length === 1 && /^(B|STRONG)$/.test(el.firstElementChild.tagName) && el.textContent.trim() === el.firstElementChild.textContent.trim())) src.b = true;
  if (st.fontStyle === 'italic') src.i = true;
  if (/underline/.test(st.textDecoration || st.textDecorationLine)) src.u = true;
  if (/line-through/.test(st.textDecoration || st.textDecorationLine)) src.s = true;
  if (['center', 'right', 'left'].includes(st.textAlign) && el.dataset.hyAlignClass === '1') src.al = st.textAlign;
  if (el.tagName === 'TH') src.th = true;
  return src;
}
// Keep a cell's content, lose the spreadsheet's look of it.
function tbCleanCell(el) {
  el.dataset.hySrc = tbSerializeSrc(tbReadSrc(el));
  delete el.dataset.hyAlignClass;
  for (const name of Array.from(el.attributes, (a) => a.name)) if (!/^(colspan|rowspan|data-hy-|data-sheets-value)/.test(name)) el.removeAttribute(name);
  el.removeAttribute('data-sheets-value');
  el.querySelectorAll('*').forEach((n) => {
    n.removeAttribute('class'); n.removeAttribute('lang');
    if (!n.style || n.tagName === 'IMG') return;  // A picture keeps its size (a logo in a signature table).
    ['font-family', 'font-size', 'line-height', 'margin', 'margin-top', 'margin-bottom', 'width', 'height', 'background', 'background-color'].forEach((p) => n.style.removeProperty(p));
    if (n.tagName === 'P') n.style.margin = '0';
    if (/^(black|rgb\(0, 0, 0\)|#000000|#000|windowtext)$/i.test(n.style.color)) n.style.removeProperty('color');
    if (!n.getAttribute('style')) n.removeAttribute('style');
  });
}
function tbCleanTable(table) {
  for (const name of Array.from(table.attributes, (a) => a.name)) if (!name.startsWith('data-hy-')) table.removeAttribute(name);
  table.dataset.hyTable = '1';
  table.querySelectorAll('colgroup,col').forEach((c) => c.remove());
  Array.from(table.rows).forEach((tr) => { for (const name of Array.from(tr.attributes, (a) => a.name)) if (!name.startsWith('data-hy-')) tr.removeAttribute(name); });
  Array.from(table.rows).forEach((tr) => Array.from(tr.cells).forEach(tbCleanCell));
}

// Clipboard HTML -> {doc, source} with <style> inlined and tables cleaned; null when there is
// neither a <style> nor a table (an ordinary paste does).
function tbReadClipboard(html) {
  if (!/<style[\s>]|<table[\s>]/i.test(html)) return null;
  const source = tbSourceOf(html);
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const cssText = $$('style', doc).map((s) => s.textContent.replace(/<!--|-->/g, '')).join('\n');
  const formats = tbNumberFormats(cssText);
  // Before inlining: Excel's own facts about each cell, and which alignments the user set.
  const alignClasses = new Set([...cssText.matchAll(/([^{}]+)\{[^}]*text-align\s*:\s*(?:center|right|left)[^}]*\}/gi)]
    .flatMap(([, sel]) => sel.split(',').map((x) => (x.trim().match(/\.([\w-]+)\s*$/) || [])[1]).filter(Boolean)));
  $$('td,th', doc.body).forEach((el) => {
    const cls = (el.getAttribute('class') || '').split(/\s+/).filter(Boolean);
    const fmt = tbStyleFormat(el.getAttribute('style')) || cls.map((c) => formats.get(c)).find(Boolean) || '';
    if (fmt) el.dataset.hyFmt = fmt;
    if (el.hasAttribute('x:num') || /"3":/.test(el.getAttribute('data-sheets-value') || '')) el.dataset.hyNum = '1';
    if (el.getAttribute('x:str') !== null) el.dataset.hyFmt = '@';
    if (cls.some((c) => alignClasses.has(c)) || /text-align/i.test(el.getAttribute('style') || '')) el.dataset.hyAlignClass = '1';
  });
  if (cssText.trim()) {
    const sheet = new CSSStyleSheet();
    try { sheet.replaceSync(cssText.replace(/windowtext/gi, '#000')); } catch { return null; }
    const weight = (sel) => Math.max(...sel.split(',').map((one) => (one.match(/#/g) || []).length * 100 +
      (one.match(/[.[:]/g) || []).length * 10 + (one.match(/(^|[\s>+~])[a-z]/gi) || []).length));
    const rules = Array.from(sheet.cssRules).filter((r) => r instanceof CSSStyleRule && r.style.cssText)
      .map((r, n) => ({ r, n, w: weight(r.selectorText) })).sort((a, b) => a.w - b.w || a.n - b.n);
    const css = new Map();
    rules.forEach(({ r }) => { try { $$(r.selectorText, doc.body).forEach((el) => css.set(el, (css.get(el) || '') + r.style.cssText)); } catch { /* A selector Chromium does not take. */ } });
    css.forEach((rule, el) => el.setAttribute('style', rule + (el.getAttribute('style') || '')));
  }
  $$('style', doc.body).forEach((s) => s.remove());
  $$('table', doc.body).filter((t) => !t.parentElement.closest('table')).forEach(tbCleanTable);
  $$('[class]', doc.body).forEach((el) => el.removeAttribute('class'));
  return { doc, source };
}
// The part of the clipboard document to insert. Excel marks the fragment inside the table (after
// its <col>s): cutting there would leave bare rows, which go in as text — then the whole body is taken.
function tbFragment(doc) {
  const body = doc.body.innerHTML, frag = body.match(/<!--StartFragment-->([\s\S]*)<!--EndFragment-->/);
  const whole = frag && !/^\s*<(\/|(tr|td|th|col|colgroup|tbody|thead|tfoot|caption)[\s>])/i.test(frag[1]) ? frag[1] : body;
  return whole.replace(/<!--(Start|End)Fragment-->/g, '');
}
