'use strict';
/* 鸿雁 · 理解新邮件 (理解层 M1): the AI reads every received mail once in the background and
   keeps a card — 要我做 / 要我知道 / 可以忽略, a one-line 要点, what is asked and by when.
   Here: the list's 要点 and its 要我做 / 知会 mark, the 等回复 view, the reader's strip, corrections and
   their rules, the background rounds and the settings. Cards are plain text only (esc).
   Uses app.js globals; app.js asks UC for a row's extras, its group, the empty text and
   menu items. */

const UC = window.UC = { status: null, cards: new Map(), busy: false };  // cards: mid -> card, or null (asked, none)
const UC_KIND = { act: '要我做', know: '要我知道', ignore: '可以忽略' };
const UC_ANSWERED = { reply: '回复', forward: '转发' };  // R3a: what went out for a 要我做 mail.
const UC_SKIP = ['AI_ENCRYPTED_CONFIRM', 'CERT_LOCKED', 'AI_MAIL_UNAVAILABLE', 'AI_BAD_RESPONSE'];  // That mail only; go on.

function ucOn() { return S.prefs.understand === 'on'; }
function ucToday() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function ucDue(due) {
  if (!due) return null;
  const today = ucToday();
  return due < today ? { text: `已过期 ${due.slice(5)}`, late: true } : due === today ? { text: '今天到期', late: true } : { text: `${due.slice(5)} 前`, late: false };
}

// ---------------------------------------------------------------- the list
// The list's last line: a small 要我做 / 知会 mark, then the card's few-word tag (cards read
// before tags existed show their 要点 up to the first clause). No filters: the mark is enough.
function ucShort(gist) { return (gist || '').split(/[，,；;。]/)[0].slice(0, 16); }
UC.row = (r) => {
  if (!ucOn()) return null;
  const c = UC.cards.get(r.id);
  if (!c) return null;
  const due = c.kind === 'act' && c.sure && !c.done ? ucDue(c.due) : null;
  const act = c.kind === 'act' && c.sure && !c.done;
  const lead = act ? (due ? `<span class="uc-due${due.late ? ' late' : ''}">${esc(due.text)}</span>` : '') +
    (c.answered ? `<span class="uc-answered">已${UC_ANSWERED[c.answered] || '回复'}</span>` : '') : '';
  const mark = act ? `<span class="uc-kind act" title="要我做：${esc(c.ask || c.gist)}">要我做</span>`
    : c.kind === 'know' && c.sure ? '<span class="uc-kind" title="只需要知道，不用你做事">知会</span>' : '';
  const short = S.prefs.listGist !== false ? c.tag || ucShort(c.gist) : '';
  const prev = mark || short ? `<div class="prev uc-gist" title="${esc('AI 要点：' + c.gist)}">${mark}${esc(short)}</div>` : '';
  return { lead, prev, cls: c.kind === 'ignore' && c.sure ? ' uc-ignore' : '' };
};
UC.group = (r) => {
  if (S.local === 'u-wait') return (r.waiting || 0) >= 7 ? '一周以上没有回复' : '3 天以上没有回复';
  return null;
};
UC.empty = () => S.local === 'u-wait' ? '没有等对方回复的邮件' : '';
const ucLocal = () => S.local === 'u-wait';
UC.menu = (r) => {
  const c = ucOn() && UC.cards.get(r.id);
  if (!c) return [];
  const info = { sender: r.fromEmail || '', subject: r.subject || '' };
  return ['-', ...Object.keys(UC_KIND).filter((k) => k !== c.kind || !c.sure).map((k) => ({ label: `改为「${UC_KIND[k]}」`, icon: k === 'act' ? 'flag' : k === 'know' ? 'spark' : 'check', run: () => ucCorrect(r.id, { kind: k }, info) })),
    ...(c.kind === 'act' ? [{ label: c.done ? '取消「已处理」' : '标为已处理', icon: 'check', run: () => ucCorrect(r.id, { done: !c.done }, info) }] : []),
    { label: '改项目…', icon: 'folder', run: () => ucProject(r.id, c, info) }];
};

