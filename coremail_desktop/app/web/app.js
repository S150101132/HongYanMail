'use strict';
/* 鸿雁 · 工作邮箱 — web interface. Talks to Python only through the Qt bridge. */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------- bridge
let bridge = null;
let seq = 0;
const pending = new Map();
const handlers = {};
function api(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = 'r' + (++seq);
    pending.set(id, { resolve, reject });
    bridge.call(id, method, JSON.stringify(params));
  });
}
function on(name, fn) { handlers[name] = fn; }

// ---------------------------------------------------------------- state
const S = {
  profile: null, prefs: {}, folders: [], folderId: null, local: null,
  rows: [], total: null, more: false, loading: false, listToken: 0,
  selected: null, message: null, filter: 'all', search: null,
  allowRemote: new Set(), drafts: [],
};

// ---------------------------------------------------------------- helpers
const PALETTE = ['#2563eb', '#7c3aed', '#db2777', '#ea580c', '#059669', '#0891b2', '#4f46e5', '#b45309', '#0d9488', '#9333ea'];
function colorFor(key) { let h = 0; for (const c of String(key || '?')) h = (h * 31 + c.charCodeAt(0)) >>> 0; return PALETTE[h % PALETTE.length]; }
function initials(name, email) {
  const n = String(name || email || '?').replace(/["'<>]/g, '').trim();
  if (/^[一-龥]/.test(n)) return n.length === 3 ? n.slice(-2) : n.slice(0, 2);  // 张晓明→晓明, 研发团队→研发
  const parts = n.split(/[\s._@-]+/).filter(Boolean);
  if (parts[0] && /^[A-Za-z0-9]{1,3}$/.test(parts[0]) && parts[0] === parts[0].toUpperCase()) return parts[0];  // IT 服务台→IT
  return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}
function avatar(el, name, email) { el.textContent = initials(name, email); el.style.background = colorFor(email || name); }
function avatarHtml(name, email, cls = 'xs') { return `<span class="avatar ${cls}" style="background:${colorFor(email || name)}">${esc(initials(name, email))}</span>`; }
function parseTs(row) { return row.ts ? new Date(row.ts * 1000) : new Date(String(row.date).replace(' ', 'T')); }
function sameDay(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
function shortDate(d) {
  if (isNaN(d)) return '';
  const now = new Date();
  const hm = d.toTimeString().slice(0, 5);
  if (sameDay(d, now)) return hm;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, y)) return '昨天';
  const diff = (now - d) / 86400000;
  if (diff < 6) return '周' + '日一二三四五六'[d.getDay()];
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`;
}
function longDate(d) {
  if (isNaN(d)) return '';
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日 周${'日一二三四五六'[d.getDay()]} ${d.toTimeString().slice(0, 5)}`;
}
function groupOf(d) {
  const now = new Date();
  if (sameDay(d, now)) return '今天';
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, y)) return '昨天';
  const startWeek = new Date(now); startWeek.setHours(0, 0, 0, 0); startWeek.setDate(now.getDate() - ((now.getDay() + 6) % 7));
  if (d >= startWeek) return '本周';
  const lastWeek = new Date(startWeek); lastWeek.setDate(startWeek.getDate() - 7);
  if (d >= lastWeek) return '上周';
  if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return '本月';
  return `${d.getFullYear()}年${d.getMonth() + 1}月`;
}
function size(n) {
  if (n == null) return '';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB';
  return (n / 1048576).toFixed(1) + ' MB';
}
function extColor(name) {
  const e = (name.split('.').pop() || '').toLowerCase();
  const map = { pdf: '#d92d20', doc: '#2563eb', docx: '#2563eb', xls: '#16a34a', xlsx: '#16a34a', csv: '#16a34a', ppt: '#ea580c', pptx: '#ea580c',
    zip: '#7c3aed', rar: '#7c3aed', '7z': '#7c3aed', png: '#0891b2', jpg: '#0891b2', jpeg: '#0891b2', gif: '#0891b2', txt: '#64748b' };
  return [map[e] || '#64748b', (e || 'FILE').slice(0, 4).toUpperCase()];
}
const EMAIL_RE = /^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$/;
function parseAddressList(text) {
  const out = [];
  for (const part of String(text).split(/[,;，；\n]+/)) {
    const t = part.trim(); if (!t) continue;
    const m = t.match(/^(.*?)<([^>]+)>$/);
    if (m) out.push({ name: m[1].replace(/["']/g, '').trim(), email: m[2].trim() });
    else out.push({ name: '', email: t });
  }
  return out;
}

// ---------------------------------------------------------------- toasts, menus, prompts
function toast(text, opts = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (opts.error ? ' err' : '');
  el.innerHTML = `<span>${esc(text)}</span>`;
  if (opts.action) {
    const b = document.createElement('button'); b.textContent = opts.action.label;
    b.onclick = () => { opts.action.run(); el.remove(); }; el.appendChild(b);
  }
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), opts.ms || (opts.error ? 6000 : 3500));
}
function fail(err) { toast((err && err.message) || '操作未完成，请稍后重试。', { error: true }); }

function showMenu(x, y, items) {
  const m = $('#menu');
  m.innerHTML = '';
  for (const it of items) {
    if (it === '-') { m.appendChild(document.createElement('hr')); continue; }
    const b = document.createElement('button');
    b.innerHTML = `${it.icon ? `<i data-icon="${it.icon}"></i>` : '<i></i>'}<span>${esc(it.label)}</span>`;
    b.onclick = () => { hideMenu(); it.run(); };
    m.appendChild(b);
  }
  renderIcons(m);
  m.classList.remove('hidden');
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 8) + 'px';
  m.style.top = Math.min(y, innerHeight - r.height - 8) + 'px';
}
function hideMenu() { $('#menu').classList.add('hidden'); }

function promptDialog({ title, text = '', body = '', okText = '确定', onOpen, collect }) {
  return new Promise((resolve) => {
    const d = $('#prompt');
    $('#prompt-title').textContent = title;
    $('#prompt-text').textContent = text;
    $('#prompt-text').classList.toggle('hidden', !text);
    $('#prompt-body').innerHTML = body;
    $('#prompt-ok').textContent = okText;
    $('#prompt-ok').classList.toggle('hidden', !collect);
    renderIcons(d);
    d.classList.remove('hidden');
    const done = (value) => { d.classList.add('hidden'); d.onkeydown = null; resolve(value); };
    $$('[data-close]', d).forEach((b) => (b.onclick = () => done(null)));
    $('#prompt-ok').onclick = () => done(collect ? collect() : true);
    d.onkeydown = (e) => { if (e.key === 'Enter' && collect && e.target.tagName === 'INPUT') { e.preventDefault(); done(collect()); } };
    if (onOpen) onOpen(d, done);
    const first = $('input', d); if (first) setTimeout(() => first.focus(), 30);
  });
}
const confirmDialog = (title, text, okText = '确定') => promptDialog({ title, text, okText, collect: () => true });

// ---------------------------------------------------------------- theme
function applyTheme(theme, density) {
  const dark = theme === 'dark' || (theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  if (density) document.documentElement.dataset.density = density;
  try { localStorage.setItem('hy-theme', theme); if (density) localStorage.setItem('hy-density', density); } catch (e) { /* optional */ }
  if (S.message) renderBody(S.message);
}
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyTheme(S.prefs.theme || 'system'));

// ---------------------------------------------------------------- login
async function showLogin(message) {
  $('#app').classList.add('hidden');
  $('#composer').classList.add('hidden');
  $('#login').classList.remove('hidden');
  $('#boot').classList.add('hidden');
  const saved = await api('savedLogin').catch(() => ({}));
  $('#login-server').value = saved.server || 'https://newmail.inspur.com/coremail/';
  if (!$('#login-user').value) $('#login-user').value = saved.username || '';
  $('#login-error').textContent = message || '';
  ($('#login-user').value ? $('#login-pass') : $('#login-user')).focus();
}
$('#login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = $('#login-submit');
  btn.disabled = true; btn.textContent = '正在登录…'; $('#login-error').textContent = '';
  try {
    const profile = await api('login', { server: $('#login-server').value, username: $('#login-user').value,
      password: $('#login-pass').value, remember: $('#login-remember').checked });
    $('#login-pass').value = '';
    await enterApp(profile);
  } catch (err) {
    $('#login-error').textContent = err.message;
  } finally { btn.disabled = false; btn.textContent = '登录'; }
});

