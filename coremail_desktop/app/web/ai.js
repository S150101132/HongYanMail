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
  const head = (extra) => `<div class="ai-head"><i data-icon="spark"></i><b>AI 摘要</b>${extra}<span class="spacer"></span>` +
    (state.loading ? '' : '<button class="btn ghost" data-ai="again" title="重新发送给 AI 服务生成">重新生成</button>') +
    '<button class="icon-btn sm" data-ai="close" title="关闭摘要"><i data-icon="close"></i></button></div>';
  if (state.loading) {
    box.innerHTML = head('<span class="muted small">正在总结…（邮件内容已发送到 AI 服务）</span>') + '<div class="ai-body"><div class="ai-wait"><span></span><span></span><span></span></div></div>';
  } else if (state.error) {
    const setup = state.error.code === 'AI_NOT_CONFIGURED' || state.error.code === 'AI_AUTH_FAILED';
    box.innerHTML = head('') + `<div class="ai-body"><p class="ai-error">${esc(state.error.message)}</p>` +
      (setup ? '<button class="btn primary" data-ai="setup">打开 AI 设置</button>' : '') + '</div>';
  } else {
    const list = (items) => items.length ? `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '<p class="muted small">无</p>';
    box.innerHTML = head(`<span class="muted small">由 ${esc(state.model || 'AI')} 生成${state.cached ? `（${esc(state.at)} 的结果）` : ''}，仅供参考</span>`) +
      `<div class="ai-body">${state.conclusion ? `<p class="ai-conclusion">${esc(state.conclusion)}</p>` : ''}` +
      `<h4>要点</h4>${list(state.points)}<h4>需要我做什么</h4>${list(state.actions)}</div>`;
  }
  renderIcons(box);
  $$('[data-ai]', box).forEach((b) => (b.onclick = () => ({
    close: () => { S.ai.delete(m.id); renderAiPanel(m); },
    again: () => summarizeMessage(m, { refresh: true }),
    setup: () => { openSettings(); setTimeout(() => $('#ai-section').scrollIntoView({ block: 'start' }), 50); },
  }[b.dataset.ai])()));
}

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
       setup: () => { openSettings(); setTimeout(() => $('#ai-section').scrollIntoView({ block: 'start' }), 50); },
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
