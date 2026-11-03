'use strict';
/* 鸿雁 · 邮件表格 (T2): puts the pieces together for the compose page. A table is formatted from its
   own content every time — analysis, then the AI's semantic corrections where the analysis was
   unsure, then the user's own choices, which nothing overrides — and only tables registered as
   ours (data-hy-table: pasted, inserted, or picked from the menu) are formatted automatically.
   Every format checks the content fingerprint and puts the table back if anything but formatting
   changed. Uses app.js globals ($, $$, S, C, api, toast). */

const TB_AI_TRUST = 0.85;  // Below this the analysis gives way to the AI.

function tbPrefs() { return { ...TB_PREFS, ...((typeof S !== 'undefined' && S.prefs && S.prefs.tables) || {}) }; }

let tbCanvas = null;
// measure(text, bold) -> px, in the editor's font.
function tbMeasurer() {
  const ed = typeof document !== 'undefined' && document.getElementById('cmp-editor');
  const cs = ed && ed.isConnected ? getComputedStyle(ed) : null;
  const size = cs ? cs.fontSize : '14px', family = cs ? cs.fontFamily : '"Microsoft YaHei", sans-serif';
  tbCanvas = tbCanvas || document.createElement('canvas');
  const ctx = tbCanvas.getContext('2d'), cache = new Map();
  return (text, bold) => {
    const key = (bold ? 'b' : 'n') + text;
    if (!cache.has(key)) { ctx.font = `${bold ? 600 : 400} ${size} ${family}`; cache.set(key, ctx.measureText(text).width); }
    return cache.get(key);
  };
}
function tbAvailable() {
  const ed = document.getElementById('cmp-editor');
  const w = ed && ed.clientWidth ? ed.clientWidth - 40 : TB_MAX_WIDTH;
  return Math.max(TB_MIN_WIDTH, Math.min(TB_MAX_WIDTH, w));
}

// The AI's corrections, if they were made for this very content.
function tbAiPlan(table) {
  try { const ai = JSON.parse(table.dataset.hyAi || 'null'); return ai && ai.fp === tbFingerprint(table) ? ai.plan : null; } catch { return null; }
}
// noAi: the analysis before any AI answer (what the AI is asked about, so the cache key stays the same).
function tbAnalyze(table, plain, { noAi = false } = {}) {
  const prefs = tbPrefs(), ai = noAi ? null : tbAiPlan(table), cellTypes = tbCellTypes(plain);
  const found = tbRoles(plain, cellTypes), roles = found.roles.slice();
  if (ai) for (const row of ai.rows || []) if (roles[row.i] !== undefined && (found.conf[row.i] < TB_AI_TRUST || row.conf >= 0.9)) roles[row.i] = row.role;
  Array.from(table.rows).forEach((tr, r) => { if (tr.dataset.hyRole) roles[r] = tr.dataset.hyRole; });
  const types = tbColumnTypes(plain, cellTypes, roles);
  if (ai) for (const col of ai.cols || []) { const c = types[col.i]; if (c && (c.conf < TB_AI_TRUST || col.conf >= 0.9)) Object.assign(c, { type: col.type, conf: col.conf }); }
  // The user's column choices sit on the column's cells.
  const manual = (c, key) => { const x = plain.cells.find((y) => y.c === c && y.cs === 1 && y.el && y.el.dataset[key]); return x ? x.el.dataset[key] : ''; };
  const cols = types.map((col, c) => ({ ...col, align: manual(c, 'hyAl') || tbColumnAlign(col, prefs), wrap: tbColumnWraps(col), width: manual(c, 'hyW'),
    px: parseInt(manual(c, 'hyWpx'), 10) || 0 }));
  const rowHeights = Array.from(table.rows, (tr) => parseInt(tr.dataset.hyH, 10) || 0);
  const theme = table.dataset.hyTheme || (prefs.theme !== 'auto' && TB_THEMES[prefs.theme] ? prefs.theme : (ai && TB_THEMES[ai.theme] ? ai.theme : tbPickTheme(plain, 'auto')));
  const sized = tbWidths(plain, roles, cols, tbMeasurer(), tbAvailable(), theme);
  const keep = table.dataset.hyKeep ? table.dataset.hyKeep === '1' : prefs.keepColors !== false;
  return { roles, conf: found.conf, cols, rowHeights, theme, widths: sized.widths, total: sized.total, overflow: sized.overflow, keepColors: keep,
    headAlign: table.dataset.hyHeadAl || prefs.headAlign, emphasis: new Set(ai ? (ai.emphasis || []) : []), cellTypes };
}
// Format one table in place (in the editor or in a pasted document). -> the analysis, or null when
// the table had to be put back.
function tbFormat(table) {
  const fp = tbFingerprint(table), copy = table.cloneNode(true);
  try {
    const plain = tbPlainFromTable(table), an = tbAnalyze(table, plain);
    tbRender(table, plain, an);
    tbScrollFrame(table, an.overflow);
    if (tbFingerprint(table) !== fp) throw new Error('content changed');
    return an;
  } catch (err) {
    table.replaceWith(copy);
    console.error('table format', err);
    return null;
  }
}
// A table wider than the page scrolls sideways in the editor; the frame is not sent (mail.js).
function tbScrollFrame(table, overflow) {
  const frame = table.parentElement && table.parentElement.matches('div[data-hy-scroll]') ? table.parentElement : null;
  if (overflow && !frame) {
    const div = table.ownerDocument.createElement('div');
    div.dataset.hyScroll = '1'; div.style.cssText = 'overflow-x:auto;max-width:100%';
    table.replaceWith(div); div.append(table);
  } else if (!overflow && frame) frame.replaceWith(table);
}
const tbOf = (node) => { const el = node && (node.nodeType === 1 ? node : node.parentElement); const t = el && el.closest('table'); return t && t.closest('#cmp-editor') ? t : null; };
const tbOwned = (t) => !t.closest('.hy-signature, .hy-quote');