// ---------------------------------------------------------------- app
async function enterApp(profile) {
  S.profile = profile; S.prefs = profile.prefs;
  applyTheme(S.prefs.theme, S.prefs.density);
  $('#login').classList.add('hidden'); $('#boot').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#me-name').textContent = profile.name || profile.account;
  $('#me-mail').textContent = profile.account;
  avatar($('#me-avatar'), profile.name, profile.account);
  const cached = await api('cachedView', { folder_id: '1' }).catch(() => null);
  if (cached && cached.folders.length) { S.folders = cached.folders; renderFolders(); }
  const inbox = (S.folders.find((f) => f.role === 'inbox') || { id: '1' }).id;
  selectFolder(inbox);
  api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(fail);
  api('startSync', { seconds: S.prefs.pollSeconds });
  refreshDraftCount();
}

const ROLE_ICON = { inbox: 'inbox', drafts: 'drafts', sent: 'sent', trash: 'trash', spam: 'spam', virus: 'virus' };
function renderFolders() {
  const nav = $('#folder-list');
  nav.innerHTML = S.folders.map((f) => `<a class="folder${f.id === S.folderId && !S.local ? ' on' : ''}" data-id="${esc(f.id)}" title="${esc(f.name)}">
      <i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i><span class="fname">${esc(f.name)}</span>
      <span class="count">${f.unread && f.role !== 'sent' && f.role !== 'trash' ? f.unread : ''}</span></a>`).join('');
  renderIcons(nav);
  $$('.folder', nav).forEach((a) => {
    a.onclick = () => selectFolder(a.dataset.id);
    a.ondragover = (e) => { if (e.dataTransfer.types.includes('text/x-hongyan-mid')) { e.preventDefault(); a.classList.add('drop'); } };
    a.ondragleave = () => a.classList.remove('drop');
    a.ondrop = (e) => { e.preventDefault(); a.classList.remove('drop'); moveMessage(e.dataTransfer.getData('text/x-hongyan-mid'), a.dataset.id); };
  });
  $('#nav-drafts').classList.toggle('on', S.local === 'drafts');
  const current = folderById(S.folderId);
  if (current && !S.local && !S.search) $('#list-title').textContent = current.name;
  const inbox = S.folders.find((f) => f.role === 'inbox');
  api('setUnread', { count: (inbox && inbox.unread) || 0 });
}
const folderById = (id) => S.folders.find((f) => f.id === String(id));

function selectFolder(id) {
  S.folderId = String(id); S.local = null; S.search = null; $('#search').value = '';
  S.rows = []; S.total = null; S.more = false;
  renderFolders();
  const f = folderById(id);
  $('#list-title').textContent = f ? f.name : '邮件';
  loadList(true);
}

async function loadList(reset) {
  const token = ++S.listToken;
  const fid = S.folderId;
  if (reset) {
    const cached = await api('cachedView', { folder_id: fid, limit: 50 }).catch(() => null);
    if (token !== S.listToken) return;
    if (cached && cached.rows.length) { S.rows = cached.rows; renderList(); }
    else { $('#mail-list').innerHTML = '<div class="skeleton"></div>'.repeat(6); }
  }
  S.loading = true;
  try {
    const offset = reset ? 0 : S.rows.length;
    const page = await api('messages', { folder_id: fid, offset, limit: 50 });
    if (token !== S.listToken) return;
    S.rows = reset ? page.rows : dedupe(S.rows.concat(page.rows));
    S.total = page.total; S.more = page.more;
    $('#sync-bar').classList.add('hidden');
    renderList();
    if (reset) api('prefetch', { ids: page.rows.slice(0, 15).map((r) => r.id) }).catch(() => {});
  } catch (err) {
    if (token !== S.listToken) return;
    if (S.rows.length) { $('#sync-bar').textContent = '无法连接服务器，正在显示本机缓存。' ; $('#sync-bar').classList.remove('hidden'); }
    else $('#mail-list').innerHTML = `<div class="list-foot">${esc(err.message)}<br><br><button class="btn ghost" onclick="loadList(true)">重试</button></div>`;
  } finally { if (token === S.listToken) S.loading = false; }
}
function dedupe(rows) { const seen = new Set(); return rows.filter((r) => !seen.has(r.id) && seen.add(r.id)); }

function visibleRows() {
  if (S.filter === 'unread') return S.rows.filter((r) => !r.flags.read);
  if (S.filter === 'flagged') return S.rows.filter((r) => r.flags.flagged);
  return S.rows;
}

function rowHtml(r) {
  const d = parseTs(r);
  const sentLike = ['sent', 'drafts'].includes((folderById(r.fid) || {}).role);
  const who = sentLike ? '致 ' + (parseAddressList(r.to).map((p) => p.name || p.email).join('、') || '（无收件人）') : (r.fromName || r.fromEmail || '（未知发件人）');
  const folderTag = S.search && folderById(r.fid) && r.fid !== S.folderId ? `<span class="tag">${esc(folderById(r.fid).name)}</span>` : '';
  const marks = (r.flags.attached ? '<i data-icon="clip"></i>' : '') + (r.flags.flagged ? '<i class="star" data-icon="star"></i>' : '') +
    (r.flags.encrypted ? '<i data-icon="lock"></i>' : '') + (r.flags.replied ? '<i data-icon="reply"></i>' : '');
  return `<div class="row${r.flags.read ? '' : ' unread'}${r.id === S.selected ? ' sel' : ''}" data-id="${esc(r.id)}" draggable="true" role="option">
    ${avatarHtml(r.fromName, sentLike ? r.to : r.fromEmail)}
    <div class="main">
      <div class="line1"><span class="who">${esc(who)}</span>${folderTag}<span class="marks">${marks}</span><span class="when">${esc(shortDate(d))}</span></div>
      <div class="subj">${esc(r.subject || '（无主题）')}</div>
      ${r.preview ? `<div class="prev">${esc(r.preview)}</div>` : ''}
    </div></div>`;
}

function renderList() {
  const list = $('#mail-list');
  const rows = visibleRows();
  let html = '';
  let group = null;
  for (const r of rows) {
    const g = S.search ? null : groupOf(parseTs(r));
    if (g && g !== group) { group = g; html += `<div class="group-head">${g}</div>`; }
    html += rowHtml(r);
  }
  if (!rows.length) {
    html = `<div class="list-foot">${S.search ? '没有找到匹配的邮件' : S.filter === 'unread' ? '没有未读邮件 🎉' : S.filter === 'flagged' ? '没有星标邮件' : '这个文件夹是空的'}</div>`;
  } else if (S.more && !S.search) {
    html += `<div class="list-foot" id="load-more">正在加载更多…</div>`;
  } else {
    html += `<div class="list-foot">${S.search ? `共 ${rows.length} 封` : '已全部加载'}</div>`;
  }
  const top = list.scrollTop;
  list.innerHTML = html;
  list.scrollTop = top;
  renderIcons(list);
  const unread = S.rows.filter((r) => !r.flags.read).length;
  $('#list-sub').textContent = S.search ? '' : (S.total != null ? `${S.total} 封` : '') ;
  if (S.filter !== 'all' && S.more && rows.length < 15 && !S.loading && !S.search) loadList(false);
  void unread;
}

