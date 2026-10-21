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
      box.innerHTML = head(by) + `<div class="ai-body">${state.conclusion ? `<p class="ai-conclusion">${esc(state.conclusion)}</p>` : ''}` +
        `<h4>要点</h4>${list(state.points)}<h4>需要我做什么</h4>${list(state.actions)}</div>`;
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
    { label: '总结这封', icon: 'spark', run: () => summarizeMessage(m) },
    { label: '总结整个会话', icon: 'spark', run: () => summarizeThread(m) },
  ]);
}

// Encrypted mail goes to the AI service only when the user agrees, this once.
async function allowEncrypted(m, what) {
  if (m.encryption === 'locked') { toast('这是一封加密邮件，请先解锁个人证书', { error: true }); return null; }
  if (m.encryption !== 'decrypted') return false;
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
  const ed = $('#cmp-editor');
  const live = document.createElement('div');
  live.className = 'ai-draft'; live.contentEditable = 'false'; live.textContent = 'AI 正在起草…';
  const first = ed.firstElementChild;
  if (first && first.tagName === 'P' && !first.textContent.trim()) first.replaceWith(live); else ed.prepend(live);
  aiBusy('AI 正在起草回复…');
  let text = '';
  try {
    const res = await streamCall('draftReply', { mid: m.id, idea: idea ? `${idea.label}：${idea.hint}` : '', note, allow_encrypted: !!state.allow },
      (d) => { text += d; live.textContent = text; });
    text = res.text;
  } catch (err) {
    if (err.code !== 'AI_CANCELLED') fail(err);
  }
  aiBusy('');
  if (!live.isConnected) return;
  if (text.trim()) { live.insertAdjacentHTML('beforebegin', textHtml(text)); C.dirty = true; }
  else live.insertAdjacentHTML('beforebegin', '<p><br></p>');
  live.remove();
  ed.focus();
}

// The composer's status line while a streamed call runs, with 停止.
function aiBusy(text) {
  const status = $('#cmp-status');
  status.innerHTML = text ? `${esc(text)} <button class="btn ghost sm" id="cmp-ai-stop">停止</button>` : '';
  const stop = $('#cmp-ai-stop'); if (stop) stop.onclick = () => api('cancelAi').catch(() => {});
}

// ---------------------------------------------------------------- 写信助手
// Only the user's own words: the selection, or the body above the signature and the
// quoted original. Those two never go to the AI service.
const AI_MODES = [['polish', '润色'], ['formal', '更正式'], ['concise', '更简洁'], ['polite', '更客气'], ['en', '译成英文'], ['zh', '译成中文']];

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
function ownRange() {
  const ed = $('#cmp-editor'), sel = getSelection();
  if (sel.rangeCount && !sel.isCollapsed && ed.contains(sel.getRangeAt(0).commonAncestorContainer)) return { range: sel.getRangeAt(0).cloneRange(), part: '选中的文字', inline: true };
  return { range: bodyRange(), part: '正文（不含签名和引用的原邮件）' };
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

function aiComposeMenu(button) {
  const r = button.getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [
    ...AI_MODES.map(([mode, label]) => ({ label, icon: 'spark', run: () => rewriteOwn(mode, label) })),
    { label: '自定义要求…', icon: 'pen', run: async () => {
      const want = await promptDialog({ title: '告诉 AI 怎么改', body: '<input id="ai-want" maxlength="300" placeholder="例如：语气坚决一点，但保持礼貌">',
        okText: '改写', onOpen: () => $('#ai-want').focus(), collect: () => $('#ai-want').value.trim() || null });
      if (want) rewriteOwn('custom', want, want);
    } },
    '-',
    { label: '发送前检查（AI）', icon: 'check', run: aiCheckDraft },
  ]);
}

