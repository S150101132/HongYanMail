'use strict';
/* 鸿雁 · v0.7 recipient groups (收件人组). Kept only on this computer (encrypted);
   applying a group only fills 主送/抄送 — nothing is sent by itself, and encryption
   is still decided from the resulting recipients. Uses app.js globals. */

S.groups = [];
async function loadGroups() { try { S.groups = await api('recipientGroups'); } catch (e) { S.groups = []; } return S.groups; }
const FIELD_LABEL = { to: '主送', cc: '抄送' };

// ---------------------------------------------------------------- composer
async function applyGroup(g) {
  try {
    const res = await api('applyRecipientGroups', { group_ids: [g.id], to: chipsValue('to'), cc: chipsValue('cc'), bcc: chipsValue('bcc') });
    setChips('to', res.to); setChips('cc', res.cc);
    if (res.cc.length) $('#row-cc').classList.remove('hidden');
    C.dirty = true; renderEncrypt();
    toast(res.added ? `已从「${g.name}」添加 ${res.added} 位收件人` : `「${g.name}」里的人都已在收件人中`);
  } catch (err) { fail(err); }
}
async function groupMenu(anchor) {
  await loadGroups();
  const r = anchor.getBoundingClientRect();
  const used = S.groups.slice(0, 15).map((g) => ({ label: `${g.name}（${g.members.length}）`, icon: 'contacts', run: () => applyGroup(g) }));
  if (S.groups.length > 15) used.push({ label: `搜索全部 ${S.groups.length} 个组…`, icon: 'search', run: pickGroup });  // V6
  const current = [...chipsValue('to').map((a) => [a, 'to']), ...chipsValue('cc').map((a) => [a, 'cc'])];
  showMenu(r.left, r.bottom + 4, [...used, ...(used.length ? ['-'] : []),
    ...(current.length ? [{ label: '将当前收件人存为组…', icon: 'draft', run: () => editGroup(null, current.map(([a, field]) => ({ ...(parseAddressList(a)[0] || { email: a }), field }))) }] : []),
    { label: '新建收件人组…', icon: 'contacts', run: () => editGroup(null) }]);  // Edit and delete live in 通讯录 › 收件人组.
}
$('#cmp-groups').onclick = (e) => groupMenu(e.currentTarget);
// More groups than the menu shows: type part of a name (or a member) and pick one.
async function pickGroup() {
  const list = (q) => S.groups.filter((g) => !q || g.name.toLowerCase().includes(q) || g.members.some((m) => `${m.name || ''} ${m.email}`.toLowerCase().includes(q)));
  const items = (q) => list(q).map((g) => `<button data-gid="${esc(g.id)}"><i data-icon="contacts"></i><span class="tpl-name">${esc(g.name)}</span><span class="muted small tpl-sub">${g.members.length} 人</span></button>`).join('')
    || '<p class="muted small">没有匹配的组</p>';
  const g = await promptDialog({ title: '选择收件人组',
    body: '<input id="grp-q" type="search" spellcheck="false" placeholder="组名或成员"><div class="pick-list tpl-pick" id="grp-list"></div>',
    onOpen: (d, done) => {
      const draw = () => { $('#grp-list', d).innerHTML = items($('#grp-q', d).value.trim().toLowerCase()); renderIcons($('#grp-list', d));
        $$('[data-gid]', d).forEach((b) => (b.onclick = () => done(S.groups.find((x) => x.id === b.dataset.gid)))); };
      $('#grp-q', d).oninput = draw; draw(); setTimeout(() => $('#grp-q', d).focus(), 0);
    } });
  if (g) applyGroup(g);
}