// 粘贴: clipboard HTML -> {html, raw, id, count} (count 0: only styles were inlined), or null.
function tbPaste(html) {
  const read = tbReadClipboard(html);
  if (!read) return null;
  const tables = $$('table', read.doc.body).filter((t) => !t.parentElement.closest('table'));
  if (!tables.length) return { html: tbFragment(read.doc), raw: null, id: '', count: 0 };
  const id = Math.random().toString(36).slice(2, 10);
  tables.forEach((t) => { t.dataset.hyPaste = id; });
  const raw = tbFragment(read.doc);
  tables.forEach(tbFormat);
  return { html: tbFragment(read.doc), raw, id, count: tables.length };
}
// 撤销整理: the pasted tables as they came (cleaned of the spreadsheet's look, not yet laid out) —
// only those still as pasted, so nothing typed since is lost. -> false when one had been edited.
function tbUndoPaste(id, raw) {
  const box = document.createElement('template'); box.innerHTML = raw;
  const before = $$('table', box.content).filter((t) => t.dataset.hyPaste === id);
  const now = $$(`#cmp-editor table[data-hy-paste="${id}"]`);
  return tbRestore(now, before);
}
// Put tables back as they were, if their content is still the same. -> false when not.
function tbRestore(tables, before) {
  if (tables.length !== before.length || tables.some((t, i) => tbFingerprint(t) !== tbFingerprint(before[i]))) {
    toast('表格内容已经改动，不能撤销排版：可在右键菜单「清除我的调整」或换样式', { error: true });
    return false;
  }
  tables.forEach((t, i) => (t.parentElement.matches('div[data-hy-scroll]') ? t.parentElement : t).replaceWith(before[i]));
  return true;
}

