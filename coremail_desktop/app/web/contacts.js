'use strict';
/* 鸿雁 — advanced search (filter panel and chips) and the read-only contacts view. Loaded after app.js. */

// ---------------------------------------------------------------- advanced search
// Conditions use the backend's filter names (backend.search_filters). The server
// applies the ones it supports; the reply's `local` list names the rest, which only
// narrow the mail already fetched — their chips say so.
const SF = {
  f: {}, local: [], server: null,
  active() { return { ...this.f }; },
  reset() { this.f = {}; this.local = []; this.renderChips(); },
  set(values) { this.f = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== '' && v != null && v !== false)); },
  open() {
    const p = $('#search-panel');
    const f = this.f;
    $$('#sp-scope button').forEach((b) => b.classList.toggle('on', b.dataset.scope === (f.scope || 'all')));
    $('#sp-from').value = f.from || ''; $('#sp-to').value = f.to || '';
    $('#sp-since').value = f.since || ''; $('#sp-until').value = f.until || '';
    $('#sp-folder').innerHTML = '<option value="">所有文件夹</option>' + S.folders.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
    $('#sp-folder').value = f.folder || '';
    for (const k of ['attachment', 'unread', 'flagged']) $('#sp-' + k).checked = !!f[k];
    const unsupported = Object.keys(SF_LABELS).filter((k) => this.server && !this.server.includes(k));
    $('#sp-note').textContent = this.server == null ? '' : unsupported.length
      ? `带 * 的条件服务器不支持，只筛选已取回的邮件：${unsupported.map((k) => SF_LABELS[k]).join('、')}` : '所有条件都由服务器筛选，结果覆盖整个邮箱。';
    p.classList.remove('hidden');
    $('#btn-search-filters').classList.add('on');
    setTimeout(() => $('#sp-from').focus(), 30);
  },
  close() { $('#search-panel').classList.add('hidden'); $('#btn-search-filters').classList.toggle('on', Object.keys(this.f).length > 0); },
  collect() {
    return { scope: $('#sp-scope .on').dataset.scope === 'subject' ? 'subject' : '', from: $('#sp-from').value.trim(), to: $('#sp-to').value.trim(),
      since: $('#sp-since').value, until: $('#sp-until').value, folder: $('#sp-folder').value,
      attachment: $('#sp-attachment').checked, unread: $('#sp-unread').checked, flagged: $('#sp-flagged').checked, with: this.f.with || '' };
  },
  renderChips(local) {
    if (local) this.local = local;
    const box = $('#search-chips');
    const keys = Object.keys(this.f);
    box.classList.toggle('hidden', !S.search || !keys.length);
    $('#btn-search-filters').classList.toggle('on', keys.length > 0);
    if (!S.search || !keys.length) { box.innerHTML = ''; return; }
    box.innerHTML = keys.map((k) => {
      const localOnly = this.local.includes(k);
      return `<span class="fchip${localOnly ? ' local' : ''}" data-key="${k}" title="${localOnly ? '服务器不支持这个条件，只筛选了已取回的邮件' : '由服务器筛选'}">${esc(chipText(k, this.f[k]))}${localOnly ? '<em>本机</em>' : ''}<button title="去掉这个条件">×</button></span>`;
    }).join('') + '<button class="link-btn" id="chips-edit">修改条件</button><button class="link-btn" id="chips-clear">清除</button>';
  },
};
const SF_LABELS = { from: '发件人', to: '收件人', with: '往来', scope: '仅主题', since: '开始日期', until: '结束日期', folder: '文件夹', attachment: '有附件', unread: '未读', flagged: '星标' };
function chipText(key, value) {
  if (key === 'from' || key === 'to' || key === 'with') return `${SF_LABELS[key]}：${value}`;
  if (key === 'since') return `${value} 起`;
  if (key === 'until') return `至 ${value}`;
  if (key === 'folder') return `文件夹：${(folderById(value) || { name: value }).name}`;
  return SF_LABELS[key] || key;
}
function isoDay(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }

