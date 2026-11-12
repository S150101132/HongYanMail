'use strict';
/* 鸿雁 · the pages that were 今天 (R2a; 每日速览 G2 above them was taken out in G3), reached from
   the 命令框, three-pane, back to the 收件箱: 在等别人
   (sent mail nobody answered and 受阻 tasks), 待我确认 (the task center's suggestions), 本周概括 (memory.js); there a
   mail opens in the reader as ever, a task as a short card (记进展 / 完成 / 在任务中心打开).
   Every view keeps S.local 'today'. Loaded last but commands.js. */

const TD = { sub: '', data: null, waiting: null, confirm: null, task: null };
const TD_SUB = { wait: '在等别人', confirm: '待我确认', week: '本周概括' };

async function showToday(sub, quiet) {
  if (!quiet || S.local !== 'today' || TD.sub !== sub) {
    S.local = 'today'; S.search = null; S.filter = 'all'; S.listToken++; S.multi.clear(); S.open.clear();
    S.rows = []; S.more = false; S.total = null; $('#search').value = '';
    TD.sub = sub; TD.data = TD.waiting = TD.confirm = null;
    tdHideTask(); renderSelection();
    $('#list-title').textContent = TD_SUB[sub];
    $('#list-sub').textContent = '';
    $('#mail-list').innerHTML = '<div class="skeleton"></div>'.repeat(4);
    quiet = false;
  }
  const token = S.listToken;
  renderFolders();
  try {
    if (sub === 'wait') TD.waiting = await api('todayWaiting');
    if (sub === 'confirm') TD.confirm = await api('tasksPending').catch(() => []);
    if (token !== S.listToken) return;
    TD.data = {};
    const mails = sub === 'wait' ? TD.waiting.mail : [];
    if (sub === 'wait') UC.wait = new Map(TD.waiting.mail.map((r) => [r.id, r]));
    mails.forEach((r) => r.card && UC.cards.set(r.id, r.card));
    S.rows = mails; S.total = mails.length;
    tdRender();
    if (sub === 'week' && typeof memTop === 'function') { MEM.weekOpen = true; memTop('week'); }
  } catch (err) { if (token === S.listToken) $('#mail-list').innerHTML = `<div class="list-foot">${esc(err.message)}</div>`; }
}

// ---------------------------------------------------------------- the sub-pages' lists
function tdTaskRow(t) {
  const due = ucDue(t.due);
  return `<div class="row td-task${TD.task === t.id ? ' sel' : ''}" data-task="${t.id}" role="option">
    <span class="avatar xs td-icon"><i data-icon="tasks"></i></span>
    <div class="main"><div class="line1"><span class="who">#${t.id}${t.system ? ' · ' + esc(t.system) : ''}</span><span class="when">${esc(t.status)}</span></div>
      <div class="subj">${due ? `<span class="uc-due${due.late ? ' late' : ''}">${esc(due.text)}</span>` : ''}${esc(t.content || '（无内容）')}</div></div></div>`;
}
function tdHead() {
  return `<div class="td-head"><button class="btn ghost sm" data-td-inbox="1">← 收件箱</button>${TD.sub === 'confirm' && tdSuggested().length > 1
    ? `<span class="spacer"></span><button class="btn ghost sm" data-td-all="0">全部驳回</button><button class="btn sm" data-td-all="1">全部采纳</button>` : ''}</div>`;
}
function tdRender() {
  const list = $('#mail-list');
  if (!TD.sub || !TD.data) return;
  S.items = S.rows.map((r) => ({ id: r.id, ids: [r.id], rows: [r] }));  // What app.js keeps for J / K and selection.
  let html = tdHead(), group = null;
  const head = (g) => { if (g !== group) { group = g; html += `<div class="group-head">${g}</div>`; } };
  if (TD.sub === 'wait') {
    for (const r of TD.waiting.mail) { head('等对方回复'); html += rowHtml(r); }
    for (const t of TD.waiting.tasks) { head('受阻的任务'); html += tdTaskRow(t); }
    if (!TD.waiting.mail.length && !TD.waiting.tasks.length) html += '<div class="list-foot">没有在等别人的事</div>';
  } else if (TD.sub === 'confirm') {
    html += TD.confirm.map(tdConfirmRow).join('') || '<div class="list-foot">没有等你确认的建议</div>';
  } else html += '<div class="list-foot muted small">本周概括在上方；点项目名看它的全部记忆。</div>';
  const top = list.scrollTop;
  list.innerHTML = html; list.scrollTop = top;
  renderIcons(list);
}
const _tdRenderList = renderList;
renderList = function (...args) { return S.local === 'today' ? tdRender() : _tdRenderList(...args); };

