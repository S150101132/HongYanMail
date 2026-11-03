'use strict';
/* 鸿雁 · 邮件表格 (T2): drag a column's or a row's border in the compose editor to size it, as in
   Excel or Word. While dragging only that column's (row's) cells change, so it stays smooth; on
   release the size is kept as the user's own choice (data-hy-wpx / data-hy-h, see controller.js)
   and the table is laid out again. Double-click a border: back to automatic. Tables in the
   signature or the quoted mail are left alone. */

const TB_GRIP = 4, TB_MIN_COL = 32, TB_MIN_ROW = 22;
let tbDrag = null;

// The border under the pointer: {kind: 'col', table, c} | {kind: 'row', table, tr} | null.
function tbBorderAt(e) {
  const cell = e.target && e.target.closest && e.target.closest('td,th');
  const table = cell && cell.closest('table');
  if (!table || !table.closest('#cmp-editor') || !tbOwned(table)) return null;
  const r = cell.getBoundingClientRect(), x = e.clientX, y = e.clientY;
  const near = (a, b) => Math.abs(a - b) <= TB_GRIP;
  if (near(x, r.right) || near(x, r.left)) {
    const c = tbColumnOf(cell);
    if (near(x, r.right)) return { kind: 'col', table, c: c + (cell.colSpan || 1) - 1 };
    if (c > 0) return { kind: 'col', table, c: c - 1 };
  }
  const row = cell.parentElement.rowIndex;
  if (near(y, r.bottom)) return { kind: 'row', table, tr: table.rows[row + (cell.rowSpan || 1) - 1] };
  if (near(y, r.top) && row > 0) return { kind: 'row', table, tr: table.rows[row - 1] };
  return null;
}
const tbBox = (el, a, b) => { const cs = getComputedStyle(el); return (parseFloat(cs[a]) || 0) + (parseFloat(cs[b]) || 0) + 1; };

function tbDragStart(e) {
  const b = e.button === 0 && tbBorderAt(e);
  if (!b) return;
  e.preventDefault(); e.stopPropagation();  // No caret, no text selection.
  const d = { ...b, x0: e.clientX, y0: e.clientY, moved: false };
  if (b.kind === 'col') {
    d.cells = tbColumnCells(b.table, b.c);
    const sample = d.cells[0] || tbGrid(b.table)[0][b.c];
    d.size0 = sample.getBoundingClientRect().width;
    d.pad = tbBox(sample, 'paddingLeft', 'paddingRight');
    d.table0 = b.table.getBoundingClientRect().width;
  } else {
    d.cells = Array.from(b.tr.cells).filter((x) => (x.rowSpan || 1) === 1);
    d.size0 = b.tr.getBoundingClientRect().height;
    d.pad = d.cells.length ? tbBox(d.cells[0], 'paddingTop', 'paddingBottom') : 0;
  }
  tbDrag = d;
  document.body.classList.add(b.kind === 'col' ? 'tb-resizing-col' : 'tb-resizing-row');
}
function tbDragMove(e) {
  const ed = document.getElementById('cmp-editor');
  if (!tbDrag) {  // Show that a border can be dragged.
    const b = e.target && ed.contains(e.target) ? tbBorderAt(e) : null;
    const cursor = b ? (b.kind === 'col' ? 'col-resize' : 'row-resize') : '';
    if (ed.style.cursor !== cursor) ed.style.cursor = cursor;
    return;
  }
  e.preventDefault();
  const d = tbDrag;
  if (d.kind === 'col') {
    d.size = Math.max(TB_MIN_COL, Math.round(d.size0 + e.clientX - d.x0));
    d.cells.forEach((x) => { x.style.width = `${d.size - d.pad}px`; });
    Object.assign(d.table.style, { width: `${Math.round(d.table0 + d.size - d.size0)}px`, maxWidth: 'none' });
  } else {
    d.size = Math.max(TB_MIN_ROW, Math.round(d.size0 + e.clientY - d.y0));
    d.cells.forEach((x) => { x.style.height = `${d.size - d.pad}px`; });
  }
  d.moved = d.moved || Math.abs(d.size - d.size0) >= 2;
}
function tbDragEnd() {
  const d = tbDrag;
  if (!d) return;
  tbDrag = null;
  document.body.classList.remove('tb-resizing-col', 'tb-resizing-row');
  if (!d.moved) return;
  if (d.kind === 'col') tbSet(d.table, 'columnPx', d.c, d.size);
  else tbSet(d.table, 'rowPx', d.tr, d.size);
}
function tbBorderReset(e) {
  const b = tbBorderAt(e);
  if (!b) return;
  e.preventDefault();
  if (b.kind === 'col') tbSet(b.table, 'columnPx', b.c, 0);
  else tbSet(b.table, 'rowPx', b.tr, 0);
}

if (typeof document !== 'undefined' && document.getElementById('cmp-editor')) {
  const ed = document.getElementById('cmp-editor');
  ed.addEventListener('mousedown', tbDragStart, true);
  ed.addEventListener('dblclick', tbBorderReset, true);
  document.addEventListener('mousemove', tbDragMove);
  document.addEventListener('mouseup', tbDragEnd);
}
