'use strict';
/* 鸿雁 · 邮件表格 (T2): writes an analysis onto a table — each cell's style is rebuilt from the theme,
   the row's role, the column's alignment and width, the source's meaningful formatting and the
   user's own choices, in that order. Only the style attribute and data-hy-* change: cell content
   is never touched, so the text and the merges stay exactly as pasted. */

// an: {roles, cols: [{type, align, wrap}], widths, overflow, theme, keepColors, headAlign, emphasis: Set}.
function tbRender(table, plain, an) {
  const t = TB_THEMES[an.theme] || TB_THEMES.business, roles = an.roles;
  const framed = roles.map((role, r) => r).filter((r) => !['title', 'note'].includes(roles[r]));
  const first = framed[0] ?? 0, last = framed[framed.length - 1] ?? plain.nRows - 1;
  const hasText = plain.cells.some((x) => x.text);
  // A header band of one grey fill is the theme's job; a coloured one is the user's.
  const headCells = plain.cells.filter((x) => roles[x.r] === 'head');
  const bands = new Set(headCells.map((x) => x.src.bg || ''));
  const keepHeadBand = an.keepColors && bands.size === 1 && !tbNeutral([...bands][0]);
  // Grey stripes over whole rows (an Excel table style) are zebra, not meaning.
  const dataRows = roles.map((role, r) => r).filter((r) => roles[r] === 'data');
  const striped = dataRows.filter((r) => {
    const xs = plain.cells.filter((x) => x.r === r), greys = xs.filter((x) => x.src.bg && tbNeutral(x.src.bg));
    return xs.length && greys.length >= xs.length / 2 && greys.every((x) => x.src.bg === greys[0].src.bg);  // A status colour may sit in it.
  });
  const zebraSource = striped.length >= Math.max(2, dataRows.length * 0.3);
  const longRows = new Set(plain.cells.filter((x) => tbVisualLength(x.text) > 40 || x.text.includes('\n')).map((x) => x.r));
  const widthOf = (x) => (an.widths ? an.widths.slice(x.c, x.c + x.cs).reduce((n, w) => n + w, 0) : 0);

  table.dataset.hyTable = '1'; table.dataset.hyLook = an.theme;
  for (const name of ['width', 'border', 'cellpadding', 'cellspacing', 'align', 'bgcolor']) table.removeAttribute(name);
  table.setAttribute('style', ['border-collapse:collapse', 'border-spacing:0', 'margin:8px 0',
    ...(an.widths ? [`width:${an.total}px`, ...(an.overflow ? [] : ['max-width:100%'])] : [])].join(';'));
  Array.from(table.rows).forEach((tr, r) => {
    for (const name of ['height', 'style', 'bgcolor', 'align', 'valign']) tr.removeAttribute(name);
    tr.dataset.hyRow = roles[r];
  });
  const dataIndex = new Map(dataRows.map((r, i) => [r, i]));
  for (const x of plain.cells) {
    const el = x.el, role = roles[x.r], col = an.cols[x.c] || {}, end = x.r + x.rs - 1, s = [];
    const blank = role === 'blank' && hasText;
    const py = blank ? 2 : role === 'title' ? 4 : t.padY;
    s.push(`padding:${py}px ${role === 'title' ? 0 : t.padX}px`);
    s.push(`vertical-align:${longRows.has(x.r) ? 'top' : 'middle'}`, 'line-height:1.5');
    // Alignment: the user's > the source's > the column's; a heading over several columns is centred.
    let align = col.align || 'left';
    if (role === 'head') align = x.cs > 1 ? 'center' : an.headAlign === 'left' || an.headAlign === 'center' ? an.headAlign : align;
    else if (['title', 'note', 'group'].includes(role) || x.cs > 1) align = role === 'group' || role === 'title' || role === 'note' ? 'left' : align;
    if (x.src.al && role !== 'head') align = x.src.al;
    if (el.dataset.hyAl) align = el.dataset.hyAl;
    s.push(`text-align:${align}`);
    // Chromium's editing drops white-space from pasted styles; the old attribute stays (and Outlook reads it).
    const nowrap = !col.wrap && x.cs === 1 && ['data', 'subtotal', 'total'].includes(role);
    const w = widthOf(x);
    if (w && !['title', 'note'].includes(role)) s.push(`width:${w - 2 * t.padX - 1}px`);
    const h = an.rowHeights && x.rs === 1 ? an.rowHeights[x.r] : 0;  // Dragged by the user (its content can still make it taller).
    if (h) s.push(`height:${Math.max(0, h - 2 * py - 1)}px`);
    // Lines: the frame, under the header, between rows, over a total.
    let top = '', bottom = '';
    if (x.r === first) top = t.top;
    else if (role === 'total' || role === 'subtotal') top = t.total.line;
    if (end >= last && x.r <= last) bottom = t.bottom;
    else if (role === 'head') bottom = roles[end + 1] === 'head' ? (t.rule || '1px solid #eaecf0') : t.head.line;
    else if (['data', 'group', 'subtotal', 'blank'].includes(role) && x.r >= first && x.r < last) bottom = t.rule;
    if (top) s.push(`border-top:${top}`);
    if (bottom) s.push(`border-bottom:${bottom}`);
    // Colour and weight: the role's, then what the source meant, then the user's fill.
    let bg = '', fg = '', bold = false;
    if (role === 'head') {
      bg = t.head.bg; fg = t.head.fg; bold = true;
      // A coloured band, or one header cell the user marked, keeps its colours; a grey band is the theme's.
      if (keepHeadBand || (an.keepColors && x.src.bg && !tbNeutral(x.src.bg))) bg = x.src.bg;
      if (an.keepColors && x.src.fg) fg = x.src.fg;
    }
    else if (role === 'total') { bg = t.total.bg; bold = true; }
    else if (role === 'subtotal') bold = true;
    else if (role === 'group') { bg = t.group.bg; fg = t.group.fg; bold = true; }
    else if (role === 'title') bold = true;
    else if (role === 'note') fg = t.note;
    else if (role === 'data' && t.zebra && dataIndex.get(x.r) % 2 === 1) bg = t.zebra;
    if (an.keepColors && role !== 'head') {
      if (x.src.bg && !(zebraSource && tbNeutral(x.src.bg))) bg = x.src.bg;
      if (x.src.fg) fg = x.src.fg;
    }
    if (x.src.b || (an.emphasis && an.emphasis.has(x.c) && role === 'data')) bold = true;
    if (el.dataset.hyFill) bg = el.dataset.hyFill === 'none' ? '' : el.dataset.hyFill;
    if (bg && !fg) fg = t.head.fg || '#1d2939';  // Every fill is light: dark text on it, also in the dark editor.
    if (bg) s.push(`background-color:${bg}`);
    if (fg) s.push(`color:${fg}`);
    if (bold) s.push('font-weight:600');
    if (x.src.i) s.push('font-style:italic');
    if (x.src.u || x.src.s) s.push(`text-decoration:${[x.src.u && 'underline', x.src.s && 'line-through'].filter(Boolean).join(' ')}`);
    if (role === 'note') s.push('font-size:0.92em');
    if (role === 'title') s.push('font-size:1.05em');
    if (['number', 'money', 'percent', 'quantity', 'index'].includes(col.type) && role !== 'head') s.push('font-variant-numeric:tabular-nums');
    for (const name of ['width', 'height', 'align', 'valign', 'bgcolor', 'nowrap', 'class']) el.removeAttribute(name);
    if (nowrap) el.setAttribute('nowrap', '');
    el.setAttribute('style', s.join(';'));
  }
}
