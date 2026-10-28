'use strict';
/* 鸿雁 · AI 助手: 「总结这封」 and its settings. The summary is rendered as plain text
   only (esc), never as HTML. Encrypted mail goes to the AI service only after the
   user agrees for that message. Uses app.js globals. */

S.ai = new Map();  // mid -> summary shown in this window (or {loading}/{error}).

// Hooked here, not in app.js: a pop-out can render its message before this file loads.
const _aiRenderMessage = renderMessage;
renderMessage = function (m) { _aiRenderMessage(m); renderAiPanel(m); };

function aiPanel() {
  let box = $('#ai-panel');
  if (!box) { box = document.createElement('div'); box.className = 'ai-panel'; box.id = 'ai-panel'; $('#r-banners').after(box); }
  return box;
}
function renderAiPanel(m) {
  const state = m && S.ai.get(m.id);
  const old = $('#ai-panel');
  if (!state) { if (old) old.remove(); return; }
  const box = aiPanel();
  const title = { thread: `会话摘要${state.count ? `（${state.count} 封）` : ''}`, reply: 'AI 回复' }[state.kind] || 'AI 摘要';
  const head = (extra) => `<div class="ai-head"><i data-icon="spark"></i><b>${title}</b>${extra}<span class="spacer"></span>` +
    (state.loading || state.kind === 'reply' ? '' : '<button class="btn ghost" data-ai="again" title="重新发送给 AI 服务生成">重新生成</button>') +
    '<button class="icon-btn sm" data-ai="close" title="关闭"><i data-icon="close"></i></button></div>';
  if (state.loading) {
    box.innerHTML = head(`<span class="muted small">${esc(state.note || '正在总结…（邮件内容已发送到 AI 服务）')}</span>`) + '<div class="ai-body"><div class="ai-wait"><span></span><span></span><span></span></div></div>';
  } else if (state.error) {
    const setup = state.error.code === 'AI_NOT_CONFIGURED' || state.error.code === 'AI_AUTH_FAILED';
    box.innerHTML = head('') + `<div class="ai-body"><p class="ai-error">${esc(state.error.message)}</p>` +
      (setup ? '<button class="btn primary" data-ai="setup">打开 AI 设置</button>' : '') + '</div>';
  } else if (state.kind === 'reply') {
    box.innerHTML = head('<span class="muted small">选一个方向，或写下你想怎么回；AI 只写正文，收件人和加密按普通回复</span>') +
      `<div class="ai-body"><div class="ai-ideas">${state.ideas.map((x, i) => `<button class="ai-idea" data-idea="${i}"><b>${esc(x.label)}</b>` +
        `<span class="muted small">${esc(x.hint)}</span></button>`).join('')}</div>` +
      '<div class="ai-reply-row"><input id="ai-reply-note" maxlength="300" placeholder="或者写下你想怎么回，例如：同意，周四前给方案">' +
      `<select id="ai-reply-mode"><option value="reply">回复</option><option value="reply_all"${state.all ? ' selected' : ''}>全部回复</option></select>` +
      '<button class="btn primary" data-ai="write">起草</button></div></div>';
  } else {
    const list = (items) => items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted small">无</p>';
    const by = `<span class="muted small">由 ${esc(state.model || 'AI')} 生成${state.cached ? `（${esc(state.at)} 的结果）` : ''}，仅供参考</span>`;
    if (state.kind === 'thread') {
      const steps = state.timeline.length ? `<ol class="ai-steps">${state.timeline.map((s) => `<li data-mid="${esc(s.mid)}" title="打开这封邮件">` +
        `<span class="muted small">${esc(s.date)} ${esc(s.who)}</span> ${esc(s.text)}</li>`).join('')}</ol>` : '<p class="muted small">无</p>';
      const notes = [state.leftOut ? `中间 ${state.leftOut} 封邮件没有发送给 AI` : '', state.skipped ? `${state.skipped} 封加密邮件没有发送给 AI` : '']
        .filter(Boolean).map((x) => `<p class="muted small">${esc(x)}。</p>`).join('');
      box.innerHTML = head(by) + `<div class="ai-body">${state.conclusion ? `<p class="ai-conclusion">${esc(state.conclusion)}</p>` : ''}` +
        `<h4>经过</h4>${steps}<h4>还没解决</h4>${list(state.open)}<h4>需要我做什么</h4>${list(state.actions)}${notes}</div>`;
    } else {
      // forMe false: the asks are someone else's (or there are none) — shown as theirs, for information.
      const mine = state.forMe !== false;
      const asks = mine ? `<h4>需要我做什么</h4>${list(state.actions)}`
        : state.actions.length ? `<h4>需要${esc(state.who || '收件人')}做什么<span class="muted small">（不是问你的，知悉即可）</span></h4>${list(state.actions)}`
        : '<p class="muted small">没有要你做的事，知悉即可。</p>';
      box.innerHTML = head(by) + `<div class="ai-body">${state.conclusion ? `<p class="ai-conclusion">${esc(state.conclusion)}</p>` : ''}` +
        `<h4>要点</h4>${list(state.points)}${asks}</div>`;
    }
  }
  renderIcons(box);
  $$('[data-ai]', box).forEach((b) => (b.onclick = () => ({
    close: () => { S.ai.delete(m.id); renderAiPanel(m); },
    again: () => state.kind === 'thread' ? summarizeThread(m, { refresh: true }) : summarizeMessage(m, { refresh: true }),
    setup: () => openSettings('ai'),
    write: () => writeReply(m, null),
  }[b.dataset.ai])()));
  $$('[data-idea]', box).forEach((b) => (b.onclick = () => writeReply(m, state.ideas[+b.dataset.idea])));
  $$('.ai-steps [data-mid]', box).forEach((li) => (li.onclick = () => openSource(li.dataset.mid)));
  const note = $('#ai-reply-note', box);
  if (note) note.onkeydown = (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); writeReply(m, null); } };
}

function aiMenu(button, m) {
  const r = button.getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [
    { label: '总结这封', icon: 'summary', run: () => summarizeMessage(m) },
    { label: '总结整个会话', icon: 'summary', run: () => summarizeThread(m) },
  ]);
}

// Encrypted mail goes to the AI service only when the user agrees, this once.
async function allowEncrypted(m, what) {
  if (m.encryption === 'locked') { toast('这是一封加密邮件，请先解锁个人证书', { error: true }); return null; }
  if (m.encryption !== 'decrypted' || m.aiAllowed) return false;  // aiAllowed: 设置里已允许（不逐封询问）。
  return (await confirmDialog('把加密邮件发送给 AI 服务？',
    `这封邮件是加密的。${what}需要把解密后的正文发送到外部 AI 服务，邮件将不再只在你和收件人之间保密。仅这一次、仅这一封。`, '发送')) ? true : null;
}

// ---------------------------------------------------------------- 总结整个会话
async function summarizeThread(m, opts = {}) {
  const current = S.ai.get(m.id);
  if (current && current.loading) return;
  const set = (v) => { S.ai.set(m.id, { kind: 'thread', ...v }); if (S.message && S.message.id === m.id) renderAiPanel(S.message); };
  set({ loading: true, note: '正在查找会话中的邮件…' });
  try {
    let st = await api('threadAiStatus', { mid: m.id });
    if (st.ids.length < 2) { set({ error: { code: 'AI_NOT_A_THREAD', message: '这封邮件不属于多封邮件的会话，可以用「总结这封」。' } }); return; }
    if (st.missing.length) {
      set({ loading: true, note: `正在下载 ${st.missing.length} 封邮件的正文（不会标为已读）…` });
      for (const id of st.missing) await api('openMessage', { mid: id, mark_read: false }).catch(() => {});
      st = await api('threadAiStatus', { mid: m.id });
    }
    let allow = false;
    if (st.encrypted) {
      allow = await confirmDialog('把加密邮件发送给 AI 服务？', `这个会话里有 ${st.encrypted} 封加密邮件。一并总结需要把它们解密后的正文发送到外部 AI 服务，` +
        '邮件将不再只在你和收件人之间保密。不同意时这些邮件不发送，摘要中会注明。仅这一次；摘要不会保存在本机。', '一并发送');
    }
    set({ loading: true, count: st.ids.length, note: `正在总结 ${st.ids.length} 封邮件…（内容已发送到 AI 服务）` });
    set(await api('summarizeThread', { mid: m.id, allow_encrypted: !!allow, refresh: !!opts.refresh }));
  } catch (err) { set({ error: err }); }
}