$('#mail-list').addEventListener('scroll', () => {
  const l = $('#mail-list');
  if (S.more && !S.loading && !S.search && l.scrollTop + l.clientHeight > l.scrollHeight - 400) loadList(false);
});
$('#mail-list').addEventListener('click', (e) => {
  const row = e.target.closest('.row'); if (!row) return;
  if (S.local === 'drafts') { openLocalDraft(row.dataset.id); return; }
  selectMessage(row.dataset.id);
});
$('#mail-list').addEventListener('dblclick', (e) => {
  const row = e.target.closest('.row'); if (row && !S.local) startCompose('reply', row.dataset.id);
});
$('#mail-list').addEventListener('dragstart', (e) => {
  const row = e.target.closest('.row'); if (!row || S.local) return;
  e.dataTransfer.setData('text/x-hongyan-mid', row.dataset.id); e.dataTransfer.effectAllowed = 'move';
});
$('#mail-list').addEventListener('contextmenu', (e) => {
  const row = e.target.closest('.row'); if (!row) return;
  e.preventDefault();
  if (S.local === 'drafts') {
    showMenu(e.clientX, e.clientY, [{ label: '打开草稿', icon: 'pen', run: () => openLocalDraft(row.dataset.id) },
      { label: '删除草稿', icon: 'trash', run: () => deleteLocalDraft(row.dataset.id) }]);
    return;
  }
  const r = S.rows.find((x) => x.id === row.dataset.id); if (!r) return;
  showMenu(e.clientX, e.clientY, [
    { label: '回复', icon: 'reply', run: () => startCompose('reply', r.id) },
    { label: '全部回复', icon: 'replyall', run: () => startCompose('reply_all', r.id) },
    { label: '转发', icon: 'forward', run: () => startCompose('forward', r.id) },
    '-',
    { label: r.flags.read ? '标为未读' : '标为已读', icon: 'mail', run: () => setFlags(r.id, { read: !r.flags.read }) },
    { label: r.flags.flagged ? '取消星标' : '添加星标', icon: 'star', run: () => setFlags(r.id, { flagged: !r.flags.flagged }) },
    { label: '移动到…', icon: 'folder', run: () => pickFolder(r.id) },
    '-',
    { label: '删除', icon: 'trash', run: () => deleteMessage(r.id) },
  ]);
});

$$('#filter-seg button').forEach((b) => (b.onclick = () => {
  $$('#filter-seg button').forEach((x) => x.classList.toggle('on', x === b));
  S.filter = b.dataset.filter; renderList();
}));
$('#btn-refresh').onclick = () => { api('syncNow'); if (!S.local) loadList(true); api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(() => {}); };

// ---------------------------------------------------------------- search
let searchToken = 0;
async function runSearch(q) {
  q = q.trim();
  if (!q) { clearSearch(); return; }
  const token = ++searchToken;
  S.search = q; S.local = null; S.filter = 'all';
  $$('#filter-seg button').forEach((x) => x.classList.toggle('on', x.dataset.filter === 'all'));
  $('#list-title').textContent = '搜索结果';
  S.rows = await api('searchLocal', { query: q }).catch(() => []);
  if (token !== searchToken) return;
  renderList();
  $('#list-sub').textContent = '本机结果 · 正在搜索服务器…';
  try {
    const remote = await api('searchServer', { query: q });
    if (token !== searchToken) return;
    S.rows = dedupe(S.rows.concat(remote.rows)).sort((a, b) => (b.ts || 0) - (a.ts || 0));
    renderList();
    $('#list-sub').textContent = `服务器 ${remote.total} 封匹配`;
  } catch (err) {
    if (token === searchToken) $('#list-sub').textContent = '服务器搜索不可用，显示本机结果';
  }
}
function clearSearch() { if (S.search) { searchToken++; selectFolder(S.folderId); } }
$('#search').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runSearch(e.target.value);
  if (e.key === 'Escape') { e.target.value = ''; clearSearch(); e.target.blur(); }
});
let searchTimer = null;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  if (!e.target.value.trim()) { clearSearch(); return; }
  searchTimer = setTimeout(async () => {  // Instant local results while typing.
    const q = e.target.value.trim(); if (!q) return;
    const token = ++searchToken;
    S.search = q; S.rows = await api('searchLocal', { query: q }).catch(() => []);
    if (token !== searchToken) return;
    $('#list-title').textContent = '搜索结果'; renderList(); $('#list-sub').textContent = '本机结果 · 回车搜索服务器';
  }, 220);
});

// ---------------------------------------------------------------- reading
let openToken = 0;
async function selectMessage(id, opts = {}) {
  S.selected = id;
  $$('#mail-list .row').forEach((r) => r.classList.toggle('sel', r.dataset.id === id));
  const row = $(`#mail-list .row[data-id="${CSS.escape(id)}"]`);
  if (row && !opts.noScroll) row.scrollIntoView({ block: 'nearest' });
  const token = ++openToken;
  const summary = S.rows.find((r) => r.id === id);
  $('#reader-empty').classList.add('hidden'); $('#reader-body').classList.remove('hidden');
  if (summary) {
    $('#r-subject').textContent = summary.subject || '（无主题）';
    $('#r-from-name').textContent = summary.fromName; $('#r-from-mail').textContent = summary.fromEmail ? `<${summary.fromEmail}>` : '';
    avatar($('#r-avatar'), summary.fromName, summary.fromEmail);
    $('#r-date').textContent = longDate(parseTs(summary)); $('#r-to').textContent = '';
    $('#r-attachments').classList.add('hidden'); $('#r-banners').innerHTML = '';
    writeFrame('<div style="color:#98a2b3">正在加载…</div>');
  }
  api('allowRemoteImages', { allow: S.allowRemote.has(id) });
  try {
    const m = await api('openMessage', { mid: id });
    if (token !== openToken) return;
    S.message = m;
    renderMessage(m);
    if (m.markedRead && summary) {
      summary.flags.read = true;
      row && row.classList.remove('unread');
      const f = folderById(summary.fid); if (f && f.unread > 0) { f.unread--; renderFolders(); }
    }
  } catch (err) {
    if (token !== openToken) return;
    S.message = null;
    writeFrame(`<div style="color:#d92d20">${esc(err.message)}</div>`);
  }
}