// The user's own choices (the table menu). Each formats the table again and may teach a preference.
const tbColumnCells = (table, c) => { const grid = tbGrid(table); return grid.map((row, r) => (tbFirstSlot(grid, r, c) && row[c].colSpan === 1 ? row[c] : null)).filter(Boolean); };
function tbColumnOf(cell) { for (const row of tbGrid(cell.closest('table'))) { const c = row.indexOf(cell); if (c >= 0) return c; } return 0; }
const TB_MANUAL = {
  align: (table, cell, v) => tbColumnCells(table, tbColumnOf(cell)).forEach((x) => { if (v) x.dataset.hyAl = v; else delete x.dataset.hyAl; }),
  width: (table, cell, v) => tbColumnCells(table, tbColumnOf(cell)).forEach((x) => { delete x.dataset.hyWpx; if (v) x.dataset.hyW = v; else delete x.dataset.hyW; }),
  // Dragged sizes (table/resize.js): a column's pixels on its cells, a row's on the row; 0 is automatic again.
  columnPx: (table, c, v) => tbColumnCells(table, c).forEach((x) => { delete x.dataset.hyW; if (v) x.dataset.hyWpx = String(v); else delete x.dataset.hyWpx; }),
  rowPx: (table, tr, v) => { if (v) tr.dataset.hyH = String(v); else delete tr.dataset.hyH; },
  role: (table, cell, v) => { const tr = cell.parentElement; if (v) tr.dataset.hyRole = v; else delete tr.dataset.hyRole; },
  fill: (table, cells, v) => cells.forEach((x) => { if (v === null) delete x.dataset.hyFill; else x.dataset.hyFill = v || 'none'; }),
  theme: (table, cell, v) => { if (v) table.dataset.hyTheme = v; else delete table.dataset.hyTheme; },
  headAlign: (table, cell, v) => { table.dataset.hyHeadAl = v; },
  keepColors: (table, cell, v) => { table.dataset.hyKeep = v ? '1' : '0'; },
};
function tbSet(table, kind, target, value) {
  table.dataset.hyTable = '1';
  const before = kind === 'align' ? tbAnalyze(table, tbPlainFromTable(table)).cols[tbColumnOf(target)] : null;
  TB_MANUAL[kind](table, target, value);
  tbFormat(table);
  if (typeof C !== 'undefined') C.dirty = true;
  if (kind === 'theme' && value) tbLearn(table, 'theme', value, `「${TB_THEMES[value].label}」样式`);
  if (kind === 'headAlign') tbLearn(table, 'headAlign', value, value === 'center' ? '表头居中' : value === 'left' ? '表头靠左' : '表头随列对齐');
  if (kind === 'keepColors') tbLearn(table, 'keepColors', value, value ? '保留原表颜色' : '去掉原表颜色');
  if (kind === 'align' && value && before && ['date', 'datetime'].includes(before.type)) tbLearn(table, 'dateAlign', value, value === 'center' ? '日期居中' : value === 'right' ? '日期靠右' : '日期靠左');
}
function tbClearManual(table) {
  table.querySelectorAll('[data-hy-al],[data-hy-w],[data-hy-wpx],[data-hy-fill]').forEach((x) => { delete x.dataset.hyAl; delete x.dataset.hyW; delete x.dataset.hyWpx; delete x.dataset.hyFill; });
  Array.from(table.rows).forEach((tr) => { delete tr.dataset.hyRole; delete tr.dataset.hyH; });
  ['hyTheme', 'hyHeadAl', 'hyKeep'].forEach((k) => delete table.dataset[k]);
  tbFormat(table);
  if (typeof C !== 'undefined') C.dirty = true;
}

// 学习偏好: the same explicit choice on two different tables offers to make it the default, once.
const tbSeen = {};
function tbLearn(table, key, value, label) {
  if (tbPrefs()[key] === value) return;
  table.dataset.hyId = table.dataset.hyId || Math.random().toString(36).slice(2, 10);
  const seen = tbSeen[key];
  if (!seen || seen.value !== value) { tbSeen[key] = { value, tables: new Set([table.dataset.hyId]), offered: false }; return; }
  seen.tables.add(table.dataset.hyId);
  if (seen.tables.size < 2 || seen.offered) return;
  seen.offered = true;
  toast(`以后粘贴的表格都用${label}？`, { ms: 10000, action: { label: '好', run: () => tbSavePrefs({ [key]: value }) } });
}
async function tbSavePrefs(values) {
  try { S.prefs = await api('setPrefs', { values: { tables: { ...tbPrefs(), ...values } } }); toast('已记住，可在「设置 → 写信 → 表格」中修改'); } catch (err) { fail(err); }
}