async function rewriteOwn(mode, label, instruction = '') {
  if (C.ai && C.ai.loading) return;
  const { range, part, inline } = ownRange();
  if (rangeHasMedia(range)) { toast(`${part}包含表格或图片，请只选中要处理的文字`, { error: true }); return; }
  const text = rangeText(range);
  if (!text) { toast('还没有可以处理的文字', { error: true }); return; }
  if (text.length > 8000) { toast('文字太长（最多 8000 字），请选中一部分再处理', { error: true }); return; }
  if (!S.aiWriteNoticed && !localStorage.getItem('hy-ai-write')) {
    if (!(await confirmDialog('写信助手', `${part}会发送到「设置 → AI 助手」中配置的 AI 服务。签名和引用的原邮件不会发送。` +
      '如果这封邮件要加密发送，请注意改写前的文字会离开本机。', '继续'))) return;
    S.aiWriteNoticed = true; try { localStorage.setItem('hy-ai-write', '1'); } catch (e) { /* private window */ }
  }
  C.ai = { mode, label, instruction, range, part, inline, text: '', loading: true };
  renderComposeAi();
  const state = C.ai;
  try {
    const res = await streamCall('rewrite', { text, mode, instruction }, (d) => { if (C.ai === state) { state.text += d; renderComposeAi(true); } });
    state.text = res.text;
  } catch (err) { state.error = err; }
  state.loading = false;
  if (C.ai === state) renderComposeAi();
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
      (a.text ? '<p class="muted small">替换后，原文中的加粗、颜色等格式会去掉；替换后可以撤销。</p>' +
        '<div class="row-gap"><button class="btn primary sm" data-cai="replace">替换</button><button class="btn ghost sm" data-cai="below">插入到下方</button>' +
        '<button class="btn ghost sm" data-cai="again">再来一次</button><button class="btn ghost sm" data-cai="close">放弃</button></div>' :
        '<div class="row-gap"><button class="btn ghost sm" data-cai="again">再试一次</button></div>'));
  renderIcons(box);
  $$('[data-cai]', box).forEach((b) => (b.onclick = () => ({
    stop: () => api('cancelAi').catch(() => {}),
    close: () => { if (a.loading) api('cancelAi').catch(() => {}); C.ai = null; renderComposeAi(); },
    again: () => { C.ai = null; rewriteOwn(a.mode, a.label, a.instruction); },
    replace: () => placeAi(a, false), below: () => placeAi(a, true),
  }[b.dataset.cai])()));
}

// Placed by hand, not execCommand: Chromium would merge the last paragraph with the
// signature below it. 撤销 restores the editor as it was.
function placeAi(a, below) {
  const ed = $('#cmp-editor'), range = a.range;
  if (!ed.contains(range.commonAncestorContainer)) { toast('原来的文字已经改动，请重新选择后再试', { error: true }); return; }
  const before = ed.innerHTML;
  const html = a.inline ? (below ? '<br>' : '') + esc(a.text).replace(/\n/g, '<br>') : textHtml(a.text);
  const piece = range.createContextualFragment(html), last = piece.lastChild;
  if (below) range.collapse(false); else range.deleteContents();
  range.insertNode(piece);
  if (last) { const r = document.createRange(); r.setStartAfter(last); r.collapse(true); const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r); }
  ed.focus();
  C.dirty = true; C.ai = null; renderComposeAi();
  toast(below ? '已插入' : '已替换', { ms: 8000, action: { label: '撤销', run: () => { ed.innerHTML = before; C.dirty = true; } } });
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

async function aiCheckDraft() {
  const value = collectDraft(false), text = wholeOwnText();
  if (!text) { toast('还没有正文可以检查', { error: true }); return; }
  const who = (list) => list.map((a) => parseAddressList(a)[0]).filter(Boolean)
    .map((p) => `${p.name || p.email.split('@')[0]}（${p.email.split('@')[1] || ''}）`);
  aiBusy('AI 正在检查…（正文已发送到 AI 服务）');
  let res;
  try {
    res = await api('checkDraft', { subject: value.subject, to: who(value.to), cc: who(value.cc), text,
      attachments: (value.attachments || []).map((a) => a.name) });
  } catch (err) { aiBusy(''); fail(err); return; }
  aiBusy('');
  if (!res.issues.length) { toast('AI 没有发现明显的问题'); return; }
  await promptDialog({ title: 'AI 发送前检查', text: '点击一条可以在正文中定位（只是提示，鸿雁不会自动修改）：', body: issueList(res.issues) });
}
$('#prompt-body').addEventListener('click', (e) => {
  const li = e.target.closest('.precheck-list li');
  if (!li) return;
  const quote = $('.precheck-quote', li);
  $('#prompt [data-close]').click();
  findInEditor(quote && quote.textContent);
});
$('#cmp-ai').onclick = (e) => aiComposeMenu(e.currentTarget);