$('#btn-search-filters').onclick = (e) => {
  e.stopPropagation();
  if ($('#search-panel').classList.contains('hidden')) {
    if (SF.server == null) api('searchCapabilities').then((c) => { SF.server = c.server; if (!$('#search-panel').classList.contains('hidden')) SF.open(); }).catch(() => {});
    SF.open();
  } else SF.close();
};
$$('#sp-scope button').forEach((b) => (b.onclick = () => $$('#sp-scope button').forEach((x) => x.classList.toggle('on', x === b))));
$$('#sp-quick button').forEach((b) => (b.onclick = () => {
  const d = new Date(); d.setDate(d.getDate() - Number(b.dataset.days) + 1);
  $('#sp-since').value = isoDay(d); $('#sp-until').value = '';
}));
$('#sp-clear').onclick = () => { SF.set({}); SF.open(); };
function applyPanel() {
  const values = SF.collect();
  if (values.since && values.until && values.since > values.until) { toast('开始日期不能晚于结束日期', { error: true }); return; }
  SF.set(values); SF.close();
  const q = $('#search').value.trim();
  if (q || Object.keys(SF.f).length) runSearch(q); else clearSearch();
}
$('#sp-run').onclick = applyPanel;
$('#search-panel').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); applyPanel(); }
  if (e.key === 'Escape') { e.stopPropagation(); SF.close(); $('#search').focus(); }
});
document.addEventListener('mousedown', (e) => {
  if (!$('#search-panel').classList.contains('hidden') && !e.target.closest('#search-panel, #btn-search-filters, #chips-edit')) SF.close();
});
$('#search-chips').addEventListener('click', (e) => {
  if (e.target.closest('#chips-edit')) { e.stopPropagation(); SF.open(); return; }
  if (e.target.closest('#chips-clear')) { SF.set({}); runSearch($('#search').value); return; }
  const chip = e.target.closest('.fchip');
  if (chip && e.target.tagName === 'BUTTON') { delete SF.f[chip.dataset.key]; runSearch($('#search').value); }
});

// ---------------------------------------------------------------- contacts (通讯录, read-only)
const CT = { tree: [], open: new Set(), dn: null, rows: [], total: 0, title: '', mode: 'dept', person: null, token: 0, personal: [], loaded: false };

async function openContacts() {
  S.local = 'contacts'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
  renderFolders();
  if (!CT.loaded) {
    CT.loaded = true;
    $('#ct-tree').innerHTML = '<div class="skeleton"></div>'.repeat(4);
    await loadDirectory(false);
    loadDirectory(true);  // The cached tree shows at once; a fresh copy follows quietly.
  }
  setTimeout(() => $('#ct-search').focus(), 30);
}
$('#nav-contacts').onclick = openContacts;

async function loadDirectory(refresh) {
  try {
    const [dir, pab] = await Promise.all([api('directory', { refresh }), api('personalContacts', { refresh }).catch(() => ({ rows: [] }))]);
    CT.tree = dir.tree; CT.personal = pab.rows || [];
    if (CT.tree[0] && !CT.open.size) CT.open.add(CT.tree[0].id);
    renderTree();
    $('#ct-tree-note').textContent = dir.at ? `${dir.cached ? '本机缓存' : '已更新'} · ${new Date(dir.at * 1000).toLocaleString('zh-CN', { hour12: false })}` : '';
    if (!CT.dn && CT.mode === 'dept' && !CT.rows.length) showContactsHint();
  } catch (err) {
    if (!CT.tree.length) $('#ct-tree').innerHTML = `<div class="list-foot">${esc(err.message)}<br><br><button class="btn ghost" id="ct-retry">重试</button></div>`;
    else if (refresh) $('#ct-tree-note').textContent = '无法连接服务器，正在显示本机缓存';
    CT.loaded = CT.tree.length > 0;
  }
}
$('#ct-tree').addEventListener('click', (e) => {
  if (e.target.closest('#ct-retry')) { CT.loaded = false; openContacts(); return; }
  const node = e.target.closest('.ct-node'); if (!node) return;
  const id = node.dataset.id;
  if (e.target.closest('.ct-twist')) { CT.open.has(id) ? CT.open.delete(id) : CT.open.add(id); renderTree(); return; }
  if (id === '@personal') { showPersonal(); return; }
  if (node.dataset.kids === '1' && !CT.open.has(id)) CT.open.add(id);
  openDepartment(id, node.dataset.name);
});
$('#ct-refresh').onclick = () => { loadDirectory(true); if (CT.dn) openDepartment(CT.dn, CT.title, true); };

