'use strict';
/* 鸿雁 — mail rules and holiday auto-reply (设置), meeting invitations (reader). Loaded after app.js.
   Hooks into app.js by wrapping renderMessage / selectMessage / openSettings. Every write goes through the
   backend, which reads the server back and reports success only when the change is there. No 标签 (R4):
   星标 is enough; rules made on the web that add a server tag still show and keep that action. */

ICONS.calendar = '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>';
ICONS.rule = '<path d="M4 6h16M4 12h10M4 18h6"/><path d="m16 15 3 3-3 3"/>';
ICONS.up = '<path d="m6 15 6-6 6 6"/>';
ICONS.down = '<path d="m6 9 6 6 6-6"/>';

// Server tags, only to name them in rules made on the web.
const TG = {
  list: [],
  byId(id) { return this.list.find((t) => t.id === id); },
  async load() { try { this.list = (await api('tags', { refresh: false })).tags; } catch (e) { /* only for names */ } },
};

// ---------------------------------------------------------------- meeting invitations
const IV = {
  ANSWER: { accept: '接受', tentative: '暂定', decline: '拒绝' },
  STATE: { 'ACCEPTED': ['已接受', 'ok'], 'DECLINED': ['已拒绝', 'bad'], 'TENTATIVE': ['暂定', 'maybe'], 'NEEDS-ACTION': ['未答复', ''], 'DELEGATED': ['已委托', ''] },
  when(c) {
    const W = '日一二三四五六';
    const day = (d) => `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 周${W[d.getDay()]}`;
    const hm = (d) => `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    if (c.allDay) {
      const s = new Date(c.startDate + 'T00:00'), e = new Date((c.endDate || c.startDate) + 'T00:00');
      const last = new Date(e.getTime() - 86400000);
      return last > s ? `${day(s)} – ${day(last)}（全天）` : `${day(s)}（全天）`;
    }
    if (c.start == null) return c.floating ? `${c.floating}（邀请未注明可识别的时区，按原样显示）` : '时间未注明';
    const s = new Date(c.start), e = c.end != null ? new Date(c.end) : null;
    const zone = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (x) { return ''; } })();
    const range = !e ? `${day(s)} ${hm(s)}` : sameDay(s, e) ? `${day(s)} ${hm(s)} – ${hm(e)}` : `${day(s)} ${hm(s)} – ${day(e)} ${hm(e)}`;
    return range + (zone ? `<span class="muted small">（本机时区 ${esc(zone)}）</span>` : '');
  },
  person(p) { return p.name ? `${esc(p.name)} <span class="muted">&lt;${esc(p.email)}&gt;</span>` : esc(p.email || '（未注明）'); },
  render(m) {
    const old = $('#iv-card'); if (old) old.remove();
    const c = m.invitation; if (!c) return;
    const card = document.createElement('div');
    card.id = 'iv-card'; card.className = 'iv-card' + (c.cancelled ? ' cancelled' : '');
    const me = (S.profile.identities || [S.profile.account]).map((x) => String(x).toLowerCase());
    const mine = c.attendees.find((a) => me.includes(String(a.email).toLowerCase()));
    const counts = {}; c.attendees.forEach((a) => (counts[a.partstat] = (counts[a.partstat] || 0) + 1));
    const answered = m.invitation.answer;
    const kind = c.cancelled ? '会议已取消' : c.method === 'REQUEST' ? '会议邀请' : c.method === 'REPLY' ? '会议答复' : '会议信息';
    card.innerHTML = `<div class="iv-head"><i data-icon="calendar"></i><span class="iv-kind">${kind}</span>${c.recurring ? '<span class="iv-badge">周期会议</span>' : ''}
        <span class="spacer"></span><button class="btn ghost sm" id="iv-ics" title="保存为 .ics 文件，可导入其他日历"><i data-icon="download"></i>下载 .ics</button></div>
      <div class="iv-title">${esc(c.summary || '（无标题会议）')}</div>
      <div class="iv-grid">
        <span class="muted">时间</span><span>${this.when(c)}</span>
        ${c.location ? `<span class="muted">地点</span><span>${esc(c.location)}</span>` : ''}
        <span class="muted">组织者</span><span>${this.person(c.organizer)}</span>
        ${c.attendees.length ? `<span class="muted">参会人</span><span><a id="iv-people">${c.attendees.length} 人</a>
          <span class="muted small">${Object.entries(counts).map(([k, n]) => `${(this.STATE[k] || [k])[0]} ${n}`).join(' · ')}</span></span>` : ''}
      </div>
      <div class="iv-people hidden">${c.attendees.map((a) => { const [t, cls] = this.STATE[a.partstat] || [a.partstat, '']; return `<div class="iv-person">
        ${avatarHtml(a.name, a.email)}<span>${this.person(a)}${a.role === 'OPT-PARTICIPANT' ? '<span class="muted small"> · 可选</span>' : ''}</span>
        <span class="iv-state ${cls}">${t}</span></div>`; }).join('')}</div>
      ${c.description ? `<details class="iv-desc"><summary>会议说明</summary><div>${esc(c.description)}</div></details>` : ''}
      <div class="iv-foot">${c.cancelled ? '<span class="iv-cancel"><i data-icon="alert"></i>组织者已取消这次会议，无需答复。</span>'
        : c.method === 'REQUEST' && m.invitation.canAnswer ? `<span class="muted small">${answered ? `你已答复：<b>${this.ANSWER[answered]}</b> · 可以更改` : mine && mine.partstat !== 'NEEDS-ACTION' ? `邀请中的状态：${(this.STATE[mine.partstat] || [''])[0]}` : '答复会发送给组织者'}</span>
          <span class="spacer"></span>${Object.entries(this.ANSWER).map(([k, t]) => `<button class="btn ${k === 'accept' ? 'primary' : 'ghost'} sm${answered === k ? ' on' : ''}" data-answer="${k}">${t}</button>`).join('')}`
        : c.method === 'REQUEST' ? '<span class="muted small">共享邮箱的邀请请在网页版中答复</span>' : ''}</div>`;
    $('#r-banners').after(card);
    renderIcons(card);
    const people = $('#iv-people', card); if (people) people.onclick = () => $('.iv-people', card).classList.toggle('hidden');
    $('#iv-ics', card).onclick = () => api('saveAttachment', { mid: m.id, part: 'invite.ics' }).then((ok) => ok && toast('已保存 .ics 文件')).catch(fail);
    $$('[data-answer]', card).forEach((b) => (b.onclick = () => this.answer(m, b.dataset.answer)));
  },
  async answer(m, answer) {
    const c = m.invitation;
    const to = c.organizer.name || c.organizer.email || '组织者';
    if (!(await confirmDialog(`${this.ANSWER[answer]}会议邀请`, `将通过邮箱服务器向 ${to} 发送「${this.ANSWER[answer]}」答复：${c.summary || '（无标题会议）'}。`, `发送「${this.ANSWER[answer]}」`))) return;
    try {
      await api('respondInvitation', { mid: m.id, answer });
      c.answer = answer;
      if (S.message === m) this.render(m);
      toast(`已向组织者发送「${this.ANSWER[answer]}」`);
    } catch (err) { fail(err); }
  },
};

// ---------------------------------------------------------------- settings: rules, auto-reply
const RL = {
  FIELDS: { from: '发件人', to: '收件人', subject: '主题', attachment: '有附件', size: '邮件大小' },
  OPS: { contains: '包含', '=': '是', endsWith: '结尾是' },
  ACTIONS: { move: '移动到', read: '标为已读', flag: '添加星标', forward: '转发到' },
  rules: [], loaded: false,
  folderName(id) { const f = S.folders.find((x) => x.id === String(id)); return f ? f.name : `文件夹 ${id}`; },
  condText(c) {
    if (c.field === 'attachment') return '有附件';
    if (c.field === 'size') return `邮件大小${c.op === '>' ? '大于' : '小于'} ${+(c.value / 1048576).toFixed(2)} MB`;
    const values = String(c.value).split(',').map((v) => v.trim()).filter(Boolean);
    return `${this.FIELDS[c.field]}${this.OPS[c.op] || c.op} ${values.length > 3 ? `${values.slice(0, 3).map(esc).join('、')} 等 ${values.length} 项` : values.map(esc).join('、')}`;
  },
  actText(a) {
    if (a.type === 'move') return `移动到「${esc(this.folderName(a.folder))}」`;
    if (a.type === 'forward') return `转发到 ${esc(a.to)}`;
    if (a.type === 'tag') { const t = TG.byId(a.tag); return `添加标签「${esc(t ? t.name : a.tag)}」`; }
    return this.ACTIONS[a.type] || a.type;
  },
  async load() {
    const pane = $('#st-rules');
    pane.innerHTML = '<p class="muted">正在读取服务器上的收信规则…</p>';
    try { this.rules = (await api('rules')).rules; this.loaded = true; this.render(); } catch (err) { pane.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  },
  render() {
    const pane = $('#st-rules');
    pane.innerHTML = `<div class="st-intro"><p class="muted small">新邮件到达时，服务器按从上到下的顺序执行这些规则（与网页版共用）。修改会立即影响收信，请仔细核对。</p>
      <button class="btn primary" id="rl-new"><i data-icon="rule"></i>新建规则</button></div>
      <div class="rl-list">${this.rules.length ? this.rules.map((r, i) => `<div class="rl-item${r.enabled ? '' : ' off'}" data-id="${esc(r.id)}">
        <label class="switch" title="${r.enabled ? '已启用，点击停用' : '已停用，点击启用'}"><input type="checkbox" data-on ${r.enabled ? 'checked' : ''}><span></span></label>
        <div class="rl-main"><div class="rl-name">${esc(r.name)}${r.editable ? '' : '<span class="iv-badge" title="含鸿雁暂不支持的条件或动作，只能在网页版修改">网页版规则</span>'}${r.stop ? '<span class="iv-badge">执行后停止</span>' : ''}</div>
          <div class="rl-sum muted small">如果${r.match === 'any' ? '任一' : ''} ${[r.conditions.map((c) => this.condText(c)).join(r.match === 'any' ? ' 或 ' : '，且 '), r.counts.conditions > r.conditions.length ? `${r.counts.conditions - r.conditions.length} 个网页版条件` : ''].filter(Boolean).join('，另有 ')}
          → ${[...r.actions.map((a) => this.actText(a)), r.counts.actions > r.actions.length ? `${r.counts.actions - r.actions.length} 个网页版动作` : ''].filter(Boolean).join('，')}</div></div>
        <button class="icon-btn" data-up ${i ? '' : 'disabled'} title="上移"><i data-icon="up"></i></button>
        <button class="icon-btn" data-down ${i < this.rules.length - 1 ? '' : 'disabled'} title="下移"><i data-icon="down"></i></button>
        <button class="icon-btn" data-edit ${r.editable ? '' : 'disabled'} title="${r.editable ? '编辑' : '请在网页版中编辑'}"><i data-icon="pen"></i></button>
        <button class="icon-btn" data-del title="删除"><i data-icon="trash"></i></button></div>`).join('') : '<p class="muted">还没有收信规则。</p>'}</div>`;
    renderIcons(pane);
    $('#rl-new').onclick = () => this.edit(null);
    $$('.rl-item', pane).forEach((el) => {
      const id = this.rules.find((r) => String(r.id) === el.dataset.id).id;
      const rule = this.rules.find((r) => r.id === id);
      $('[data-on]', el).onchange = (e) => this.run('setRuleEnabled', { rule_id: id, enabled: e.target.checked }, e.target.checked ? '规则已启用' : '规则已停用');
      $('[data-up]', el).onclick = () => this.run('moveRule', { rule_id: id, step: -1 }, '已调整顺序');
      $('[data-down]', el).onclick = () => this.run('moveRule', { rule_id: id, step: 1 }, '已调整顺序');
      $('[data-edit]', el).onclick = () => this.edit(rule);
      $('[data-del]', el).onclick = async () => {
        if (await confirmDialog('删除收信规则', `删除规则「${rule.name}」后，服务器不再对新邮件执行它。已经处理过的邮件不受影响。`, '删除')) this.run('deleteRule', { rule_id: id }, '规则已删除');
      };
    });
  },
  async run(method, params, done) {
    try { this.rules = (await api(method, params)).rules; this.render(); toast(done); } catch (err) { fail(err); this.load(); }
  },
  condRow(c = { field: 'from', op: 'contains', value: '' }) {
    const opts = (map, v) => Object.entries(map).map(([k, t]) => `<option value="${esc(k)}"${k === v ? ' selected' : ''}>${t}</option>`).join('');
    const size = c.field === 'size' ? +(c.value / 1048576).toFixed(2) || '' : '';
    return `<div class="rl-row" data-cond><select data-field>${opts(this.FIELDS, c.field)}</select>
      <select data-op class="${['attachment', 'size'].includes(c.field) ? 'hidden' : ''}">${opts(this.OPS, c.op)}</select>
      <select data-sop class="${c.field === 'size' ? '' : 'hidden'}">${opts({ '>': '大于', '<': '小于' }, c.op)}</select>
      <input data-value class="${c.field === 'attachment' ? 'hidden' : ''}" value="${esc(c.field === 'size' ? size : c.value || '')}" placeholder="${c.field === 'size' ? 'MB' : '多个值用英文逗号分隔'}">
      <button class="icon-btn" data-rm title="删除这个条件"><i data-icon="close"></i></button></div>`;
  },
  actRow(a = { type: 'move' }) {
    const folders = S.folders.filter((f) => !['drafts', 'sent', 'virus'].includes(f.role));
    const kinds = a.type === 'tag' ? { ...this.ACTIONS, tag: '添加标签' } : this.ACTIONS;  // Kept, not offered.
    return `<div class="rl-row" data-act-row><select data-type>${Object.entries(kinds).map(([k, t]) => `<option value="${k}"${k === a.type ? ' selected' : ''}>${t}</option>`).join('')}</select>
      <select data-folder class="${a.type === 'move' ? '' : 'hidden'}">${folders.map((f) => `<option value="${esc(f.id)}"${f.id === a.folder ? ' selected' : ''}>${esc(f.name)}</option>`).join('')}</select>
      <select data-tag class="${a.type === 'tag' ? '' : 'hidden'}">${TG.list.map((t) => `<option value="${esc(t.id)}"${t.id === a.tag ? ' selected' : ''}>${esc(t.name)}</option>`).join('')}</select>
      <input data-to class="${a.type === 'forward' ? '' : 'hidden'}" value="${esc(a.to || '')}" placeholder="转发到的邮件地址">
      <button class="icon-btn" data-rm title="删除这个动作"><i data-icon="close"></i></button></div>`;
  },
  async edit(rule, draft) {
    const r = draft || rule || { name: '', enabled: true, match: 'all', stop: false, conditions: [{ field: 'subject', op: 'contains', value: '' }], actions: [{ type: 'move', folder: '' }] };
    const body = `<div class="rl-form">
      <label class="pref-row">规则名称 <input id="rl-name" value="${esc(r.name)}" maxlength="60"></label>
      <div class="rl-sec">当邮件满足 <select id="rl-match"><option value="all"${r.match === 'any' ? '' : ' selected'}>以下所有条件</option><option value="any"${r.match === 'any' ? ' selected' : ''}>以下任一条件</option></select> 时</div>
      <div id="rl-conds">${r.conditions.map((c) => this.condRow(c)).join('')}</div><a id="rl-add-cond">+ 添加条件</a>
      <div class="rl-sec">执行以下动作</div>
      <div id="rl-acts">${r.actions.map((a) => this.actRow(a)).join('')}</div><a id="rl-add-act">+ 添加动作</a>
      <label class="check"><input type="checkbox" id="rl-stop"${r.stop ? ' checked' : ''}> 执行后不再执行后面的规则</label>
      <label class="check"><input type="checkbox" id="rl-enabled"${r.enabled ? ' checked' : ''}> 启用这条规则</label></div>`;
    const wire = (d) => {
      renderIcons(d);
      $$('[data-rm]', d).forEach((b) => (b.onclick = () => b.closest('.rl-row').remove()));
      $$('[data-field]', d).forEach((s) => (s.onchange = () => { const row = s.closest('.rl-row'); row.outerHTML = this.condRow({ field: s.value, op: s.value === 'size' ? '>' : 'contains', value: s.value === 'size' ? 1048576 : '' }); wire(d); }));
      $$('[data-type]', d).forEach((s) => (s.onchange = () => { const row = s.closest('.rl-row'); row.outerHTML = this.actRow({ type: s.value }); wire(d); }));
    };
    const value = await promptDialog({
      title: rule ? '编辑收信规则' : '新建收信规则', body, okText: '保存到服务器',
      onOpen: (d) => {
        $('#rl-add-cond', d).onclick = () => { $('#rl-conds', d).insertAdjacentHTML('beforeend', this.condRow()); wire(d); };
        $('#rl-add-act', d).onclick = () => { $('#rl-acts', d).insertAdjacentHTML('beforeend', this.actRow({ type: 'read' })); wire(d); };
        wire(d);
      },
      collect: () => ({
        id: rule ? rule.id : null, name: $('#rl-name').value.trim(), match: $('#rl-match').value, stop: $('#rl-stop').checked, enabled: $('#rl-enabled').checked,
        conditions: $$('#rl-conds [data-cond]').map((row) => {
          const field = $('[data-field]', row).value, v = $('[data-value]', row).value.trim();
          if (field === 'attachment') return { field };
          if (field === 'size') return { field, op: $('[data-sop]', row).value, value: Math.round(parseFloat(v) * 1048576) || 0 };
          return { field, op: $('[data-op]', row).value, value: v.split(/[,，;；]/).map((x) => x.trim()).filter(Boolean).join(',') };
        }),
        actions: $$('#rl-acts [data-act-row]').map((row) => {
          const type = $('[data-type]', row).value;
          return type === 'move' ? { type, folder: $('[data-folder]', row).value } : type === 'tag' ? { type, tag: $('[data-tag]', row).value }
            : type === 'forward' ? { type, to: $('[data-to]', row).value.trim() } : { type };
        }),
      }),
    });
    if (!value) return;
    const { id, ...view } = value;
    try {
      const res = await api('saveRule', { rule: id == null ? view : { ...view, id } });
      this.rules = res.rules; this.render(); toast(rule ? '规则已保存' : '规则已创建');
    } catch (err) { fail(err); this.edit(rule, value); }
  },
};

const AR = {
  data: null,
  local(t) { return t ? t.slice(0, 16).replace(' ', 'T') : ''; },
  server(v) { return v ? v.replace('T', ' ') + ':00' : ''; },
  async load() {
    const pane = $('#st-autoreply');
    pane.innerHTML = '<p class="muted">正在读取自动回复设置…</p>';
    try { this.data = await api('autoReply'); this.render(); } catch (err) { pane.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  },
  render() {
    const d = this.data;
    $('#st-autoreply').innerHTML = `<div class="ar-form">
      <p class="muted small">休假或外出时，服务器会在设定的时间内自动回复来信（与网页版共用）。正文中可写明外出时间和工作代理人。</p>
      <label class="switch-row"><label class="switch"><input type="checkbox" id="ar-on"${d.enabled ? ' checked' : ''}><span></span></label><b>开启休假自动回复</b>
        <span id="ar-state" class="muted small"></span></label>
      <div class="ar-time">从 <input type="datetime-local" id="ar-start" value="${esc(this.local(d.start))}"> 到 <input type="datetime-local" id="ar-end" value="${esc(this.local(d.end))}">
        <span class="muted small">结束时间可不填${d.zone ? ` · 按账号时区 ${esc(d.zone)}` : ''}</span></div>
      <label class="check"><input type="checkbox" id="ar-internal"${d.internalOnly ? ' checked' : ''}> 只对本单位用户发送回复</label>
      <textarea id="ar-text" rows="7" maxlength="4000" placeholder="自动回复内容">${esc(d.text)}</textarea>
      <div class="row-end"><button class="btn ghost" id="ar-default">填入示例文字</button><button class="btn primary" id="ar-save">保存到服务器</button></div></div>`;
    const state = () => {
      const on = $('#ar-on').checked, s = $('#ar-start').value, e = $('#ar-end').value, now = localInput(new Date());
      $('#ar-state').textContent = !on ? '已关闭' : e && e < now ? '时间已过，不会回复' : s > now ? '将于开始时间生效' : '生效中';
    };
    ['ar-on', 'ar-start', 'ar-end'].forEach((id) => ($('#' + id).oninput = state)); state();
    $('#ar-default').onclick = () => {
      const s = $('#ar-start').value || localInput(new Date()), e = $('#ar-end').value;
      $('#ar-text').value = `${s.replace('T', ' ')} 到 ${e ? e.replace('T', ' ') : '（返回日期）'}，我不在办公室。\n\n已收到您的来信。我正在休假中，很抱歉暂无法及时回复您的邮件。如有紧急情况请联系我的同事：【 】`;
    };
    $('#ar-save').onclick = () => this.save();
  },
  async save() {
    const v = { enabled: $('#ar-on').checked, start: this.server($('#ar-start').value), end: this.server($('#ar-end').value),
      internalOnly: $('#ar-internal').checked, text: $('#ar-text').value.trim(), welcomeTip: !!this.data.welcomeTip };
    const who = v.internalOnly ? '本单位的同事' : '所有发信人（包括外部联系人）';
    const text = v.enabled ? `保存后，${v.start ? v.start.slice(0, 16) : '现在'} 起${v.end ? `到 ${v.end.slice(0, 16)}` : '（没有结束时间，需手动关闭）'}，${who}给你发信时会收到这段自动回复。`
      : '保存后，自动回复保持关闭，任何人都不会收到自动回复。';
    if (!(await confirmDialog('保存休假自动回复', text, '保存'))) return;
    try { this.data = await api('setAutoReply', { values: v }); this.render(); toast(v.enabled ? '自动回复已开启' : '自动回复设置已保存'); } catch (err) { fail(err); }
  },
};

// Settings pages: the left list picks one page. Local pages are in index.html; the
// server-backed ones (rules, auto-reply; manage.js adds 黑白名单) load when opened.
// Only 常规 and 写信 hold preferences saved by the 保存 button at the foot.
const SAVED_PAGES = ['general', 'compose'];
function addSettingsPage(name, label, icon) {
  $('#st-nav').insertAdjacentHTML('beforeend', `<button data-tab="${name}"><i data-icon="${icon}"></i>${label}</button>`);
  const pane = document.createElement('div');
  pane.className = 'modal-body st-pane hidden'; pane.dataset.pane = name; pane.id = 'st-' + name;
  $('#st-main').appendChild(pane);
  renderIcons($('#st-nav'));
}
(function settingsTabs() {
  addSettingsPage('rules', '收信规则', 'route');
  addSettingsPage('autoreply', '休假自动回复', 'clock');
  const show = (name) => {
    if (!$(`#st-nav [data-tab="${name}"]`)) name = 'general';
    $$('#st-nav button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
    $$('#settings [data-pane]').forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== name));
    $('#st-foot').classList.toggle('hidden', !SAVED_PAGES.includes(name));
    $('#st-main').scrollTop = 0;
    if (name === 'rules') TG.load().then(() => RL.load());
    if (name === 'autoreply') AR.load();
  };
  $('#st-nav').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) RL.show(b.dataset.tab); });
  RL.show = show;
})();

// ---------------------------------------------------------------- hooks into app.js
const _renderMessage = renderMessage;
renderMessage = function (m) { _renderMessage(m); IV.render(m); };
const _selectMessage = selectMessage;
selectMessage = function (id, opts) { const c = $('#iv-card'); if (c) c.remove(); return _selectMessage(id, opts); };
const _openSettings = openSettings;
// openSettings(page, anchor): a page of the list ('ai', 'tasks', …) and a heading on it to scroll to.
openSettings = function (page, anchor) {
  _openSettings();
  RL.show(typeof page === 'string' ? page : 'general');
  if (anchor) setTimeout(() => { const el = $(anchor); if (el) el.scrollIntoView({ block: 'start' }); }, 30);
};
