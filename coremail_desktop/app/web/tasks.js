'use strict';
/* 鸿雁 · 任务中心: the task list kept by the backend (tasks/center.py). List, detail with
   its timeline, the change dialogs (each shows the differences before saving and the full
   list of unfinished tasks after), mail links, and the import / switch-over settings.
   Uses app.js and contacts.js globals. */

const TK = { view: 'open', query: '', rows: [], counts: null, selected: null, detail: null, status: null, token: 0, tab: 'story', story: null };
const TK_OPEN = ['未开始', '待处理', '进行中', '受阻', '待验收'];
const TK_LABEL = { id: '编号', system: '系统', content: '任务内容', priority: '优先级', status: '状态', latest: '最新进展', owner: '负责人／责任边界',
  start: '开始日期', due: '计划完成日期', end: '结束日期', blockers: '阻塞与依赖', next: '下一步', updated: '最近更新日期',
  history: '进展与历史记录', acceptance: '完成／验收标准' };
const TK_KIND = { imported: '导入', created: '新建', edited: '编辑', progress: '记录进展', completed: '标记完成', canceled: '取消任务',
  reopened: '重新打开', linked: '关联邮件', unlinked: '取消关联邮件', external: 'Excel 外部修改', undone: '撤销', suggested: 'AI 建议' };
const TK_TABS = { open: '未完成', done: '已完成', canceled: '已取消', all: '全部' };
const TK_DATES = ['start', 'due', 'end', 'updated'];
const TK_LONG = ['content', 'latest', 'owner', 'blockers', 'next', 'acceptance', 'history'];
const tkStatusClass = (s) => ({ 受阻: 'blocked', 进行中: 'doing', 待验收: 'review', 待处理: 'pending', 已完成: 'done', 已取消: 'canceled' }[s] || 'todo');
const tkText = (v) => (v == null ? '' : typeof v === 'object' ? (v.text || '') : String(v));
const tkStatus = (s) => `<span class="tk-status s-${tkStatusClass(s)}">${esc(s)}</span>`;
const tkToday = () => (TK.status && TK.status.today) || isoDay(new Date());

// ---------------------------------------------------------------- view switching
function renderTasksNav() {
  $('#nav-tasks').classList.toggle('on', S.local === 'tasks');
  $('#app').classList.toggle('tasks-mode', S.local === 'tasks');
  $('#tasks-count').textContent = TK.counts && TK.counts.open ? TK.counts.open : '';
}
const _tasksRenderFolders = renderFolders;
renderFolders = function () { _tasksRenderFolders(); renderTasksNav(); };

async function openTasks() {
  S.local = 'tasks'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
  renderFolders();
  await loadTasks();
}
$('#nav-tasks').onclick = openTasks;

async function loadTasks() {
  const token = ++TK.token;
  try {
    const [view, status] = await Promise.all([api('tasksOverview', { view: TK.view, query: TK.query }), api('tasksStatus')]);
    if (token !== TK.token) return;
    TK.rows = TK.ids ? view.rows.filter((r) => TK.ids.includes(r.id)) : view.rows; TK.counts = view.counts; TK.status = status;
    renderTaskList(); renderTasksNav();
    if (TK.gantt) showGantt();
    else if (TK.selected && TK.rows.some((r) => r.id === TK.selected)) showTask(TK.selected);
    else if (TK.selected == null || !TK.detail) renderTaskEmpty();
  } catch (err) { $('#tk-list').innerHTML = `<div class="list-foot">${esc(err.message)}</div>`; }
}

function renderTaskList() {
  const st = TK.status, c = TK.counts;
  const n = { open: c.open, done: c.done, canceled: c.canceled, all: c.total };
  $$('#tk-tabs button').forEach((b) => { b.classList.toggle('on', b.dataset.view === TK.view); b.textContent = `${TK_TABS[b.dataset.view]} ${n[b.dataset.view]}`; });
  $('#tk-sub').textContent = st.mode === 'trial' ? '演练模式' : '';
  $('#tk-new').classList.toggle('hidden', st.mode === 'off');
  $('#tk-gantt').classList.toggle('hidden', st.mode === 'off');
  $('#tk-ask').classList.toggle('hidden', st.mode === 'off');
  $('#tk-report').classList.toggle('hidden', st.mode === 'off');
  $('.tk-filter-row').classList.toggle('hidden', st.mode === 'off');
  renderTaskBanner();
  if (st.mode === 'off') {
    $('#tk-list').innerHTML = '<div class="list-foot">还没有导入任务。<br>在「设置 → 任务中心」中选择任务文件夹，检查并导入两份工作簿。<br><br><button class="btn primary sm" id="tk-setup">打开设置</button></div>';
    $('#tk-setup').onclick = openTaskSettings; $('#tk-foot').innerHTML = '';
    return;
  }
  $('#tk-list').innerHTML = TK.rows.length ? TK.rows.map((r) => `
    <div class="tk-row${r.id === TK.selected ? ' sel' : ''}" data-id="${r.id}" role="option">
      <div class="tk-line1"><b class="tk-id">#${r.id}</b><span class="tk-sys">${esc(r.system)}</span>${r.priority ? `<span class="tk-pri">${esc(r.priority)}</span>` : ''}<span class="spacer"></span>${tkStatus(r.status)}</div>
      <div class="tk-content">${esc(r.content)}</div>
      <div class="tk-meta">${r.pending ? `<span class="tk-newmail">待确认 ${r.pending}</span>` : ''}${r.newMail ? `<span class="tk-newmail">有新邮件 ${r.newMail}</span>` : ''}${r.due ? `<span class="${r.overdue ? 'tk-overdue' : ''}">计划 ${esc(r.due)}${r.overdue ? ' · 已逾期' : ''}</span>` : ''}${r.end ? `<span>结束 ${esc(r.end)}</span>` : ''}${r.updated ? `<span>更新 ${esc(r.updated)}</span>` : ''}</div>
    </div>`).join('') : `<div class="list-foot">${TK.query || TK.ids ? '没有符合条件的任务' : TK.view === 'open' ? '当前无未完成任务' : '没有任务'}</div>`;
  const ex = st.export || {};
  const where = st.mode === 'live' ? '任务文件夹' : '演练文件夹';
  const line = ex.state === 'ok' ? `已导出到${where}${ex.at ? ' · ' + esc(ex.at.slice(5, 16)) : ''}` : ex.state === 'locked' ? '待导出：Excel 正在打开任务文件'
    : ex.state === 'external' ? '导出已暂停：任务文件在外部被改过' : ex.state === 'in_use' ? '待导出：任务文件被其他程序占用'
    : ex.state === 'denied' ? '待导出：没有写入任务文件夹的权限' : ex.state === 'failed' ? '导出没有成功' : '';
  $('#tk-foot').innerHTML = `<span class="muted small">${line}</span><span class="spacer"></span><button class="link-btn" id="tk-verify">核验清单</button>`;
  $('#tk-verify').onclick = async () => { try { showVerify({ verify: await api('tasksVerify'), export: TK.status.export }); } catch (err) { fail(err); } };
}

function renderTaskBanner() {
  const st = TK.status, ex = st.export || {}, parts = [];
  if (st.pending) parts.push(`<span>有 ${st.pending} 条修改等待你确认（AI 根据新邮件的建议，或在 Excel 中做的修改）。</span><button class="btn sm" data-tk="review">去确认</button>`);
  if (ex.state === 'external') {
    parts.push(`<span>任务文件夹中的 ${esc((ex.external || []).join('、'))} 在鸿雁之外被改过，已暂停导出，没有覆盖。</span>
      <button class="btn sm" data-tk="ext-import">读入外部修改</button><button class="btn sm" data-tk="ext-overwrite">以鸿雁为准</button>`);
  } else if (ex.state === 'locked') parts.push('<span>任务文件正被 Excel 打开：修改已保存在鸿雁，还没有导出。关闭 Excel 后重试。</span><button class="btn sm" data-tk="export">立即导出</button>');
  else if (ex.state === 'in_use') parts.push('<span>任务文件被其他程序占用（如资源管理器预览窗格、WPS、同步软件），修改已保存在鸿雁，还没有导出。关闭占用的程序后重试。</span><button class="btn sm" data-tk="export">立即导出</button>');
  else if (ex.state === 'denied') parts.push('<span>鸿雁没有权限写入任务文件夹（可能被安全软件拦截），修改已保存在鸿雁，任务文件夹没有改动。</span><button class="btn sm" data-tk="export">重试导出</button>');
  else if (ex.state === 'failed') parts.push('<span>导出没有成功，修改已保存在鸿雁中。</span><button class="btn sm" data-tk="export">重试导出</button>');
  $('#tk-banner').classList.toggle('hidden', !parts.length);
  $('#tk-banner').innerHTML = parts.map((p) => `<div class="tk-banner-row">${p}</div>`).join('');
}
$('#tk-banner').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-tk]'); if (!b) return;
  try {
    if (b.dataset.tk === 'review') { reviewPending(); return; }
    if (b.dataset.tk === 'export') { const ex = await api('tasksExport'); toast(ex.state === 'ok' ? '已导出' : '仍未能导出', { error: ex.state !== 'ok' }); }
    if (b.dataset.tk === 'ext-overwrite') {
      if (!(await confirmDialog('以鸿雁为准', '外部改过的文件会先复制一份到鸿雁数据目录的 tasks\\displaced 下，再用鸿雁中的任务覆盖。', '覆盖'))) return;
      await api('tasksResolve', { action: 'overwrite' }); toast('已按鸿雁中的任务重新导出');
    }
    if (b.dataset.tk === 'ext-import') {
      const r = await api('tasksResolve', { action: 'import' });
      toast(r.pending ? `读到 ${r.pending} 处外部修改，请逐条审核` : '没有发现与鸿雁不同的修改');
      if (r.pending) { await loadTasks(); reviewPending(); return; }
    }
    loadTasks();
  } catch (err) { fail(err); }
});