function renderTree() {
  const draw = (nodes, depth) => nodes.map((n) => {
    const kids = n.children && n.children.length;
    const open = CT.open.has(n.id);
    return `<div class="ct-node${CT.mode === 'dept' && CT.dn === n.id ? ' on' : ''}" data-id="${esc(n.id)}" data-name="${esc(n.name)}" data-kids="${kids ? 1 : 0}" role="treeitem" style="padding-left:${8 + depth * 14}px">
      <span class="ct-twist${kids ? '' : ' none'}${open ? ' open' : ''}"><i data-icon="chevron"></i></span><i data-icon="${depth ? 'folder' : 'building'}"></i><span class="ct-name" title="${esc(n.name)}">${esc(n.name || '（未命名）')}</span></div>`
      + (kids && open ? draw(n.children, depth + 1) : '');
  }).join('');
  const personal = CT.personal.length
    ? `<div class="ct-node${CT.mode === 'personal' ? ' on' : ''}" data-id="@personal" style="padding-left:8px"><span class="ct-twist none"></span><i data-icon="user"></i><span class="ct-name">个人通讯录</span><span class="count">${CT.personal.length}</span></div><div class="ct-sep"></div>` : '';
  $('#ct-tree').innerHTML = personal + (CT.tree.length ? draw(CT.tree, 0) : '<div class="list-foot">服务器没有返回组织架构</div>');
  renderIcons($('#ct-tree'));
}

function showContactsHint() {
  $('#ct-title').textContent = '通讯录'; $('#ct-sub').textContent = '';
  $('#ct-list').innerHTML = '<div class="list-foot">选择左侧部门查看成员，或在上方输入姓名、邮箱后回车搜索全公司。</div>';
  renderCard(null);
}

async function openDepartment(dn, name, refresh = false) {
  const token = ++CT.token;
  CT.mode = 'dept'; CT.dn = dn; CT.title = name; CT.person = null;
  renderTree();
  $('#ct-title').textContent = name || '部门'; $('#ct-sub').textContent = '正在读取…';
  if (!refresh) $('#ct-list').innerHTML = '<div class="skeleton"></div>'.repeat(5);
  try {
    let page = await api('members', { dn, refresh });
    if (token !== CT.token) return;
    showMembers(page);
    if (page.cached && !refresh) {  // Instant from the cache, then refreshed in the background.
      page = await api('members', { dn, refresh: true });
      if (token === CT.token) showMembers(page);
    }
  } catch (err) {
    if (token !== CT.token) return;
    if (!CT.rows.length || refresh) $('#ct-list').innerHTML = `<div class="list-foot">${esc(err.message)}</div>`;
    $('#ct-sub').textContent = '';
  }
}
function showMembers(page) {
  CT.rows = page.rows; CT.total = page.total;
  drawMembers();
}
function showPersonal() {
  CT.token++; CT.mode = 'personal'; CT.dn = null; CT.title = '个人通讯录'; CT.rows = CT.personal; CT.total = CT.personal.length; CT.person = null;
  renderTree(); $('#ct-title').textContent = '个人通讯录'; drawMembers();
}

function memberFilter() { return CT.mode === 'search' ? '' : $('#ct-search').value.trim().toLowerCase(); }
function drawMembers() {
  const q = memberFilter();
  const rows = q ? CT.rows.filter((p) => [p.name, p.email, p.title, p.dept].some((v) => String(v || '').toLowerCase().includes(q))) : CT.rows;
  const more = CT.mode === 'dept' && CT.total > CT.rows.length;
  $('#ct-sub').textContent = CT.mode === 'search' ? `${rows.length} 人` : q ? `${rows.length} / ${CT.rows.length} 人` : `${CT.total} 人` + (more ? ` · 显示前 ${CT.rows.length} 人` : '');
  const list = $('#ct-list');
  list.innerHTML = rows.length ? rows.map((p, i) => `<div class="row ct-row${CT.person && CT.person.email === p.email ? ' sel' : ''}" data-i="${CT.rows.indexOf(p)}" role="option">
      ${avatarHtml(p.name, p.email)}
      <div class="main"><div class="line1"><span class="who">${esc(p.name || p.email)}</span>${p.title ? `<span class="tag">${esc(p.title)}</span>` : ''}</div>
      <div class="prev">${esc(p.email)}</div></div></div>`).join('')
    + (more && !q ? '<div class="list-foot">人数较多，只显示一部分。请在上方输入姓名搜索。</div>' : '')
    : `<div class="list-foot">${q ? (CT.mode === 'search' ? '全公司没有找到匹配的人' : '本部门没有匹配的人 · 回车搜索全公司') : '这个部门没有直属成员'}</div>`;
  renderIcons(list);
  if (!CT.person) renderCard(null);  // A new list starts without a selection.
}
$('#ct-list').addEventListener('click', (e) => {
  const row = e.target.closest('.ct-row'); if (!row) return;
  CT.person = CT.rows[+row.dataset.i];
  $$('#ct-list .ct-row').forEach((r) => r.classList.toggle('sel', r === row));
  renderCard(CT.person);
});
$('#ct-list').addEventListener('dblclick', (e) => { const row = e.target.closest('.ct-row'); if (row) writeTo(CT.rows[+row.dataset.i]); });