// ---------------------------------------------------------------- from a message
async function groupFromMessage(m) {
  try {
    const members = await api('messageRecipients', { mid: m.id });
    if (!members.length) { toast('这封邮件没有可以保存的收件人', { error: true }); return; }
    editGroup(null, members);
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- editor
// A promptDialog with a name, a search box (the same suggestions as the composer:
// recent contacts and the company directory) and the member list.
async function editGroup(group, preset) {
  const state = { name: group ? group.name : '', members: (preset || (group ? group.members : [])).map((m) => ({ ...m })) };
  for (;;) {
    const got = await groupDialog(group, state);
    if (!got) return null;
    if (!got.name) { toast('请填写组名称', { error: true }); continue; }
    if (!got.members.length) { toast('请至少添加一位成员', { error: true }); continue; }
    try {
      const saved = await api('saveRecipientGroup', { value: { id: group ? group.id : null, name: got.name, members: got.members } });
      await loadGroups(); renderGroups();
      toast(group ? `收件人组「${saved.name}」已保存` : `已新建收件人组「${saved.name}」（${saved.members.length} 人）`);
      return saved;
    } catch (err) { fail(err); }
  }
}
function groupDialog(group, state) {
  const body = `<input type="text" id="grp-name" maxlength="40" placeholder="组名称，例如：项目周会" value="${esc(state.name)}">
    <div class="grp-add"><input type="text" id="grp-input" spellcheck="false" placeholder="输入姓名或邮箱，从通讯录和最近联系人中选择；也可直接输入地址后回车">
      <select id="grp-field" title="新成员默认为"><option value="to">主送</option><option value="cc">抄送</option></select></div>
    <div class="grp-head muted small"><span id="grp-count"></span><span class="spacer"></span><a data-all="to">全部设为主送</a><a data-all="cc">全部设为抄送</a></div>
    <div id="grp-members" class="grp-members"></div>`;
  return promptDialog({ title: group ? '编辑收件人组' : '新建收件人组', okText: '保存', body,
    onOpen: (d) => {
      const input = $('#grp-input', d);
      const draw = () => drawGroupMembers(state);
      $('#grp-name', d).oninput = (e) => { state.name = e.target.value; };
      const add = (p) => {
        if (!p || !EMAIL_RE.test(p.email)) { toast(`邮箱地址无效：${(p && p.email) || ''}`, { error: true }); return; }
        const same = state.members.find((m) => m.email.toLowerCase() === p.email.toLowerCase());
        if (same) toast(`${same.name || same.email} 已在组里`);
        else state.members.push({ name: p.name || '', email: p.email, field: $('#grp-field', d).value });
        input.value = ''; draw(); input.focus();
        const list = $('#grp-members', d); list.scrollTop = list.scrollHeight;
      };
      let timer = null, token = 0;
      input.addEventListener('input', () => {
        clearTimeout(timer);
        const q = input.value.trim();
        if (!q || /[,;，；]/.test(q)) { hideSuggest(); return; }
        timer = setTimeout(async () => {
          const mine = ++token;
          const items = await api('suggest', { query: q }).catch(() => []);
          if (mine === token && input.value.trim() === q) showSuggest(null, input, items, { pick: add });
        }, 160);
      });
      input.addEventListener('keydown', (e) => {
        const open = !$('#suggest').classList.contains('hidden') && sug.input === input;
        if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
          e.preventDefault(); sug.index = (sug.index + (e.key === 'ArrowDown' ? 1 : -1) + sug.items.length) % sug.items.length; drawSuggest(); return;
        }
        if (e.key === 'Enter' || (e.key === 'Tab' && open)) {
          e.stopPropagation();  // Enter here adds a member; it does not save the dialog.
          if (open && sug.items.length) { e.preventDefault(); pickSuggest(sug.index); return; }
          if (e.key === 'Enter' && input.value.trim()) { e.preventDefault(); hideSuggest(); for (const p of parseAddressList(input.value)) add(p); }
          else if (e.key === 'Enter') e.preventDefault();
        }
        if (e.key === 'Escape' && open) { e.stopPropagation(); hideSuggest(); }
      });
      input.addEventListener('blur', () => setTimeout(() => { if (sug.input === input) hideSuggest(); }, 150));
      $$('[data-all]', d).forEach((a) => (a.onclick = () => { state.members.forEach((m) => (m.field = a.dataset.all)); draw(); }));
      $('#grp-members', d).onclick = (e) => {
        const row = e.target.closest('[data-i]'); if (!row) return;
        const m = state.members[+row.dataset.i];
        if (e.target.closest('[data-field]')) m.field = e.target.closest('[data-field]').dataset.field;
        else if (e.target.closest('.grp-del')) state.members.splice(+row.dataset.i, 1);
        else return;
        draw();
      };
      draw();
      if (state.name) setTimeout(() => input.focus(), 40);
    },
    collect: () => { hideSuggest(); return { name: state.name.trim(), members: state.members }; } });
}
function drawGroupMembers(state) {
  const box = $('#grp-members'); if (!box) return;
  const to = state.members.filter((m) => m.field === 'to').length;
  $('#grp-count').textContent = state.members.length ? `${state.members.length} 人 · 主送 ${to} · 抄送 ${state.members.length - to}` : '';
  box.innerHTML = state.members.length ? state.members.map((m, i) => `<div class="grp-row" data-i="${i}">${avatarHtml(m.name, m.email, 'sm')}
      <div class="t"><div>${esc(m.name || m.email)}</div><div class="muted small">${esc(m.email)}</div></div>
      <span class="seg sm">${['to', 'cc'].map((f) => `<button data-field="${f}" class="${m.field === f ? 'on' : ''}">${FIELD_LABEL[f]}</button>`).join('')}</span>
      <button class="icon-btn grp-del" title="移出这个组">×</button></div>`).join('')
    : '<p class="muted small grp-empty">还没有成员。在上面输入姓名或邮箱添加。</p>';
}

// ---------------------------------------------------------------- 通讯录 › 收件人组
// Listed under 个人通讯录 in the contacts tree (contacts.js); the card shows the members.
function groupCounts(g) { const to = g.members.filter((m) => m.field === 'to').length; return `${g.members.length} 人（主送 ${to}，抄送 ${g.members.length - to}）`; }
function renderGroups() {
  if (S.local !== 'contacts') return;
  renderTree();
  if (CT.mode === 'groups') { drawGroups(); if (CT.group) showGroup(CT.group); }
}
function showGroups() {
  CT.token++; CT.mode = 'groups'; CT.dn = null; CT.person = null; CT.group = null;
  renderTree(); $('#ct-title').textContent = '收件人组'; drawGroups(); renderCard(null);
}
function drawGroups() {
  const q = $('#ct-search').value.trim().toLowerCase();
  const rows = S.groups.filter((g) => !q || g.name.toLowerCase().includes(q) || g.members.some((m) => `${m.name || ''} ${m.email}`.toLowerCase().includes(q)));
  $('#ct-sub').textContent = q ? `${rows.length} / ${S.groups.length} 个组` : `${S.groups.length} 个组 · 只保存在这台电脑，已加密`;
  const list = $('#ct-list');
  list.innerHTML = rows.map((g) => {
    const names = g.members.slice(0, 4).map((m) => m.name || m.email).join('、') + (g.members.length > 4 ? ' 等' : '');
    return `<div class="row ct-grp${CT.group === g.id ? ' sel' : ''}" data-gid="${esc(g.id)}" role="option" title="双击给这组写邮件">
      <span class="avatar grp-icon"><i data-icon="contacts"></i></span>
      <div class="main"><div class="line1"><span class="who">${esc(g.name)}</span></div><div class="prev">${groupCounts(g)} · ${esc(names)}</div></div></div>`;
  }).join('') + (rows.length ? '' : `<div class="list-foot">${q ? '没有匹配的组' : '把常一起写信的人存为一个组，每人可设为主送或抄送；写信时点收件人旁的「收件人组」一键添加。'}</div>`)
    + '<div class="list-foot"><button class="btn ghost sm" id="ct-grp-new">新建收件人组…</button></div>';
  renderIcons(list);
}
function showGroup(id) {
  const g = S.groups.find((x) => x.id === id);
  CT.group = g ? g.id : null;
  $$('#ct-list .ct-grp').forEach((r) => r.classList.toggle('sel', r.dataset.gid === CT.group));
  if (!g) { renderCard(null); return; }
  const card = $('#ct-card');
  card.innerHTML = `<div class="ct-card">
    <span class="avatar xl grp-icon"><i data-icon="contacts"></i></span>
    <h1>${esc(g.name)}</h1><p class="muted">${groupCounts(g)}</p>
    <div class="grp-members grp-card-members">${g.members.map((m) => `<div class="grp-row">${avatarHtml(m.name, m.email, 'sm')}
      <div class="t"><div>${esc(m.name || m.email)}</div><div class="muted small">${esc(m.email)}</div></div><span class="tag">${FIELD_LABEL[m.field] || '主送'}</span></div>`).join('')}</div>
    <div class="ct-actions">
      <button class="btn primary" id="grp-write"><i data-icon="pen"></i>写邮件</button>
      <button class="btn ghost" id="grp-edit">编辑</button><button class="btn ghost danger" id="grp-delete">删除</button>
    </div></div>`;
  renderIcons(card);
  $('#grp-write').onclick = () => writeToGroup(g);
  $('#grp-edit').onclick = () => editGroup(g);
  $('#grp-delete').onclick = async () => {
    if (!(await confirmDialog('删除收件人组', `确定删除收件人组「${g.name}」吗？已写好的邮件不受影响。`, '删除'))) return;
    try { S.groups = await api('deleteRecipientGroup', { group_id: g.id }); CT.group = null; renderGroups(); renderCard(null); } catch (err) { fail(err); }
  };
}
function writeToGroup(g) {
  if (!g) return;
  const addr = (m) => { const name = String(m.name || '').replace(/["<>]/g, ''); return name ? `"${name}" <${m.email}>` : m.email; };
  startCompose('new', null, { to: g.members.filter((m) => m.field !== 'cc').map(addr), cc: g.members.filter((m) => m.field === 'cc').map(addr) });
}