// 待我确认: the task center's pending changes. AI suggestions are taken or dropped here; an
// edit made in Excel is reviewed in the task center, where its differences show.
const tdSuggested = () => (TD.confirm || []).filter((p) => p.kind === 'suggested');
function tdConfirmRow(p) {
  const ai = p.kind === 'suggested';
  const what = ai ? `AI 建议：${TK_OP[(p.action || {}).op] || '修改'}` : 'Excel 中的修改';
  const text = ai ? (p.action && p.action.text) || p.note || p.basis || '' : Object.keys(p.changes || {}).map((k) => TK_LABEL[k] || k).join('、');
  return `<div class="row td-confirm" data-task="${p.task}" data-seq="${p.seq}" role="option">
    <span class="avatar xs td-icon"><i data-icon="${ai ? 'spark' : 'file'}"></i></span>
    <div class="main"><div class="line1"><span class="who">#${p.task} · ${what}</span><span class="when">${esc(String(p.date || '').slice(5))}</span></div>
      <div class="prev">${esc(text)}</div>
      <div class="row-gap td-acts">${ai ? `<button class="btn sm" data-td-accept="${p.seq}">采纳</button><button class="btn ghost sm" data-td-reject="${p.seq}">驳回</button>`
        : '<button class="btn ghost sm" data-td-open="1">在任务中心查看</button>'}</div></div></div>`;
}
async function tdReview(seqs, accept) {
  try {
    let result = null;
    for (const seq of seqs) result = await api('taskReview', { seq, accept });
    afterSave(result, true);
    toast(accept ? `已采纳 ${seqs.length} 条建议` : `已驳回 ${seqs.length} 条建议`);
  } catch (err) { fail(err); showToday('confirm', true); }
}

// Clicks on the list: links of the head, task rows, the confirm buttons. Mail rows go on to app.js.
$('#mail-list').addEventListener('click', (e) => {
  if (S.local !== 'today') return;
  const t = e.target;
  const stop = () => { e.stopPropagation(); e.preventDefault(); };
  if (t.closest('[data-td-inbox]')) { stop(); selectFolder((S.folders.find((f) => f.role === 'inbox') || { id: '1' }).id); return; }
  const all = t.closest('[data-td-all]');
  if (all) {
    stop();
    const accept = all.dataset.tdAll === '1', seqs = tdSuggested().map((p) => p.seq);
    confirmDialog(accept ? '全部采纳' : '全部驳回', `${accept ? '采纳' : '驳回'}这 ${seqs.length} 条 AI 建议？采纳的会写进任务，之后可以在任务的变更记录里撤销。`, accept ? '全部采纳' : '全部驳回')
      .then((ok) => ok && tdReview(seqs, accept));
    return;
  }
  const one = t.closest('[data-td-accept], [data-td-reject]');
  if (one) { stop(); tdReview([Number(one.dataset.tdAccept || one.dataset.tdReject)], !!one.dataset.tdAccept); return; }
  const row = t.closest('.row[data-task]');
  if (row) {
    stop();
    if (t.closest('[data-td-open]') || row.classList.contains('td-confirm') && !row.querySelector('[data-td-accept]')) { openTask(Number(row.dataset.task)); return; }
    tdShowTask(Number(row.dataset.task));
  }
}, true);
$('#mail-list').addEventListener('contextmenu', (e) => { if (S.local === 'today' && e.target.closest('.row[data-task]')) { e.stopPropagation(); e.preventDefault(); } }, true);

