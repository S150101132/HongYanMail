'use strict';
/* 鸿雁 · v0.5 compose and send: undo send, offline outbox, server drafts, body images,
   tables, forward as attachment, opening .eml files. Uses app.js globals ($, api, C, S...). */

// ---------------------------------------------------------------- 撤销发送
// The backend holds the draft for the delay and the main window's timer releases it;
// this page only shows the countdown and asks for the undo.
const HOLDS = new Map();  // localId -> { el, until, timer, meta }
function showHold(res, meta = {}) {
  dropHold(res.localId);
  const el = document.createElement('div');
  el.className = 'toast hold';
  el.innerHTML = `<span class="hold-text"></span><button>撤销</button>`;
  const until = Date.now() + (res.delayMs || 0);
  const subject = meta.subject || '（无主题）';
  const tick = () => {
    const left = Math.ceil((until - Date.now()) / 1000);
    $('.hold-text', el).textContent = left > 0 ? `正在发送「${subject}」… ${left} 秒后发出` : `正在发送「${subject}」…`;
    $('button', el).classList.toggle('hidden', left <= 0);
  };
  tick();
  const timer = setInterval(tick, 250);
  $('button', el).onclick = () => undoSend(res.localId);
  $('#toasts').appendChild(el);
  // If no result ever arrives (the main window went away), the bar does not stay forever.
  HOLDS.set(res.localId, { el, until, timer, meta, stale: setTimeout(() => dropHold(res.localId), (res.delayMs || 0) + 120000) });
}
function dropHold(localId) {
  const h = HOLDS.get(localId); if (!h) return null;
  clearInterval(h.timer); clearTimeout(h.stale); h.el.remove(); HOLDS.delete(localId);
  return h;
}
async function undoSend(localId) {
  try {
    const draft = await api('cancelSend', { local_id: localId });
    dropHold(localId);
    if (C.draft && !$('#composer').classList.contains('hidden') && C.dirty) await saveDraft(true);
    openComposer(draft);
    toast('已撤销发送，邮件已回到写信窗口');
  } catch (err) { fail(err); }
}
// The outcome of a send, immediate or after the undo delay.
function sendOutcome(res, meta = {}) {
  const subject = res.subject || meta.subject || '';
  if (res.state === 'Accepted') {
    const sentText = meta.encrypt ? (meta.sign ? '已签名并加密发送' : '加密邮件已发送') : meta.sign ? '已签名发送' : '邮件已发送';
    toast(res.scheduledAt ? `${meta.sign ? '已签名的' : ''}邮件已定时，将于 ${scheduleText(res.scheduledAt)} 发送` : sentText);
    if (res.scheduledAt) refreshScheduled();
    refreshDraftCount();
    const f = folderById(S.folderId);
    if (!S.local && !S.search && f && (f.role === 'sent' || (res.serverDraft && f.role === 'drafts'))) loadList(true);
  } else if (res.state === 'Queued') {
    toast('网络不可用：邮件已放入发件箱，恢复连接后自动发送。', { ms: 7000, action: { label: '查看发件箱', run: () => $('#nav-outbox').click() } });
  } else if (res.state === 'Unknown') {
    toast('发送结果不确定：请先在“已发送”中核对，本应用不会自动重发。草稿已保留。', { error: true, ms: 9000,
      action: { label: '查看发件箱', run: () => $('#nav-outbox').click() } });
  } else if (res.state === 'Cancelled') {
    return;
  } else {
    const open = res.localId ? { label: '打开草稿', run: () => openLocalDraft(res.localId) } : null;
    toast(res.failureMessage || res.message || `「${subject || '（无主题）'}」发送失败，草稿已保存在本机，可修改后重试。`, { error: true, ms: 9000, action: open });
    refreshDraftCount();
  }
  refreshOutbox();
}
on('sendResult', (v) => {
  const h = dropHold(v.localId);
  // A pop-out that sent this may be gone: the main window then reports it.
  if (h || !READER_MID) sendOutcome(v, h ? h.meta : {});
});

