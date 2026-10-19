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
    status.textContent = (a.demo ? '演示模式：摘要由本机生成，不联网。' : a.hasKey ? '已设置 API Key。' : '未设置 API Key，「总结」暂不可用。') +
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