// Cards for the rows on screen, asked once per row.
async function ucLoad() {
  if (!ucOn()) return;
  const ids = [...new Set([...S.rows, ...[...S.thread.values()].flat()].map((r) => r.id))].filter((id) => !UC.cards.has(id) && !id.startsWith('arc:'));
  if (!ids.length) return;
  ids.forEach((id) => UC.cards.set(id, null));
  try {
    const found = await api('understandCards', { ids: ids.slice(0, 1000) });
    const keys = Object.keys(found);
    keys.forEach((id) => UC.cards.set(id, found[id]));
    if (keys.length) renderList();
  } catch { ids.forEach((id) => UC.cards.delete(id)); }
}
const _ucRenderList = renderList;
renderList = function (...args) { const r = _ucRenderList(...args); ucLoad(); return r; };

// ---------------------------------------------------------------- 等回复 (催办), opened from 今天 → 在等别人
async function showUnderstand(kind, quiet) {
  if (!quiet || S.local !== 'u-' + kind) {
    S.local = 'u-' + kind; S.search = null; S.filter = 'all'; S.listToken++; S.multi.clear(); renderSelection();
    S.rows = []; S.more = false; S.total = null; $('#search').value = '';
    const f = folderById(S.folderId);
    $('#list-title').textContent = f ? folderTitle(f) : '等回复';
    $('#mail-list').innerHTML = '<div class="skeleton"></div>'.repeat(4);
  }
  const token = S.listToken;
  renderFolders();
  try {
    const view = kind === 'wait' ? await api('followups').then((rows) => ({ rows, total: rows.length })) : await api('understandView', { kind });
    if (token !== S.listToken) return;
    if (kind === 'wait') UC.wait = new Map(view.rows.map((r) => [r.id, r]));
    else view.rows.forEach((r) => UC.cards.set(r.id, r.card));
    S.rows = view.rows; S.total = view.total;
    renderList();
    $('#list-sub').textContent = view.total ? `${view.total} 封` : '';
    $('#list-sub').title = kind === 'wait' ? '最近 30 天你发出的、提了问的邮件；对方回复后自动消失' : '最近 30 天，由 AI 判断；判断不对时可以在右键菜单中纠正';
    if (typeof memTop === 'function') memTop(kind);
  } catch (err) { if (token === S.listToken) $('#mail-list').innerHTML = `<div class="list-foot">${esc(err.message)}</div>`; }
}
// The view is reached from 今天 → 在等别人 (today.js wraps ucOpen) and the conversation.
function ucOpen(kind) {
  const f = S.folders.find((x) => x.role === 'sent');
  if (!f) return;
  if (S.folderId !== f.id) { S.folderId = f.id; S.local = null; }
  showUnderstand(kind);
}
$$('#filter-seg button[data-filter]').forEach((b) => b.addEventListener('click', () => { if (ucLocal()) selectFolder(S.folderId); }));
function renderUcNav() {
  $$('#filter-seg button[data-filter]').forEach((x) => x.classList.toggle('on', !ucLocal() && x.dataset.filter === S.filter));
}
const _ucRenderFolders = renderFolders;
renderFolders = function () { _ucRenderFolders(); renderUcNav(); };
function ucRefreshStatus() { return api('understandStatus').then((st) => { UC.status = st; renderUcNav(); return st; }).catch(() => null); }