// ---------------------------------------------------------------- 离线发件箱
async function refreshOutbox() {
  if (READER_MID) return;
  try { S.outbox = await api('outbox'); } catch (e) { return; }
  const n = S.outbox.queued.length + S.outbox.review.length;
  $('#outbox-count').textContent = n || '';
  if (S.outbox.abandoned.length) {
    const n2 = S.outbox.abandoned.length;
    toast(`上次退出时有 ${n2} 封邮件还在撤销等待中，没有发出，已保留在本机草稿。`, { ms: 10000, action: { label: '查看草稿', run: () => $('#nav-drafts').click() } });
  }
  if (S.local === 'outbox') renderOutbox();
}
function renderOutbox() {
  const o = S.outbox || { queued: [], review: [] };
  const list = $('#mail-list');
  const rows = o.queued.map((q) => `<div class="row" data-id="q:${esc(q.localId)}">
      <span class="avatar xs" style="background:#2563eb"><i data-icon="clock"></i></span>
      <div class="main"><div class="line1"><span class="who">${esc(q.subject || '（无主题）')}</span><span class="when">${esc(q.queuedAt)}</span></div>
      <div class="prev">等待联网后自动发送 · 点击编辑，右键更多</div></div></div>`).concat(o.review.map((r) => `<div class="row" data-id="r:${esc(r.id)}">
      <span class="avatar xs" style="background:#b54708"><i data-icon="alert"></i></span>
      <div class="main"><div class="line1"><span class="who">${esc(r.subject || '（无主题）')}</span><span class="when">${esc(r.at)}</span></div>
      <div class="prev">发送结果待确认：可能已经发出。请先到“已发送”核对，本应用不会自动重发</div></div></div>`));
  list.innerHTML = rows.length ? rows.join('') + (o.queued.length ? '<div class="list-foot"><button class="btn ghost" id="outbox-retry">立即重试发送</button></div>' : '')
    : '<div class="list-foot">发件箱是空的</div>';
  $('#list-sub').textContent = o.queued.length ? `${o.queued.length} 封等待联网发送` : o.review.length ? `${o.review.length} 封待核对` : '离线时发送的邮件会在这里等待';
  const retry = $('#outbox-retry'); if (retry) retry.onclick = flushOutboxNow;
  renderIcons(list);
}
$('#nav-outbox').onclick = async () => {
  S.local = 'outbox'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
  renderFolders();
  $('#list-title').textContent = '发件箱'; $('#list-sub').textContent = '';
  renderOutbox(); refreshOutbox();
};
async function flushOutboxNow() {
  try {
    const res = await api('flushOutbox');
    if (!res.results.length) toast('发件箱里没有等待发送的邮件');
    else outboxFlushed(res);
  } catch (err) { fail(err); }
}
function outboxFlushed(v) {
  const sent = v.results.filter((r) => r.state === 'Accepted').length;
  const still = v.results.some((r) => r.state === 'Queued');
  if (sent) toast(`发件箱：${sent} 封邮件已发出`);
  if (still && !sent) toast('网络仍不可用，发件箱中的邮件稍后自动重试');
  v.results.filter((r) => !['Accepted', 'Queued'].includes(r.state)).forEach((r) => sendOutcome(r));
  refreshOutbox(); refreshDraftCount();
}
on('outboxFlushed', outboxFlushed);
async function outboxOpen(id) {
  const kind = id.slice(0, 2), key = id.slice(2);
  if (kind === 'q:') {
    await api('outboxRemove', { local_id: key }).catch(() => {});
    toast('已移出发件箱：修改后重新发送即可'); refreshOutbox();
    openLocalDraft(key);
  } else {
    const r = (S.outbox.review || []).find((x) => x.id === key);
    if (r) openLocalDraft(r.localId);
  }
}
function outboxMenu(e, id) {
  const kind = id.slice(0, 2), key = id.slice(2);
  const sent = () => { const f = S.folders.find((x) => x.role === 'sent'); if (f) selectFolder(f.id); };
  showMenu(e.clientX, e.clientY, kind === 'q:' ? [
    { label: '立即重试发送', icon: 'send', run: flushOutboxNow },
    { label: '编辑', icon: 'pen', run: () => outboxOpen(id) },
    { label: '移出发件箱（保留为本机草稿）', icon: 'draft', run: async () => { S.outbox = await api('outboxRemove', { local_id: key }).catch(fail) || S.outbox; refreshOutbox(); } },
  ] : [
    { label: '查看“已发送”', icon: 'sent', run: sent },
    { label: '打开草稿', icon: 'pen', run: () => outboxOpen(id) },
    { label: '已核对，从列表移除', icon: 'check', run: async () => { S.outbox = await api('dismissReview', { job_id: key }).catch(fail) || S.outbox; refreshOutbox(); } },
  ]);
}