// ---------------------------------------------------------------- 帮我起草回复
async function replyAssist(m) {
  const current = S.ai.get(m.id);
  if (current && current.loading) return;
  const allow = await allowEncrypted(m, '起草回复');
  if (allow === null) return;
  const set = (v) => { S.ai.set(m.id, { kind: 'reply', ...v }); if (S.message && S.message.id === m.id) renderAiPanel(S.message); };
  // Reply to all by default when the original went to more than one person (besides me).
  const all = ((m.to || []).length + (m.cc || []).length) > 1;
  set({ loading: true, note: '正在想回复方向…（邮件内容已发送到 AI 服务）' });
  try { set({ ...(await api('replyIdeas', { mid: m.id, allow_encrypted: allow })), all, allow }); } catch (err) { set({ error: err }); }
  const note = $('#ai-reply-note'); if (note) note.focus();
}

S.streams = new Map();  // req -> function(delta) while a streamed AI call runs.
on('aiStream', (p) => { const fn = p && S.streams.get(p.req); if (fn) fn(p.delta); });
function streamCall(method, params, onDelta) {
  const req = Math.random().toString(36).slice(2, 12);
  S.streams.set(req, onDelta);
  return api(method, { ...params, req }).finally(() => S.streams.delete(req));
}
function textHtml(text) {
  return text.split(/\n{2,}/).map((p) => `<p>${esc(p).replace(/\n/g, '<br>')}</p>`).join('');
}

async function writeReply(m, idea) {
  const state = S.ai.get(m.id) || {};
  const note = ($('#ai-reply-note') || {}).value || '';
  if (!idea && !note.trim()) { toast('请选一个回复方向，或写下你想怎么回'); return; }
  const mode = ($('#ai-reply-mode') || {}).value || 'reply';
  await startCompose(mode, m.id);
  if ($('#composer').classList.contains('hidden')) return;
  try {
    await streamIntoComposer('draftReply', { mid: m.id, idea: idea ? `${idea.label}：${idea.hint}` : '', note, allow_encrypted: !!state.allow });
  } catch (err) { if (err.code !== 'AI_CANCELLED') fail(err); }
}
// A streamed body into the open composer, above the signature; errors go to the caller.
async function streamIntoComposer(method, params) {
  const ed = $('#cmp-editor');
  const live = document.createElement('div');
  live.className = 'ai-draft'; live.contentEditable = 'false'; live.textContent = 'AI 正在起草…';
  const first = ed.firstElementChild;
  if (first && first.tagName === 'P' && !first.textContent.trim()) first.replaceWith(live); else ed.prepend(live);
  aiBusy('AI 正在起草…');
  let text = '';
  try {
    text = (await streamCall(method, params, (d) => { text += d; live.textContent = text; })).text;
  } finally {
    aiBusy('');
    if (live.isConnected) {
      if (text.trim()) { live.insertAdjacentHTML('beforebegin', textHtml(text)); C.dirty = true; }
      else live.insertAdjacentHTML('beforebegin', '<p><br></p>');
      live.remove();
      ed.focus();
    }
  }
}

// The composer's status line while a streamed call runs, with 停止.
function aiBusy(text) {
  const status = $('#cmp-status');
  status.innerHTML = text ? `${esc(text)} <button class="btn ghost sm" id="cmp-ai-stop">停止</button>` : '';
  const stop = $('#cmp-ai-stop'); if (stop) stop.onclick = () => api('cancelAi').catch(() => {});
}

// ---------------------------------------------------------------- 写信助手
// The AI button: the user's words above the signature (the signature never goes to the
// AI service) written into a mail, then changed by talking to it until the user is happy.

function bodyRange() {
  const ed = $('#cmp-editor'), range = document.createRange();
  range.selectNodeContents(ed);
  let stop = [...ed.children].find((c) => c.matches('.hy-signature, .hy-quote') || c.querySelector('.hy-signature, .hy-quote'));
  // The blank lines above the signature belong to it.
  while (stop && stop.previousSibling && (stop.previousSibling.nodeName === 'BR' || !stop.previousSibling.textContent.trim() &&
    stop.previousSibling.nodeType === Node.TEXT_NODE)) stop = stop.previousSibling;
  if (stop) range.setEndBefore(stop);
  return range;
}
function rangeText(range) {
  const box = document.createElement('div');
  box.style.cssText = 'position:fixed;left:-10000px;top:0;white-space:pre-wrap;width:600px';
  box.append(range.cloneContents());
  document.body.append(box);
  const text = box.innerText.replace(/\n{3,}/g, '\n\n').trim();
  box.remove();
  return text;
}
function rangeHasMedia(range) {
  const box = document.createElement('div'); box.append(range.cloneContents());
  return !!box.querySelector('table, img, .hy-signature, .hy-quote');
}

// AI 写信 (口述成信): the points the user wrote in the body (above the signature
// and the quoted original) become the whole mail and replace them; with an empty body a
// dialog asks for them. Replies and forwards bring the original as context. 修改意见 in
// the panel goes on from the last version, as many rounds as wanted; the user sends.
async function writeFromNote() {
  if (C.ai && C.ai.loading) return;
  const body = bodyRange(), media = rangeHasMedia(body), source = !!(C.draft.sourceMid || C.draft.replySourceId);
  let note = media ? '' : rangeText(body);
  if (!note) {
    note = await promptDialog({ title: 'AI 写信',
      body: '<textarea id="ai-note" rows="6" maxlength="8000" placeholder="写下大概要说什么，可以分条、很口语，例如：&#10;1. 同意周四上线&#10;2. 测试报告明天发&#10;3. 有问题找我"></textarea>' +
        `<p class="muted small">AI 会整理成正式的邮件正文${source ? '，并参考原邮件' : ''}；中文还是英文按收件人和原邮件判断，也可以在话里说明。也可以直接写在正文里再点这里。</p>`,
      okText: '写成邮件', onOpen: () => $('#ai-note').focus(), collect: () => $('#ai-note').value.trim() || null });
    if (!note) return;
  }
  if (note.length > 8000) { toast('文字太长（最多 8000 字）', { error: true }); return; }
  if (!(await aiWriteNotice(`你写的要点${source ? '和要回复的原邮件' : ''}会发送到「设置 → AI 助手」中配置的 AI 服务，签名不会发送。`))) return;
  // A table or picture in the body stays: the mail goes in above it.
  const range = body.cloneRange(); if (media) range.collapse(true);
  const d = C.draft, people = (field) => chipsValue(field).map((a) => parseAddressList(a)[0]).filter(Boolean).map((p) => ({ name: p.name || '', email: p.email }));
  const how = /^reply/.test(d.mode) ? 'reply' : /^forward/.test(d.mode) ? 'forward' : 'new';
  noteRun({ label: 'AI 写信', range, part: source ? '结合原邮件整理' : '整理成正式邮件',
    params: { note, subject: $('#cmp-subject').value, to: people('to'), cc: people('cc'), mid: d.sourceMid || d.replySourceId || '', how }, rounds: [], allow: false });
}
async function noteRun(base) {
  const state = C.ai = { ...base, text: '', error: null, loading: true };
  renderComposeAi();
  const run = () => streamCall('writeFromNote', { ...state.params, rounds: state.rounds, allow_encrypted: state.allow },
    (x) => { if (C.ai === state) { state.text += x; renderComposeAi(true); } });
  try {
    try { state.text = (await run()).text; } catch (err) {
      if (err.code !== 'AI_ENCRYPTED_CONFIRM') throw err;
      if (!(await confirmDialog('把加密邮件发送给 AI 服务？', '要回复的原邮件是加密的。一并参考需要把解密后的正文发送到外部 AI 服务。仅这一次、仅这一封。', '发送'))) {
        state.error = { code: 'AI_CANCELLED' };
      } else { state.allow = true; state.text = (await run()).text; }
    }
  } catch (err) { state.error = err; }
  state.loading = false;
  if (C.ai === state) renderComposeAi();
}
// 修改意见 on the version shown: the next round goes on from it.
function noteRevise(a) {
  const input = $('#cmp-ai-want'), want = input.value.trim();
  if (!want) { input.focus(); return; }
  noteRun({ ...a, rounds: [...a.rounds, { text: a.text, want }].slice(-5) });
}