$('#tk-tabs').addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  TK.view = b.dataset.view; loadTasks();
});
$('#tk-list').addEventListener('click', (e) => {
  const row = e.target.closest('.tk-row'); if (row) showTask(Number(row.dataset.id));
});
$('#tk-list').addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'j' && e.key !== 'k') return;
  const i = TK.rows.findIndex((r) => r.id === TK.selected);
  const next = TK.rows[Math.max(0, Math.min(TK.rows.length - 1, i + (e.key === 'ArrowDown' || e.key === 'j' ? 1 : -1)))];
  if (next) { e.preventDefault(); showTask(next.id); }
});
$('#tk-list').tabIndex = 0;

// ---------------------------------------------------------------- detail
function renderTaskEmpty() {
  TK.detail = null;
  $('#tk-detail').innerHTML = `<div class="empty"><div class="empty-art"></div><p>选择一条任务查看详情和时间线</p>
    <p class="muted small">每次保存后都会核验，并列出全部未完成任务。</p></div>`;
}

async function showTask(id) {
  TK.selected = id; TK.gantt = false;
  $$('#tk-list .tk-row').forEach((el) => el.classList.toggle('sel', Number(el.dataset.id) === id));
  const el = $(`#tk-list .tk-row[data-id="${id}"]`); if (el) el.scrollIntoView({ block: 'nearest' });
  try {
    const [d, story] = await Promise.all([api('taskGet', { id }), api('taskStory', { id }).catch(() => null)]);
    if (TK.selected !== id) return;
    TK.detail = d; TK.story = story; renderTaskDetail();
    if (typeof chatMailChanged === 'function') chatMailChanged();  // 问邮箱 (ai.js) can take the selected task along.
    nameSteps(id);
  } catch (err) { fail(err); }
}

// 事件演进图: the task's steps newest first, each a short title and one line; a click shows
// the full text, the mail it came from and what changed. Titles come from the AI once per step
// (kept by the backend) when it is set up and agreed to; until then from the text itself.
const STEP_ICON = { start: 'flag', analysis: 'search', contact: 'mail', wait: 'clock', blocked: 'alert', progress: 'pen',
  done: 'check', cancel: 'close', reopen: 'undo', change: 'settings', mail: 'mail' };
function stepDay(day) {
  if (!day) return '早期';
  return day.slice(0, 4) === String(new Date().getFullYear()) ? day.slice(5) : day;
}
function storyHtml(story) {
  if (!story) return '<p class="muted">演进图暂时无法生成。</p>';
  if (!story.steps.length) return '<p class="muted">还没有进展记录。</p>';
  const naming = TK.naming === story.id ? '<span class="tk-spin"></span>AI 正在为各个环节起标题…'
    : story.missing.length && TK.status && TK.status.mode !== 'off' ? `<button class="link-btn" id="tk-name-steps">用 AI 为 ${story.missing.length} 个环节起标题</button>` : '';
  const waiting = (story.pending || []).filter((p) => p.kind === 'suggested').map((p) => `
    <div class="tk-step k-suggest">
      <div class="tk-step-icon"><i data-icon="spark"></i></div>
      <div class="tk-step-body"><div class="tk-step-title"><b>待确认 — ${esc(TK_OP[(p.action || {}).op] || 'AI 建议')}</b><span class="tk-pending">AI 建议</span></div>
        <div class="tk-step-sum">${esc(p.note || p.basis || '')}</div>
        ${(p.mails || []).length ? `<div class="tk-mailrefs">${p.mails.map((m) => `<button class="tk-mailref" data-open="${esc(m.mid)}"><i data-icon="mail"></i>${esc(m.subject || '（无主题）')}<span>${esc(String(m.date || '').slice(5, 16))}</span></button>`).join('')}</div>` : ''}
        ${changeRows(p.changes)}
        <div class="row-gap tk-step-acts"><button class="btn sm primary" data-accept="${p.seq}">采纳</button><button class="btn ghost sm" data-reject="${p.seq}">驳回</button></div>
      </div></div>`).join('');
  return `<div class="tk-story">${naming ? `<div class="tk-story-note muted small">${naming}</div>` : ''}${waiting}${story.steps.map((s, i) => `
    <div class="tk-step k-${s.kind}${i === 0 ? ' now' : ''}" data-key="${esc(s.key)}">
      <div class="tk-step-icon"><i data-icon="${STEP_ICON[s.kind] || 'pen'}"></i></div>
      <div class="tk-step-body"><div class="tk-step-main" title="点击查看详情">
        <div class="tk-step-title"><b>${esc(stepDay(s.day))} — ${esc(s.title)}</b>${s.actor === 'ai' ? '<span class="tk-ai">AI</span>' : ''}${i === 0 ? tkStatus(story.status) : ''}</div>
        <div class="tk-step-sum">${esc(s.summary)}</div></div>
        <div class="tk-step-more">
          <div class="tk-step-text">${esc(s.text)}</div>
          ${s.basis ? `<div class="tk-ev-note">${s.mails && s.mails.length ? '依据' : '指令'}：「${esc(s.basis)}」</div>` : ''}
          ${(s.mails || []).length ? `<div class="tk-mailrefs">${s.mails.map((m) => `<button class="tk-mailref" data-open="${esc(m.mid)}" title="打开邮件"><i data-icon="mail"></i>${esc(m.subject || '（无主题）')}<span>${esc(String(m.date || '').slice(5, 16))}</span></button>`).join('')}</div>` : ''}
          ${s.changes && Object.keys(s.changes).length ? changeRows(s.changes) : ''}
        </div>
        ${(story.undoable || []).includes(s.seq) ? `<div class="row-gap tk-step-undo"><button class="link-btn" data-undo="${s.seq}" title="这一步记错了（例如 AI 把别的任务的邮件记到这里）：撤销它，变更记录里记一条撤销">撤销这一步</button></div>` : ''}
      </div></div>`).join('')}</div>`;
}
async function nameSteps(id, asked) {
  const story = TK.story;
  if (!story || story.id !== id || !story.missing.length || TK.naming) return;
  let agreed = false, prompted = false;
  try { agreed = localStorage.getItem('hy-tk-ai') === '1'; prompted = localStorage.getItem('hy-tk-ai-asked') === '1'; } catch (e) { /* optional */ }
  if (!agreed) {
    // Asked once by itself (when the AI is set up); after a no, only the link asks again.
    const ai = await api('aiSettings').catch(() => null);
    if (!ai || !ai.ready) { if (asked) openSettings('ai'); return; }
    if (!asked && prompted) return;
    try { localStorage.setItem('hy-tk-ai-asked', '1'); } catch (e) { /* optional */ }
    if (!(await tkAiConsent())) return;
    nameOpenTasks(id);
  }
  TK.naming = id; if (TK.tab === 'story' && TK.detail && TK.detail.task.id === id) renderTaskTab('story');
  try {
    const named = await api('taskStoryTitles', { id });
    if (TK.story && TK.story.id === id) TK.story = named;
  } catch (err) {
    if (asked && err.code !== 'AI_NOT_CONFIGURED') fail(err);
    if (asked && err.code === 'AI_NOT_CONFIGURED') openSettings('ai');
  } finally {
    TK.naming = null;
    if (TK.tab === 'story' && TK.detail && TK.detail.task.id === id) renderTaskTab('story');
  }
}

// Right after the user agrees: name the steps of every unfinished task in the background.
async function nameOpenTasks(skip) {
  let rows = [];
  try { rows = (await api('tasksOverview', { view: 'open' })).rows; } catch (e) { return; }
  for (const r of rows) {
    if (r.id === skip) continue;
    try {
      const named = await api('taskStoryTitles', { id: r.id });
      if (TK.story && TK.story.id === r.id) { TK.story = named; if (TK.tab === 'story') renderTaskTab('story'); }
    } catch (e) { return; }  // AI unavailable: stop quietly; opening a task tries again.
  }
}

// The timeline: task changes and linked mail, newest first. A change made from mail shows that
// mail; the latest change (not a new task, not an undo) can be undone.
function taskTimeline(d) {
  const mailTime = (s) => { const t = Date.parse(String(s || '').replace(' ', 'T')); return Number.isNaN(t) ? 0 : t / 1000; };
  const linkedFrom = new Set(d.events.flatMap((e) => (e.mails || []).map((m) => m.mid)));
  const items = d.events.map((e) => ({ at: e.at || 0, html: eventHtml(e) }))
    .concat(d.links.filter((l) => !linkedFrom.has(l.mid)).map((l) => ({ at: mailTime(l.date) || l.at || 0, html: `<div class="tk-event tk-mailev">
      <div class="tk-ev-head"><i data-icon="mail"></i><b>关联邮件</b><span>${esc(String(l.date || '').slice(0, 16))}</span><span class="spacer"></span>
      <button class="link-btn" data-open="${esc(l.mid)}">打开</button></div><div class="tk-ev-note">${esc(l.subject || '（无主题）')}${l.from ? ' · ' + esc(l.from) : ''}</div></div>` })));
  items.sort((a, b) => b.at - a.at);
  return items.map((i) => i.html).join('') || '<p class="muted">没有记录。</p>';
}
function eventHtml(e) {
  const undoable = (TK.detail.undoable || []).includes(e.seq);
  const when = e.at ? new Date(e.at * 1000).toLocaleString('zh-CN', { hour12: false }) : '';
  const state = e.review === 'pending' ? '<span class="tk-pending">待确认</span>' : e.review === 'rejected' ? '<span class="tk-tbd">已驳回</span>'
    : e.review === 'accepted' ? '<span class="tk-tbd">已采纳</span>' : '';
  const who = e.actor === 'ai' ? '<span class="tk-ai" title="由 AI 生成">AI</span>' : e.actor === 'excel' ? '<span class="tk-ai">Excel</span>' : '';
  const basis = e.basis ? `<div class="tk-ev-note">${e.mails && e.mails.length ? '依据' : '指令'}：「${esc(e.basis)}」</div>` : '';
  const mails = (e.mails || []).map((m) => `<button class="tk-mailref" data-open="${esc(m.mid)}" title="打开邮件"><i data-icon="mail"></i>${esc(m.subject || '（无主题）')}<span>${esc(String(m.date || '').slice(5, 16))}</span></button>`).join('');
  const note = e.kind === 'imported' ? `从 ${esc(e.note)} 导入` : e.note ? `${e.kind === 'canceled' ? '原因' : e.kind === 'linked' || e.kind === 'unlinked' ? '邮件' : '依据'}：${esc(e.note)}` : '';
  const actions = e.review === 'pending' ? `<div class="row-gap"><button class="btn sm" data-accept="${e.seq}">采纳</button><button class="btn ghost sm" data-reject="${e.seq}">驳回</button></div>` : '';
  return `<div class="tk-event"><div class="tk-ev-head"><b>${TK_KIND[e.kind] || esc(e.kind)}</b>${who}<span>${esc(e.date || '')}</span>${state}<span class="spacer"></span>
    ${undoable ? `<button class="link-btn" data-undo="${e.seq}">撤销这一步</button>` : ''}<span class="muted small">${esc(when)}</span></div>
    ${basis}${mails ? `<div class="tk-mailrefs">${mails}</div>` : ''}${note ? `<div class="tk-ev-note">${note}</div>` : ''}${changeRows(e.changes)}${actions}</div>`;
}