function people(list) { return list.map((p) => `<a title="${esc(p.email)}" data-mailto="${esc(p.email)}">${esc(p.name || p.email)}</a>`).join('、'); }
function renderMessage(m) {
  $('#r-subject').textContent = m.subject || '（无主题）';
  const from = m.from[0] || { name: '', email: '' };
  $('#r-from-name').textContent = from.name || from.email; $('#r-from-mail').textContent = from.email ? `<${from.email}>` : '';
  avatar($('#r-avatar'), from.name, from.email);
  const d = m.date ? new Date(String(m.date).replace(' ', 'T')) : null;
  $('#r-date').textContent = d && !isNaN(d) ? longDate(d) : (m.date || '');
  $('#r-to').innerHTML = (m.to.length ? '收件人：' + people(m.to) : '') + (m.cc.length ? '<br>抄送：' + people(m.cc) : '');
  $$('#r-to a').forEach((a) => (a.onclick = () => startCompose('new', null, { to: [a.dataset.mailto] })));
  $$('.reader-toolbar [data-act="flag"]').forEach((b) => b.classList.toggle('on', !!m.flags.flagged));
  // Banners
  const banners = [];
  if (m.encryption === 'locked') banners.push(`<div class="banner"><i data-icon="lock"></i><span>这是一封证书加密邮件，解锁个人证书后即可阅读。</span><span class="spacer"></span><button class="btn primary" id="b-unlock">解锁证书</button></div>`);
  if (m.encryption === 'decrypted') banners.push(`<div class="banner info"><i data-icon="lock"></i><span>此邮件已使用你的证书在本机解密。</span></div>`);
  if (m.hasRemoteImages && !S.allowRemote.has(m.id)) banners.push(`<div class="banner"><i data-icon="image"></i><span>为保护隐私，已阻止远程图片。</span><span class="spacer"></span><button class="btn ghost" id="b-images">显示图片</button></div>`);
  $('#r-banners').innerHTML = banners.join('');
  renderIcons($('#r-banners'));
  const unlock = $('#b-unlock'); if (unlock) unlock.onclick = unlockCertificate;
  const images = $('#b-images'); if (images) images.onclick = async () => { S.allowRemote.add(m.id); await api('allowRemoteImages', { allow: true }); renderMessage(m); };
  // Attachments
  const atts = $('#r-attachments');
  if (m.attachments.length) {
    atts.innerHTML = m.attachments.map((a) => { const [c, e] = extColor(a.name); return `<div class="att" title="${esc(a.name)}">
      <span class="ext" style="background:${c}">${esc(e)}</span><div class="meta"><div class="name">${esc(a.name)}</div><div class="size">${size(a.size)}</div></div>
      <button class="icon-btn" data-open="${esc(a.part)}" title="打开"><i data-icon="open"></i></button>
      <button class="icon-btn" data-save="${esc(a.part)}" title="另存为"><i data-icon="download"></i></button></div>`; }).join('') +
      (m.attachments.length > 1 ? '<button class="btn ghost" id="save-all"><i data-icon="download"></i>全部保存</button>' : '');
    renderIcons(atts);
    atts.classList.remove('hidden');
    $$('[data-open]', atts).forEach((b) => (b.onclick = () => api('openAttachment', { mid: m.id, part: b.dataset.open }).catch(fail)));
    $$('[data-save]', atts).forEach((b) => (b.onclick = () => api('saveAttachment', { mid: m.id, part: b.dataset.save }).then((ok) => ok && toast('附件已保存')).catch(fail)));
    const all = $('#save-all'); if (all) all.onclick = () => api('saveAllAttachments', { mid: m.id }).then((n) => n && toast(`已保存 ${n} 个附件`)).catch(fail);
  } else { atts.classList.add('hidden'); atts.innerHTML = ''; }
  renderBody(m);
  $('.reader-scroll').scrollTop = 0;
}

function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<>"']+)/g, '<a href="$1">$1</a>');
}
function renderBody(m) {
  if (m.html) writeFrame(m.html, { remote: S.allowRemote.has(m.id), paper: true });
  else writeFrame(`<div style="white-space:pre-wrap">${linkify(m.text || '')}</div>`, {});
}
function writeFrame(html, opts = {}) {
  const frame = $('#r-frame');
  const dark = document.documentElement.dataset.theme === 'dark';
  const paper = opts.paper && dark;
  const fg = dark && !paper ? '#e6e9ef' : '#1d2433';
  const csp = `default-src 'none'; style-src 'unsafe-inline'; img-src data: ${opts.remote ? 'https: http:' : ''}; font-src data:`;
  frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">
    <style>html{background:${paper ? '#fff' : 'transparent'};${paper ? 'border-radius:10px;' : ''}}
    body{margin:0;padding:${paper ? '18px 20px' : '0'};color:${fg};font:14.5px/1.7 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif;word-wrap:break-word;overflow-wrap:anywhere;overflow-x:auto}
    img{max-width:100%;height:auto} a{color:${dark && !paper ? '#7aa2ff' : '#2563eb'}} pre{white-space:pre-wrap}
    blockquote{margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d0d5dd;color:${dark && !paper ? '#b3bac7' : '#475467'}}
    ::selection{background:#bfd3ff}</style></head><body>${html}</body></html>`;
}
$('#r-frame').addEventListener('load', () => {
  const frame = $('#r-frame');
  const doc = frame.contentDocument; if (!doc) return;
  let last = 0;
  const fit = () => {
    const h = Math.max(120, doc.body.scrollHeight + 24);
    if (Math.abs(h - last) > 2) { last = h; frame.style.height = h + 'px'; }
  };
  fit();
  $$('img', doc).forEach((img) => img.addEventListener('load', fit));
  try { new ResizeObserver(() => requestAnimationFrame(fit)).observe(doc.body); } catch (e) { /* optional */ }
  doc.addEventListener('keydown', (e) => handleKey(e));
});

$('#reader-body').addEventListener('click', (e) => {
  const b = e.target.closest('[data-act]'); if (!b || !S.message) return;
  const m = S.message;
  ({
    reply: () => startCompose('reply', m.id), reply_all: () => startCompose('reply_all', m.id), forward: () => startCompose('forward', m.id),
    delete: () => deleteMessage(m.id), move: () => pickFolder(m.id), unread: () => setFlags(m.id, { read: false }),
    flag: () => setFlags(m.id, { flagged: !m.flags.flagged }), find: openFind,
  }[b.dataset.act] || (() => {}))();
});

async function setFlags(id, flags) {
  const r = S.rows.find((x) => x.id === id);
  const before = r ? { ...r.flags } : null;
  if (r) { Object.assign(r.flags, flags); renderList(); }
  if (S.message && S.message.id === id) { Object.assign(S.message.flags, flags); $$('.reader-toolbar [data-act="flag"]').forEach((b) => b.classList.toggle('on', !!S.message.flags.flagged)); }
  try {
    await api('setFlags', { mid: id, read: flags.read ?? null, flagged: flags.flagged ?? null });
    if ('read' in flags && r && before && before.read !== flags.read) {
      const f = folderById(r.fid); if (f) { f.unread = Math.max(0, (f.unread || 0) + (flags.read ? -1 : 1)); renderFolders(); }
    }
    if (flags.read === false) toast('已标为未读');
  } catch (err) { if (r && before) { r.flags = before; renderList(); } fail(err); }
}

function neighbour(id) {
  const rows = visibleRows(); const i = rows.findIndex((r) => r.id === id);
  return rows[i + 1] || rows[i - 1] || null;
}
function removeRow(id) {
  const next = S.selected === id ? neighbour(id) : null;
  S.rows = S.rows.filter((r) => r.id !== id);
  renderList();
  if (S.selected === id) {
    if (next) selectMessage(next.id); else { S.selected = null; S.message = null; $('#reader-body').classList.add('hidden'); $('#reader-empty').classList.remove('hidden'); }
  }
}
async function deleteMessage(id) {
  const r = S.rows.find((x) => x.id === id);
  const trash = S.folders.find((f) => f.role === 'trash');
  if (r && trash && r.fid === trash.id) { toast('邮件已在已删除文件夹中。为避免误删，本应用不提供彻底删除。'); return; }
  removeRow(id);
  try {
    await api('delete', { mid: id });
    toast('已移到「已删除」', { action: trash && r ? { label: '撤销', run: () => moveMessage(id, r.fid, true) } : null });
  } catch (err) { fail(err); loadList(true); }
}
async function moveMessage(id, folderId, undo) {
  const r = S.rows.find((x) => x.id === id);
  if (r && r.fid === String(folderId)) return;
  if (r && !S.search) removeRow(id);
  try {
    await api('move', { mid: id, folder_id: String(folderId) });
    if (undo) { toast('已撤销'); loadList(true); }
    else toast(`已移动到「${(folderById(folderId) || {}).name || '文件夹'}」`);
    api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(() => {});
  } catch (err) { fail(err); loadList(true); }
}
async function pickFolder(id) {
  const r = S.rows.find((x) => x.id === id) || {};
  await promptDialog({ title: '移动到文件夹',
    body: '<div class="pick-list">' + S.folders.filter((f) => f.id !== r.fid && f.role !== 'virus').map((f) =>
      `<button data-fid="${esc(f.id)}"><i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i>${esc(f.name)}</button>`).join('') + '</div>',
    onOpen: (d, done) => $$('[data-fid]', d).forEach((b) => (b.onclick = () => { done(true); moveMessage(id, b.dataset.fid); })) });
}

async function unlockCertificate() {
  const got = await promptDialog({ title: '解锁个人证书', text: '输入证书密码，然后选择 .pfx 证书文件。',
    body: '<input type="password" id="cert-pw" placeholder="证书密码"><label class="check" style="display:flex;gap:8px;margin-top:10px"><input type="checkbox" id="cert-remember" checked> 记住证书（Windows 加密保存，下次自动解锁）</label>',
    okText: '选择证书文件…', collect: () => ({ password: $('#cert-pw').value, remember: $('#cert-remember').checked }) });
  if (got == null) return;
  try {
    const res = await api('unlockCertificate', got);
    if (!res) return;
    S.profile.certificate = res;
    toast('证书已解锁');
    if (S.message) selectMessage(S.message.id, { noScroll: true });
  } catch (err) { fail(err); }
}

// find in body
function openFind() { $('#find-bar').classList.remove('hidden'); $('#find-input').focus(); $('#find-input').select(); }
function closeFind() { $('#find-bar').classList.add('hidden'); api('findText', { text: '' }); }
$('#find-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') api('findText', { text: e.target.value, backward: e.shiftKey });
  if (e.key === 'Escape') closeFind();
});
$('#find-next').onclick = () => api('findText', { text: $('#find-input').value });
$('#find-prev').onclick = () => api('findText', { text: $('#find-input').value, backward: true });
$('#find-close').onclick = closeFind;