async function summarizeMessage(m, opts = {}) {
  const current = S.ai.get(m.id);
  if (current && current.loading) return;
  if (current && !current.error && !opts.refresh) { $('#ai-panel').scrollIntoView({ block: 'nearest' }); return; }
  if (m.encryption === 'locked') { toast('这是一封加密邮件，请先解锁个人证书再总结', { error: true }); return; }
  let allow = false;
  if (m.encryption === 'decrypted') {
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
    $('#ai-base').value = a.baseUrl; $('#ai-model').value = a.model; $('#ai-key').value = '';
    $('#ai-key').placeholder = a.hasKey ? '已保存（不可查看），输入新 Key 可替换' : '未设置';
    $('#ai-clear').classList.toggle('hidden', !a.hasKey);
    status.textContent = (a.demo ? '演示模式：摘要由本机生成，不联网。' : a.hasKey ? '已设置 API Key。' : '未设置 API Key，「总结」和「问邮箱」暂不可用。') +
      ` 本月已调用 ${a.usage.calls} 次，共 ${a.usage.tokens} tokens。`;
  }).catch(fail);
}
function aiValues() {
  const values = { base_url: $('#ai-base').value, model: $('#ai-model').value };
  const key = $('#ai-key').value.trim(); if (key) values.key = key;
  return values;
}
$('#ai-save').onclick = async () => {
  try { await api('setAiSettings', aiValues()); toast('AI 设置已保存'); renderAiSettings(); } catch (err) { fail(err); }
};
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
// The answer and its sources are plain text (esc); [M3] citations become links to
// the source cards, which open the mail in a pop-out reader.
S.ask = null;  // {question, allow, loading, steps, result | error}

function showAsk(question) {
  $('#reader').classList.add('asking');
  if (question && question.trim()) { $('#ask-input').value = question.trim(); askMailbox(); }
  else { renderAsk(); $('#ask-input').focus(); }
}
function hideAsk() { $('#reader').classList.remove('asking'); }

async function askMailbox(allow = []) {
  const question = $('#ask-input').value.trim();
  if (!question || (S.ask && S.ask.loading)) return;
  const state = S.ask = { question, allow, loading: true, steps: [] };
  renderAsk();
  try { state.result = await api('askMailbox', { question, allow }); } catch (err) { state.error = err; }
  state.loading = false;
  if (S.ask === state) renderAsk();
}