// Once per computer: what goes to the AI service and what never does.
async function aiWriteNotice(what) {
  if (S.aiWriteNoticed || localStorage.getItem('hy-ai-write')) return true;
  if (!(await confirmDialog('写信助手', what + '如果这封邮件要加密发送，请注意改写前的文字会离开本机。', '继续'))) return false;
  S.aiWriteNoticed = true; try { localStorage.setItem('hy-ai-write', '1'); } catch (e) { /* private window */ }
  return true;
}

function renderComposeAi(onlyText) {
  const box = $('#cmp-ai-panel'), a = C.ai;
  box.classList.toggle('hidden', !a);
  if (!a) { box.innerHTML = ''; return; }
  if (onlyText && $('.cmp-ai-text', box)) { $('.cmp-ai-text', box).textContent = a.text; return; }
  const cancelled = a.error && a.error.code === 'AI_CANCELLED';
  box.innerHTML = `<div class="ai-head"><i data-icon="spark"></i><b>${esc(a.label)}</b><span class="muted small">${esc(a.part)}</span><span class="spacer"></span>` +
    (a.loading ? '<button class="btn ghost sm" data-cai="stop">停止</button>' : '') +
    '<button class="icon-btn sm" data-cai="close" title="放弃"><i data-icon="close"></i></button></div>' +
    (a.error && !cancelled ? `<p class="ai-error">${esc(a.error.message)}</p>` : '') +
    `<div class="cmp-ai-text">${esc(a.text)}</div>` +
    (a.loading ? '<div class="ai-wait"><span></span><span></span><span></span></div>' :
      (a.text ? '<div class="ai-reply-row"><input id="cmp-ai-want" maxlength="300" placeholder="还想怎么改？例如：更正式、译成英文、加上下周一前回复">' +
        '<button class="btn ghost sm" data-cai="revise">按意见修改</button>' +
        '<button class="btn primary sm" data-cai="replace" title="替换正文里你写的要点（签名和原邮件不动），可以撤销">用这一版</button></div>' :
        '<div class="row-gap"><button class="btn ghost sm" data-cai="again">再试一次</button></div>'));
  renderIcons(box);
  const want = $('#cmp-ai-want', box);
  if (want) want.onkeydown = (e) => { if (e.key === 'Enter' && !e.isComposing) { e.preventDefault(); noteRevise(a); } };
  $$('[data-cai]', box).forEach((b) => (b.onclick = () => ({
    stop: () => api('cancelAi').catch(() => {}),
    close: () => { if (a.loading) api('cancelAi').catch(() => {}); C.ai = null; renderComposeAi(); },
    again: () => noteRun(a),
    revise: () => noteRevise(a),
    replace: () => placeAi(a),
  }[b.dataset.cai])()));
}