// ---------------------------------------------------------------- the reader's strip
const _ucRenderMessage = renderMessage;
renderMessage = function (m) { _ucRenderMessage(m); renderUcStrip(m); };
function renderUcStrip(m) {
  const old = $('#uc-strip'); if (old) old.remove();
  if (!ucOn() || !m || m.imported) return;
  const c = UC.cards.get(m.id);
  if (c === undefined) {
    UC.cards.set(m.id, null);
    api('understandCards', { ids: [m.id] }).then((found) => {
      if (!found[m.id]) return;
      UC.cards.set(m.id, found[m.id]);
      if (S.message && S.message.id === m.id) renderUcStrip(S.message);
    }).catch(() => UC.cards.delete(m.id));
    return;
  }
  if (!c) return;
  const box = document.createElement('div'); box.id = 'uc-strip';
  const project = c.project ? `<span class="uc-project">${esc(c.project)}</span>` : '';
  const more = '<button class="icon-btn sm" data-uc="menu" title="纠正 AI 的判断"><i data-icon="more"></i></button>';
  const act = c.kind === 'act' && c.sure;
  if (act && !c.done) {
    const due = ucDue(c.due);
    box.className = 'uc-strip act';
    box.innerHTML = `<i data-icon="flag"></i><span class="uc-text"><b>要你做：</b>${esc(c.ask || c.gist)}</span>` +
      (due ? `<span class="uc-due${due.late ? ' late' : ''}">${esc(due.text)}</span>` : '') +
      (c.answered ? `<span class="uc-answered">已${UC_ANSWERED[c.answered] || '回复'}</span>` : '') + project +
      (typeof mailToTask === 'function' ? '<button class="btn ghost" data-uc="task" title="交给任务中心（AI 判断新建还是更新哪条任务）">转为任务</button>' : '') +
      '<button class="btn ghost" data-uc="done" title="从「要我做」中移除">已处理</button>' + more;
  } else {
    const text = act ? `已处理 · ${c.ask || c.gist}` : !c.sure ? `${c.gist}（AI 不确定）` : c.kind === 'know' ? `知会 · ${c.gist}` : `AI 判断：可以忽略${c.gist ? ' · ' + c.gist : ''}`;
    box.className = 'uc-strip';
    box.innerHTML = `<i data-icon="spark"></i><span class="uc-text muted">${esc(text)}</span>${project}${more}`;
  }
  box.title = `由 ${c.actor === 'user' ? '你' : c.rule ? '规则' : c.model || 'AI'} 判断${c.at ? '（' + c.at + '）' : ''}`;
  $('#r-banners').after(box);
  renderIcons(box);
  const from = m.from[0] || {};
  const info = { sender: from.email || '', subject: m.subject || '' };
  $$('[data-uc]', box).forEach((b) => (b.onclick = (e) => ({
    task: () => mailToTask([m.id]),
    done: () => ucCorrect(m.id, { done: true }, info),
    menu: () => {
      const r = b.getBoundingClientRect();
      showMenu(r.left, r.bottom + 4, UC.menu({ id: m.id, fromEmail: from.email, subject: m.subject }).filter((x) => x !== '-'));
      void e;
    },
  }[b.dataset.uc])()));
}

// ---------------------------------------------------------------- corrections and rules
async function ucCorrect(mid, change, info) {
  try { UC.cards.set(mid, await api('understandCorrect', { mid, ...change })); } catch (err) { fail(err); return; }
  ucAfterChange();
  if (change.kind) {
    toast(`已改为「${UC_KIND[change.kind]}」`, { ms: 9000, action: { label: '以后同类都这样', run: () => ucRuleDialog(info, change.kind) } });
  } else if (change.done) toast('已标为已处理');
}
async function ucProject(mid, c, info) {
  const value = await promptDialog({ title: '改项目', okText: '保存',
    body: `<label class="pref-row">项目<input id="uc-project-name" maxlength="30" value="${esc(c.project || '')}" placeholder="不属于任何项目就留空"></label>`,
    collect: () => $('#uc-project-name').value.trim() });
  if (value === null) return;
  await ucCorrect(mid, { project: value }, info);
}
function ucAfterChange() {
  renderList();
  if (S.message) renderUcStrip(S.message);
  if (ucLocal()) showUnderstand(S.local.slice(2), true);
  ucRefreshStatus();
}