// ---------------------------------------------------------------- local drafts
async function refreshDraftCount() {
  try { S.drafts = await api('listDrafts'); $('#draft-count').textContent = S.drafts.length || ''; } catch (e) { /* local storage optional */ }
}
$('#nav-drafts').onclick = async () => {
  S.local = 'drafts'; S.search = null; S.listToken++;
  renderFolders();
  $('#list-title').textContent = '本机草稿'; $('#list-sub').textContent = '仅保存在这台电脑，已加密';
  await refreshDraftCount();
  const list = $('#mail-list');
  list.innerHTML = S.drafts.length ? S.drafts.map((d) => `<div class="row" data-id="${esc(d.local_id)}">
      <span class="avatar xs" style="background:#64748b"><i data-icon="draft"></i></span>
      <div class="main"><div class="line1"><span class="who">${esc(d.subject || '（无主题）')}</span><span class="when">${esc(d.modified)}</span></div>
      <div class="prev">点击继续编辑 · 右键删除</div></div></div>`).join('') : '<div class="list-foot">没有本机草稿</div>';
  renderIcons(list);
};
async function openLocalDraft(id) { try { openComposer(await api('restoreDraft', { local_id: id })); } catch (err) { fail(err); } }
async function deleteLocalDraft(id) {
  if (!(await confirmDialog('删除草稿', '确定删除这份本机草稿吗？此操作无法撤销。', '删除'))) return;
  await api('deleteDraft', { local_id: id }).catch(fail); $('#nav-drafts').click();
}

// ---------------------------------------------------------------- composer
const C = { draft: null, dirty: false, timer: null, fields: {} };
async function startCompose(mode, mid, extra = {}) {
  if (C.draft && !$('#composer').classList.contains('hidden')) {
    if (C.dirty) await saveDraft(true);
  }
  try {
    const draft = await api('compose', { mode, mid });
    Object.assign(draft, extra);
    openComposer(draft);
  } catch (err) { fail(err); }
}

