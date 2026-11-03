'use strict';
/* 鸿雁 · 邮件表格 (T2): the table model. A table is read into a plain model — each cell's text,
   spans and what the source said about it — that the analysis (types.js, structure.js) and the
   layout (layout.js) work on without the page. Only render.js writes back, and only styles and
   attributes: a cell's content is never rebuilt. Pure functions here, except tbPlainFromTable and
   tbFingerprint. */

const TB_VERSION = 1;  // Of the analysis: part of the AI cache key.

// [[cell element]]: a merged cell sits in every slot it covers.
function tbGrid(table) {
  const rows = Array.from(table.rows), grid = rows.map(() => []);
  rows.forEach((row, r) => {
    let c = 0;
    Array.from(row.cells).forEach((cell) => {
      while (grid[r][c]) c++;
      for (let i = 0; i < Math.min(cell.rowSpan || 1, rows.length - r); i++) for (let j = 0; j < (cell.colSpan || 1); j++) grid[r + i][c + j] = cell;
      c += cell.colSpan || 1;
    });
  });
  return grid;
}
// A cell counts in its top-left slot only.
const tbFirstSlot = (grid, r, c) => grid[r][c] && grid[r][c - 1] !== grid[r][c] && (!r || grid[r - 1][c] !== grid[r][c]);

const tbCleanText = (s) => String(s ?? '').replace(/[ \t 　]+/g, ' ').replace(/ *\n */g, '\n').trim();
// Visual length: a CJK character is about two Latin ones wide.
const tbVisualLength = (s) => { let n = 0; for (const ch of String(s)) n += /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/.test(ch) ? 2 : 1; return n; };

// What the source said about a cell, kept on it as data-hy-src: "bg=#ffeb9c;fg=#9c0006;b;i;u;s;al=center".
function tbParseSrc(text) {
  const src = {};
  for (const part of String(text || '').split(';')) {
    const [k, v] = part.split('=');
    if (k) src[k.trim()] = v === undefined ? true : v.trim();
  }
  return src;
}
const tbSerializeSrc = (src) => Object.entries(src).filter(([, v]) => v).map(([k, v]) => (v === true ? k : `${k}=${v}`)).join(';');

// Plain model from rows of cells; a cell is its text or {text, cs, rs, num, fmt, src, ...}.
// -> {nRows, nCols, cells: [{id, r, c, rs, cs, text, ...}], at: [[cell id]]}.
function tbPlain(rows) {
  const cells = [], at = rows.map(() => []);
  rows.forEach((row, r) => {
    let c = 0;
    row.forEach((v) => {
      const x = v !== null && typeof v === 'object' ? v : { text: v };
      while (at[r][c] !== undefined) c++;
      const cell = { ...x, id: cells.length, r, c, rs: Math.max(1, Math.min(x.rs || 1, rows.length - r)), cs: Math.max(1, x.cs || 1), text: tbCleanText(x.text) };
      cell.src = typeof x.src === 'string' ? tbParseSrc(x.src) : x.src || {};
      cells.push(cell);
      for (let i = 0; i < cell.rs; i++) for (let j = 0; j < cell.cs; j++) at[r + i][c + j] = cell.id;
      c += cell.cs;
    });
  });
  const nCols = Math.max(0, ...at.map((row) => row.length));
  at.forEach((row) => { for (let c = 0; c < nCols; c++) if (row[c] === undefined) row[c] = -1; });
  return { nRows: rows.length, nCols, cells, at };
}
// Cells whose top-left slot is in row r / column c.
const tbRowCells = (plain, r) => plain.cells.filter((x) => x.r === r);
const tbColCells = (plain, c) => plain.cells.filter((x) => x.c === c && x.cs === 1);

// A cell's text as the user sees it: <br> and block ends are line breaks.
function tbCellText(el) {
  const copy = el.cloneNode(true);
  copy.querySelectorAll('br').forEach((b) => b.replaceWith('\n'));
  copy.querySelectorAll('p,div,li').forEach((b) => b.append('\n'));
  return copy.textContent;
}
function tbPlainFromTable(table) {
  const rows = Array.from(table.rows, (tr) => Array.from(tr.cells, (el) => ({
    el, text: tbCellText(el), cs: el.colSpan || 1, rs: el.rowSpan || 1,
    num: el.dataset.hyNum === '1', fmt: el.dataset.hyFmt || '', src: el.dataset.hySrc || '',
  })));
  return tbPlain(rows);
}

// Content and shape only — what no formatting step may change.
function tbFingerprint(table) {
  let h = 2166136261;
  const text = Array.from(table.rows, (tr) => Array.from(tr.cells, (c) => `${c.rowSpan}x${c.colSpan}:${c.innerHTML}`).join('\u0001')).join('\u0002');
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return `${table.rows.length}.${h.toString(36)}`;
}