// 回复后只标已回复 (R3a): a reply or forward that went out marks its 要我做 mail 已回复 / 已转发;
// it stays on the list (a reply is not the work done) and the toast offers 已处理 in one click.
const _ucSendOutcome = sendOutcome;
sendOutcome = function (res, meta = {}) { _ucSendOutcome(res, meta); ucAnswered(res, meta); };
async function ucAnswered(res, meta) {
  if (!ucOn() || !meta.source || !['Accepted', 'Queued'].includes(res.state)) return;
  const mid = meta.source, how = meta.how === 'forward' ? 'forward' : 'reply';
  let changed;
  try { ({ changed } = await api('understandAnswered', { mid, how })); } catch { return; }
  if (!changed.length) return;
  UC.cards.delete(mid); ucAfterChange();
  toast(`已${UC_ANSWERED[how]}，这封仍在「要我做」里。事情办完了就点「已处理」`, { ms: 9000,
    action: { label: '已处理', run: () => ucCorrect(mid, { done: true }, {}) } });
}
async function ucRuleDialog(info, kind) {
  const email = (info.sender || '').toLowerCase(), domain = email.includes('@') ? email.split('@')[1] : '';
  const word = (/(周报|日报|月报|纪要|通知|公告)/.exec(info.subject) || [])[1] || '';
  const value = await promptDialog({ title: `以后同类邮件都按「${UC_KIND[kind]}」处理`, okText: '添加规则',
    body: `<p class="muted small">符合条件的邮件，鸿雁直接按「${UC_KIND[kind]}」处理，不再由 AI 判断${kind === 'ignore' ? '（也不会发送给 AI）' : ''}。规则可以在「设置 → AI 助手」中删除。</p>` +
      '<div class="uc-who">' + (email ? `<label class="check"><input type="radio" name="uc-who" value="addr" checked> 发件人是 ${esc(email)}</label>` : '') +
      (domain ? `<label class="check"><input type="radio" name="uc-who" value="domain"> 发件人来自 @${esc(domain)}</label>` : '') +
      `<label class="check"><input type="radio" name="uc-who" value="any"${email ? '' : ' checked'}> 任何发件人</label></div>` +
      `<label class="pref-row uc-kw">并且主题包含<input id="uc-kw" maxlength="20" value="${esc(word)}" placeholder="可以不填，例如 周报"></label>`,
    collect: () => ({ who: ($('input[name="uc-who"]:checked') || {}).value, keyword: $('#uc-kw').value.trim() }) });
  if (!value) return;
  const sender = value.who === 'addr' ? email : value.who === 'domain' ? '@' + domain : '';
  if (!sender && !value.keyword) { toast('「任何发件人」时请填写主题关键字', { error: true }); return; }
  try {
    const res = await api('understandAddRule', { sender, keyword: value.keyword, kind });
    UC.cards.clear();
    ucAfterChange();
    toast(`已添加规则${res.changed ? `，${res.changed} 封邮件已按规则调整` : ''}`);
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- background rounds
// Like 任务中心's 自动更新: bodies come down first (not marked read), then one AI call each.
async function ucRound() {
  if (UC.busy || READER_MID || !ucOn()) return;
  UC.busy = true;
  let done = 0, left = 0;
  try {
    const ai = await api('aiSettings').catch(() => null);
    if (!ai || !ai.ready) return;
    const pending = await api('understandPending');
    left = pending.left;
    if (!pending.ids.length) return;
    if (pending.missing.length) await api('understandFetch', { ids: pending.missing }).catch(() => {});
    for (const id of pending.ids) {
      try { UC.cards.set(id, await api('understandMail', { mid: id })); done++; } catch (err) { if (!UC_SKIP.includes(err.code)) break; }
    }
  } catch (e) { /* the next round tries again */ } finally { UC.busy = false; }
  if (!done) return;
  if (ucLocal()) showUnderstand(S.local.slice(2), true);
  else if (!S.local) renderList();
  if (S.message) renderUcStrip(S.message);
  ucRefreshStatus();
  if (left > done) setTimeout(ucRound, 20000);  // Older mail of the last 30 days: keep going while idle.
}
setInterval(ucRound, 3 * 60 * 1000);

// First start with AI set up: ask once (the design's Q1 and Q5).
async function ucAsk() {
  const ai = await api('aiSettings').catch(() => null);
  if (!ai || !ai.ready || S.prefs.understand) return;
  const value = await promptDialog({ title: '让 AI 先把新邮件读一遍？', okText: '开启',
    body: '<p class="muted">鸿雁会把最近 30 天收到的邮件和以后的新邮件各读一次，分成「要我做」「要我知道」「可以忽略」，在列表里给邮件标上「要我做」「知会」并显示一句话要点，并记下你的项目的进展。</p>' +
      '<p class="muted small">邮件内容会发送到「设置 → AI 助手」中配置的 AI 服务，每月约 45 万 tokens。不会标为已读，不会移动、删除或回复邮件。以后可以在设置中关闭。</p>' +
      '<label class="check"><input id="uc-ask-enc" type="checkbox" checked> 包括加密邮件（可以在设置中排除个别文件夹和发件人）</label>',
    collect: () => ({ encrypted: $('#uc-ask-enc').checked }) });
  try {
    if (!value) { await api('setUnderstand', { state: 'off' }); S.prefs.understand = 'off'; toast('可以在「设置 → AI 助手」中随时开启「理解新邮件」'); return; }
    await api('setAiEncrypted', { allow: value.encrypted });
    UC.status = await api('setUnderstand', { state: 'on' });
    S.prefs.understand = 'on';
    renderFolders();
    ucRound();
  } catch (err) { fail(err); }
}
const _ucEnterApp = enterApp;
enterApp = async function (...args) {
  const r = await _ucEnterApp(...args);
  if (READER_MID) return r;
  ucRefreshStatus().then((st) => {
    if (!st || !st.available) return;
    if (!st.state) setTimeout(ucAsk, 6000);
    else if (st.state === 'on') setTimeout(ucRound, 15000);
  });
  return r;
};

// ---------------------------------------------------------------- settings
const _ucRenderAiSettings = renderAiSettings;
renderAiSettings = function () { _ucRenderAiSettings(); renderUcSettings(); };
async function renderUcSettings() {
  const st = await ucRefreshStatus();
  if (!st) return;
  const enc = st.encrypted;
  $('#uc-enc-allow').checked = enc.allow;
  $('#uc-enc-folders').innerHTML = '<b class="small">排除文件夹</b>' + S.folders.map((f) => `<label class="check" style="padding-left:${(f.depth || 0) * 18}px">` +
    `<input type="checkbox" data-ucf="${esc(f.id)}"${enc.folders.includes(f.id) ? ' checked' : ''}> ${esc(f.name)}</label>`).join('');
  $('#uc-enc-senders').value = enc.senders.join('\n');
  $('#uc-on').checked = st.state === 'on';
  $('#uc-gist').checked = S.prefs.listGist !== false;
  $('#mem-facts').checked = !!S.prefs.memoryFacts;
  $('#uc-status').textContent = !st.available ? (st.unavailable || '无法打开本机的理解数据，「理解新邮件」暂不可用。') : st.state !== 'on' ? '未开启。' :
    `本月已理解 ${st.month} 封邮件；要我做 ${st.counts.act} 封，知会 ${st.counts.know} 封未读，等回复 ${st.counts.wait || 0} 封` +
    (st.failed ? `；${st.failed} 封这次无法读取（例如证书未解锁），已跳过` : '') + '。';
  const rules = st.available ? await api('understandRules').catch(() => []) : [];
  const who = (r) => r.sender ? (r.sender.startsWith('@') ? `来自 ${r.sender}` : `发件人 ${r.sender}`) : '任何发件人';
  if (typeof renderMemProjects === 'function') renderMemProjects(st.available && st.state === 'on');
  $('#uc-rules').innerHTML = rules.length ? `<b class="small">规则（${rules.length}）</b><ul class="uc-rule-list">` + rules.map((r) =>
    `<li><span>${esc(who(r))}${r.keyword ? `，主题含「${esc(r.keyword)}」` : ''} → ${esc(UC_KIND[r.kind])}</span>` +
    `<button class="btn ghost sm" data-ucdel="${esc(r.id)}">删除</button></li>`).join('') + '</ul>' : '';
  $$('[data-ucdel]', $('#uc-rules')).forEach((b) => (b.onclick = async () => {
    try { await api('understandDeleteRule', { rule_id: b.dataset.ucdel }); renderUcSettings(); } catch (err) { fail(err); }
  }));
}
$('#uc-enc-save').onclick = async () => {
  try {
    await api('setAiEncrypted', { allow: $('#uc-enc-allow').checked, folders: $$('#uc-enc-folders [data-ucf]').filter((c) => c.checked).map((c) => c.dataset.ucf),
      senders: $('#uc-enc-senders').value.split(/[\s,;，；]+/).filter(Boolean) });
    toast('加密邮件设置已保存');
    renderUcSettings();
  } catch (err) { fail(err); }
};
$('#uc-on').onchange = async () => {
  try {
    UC.status = await api('setUnderstand', { state: $('#uc-on').checked ? 'on' : 'off' });
    S.prefs.understand = UC.status.state;
    if (!ucOn() && ucLocal()) selectFolder(S.folderId);
    renderFolders(); renderList();
    if (S.message) renderUcStrip(S.message);
    renderUcSettings();
    if (ucOn()) ucRound();
  } catch (err) { fail(err); }
};
$('#uc-gist').onchange = async () => {
  try { S.prefs = await api('setPrefs', { values: { listGist: $('#uc-gist').checked } }); renderList(); } catch (err) { fail(err); }
};
$('#uc-clear').onclick = async () => {
  if (!(await confirmDialog('清除理解结果和规则', '删除本机保存的全部理解结果和你添加的规则。之后「理解新邮件」开着时，会重新读最近 30 天的邮件（再次发送给 AI 服务）。', '清除'))) return;
  try { UC.status = await api('understandClear'); UC.cards.clear(); renderList(); renderUcSettings(); renderUcNav(); if (typeof memReset === 'function') memReset(); } catch (err) { fail(err); }
};