function openComposer(draft) {
  C.draft = draft; C.dirty = false; C.encrypt = draft.encrypt === true ? true : draft.encrypt === false ? false : null;
  const box = $('#composer');
  box.classList.remove('hidden');
  $('#cmp-title').textContent = { new: '写邮件', reply: '回复', reply_all: '全部回复', forward: '转发' }[draft.mode] || '写邮件';
  $('#cmp-status').textContent = '';
  const ids = S.profile.identities || [S.profile.account];
  $('#cmp-from').innerHTML = ids.map((i) => `<option${i === draft.from ? ' selected' : ''}>${esc(i)}</option>`).join('');
  $('#row-from').classList.toggle('hidden', ids.length < 2);
  for (const field of ['to', 'cc', 'bcc']) setChips(field, draft[field] || []);
  $('#row-cc').classList.toggle('hidden', !(draft.cc || []).length);
  $('#row-bcc').classList.toggle('hidden', !(draft.bcc || []).length);
  $('#cmp-subject').value = draft.subject || '';
  $('#cmp-editor').innerHTML = draft.html || '<p><br></p>';
  renderAtts();
  renderEncrypt();
  setTimeout(() => {
    if (!(draft.to || []).length) $('.chips[data-field="to"] input').focus();
    else { const ed = $('#cmp-editor'); ed.focus(); const r = document.createRange(); r.setStart(ed, 0); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
  }, 30);
  clearInterval(C.timer);
  C.timer = setInterval(() => { if (C.dirty) saveDraft(true); }, 20000);
}

function chipEl(addr) {
  const p = typeof addr === 'string' ? parseAddressList(addr)[0] || { name: '', email: addr } : addr;
  const el = document.createElement('span');
  el.className = 'chip' + (EMAIL_RE.test(p.email) ? '' : ' bad');
  el.dataset.email = p.email; el.dataset.name = p.name || '';
  el.title = p.email;
  el.innerHTML = `<span>${esc(p.name ? `${p.name}` : p.email)}</span><button tabindex="-1" title="移除">×</button>`;
  el.querySelector('button').onclick = () => { el.remove(); C.dirty = true; renderEncrypt(); };
  return el;
}
function setChips(field, list) {
  const box = $(`.chips[data-field="${field}"]`);
  box.innerHTML = '';
  for (const a of list) box.appendChild(chipEl(a));
  const input = document.createElement('input');
  input.type = 'text'; input.spellcheck = false;
  box.appendChild(input);
  wireChipInput(box, input);
  box.onclick = (e) => { if (e.target === box) input.focus(); };
}
function addChip(box, input, addr) {
  box.insertBefore(chipEl(addr), input); input.value = ''; C.dirty = true; hideSuggest(); renderEncrypt();
}

// ---- encryption: colleagues encrypted by default, any external recipient turns it off
function allRecipients() { return ['to', 'cc', 'bcc'].flatMap((f) => $$(`.chips[data-field="${f}"] .chip`).map((c) => c.dataset.email)); }
function isInternal(addr) { return (S.profile.internalDomains || []).includes(String(addr).split('@').pop().toLowerCase()); }
function autoEncrypt() { const r = allRecipients(); return r.length > 0 && r.every(isInternal); }
function encryptOn() { return C.encrypt === null || C.encrypt === undefined ? autoEncrypt() : C.encrypt; }
function renderEncrypt() {
  if (!C.draft) return;
  const on = encryptOn();
  const external = allRecipients().filter((a) => !isInternal(a));
  $('#cmp-encrypt').classList.toggle('on', on);
  $('#cmp-encrypt span').textContent = on ? '加密发送' : '不加密';
  $('#cmp-enc-hint').textContent = C.encrypt === null || C.encrypt === undefined
    ? (on ? '收件人均为同事，自动加密' : external.length ? '含外部收件人，默认不加密' : '')
    : '已手动' + (on ? '开启' : '关闭');
}
$('#cmp-encrypt').onclick = () => { C.encrypt = !encryptOn(); C.dirty = true; renderEncrypt(); };
function commitInput(box, input) {
  const list = parseAddressList(input.value);
  for (const p of list) addChip(box, input, p);
  input.value = '';
}
function chipsValue(field) {
  const box = $(`.chips[data-field="${field}"]`);
  const input = $('input', box);
  if (input.value.trim()) commitInput(box, input);
  return $$('.chip', box).map((c) => (c.dataset.name ? `"${c.dataset.name.replace(/"/g, '')}" <${c.dataset.email}>` : c.dataset.email));
}

let sug = { items: [], index: 0, box: null, input: null, token: 0 };
function wireChipInput(box, input) {
  let timer = null;
  input.addEventListener('input', () => {
    C.dirty = true;
    if (/[,;，；]$/.test(input.value)) { commitInput(box, input); return; }
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { hideSuggest(); return; }
    timer = setTimeout(async () => {
      const token = ++sug.token;
      const items = await api('suggest', { query: q }).catch(() => []);
      if (token !== sug.token || input.value.trim() !== q) return;
      showSuggest(box, input, items);
    }, 160);
  });
  input.addEventListener('keydown', (e) => {
    const open = !$('#suggest').classList.contains('hidden') && sug.input === input;
    if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault(); sug.index = (sug.index + (e.key === 'ArrowDown' ? 1 : -1) + sug.items.length) % sug.items.length; drawSuggest(); return;
    }
    if ((e.key === 'Enter' || e.key === 'Tab') && open && sug.items.length) {
      e.preventDefault(); addChip(box, input, sug.items[sug.index]); return;
    }
    if (e.key === 'Enter' && input.value.trim()) { e.preventDefault(); commitInput(box, input); return; }
    if (e.key === 'Backspace' && !input.value) { const chips = $$('.chip', box); if (chips.length) { chips[chips.length - 1].remove(); C.dirty = true; } }
    if (e.key === 'Escape' && open) { e.stopPropagation(); hideSuggest(); }
  });
  input.addEventListener('blur', () => setTimeout(() => { if (input.value.trim() && sug.input !== input) commitInput(box, input); }, 150));
  input.addEventListener('paste', (e) => {
    const text = e.clipboardData.getData('text');
    if (/[,;\n]/.test(text)) { e.preventDefault(); for (const p of parseAddressList(text)) addChip(box, input, p); }
  });
}
function showSuggest(box, input, items) {
  sug.items = items; sug.index = 0; sug.box = box; sug.input = input;
  if (!items.length) { hideSuggest(); return; }
  const r = input.getBoundingClientRect();
  const el = $('#suggest');
  el.style.left = r.left + 'px'; el.style.top = r.bottom + 4 + 'px';
  el.classList.remove('hidden');
  drawSuggest();
}
function drawSuggest() {
  const el = $('#suggest');
  el.innerHTML = sug.items.map((p, i) => `<div class="sug${i === sug.index ? ' on' : ''}" data-i="${i}">${avatarHtml(p.name, p.email, 'sm')}
    <div class="t"><div>${esc(p.name || p.email)}</div><div class="muted small">${esc(p.email)}</div></div>
    <span class="src">${p.source === 'directory' ? '通讯录' : '最近联系'}</span></div>`).join('');
  $$('.sug', el).forEach((d) => (d.onmousedown = (e) => { e.preventDefault(); addChip(sug.box, sug.input, sug.items[+d.dataset.i]); sug.input.focus(); }));
}
function hideSuggest() { $('#suggest').classList.add('hidden'); sug.input = null; }

function renderAtts() {
  const atts = C.draft.attachments || [];
  $('#cmp-atts').innerHTML = atts.map((a, i) => `<span class="cmp-att"><i data-icon="file"></i>${esc(a.name)} <span class="muted">${size(a.size)}</span><button data-i="${i}" title="移除">×</button></span>`).join('');
  renderIcons($('#cmp-atts'));
  $$('#cmp-atts button').forEach((b) => (b.onclick = () => { atts.splice(+b.dataset.i, 1); C.dirty = true; renderAtts(); }));
  const total = atts.reduce((s, a) => s + (a.size || 0), 0);
  $('#cmp-size').textContent = atts.length ? `${atts.length} 个附件 · ${size(total)}` : '';
}
function addAttachments(list) {
  const atts = C.draft.attachments || (C.draft.attachments = []);
  for (const a of list) {
    if (a.size > 10 * 1048576) { toast(`「${a.name}」超过 10 MB，未添加`, { error: true }); continue; }
    atts.push(a);
  }
  const total = atts.reduce((s, a) => s + (a.size || 0), 0);
  if (total > 20 * 1048576 || atts.length > 20) toast('附件总计不能超过 20 MB / 20 个，发送前请删减', { error: true });
  C.dirty = true; renderAtts();
}
$('#cmp-attach').onclick = async () => { try { addAttachments(await api('pickFiles')); } catch (err) { fail(err); } };
function readFile(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve({ name: file.name, type: file.type || 'application/octet-stream', size: file.size, data: String(fr.result).split(',')[1] || '' });
    fr.onerror = reject; fr.readAsDataURL(file);
  });
}
const composer = $('#composer');
composer.addEventListener('dragover', (e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); $('#cmp-drop').classList.remove('hidden'); } });
composer.addEventListener('dragleave', (e) => { if (!composer.contains(e.relatedTarget)) $('#cmp-drop').classList.add('hidden'); });
composer.addEventListener('drop', async (e) => {
  if (!e.dataTransfer.files.length) return;
  e.preventDefault(); $('#cmp-drop').classList.add('hidden');
  addAttachments(await Promise.all(Array.from(e.dataTransfer.files).map(readFile)));
});
$('#cmp-editor').addEventListener('paste', async (e) => {
  const files = Array.from(e.clipboardData.files || []);
  if (files.length) { e.preventDefault(); addAttachments(await Promise.all(files.map(readFile))); toast('粘贴的图片已作为附件添加'); }
});
$('#cmp-editor').addEventListener('input', () => { C.dirty = true; });
$('#cmp-subject').addEventListener('input', () => { C.dirty = true; });
$$('.cmp-toggles a').forEach((a) => (a.onclick = () => { $('#row-' + a.dataset.show).classList.remove('hidden'); $(`.chips[data-field="${a.dataset.show}"] input`).focus(); }));