function renderAsk() {
  const out = $('#ask-out'), a = S.ask;
  $('#ask-run').disabled = !!(a && a.loading);
  if (!a) { out.innerHTML = ''; return; }
  const stepList = (steps) => steps.length ? `<ol class="ask-steps">${steps.map((step) => `<li>${esc(step)}</li>`).join('')}</ol>` : '';
  if (a.loading) {
    out.innerHTML = '<div class="ask-card"><div class="ai-head"><i data-icon="spark"></i><b>正在查找</b><span class="muted small">搜索和阅读的内容会发送到 AI 服务</span>' +
      `<button class="btn ghost" data-ask="cancel">取消</button></div><div class="ai-body"><div class="ai-wait"><span></span><span></span><span></span></div>${stepList(a.steps)}</div></div>`;
  } else if (a.error) {
    const setup = a.error.code === 'AI_NOT_CONFIGURED' || a.error.code === 'AI_AUTH_FAILED';
    out.innerHTML = `<div class="ask-card"><div class="ai-body"><p class="${a.error.code === 'AI_CANCELLED' ? 'muted' : 'ai-error'}">${esc(a.error.message)}</p>` +
      (setup ? '<button class="btn primary" data-ask="setup">打开 AI 设置</button>' : '') + stepList(a.steps) + '</div></div>';
  } else {
    const r = a.result;
    const answer = esc(r.answer).replace(/\[(M\d+(?:, M\d+)*)\]/g, (_, refs) =>
      refs.split(', ').map((ref) => `<a class="ask-ref" data-ref="${ref}" title="打开出处 ${ref.slice(1)}">${ref.slice(1)}</a>`).join(''));
    const card = (s) => `<div class="ask-src" data-mid="${esc(s.mid)}" title="在新窗口中打开这封邮件"><span class="ask-num">${esc(s.ref.slice(1))}</span>` +
      `<div class="ask-src-main"><div class="ask-src-top"><b>${esc(s.subject)}</b><span class="muted small">${esc(s.fromName)} · ${esc(String(s.date).slice(0, 16))}</span></div>` +
      (s.snippet ? `<div class="muted small ask-snip">${esc(s.snippet)}</div>` : '') +
      (s.encrypted ? `<div class="ask-enc"><i data-icon="lock"></i>加密邮件，AI 没有读取正文<button class="btn ghost sm" data-allow="${esc(s.mid)}">允许读取这封并重新回答</button></div>` : '') +
      '</div></div>';
    const cited = r.sources.filter((s) => s.cited), other = r.sources.filter((s) => !s.cited);
    out.innerHTML = `<div class="ask-card"><div class="ai-head"><i data-icon="spark"></i><b>答案</b><span class="muted small">由 ${esc(r.model || 'AI')} 根据邮件生成，仅供参考</span>` +
      `<button class="btn ghost" data-ask="again" title="重新发送给 AI 服务查找">重新回答</button></div><div class="ai-body"><p class="ask-answer">${answer}</p>` +
      (r.limited ? '<p class="ask-limited">已达到查找次数或时间的上限，答案可能不完整。</p>' : '') + '</div></div>' +
      (cited.length ? `<div class="ask-sources"><h4>出处</h4>${cited.map(card).join('')}</div>` : '') +
      (other.length ? `<div class="ask-sources"><h4>也查阅过</h4>${other.map(card).join('')}</div>` : '') +
      (r.steps.length ? `<details class="ask-process"><summary>查找过程（${r.steps.length} 步）</summary>${stepList(r.steps)}</details>` : '');
  }
  renderIcons(out);
}

function openSource(mid) { if (mid) api('openReader', { mid }).catch(fail); }
$('#ask-out').addEventListener('click', async (e) => {
  const a = S.ask, t = e.target;
  const ref = t.closest('[data-ref]'), allow = t.closest('[data-allow]'), act = t.closest('[data-ask]');
  if (act) {
    ({ cancel: () => api('cancelAsk').catch(() => {}),
       again: () => { $('#ask-input').value = a.question; askMailbox(a.allow); },
       setup: () => openSettings('ai'),
    })[act.dataset.ask]();
  } else if (allow) {
    if (!(await confirmDialog('把这封加密邮件发送给 AI 服务？',
      '回答这个问题需要把这封邮件解密后的正文发送到外部 AI 服务，邮件将不再只在你和收件人之间保密。仅这一次提问有效。', '发送并重新回答'))) return;
    $('#ask-input').value = a.question;
    askMailbox([...a.allow, allow.dataset.allow]);
  } else if (ref) {
    const source = a.result.sources.find((s) => s.ref === ref.dataset.ref);
    openSource(source && source.mid);
  } else if (t.closest('.ask-src')) {
    openSource(t.closest('.ask-src').dataset.mid);
  }
});
on('askProgress', (p) => { if (S.ask && S.ask.loading && p) { S.ask.steps.push(p.step); renderAsk(); } });
$('#btn-ask').onclick = () => showAsk($('#search').value);
$('#ask-close').onclick = hideAsk;
$('#ask-form').onsubmit = (e) => { e.preventDefault(); askMailbox(); };
$('#ask-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); askMailbox(); }
  if (e.key === 'Escape') { e.stopPropagation(); hideAsk(); }
});