// Placed by hand, not execCommand: Chromium would merge the last paragraph with the
// signature below it. 撤销 restores the editor as it was.
function placeAi(a) {
  const ed = $('#cmp-editor'), range = a.range;
  if (!ed.contains(range.commonAncestorContainer)) { toast('正文已经改动，请重新点 AI', { error: true }); return; }
  const before = ed.innerHTML;
  const piece = range.createContextualFragment(textHtml(a.text)), last = piece.lastChild;
  range.deleteContents();
  range.insertNode(piece);
  if (last) { const r = document.createRange(); r.setStartAfter(last); r.collapse(true); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
  ed.focus();
  C.dirty = true; C.ai = null; renderComposeAi();
  toast('已放入正文', { ms: 8000, action: { label: '撤销', run: () => { ed.innerHTML = before; C.dirty = true; } } });
}

// ---------------------------------------------------------------- 发送前检查
function wholeOwnText() { return rangeText(bodyRange()); }
const ISSUE_KIND = { date: '日期', greeting: '称呼', address: '外部邮箱', attachment: '附件', content: '内容', tone: '语气' };
function issueList(issues) {
  return '<ul class="precheck-list">' + issues.map((i, n) => `<li data-n="${n}"><b>${esc(ISSUE_KIND[i.kind] || '提示')}</b>` +
    `${i.quote ? `<span class="precheck-quote">${esc(i.quote)}</span>` : ''}<div>${esc(i.advice)}</div></li>`).join('') + '</ul>';
}
function findInEditor(quote) {
  const ed = $('#cmp-editor'); ed.focus();
  const sel = getSelection(); sel.removeAllRanges(); const r = document.createRange(); r.setStart(ed, 0); r.collapse(true); sel.addRange(r);
  if (quote && window.find) window.find(quote, false, false, false, false, false, false);
}

// Local rules on every send (no network); quiet when nothing is found. -> send?
async function precheckBeforeSend(value) {
  const people = (list) => list.map((a) => parseAddressList(a)[0]).filter(Boolean);
  let res;
  try {
    res = await api('precheck', { text: wholeOwnText(), to: people(value.to).map((p) => ({ name: p.name || '', email: p.email })),
      recipients: people([...value.to, ...value.cc, ...value.bcc]).map((p) => p.email) });
  } catch (err) { return true; }  // The check must never stop a send by failing.
  if (!res.issues.length) return true;
  return !!(await promptDialog({ title: '发送前请确认', text: '鸿雁在本机检查时发现（点击一条可以在正文中定位）：', body: issueList(res.issues) +
    '<p class="muted small">这些只是提示，可能并不是问题。可以在「设置」的「发送前检查」中关闭。</p>', okText: '仍然发送', collect: () => true }));
}

$('#prompt-body').addEventListener('click', (e) => {
  const li = e.target.closest('.precheck-list li');
  if (!li) return;
  const quote = $('.precheck-quote', li);
  $('#prompt [data-close]').click();
  findInEditor(quote && quote.textContent);
});
$('#cmp-ai').onclick = () => writeFromNote();

async function summarizeMessage(m, opts = {}) {
  const current = S.ai.get(m.id);
  if (current && current.loading) return;
  if (current && !current.error && !opts.refresh) { $('#ai-panel').scrollIntoView({ block: 'nearest' }); return; }
  if (m.encryption === 'locked') { toast('这是一封加密邮件，请先解锁个人证书再总结', { error: true }); return; }
  let allow = false;
  if (m.encryption === 'decrypted' && !m.aiAllowed) {
    allow = await confirmDialog('把加密邮件发送给 AI 服务？',
      '这封邮件是加密的。总结需要把解密后的正文发送到外部 AI 服务，邮件将不再只在你和收件人之间保密。仅这一次、仅这一封；摘要不会保存在本机。', '发送并总结');
    if (!allow) return;
  }
  S.ai.set(m.id, { loading: true });
  renderAiPanel(m);
  try {
    S.ai.set(m.id, await api('summarize', { mid: m.id, allow_encrypted: allow, refresh: !!opts.refresh }));
  } catch (err) {
    S.ai.set(m.id, { error: err });
  }
  if (S.message && S.message.id === m.id) renderAiPanel(S.message);  // The reader may have moved on.
}

// ---------------------------------------------------------------- settings
function renderAiSettings() {
  const status = $('#ai-status');
  api('aiSettings').then((a) => {
    $('#ai-base').value = a.baseUrl; $('#ai-key').value = '';
    fillModels(a.model, [a.model]);
    if (a.hasKey || a.demo) api('aiModels').then((ids) => fillModels(a.model, ids)).catch(() => {});  // Stays on the saved one.
    $('#ai-key').placeholder = a.hasKey ? '已保存（不可查看），输入新 Key 可替换' : '未设置';
    $('#ai-clear').classList.toggle('hidden', !a.hasKey);
    $('#ai-actions').checked = a.actions;
    renderMcp();
    status.textContent = (a.demo ? '演示模式：摘要由本机生成，不联网。' : a.hasKey ? '已设置 API Key。' : '未设置 API Key，「总结」和「问鸿雁」暂不可用。') +
      ` 本月已调用 ${a.usage.calls} 次，共 ${a.usage.tokens} tokens。`;
  }).catch(fail);
}
// 模型: a drop-down of what the service offers (GET /models), plus 「手动填写…」 for a name it does not list.
const AI_PRESETS = ['deepseek-chat', 'deepseek-reasoner'];  // While the list is not in yet, or the service has none.
function fillModels(current, ids) {
  const list = $('#ai-model-list');
  const presets = ids.length > 1 || !/deepseek\.com/.test($('#ai-base').value) ? [] : AI_PRESETS;
  const names = [...new Set([current, ...ids, ...presets])].filter(Boolean);
  list.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join('') + '<option value="">手动填写…</option>';
  list.value = current; $('#ai-model').value = current; $('#ai-model').classList.add('hidden');
}
$('#ai-model-list').onchange = (e) => {
  const manual = !e.target.value;
  $('#ai-model').classList.toggle('hidden', !manual);
  if (manual) { $('#ai-model').value = ''; $('#ai-model').focus(); } else $('#ai-model').value = e.target.value;
};
function aiValues() {
  const values = { base_url: $('#ai-base').value, model: $('#ai-model').value };
  const key = $('#ai-key').value.trim(); if (key) values.key = key;
  return values;
}
$('#ai-save').onclick = async () => {
  try { await api('setAiSettings', aiValues()); toast('AI 设置已保存'); renderAiSettings(); } catch (err) { fail(err); }
};
$('#ai-actions').onchange = async (e) => {
  try { await api('setAiSettings', { actions: e.target.checked }); toast(e.target.checked ? '对话里可以提议邮件操作' : '对话里不再提议邮件操作'); } catch (err) { fail(err); renderAiSettings(); }
};
// 本机只读 MCP 服务 (X1): off by default; on shows the command that adds 鸿雁 to Claude Code.
function renderMcp(st) {
  const show = (s) => {
    $('#mcp-on').checked = s.enabled;
    $('#mcp-how').classList.toggle('hidden', !s.enabled);
    $('#mcp-command').textContent = s.command || '安装版暂不支持，请从源码运行鸿雁。';
    $('#mcp-copy').classList.toggle('hidden', !s.command);
    $('#mcp-use').textContent = s.calls ? `本次运行已被读取 ${s.calls} 次，最近一次 ${s.last}。` : '本次运行还没有被读取。';
  };
  if (st) show(st); else api('mcpStatus').then(show).catch(fail);
}
$('#mcp-on').onchange = async (e) => {
  try { renderMcp(await api('setMcp', { enabled: e.target.checked })); toast(e.target.checked ? '本机 AI 工具可以读取邮箱' : '已关闭，本机 AI 工具不能再读取邮箱'); }
  catch (err) { fail(err); renderMcp(); }
};
$('#mcp-copy').onclick = () => copyText($('#mcp-command').textContent, '命令');
$('#ai-test').onclick = async () => {
  const b = $('#ai-test'); b.disabled = true; b.textContent = '正在测试…';
  try {
    await api('setAiSettings', aiValues());
    await api('aiTest');
    toast('连接成功');
  } catch (err) { fail(err); }
  b.disabled = false; b.textContent = '测试连接'; renderAiSettings();
};
$('#ai-clear').onclick = async () => {
  if (!(await confirmDialog('清除 API Key', '清除后「总结」将不可用，需要重新填写 Key。', '清除'))) return;
  try { await api('setAiSettings', { clear_key: true }); toast('已清除 API Key'); renderAiSettings(); } catch (err) { fail(err); }
};

// ---------------------------------------------------------------- 问邮箱
// A conversation in the right column (Ctrl+J). The backend keeps its refs and earlier
// answers by id, so a follow-up can say "第二封"; the page only shows the turns. Answers
// and sources are plain text (esc); [M3] citations become links to the source cards.
const CHAT_TURNS = 6;  // ai/agent.py: earlier turns sent with each question.
S.chat = { id: '', turns: [], attach: '' };  // turn: {question, allow, here, loading, steps, result | error}

function chatOpen() { return $('#app').classList.contains('chatting'); }
function showAsk(question) {
  $('#app').classList.add('chatting');
  renderAsk();
  if (question && question.trim()) { $('#ask-input').value = question.trim(); askMailbox(); }
  else $('#ask-input').focus();
}
function hideAsk() { $('#app').classList.remove('chatting'); }
function toggleChat() { if (chatOpen()) hideAsk(); else showAsk(); }
function newChat() {
  if (S.chat.turns.some((t) => t.loading)) api('cancelAsk').catch(() => {});
  S.chat = { id: '', turns: [], attach: '' };
  renderAsk(); $('#ask-input').focus();
}

// The mail open in the reader goes along with a question only when asked: the chip above the
// box turns it on for that mail, and a question saying "这封" / "这件事" takes it by itself.
// In 任务 the same goes for the selected task ("这个任务已经完成了吗").
const CHAT_THIS = /这封|这件事|这个邮件|本邮件|此邮件|这份|上面这/;
const CHAT_THIS_TASK = /这个任务|该任务|这条任务|此任务|这项任务|这一条/;
function chatHere() {
  if (READER_MID) return null;
  const card = S.local === 'today' && typeof TD !== 'undefined' && TD.task;  // 今天's task card (today.js).
  if (S.local === 'tasks' || card) {
    const d = typeof TK !== 'undefined' && TK.detail;
    if (!d || d.task.id !== (card || TK.selected)) return null;
    return { kind: 'task', key: 'task:' + d.task.id, id: d.task.id, subject: `#${d.task.id} ${tkText(d.task.content).slice(0, 60)}` };
  }
  const m = S.message;
  if (!m || S.multi.size >= 2 || S.selected !== m.id || $('#reader-body').classList.contains('hidden')) return null;
  return { kind: 'mail', key: m.id, mid: m.id, subject: m.subject || '（无主题）' };
}
function chatMailChanged() { if (chatOpen()) renderHere(); }
function renderHere() {
  const box = $('#chat-here'), here = chatHere();
  box.classList.toggle('hidden', !here);
  if (!here) { box.innerHTML = ''; return; }
  const on = S.chat.attach === here.key, what = here.kind === 'task' ? '任务' : '邮件';
  box.classList.toggle('on', on);
  box.title = on ? `这${what === '任务' ? '条任务' : '封邮件'}会随问题一起发给 AI：${here.subject}（点击取消）`
    : `点击让下一个问题带上：${here.subject}。问题里说"${what === '任务' ? '这个任务' : '这封'}"时会自动带上。`;
  box.innerHTML = `<i data-icon="${here.kind === 'task' ? 'tasks' : 'mail'}"></i><span>${on ? '已带上：' + esc(here.subject) : `带上正在看的${what}`}</span>`;
  renderIcons(box);
}
const _chatRenderFolders = renderFolders;  // Switching to 任务 or back changes what can go along.
renderFolders = function () { _chatRenderFolders(); chatMailChanged(); };

async function askMailbox(allow = [], again = null) {
  const turns = S.chat.turns, last = turns[turns.length - 1];
  if (last && last.loading) return;
  const question = again ? again.question : $('#ask-input').value.trim();
  if (!question) return;
  const open = chatHere();
  const here = again ? again.here
    : open && (S.chat.attach === open.key || (open.kind === 'task' ? CHAT_THIS_TASK : CHAT_THIS).test(question)) ? open : null;
  // Answering the last question again replaces that turn; only a turn the backend kept is redone there.
  const redo = !!(again && again.result);
  const turn = { question, allow, here, loading: true, steps: [] };
  if (again) turns[turns.length - 1] = turn; else turns.push(turn);
  if (!again) { $('#ask-input').value = ''; fitAsk(); }
  const chat = S.chat;
  renderAsk();
  try {
    turn.result = await api('askMailbox', { question, allow, chat: chat.id, redo,
      current: here && here.kind === 'mail' ? here.mid : '', current_task: here && here.kind === 'task' ? here.id : 0 });
    chat.id = turn.result.chat;
    if (S.chat === chat) supersedePlans(turn.result);
  } catch (err) { turn.error = err; }
  turn.loading = false;
  if (S.chat === chat) renderAsk();
}

function renderAsk() {
  const out = $('#ask-out'), turns = S.chat.turns, last = turns[turns.length - 1];
  $('#ask-run').disabled = !!(last && last.loading);
  renderHere();
  if (!turns.length) {
    // One assistant for every page (R2b): the samples follow where the user is.
    const tries = S.local === 'tasks' ? ['哪些任务受阻？', '这周有哪些任务到期？', '这个任务今天完成了', '给这个任务加一个前提任务：…']
      : ['现在有什么任务，哪些需要我做？', '上周有哪些邮件需要我回复？', '最近谁给我发过报价？'];
    out.innerHTML = '<div class="chat-empty"><b>问鸿雁</b>查邮件和任务、记进展、起草回复，都在这里说。AI 会搜索阅读邮件和任务后回答，并标出出处；要改任务或起草时先给你看计划，你确认后才执行。<br>可以接着追问，例如「第二封是谁发的」。' +
      `<div class="chat-try">${tries.map((q) => `<button type="button" data-try="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>`;
    return;
  }
  out.innerHTML = turns.map((t, i) => t.event ? `<div class="chat-event">${esc(t.event)}</div>${t.result.plans.map((p, j) => planCard(p, i, j)).join('')}` :
    `<div class="chat-q">${esc(t.question)}${t.here ? `<span class="chat-here-tag">附带：${esc(t.here.subject)}</span>` : ''}</div>` +
    turnCard(t, i, i === turns.length - 1) +
    (t.result && t.result.restarted ? '<div class="chat-sep">之前的对话已失效（例如重新登录过），这次作为新对话回答。</div>' : '') +
    (t.result && t.result.turn === CHAT_TURNS + 1 ? '<div class="chat-sep">更早的问答不再带给 AI。换个话题可以点「新对话」。</div>' : '')).join('');
  renderIcons(out);
  out.scrollTop = out.scrollHeight;
}

function turnCard(a, index, latest) {
  const stepList = (steps) => steps.length ? `<ol class="ask-steps">${steps.map((step) => `<li>${esc(step)}</li>`).join('')}</ol>` : '';
  if (a.loading) {
    return '<div class="ask-card"><div class="ai-head"><i data-icon="spark"></i><b>正在查找</b><span class="muted small">搜索和阅读的内容会发送到 AI 服务</span>' +
      `<button class="btn ghost" data-ask="cancel">取消</button></div><div class="ai-body"><div class="ai-wait"><span></span><span></span><span></span></div>${stepList(a.steps)}</div></div>`;
  }
  if (a.error) {
    const code = a.error.code, setup = code === 'AI_NOT_CONFIGURED' || code === 'AI_AUTH_FAILED';
    return `<div class="ask-card" data-turn="${index}"><div class="ai-body"><p class="${code === 'AI_CANCELLED' ? 'muted' : 'ai-error'}">${esc(a.error.message)}</p>` +
      (setup ? '<button class="btn primary" data-ask="setup">打开 AI 设置</button>' : '') +
      (code === 'AI_CONTEXT_TOO_LONG' ? '<button class="btn primary" data-ask="new">开始新对话</button>' : '') +
      (latest && !setup ? ' <button class="btn ghost" data-ask="again">重试</button>' : '') + stepList(a.steps) + '</div></div>';
  }
  const r = a.result;
  // [M3] links to a source card, [T12] to the task in 任务.
  const answer = esc(r.answer).replace(/\[([MT]\d+(?:, [MT]\d+)*)\]/g, (_, refs) =>
    refs.split(', ').map((ref) => ref[0] === 'T' ? `<a class="ask-ref task" data-task="${ref.slice(1)}" title="在任务中心打开 #${ref.slice(1)}">#${ref.slice(1)}</a>`
      : `<a class="ask-ref" data-ref="${ref}" title="打开出处 ${ref.slice(1)}">${ref.slice(1)}</a>`).join(''));
  const taskCard = (k) => `<div class="ask-src ask-task" data-task="${esc(k.id)}" title="在任务中心打开"><span class="ask-num">#${esc(k.id)}</span>` +
    `<div class="ask-src-main"><div class="ask-src-top"><b>${esc(k.content)}</b><span class="muted small">${esc(k.status)}${k.due ? ' · ' + esc(k.due) : ''}</span></div>` +
    (k.system ? `<div class="muted small ask-snip">${esc(k.system)}</div>` : '') + '</div></div>';
  const tasks = r.tasks || [];
  const card = (s) => `<div class="ask-src" data-mid="${esc(s.mid)}" title="打开这封邮件"><span class="ask-num">${esc(s.ref.slice(1))}</span>` +
    `<div class="ask-src-main"><div class="ask-src-top"><b>${esc(s.subject)}</b><span class="muted small">${esc(s.fromName)} · ${esc(String(s.date).slice(5, 16))}</span></div>` +
    (s.snippet ? `<div class="muted small ask-snip">${esc(s.snippet)}</div>` : '') +
    (s.encrypted ? '<div class="ask-enc"><i data-icon="lock"></i>加密邮件，AI 没有读取正文' +
      (latest ? `<button class="btn ghost sm" data-allow="${esc(s.mid)}">允许读取这封并重新回答</button>` : '') + '</div>' : '') +
    '</div></div>';
  const cited = r.sources.filter((s) => s.cited), other = r.sources.filter((s) => !s.cited);
  return `<div class="ask-card" data-turn="${index}"><div class="ai-head"><i data-icon="spark"></i><b>答案</b><span class="muted small">由 ${esc(r.model || 'AI')} 根据邮件生成，仅供参考</span>` +
    (latest ? '<button class="btn ghost" data-ask="again" title="重新发送给 AI 服务查找">重新回答</button>' : '') +
    `</div><div class="ai-body"><p class="ask-answer">${answer}</p>` +
    (r.limited ? '<p class="ask-limited">已达到查找次数或时间的上限，答案可能不完整。</p>' : '') +
    (r.plans || []).map((p, j) => planCard(p, index, j)).join('') +
    (tasks.length ? `<div class="ask-sources"><h4>任务</h4>${tasks.map(taskCard).join('')}</div>` : '') +
    (cited.length ? `<div class="ask-sources"><h4>出处</h4>${cited.map(card).join('')}</div>` : '') +
    (other.length ? `<details class="ask-process"><summary>也查阅过 ${other.length} 封</summary>${other.map(card).join('')}</details>` : '') +
    (r.steps.length ? `<details class="ask-process"><summary>查找过程（${r.steps.length} 步）</summary>${stepList(r.steps)}</details>` : '') +
    '</div></div>';
}

// 助理 plans (W2): the preview of tasks.js (planItem), run by id on 执行 — the backend keeps
// the operations, the page only shows them. p.state: '' | running | done | off.
const planReady = (x) => !x.error && x.changes && Object.keys(x.changes).length > 0;
function planCard(p, turn, j) {
  if (p.kind === 'draft') return draftCard(p, turn, j);
  if (p.kind === 'link') return linkCard(p, turn, j);
  if (p.kind === 'mail') return mailCard(p, turn, j);
  if (p.kind === 'remember') return rememberCard(p, turn, j);
  // Each operation can be left out (T1b, now here): p.skip holds the unticked indexes.
  const skip = p.skip || new Set(), picking = !p.state && p.preview.filter(planReady).length > 1;
  const ready = p.preview.filter((x, i) => planReady(x) && !skip.has(i)).length, at = `data-turn="${turn}" data-plan="${j}"`;
  const title = p.state === 'done' ? '任务操作 · 已执行' : p.state === 'off' ? '任务操作 · 未执行' : ready ? '任务操作 · 待你确认' : '任务操作 · 不能执行';
  let foot = '';
  if (p.state === 'running') foot = '<span class="tk-spin"></span>正在执行…';
  else if (p.state === 'done') {
    const o = p.outcome, c = o.verify.counts;
    foot = `<span class="plan-ok">已执行 ${o.done.length} 项${o.failed.length ? `，${o.failed.length} 项没有执行` : ''}</span>` +
      `<span class="muted small">核验通过：未完成 ${c.open} · 已完成 ${c.done} · 已取消 ${c.canceled}</span><span class="spacer"></span>` +
      `<button class="btn ghost sm" ${at} data-plan-act="verify">核验结果</button>`;
  } else if (p.state === 'off') foot = `<span class="muted small">${esc(p.note)}</span>`;
  else if (ready) {
    foot = `<span class="muted small">${picking ? '取消勾选的项不会执行；依赖新任务的项要和新任务一起执行' : '点「执行」后才会写入任务清单'}</span><span class="spacer"></span>` +
      `<button class="btn ghost sm" ${at} data-plan-act="edit">修改</button><button class="btn primary sm" ${at} data-plan-act="run">执行${ready > 1 ? ` ${ready} 项` : ''}</button>`;
  }
  return `<div class="chat-plan${p.state ? ' ' + p.state : ''}" ${at}><div class="chat-plan-head"><i data-icon="tasks"></i><b>${title}</b>` +
    (p.summary ? `<span class="muted small">${esc(p.summary)}</span>` : '') + `</div><div class="tk-plan">${p.preview.map((x, i) => planItem(x, picking && planReady(x) ? i : undefined, !skip.has(i))).join('')}</div>` +
    (foot ? `<div class="chat-plan-foot">${foot}</div>` : '') + '</div>' + (p.nudge ? nudgeLine(p.nudge, at) : '');
}
// 提醒回信 (W4): under an executed plan, once, when the task's conversation waits on the user.
// 起草 shows the draft card (as if the user asked for that reply); 不用 holds until a new mail.
function nudgeLine(n, at) {
  return `<div class="chat-nudge${n.state ? ' ' + n.state : ''}"><i data-icon="mail"></i><span>${esc(n.text)}</span>` +
    (n.state === 'off' ? '<span class="muted small">好的，这个会话有新邮件之前不再提醒。</span>' : n.state ? '' :
      `<span class="spacer"></span><button class="btn ghost sm" ${at} data-plan-act="nudge-skip">不用</button>` +
      `<button class="btn primary sm" ${at} data-plan-act="nudge-draft">起草</button>`) + '</div>';
}
function nudgeAct(p, turn, act) {
  const n = p.nudge;
  if (n.state) return;
  if (act === 'nudge-draft') { n.state = 'open'; turn.result.plans.push(n.plan); }
  else { n.state = 'off'; api('assistantNudgeDismiss', { plan: n.plan.id }).catch(fail); }
  renderAsk();
}
// 记进「关于我」 (Me2): what the user asked to keep, written only on 记住.
function rememberCard(p, turn, j) {
  const at = `data-turn="${turn}" data-plan="${j}"`, r = p.remember;
  const foot = p.state === 'done' ? `<span class="plan-ok">${esc(p.note)}</span>` : p.state === 'off' ? `<span class="muted small">${esc(p.note)}</span>` :
    '<span class="muted small">点「记住」后，问鸿雁、理解邮件和写信都会参考这一条；可在 设置 → 关于我 修改</span><span class="spacer"></span>' +
    `<button class="btn ghost sm" ${at} data-plan-act="skip">不用</button><button class="btn primary sm" ${at} data-plan-act="remember">记住</button>`;
  return `<div class="chat-plan${p.state ? ' ' + p.state : ''}" ${at}><div class="chat-plan-head"><i data-icon="user"></i>` +
    `<b>记进「关于我」· ${esc(r.group)}${p.state === 'done' ? ' · 已记住' : p.state === 'off' ? ' · 没有记' : ''}</b></div>` +
    `<div class="remember-text">${esc(r.text)}${r.was ? `<div class="muted small">取代：${esc(r.was)}（旧的一条留在「以前」，可以恢复）</div>` : ''}</div>` +
    `<div class="chat-plan-foot">${foot}</div></div>`;
}
async function rememberRun(p) {
  if (p.state) return;
  try { await api('assistantRememberRun', { plan: p.id }); p.state = 'done'; p.note = '已记住。'; } catch (err) { p.state = 'off'; p.note = err.message; }
  renderAsk();
}
// 没有关联邮件 (W4): up to five candidates the AI found; the user ticks and links them.
function linkCard(p, turn, j) {
  const at = `data-turn="${turn}" data-plan="${j}"`, picks = p.picks || [], live = !p.state;
  const rows = p.link.mails.map((m, i) => `<div class="link-row"><label><input type="checkbox" data-draft="link" value="${i}"` +
    `${picks.includes(i) ? ' checked' : ''}${live ? '' : ' disabled'}><b>${esc(m.subject)}</b></label>` +
    `<span class="muted small">${esc(m.fromName)} · ${esc(String(m.date).slice(5, 16))}</span>` +
    `<a class="link-open" data-open="${esc(m.mid)}" title="打开这封邮件">打开</a></div>`).join('');
  let foot;
  if (p.state === 'done') foot = `<span class="plan-ok">${esc(p.note)}</span>`;
  else if (p.state === 'off') foot = `<span class="muted small">${esc(p.note)}</span>`;
  else foot = '<span class="muted small">勾选属于这条任务的邮件</span><span class="spacer"></span>' +
    `<button class="btn ghost sm" ${at} data-plan-act="skip">都不是</button>` +
    `<button class="btn primary sm" ${at} data-plan-act="link"${picks.length ? '' : ' disabled'}>关联${picks.length > 1 ? ` ${picks.length} 封` : ''}</button>`;
  return `<div class="chat-plan${p.state ? ' ' + p.state : ''}" ${at}><div class="chat-plan-head"><i data-icon="tasks"></i>` +
    `<b>关联邮件到 #${esc(p.link.task)}${p.state === 'done' ? ' · 已关联' : p.state === 'off' ? ' · 未关联' : ' · 待你选择'}</b></div>` +
    `<div class="link-rows">${rows}</div><div class="chat-plan-foot">${foot}</div></div>`;
}
async function linkRun(p) {
  if (p.state || !(p.picks || []).length) return;
  try {
    const result = await api('assistantLinkRun', { plan: p.id, picks: p.picks });
    p.state = 'done'; p.note = `已关联 ${result.linked} 封。可以接着说「回个邮件问问」。`;
    loadMailIndex(); TK.story = null;
    if (S.local === 'tasks') loadTasks().catch(fail);
  } catch (err) { p.state = 'off'; p.note = err.message; }
  renderAsk();
}
// 操作类工具 (A3): what will happen to how many mails, the first few, and the question it
// came from. 执行 runs it through the list's batch methods; 撤销 puts each mail back.
function mailCard(p, turn, j) {
  const m = p.mail, at = `data-turn="${turn}" data-plan="${j}"`;
  const title = p.state === 'done' ? '邮件操作 · 已执行' : p.state === 'undone' ? '邮件操作 · 已撤销' : p.state === 'off' ? '邮件操作 · 未执行' : '邮件操作 · 待你确认';
  const rows = m.samples.map((s) => `<div class="link-row"><b>${esc(s.subject)}</b><span class="muted small">${esc(s.fromName)} · ${esc(String(s.date).slice(5, 16))}` +
    `${s.folder ? ' · ' + esc(s.folder) : ''}</span><a class="link-open" data-open="${esc(s.mid)}" title="打开这封邮件">打开</a></div>`).join('');
  const notes = [m.count > m.samples.length ? `另有 ${m.count - m.samples.length} 封` : '', m.skipped ? `${m.skipped} 封已经是这样，跳过` : '',
    m.local ? `${m.local} 封在本地存档，不能操作` : ''].filter(Boolean).join('；');
  let foot;
  if (p.state === 'running') foot = '<span class="tk-spin"></span>正在执行…';
  else if (p.state === 'done') foot = `<span class="plan-ok">${esc(p.note)}</span><span class="spacer"></span>` +
    (p.undo ? `<button class="btn ghost sm" ${at} data-plan-act="undo">撤销</button>` : '');
  else if (p.state === 'undone' || p.state === 'off') foot = `<span class="muted small">${esc(p.note)}</span>`;
  else foot = `<span class="muted small">点「执行」后才会操作，执行后可以撤销</span><span class="spacer"></span>` +
    `<button class="btn ghost sm" ${at} data-plan-act="edit">修改</button><button class="btn primary sm" ${at} data-plan-act="mail">执行</button>`;
  return `<div class="chat-plan${p.state ? ' ' + p.state : ''}" ${at}><div class="chat-plan-head"><i data-icon="${m.op === 'trash' ? 'trash' : 'mail'}"></i><b>${title}</b>` +
    (p.summary ? `<span class="muted small">${esc(p.summary)}</span>` : '') + '</div>' +
    `<div class="mail-plan-what">${p.state ? '计划' : '将'}${esc(m.what)} <b>${esc(m.count)}</b> 封</div><div class="link-rows">${rows}</div>` +
    (notes ? `<div class="muted small mail-plan-note">${esc(notes)}</div>` : '') +
    `<div class="muted small mail-plan-note">依据：${esc(p.basis)}</div><div class="chat-plan-foot">${foot}</div></div>`;
}
function refreshMail(res) {
  applyFolders(res);
  if (S.search) runSearch(S.search); else loadList(true);
}
async function mailRun(p) {
  if (p.state) return;
  p.state = 'running'; renderAsk();
  try {
    const res = await api('assistantMailRun', { plan: p.id });
    p.state = 'done'; p.undo = res.undo;
    p.note = `已${p.mail.what} ${res.done.length} 封` + (res.failed.length ? `，${res.failed.length} 封未完成，请稍后重试` : '');
    refreshMail(res);
  } catch (err) { p.state = 'off'; p.note = err.message; }
  renderAsk();
}
async function mailUndo(p) {
  const undo = p.undo;
  if (!undo) return;
  p.undo = ''; renderAsk();
  try {
    const res = await api('assistantMailUndo', { undo });
    p.state = 'undone'; p.note = `已撤销：${res.done.length} 封已恢复` + (res.failed.length ? `，${res.failed.length} 封未恢复` : '');
    refreshMail(res);
  } catch (err) { fail(err); }
  renderAsk();
}
async function runPlan(p) {
  if (p.state) return;
  p.state = 'running'; renderAsk();
  try {
    const picks = p.skip && p.skip.size ? p.preview.map((x, i) => i).filter((i) => planReady(p.preview[i]) && !p.skip.has(i)) : null;
    const result = await api('assistantPlanRun', picks ? { plan: p.id, picks } : { plan: p.id });
    p.state = 'done'; p.outcome = result;
    if (result.failed.length) toast(`${result.failed.length} 项没有执行：${result.failed.map((f) => (f.id ? '#' + f.id + ' ' : '') + (TK_OP[f.op] || f.op)).join('、')}`, { error: true, ms: 8000 });
    const ex = result.export || {};
    if (ex.state && ex.state !== 'ok') toast('已保存；导出未完成，见任务列表上方的提示', { error: true });
    if (result.unblocked && result.unblocked.length && typeof offerUnblock === 'function') offerUnblock(result.unblocked);  // P1
    loadMailIndex(); TK.story = null;
    // A task that left the current tab (完成, 取消) opens under 全部, as from a task link.
    await loadTasks();
    if (S.local === 'tasks' && result.ids.length) openTask(result.ids[result.ids.length - 1]).catch(fail);
  } catch (err) { p.state = 'off'; p.note = err.message; }
  renderAsk();
  if (p.state !== 'done' || !p.outcome.nudge) return;
  try { p.nudge = await api('assistantNudge', { nudge: p.outcome.nudge }); } catch (err) { return; }  // Only a reminder.
  if (p.nudge) renderAsk();
}
// A newer plan for the same tasks replaces an older one still waiting (the backend dropped it too).
function supersedePlans(result) {
  const ids = new Set((result.plans || []).flatMap((p) => (p.preview || []).map((x) => x.op !== 'create' && x.id)).filter(Boolean));
  if (!ids.size) return;
  S.chat.turns.forEach((t) => (t.result && t.result !== result && t.result.plans || []).forEach((p) => {
    if (!p.state && (p.preview || []).some((x) => ids.has(x.id) && x.op !== 'create')) { p.state = 'off'; p.note = '已被下面新的计划取代。'; }
  }));
}

// 助理·起草 (W3): a draft card. The AI chose the mail, the purpose and the points; the user
// picks the recipients here (the reply's, or a recipient group) and edits the points. 起草
// opens the composer with the AI's body; the user checks and sends it. Choices live on p,
// so a re-render keeps them.
function draftTarget(p) { const d = p.draft; return p.target !== undefined ? p.target : d.group || (d.reply ? d.mode : ''); }
function draftCard(p, turn, j) {
  const d = p.draft, at = `data-turn="${turn}" data-plan="${j}"`, target = draftTarget(p);
  const group = d.groups.find((g) => g.id === target);
  const points = p.points !== undefined ? p.points : d.points.join('\n');
  const groupText = (g) => `${g.name}（${g.count} 人${g.overlap ? `，重合 ${g.overlap} 人` : ''}${g.external ? `，含外部 ${g.external} 人` : ''}）`;
  const groupOptions = d.groups.length ? `<optgroup label="收件人组">${d.groups.map((g) => `<option value="${esc(g.id)}"${g.id === target ? ' selected' : ''}>${esc(groupText(g))}</option>`).join('')}</optgroup>` : '';
  const live = !p.state, chosen = p.contacts || (d.contacts || []).map((_, i) => i);
  let to;
  if (d.reply) {
    to = `<div class="draft-what">回复 <b>${esc(d.mail.fromName)}</b>${d.mail.domain ? `（@${esc(d.mail.domain)}）` : ''} ${esc(String(d.mail.date).slice(5, 10))} 的《${esc(d.mail.subject)}》</div>` +
      `<label class="draft-row"><span>回复给</span><select data-draft="target"${live ? '' : ' disabled'}>` +
      `<option value="reply"${target === 'reply' ? ' selected' : ''}>按原邮件回复</option><option value="reply_all"${target === 'reply_all' ? ' selected' : ''}>全部回复</option>${groupOptions}</select></label>` +
      (group ? `<label class="draft-keep"><input type="checkbox" data-draft="keep"${p.keep ? ' checked' : ''}${live ? '' : ' disabled'}>保留原发件人在主送</label>` : '');
  } else {
    to = `<div class="draft-what">新邮件《${esc(d.subject)}》</div>` + (d.contacts.length ? `<div class="draft-row"><span>收件人</span><div class="draft-people">${d.contacts.map((c, i) =>
      `<label><input type="checkbox" data-draft="contact" value="${i}"${chosen.includes(i) ? ' checked' : ''}${live ? '' : ' disabled'}>${esc(c.name || c.domain)}` +
      `<span class="muted small">@${esc(c.domain)}</span>${c.external ? '<em class="draft-ext">外部</em>' : ''}</label>`).join('')}</div></div>` : '') +
      `<label class="draft-row"><span>收件人组</span><select data-draft="target"${live ? '' : ' disabled'}><option value="">（不用）</option>${groupOptions}</select></label>`;
  }
  const external = (group ? group.external : 0) + (d.reply ? 0 : chosen.filter((i) => d.contacts[i].external).length);
  let foot;
  if (p.state === 'drafted') foot = '<span class="plan-ok">已打开写信窗口</span><span class="muted small">请核对收件人、加密和正文后自己发送；发出后会提议记一条进展</span>' +
    `<span class="spacer"></span><button class="btn ghost sm" ${at} data-plan-act="draft">再起草一次</button>`;
  else if (p.state === 'off') foot = `<span class="muted small">${esc(p.note)}</span>`;
  else foot = '<span class="muted small">AI 只写正文，不会发送</span><span class="spacer"></span>' +
    `<button class="btn ghost sm" ${at} data-plan-act="skip">不用</button><button class="btn primary sm" ${at} data-plan-act="draft">起草</button>`;
  return `<div class="chat-plan${p.state ? ' ' + p.state : ''}" ${at}><div class="chat-plan-head"><i data-icon="mail"></i>` +
    `<b>${d.reply ? '起草回复' : '起草新邮件'}${p.state === 'drafted' ? ' · 已起草' : p.state === 'off' ? ' · 未起草' : ' · 待你确认'}</b></div>` +
    `<div class="draft-body">${to}<div class="draft-row"><span>目的</span><div>${esc(d.purpose)}</div></div>` +
    (d.scheduleAt ? `<div class="draft-row"><span>定时</span><div>${esc(scheduleText(d.scheduleAt))} 发出（写信窗口里可以改；仍由你点发送）</div></div>` : '') +
    `<label class="draft-row"><span>要点</span><textarea data-draft="points" rows="${Math.min(6, Math.max(2, points.split('\n').length))}"` +
    ` placeholder="每行一条"${live ? '' : ' disabled'}>${esc(points)}</textarea></label>` +
    (external ? `<div class="draft-warn">含外部收件人 ${external} 人：请在写信窗口核对；外部收件人默认不加密</div>` : '') +
    `</div><div class="chat-plan-foot">${foot}</div></div>`;
}
async function draftFromPlan(p) {
  const d = p.draft, params = { plan: p.id, target: draftTarget(p), keep_sender: !!p.keep };
  if (!d.reply) params.contacts = p.contacts || d.contacts.map((_, i) => i);
  let draft;
  try { draft = await api('assistantDraftCompose', params); } catch (err) {
    fail(err);
    if (err.code === 'AI_PLAN_EXPIRED') { p.state = 'off'; p.note = err.message; renderAsk(); }
    return;
  }
  if (C.draft && !$('#composer').classList.contains('hidden') && C.dirty) await saveDraft(true);
  openComposer(draft);
  $('#cmp-title').textContent += ' · 助理起草';
  S.assistDrafts.set(draft.localId, p.id);
  p.state = 'drafted'; renderAsk();
  const points = (p.points !== undefined ? p.points : d.points.join('\n')).split('\n').map((x) => x.trim()).filter(Boolean);
  let allow = false;
  for (;;) {
    try { await streamIntoComposer('assistantDraftWrite', { plan: p.id, points, allow_encrypted: allow }); return; } catch (err) {
      if (err.code === 'AI_ENCRYPTED_CONFIRM' && !allow && await confirmDialog('把加密邮件发送给 AI 服务？',
        '要回复的邮件是加密的。起草需要把解密后的正文发送到外部 AI 服务，邮件将不再只在你和收件人之间保密。仅这一次、仅这一封。', '发送并起草')) { allow = true; continue; }
      if (err.code !== 'AI_CANCELLED') fail(err);
      return;
    }
  }
}
$('#ask-out').addEventListener('change', (e) => {
  const el = e.target.closest('[data-draft]'), card = el && el.closest('.chat-plan[data-plan]');
  if (!card) return;
  const p = S.chat.turns[+card.dataset.turn].result.plans[+card.dataset.plan];
  if (el.dataset.draft === 'target') p.target = el.value;
  else if (el.dataset.draft === 'keep') p.keep = el.checked;
  else if (el.dataset.draft === 'contact') p.contacts = $$('[data-draft="contact"]', card).filter((c) => c.checked).map((c) => +c.value);
  else if (el.dataset.draft === 'link') p.picks = $$('[data-draft="link"]', card).filter((c) => c.checked).map((c) => +c.value);
  if (el.dataset.draft !== 'points') renderAsk();
});
$('#ask-out').addEventListener('change', (e) => {
  const box = e.target.closest('.tk-pick'), card = box && box.closest('.chat-plan[data-plan]');
  if (!card) return;
  const p = S.chat.turns[+card.dataset.turn].result.plans[+card.dataset.plan];
  p.skip = p.skip || new Set();
  if (box.checked) p.skip.delete(+box.dataset.pick); else p.skip.add(+box.dataset.pick);
  renderAsk();
});
$('#ask-out').addEventListener('input', (e) => {
  const el = e.target.closest('[data-draft="points"]'), card = el && el.closest('.chat-plan[data-plan]');
  if (card) S.chat.turns[+card.dataset.turn].result.plans[+card.dataset.plan].points = el.value;
});

// A drafted mail that went out (or into the outbox): offer the progress line (Q5: never by itself).
S.assistDrafts = new Map();  // composer localId -> draft plan id
const _assistSendOutcome = sendOutcome;
sendOutcome = function (res, meta = {}) { _assistSendOutcome(res, meta); assistAfterSend(res); };
async function assistAfterSend(res) {
  const plan = res && S.assistDrafts.get(res.localId);
  if (!plan || !['Accepted', 'Queued'].includes(res.state)) return;
  S.assistDrafts.delete(res.localId);
  let next;
  try { next = await api('assistantSent', { plan }); } catch (err) { return; }  // Only a proposal.
  if (!next) return;
  S.chat.turns.push({ event: '邮件已发出。要在任务里记一条进展吗？', result: { plans: [next] } });
  if (chatOpen()) renderAsk();
  else toast('邮件已发出。要在任务里记一条进展吗？', { ms: 9000, action: { label: '查看', run: () => showAsk() } });
}

// A source opens in the reader beside the conversation; where the reader is not shown
// (任务, 通讯录, a pop-out) it opens in its own window.
// A task opens in 任务 (the conversation stays); one not on the current tab shows under 全部.
async function openTask(id) {
  id = Number(id);
  if (!id) return;
  if (S.local !== 'tasks') await openTasks();
  if (TK.rows.some((r) => r.id === id)) { showTask(id); return; }
  clearTaskFilter(); TK.view = 'all'; TK.selected = id;
  $$('#tk-tabs [data-view]').forEach((b) => b.classList.toggle('on', b.dataset.view === 'all'));
  await loadTasks();
}
function openSource(mid) {
  if (!mid) return;
  if (READER_MID || $('#reader').offsetParent === null) { api('openReader', { mid }).catch(fail); return; }
  if (S.multi.size) clearSelection(false);
  selectMessage(mid);
}
$('#ask-out').addEventListener('click', async (e) => {
  const t = e.target, turns = S.chat.turns, last = turns[turns.length - 1];
  const ref = t.closest('[data-ref]'), allow = t.closest('[data-allow]'), act = t.closest('[data-ask]'), tried = t.closest('[data-try]');
  const task = t.closest('[data-task]'), planAct = t.closest('[data-plan-act]');
  const turn = turns[+((t.closest('[data-turn]') || {}).dataset || {}).turn] || last;
  if (planAct) {
    const p = turn.result.plans[+planAct.dataset.plan];
    if (planAct.dataset.planAct === 'run') runPlan(p);
    else if (planAct.dataset.planAct === 'verify') showVerify(p.outcome);
    else if (planAct.dataset.planAct === 'draft') draftFromPlan(p);
    else if (planAct.dataset.planAct === 'link') linkRun(p);
    else if (planAct.dataset.planAct === 'remember') rememberRun(p);
    else if (planAct.dataset.planAct === 'mail') mailRun(p);
    else if (planAct.dataset.planAct === 'undo') mailUndo(p);
    else if (planAct.dataset.planAct.startsWith('nudge-')) nudgeAct(p, turn, planAct.dataset.planAct);
    else if (planAct.dataset.planAct === 'skip') { p.state = 'off'; p.note = p.kind === 'link' ? '没有关联。' : p.kind === 'remember' ? '没有记。' : '没有起草。'; renderAsk(); }
    else { p.state = 'off'; p.note = '没有执行。在下面说说要怎么改，例如「日期改成昨天」。'; renderAsk(); $('#ask-input').focus(); }
  } else if (t.closest('[data-open]')) openSource(t.closest('[data-open]').dataset.open);
  else if (tried) {  // A sample ending in … is only begun: the user finishes it.
    const q = tried.dataset.try; $('#ask-input').value = q.replace(/…$/, '');
    if (q.endsWith('…')) { fitAsk(); $('#ask-input').focus(); } else askMailbox();
  }
  else if (task) openTask(task.dataset.task).catch(fail);
  else if (act) {
    ({ cancel: () => api('cancelAsk').catch(() => {}),
       again: () => askMailbox(last.allow, last),
       setup: () => openSettings('ai'),
       new: newChat,
    })[act.dataset.ask]();
  } else if (allow) {
    if (!(await confirmDialog('把这封加密邮件发送给 AI 服务？',
      '回答这个问题需要把这封邮件解密后的正文发送到外部 AI 服务，邮件将不再只在你和收件人之间保密。仅这一次提问有效。', '发送并重新回答'))) return;
    askMailbox([...last.allow, allow.dataset.allow], last);
  } else if (ref) {
    const source = turn && turn.result && turn.result.sources.find((s) => s.ref === ref.dataset.ref);
    openSource(source && source.mid);
  } else if (t.closest('.ask-src')) {
    openSource(t.closest('.ask-src').dataset.mid);
  }
});
on('askProgress', (p) => {
  const last = S.chat.turns[S.chat.turns.length - 1];
  if (last && last.loading && p) { last.steps.push(p.step); renderAsk(); }
});
function fitAsk() {
  const box = $('#ask-input');
  box.style.height = 'auto';
  box.style.height = Math.min(box.scrollHeight, 6 * 21 + 8) + 'px';
  box.style.overflowY = box.scrollHeight > 6 * 21 + 8 ? 'auto' : 'hidden';
}
$('#btn-ask').onclick = () => {  // The words go to the conversation; the list goes back to the folder.
  const q = $('#search').value;
  if (q.trim()) { $('#search').value = ''; clearSearch(); }
  showAsk(q);
};
// The mail list is folded away while chatting: going to a folder or view brings it back.
$('#app .sidebar').addEventListener('click', (e) => { if (chatOpen() && e.target.closest('a.folder')) hideAsk(); }, true);
$('#chat-here').onclick = () => { const here = chatHere(); if (here) { S.chat.attach = S.chat.attach === here.key ? '' : here.key; renderHere(); } };
$('#ask-close').onclick = hideAsk;
$('#chat-new').onclick = newChat;
$('#ask-form').onsubmit = (e) => { e.preventDefault(); askMailbox(); };
$('#ask-input').addEventListener('input', fitAsk);
$('#ask-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); askMailbox(); }
  if (e.key === 'Escape') { e.stopPropagation(); hideAsk(); }
});