// ---------------------------------------------------------------- the task card in the reader
async function tdShowTask(id) {
  S.selected = null; S.message = null; TD.task = id;
  $$('#mail-list .row').forEach((r) => r.classList.toggle('sel', Number(r.dataset.task) === id && r.classList.contains('td-task')));
  ['#reader-empty', '#reader-body', '#reader-multi'].forEach((s) => $(s).classList.add('hidden'));
  const card = $('#reader-task');
  card.classList.remove('hidden');
  card.innerHTML = '<div class="td-card"><div class="skeleton"></div></div>';
  let d;
  try { d = await api('taskGet', { id }); } catch (err) { card.innerHTML = `<div class="td-card"><p class="muted">${esc(err.message)}</p></div>`; return; }
  if (TD.task !== id) return;
  TK.detail = d;  // taskAction (tasks.js) works on the task shown.
  const t = d.task, due = ucDue(tkText(t.due)), open = TK_OPEN.includes(t.status);
  const field = (k) => tkText(t[k]) ? `<div class="td-field"><span class="muted">${TK_LABEL[k]}</span><p>${esc(tkText(t[k]))}</p></div>` : '';
  card.innerHTML = `<div class="td-card">
    <div class="td-card-head"><b>#${t.id}</b>${t.system ? `<span class="tk-sys">${esc(t.system)}</span>` : ''}${tkStatus(t.status)}</div>
    <h2>${esc(tkText(t.content))}</h2>
    <div class="td-card-meta">${due ? `<span class="uc-due${due.late ? ' late' : ''}">${esc(due.text)}</span>` : ''}${tkText(t.due) ? `计划完成 ${esc(tkText(t.due))}` : '没有计划完成日期'}</div>
    <div class="row-gap">${open ? '<button class="btn primary sm" data-tdc="progress"><i data-icon="pen"></i>记录进展</button><button class="btn sm" data-tdc="complete"><i data-icon="check"></i>标记完成</button>' : ''}
      <button class="btn ghost sm" data-tdc="open">在任务中心打开</button></div>
    ${field('latest')}${field('next')}${field('blockers')}${field('owner')}</div>`;
  renderIcons(card);
  $$('[data-tdc]', card).forEach((b) => (b.onclick = () => (b.dataset.tdc === 'open' ? openTask(id) : taskAction(b.dataset.tdc))));
  if (typeof chatMailChanged === 'function') chatMailChanged();
}
function tdHideTask() { TD.task = null; $('#reader-task').classList.add('hidden'); }

// A mail chosen, or a selection: the card goes. Leaving the page: the card and the week go.
const _tdSelectMessage = selectMessage;
selectMessage = function (...args) { if (TD.task) tdHideTask(); return _tdSelectMessage(...args); };
const _tdRenderSelection = renderSelection;
renderSelection = function () {
  _tdRenderSelection();
  if (TD.task && S.multi.size >= 2) tdHideTask();
  else if (TD.task) $('#reader-empty').classList.add('hidden');
};
const _tdRenderFolders = renderFolders;
renderFolders = function () {
  _tdRenderFolders();
  const here = S.local === 'today';
  $('#app').classList.toggle('today-mode', here);
  if (!here && TD.task) tdHideTask();
};

// Changes elsewhere bring a sub-page up to date: a task saved, a card corrected or done.
const _tdAfterSave = afterSave;
afterSave = function (result, quiet) { _tdAfterSave(result, quiet); if (S.local === 'today' && TD.sub) showToday(TD.sub, true).then(() => TD.task && tdShowTask(TD.task)); };
// ucRefreshStatus runs after every correction and every background round of the 理解层.
const _tdUcRefreshStatus = ucRefreshStatus;
ucRefreshStatus = function () { if (S.local === 'today' && TD.sub && TD.sub !== 'week') showToday(TD.sub, true); return _tdUcRefreshStatus(); };
// The waiting mail now lives on 今天 → 在等别人.
const _tdUcOpen = ucOpen;
ucOpen = function (kind) { return kind === 'wait' ? showToday('wait') : _tdUcOpen(kind); };

$('#btn-refresh').addEventListener('click', () => { if (S.local === 'today') showToday(TD.sub, true); });