function execIn(editor, cmd, value) { editor.focus(); document.execCommand(cmd, false, value); }
async function insertLink(editor) {
  const sel = getSelection(); const range = sel.rangeCount ? sel.getRangeAt(0).cloneRange() : null;
  const url = await promptDialog({ title: '插入链接', body: '<input type="text" id="link-url" placeholder="https://">', collect: () => $('#link-url').value.trim() });
  if (!url) return;
  editor.focus(); if (range) { sel.removeAllRanges(); sel.addRange(range); }
  const href = /^(https?:|mailto:)/i.test(url) ? url : 'https://' + url;
  if (range && !range.collapsed) document.execCommand('createLink', false, href);
  else document.execCommand('insertHTML', false, `<a href="${esc(href)}">${esc(url)}</a>`);
}
function wireToolbar(bar, editor) {
  $$('button[data-cmd]', bar).forEach((b) => {
    b.onmousedown = (e) => e.preventDefault();
    b.onclick = () => (b.dataset.cmd === 'link' ? insertLink(editor) : execIn(editor, b.dataset.cmd));
  });
}
wireToolbar($('#cmp-toolbar'), $('#cmp-editor'));
wireToolbar($('#sig-toolbar'), $('#pref-signature'));
$('#fmt-size').onchange = (e) => { if (e.target.value) execIn($('#cmp-editor'), 'fontSize', e.target.value); e.target.value = ''; };
$('#fmt-color').oninput = (e) => execIn($('#cmp-editor'), 'foreColor', e.target.value);
$('#fmt-hilite').oninput = (e) => execIn($('#cmp-editor'), 'hiliteColor', e.target.value);
$('#sig-color').oninput = (e) => execIn($('#pref-signature'), 'foreColor', e.target.value);

function collectDraft() {
  const value = { ...C.draft, from: $('#cmp-from').value || C.draft.from, to: chipsValue('to'), cc: chipsValue('cc'), bcc: chipsValue('bcc'),
    subject: $('#cmp-subject').value, html: $('#cmp-editor').innerHTML };
  value.encrypt = encryptOn();
  return value;
}
async function saveDraft(quiet) {
  if (!C.draft) return;
  try {
    const res = await api('saveDraft', { value: collectDraft() });
    C.draft.revision = res.revision; C.dirty = false;
    $('#cmp-status').textContent = '已保存 ' + new Date().toTimeString().slice(0, 5);
    if (!quiet) toast('草稿已保存到本机');
    refreshDraftCount();
  } catch (err) { $('#cmp-status').textContent = '草稿保存失败'; if (!quiet) fail(err); }
}
async function sendMail() {
  const value = collectDraft();
  if (!value.to.length && !value.cc.length && !value.bcc.length) { toast('请至少填写一个收件人', { error: true }); $('.chips[data-field="to"] input').focus(); return; }
  const bad = [...value.to, ...value.cc, ...value.bcc].map((a) => parseAddressList(a)[0]).filter((p) => !EMAIL_RE.test(p.email));
  if (bad.length) { toast(`收件人地址无效：${bad[0].email}`, { error: true }); return; }
  if (!value.subject.trim() && !(await confirmDialog('没有主题', '这封邮件没有主题，仍要发送吗？', '仍然发送'))) return;
  const text = $('#cmp-editor').innerText;
  if (/附件|attach/i.test(text.split('原始邮件')[0]) && !(value.attachments || []).length &&
      !(await confirmDialog('忘记添加附件？', '正文提到了“附件”，但还没有添加附件。仍要发送吗？', '仍然发送'))) return;
  const btn = $('#cmp-send'); btn.disabled = true; btn.innerHTML = '<i data-icon="send"></i>发送中…'; renderIcons(btn);
  $('#cmp-status').textContent = (value.attachments || []).length ? '正在上传附件并发送…' : '正在发送…';
  try {
    const res = await api('send', { value });
    C.draft.revision = res.revision;
    if (res.state === 'NeedsCertificates') {
      $('#cmp-status').textContent = '部分收件人没有加密证书';
      const plain = await promptDialog({ title: '无法加密发送',
        text: '以下收件人在公司通讯录中没有加密证书，这封邮件无法加密发给他们：',
        body: '<ul class="miss-list">' + res.missing.map((a) => `<li>${esc(a)}</li>`).join('') + '</ul>' +
          '<p class="muted small">你可以移除这些收件人后加密发送，或改为不加密发送这一封。</p>',
        okText: '改为不加密发送', collect: () => true });
      if (plain) { C.encrypt = false; renderEncrypt(); setTimeout(sendMail, 0); }
      return;
    }
    if (res.state === 'Accepted') {
      closeComposer(true); toast(value.encrypt ? '加密邮件已发送' : '邮件已发送');
      refreshDraftCount();
      const sent = S.folders.find((f) => f.role === 'sent'); if (sent && S.folderId === sent.id) loadList(true);
    } else if (res.state === 'Unknown') {
      $('#cmp-status').textContent = '发送结果待确认';
      toast('发送结果不确定：请先在“已发送”中核对，本应用不会自动重发。草稿已保留。', { error: true, ms: 9000 });
    } else {
      $('#cmp-status').textContent = '发送失败，草稿已保留';
      fail({ message: res.failureMessage || '发送失败，草稿已保存在本机，可修改后重试。' });
    }
  } catch (err) { $('#cmp-status').textContent = '发送失败，草稿已保留'; fail(err); }
  finally { btn.disabled = false; btn.innerHTML = '<i data-icon="send"></i>发送'; renderIcons(btn); }
}
function closeComposer(sent) {
  clearInterval(C.timer);
  $('#composer').classList.add('hidden'); $('#composer').classList.remove('max'); hideSuggest();
  if (!sent && C.dirty) saveDraft(true).then(() => toast('已保存到本机草稿'));
  if (sent) C.draft = null;
}
$('#cmp-send').onclick = sendMail;
$('#cmp-save').onclick = () => saveDraft(false);
$('#cmp-close').onclick = () => closeComposer(false);
$('#cmp-max').onclick = () => $('#composer').classList.toggle('max');
$('#cmp-discard').onclick = async () => {
  if (!(await confirmDialog('丢弃草稿', '确定丢弃这封邮件吗？本机草稿也会删除。', '丢弃'))) return;
  const id = C.draft && C.draft.localId;
  C.dirty = false; closeComposer(true);
  if (id) api('deleteDraft', { local_id: id }).then(refreshDraftCount).catch(() => {});
};
$('#btn-compose').onclick = () => startCompose('new');
composer.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); sendMail(); }
  else if (e.ctrlKey && (e.key === 's' || e.key === 'S')) { e.preventDefault(); saveDraft(false); }
  else if (e.key === 'Escape' && $('#suggest').classList.contains('hidden')) { e.preventDefault(); closeComposer(false); }
  e.stopPropagation();
});

