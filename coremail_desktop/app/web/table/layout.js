'use strict';
/* 鸿雁 · 邮件表格 (T2): themes, alignment and column widths. A theme is a flat set of values (Great
   Tables' options); the three presets only differ in them. Widths are pixels that survive Outlook:
   short values (numbers, dates, codes, states) get what they need on one line, long text shares
   the rest with a floor and a cap so one long cell cannot distort the table; a table too wide for
   the page keeps what it needs rather than being squeezed. Colours are hex: Outlook ignores rgba.
   Pure functions; text is measured by the caller's measure(text, bold) -> px. */

const TB_THEMES = {
  business: { label: '简洁商务', padY: 7, padX: 12, top: '1px solid #c4cad4', head: { bg: '#f2f4f7', fg: '#1d2939', line: '1px solid #c4cad4' },
    rule: '1px solid #eaecf0', bottom: '1px solid #c4cad4', zebra: '', total: { bg: '#f9fafb', line: '1px solid #98a2b3' }, group: { bg: '#f9fafb', fg: '#344054' }, note: '#667085' },
  compact: { label: '紧凑数据', padY: 4, padX: 8, top: '1px solid #c4cad4', head: { bg: '#f2f4f7', fg: '#1d2939', line: '1px solid #c4cad4' },
    rule: '', bottom: '1px solid #c4cad4', zebra: '#f9fafb', total: { bg: '#f2f4f7', line: '1px solid #98a2b3' }, group: { bg: '#eef1f5', fg: '#344054' }, note: '#667085' },
  report: { label: '正式报告', padY: 6, padX: 12, top: '2px solid #344054', head: { bg: '', fg: '', line: '1px solid #344054' },
    rule: '', bottom: '2px solid #344054', zebra: '', total: { bg: '', line: '1px solid #344054' }, group: { bg: '', fg: '#1d2939' }, note: '#667085' },
};
const TB_THEME_NAMES = Object.keys(TB_THEMES);
const TB_PREFS = { theme: 'auto', headAlign: 'column', dateAlign: 'center', keepColors: true };
const TB_MAX_WIDTH = 640, TB_MIN_WIDTH = 360, TB_TEXT_FLOOR = 120, TB_TEXT_CAP = 360, TB_WIDE_CAP = 480;

function tbPickTheme(plain, theme) {
  if (TB_THEMES[theme]) return theme;
  return plain.nRows > 20 || plain.nCols > 8 ? 'compact' : 'business';
}
const TB_NOWRAP = new Set(['number', 'money', 'percent', 'quantity', 'date', 'time', 'datetime', 'index', 'id', 'status']);
function tbColumnAlign(col, prefs = TB_PREFS) {
  if (['number', 'money', 'percent', 'quantity'].includes(col.type)) return 'right';
  if (['date', 'datetime', 'time'].includes(col.type)) return prefs.dateAlign || 'center';
  if (col.type === 'index' || col.type === 'status') return 'center';
  if (col.type === 'id') return col.maxLen <= 12 ? 'center' : 'left';
  return 'left';
}
// Short text (names, places) stays on one line too.
const tbColumnWraps = (col) => !(TB_NOWRAP.has(col.type) || col.type === 'empty' || ((col.type === 'text' || col.type === 'mixed') && col.maxLen <= 16));
const TB_SLACK = 6;  // Canvas and layout round text differently: without it short headings break.

// -> {widths: [px] | null, total, overflow}. cols: [{wrap, width?: 'narrow'|'wide'|''}] (width: the user's choice).
function tbWidths(plain, roles, cols, measure, available, theme) {
  const t = TB_THEMES[theme] || TB_THEMES.business, pad = 2 * t.padX + 2 + TB_SLACK;
  const W = Math.max(TB_MIN_WIDTH, Math.min(TB_MAX_WIDTH, available || TB_MAX_WIDTH));
  if (!plain.cells.some((x) => x.text)) return { widths: null, total: 0, overflow: false };
  const lines = (text) => text.split('\n');
  const tokens = (text) => text.split(/\s+|(?<=[⺀-鿿＀-￯，。、；：])|(?=[⺀-鿿＀-￯])/).filter(Boolean);
  const need = cols.map((col, c) => {
    if (col.px) return { min: col.px, max: col.px, wrap: false };  // Dragged by the user: exactly that.
    const body = plain.cells.filter((x) => x.c === c && x.cs === 1 && x.text && ['data', 'subtotal', 'total'].includes(roles[x.r]));
    const head = plain.cells.filter((x) => x.c === c && x.cs === 1 && x.text && roles[x.r] === 'head');
    const wide = (xs, bold) => Math.max(0, ...xs.flatMap((x) => lines(x.text).map((l) => measure(l, bold || roles[x.r] !== 'data'))));
    const word = (xs, bold) => Math.max(0, ...xs.flatMap((x) => tokens(x.text).map((w) => measure(w, bold || roles[x.r] !== 'data'))));
    const bodyMax = wide(body), headMax = wide(head, true), headMin = word(head, true);
    const wrap = col.width === 'narrow' ? false : col.width === 'wide' ? true : col.wrap;
    if (!wrap) {
      const want = Math.ceil(Math.max(bodyMax, headMin, Math.min(headMax, Math.max(bodyMax, 140)), col.width === 'narrow' ? headMax : 0)) + pad;
      return { min: want, max: want, wrap };
    }
    const max = Math.ceil(Math.max(bodyMax, headMax, 24)) + pad, min = Math.ceil(Math.max(word(body), headMin, 24)) + pad;
    const floor = Math.min(max, Math.max(min, col.width === 'wide' ? 240 : TB_TEXT_FLOOR));
    return { min, max, wrap, floor, cap: Math.max(floor, col.width === 'wide' ? TB_WIDE_CAP : TB_TEXT_CAP), weight: Math.sqrt(max) * (col.width === 'wide' ? 2 : 1) };
  });
  const fixed = need.filter((x) => !x.wrap).reduce((n, x) => n + x.max, 0), flex = need.filter((x) => x.wrap);
  const widths = need.map((x) => x.max);
  if (fixed + flex.reduce((n, x) => n + x.max, 0) > W) {
    let left = W - fixed;
    if (left >= flex.reduce((n, x) => n + x.floor, 0)) {
      // Share what is left by weight, each between its floor and cap (water-filling).
      let open = need.map((x, c) => c).filter((c) => need[c].wrap);
      for (let guard = 0; open.length && guard < 20; guard++) {
        const weight = open.reduce((n, c) => n + need[c].weight, 0);
        const fixedNow = open.filter((c) => {
          const share = left * need[c].weight / weight;
          if (share < need[c].floor) { widths[c] = need[c].floor; return true; }
          if (share > Math.min(need[c].cap, need[c].max)) { widths[c] = Math.min(need[c].cap, need[c].max); return true; }
          return false;
        });
        if (!fixedNow.length) { open.forEach((c) => { widths[c] = Math.floor(left * need[c].weight / weight); }); break; }
        fixedNow.forEach((c) => { left -= widths[c]; });
        open = open.filter((c) => !fixedNow.includes(c));
      }
    } else need.forEach((x, c) => { if (x.wrap) widths[c] = Math.max(x.min, Math.min(x.floor, x.max)); });
  }
  const total = widths.reduce((n, x) => n + x, 0);
  return { widths, total, overflow: total > W };
}