// After editing: a table the caret leaves is laid out again if its text changed (not while typing).
let tbEditing = null, tbTouched = false;
if (typeof document !== 'undefined' && document.getElementById('cmp-editor')) {
  document.getElementById('cmp-editor').addEventListener('input', () => { if (tbEditing) tbTouched = true; });
  document.addEventListener('selectionchange', () => {
    const s = getSelection(), now = s.rangeCount ? tbOf(s.anchorNode) : null;
    if (now === tbEditing) return;
    const left = tbEditing, changed = tbTouched;
    tbEditing = now && now.dataset.hyTable && tbOwned(now) ? now : null; tbTouched = false;
    if (left && changed && left.isConnected) tbFormat(left);
  });
}

// AI 优化表格: what the AI reads of a table — every row's role and type signature, the full text of
// the rows that matter (header, titles, groups, totals, notes, odd rows, a sample of the middle),
// within a budget. Row and column numbers are the table's own.
const TB_SIG = { empty: '.', missing: '-', int: 'n', decimal: 'n', money: '$', percent: '%', quantity: 'q', date: 'd', datetime: 'd', time: 't', id: 'i', text: 's', longtext: 'L' };
function tbAiPayload(table) {
  const plain = tbPlainFromTable(table), an = tbAnalyze(table, plain, { noAi: true }), roles = an.roles;
  const rowSig = (r) => plain.at[r].map((id, c) => { const x = plain.cells[id]; return !x ? '.' : x.r !== r || x.c !== c ? '^' : TB_SIG[an.cellTypes[x.id]] || 's'; }).join('');
  const pick = new Set();
  roles.forEach((role, r) => { if (role !== 'data' && role !== 'blank') pick.add(r); });
  const data = roles.map((role, r) => r).filter((r) => roles[r] === 'data');
  data.slice(0, 8).forEach((r) => pick.add(r)); data.slice(-5).forEach((r) => pick.add(r));
  const usual = (c) => an.cols[c].type;
  data.filter((r) => plain.cells.some((x) => x.r === r && x.cs === 1 && !['empty', 'missing'].includes(an.cellTypes[x.id]) &&
    (TB_NUMERIC.has(an.cellTypes[x.id]) !== ['number', 'money', 'percent', 'quantity'].includes(usual(x.c))))).slice(0, 10).forEach((r) => pick.add(r));
  for (let i = 1; i < 12 && data.length > 13; i++) pick.add(data[Math.floor(i * data.length / 12)]);
  let budget = 6000;
  const rows = [...pick].sort((a, b) => a - b).map((r) => {
    const cells = plain.cells.filter((x) => x.r === r).map((x) => ({ c: x.c, ...(x.cs > 1 ? { cs: x.cs } : {}), ...(x.rs > 1 ? { rs: x.rs } : {}), t: x.text.slice(0, 40) }));
    budget -= JSON.stringify(cells).length;
    return budget > 0 ? { i: r, role: roles[r], cells } : null;
  }).filter(Boolean);
  return { rows: plain.nRows, cols: plain.nCols, signatures: roles.map((role, r) => `${r}:${role}:${rowSig(r)}`),
    columns: an.cols.map((c, i) => ({ i, type: c.type, conf: Math.round(c.conf * 100) / 100, head: c.head.slice(0, 40) })), sample: rows };
}
// The AI's answer for a table, kept with the content it was made for.
function tbApplyAi(table, plan) {
  table.dataset.hyTable = '1';
  table.dataset.hyAi = JSON.stringify({ fp: tbFingerprint(table), plan });
  return tbFormat(table);
}