// The fields block is folded by default so the story comes first; the choice is remembered.
function tkFieldsOpen() { try { return localStorage.getItem('hy-tk-fields') === '1'; } catch (e) { return false; } }

function renderTaskDetail() {
  const d = TK.detail, t = d.task, open = TK_OPEN.includes(t.status);
  const field = (k, wide) => `<div class="tk-field${wide ? ' wide' : ''}"><dt>${TK_LABEL[k]}</dt><dd>${t[k] ? esc(tkText(t[k]))
    : `<span class="tk-tbd">${d.missing.includes(k) ? '待确认' : '—'}</span>`}</dd></div>`;
  $('#tk-detail').innerHTML = `<div class="tk-detail">
    <div class="tk-head"><span class="tk-id big">#${t.id}</span><span class="tk-sys">${esc(t.system)}</span>${tkStatus(t.status)}
      ${t.priority ? `<span class="tk-pri">优先级 ${esc(t.priority)}</span>` : ''}${d.overdue ? '<span class="tk-overdue">已逾期</span>' : ''}
      <span class="spacer"></span><span class="muted small">${esc(d.sheet)}</span></div>
    <h1 class="tk-title">${esc(t.content)}</h1>
    <div class="tk-actions">
      <button class="btn primary sm" data-act="progress"><i data-icon="pen"></i>记录进展</button>
      <button class="btn sm" data-act="edit">编辑</button>
      ${open ? '<button class="btn sm" data-act="complete"><i data-icon="check"></i>标记完成</button><button class="btn sm" data-act="cancel">取消任务</button>'
    : '<button class="btn sm" data-act="reopen"><i data-icon="undo"></i>重新打开</button>'}
    </div>
    <div class="tk-facts"><span>${[['start', '开始'], ['due', '计划完成'], ['end', '结束'], ['updated', '更新']].filter(([k]) => t[k]).map(([k, n]) => `${n} ${esc(tkText(t[k]))}`).join(' · ') || '没有日期'}</span>
      <button class="link-btn" id="tk-fields-toggle">${tkFieldsOpen() ? '收起详细信息' : '详细信息'}</button></div>
    ${relationsHtml(d.relations)}
    <dl class="tk-fields${tkFieldsOpen() ? '' : ' hidden'}">${field('latest', true)}${field('owner', true)}${field('start')}${field('due')}${field('end')}${field('updated')}
      ${field('blockers', true)}${field('next', true)}${field('acceptance', true)}</dl>
    <div class="seg tk-dtabs"><button data-tab="story">演进图</button><button data-tab="timeline">变更记录 ${d.events.length}</button><button data-tab="history">进展原文</button><button data-tab="mail">关联邮件 ${d.links.length}</button></div>
    <div id="tk-tab" class="tk-tab"></div></div>`;
  renderIcons($('#tk-detail'));
  renderTaskTab(TK.tab);
}