// ---------------------------------------------------------------- 服务器草稿箱
function inDraftsFolder() { const f = folderById(S.folderId); return !S.local && !S.search && !!f && f.role === 'drafts' && !f.mailbox; }
async function editServerDraft(mid) {
  try {
    const draft = await api('editServerDraft', { mid });
    if (C.draft && !$('#composer').classList.contains('hidden') && C.dirty) await saveDraft(true);
    openComposer(draft);
  } catch (err) { fail(err); }
}
// After a server save the draft has a new id there: refresh the folder if it is shown.
function serverDraftSaved() { if (inDraftsFolder()) loadList(true); }
function composerOpened(draft) {
  if (draft.serverDraftId) $('#cmp-status').textContent = '正在编辑服务器草稿 · Ctrl+S 保存到草稿箱';
  if (draft.encryptedOriginal) toast('原邮件是加密邮件：附件是加密原件，收件人需要能解密它的证书才能打开。', { ms: 9000 });
}

// ---------------------------------------------------------------- 写信插入图片
// Body pictures stay data: URIs in the editor and drafts; sending makes them inline parts.
const BODY_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/bmp'];
const isBodyImage = (f) => BODY_IMAGE_TYPES.includes(String(f.type).toLowerCase());
function insertImageData(list, range) {
  const ok = list.filter((a) => {
    if (a.size > 10 * 1048576) { toast(`「${a.name}」超过 10 MB，未插入`, { error: true }); return false; }
    return true;
  });
  if (!ok.length) return;
  const editor = $('#cmp-editor');
  editor.focus();
  if (range) { const s = getSelection(); s.removeAllRanges(); s.addRange(range); }
  document.execCommand('insertHTML', false, ok.map((a) => `<img src="data:${esc(a.type)};base64,${a.data}" alt="${esc(a.name)}" style="max-width:100%">`).join(''));
  C.dirty = true;
}
async function insertImageFiles(files, range) { insertImageData(await Promise.all(files.map(readFile)), range); }
$('#cmp-image').onmousedown = (e) => e.preventDefault();
$('#cmp-image').onclick = async () => {
  const s = getSelection(); const range = s.rangeCount && $('#cmp-editor').contains(s.anchorNode) ? s.getRangeAt(0).cloneRange() : null;
  try {
    const picked = await api('pickFiles', { images: true });
    insertImageData(picked.filter(isBodyImage), range);
    const others = picked.filter((a) => !isBodyImage(a));
    if (others.length) { addAttachments(others); toast('不是图片的文件已作为附件添加'); }
  } catch (err) { fail(err); }
};