let ctTimer = null;
$('#ct-search').addEventListener('input', () => {
  clearTimeout(ctTimer);
  if (CT.mode === 'search' && !$('#ct-search').value.trim()) { CT.mode = 'dept'; if (CT.dn) openDepartment(CT.dn, CT.title); else showContactsHint(); return; }
  ctTimer = setTimeout(() => { if (CT.mode !== 'search') drawMembers(); }, 120);
});
$('#ct-search').addEventListener('keydown', async (e) => {
  if (e.key === 'Escape') { e.target.value = ''; e.target.dispatchEvent(new Event('input')); return; }
  if (e.key !== 'Enter') return;
  const q = e.target.value.trim(); if (!q) return;
  const token = ++CT.token;
  $('#ct-title').textContent = '全公司搜索'; $('#ct-sub').textContent = '正在搜索…';
  try {
    const rows = await api('findPeople', { query: q });
    if (token !== CT.token) return;
    CT.mode = 'search'; CT.rows = rows; CT.total = rows.length; CT.person = null; renderTree();
    drawMembers();  // Every server match is shown (the directory also matches pinyin).
    $('#ct-sub').textContent = `${rows.length} 人` + (rows.length >= 20 ? ' · 最多显示 20 人' : '');
  } catch (err) { if (token === CT.token) { $('#ct-sub').textContent = ''; fail(err); } }
});

function renderCard(p) {
  const card = $('#ct-card');
  if (!p) { card.innerHTML = '<div class="empty"><div class="empty-art ct-art"></div><p>选择一位同事查看详情</p><p class="muted small">双击联系人直接写邮件</p></div>'; return; }
  const dept = p.dept || (CT.mode === 'dept' ? CT.title : '');
  card.innerHTML = `<div class="ct-card">
    ${avatarHtml(p.name, p.email, 'xl')}
    <h1>${esc(p.name || p.email)}</h1>
    ${p.title ? `<p class="muted">${esc(p.title)}</p>` : ''}
    <dl class="ct-facts">
      <dt>邮箱</dt><dd class="selectable">${esc(p.email)}</dd>
      ${dept ? `<dt>部门</dt><dd>${esc(dept)}</dd>` : ''}
      ${CT.mode === 'personal' ? '<dt>来源</dt><dd>个人通讯录</dd>' : ''}
    </dl>
    <div class="ct-actions">
      <button class="btn primary" id="ct-write"><i data-icon="pen"></i>写邮件</button>
      <button class="btn ghost" id="ct-mails"><i data-icon="search"></i>查看往来邮件</button>
      <button class="btn ghost" id="ct-copy" title="复制邮箱地址"><i data-icon="copy"></i>复制邮箱</button>
    </div></div>`;
  renderIcons(card);
  $('#ct-write').onclick = () => writeTo(p);
  $('#ct-mails').onclick = () => mailsWith(p);
  $('#ct-copy').onclick = () => {
    const t = document.createElement('textarea'); t.value = p.email; document.body.appendChild(t); t.select();
    const ok = document.execCommand('copy'); t.remove(); toast(ok ? '已复制邮箱地址' : '无法访问剪贴板', { error: !ok });
  };
}
function writeTo(p) {
  const name = String(p.name || '').replace(/["<>]/g, '');
  startCompose('new', null, { to: [name ? `"${name}" <${p.email}>` : p.email] });
}
function mailsWith(p) {
  // 往来邮件: every message between this person and me (webmail's 'dealings').
  SF.set({ with: p.email }); $('#search').value = '';  // The chip names the person.
  runSearch('');
  $('#list-title').textContent = '往来邮件';
}
// Ctrl+F in the contacts view searches people, not mail.
document.addEventListener('keydown', (e) => {
  if (S.local === 'contacts' && e.ctrlKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); e.stopImmediatePropagation(); $('#ct-search').focus(); $('#ct-search').select(); }
}, true);
renderCard(null);