function renderTaskTab(tab) {
  TK.tab = tab;
  const d = TK.detail;
  $$('#tk-detail .tk-dtabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  let html = '';
  if (tab === 'story') {
    html = storyHtml(TK.story);
  } else if (tab === 'history') {
    const text = tkText(d.task.history);
    html = text ? `<div class="tk-history">${text.split('\n').map((line) => {
      const m = line.match(/^(\d{4}-\d{2}-\d{2})(.*)$/);
      return `<div>${m ? `<b>${esc(m[1])}</b>${esc(m[2])}` : esc(line) || '&nbsp;'}</div>`;
    }).join('')}</div>` : '<p class="muted">还没有进展记录。</p>';
  } else if (tab === 'mail') {
    const fresh = (d.newMails || []).map((m) => `<div class="tk-link new"><i data-icon="mail"></i><div class="tk-link-main"><b>${esc(m.subject || '（无主题）')}</b>
      <span class="muted small">${esc(m.from || '')} · ${esc(m.date || '')} · 同一会话的新邮件，尚未关联</span></div>
      <button class="btn sm" data-open="${esc(m.id)}">打开</button><button class="btn primary sm" data-use="${esc(m.id)}">用这封更新</button></div>`).join('');
    html = (fresh ? `<h4 class="tk-sub">有新邮件</h4>${fresh}<h4 class="tk-sub">已关联</h4>` : '') + (d.links.length ? d.links.map((l) => `<div class="tk-link"><i data-icon="mail"></i><div class="tk-link-main"><b>${esc(l.subject || '（无主题）')}</b>
      <span class="muted small">${esc(l.date || '')}${l.source === 'archive' ? ' · 本地存档' : ''}</span></div>
      <button class="btn sm" data-open="${esc(l.mid)}">打开</button><button class="btn ghost sm" data-unlink="${esc(l.id)}">取消关联</button></div>`).join('')
      : '<p class="muted">还没有关联邮件。</p>') + '<p class="muted small">阅读邮件时，在「更多」菜单中选择「关联到任务…」。</p>';
  } else {
    html = taskTimeline(d);
  }
  $('#tk-tab').innerHTML = html;
  renderIcons($('#tk-tab'));
}

// Field changes as label: old → new; for the history column only the added text.
function changeRows(changes) {
  const rows = Object.entries(changes || {}).map(([f, [a, b]]) => {
    a = tkText(a); b = tkText(b);
    if (f === 'history' && b.startsWith(a)) return `<div class="tk-change"><span class="tk-flabel">${TK_LABEL[f]}</span><span class="tk-new">＋ ${esc(b.slice(a.length).replace(/^\n/, ''))}</span></div>`;
    return `<div class="tk-change"><span class="tk-flabel">${TK_LABEL[f] || esc(f)}</span>${a ? `<span class="tk-old">${esc(a)}</span><span class="tk-arrow">→</span>` : ''}<span class="tk-new">${b ? esc(b) : '（清空）'}</span></div>`;
  });
  return rows.length ? `<div class="tk-changes">${rows.join('')}</div>` : '';
}

$('#tk-detail').addEventListener('click', async (e) => {
  const tab = e.target.closest('[data-tab]'); if (tab) { renderTaskTab(tab.dataset.tab); return; }
  const act = e.target.closest('[data-act]'); if (act) { taskAction(act.dataset.act); return; }
  const goto = e.target.closest('[data-goto]'); if (goto) { showTask(Number(goto.dataset.goto)); return; }
  if (e.target.closest('#tk-rel-edit')) { editRelations(); return; }
  if (e.target.closest('#tk-name-steps')) { nameSteps(TK.detail.task.id, true); return; }
  if (e.target.closest('#tk-fields-toggle')) {
    try { localStorage.setItem('hy-tk-fields', tkFieldsOpen() ? '0' : '1'); } catch (err) { /* optional */ }
    renderTaskDetail(); return;
  }
  const step = e.target.closest('.tk-step-main'); if (step) { step.closest('.tk-step').classList.toggle('open'); return; }
  const opener = e.target.closest('[data-open]'); if (opener) { api('openReader', { mid: opener.dataset.open }).catch(fail); return; }
  const use = e.target.closest('[data-use]'); if (use) { mailToTask([use.dataset.use], { target: TK.detail.task.id }); return; }
  const undo = e.target.closest('[data-undo]');
  if (undo) {
    if (!(await confirmDialog('撤销这一步', '把这一步改过的内容恢复原样（之后又被改过的字段保留新的值），并取消这一步带来的邮件关联。进展原文里保留原来的记录并注明已撤销，变更记录里记一条撤销；演进图里不再显示这一步。', '撤销'))) return;
    runChange(() => api('taskUndo', { seq: Number(undo.dataset.undo) }));
    return;
  }
  const unlink = e.target.closest('[data-unlink]');
  const accept = e.target.closest('[data-accept]'), reject = e.target.closest('[data-reject]');
  try {
    if (unlink) {
      if (!(await confirmDialog('取消关联', '只去掉这条任务与邮件的关联，邮件本身不受影响。', '取消关联'))) return;
      afterSave(await api('taskUnlink', { id: TK.detail.task.id, link: unlink.dataset.unlink }), true);
    } else if (accept || reject) {
      afterSave(await api('taskReview', { seq: Number((accept || reject).dataset.accept || (accept || reject).dataset.reject), accept: !!accept }), !!reject);
    }
  } catch (err) { fail(err); if (err.code === 'TASK_CONFLICT') loadTasks(); }
});

// ---------------------------------------------------------------- 任务关系与甘特图 (P1)
// 上级 / 依赖 #N live in 阻塞与依赖 (the backend reads them, tasks/plan.py): no extra column.
const REL_PARENT = /(?:上级|属于|父任务|母任务)\s*[:：]?\s*#\s*\d{1,5}/g;
const REL_DEPS = /(?:依赖|前置|等待|先完成)\s*[:：]?\s*(?:#\s*\d{1,5}\s*[、,，和及/]?\s*)+/g;
function relationsHtml(rel) {
  if (!rel) return '';
  const chip = (t) => `<a class="tk-rel-chip" data-goto="${t.id}" title="${esc(t.content)}">#${t.id}<span class="tk-rel-status s-${tkStatusClass(t.status)}">${esc(t.status)}</span></a>`;
  const line = (label, items, extra = '') => items.length ? `<div class="tk-rel-line"><span class="tk-rel-label">${label}</span>${items.map(chip).join('')}${extra}</div>` : '';
  const roll = rel.rollup && rel.rollup.total ? `<span class="tk-rel-roll"><span style="width:${Math.round(100 * rel.rollup.done / rel.rollup.total)}%"></span></span><span class="muted small">${rel.rollup.done}/${rel.rollup.total} 完成</span>` : '';
  const body = line('上级', rel.parent ? [rel.parent] : []) + line('子任务', rel.children, roll) +
    line('前置', rel.deps, rel.waiting.length ? `<span class="muted small">还有 ${rel.waiting.length} 条未完成</span>` : '') + line('后续', rel.next) +
    (rel.missing.length ? `<div class="tk-rel-line"><span class="tk-rel-label">无效</span><span class="muted small">阻塞与依赖里写的 ${rel.missing.map((n) => '#' + n).join('、')} 不存在</span></div>` : '');
  return `<div class="tk-rel">${body || '<span class="muted small">没有上级、子任务或前置任务。</span>'}<button class="link-btn" id="tk-rel-edit">设置关系</button></div>`;
}
async function editRelations() {
  const d = TK.detail, t = d.task, rel = d.relations || {};
  const v = await promptDialog({ title: `设置关系 · #${t.id}`, okText: '检查修改',
    text: '关系写在「阻塞与依赖」里（如「上级 #8」「依赖 #12、#13」），Excel 中同样可见。前置任务完成后，鸿雁会提示把受阻的后续任务改回进行中。',
    body: `<div class="tk-form">${tkRow('上级任务', `<input data-f="parent" type="text" value="${rel.parent ? '#' + rel.parent.id : ''}" placeholder="例如 #8，没有就留空" spellcheck="false">`)}
      ${tkRow('前置任务', `<input data-f="deps" type="text" value="${(rel.deps || []).map((x) => '#' + x.id).join('、')}" placeholder="例如 #12、#13：它们完成后才能开始" spellcheck="false">`)}</div>`,
    collect: () => tkCollect($('#prompt')) });
  if (!v) return;
  const num = (s) => [...String(s).matchAll(/\d{1,5}/g)].map((m) => Number(m[0])).filter((n) => n !== t.id);
  const parent = num(v.parent)[0], deps = [...new Set(num(v.deps))];
  const rest = tkText(t.blockers).replace(REL_PARENT, '').replace(REL_DEPS, '').replace(/^[\s；;，,。]+|[\s；;，,]+$/g, '').replace(/([；;，,])\s*[；;，,]+/g, '$1');
  const blockers = [rest, parent ? `上级 #${parent}` : '', deps.length ? `依赖 ${deps.map((n) => '#' + n).join('、')}` : ''].filter(Boolean).join('；');
  if (blockers === tkText(t.blockers)) { toast('没有修改'); return; }
  if (!(await confirmChanges(`设置关系 · #${t.id}`, { blockers: [t.blockers, blockers] }))) return;
  runChange(() => api('taskUpdate', { id: t.id, rev: d.rev, fields: { blockers }, note: '设置任务关系' }));
}
function offerUnblock(items) {
  const names = items.map((u) => `#${u.id}`).join('、');
  toast(`前置任务已完成：${names} 不再受阻`, { ms: 15000, action: { label: '改为进行中', run: async () => {
    for (const u of items) {
      try { await api('taskUpdate', { id: u.id, rev: u.rev, fields: { status: '进行中' }, note: '前置任务已完成' }); }
      catch (err) { fail(err); }
    }
    toast(`${names} 已改为进行中`); loadTasks();
  } } });
}

$('#tk-gantt').onclick = () => { TK.gantt = true; showGantt(); };
async function showGantt() {
  TK.gantt = true; TK.selected = null; TK.detail = null;
  $$('#tk-list .tk-row').forEach((el) => el.classList.remove('sel'));
  let g;
  try { g = await api('tasksGantt', { view: TK.view, query: TK.query }); } catch (err) { fail(err); return; }
  if (!TK.gantt) return;
  if (TK.ids) g.rows = g.rows.filter((r) => TK.ids.includes(r.id));
  renderGantt(g);
}
function renderGantt(g) {
  const pane = $('#tk-detail');
  const head = `<div class="tk-gantt-head"><b>甘特图</b><span class="muted small">${g.rows.length} 条有日期的任务 · ${TK_TABS[TK.view]}${TK.query ? ' · ' + esc(TK.query) : ''}</span><span class="spacer"></span>
    <span class="tk-gl"><i class="gl-bar"></i>计划</span><span class="tk-gl"><i class="gl-bar late"></i>逾期</span><span class="tk-gl"><i class="gl-bar wait"></i>等前置</span><span class="tk-gl"><i class="gl-bar done"></i>已完成</span></div>`;
  if (!g.rows.length) {
    pane.innerHTML = `<div class="tk-gantt">${head}<div class="empty"><p>列表里的任务都没有开始日期或计划完成日期。</p><p class="muted small">在任务的「编辑」中填写日期后就会出现在这里。</p></div></div>`;
    return;
  }
  const ms = (s) => Date.parse(s + 'T00:00:00Z'), day = (s) => Math.round((ms(s) - ms(g.from)) / 864e5);
  const days = day(g.to) + 1, LABEL = 250, H = 30;
  const W = Math.max(4, Math.min(30, Math.floor(((pane.clientWidth || 800) - LABEL - 24) / days)));
  const width = days * W;
  // The scale: a tick per month, and per week when there is room.
  let scale = '', last = -99;
  for (let i = 0; i < days; i++) {
    const d = new Date(ms(g.from) + i * 864e5), date = d.getUTCDate(), x = i * W;
    if (date === 1 || i === 0) { scale += `<span class="tk-gm" style="left:${x}px">${d.getUTCFullYear() !== new Date().getFullYear() ? d.getUTCFullYear() + '-' : ''}${d.getUTCMonth() + 1} 月</span>`; last = x + 30; }
    else if (d.getUTCDay() === 0 && W * 7 >= 28 && x > last + 6) { scale += `<span class="tk-gw" style="left:${x}px">${date}</span>`; last = x + 16; }
  }
  const index = new Map(g.rows.map((r, i) => [r.id, i]));
  const bars = g.rows.map((r) => {
    const x = day(r.start) * W, w = Math.max(W, (day(r.end) - day(r.start) + 1) * W);
    const cls = r.status === '已完成' ? 'done' : r.status === '已取消' ? 'canceled' : r.late ? 'late' : r.waiting ? 'wait' : r.status === '受阻' ? 'blocked' : '';
    const roll = r.rollup && r.rollup.total ? `<span class="tk-groll" style="width:${Math.round(100 * r.rollup.done / r.rollup.total)}%"></span>` : '';
    const tip = `#${r.id} ${r.content}\n${r.start}${r.end !== r.start ? ' → ' + r.end : ''} · ${r.status}${r.late ? ' · 已逾期' : ''}${r.waiting ? ' · 等前置任务' : ''}` +
      (r.rollup && r.rollup.total ? `\n子任务 ${r.rollup.done}/${r.rollup.total} 完成` : '');
    return `<div class="tk-grow" data-goto="${r.id}"><div class="tk-glabel" style="padding-left:${10 + r.depth * 16}px" title="${esc(tip)}">
        <b>#${r.id}</b><span>${esc(r.content)}</span></div>
      <div class="tk-gtrack" style="width:${width}px"><div class="tk-gbar ${cls}${r.rollup ? ' parent' : ''}" style="left:${x}px;width:${w}px" title="${esc(tip)}">${roll}</div></div></div>`;
  }).join('');
  const lines = g.rows.flatMap((r) => r.deps.filter((dep) => index.has(dep)).map((dep) => {
    const from = g.rows[index.get(dep)], y1 = index.get(dep) * H + H / 2, y2 = index.get(r.id) * H + H / 2;
    const x1 = (day(from.end) + 1) * W, x2 = day(r.start) * W;
    return `<path d="M${x1} ${y1} h6 V${y2} H${Math.max(x2, x1 + 6)}" />`;
  })).join('');
  const today = (day(g.today) + 0.5) * W;
  pane.innerHTML = `<div class="tk-gantt">${head}<div class="tk-gscroll"><div class="tk-gbody" style="width:${LABEL + width}px">
      <div class="tk-gscale"><div class="tk-glabel"></div><div class="tk-gtrack" style="width:${width}px">${scale}</div></div>
      <div class="tk-grows">${bars}
        <svg class="tk-glines" style="left:${LABEL}px" width="${width}" height="${g.rows.length * H}"><defs><marker id="tk-arrow" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L6 3L0 6z"/></marker></defs>${lines}</svg>
        <div class="tk-gtoday" style="left:${LABEL + today}px" title="今天 ${esc(g.today)}"></div></div></div></div>
    <p class="muted small tk-gnote">点击一行打开任务。条形从开始日期到计划完成日期（已完成的到结束日期）；只有一个日期的任务显示为一天。箭头表示前置关系。</p></div>`;
  $$('.tk-glines path', pane).forEach((p) => p.setAttribute('marker-end', 'url(#tk-arrow)'));
  const scroller = $('.tk-gscroll', pane);
  scroller.scrollLeft = Math.max(0, today - (scroller.clientWidth - LABEL) / 2);
}

// ---------------------------------------------------------------- forms
function tkInput(k, value, opts = {}) {
  const v = tkText(value);
  if (k === 'priority') return `<select data-f="priority">${['', '高', '中', '低'].map((p) => `<option value="${p}"${p === v ? ' selected' : ''}>${p || '（未定）'}</option>`).join('')}</select>`;
  if (k === 'status') {
    const list = opts.archived ? [v] : TK_OPEN;
    return `<select data-f="status"${opts.archived ? ' disabled' : ''}>${list.map((s) => `<option${s === v ? ' selected' : ''}>${esc(s)}</option>`).join('')}</select>`;
  }
  if (TK_DATES.includes(k)) {
    const kept = v && !/^\d{4}-\d{2}-\d{2}$/.test(v);  // A date kept as text from Excel: shown, edited as text.
    return kept ? `<input data-f="${k}" type="text" value="${esc(v)}" title="Excel 中按文字保存的日期；修改时请改成 年-月-日">` : `<input data-f="${k}" type="date" value="${esc(v)}">`;
  }
  if (TK_LONG.includes(k)) return `<textarea data-f="${k}" rows="${k === 'history' ? 8 : k === 'content' ? 3 : 2}" spellcheck="false">${esc(v)}</textarea>`;
  return `<input data-f="${k}" type="text" value="${esc(v)}" spellcheck="false">`;
}
const tkRow = (label, control, hint = '') => `<label class="tk-frow"><span>${label}</span>${control}${hint ? `<em class="muted small">${hint}</em>` : ''}</label>`;
function tkCollect(dlg) {
  const out = {};
  $$('[data-f]', dlg).forEach((el) => { if (!el.disabled) out[el.dataset.f] = el.value; });
  return out;
}

async function confirmChanges(title, changes, okText = '保存') {
  if (!Object.keys(changes).length) { toast('没有修改'); return false; }
  return promptDialog({ title, text: '请核对这次的修改：', body: `<div class="tk-form">${changeRows(changes)}</div>`, okText, collect: () => true });
}

function afterSave(result, quiet) {
  loadMailIndex();
  if (result && result.unblocked && result.unblocked.length) offerUnblock(result.unblocked);
  TK.story = null;
  if (!quiet && result && result.verify) showVerify(result);
  else if (result && result.export && result.export.state && result.export.state !== 'ok') toast('已保存；导出未完成，见任务列表上方的提示', { error: true });
  loadTasks();
}

function showVerify(result) {
  const v = result.verify, c = v.counts, ex = result.export || {};
  const live = TK.status && TK.status.mode === 'live';
  const exportText = ex.state === 'ok' ? (live ? '已导出到任务文件夹：未完成任务.xlsx、已完成任务.xlsx、任务数据.json。请在任务文件夹中 git 提交。' : '演练模式：已导出到鸿雁数据目录，任务文件夹没有改动。')
    : ex.state === 'locked' ? '还没有导出：Excel 正在打开任务文件。' : ex.state === 'in_use' ? '还没有导出：任务文件被其他程序占用。'
    : ex.state === 'denied' ? '还没有导出：没有写入任务文件夹的权限。' : ex.state === 'external' ? '导出已暂停：任务文件在鸿雁之外被改过。' : ex.state === 'failed' ? '导出没有成功。' : '';
  const table = v.open.length ? `<table class="tk-table"><thead><tr><th>编号</th><th>系统</th><th>任务内容</th><th>状态</th></tr></thead><tbody>${v.open.map((t) =>
    `<tr><td>#${t.id}</td><td>${esc(t.system)}</td><td>${esc(t.content)}</td><td>${tkStatus(t.status)}</td></tr>`).join('')}</tbody></table>` : '<p><b>当前无未完成任务</b></p>';
  const commit = live && ex.state === 'ok' && ex.message ? `<div class="tk-commit"><code>${esc(ex.message)}</code><button class="btn sm" id="tk-copy">复制提交说明</button></div>` : '';
  infoDialog({ title: '已保存并核验', body: `<div class="tk-verify">
      <p>重新读取任务库核对通过（${esc(v.at)}）。未完成 <b>${c.open}</b> · 已完成 ${c.done} · 已取消 ${c.canceled} · 合计 ${c.total}</p>
      ${table}<p class="muted small">${exportText}</p>${commit}</div>`,
  onOpen: (dlg) => { const b = $('#tk-copy', dlg); if (b) b.onclick = () => copyText(ex.message, '提交说明'); } });
}

async function infoDialog(opts) {  // promptDialog with nothing to confirm: its button reads 关闭.
  const button = $('#prompt .modal-foot [data-close]');
  button.textContent = '关闭';
  try { return await promptDialog(opts); } finally { button.textContent = '取消'; }
}

async function runChange(call) {
  try { afterSave(await call()); return true; } catch (err) { fail(err); if (err.code === 'TASK_CONFLICT') loadTasks(); return false; }
}

async function taskAction(act) {
  const d = TK.detail, t = d.task, id = t.id, rev = d.rev, archived = !TK_OPEN.includes(t.status);
  if (act === 'progress') {
    const v = await promptDialog({ title: `记录进展 · #${id}`, okText: '检查修改',
      body: `<div class="tk-form">${tkRow('日期', `<input data-f="date" type="date" value="${tkToday()}">`, '默认今天（沙特时间）')}
        ${tkRow('进展', '<textarea data-f="text" rows="4" spellcheck="false" placeholder="这次确认的结果或问题"></textarea>')}
        <label class="check"><input type="checkbox" id="tk-as-latest" checked> 同时作为「最新进展」</label>
        ${archived ? '' : tkRow('状态', tkInput('status', t.status)) + tkRow('下一步', tkInput('next', t.next)) + tkRow('阻塞与依赖', tkInput('blockers', t.blockers))}</div>`,
      collect: () => ({ ...tkCollect($('#prompt')), latest: $('#tk-as-latest').checked }) });
    if (!v) return;
    if (!v.text.trim()) { toast('请填写进展', { error: true }); return; }
    const on = v.date || tkToday(), line = `${on}：${v.text.trim()}`;
    const fields = {};
    for (const k of ['status', 'next', 'blockers']) if (k in v && v[k] !== tkText(t[k])) fields[k] = v[k];
    const changes = { ...Object.fromEntries(Object.entries(fields).map(([k, x]) => [k, [t[k], x]])),
      history: [t.history, t.history ? `${t.history}\n${line}` : line] };
    if (v.latest) changes.latest = [t.latest, line];
    if (on !== tkText(t.updated)) changes.updated = [t.updated, on];
    if (!(await confirmChanges(`记录进展 · #${id}`, changes))) return;
    runChange(() => api('taskProgress', { id, rev, text: v.text, date: v.date, latest: v.latest, fields }));
  } else if (act === 'edit') {
    const keys = ['system', 'content', 'priority', 'status', 'latest', 'owner', 'start', 'due', 'end', 'updated', 'blockers', 'next', 'acceptance', 'history'];
    const v = await promptDialog({ title: `编辑 · #${id}`, okText: '检查修改',
      body: `<div class="tk-form wide">${keys.map((k) => tkRow(TK_LABEL[k], tkInput(k, t[k], { archived }),
        k === 'history' ? '只应在末尾追加；改动原有文字视为更正，需要填写依据' : '')).join('')}
        ${tkRow('依据', '<input data-f="note" type="text" spellcheck="false" placeholder="这次修改的依据（选填；更正历史时必填）">')}</div>`,
      collect: () => tkCollect($('#prompt')) });
    if (!v) return;
    const note = v.note; delete v.note;
    const fields = Object.fromEntries(Object.entries(v).filter(([k, x]) => x !== tkText(t[k])));
    const changes = Object.fromEntries(Object.entries(fields).map(([k, x]) => [k, [t[k], x]]));
    if (Object.keys(fields).length && !('updated' in fields) && tkToday() !== tkText(t.updated)) changes.updated = [t.updated, tkToday()];
    if (!(await confirmChanges(`编辑 · #${id}`, changes))) return;
    runChange(() => api('taskUpdate', { id, rev, fields, note }));
  } else if (act === 'complete') {
    const v = await promptDialog({ title: `标记完成 · #${id}`, okText: '检查修改', text: '完成后归档到「已完成任务」，编号和历史保留。',
      body: `<div class="tk-form">${tkRow('完成日期', `<input data-f="date" type="date" value="${tkToday()}">`, '没有指定时按今天（沙特时间）')}
        ${tkRow('说明', '<input data-f="note" type="text" spellcheck="false" placeholder="交付结果或确认方（选填）">')}</div>`,
      collect: () => tkCollect($('#prompt')) });
    if (!v) return;
    const end = v.date || tkToday();
    const line = `${end}：已完成${v.note.trim() ? '：' + v.note.trim() : '。'}`;
    if (!(await confirmChanges(`标记完成 · #${id}`, { status: [t.status, '已完成'], end: [t.end, end], updated: [t.updated, end],
      history: [t.history, t.history ? `${t.history}\n${line}` : line] }))) return;
    runChange(() => api('taskComplete', { id, rev, date: v.date, note: v.note }));
  } else if (act === 'cancel') {
    const v = await promptDialog({ title: `取消任务 · #${id}`, okText: '检查修改', text: '取消后归档到「已取消任务」，编号和历史保留。',
      body: `<div class="tk-form">${tkRow('原因', '<input data-f="reason" type="text" spellcheck="false" placeholder="取消、移交或不再负责的原因（必填）">')}
        ${tkRow('取消日期', '<input data-f="date" type="date">', '选填；不填则结束日期保持原样')}</div>`,
      collect: () => tkCollect($('#prompt')) });
    if (!v) return;
    if (!v.reason.trim()) { toast('请填写原因', { error: true }); return; }
    const on = v.date || tkToday(), line = `${on}：已取消：${v.reason.trim()}`;
    const changes = { status: [t.status, '已取消'], updated: [t.updated, on], history: [t.history, t.history ? `${t.history}\n${line}` : line] };
    if (v.date) changes.end = [t.end, v.date];
    if (!(await confirmChanges(`取消任务 · #${id}`, changes))) return;
    runChange(() => api('taskCancel', { id, rev, reason: v.reason, date: v.date }));
  } else if (act === 'reopen') {
    const v = await promptDialog({ title: `重新打开 · #${id}`, okText: '检查修改', text: '任务回到「未完成任务」，编号和历史保留，结束日期清空。',
      body: `<div class="tk-form">${tkRow('状态', tkInput('status', '进行中'))}${tkRow('原因', '<input data-f="reason" type="text" spellcheck="false" placeholder="选填">')}</div>`,
      collect: () => tkCollect($('#prompt')) });
    if (!v) return;
    if (v.status === '受阻' && !tkText(t.blockers).trim()) { toast('改为「受阻」前，请先在「编辑」中填写阻塞与依赖', { error: true }); return; }
    const line = `${tkToday()}：重新打开${v.reason.trim() ? '：' + v.reason.trim() : '。'}`;
    if (!(await confirmChanges(`重新打开 · #${id}`, { status: [t.status, v.status], end: [t.end, ''],
      history: [t.history, t.history ? `${t.history}\n${line}` : line] }))) return;
    runChange(() => api('taskReopen', { id, rev, status: v.status, reason: v.reason }));
  }
}

$('#tk-new').onclick = async () => {
  const keys = ['system', 'content', 'priority', 'status', 'owner', 'start', 'due', 'blockers', 'next', 'acceptance'];
  const v = await promptDialog({ title: '新建任务', okText: '检查', text: '编号自动取最大编号加一。只填写确实知道的内容；不知道的留空，不会自动补。',
    body: `<div class="tk-form wide">${keys.map((k) => tkRow(TK_LABEL[k], tkInput(k, k === 'owner' ? '用户本人' : k === 'status' ? '未开始' : ''))).join('')}</div>`,
    collect: () => tkCollect($('#prompt')) });
  if (!v) return;
  if (!v.content.trim()) { toast('请填写任务内容', { error: true }); return; }
  const changes = Object.fromEntries(Object.entries(v).filter(([, x]) => x).map(([k, x]) => [k, ['', x]]));
  changes.updated = ['', tkToday()];
  if (!(await confirmChanges('新建任务', changes))) return;
  try {
    const result = await api('taskCreate', { fields: v });
    if (result.ids && result.ids[0]) TK.selected = result.ids[0];
    TK.view = 'open'; afterSave(result);
  } catch (err) { fail(err); }
};

async function reviewPending() {
  try {
    const items = await api('tasksPending');
    if (!items.length) { toast('没有待审核的修改'); return; }
    const first = items[0];
    TK.selected = first.task; TK.tab = first.kind === 'suggested' ? 'story' : 'timeline'; TK.view = 'all'; await loadTasks();
    toast(`共 ${items.length} 条待确认：逐条「采纳」或「驳回」`);
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- 筛选 and 问鸿雁
// Asking and changing tasks in words happens in the one 助理 sidebar (R2b), which takes the
// selected task along. Here only a keyword filter (no AI); a number alone opens that task.
const TK_OP = { progress: '记录进展', update: '修改', complete: '标记完成', cancel: '取消任务', reopen: '重新打开', create: '新建任务' };

async function tkAiConsent() {
  try { if (localStorage.getItem('hy-tk-ai') === '1') return true; } catch (e) { /* optional */ }
  const ok = await confirmDialog('在任务中心使用 AI', '为了理解你的指令、为演进图起标题、从邮件生成任务，任务内容（以及你选中的邮件）会发送到「设置 → AI 助手」中配置的 AI 服务。AI 给出的修改在你确认之前不会执行。', '同意');
  if (ok) try { localStorage.setItem('hy-tk-ai', '1'); } catch (e) { /* optional */ }
  return ok;
}

function clearTaskFilter() { TK.query = ''; TK.ids = null; $('#tk-filter').value = ''; }
let tkFilterTimer = 0;
$('#tk-filter').addEventListener('input', () => {
  clearTimeout(tkFilterTimer);
  tkFilterTimer = setTimeout(() => {
    const q = $('#tk-filter').value.trim();
    if (/^#?\s*\d{1,4}$/.test(q) || q === TK.query) return;  // A number waits for Enter.
    TK.query = q; TK.ids = null; loadTasks();
  }, 250);
});
$('#tk-filter').addEventListener('keydown', async (e) => {
  if (e.key === 'Escape' && e.target.value) { e.stopPropagation(); clearTaskFilter(); loadTasks(); return; }
  const number = e.key === 'Enter' && !e.isComposing && e.target.value.trim().match(/^#?\s*(\d{1,4})$/);
  if (!number) return;
  e.preventDefault(); clearTaskFilter(); TK.view = 'all'; TK.selected = Number(number[1]); TK.tab = 'story';
  await loadTasks();
  if (!TK.rows.some((r) => r.id === TK.selected)) toast(`没有编号为 ${number[1]} 的任务`, { error: true });
});
$('#tk-ask').onclick = () => showAsk();
$('#tk-report').onclick = () => {
  const r = $('#tk-report').getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [{ label: '本周周报', icon: 'mail', run: () => writeReport('') }, { label: '上周周报', icon: 'mail', run: () => writeReport('last') }]);
};

// pick: the action's index when it gets a checkbox (each operation confirmed on its own).
function planItem(p, pick, on = true) {
  const box = pick === undefined ? '' : `<input type="checkbox" class="tk-pick" data-pick="${pick}"${on ? ' checked' : ''} title="执行这一项">`;
  const head = `<div class="tk-ev-head">${box}<b>${TK_OP[p.op] || esc(p.op)}</b><span>${p.id ? '#' + p.id : ''}</span>
    ${p.op !== 'create' && p.content ? `<span class="tk-plan-what">${esc(p.content)}</span>` : ''}${p.status ? tkStatus(p.status) : ''}</div>`;
  const changes = p.changes && Object.keys(p.changes).length;
  const body = p.error ? `<div class="tk-bad">不会执行：${esc(p.message || '这项操作不符合任务规则。')}</div>`
    : changes ? changeRows(p.changes) : '<div class="muted small">没有需要修改的内容，不会执行。</div>';
  return `<div class="tk-plan-item${p.error || !changes || !on ? ' off' : ''}">${head}${body}</div>`;
}

// ---------------------------------------------------------------- 邮件 → 任务（AI）
function mailTaskItems(ids, m) {
  const one = ids.length === 1;
  return ['-', { label: one ? '转为任务（AI）…' : `转为任务（AI，${ids.length} 封）…`, icon: 'spark', run: () => mailToTask(ids) },
    ...(one ? [{ label: '关联到任务…', icon: 'tasks', run: () => linkMailToTask(m || { id: ids[0] }) }] : [])];
}

async function mailToTask(ids, opts = {}) {
  if (ids.length > 5) { toast('一次最多选择 5 封邮件转为任务', { error: true }); return; }
  if (!(await tkAiConsent())) return;
  let target = opts.target || 'auto', text = '', history = [], allow = [];
  toast('正在阅读邮件并生成任务计划…');
  for (const id of ids) {  // Bodies not downloaded yet come down now (without marking the mail read).
    if (!(S.message && S.message.id === id)) await api('openMessage', { mid: id, mark_read: false }).catch(() => {});
  }
  for (;;) {
    let plan;
    try {
      plan = await api('tasksFromMail', { mids: ids, target, text, history, allow });
    } catch (err) {
      if (err.code === 'AI_ENCRYPTED_CONFIRM' && !allow.length
          && await confirmDialog('加密邮件', '选中的邮件里有加密邮件。确认后，它解密后的内容会发送到 AI 服务用来生成任务计划，不会保存。', '同意发送')) { allow = ids; continue; }
      if (err.code !== 'AI_ENCRYPTED_CONFIRM') fail(err);
      if (err.code === 'AI_NOT_CONFIGURED') openSettings('ai');
      return;
    }
    history = [...history, ...(text ? [{ role: 'user', content: '补充说明：' + text }] : []), { role: 'assistant', content: plan.raw }].slice(-8);
    const choice = await showMailPlan(plan);
    if (!choice) return;
    if (choice.retarget !== undefined) { target = choice.retarget; text = ''; history = []; continue; }
    if (choice.answer) { text = choice.answer; continue; }
    const runnable = plan.preview.map((p) => !p.error && p.changes && Object.keys(p.changes).length > 0);
    const actions = plan.actions.filter((_, i) => runnable[i]).map((a) => (a.op === 'progress' && choice.date ? { ...a, date: choice.date } : a));
    try {
      const result = await api('tasksCommandRun', { actions, text: plan.mails.map((m) => m.subject || '（无主题）').join(' / ') });
      if (result.failed.length) toast(`${result.failed.length} 项没有执行`, { error: true });
      if (result.ids.length) { clearTaskFilter(); TK.selected = result.ids[result.ids.length - 1]; TK.view = 'all'; TK.tab = 'story'; }
      afterSave(result);
    } catch (err) { fail(err); }
    return;
  }
}

function showMailPlan(plan) {
  const runnable = plan.preview.filter((p) => !p.error && p.changes && Object.keys(p.changes).length).length;
  const progress = plan.actions.find((a) => a.op === 'progress');
  const mails = plan.mails.map((m) => `<li><b>${esc(m.subject || '（无主题）')}</b> <span class="muted small">${esc(m.from || '')} · ${esc(m.date || '')}</span></li>`).join('');
  const on = (t) => (String(plan.target) === String(t) ? ' on' : '');
  const targets = `<div class="tk-targets"><span class="muted small">改为：</span><button class="btn sm${on('auto')}" data-target="auto">由 AI 判断</button>
    <button class="btn sm${on('new')}" data-target="new">新建任务</button>
    ${plan.candidates.map((c) => `<button class="btn sm${on(c.id)}" data-target="${c.id}" title="${esc(c.content)}">更新 #${c.id}</button>`).join('')}
    <input type="text" id="tk-target-id" placeholder="其他编号" inputmode="numeric"><button class="btn sm" data-target="typed">更新</button></div>`;
  const questions = plan.questions.length ? `<div class="tk-questions"><b>需要你补充：</b><ul>${plan.questions.map((q) => `<li>${esc(q)}</li>`).join('')}</ul>
    <textarea id="tk-answer" rows="2" spellcheck="false" placeholder="在这里补充信息"></textarea><div class="row-gap"><button class="btn sm primary" id="tk-answer-go">按补充重新生成</button></div></div>` : '';
  const date = progress ? `<label class="tk-frow"><span>进展日期</span><input type="date" id="tk-plan-date" value="${esc(progress.date || '')}"></label>` : '';
  return promptDialog({ title: runnable ? '从邮件生成任务操作' : '还不能执行', okText: runnable > 1 ? `确认执行 ${runnable} 项` : '确认执行',
    body: `<div class="tk-plan"><ul class="tk-mails">${mails}</ul>${targets}${plan.reply ? `<p>${esc(plan.reply)}</p>` : ''}${questions}${date}
      ${plan.preview.map(planItem).join('')}${date ? '<p class="muted small">改了进展日期后，执行时按新日期记录。</p>' : ''}</div>`,
    collect: runnable ? () => ({ run: true, date: ($('#tk-plan-date') || {}).value || '' }) : null,
    onOpen: (dlg, done) => {
      $$('[data-target]', dlg).forEach((b) => (b.onclick = () => {
        let t = b.dataset.target;
        if (t === 'typed') { t = $('#tk-target-id', dlg).value.trim().replace(/^#/, ''); if (!/^\d+$/.test(t)) { toast('请输入任务编号', { error: true }); return; } }
        done({ retarget: /^\d+$/.test(t) ? Number(t) : t });
      }));
      const go = $('#tk-answer-go', dlg);
      if (go) go.onclick = () => { const a = $('#tk-answer', dlg).value.trim(); if (a) done({ answer: a }); };
    } });
}

// #编号 chips beside linked mail in the list and the reader; a click opens the task.
TK.mailIndex = {};
async function loadMailIndex() {
  try { TK.mailIndex = await api('tasksMailIndex'); } catch (e) { TK.mailIndex = {}; }
  decorateMailChips();
  if (S.message) decorateReaderChips(S.message);
}
const tkChips = (mid) => (TK.mailIndex[mid] || []).map((id) => `<span class="tk-chip" data-task="${id}" title="已关联任务 #${id}，点击打开">#${id}</span>`).join('');
function decorateMailChips() {
  $$('#mail-list .row[data-id]').forEach((row) => {
    const old = $('.tk-chips', row); if (old) old.remove();
    const chips = tkChips(row.dataset.id), line = $('.line1', row);
    if (chips && line) line.insertAdjacentHTML('beforeend', `<span class="tk-chips">${chips}</span>`);
  });
}
function decorateReaderChips(m) {
  const old = $('#r-tk-chips'); if (old) old.remove();
  const chips = m && tkChips(m.id);
  if (chips) $('#r-subject').insertAdjacentHTML('beforeend', `<span id="r-tk-chips" class="tk-chips">${chips}</span>`);
}
const _tkRenderList = renderList;
renderList = function (...args) { const r = _tkRenderList(...args); decorateMailChips(); return r; };
const _tkRenderMessage = renderMessage;
renderMessage = function (m) { const r = _tkRenderMessage(m); decorateReaderChips(m); return r; };
document.addEventListener('click', async (e) => {
  const chip = e.target.closest('.tk-chip[data-task]'); if (!chip) return;
  e.stopPropagation(); e.preventDefault();
  if (READER_MID) return;
  clearTaskFilter(); TK.view = 'all'; TK.selected = Number(chip.dataset.task); TK.tab = 'story';
  await openTasks();
}, true);

// ---------------------------------------------------------------- 周报
// range: {from, to}, '' (this week) or 'last'. The report opens as a new mail; nothing is sent.
async function writeReport(range) {
  let start = '', end = '';
  if (range === 'last') {
    const today = new Date(tkToday() + 'T00:00:00');
    const sunday = new Date(today); sunday.setDate(today.getDate() - today.getDay() - 7);
    const saturday = new Date(sunday); saturday.setDate(sunday.getDate() + 6);
    start = isoDay(sunday); end = isoDay(saturday);
  } else if (range && range.from) { start = range.from; end = range.to || tkToday(); }
  if (!(await tkAiConsent())) return;
  toast('正在汇总任务记录并写周报…');
  try {
    const report = await api('tasksReport', { start, end });
    const draft = await api('compose', { mode: 'new' });
    draft.subject = report.subject;
    draft.html = report.html + (draft.html || '');  // The signature stays below the report.
    openComposer(draft);
    C.dirty = true;
    toast(`周报已写入新邮件（${report.from} 至 ${report.to}，涉及 ${report.active} 项任务）。请核对内容、填写收件人后再发送`, { ms: 8000 });
  } catch (err) {
    fail(err);
    if (err.code === 'AI_NOT_CONFIGURED') openSettings('ai');
  }
}

// ---------------------------------------------------------------- mail links
async function linkMailToTask(m) {
  let rows;
  try { rows = (await api('tasksOverview', { view: 'open' })).rows; } catch (err) { fail(err); return; }
  if (!rows.length) { toast('当前没有未完成任务'); return; }
  const list = (q) => rows.filter((r) => !q || `#${r.id} ${r.system} ${r.content}`.toLowerCase().includes(q.toLowerCase()))
    .map((r) => `<button class="tk-pick-row" data-id="${r.id}"><b>#${r.id}</b><span class="tk-sys">${esc(r.system)}</span><span class="tk-pick-text">${esc(r.content)}</span>${tkStatus(r.status)}</button>`).join('');
  const id = await promptDialog({ title: '关联到任务', text: m.subject || '', body: `<div class="tk-form"><input id="tk-pick-q" type="search" placeholder="筛选：编号、系统或内容" spellcheck="false"><div id="tk-pick" class="tk-pick">${list('')}</div></div>`,
    onOpen: (dlg, done) => {
      $('#tk-pick-q', dlg).oninput = (e) => { $('#tk-pick', dlg).innerHTML = list(e.target.value.trim()); };
      $('#tk-pick', dlg).onclick = (e) => { const b = e.target.closest('[data-id]'); if (b) done(Number(b.dataset.id)); };
    } });
  if (!id) return;
  try { await api('taskLink', { id, mid: m.id }); toast(`已关联到任务 #${id}`); loadMailIndex(); if (S.local === 'tasks') loadTasks(); } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- settings
function taskStatusText(st) {
  const c = st.counts;
  const mode = { off: '还没有导入。', trial: '演练模式：任务文件夹只读，修改导出到鸿雁数据目录。', live: '正式使用：鸿雁是唯一写入方，每次修改后导出到任务文件夹。' }[st.mode];
  const counts = st.mode === 'off' ? '' : ` 未完成 ${c.open} · 已完成 ${c.done} · 已取消 ${c.canceled} · 合计 ${c.total}。`;
  const imported = st.lastImport ? ` 上次导入 ${st.lastImport.at}。` : '';
  return mode + counts + imported;
}
function applyTaskStatus(st) {
  TK.status = st;
  if (document.activeElement !== $('#tasks-folder') && !$('#tasks-folder').dataset.edited) $('#tasks-folder').value = st.folder || '';
  $('#tasks-folder').disabled = st.mode === 'live';
  $('#tasks-pick').classList.toggle('hidden', st.mode === 'live');
  $('#tasks-status').textContent = taskStatusText(st);
  $('#tasks-import').classList.toggle('hidden', st.mode === 'live');
  $('#tasks-compare').classList.toggle('hidden', st.mode !== 'trial');
  $('#tasks-stop').classList.toggle('hidden', st.mode !== 'live');
  $('#tasks-export-now').classList.toggle('hidden', st.mode === 'off');
  if (TK.counts == null && st.counts) { TK.counts = st.counts; renderTasksNav(); }
}
async function saveTaskFolder() {  // Only a folder the user typed or picked (not a field still loading).
  const box = $('#tasks-folder');
  if (!box.dataset.edited) return;
  delete box.dataset.edited;
  applyTaskStatus(await api('tasksSetFolder', { folder: box.value.trim() }));
}
function openTaskSettings() { openSettings('tasks'); }
const _tasksOpenSettings = openSettings;
openSettings = function (...args) { const r = _tasksOpenSettings(...args); api('tasksStatus').then(applyTaskStatus).catch(() => {}); return r; };
$('#tasks-folder').addEventListener('input', () => { $('#tasks-folder').dataset.edited = '1'; });
$('#tasks-folder').addEventListener('change', () => saveTaskFolder().catch(fail));
$('#tasks-pick').onclick = async () => {
  const folder = await api('pickFolder', { title: '选择任务文件夹（其中有 未完成任务.xlsx 和 已完成任务.xlsx）' }).catch(fail);
  if (folder) { $('#tasks-folder').value = folder; $('#tasks-folder').dataset.edited = '1'; saveTaskFolder().catch(fail); }
};

function reportBody(p) {
  const r = p.report, s = r.sheets;
  const list = (ids) => ids.length ? ids.slice(0, 12).map((i) => `#${i}`).join('、') + (ids.length > 12 ? ` 等 ${ids.length} 条` : '') : '';
  const lines = [
    `未完成任务 ${s['未完成任务'].count} 条 · 已完成任务 ${s['已完成任务'].count} 条 · 已取消任务 ${s['已取消任务'].count} 条 · 合计 <b>${r.total}</b>`,
    r.duplicates.length ? `<span class="tk-bad">编号重复：${list(r.duplicates)}</span>` : '编号唯一，没有重复',
    r.mismatch.length ? `<span class="tk-bad">状态与所在工作表不符：${list(r.mismatch)}</span>` : '状态与所在工作表一致',
    r.badRows.length ? `<span class="tk-bad">编号无法识别的行：${esc(r.badRows.slice(0, 8).join('、'))}</span>` : '',
    p.ok ? '往返校验通过：导出后再读回，所有任务逐格一致' : `<span class="tk-bad">往返校验有 ${p.differenceCount} 处不一致：${esc(p.differences.slice(0, 8).map((d) => (d.id ? '#' + d.id + ' ' : '') + (TK_LABEL[d.field] || d.field)).join('、'))}</span>`,
  ].filter(Boolean);
  const notes = [
    r.isoDates ? `${r.isoDates} 个日期单元格是 ISO 文本格式，导出时改为普通日期（显示不变）` : '',
    r.textDates.length ? `按文字保存的日期：${list(r.textDates)}（原样保留）` : '',
    r.typed ? `${r.typed} 个文字列单元格是数字等其他类型（原样保留）` : '',
    r.spaces ? `${r.spaces} 个单元格首尾有空格（原样保留）` : '',
    r.dateOrder.length ? `开始日期晚于计划/结束日期：${list(r.dateOrder)}（原样保留，不推算）` : '',
    r.noEnd['已完成'].length ? `已完成但没有结束日期：${list(r.noEnd['已完成'])}（原样保留）` : '',
    r.noEnd['已取消'] ? `${r.noEnd['已取消']} 条已取消任务没有结束日期（原样保留）` : '',
    r.placeholders.length ? `只有编号的占位行（不导入）：${list(r.placeholders)}` : '',
    '第 2 行说明改为「由鸿雁任务中心导出：请在鸿雁中修改，不要直接编辑本文件。」',
  ].filter(Boolean);
  return `<div class="tk-verify"><ul class="tk-report">${lines.map((l) => `<li>${l}</li>`).join('')}</ul>
    <p class="muted small">格式说明：</p><ul class="tk-report muted small">${notes.map((l) => `<li>${l}</li>`).join('')}</ul></div>`;
}

$('#tasks-check').onclick = async () => {
  try {
    await saveTaskFolder();
    const p = await api('tasksImportPreview');
    const go = await promptDialog({ title: '检查任务工作簿', text: p.ok ? '检查通过。先导入为演练：任务文件夹不会被修改，可以在鸿雁中随意试用。' : '检查没有通过，不能导入。',
      body: reportBody(p), okText: '导入为演练', collect: p.ok ? () => true : null });
    if (!go) return;
    const res = await api('tasksImport', { mode: 'trial' });
    applyTaskStatus(res.status);
    toast(`已导入 ${res.status.counts.total} 条任务（演练）`);
    $('#settings').classList.add('hidden'); TK.view = 'open'; openTasks();
  } catch (err) { fail(err); }
};
$('#tasks-live').onclick = async () => {
  try {
    await saveTaskFolder();
    const p = await api('tasksImportPreview');
    const go = await promptDialog({ title: '切换为正式', text: p.ok
      ? '鸿雁将重新读取任务文件夹中的两份工作簿（演练中的修改会被替换），校验通过后成为唯一写入方，并立即导出 未完成任务.xlsx、已完成任务.xlsx 和 任务数据.json 到任务文件夹。切换前请先在任务文件夹中 git 提交当前状态；切换后请不要再用 Excel 或 ChatGPT 修改这两份文件。'
      : '检查没有通过，不能切换。', body: reportBody(p), okText: '切换为正式', collect: p.ok ? () => true : null });
    if (!go) return;
    const res = await api('tasksImport', { mode: 'live' });
    applyTaskStatus(res.status);
    $('#settings').classList.add('hidden'); TK.view = 'open';
    await openTasks();
    showVerify(res);
  } catch (err) { fail(err); }
};
$('#tasks-stop').onclick = async () => {
  if (!(await confirmDialog('停用正式模式', '鸿雁将不再写入任务文件夹（那里的 Excel 是最后一次导出的状态，可以继续用 Excel 维护）。鸿雁中的任务保留，之后可以重新检查并导入。', '停用'))) return;
  try { applyTaskStatus(await api('tasksStopLive')); loadTasks(); toast('已停用正式模式'); } catch (err) { fail(err); }
};
$('#tasks-export-now').onclick = async () => {
  try { const ex = await api('tasksExport'); toast(ex.state === 'ok' ? '已导出' : '未能导出，见任务列表上方的提示', { error: ex.state !== 'ok' }); applyTaskStatus(await api('tasksStatus')); } catch (err) { fail(err); }
};
$('#tasks-compare').onclick = async () => {
  try {
    const r = await api('tasksCompare');
    const f = r.folderCounts;
    const diffs = r.differences.map((d) => `<li>#${d.id} ${d.field === 'missing' ? '只在鸿雁中' : d.field === 'extra' ? '只在 Excel 中' : esc(TK_LABEL[d.field] || d.field)}</li>`).join('');
    infoDialog({ title: '对照 Excel', text: r.count ? `鸿雁与任务文件夹中的工作簿有 ${r.count} 处不同：` : '鸿雁与任务文件夹中的工作簿完全一致。',
      body: `<div class="tk-verify"><p class="muted small">Excel：未完成 ${f['未完成任务']} · 已完成 ${f['已完成任务']} · 已取消 ${f['已取消任务']}；鸿雁：未完成 ${r.counts.open} · 已完成 ${r.counts.done} · 已取消 ${r.counts.canceled}</p>${diffs ? `<ul class="tk-report">${diffs}</ul>` : ''}</div>` });
  } catch (err) { fail(err); }
};
$('#tasks-restore').onclick = async () => {
  if (!(await confirmDialog('从 任务数据.json 恢复', '用任务文件夹中的 任务数据.json 替换鸿雁中的全部任务和时间线（例如换了电脑）。当前任务库会先备份。恢复后进入正式模式。', '恢复'))) return;
  try {
    await saveTaskFolder();
    const res = await api('tasksRestore', { mode: 'live' });
    applyTaskStatus(res.status); $('#settings').classList.add('hidden'); await openTasks(); showVerify(res);
  } catch (err) { fail(err); }
};

// ---------------------------------------------------------------- 自动更新（新邮件）
// New mail in the conversation of an unfinished task: its body is downloaded (not marked read),
// then the AI reads it. Progress it is sure belongs to the task is written at once; doubt and
// status changes wait as suggestions; mail about another matter is left alone.
// Only after the user agreed to AI in the task center; nothing runs when the AI is not set up.
let tkAutoBusy = false;
async function autoMail() {
  let agreed = false;
  try { agreed = localStorage.getItem('hy-tk-ai') === '1'; } catch (e) { /* optional */ }
  if (tkAutoBusy || !agreed || READER_MID) return;
  tkAutoBusy = true;
  const applied = [], suggested = [];
  try {
    const ai = await api('aiSettings').catch(() => null);
    if (!ai || !ai.ready) return;
    for (const item of await api('tasksNewMail')) {  // One item per task: its new mails are read together.
      for (const mid of item.mids) await api('openMessage', { mid, mark_read: false }).catch(() => {});
      try {
        const r = await api('tasksAutoMail', { task: item.task, mids: item.mids });
        applied.push(...r.applied); suggested.push(...r.suggested);
      } catch (e) { break; }  // AI unavailable: the next round tries again.
    }
  } catch (e) { /* the next round tries again */ } finally { tkAutoBusy = false; }
  if (!applied.length && !suggested.length) return;
  const ids = [...new Set([...applied, ...suggested])];
  toast(`根据新邮件：${applied.length ? `已更新 ${[...new Set(applied)].map((i) => '#' + i).join('、')} 的进展` : ''}${applied.length && suggested.length ? '；' : ''}${suggested.length ? `${suggested.length} 条修改待你确认` : ''}`,
    { ms: 10000, action: { label: '查看', run: () => { clearTaskFilter(); TK.view = 'all'; TK.selected = ids[0]; TK.tab = 'story'; openTasks(); } } });
  loadMailIndex();
  if (S.local === 'tasks') loadTasks(); else api('tasksStatus').then((st) => { TK.status = st; TK.counts = st.counts; renderTasksNav(); }).catch(() => {});
}
setInterval(autoMail, 3 * 60 * 1000);

// On start: the open count beside 任务 in the sidebar.
const _tasksEnterApp = enterApp;
enterApp = async function (...args) {
  const r = await _tasksEnterApp(...args);
  api('tasksStatus').then((st) => {
    TK.status = st; TK.counts = st.counts; renderTasksNav();
    if (st.mode !== 'off') { loadMailIndex(); setTimeout(autoMail, 20000); }
  }).catch(() => {});
  return r;
};