// ---------------------------------------------------------------- 表格
const CELL_STYLE = 'border:1px solid #98a2b3;padding:4px 8px;min-width:40px;vertical-align:top';
const BORDERS = { solid: '1px solid #98a2b3', dashed: '1px dashed #98a2b3', none: '0' };
function tableHtml(rows, cols) {
  const row = '<tr>' + `<td style="${CELL_STYLE}"><br></td>`.repeat(cols) + '</tr>';
  return `<table style="border-collapse:collapse;margin:6px 0" cellspacing="0" cellpadding="0"><tbody>${row.repeat(rows)}</tbody></table><p><br></p>`;
}
function currentCell() {
  const s = getSelection(); if (!s.rangeCount) return null;
  let n = s.anchorNode; n = n && (n.nodeType === 1 ? n : n.parentElement);
  const cell = n && n.closest('td,th');
  return cell && $('#cmp-editor').contains(cell) ? cell : null;
}
function caretIn(cell) {
  const r = document.createRange(); r.selectNodeContents(cell); r.collapse(true);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
}
function newCell(like) {
  const td = document.createElement(like && like.tagName === 'TH' ? 'th' : 'td');
  td.setAttribute('style', (like && like.getAttribute('style')) || CELL_STYLE);
  td.innerHTML = '<br>';
  return td;
}
const colOf = (cell) => { let x = 0; for (let c = cell.previousElementSibling; c; c = c.previousElementSibling) x += c.colSpan || 1; return x; };
function cellAt(tr, x) { let pos = 0; for (const c of tr.cells) { if (x < pos + (c.colSpan || 1)) return c; pos += c.colSpan || 1; } return null; }
const TABLE_OPS = {
  rowAbove: (cell) => addRow(cell, false), rowBelow: (cell) => addRow(cell, true),
  colLeft: (cell) => addCol(cell, false), colRight: (cell) => addCol(cell, true),
  delRow: (cell) => { const t = cell.closest('table'); cell.parentElement.remove(); if (!t.rows.length) t.remove(); },
  delCol: (cell) => {
    const t = cell.closest('table'), x = colOf(cell);
    for (const tr of Array.from(t.rows)) { const c = cellAt(tr, x); if (!c) continue; if (c.colSpan > 1) c.colSpan--; else c.remove(); }
    for (const tr of Array.from(t.rows)) if (!tr.cells.length) tr.remove();
    if (!t.rows.length) t.remove();
  },
  merge: (cell) => {
    const next = cell.nextElementSibling; if (!next) { toast('右侧没有可合并的单元格'); return; }
    if (next.textContent.trim() || next.querySelector('img')) cell.insertAdjacentHTML('beforeend', ' ' + next.innerHTML);
    cell.colSpan = (cell.colSpan || 1) + (next.colSpan || 1); next.remove();
  },
  delTable: (cell) => cell.closest('table').remove(),
};
function addRow(cell, after) {
  const tr = cell.parentElement, row = document.createElement('tr');
  for (const c of tr.cells) { const n = newCell(c); if (c.colSpan > 1) n.colSpan = c.colSpan; row.appendChild(n); }
  tr.parentElement.insertBefore(row, after ? tr.nextSibling : tr);
  return row;
}
function addCol(cell, after) {
  const x = colOf(cell) + (after ? (cell.colSpan || 1) - 1 : 0);
  for (const tr of Array.from(cell.closest('table').rows)) {
    const c = cellAt(tr, x);
    if (!c) { tr.appendChild(newCell(tr.cells[tr.cells.length - 1])); continue; }
    tr.insertBefore(newCell(c), after ? c.nextSibling : c);
  }
}
function setBorder(cell, kind) {
  const t = cell.closest('table');
  for (const c of t.querySelectorAll('td,th')) if (c.closest('table') === t) c.style.border = BORDERS[kind];
  t.style.borderCollapse = 'collapse';
}
function tableOp(cell, op) { TABLE_OPS[op](cell); C.dirty = true; }
function tableMenu(x, y, cell) {
  showMenu(x, y, [
    { label: '在上方插入行', run: () => tableOp(cell, 'rowAbove') }, { label: '在下方插入行', run: () => tableOp(cell, 'rowBelow') },
    { label: '在左侧插入列', run: () => tableOp(cell, 'colLeft') }, { label: '在右侧插入列', run: () => tableOp(cell, 'colRight') },
    '-', { label: '删除行', icon: 'trash', run: () => tableOp(cell, 'delRow') }, { label: '删除列', icon: 'trash', run: () => tableOp(cell, 'delCol') },
    { label: '与右侧单元格合并', run: () => tableOp(cell, 'merge') },
    '-', { label: '边框：实线', run: () => { setBorder(cell, 'solid'); C.dirty = true; } },
    { label: '边框：虚线', run: () => { setBorder(cell, 'dashed'); C.dirty = true; } },
    { label: '边框：无', run: () => { setBorder(cell, 'none'); C.dirty = true; } },
    '-', { label: '删除表格', icon: 'trash', run: () => tableOp(cell, 'delTable') },
  ]);
}
function tablePicker(button) {
  const range = (() => { const s = getSelection(); return s.rangeCount && $('#cmp-editor').contains(s.anchorNode) ? s.getRangeAt(0).cloneRange() : null; })();
  let box = $('#tbl-picker');
  if (!box) { box = document.createElement('div'); box.id = 'tbl-picker'; box.className = 'tbl-picker'; document.body.appendChild(box); }
  box.innerHTML = '<div class="tbl-grid">' + Array.from({ length: 80 }, (_, i) => `<span data-r="${Math.floor(i / 10) + 1}" data-c="${i % 10 + 1}"></span>`).join('') +
    '</div><div class="tbl-size muted small">选择表格大小</div>';
  const r = button.getBoundingClientRect();
  box.style.left = Math.min(r.left, innerWidth - 260) + 'px'; box.style.top = r.bottom + 4 + 'px';
  box.classList.remove('hidden');
  box.onmouseover = (e) => {
    const s = e.target.closest('span'); if (!s) return;
    $$('span', box).forEach((c) => c.classList.toggle('on', +c.dataset.r <= +s.dataset.r && +c.dataset.c <= +s.dataset.c));
    $('.tbl-size', box).textContent = `${s.dataset.r} 行 × ${s.dataset.c} 列`;
  };
  box.onmousedown = (e) => e.preventDefault();
  box.onclick = (e) => {
    const s = e.target.closest('span'); if (!s) return;
    box.classList.add('hidden');
    const editor = $('#cmp-editor'); editor.focus();
    if (range) { const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range); }
    document.execCommand('insertHTML', false, tableHtml(+s.dataset.r, +s.dataset.c));
    C.dirty = true;
  };
  setTimeout(() => document.addEventListener('mousedown', function away(e) {
    if (!box.contains(e.target)) { box.classList.add('hidden'); document.removeEventListener('mousedown', away); }
  }), 0);
}
$('#cmp-table').onmousedown = (e) => e.preventDefault();
$('#cmp-table').onclick = (e) => {
  const cell = currentCell(), b = e.currentTarget;
  if (!cell) { tablePicker(b); return; }
  const r = b.getBoundingClientRect();
  tableMenu(r.left, r.bottom + 4, cell);
  $('#menu').insertAdjacentHTML('afterbegin', '<button id="tbl-new"><i></i><span>插入新表格…</span></button><hr>');
  $('#tbl-new').onclick = () => { hideMenu(); tablePicker(b); };
};
$('#cmp-editor').addEventListener('contextmenu', (e) => {
  const cell = e.target.closest && e.target.closest('td,th');
  if (!cell) return;
  e.preventDefault();
  caretIn(cell);
  tableMenu(e.clientX, e.clientY, cell);
});
// Tab / Shift+Tab move between cells; Tab in the last cell adds a row.
$('#cmp-editor').addEventListener('keydown', (e) => {
  if (e.key !== 'Tab' || e.ctrlKey || e.altKey) return;
  const cell = currentCell(); if (!cell) return;
  e.preventDefault();
  const table = cell.closest('table');
  const cells = () => $$('td,th', table).filter((c) => c.closest('table') === table);
  let list = cells(), i = list.indexOf(cell) + (e.shiftKey ? -1 : 1);
  if (i < 0) return;
  if (i >= list.length) { addRow(list[list.length - 1], true); C.dirty = true; list = cells(); }
  caretIn(list[i]);
});

// ---------------------------------------------------------------- .eml 文件
async function importEml() {
  try { await api('importEml'); } catch (err) { fail(err); }
}