// ---------------------------------------------------------------- settings
function openSettings() {
  const p = S.prefs;
  const d = $('#settings'); d.classList.remove('hidden'); renderIcons(d);
  $$('#theme-seg button').forEach((b) => b.classList.toggle('on', b.dataset.theme === p.theme));
  $$('#density-seg button').forEach((b) => b.classList.toggle('on', b.dataset.density === p.density));
  $('#pref-poll').value = String(p.pollSeconds);
  $('#pref-notify').checked = !!p.notify; $('#pref-markread').checked = !!p.markReadOnOpen;
  $('#pref-signature').innerHTML = p.signature || '';
  $('#settings-account').textContent = `${S.profile.name || ''} ${S.profile.account}`;
  renderCertStatus();
  api('runtimeStatus').then((r) => { $('#runtime-note').classList.toggle('hidden', r.sandboxed); $('#data-dir').textContent = '本机数据（已加密）：' + r.dataDir; }).catch(() => {});
  api('certificateStatus').then((c) => { S.profile.certificate = c; renderCertStatus(); }).catch(() => {});
}
function renderCertStatus() {
  const c = S.profile.certificate || {};
  $('#cert-status').textContent = c.unlocked ? `个人证书已解锁 · 有效期至 ${c.expires}${c.remembered ? ' · 已记住' : ''}` : '个人证书未解锁（不影响加密发送，只影响阅读加密邮件）';
  $('#btn-cert-forget').classList.toggle('hidden', !c.remembered && !c.unlocked);
}
$('#btn-cert-forget').onclick = async () => { S.profile.certificate = await api('forgetCertificate').catch(() => S.profile.certificate); renderCertStatus(); toast('已忘记证书'); };
$$('#theme-seg button').forEach((b) => (b.onclick = () => { $$('#theme-seg button').forEach((x) => x.classList.toggle('on', x === b)); applyTheme(b.dataset.theme); }));
$$('#density-seg button').forEach((b) => (b.onclick = () => { $$('#density-seg button').forEach((x) => x.classList.toggle('on', x === b)); document.documentElement.dataset.density = b.dataset.density; }));
$('#btn-account').onclick = openSettings;
$$('#settings [data-close]').forEach((b) => (b.onclick = () => { $('#settings').classList.add('hidden'); applyTheme(S.prefs.theme, S.prefs.density); }));
$('#btn-save-settings').onclick = async () => {
  const values = { theme: $('#theme-seg .on').dataset.theme, density: $('#density-seg .on').dataset.density, pollSeconds: +$('#pref-poll').value,
    notify: $('#pref-notify').checked, markReadOnOpen: $('#pref-markread').checked, signature: $('#pref-signature').innerHTML.replace(/^(<br>)+$/, '') };
  try {
    S.prefs = await api('setPrefs', { values });
    applyTheme(S.prefs.theme, S.prefs.density);
    api('startSync', { seconds: S.prefs.pollSeconds });
    $('#settings').classList.add('hidden'); toast('设置已保存');
  } catch (err) { fail(err); }
};
$('#btn-cert').onclick = () => { $('#settings').classList.add('hidden'); unlockCertificate(); };
$('#btn-logout').onclick = async () => {
  if (!(await confirmDialog('退出登录', '退出后需要重新输入密码。本机缓存保持加密保存。', '退出登录'))) return;
  $('#settings').classList.add('hidden');
  await api('logout', { forget: true }).catch(() => {});
  location.reload();
};

// ---------------------------------------------------------------- keyboard
function handleKey(e) {
  const t = e.target;
  if (!$('#prompt').classList.contains('hidden') || !$('#settings').classList.contains('hidden')) {
    if (e.key === 'Escape') { $$('#prompt [data-close], #settings [data-close]').forEach((b) => !b.closest('.hidden') && b.click()); }
    return;
  }
  if (e.key === 'Escape') { hideMenu(); closeFind(); }
  if (e.ctrlKey && (e.key === 'n' || e.key === 'N')) { e.preventDefault(); startCompose('new'); return; }
  if (e.key === 'F5') { e.preventDefault(); $('#btn-refresh').click(); return; }
  if ((e.ctrlKey && (e.key === 'f' || e.key === 'F')) || (e.key === '/' && !isTyping(t))) { e.preventDefault(); $('#search').focus(); $('#search').select(); return; }
  if (isTyping(t) || e.ctrlKey || e.altKey || $('#app').classList.contains('hidden')) return;
  const m = S.message;
  const rows = visibleRows();
  const i = rows.findIndex((r) => r.id === S.selected);
  const k = e.key;
  if (k === 'j' || k === 'ArrowDown') { e.preventDefault(); const n = rows[Math.min(rows.length - 1, i + 1)]; if (n) selectMessage(n.id); }
  else if (k === 'k' || k === 'ArrowUp') { e.preventDefault(); const n = rows[Math.max(0, i - 1)]; if (n) selectMessage(n.id); }
  else if (k === 'c') startCompose('new');
  else if (!m) return;
  else if (k === 'r') startCompose('reply', m.id);
  else if (k === 'a') startCompose('reply_all', m.id);
  else if (k === 'f') startCompose('forward', m.id);
  else if (k === 'Delete' || k === '#') deleteMessage(m.id);
  else if (k === 'u') setFlags(m.id, { read: false });
  else if (k === 's') setFlags(m.id, { flagged: !m.flags.flagged });
  else if (k === 'v') pickFolder(m.id);
}
function isTyping(t) { return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable); }
document.addEventListener('keydown', handleKey);
document.addEventListener('click', (e) => { if (!e.target.closest('#menu')) hideMenu(); if (!e.target.closest('#suggest') && !e.target.closest('.chips')) hideSuggest(); });
document.addEventListener('contextmenu', (e) => { if (!isTyping(e.target) && !e.target.closest('.reader-scroll')) e.preventDefault(); });

// ---------------------------------------------------------------- push events from Python
on('synced', (v) => {
  S.folders = v.folders; renderFolders();
  if (!S.local && !S.search && S.folderId === v.inbox && !S.loading) {
    const known = new Set(S.rows.map((r) => r.id));
    const fresh = v.rows.filter((r) => !known.has(r.id));
    // Merge without losing pages the user already scrolled to.
    const byId = new Map(v.rows.map((r) => [r.id, r]));
    S.rows = S.rows.map((r) => byId.get(r.id) || r);
    if (fresh.length) S.rows = dedupe(fresh.concat(S.rows)).sort((a, b) => (b.ts || 0) - (a.ts || 0));
    if (v.total != null) S.total = v.total;
    renderList();
  }
  $('#sync-bar').classList.add('hidden');
});
on('syncError', (err) => {
  if (err && err.code === 'NETWORK_ERROR') { $('#sync-bar').textContent = '网络不可用，稍后自动重试。正在显示本机缓存。'; $('#sync-bar').classList.remove('hidden'); }
});
on('authRequired', () => showLogin('会话已失效，请重新登录。'));
on('compose', () => startCompose('new'));
on('mailto', (addr) => startCompose('new', null, { to: [decodeURIComponent(addr || '')] }));
on('openMessage', async (id) => {
  if (!id) return;
  const inbox = S.folders.find((f) => f.role === 'inbox');
  if (inbox && (S.folderId !== inbox.id || S.search || S.local)) { selectFolder(inbox.id); await sleep(400); }
  selectMessage(id);
});

// ---------------------------------------------------------------- boot
(function boot() {
  try { applyTheme(localStorage.getItem('hy-theme') || 'system', localStorage.getItem('hy-density') || 'comfortable'); } catch (e) { applyTheme('system'); }
  renderIcons();
  new QWebChannel(qt.webChannelTransport, async (channel) => {
    bridge = channel.objects.bridge;
    bridge.reply.connect((id, ok, payload) => {
      const p = pending.get(id); if (!p) return;
      pending.delete(id);
      const value = payload ? JSON.parse(payload) : null;
      ok ? p.resolve(value) : p.reject(value || { message: '操作未完成' });
    });
    bridge.event.connect((name, payload) => { const fn = handlers[name]; if (fn) fn(payload ? JSON.parse(payload) : null); });
    const saved = await api('savedLogin').catch(() => ({}));
    if (saved.remember) {
      try { await enterApp(await api('autoLogin')); return; }
      catch (err) { showLogin(err.message); return; }
    }
    showLogin();
  });
})();
