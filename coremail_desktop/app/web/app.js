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
  allowRemote: new Set(), drafts: [], items: [], multi: new Set(), anchor: null,
  shared: [], openBoxes: new Set(), open: new Set(), thread: new Map(), threadLoading: new Set(), counts: new Map(), sentSync: 0, templates: [],
};
// A pop-out reader window loads this page as index.html#reader=<mid>.
const READER_MID = (/^#reader=(.+)$/.exec(location.hash) || [])[1] ? decodeURIComponent(location.hash.slice(8)) : null;

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
  applyWidths();
  renderThreadToggle();
  $('#login').classList.add('hidden'); $('#boot').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#me-name').textContent = profile.name || profile.account;
  $('#me-mail').textContent = profile.account;
  avatar($('#me-avatar'), profile.name, profile.account);
  const cached = await api('cachedView', { folder_id: '1' }).catch(() => null);
  if (cached && cached.folders.length) { S.folders = cached.folders; renderFolders(); }
  const inbox = (S.folders.find((f) => f.role === 'inbox') || { id: '1' }).id;
  selectFolder(inbox);
  api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(fail);
  loadShared();
  api('startSync', { seconds: S.prefs.pollSeconds });
  refreshDraftCount();
  refreshOutbox();
  setTimeout(refreshScheduled, 1500);
  loadTemplates();
}

// ---------------------------------------------------------------- pop-out reader window
async function enterReader(mid) {
  document.documentElement.classList.add('reader-only');
  const profile = await api('profile').catch(() => null);
  $('#boot').classList.add('hidden');
  if (!profile || !profile.account) { $('#login').classList.add('hidden'); api('closeWindow'); return; }
  S.profile = profile; S.prefs = profile.prefs;
  applyTheme(S.prefs.theme, S.prefs.density);
  $('#app').classList.remove('hidden');
  [S.folders, S.shared] = await Promise.all([api('folders', { refresh: false }).catch(() => []), api('sharedMailboxes', { refresh: false }).catch(() => [])]);
  loadTemplates();
  selectMessage(mid);
}
function popOut(id) { if (id && S.local !== 'drafts' && S.local !== 'outbox') api('openReader', { mid: id }).catch(fail); }
// Changes made in a pop-out reach the main window's list (read state, star, deletion).
function notifyMain(value) { if (READER_MID) api('notifyMain', { name: 'mailChanged', value }).catch(() => {}); }
async function readerAct(act, m) {
  if (m.imported && ['flag', 'unread', 'delete', 'move'].includes(act)) { toast('这是从本机文件打开的邮件，只能阅读、回复和转发'); return true; }
  if (act === 'flag' || act === 'unread') {
    const flags = act === 'flag' ? { flagged: !m.flags.flagged } : { read: false };
    const before = { ...m.flags };
    Object.assign(m.flags, flags); renderFlagButton();
    try {
      await api('setFlags', { mid: m.id, read: flags.read ?? null, flagged: flags.flagged ?? null });
      notifyMain({ kind: 'flags', id: m.id, flags });
      if (flags.read === false) toast('已标为未读');
    } catch (err) { m.flags = before; renderFlagButton(); fail(err); }
    return true;
  }
  const leave = async (call, value) => {
    try { await call; notifyMain(value); api('closeWindow'); } catch (err) { fail(err); }
  };
  if (act === 'delete') {
    const trash = foldersFor(m.fid).find((f) => f.role === 'trash');
    if (trash && m.fid === trash.id) { toast('邮件已在已删除文件夹中。为避免误删，本应用不提供彻底删除。'); return true; }
    leave(api('delete', { mid: m.id }), { kind: 'removed', id: m.id, from: m.fid, deleted: true });
    return true;
  }
  if (act === 'move') {
    await promptDialog({ title: '移动到文件夹',
      body: '<div class="pick-list">' + foldersFor(m.fid).filter((f) => f.id !== m.fid && f.role !== 'virus').map((f) =>
        `<button data-fid="${esc(f.id)}"><i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i>${esc(f.name)}</button>`).join('') + '</div>',
      onOpen: (d, done) => $$('[data-fid]', d).forEach((b) => (b.onclick = () => {
        done(true); leave(api('move', { mid: m.id, folder_id: b.dataset.fid }), { kind: 'removed', id: m.id, from: m.fid, to: b.dataset.fid });
      })) });
    return true;
  }
  return false;
}
on('mailChanged', (v) => {
  const r = rowOf(v.id);
  if (v.kind === 'flags') {
    if (r) {
      const was = r.flags.read;
      Object.assign(r.flags, v.flags); renderList();
      const f = folderById(r.fid);
      if ('read' in v.flags && was !== v.flags.read && f) { f.unread = Math.max(0, (f.unread || 0) + (v.flags.read ? -1 : 1)); renderFolders(); }
    }
    if (S.message && S.message.id === v.id) { Object.assign(S.message.flags, v.flags); renderFlagButton(); }
  } else if (v.kind === 'removed') {
    if (r) removeRow(v.id);
    const name = v.deleted ? '已删除' : (folderById(v.to) || {}).name || '文件夹';
    toast(`已移到「${name}」`, { ms: 8000, action: v.from ? { label: '撤销', run: () => moveMessage(v.id, v.from, true) } : null });
    api('folders', { refresh: false }).then((f) => { S.folders = f; renderFolders(); }).catch(() => {});
  }
});

// ---------------------------------------------------------------- resizable columns
// `others` are the other fixed columns beside it; the last column (reader / contact
// details) never gets narrower than `rest` while dragging.
const SPLITS = { sidebarWidth: { el: '#split-side', prop: '--side-w', min: 180, max: 400, def: 232, others: ['.list-pane'], rest: 420 },
  listWidth: { el: '#split-list', prop: '--list-w', min: 280, max: 900, def: 400, others: ['.sidebar'], rest: 420 },
  ctTreeWidth: { el: '#split-ct-tree', prop: '--ct-tree-w', min: 160, max: 480, def: 250, others: ['.sidebar', '.ct-list-pane'], rest: 320 },
  ctListWidth: { el: '#split-ct-list', prop: '--ct-list-w', min: 240, max: 720, def: 340, others: ['.sidebar', '.ct-tree-pane'], rest: 320 } };
function applyWidths() {
  for (const [key, sp] of Object.entries(SPLITS)) $('#app').style.setProperty(sp.prop, (S.prefs[key] || sp.def) + 'px');
}
function saveWidths() {
  const values = Object.fromEntries(Object.keys(SPLITS).map((k) => [k, S.prefs[k]]).filter(([, v]) => v != null));
  api('setPrefs', { values }).then((p) => { S.prefs = p; applyWidths(); }).catch(() => {});
}
for (const [key, sp] of Object.entries(SPLITS)) {
  const handle = $(sp.el);
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const pane = handle.parentElement, others = sp.others.map((s) => $(s).getBoundingClientRect().width).reduce((a, b) => a + b, 0);
    const startX = e.clientX, startW = pane.getBoundingClientRect().width;
    handle.setPointerCapture(e.pointerId); handle.classList.add('drag'); document.body.classList.add('resizing');
    const move = (ev) => {
      const room = innerWidth - others - sp.rest;
      S.prefs[key] = Math.round(Math.max(sp.min, Math.min(sp.max, room, startW + ev.clientX - startX)));
      applyWidths();
    };
    const up = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); handle.removeEventListener('pointercancel', up);
      handle.classList.remove('drag'); document.body.classList.remove('resizing');
      saveWidths();
    };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', up); handle.addEventListener('pointercancel', up);
  });
  handle.addEventListener('dblclick', () => { S.prefs[key] = sp.def; applyWidths(); saveWidths(); });
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
    a.ondrop = (e) => {
      e.preventDefault(); a.classList.remove('drop');
      const many = e.dataTransfer.getData('text/x-hongyan-mids');
      if (many) batchMove(a.dataset.id, { ids: JSON.parse(many) }); else moveMessage(e.dataTransfer.getData('text/x-hongyan-mid'), a.dataset.id);
    };
  });
  $('#nav-drafts').classList.toggle('on', S.local === 'drafts');
  $('#nav-scheduled').classList.toggle('on', S.local === 'scheduled');
  $('#nav-outbox').classList.toggle('on', S.local === 'outbox');
  $('#nav-contacts').classList.toggle('on', S.local === 'contacts');
  $('#app').classList.toggle('contacts-mode', S.local === 'contacts');
  SF.renderChips();
  const current = folderById(S.folderId);
  if (current && !S.local && !S.search) $('#list-title').textContent = folderTitle(current);
  const inbox = S.folders.find((f) => f.role === 'inbox');
  api('setUnread', { count: (inbox && inbox.unread) || 0 });
  renderShared();
}
const folderById = (id) => S.folders.find((f) => f.id === String(id)) || S.shared.flatMap((b) => b.folders).find((f) => f.id === String(id));

// ---------------------------------------------------------------- shared mailboxes
// Folders of a shared mailbox have ids 'fid@box' and carry `mailbox`; its rights
// (rw: change state, send: send as it) are enforced again by the backend.
const boxById = (id) => S.shared.find((b) => b.id === id);
function boxOf(fid) { const f = folderById(fid); return f && f.mailbox ? boxById(f.mailbox) || null : null; }
function currentBox() { return !S.local && !S.search ? boxOf(S.folderId) : null; }
function foldersFor(fid) { const b = boxOf(fid); return b ? b.folders : S.folders; }
function folderTitle(f) { const b = f.mailbox && boxById(f.mailbox); return b ? `${f.name} · ${b.name || b.email}` : f.name; }
function writable(items) {
  const b = currentBox(); if (!b || b.rw) return items;
  return items.filter((it) => it === '-' || !it.write).filter((it, i, a) => it !== '-' || (i > 0 && i < a.length - 1 && a[i + 1] !== '-'));
}
function loadShared() { api('sharedMailboxes').then((b) => { S.shared = b; renderFolders(); }).catch(() => {}); }
function renderShared() {
  const nav = $('#shared-list');
  $('#shared-section').classList.toggle('hidden', !S.shared.length);
  const here = !S.local && !S.search;
  nav.innerHTML = S.shared.map((b) => {
    const open = S.openBoxes.has(b.id);
    const rights = (b.rw ? '' : ' · 只读') + (b.send ? '' : ' · 不能以此身份发信');
    return `<a class="folder box${open ? ' open' : ''}" data-box="${esc(b.id)}" title="${esc(b.email + rights)}">
      <i data-icon="chevron" class="chev"></i><i data-icon="shared"></i><span class="fname">${esc(b.name || b.email)}</span>
      <span class="count">${!open && b.unread ? b.unread : ''}</span></a>` + (open ? b.folders.filter((f) => f.role !== 'virus').map((f) =>
      `<a class="folder sub${here && f.id === S.folderId ? ' on' : ''}" data-id="${esc(f.id)}" title="${esc(f.name)}">
      <i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i><span class="fname">${esc(f.name)}</span>
      <span class="count">${f.unread && f.role !== 'sent' && f.role !== 'trash' ? f.unread : ''}</span></a>`).join('') : '');
  }).join('');
  renderIcons(nav);
  $$('[data-box]', nav).forEach((a) => (a.onclick = () => {
    const id = a.dataset.box; if (S.openBoxes.has(id)) S.openBoxes.delete(id); else S.openBoxes.add(id); renderShared();
  }));
  $$('[data-id]', nav).forEach((a) => (a.onclick = () => selectFolder(a.dataset.id)));
}
function applyFolders(res) { if (res.shared) S.shared = res.shared; if (res.folders.length) S.folders = res.folders; renderFolders(); }

function selectFolder(id) {
  S.folderId = String(id); S.local = null; S.search = null; $('#search').value = ''; SF.reset();
  S.rows = []; S.total = null; S.more = false; S.multi.clear(); S.open.clear(); renderSelection();
  const box = boxOf(id); if (box) S.openBoxes.add(box.id);
  renderFolders();
  const f = folderById(id);
  $('#list-title').textContent = f ? folderTitle(f) : '邮件';
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
    if (reset) S.counts.clear();  // Conversation sizes are re-read with the fresh page.
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

// ---------------------------------------------------------------- conversations (Outlook-style, expanded in place)
// In conversation mode the list shows one row per conversation of the loaded rows (newest
// first). Its triangle expands the whole conversation right under it: received and sent
// mail from every folder (backend 'thread': the cache plus a subject search on the server).
const SUBJECT_PREFIX = /^\s*(?:re|fwd?|fw|回复|转发|答复)\s*(?:\[\d+\])?\s*[:：]\s*/i;
function threadKey(subject) {
  let s = String(subject || '').trim();
  for (let prev = null; prev !== s;) { prev = s; s = s.replace(SUBJECT_PREFIX, '').trim(); }
  return s.replace(/\s+/g, ' ').toLowerCase();
}
function conversations(rows) {
  const byKey = new Map(); const items = [];
  for (const r of rows) {
    const key = threadKey(r.subject) || 'id:' + r.id;
    const item = byKey.get(key);
    if (item) { item.ids.push(r.id); item.rows.push(r); continue; }
    const fresh = { id: r.id, ids: [r.id], rows: [r], key };
    byKey.set(key, fresh);
    items.push(fresh);
  }
  for (const it of items) if (it.rows.length > 1) { it.rows.sort((a, b) => (b.ts || 0) - (a.ts || 0)); it.ids = it.rows.map((r) => r.id); it.id = it.ids[0]; }
  return items;
}
function listItems() {
  const rows = visibleRows();
  const single = (r) => ({ id: r.id, ids: [r.id], rows: [r] });
  if (!S.prefs.threads || S.local) return rows.map(single);
  return conversations(rows);
}
// The whole conversation once fetched (newest first), else the loaded rows of it.
function threadRows(item) { return (item.key && S.thread.get(item.key)) || item.rows; }
function threadSize(item) { return item.key ? Math.max(item.ids.length, S.counts.get(item.id) || 0, threadRows(item).length) : item.ids.length; }
function threadOf(id) { return S.items.find((it) => it.key && S.open.has(it.key) && threadRows(it).some((r) => r.id === id)); }
// A message's summary: loaded in the list, or shown in an expanded conversation (other folders).
function rowOf(id) { return S.rows.find((r) => r.id === id) || [...S.thread.values()].flat().find((r) => r.id === id); }
async function toggleThread(id, open) {
  const item = S.items.find((it) => it.id === id && it.key); if (!item) return;
  if (open === undefined) open = !S.open.has(item.key);
  if (!open) { S.open.delete(item.key); renderList(); return; }
  S.open.add(item.key); S.threadLoading.add(item.key); renderList();
  try {
    const res = await api('thread', { mid: item.id });
    // Rows already in the list stay the same objects, so flags change in one place.
    S.thread.set(item.key, res.rows.map((r) => S.rows.find((x) => x.id === r.id) || r));
  } catch (err) { fail(err); }
  S.threadLoading.delete(item.key);
  if (S.open.has(item.key)) renderList();
}
async function loadThreadCounts() {
  if (!S.prefs.threads || S.local) return;
  const ids = S.items.map((it) => it.id).filter((id) => !S.counts.has(id));
  if (!ids.length) return;
  ids.forEach((id) => S.counts.set(id, 0));
  // Sent mail joins conversations from the cache, so its newest page is kept fresh.
  const sent = S.folders.find((f) => f.role === 'sent');
  if (sent && Date.now() - S.sentSync > 600000) {
    S.sentSync = Date.now();
    await api('messages', { folder_id: sent.id, offset: 0, limit: 100 }).catch(() => {});
    S.items.forEach((it) => S.counts.set(it.id, 0));
  }
  try {
    const counts = await api('threadCounts', { ids: S.items.map((it) => it.id) });
    Object.entries(counts).forEach(([id, n]) => S.counts.set(id, n));
    renderList();
  } catch { /* The triangle then shows only for conversations of the loaded rows. */ }
}
function itemOf(id) { return S.items.find((it) => it.ids.includes(id)) || { id, ids: [id], rows: S.rows.filter((r) => r.id === id) }; }
function participants(item) {
  const names = [...new Map(threadRows(item).map((r) => [r.fromEmail || r.fromName, r.fromName || r.fromEmail])).values()].filter(Boolean);
  return names.slice(0, 3).join('、') + (names.length > 3 ? ` 等 ${names.length} 人` : '');
}

function rowHtml(r, item = { ids: [r.id], rows: [r] }) {
  const d = parseTs(r);
  const size = threadSize(item), thread = size > 1, open = thread && S.open.has(item.key);
  const sentLike = ['sent', 'drafts'].includes((folderById(r.fid) || {}).role);
  const who = thread ? participants(item) : sentLike ? '致 ' + (parseAddressList(r.to).map((p) => p.name || p.email).join('、') || '（无收件人）') : (r.fromName || r.fromEmail || '（未知发件人）');
  const folderTag = S.search && folderById(r.fid) && r.fid !== S.folderId ? `<span class="tag">${esc(folderById(r.fid).name)}</span>` : '';
  const any = (flag) => item.rows.some((x) => x.flags[flag]);
  const marks = (any('attached') ? '<i data-icon="clip"></i>' : '') + (any('flagged') ? '<i class="star" data-icon="star"></i>' : '') +
    (r.flags.encrypted ? '<i data-icon="lock"></i>' : '') + (r.flags.replied ? '<i data-icon="reply"></i>' : '');
  const role = (folderById(r.fid) || {}).role;
  const badge = r.flags.scheduleDelivery ? `<span class="row-badge" title="定时发送">${r.scheduleAt ? '定时 ' + esc(scheduleText(r.scheduleAt)) : '定时'}</span>`
    : role === 'sent' && r.flags.rcptRead ? '<span class="row-badge" title="收件人已阅读（服务器投递记录）">已读</span>' : '';
  const unread = item.rows.some((x) => !x.flags.read);
  const msel = S.multi.size && item.ids.every((id) => S.multi.has(id));
  const twisty = item.key ? `<button class="twisty${open ? ' open' : ''}${thread ? '' : ' none'}" data-twisty="${esc(item.id)}" tabindex="-1"
    title="${open ? '收起会话 (←)' : '展开会话：收到和发出的往来邮件 (→)'}"><i data-icon="chevron"></i></button>` : '';
  return `<div class="row${item.key ? ' conv' : ''}${open ? ' open' : ''}${unread ? ' unread' : ''}${!open && item.ids.includes(S.selected) ? ' sel' : ''}${msel ? ' msel' : ''}" data-id="${esc(r.id)}" draggable="true" role="option">
    ${twisty}${avatarHtml(r.fromName, sentLike ? r.to : r.fromEmail)}
    <div class="main">
      <div class="line1"><span class="who">${esc(who)}</span>${thread ? `<span class="tcount" title="${size} 封邮件">${size}</span>` : ''}${folderTag}<span class="marks">${marks}</span><span class="when">${esc(shortDate(d))}</span></div>
      <div class="subj">${badge}${esc(r.subject || '（无主题）')}${window.TG ? TG.chips(r.tags) : ''}</div>
      ${r.preview ? `<div class="prev">${esc(r.preview)}</div>` : ''}
    </div></div>`;
}

// One message of an expanded conversation; mail from another folder (已发送…) says where it is.
function childHtml(r) {
  const role = (folderById(r.fid) || {}).role;
  const who = role === 'sent' ? '我 → ' + (parseAddressList(r.to).map((p) => p.name || p.email).join('、') || '（无收件人）') : (r.fromName || r.fromEmail || '（未知发件人）');
  const where = r.fid !== S.folderId && folderById(r.fid) ? `<span class="tag">${esc(folderById(r.fid).name)}</span>` : '';
  const marks = (r.flags.attached ? '<i data-icon="clip"></i>' : '') + (r.flags.flagged ? '<i class="star" data-icon="star"></i>' : '') +
    (r.flags.encrypted ? '<i data-icon="lock"></i>' : '');
  return `<div class="row child${role === 'sent' ? ' mine' : ''}${r.flags.read ? '' : ' unread'}${S.selected === r.id ? ' sel' : ''}${S.multi.has(r.id) ? ' msel' : ''}" data-id="${esc(r.id)}" data-child="1" draggable="true" role="option">
    <div class="main"><div class="line1"><span class="who">${esc(who)}</span>${where}<span class="marks">${marks}</span><span class="when">${esc(shortDate(parseTs(r)))}</span></div>
      ${r.preview ? `<div class="prev">${esc(r.preview)}</div>` : ''}</div></div>`;
}

function renderList() {
  const list = $('#mail-list');
  const rows = visibleRows();
  S.items = listItems();
  let html = '';
  let group = null;
  for (const it of S.items) {
    const r = it.rows[0];
    const g = S.search ? null : groupOf(parseTs(r));
    if (g && g !== group) { group = g; html += `<div class="group-head">${g}</div>`; }
    html += rowHtml(r, it);
    if (it.key && S.open.has(it.key) && threadSize(it) > 1) {
      html += '<div class="thread-kids" role="group">' + threadRows(it).map(childHtml).join('') +
        (S.threadLoading.has(it.key) ? '<div class="kids-note">正在查找往来邮件…</div>' : '') + '</div>';
    }
  }
  if (!rows.length) {
    html = `<div class="list-foot">${S.local === 'scheduled' ? '没有等待发出的定时邮件' : S.search ? '没有找到匹配的邮件' : S.filter === 'unread' ? '没有未读邮件 🎉' : S.filter === 'flagged' ? '没有星标邮件' : '这个文件夹是空的'}</div>`;
  } else if (S.more && !S.search) {
    html += `<div class="list-foot" id="load-more">正在加载更多…</div>`;
  } else {
    html += `<div class="list-foot">${S.search && S.searchMore ? `已显示 ${rows.length} 封 · <button class="btn ghost sm" id="search-more">加载更多结果</button>` : S.search ? `共 ${rows.length} 封` : '已全部加载'}</div>`;
  }
  const top = list.scrollTop;
  list.innerHTML = html;
  list.scrollTop = top;
  renderIcons(list);
  const unread = S.rows.filter((r) => !r.flags.read).length;
  $('#list-sub').textContent = S.search ? '' : (S.total != null ? `${S.total} 封` : '') ;
  if (S.filter !== 'all' && S.more && rows.length < 15 && !S.loading && !S.search) loadList(false);
  loadThreadCounts();
  void unread;
}

$('#mail-list').addEventListener('scroll', () => {
  const l = $('#mail-list');
  if (S.more && !S.loading && !S.search && l.scrollTop + l.clientHeight > l.scrollHeight - 400) loadList(false);
});
$('#mail-list').addEventListener('click', (e) => {
  if (e.target.closest('#search-more')) { moreSearch(); return; }
  const twisty = e.target.closest('[data-twisty]');
  if (twisty) { toggleThread(twisty.dataset.twisty); return; }
  const row = e.target.closest('.row'); if (!row) return;
  if (S.local === 'drafts') { openLocalDraft(row.dataset.id); return; }
  if (S.local === 'outbox') { outboxOpen(row.dataset.id); return; }
  if (e.detail > 1) return;  // The second click of a double-click pops the message out instead.
  const child = !!row.dataset.child;
  if (e.ctrlKey || e.metaKey) { toggleItem(row.dataset.id, child); return; }
  if (e.shiftKey && !child) { selectRange(row.dataset.id); return; }
  clearSelection(false);
  S.anchor = row.dataset.id;
  selectMessage(row.dataset.id, { child });
});
$('#mail-list').addEventListener('dblclick', (e) => {
  if (e.target.closest('[data-twisty]')) return;
  if (e.target.closest('.row') && !S.local) (inDraftsFolder() ? editServerDraft(S.selected) : popOut(S.selected));
});
$('#mail-list').addEventListener('dragstart', (e) => {
  const row = e.target.closest('.row'); if (!row || S.local) return;
  const own = row.dataset.child ? [row.dataset.id] : itemOf(row.dataset.id).ids;
  const ids = S.multi.size > 1 && own.every((id) => S.multi.has(id)) ? [...S.multi] : own;
  e.dataTransfer.setData('text/x-hongyan-mid', row.dataset.id); e.dataTransfer.effectAllowed = 'move';
  if (ids.length > 1) e.dataTransfer.setData('text/x-hongyan-mids', JSON.stringify(ids));
});
$('#mail-list').addEventListener('contextmenu', (e) => {
  const row = e.target.closest('.row'); if (!row) return;
  e.preventDefault();
  if (S.local === 'outbox') { outboxMenu(e, row.dataset.id); return; }
  if (S.local === 'drafts') {
    showMenu(e.clientX, e.clientY, [{ label: '打开草稿', icon: 'pen', run: () => openLocalDraft(row.dataset.id) },
      { label: '删除草稿', icon: 'trash', run: () => deleteLocalDraft(row.dataset.id) }]);
    return;
  }
  const r = rowOf(row.dataset.id); if (!r) return;
  if (S.multi.size > 1 && (row.dataset.child ? [r.id] : itemOf(r.id).ids).every((id) => S.multi.has(id))) {
    const items = writable([
      { label: `标为已读（${S.multi.size} 封）`, icon: 'check', run: () => batchFlags({ read: true }), write: true },
      { label: '标为未读', icon: 'mail', run: () => batchFlags({ read: false }), write: true },
      { label: '添加星标', icon: 'star', run: () => batchFlags({ flagged: true }), write: true },
      { label: '取消星标', icon: 'star', run: () => batchFlags({ flagged: false }), write: true },
      { label: '移动到…', icon: 'folder', run: () => pickBatchFolder(), write: true },
      '-',
      { label: '删除', icon: 'trash', run: () => batchMove(null, { delete: true }), write: true },
    ]);
    if (items.length) showMenu(e.clientX, e.clientY, items);
    return;
  }
  showMenu(e.clientX, e.clientY, writable([
    { label: '回复', icon: 'reply', run: () => startCompose('reply', r.id) },
    { label: '全部回复', icon: 'replyall', run: () => startCompose('reply_all', r.id) },
    { label: '转发', icon: 'forward', run: () => startCompose('forward', r.id) },
    { label: '作为附件转发', icon: 'clip', run: () => startCompose('forward_attach', r.id) },
    ...(inDraftsFolder() && !r.flags.scheduleDelivery ? [{ label: '继续编辑草稿', icon: 'pen', run: () => editServerDraft(r.id) }] : []),
    '-',
    { label: r.flags.read ? '标为未读' : '标为已读', icon: 'mail', run: () => setFlags(r.id, { read: !r.flags.read }), write: true },
    { label: r.flags.flagged ? '取消星标' : '添加星标', icon: 'star', run: () => setFlags(r.id, { flagged: !r.flags.flagged }), write: true },
    { label: '移动到…', icon: 'folder', run: () => pickFolder(r.id), write: true },
    '-',
    { label: '删除', icon: 'trash', run: () => deleteMessage(r.id), write: true },
  ]));
});

$$('#filter-seg button').forEach((b) => (b.onclick = () => {
  $$('#filter-seg button').forEach((x) => x.classList.toggle('on', x === b));
  S.filter = b.dataset.filter; renderList();
}));
$('#btn-refresh').onclick = () => { api('syncNow'); if (!S.local) loadList(true); api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(() => {}); loadShared(); };

// ---------------------------------------------------------------- search
let searchToken = 0;
async function runSearch(q) {
  q = q.trim();
  const filters = SF.active();  // Advanced-search conditions (contacts.js); a search may have only these.
  if (!q && !Object.keys(filters).length) { clearSearch(); return; }
  const token = ++searchToken;
  S.search = q || ' '; S.local = null; S.filter = 'all'; S.multi.clear(); S.searchMore = null; S.open.clear(); renderSelection(); renderFolders();
  $$('#filter-seg button').forEach((x) => x.classList.toggle('on', x.dataset.filter === 'all'));
  $('#list-title').textContent = '搜索结果';
  SF.renderChips();
  S.rows = await api('searchLocal', { query: q, filters }).catch(() => []);
  if (token !== searchToken) return;
  renderList();
  $('#list-sub').textContent = '本机结果 · 正在搜索服务器…';
  try {
    const remote = await api('searchServer', { query: q, filters });
    if (token !== searchToken) return;
    S.rows = dedupe(S.rows.concat(remote.rows)).sort((a, b) => (b.ts || 0) - (a.ts || 0));
    S.searchMore = remote.more ? { query: q, filters, offset: 50, token } : null;
    renderList();
    $('#list-sub').textContent = `服务器 ${remote.total} 封匹配` + (remote.local.length ? ' · 部分条件仅筛选已取回的邮件' : '');
    SF.renderChips(remote.local);
  } catch (err) {
    if (token === searchToken) $('#list-sub').textContent = '服务器搜索不可用，显示本机结果';
  }
}
async function moreSearch() {
  const m = S.searchMore; if (!m || m.token !== searchToken || m.busy) return;
  m.busy = true;
  try {
    const remote = await api('searchServer', { query: m.query, filters: m.filters, offset: m.offset });
    if (m.token !== searchToken) return;
    S.rows = dedupe(S.rows.concat(remote.rows)).sort((a, b) => (b.ts || 0) - (a.ts || 0));
    S.searchMore = remote.more ? { ...m, offset: m.offset + 50, busy: false } : null;
    renderList();
  } catch (err) { m.busy = false; fail(err); }
}
function clearSearch() { if (S.search) { searchToken++; selectFolder(S.folderId); } else SF.reset(); }
$('#search').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runSearch(e.target.value);
  if (e.key === 'Escape') { e.target.value = ''; clearSearch(); e.target.blur(); }
  if (e.key === 'ArrowDown' && e.altKey) { e.preventDefault(); SF.open(); }
});
let searchTimer = null;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  if (!e.target.value.trim()) { if (!Object.keys(SF.active()).length) clearSearch(); return; }
  searchTimer = setTimeout(async () => {  // Instant local results while typing.
    const q = e.target.value.trim(); if (!q) return;
    const token = ++searchToken;
    S.search = q; S.searchMore = null; S.rows = await api('searchLocal', { query: q, filters: SF.active() }).catch(() => []);
    if (token !== searchToken) return;
    $('#list-title').textContent = '搜索结果'; renderList(); $('#list-sub').textContent = '本机结果 · 回车搜索服务器';
  }, 220);
});

// ---------------------------------------------------------------- reading
let openToken = 0;
async function selectMessage(id, opts = {}) {
  S.selected = id;
  const item = itemOf(id);
  // In an expanded conversation the message's own line is selected; otherwise the list row.
  const inThread = opts.child || !!threadOf(id);
  $$('#mail-list .row').forEach((r) => r.classList.toggle('sel', r.dataset.child ? r.dataset.id === id
    : !inThread && !r.classList.contains('open') && item.ids.includes(r.dataset.id)));
  const row = inThread ? $(`#mail-list .row.child[data-id="${CSS.escape(id)}"]`) : $(`#mail-list .row[data-id="${CSS.escape(item.id)}"]`);
  if (row && !opts.noScroll) row.scrollIntoView({ block: 'nearest' });
  if (S.multi.size >= 2) return;  // The selection toolbar stays; J/K only move the cursor.
  const token = ++openToken;
  const summary = rowOf(id);
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
    if (READER_MID) { document.title = (m.subject || '（无主题）') + ' · 鸿雁'; if (m.markedRead) notifyMain({ kind: 'flags', id, flags: { read: true } }); }
    if (m.markedRead && summary) {
      summary.flags.read = true;
      row && row.classList.toggle('unread', row.dataset.child ? false : itemOf(id).rows.some((r) => !r.flags.read));
      if (row && row.dataset.child) renderList();  // The conversation's own row may now be read too.
      const f = folderById(summary.fid); if (f && f.unread > 0) { f.unread--; renderFolders(); }
    }
  } catch (err) {
    if (token !== openToken) return;
    S.message = null;
    writeFrame(`<div style="color:#d92d20">${esc(err.message)}</div>`);
  }
}

function renderRecipients(m) {
  const box = $('#r-to');
  const total = m.to.length + m.cc.length;
  const parts = [];
  if (m.to.length) parts.push('收件人：' + people(m.to));
  if (m.cc.length) parts.push('抄送：' + people(m.cc));
  box.innerHTML = `<span class="r-list">${parts.join('<span class="r-sep">·</span>')}</span>` +
    `<a class="r-more hidden" title="显示全部收件人和抄送">展开（共 ${total} 人）</a>`;
  box.classList.add('clamped');
  $$('.r-list a', box).forEach((a) => (a.onclick = () => personPopover(a, { name: a.dataset.name || '', email: a.dataset.mailto })));
  const more = $('.r-more', box);
  more.onclick = () => {
    const open = box.classList.toggle('clamped');
    more.textContent = open ? `展开（共 ${total} 人）` : '收起';
  };
  // Only offer the toggle when the single line actually overflows.
  requestAnimationFrame(() => { const list = $('.r-list', box); more.classList.toggle('hidden', !list || list.scrollWidth <= list.clientWidth + 1); });
}
function people(list) { return list.map((p) => `<a title="${esc(p.email)}" data-mailto="${esc(p.email)}" data-name="${esc(p.name || '')}">${esc(p.name || p.email)}</a>`).join('、'); }
function renderMessage(m) {
  $('#r-subject').textContent = m.subject || '（无主题）';
  const from = m.from[0] || { name: '', email: '' };
  $('#r-from-name').textContent = from.name || from.email; $('#r-from-mail').textContent = from.email ? `<${from.email}>` : '';
  avatar($('#r-avatar'), from.name, from.email);
  const d = m.date ? new Date(String(m.date).replace(' ', 'T')) : null;
  $('#r-date').textContent = d && !isNaN(d) ? longDate(d) : (m.date || '');
  renderRecipients(m);
  renderFlagButton();
  const role = (folderById(m.fid) || {}).role;
  const panel = $('#ds-panel'); if (panel) panel.remove();
  $$('#reader-body .sent-only').forEach((b) => b.classList.toggle('hidden', role !== 'sent'));
  const box = m.mailbox;
  $$('#reader-body .rw-only').forEach((b) => b.classList.toggle('hidden', (!!box && !box.rw) || !!m.imported));
  setRecallAllowed(!box, '共享邮箱的邮件请在网页版中撤回');
  $$('#reader-body .sched-only').forEach((b) => b.classList.toggle('hidden', !m.scheduled));
  // Banners
  const banners = [];
  if (box) banners.push(`<div class="banner info"><i data-icon="shared"></i><span>共享邮箱「${esc(box.name || box.email)}」${box.rw ? '' : ' · 只读'} · 回复${box.send ? '将以该邮箱身份发出' : '将以你自己的地址发出'}。</span></div>`);
  if (m.scheduled) banners.push(`<div class="banner info"><i data-icon="clock"></i><span>这是一封定时邮件${m.scheduleAt ? `，将于 <b>${esc(scheduleText(m.scheduleAt))}</b> 由服务器发出` : '，到时间由服务器发出'}。</span><span class="spacer"></span><button class="btn ghost" data-act="unschedule">取消定时</button></div>`);
  if (m.imported) banners.push('<div class="banner info"><i data-icon="open"></i><span>这是从本机 .eml 文件打开的邮件（只读，不在服务器上）。可以回复或转发。</span></div>');
  if (m.serverDraft) banners.push('<div class="banner info"><i data-icon="draft"></i><span>这是服务器草稿箱中的草稿（网页版、手机或其他客户端保存）。</span><span class="spacer"></span><button class="btn primary" id="b-edit-draft">继续编辑</button></div>');
  if (m.encryption === 'locked') banners.push(`<div class="banner"><i data-icon="lock"></i><span>这是一封证书加密邮件，解锁个人证书后即可阅读。</span><span class="spacer"></span><button class="btn primary" id="b-unlock">解锁证书</button></div>`);
  if (m.encryption === 'decrypted') banners.push(`<div class="banner info"><i data-icon="lock"></i><span>此邮件已使用你的证书在本机解密。</span></div>`);
  const sig = signatureBanner(m.signature); if (sig) banners.push(sig);
  if (m.hasRemoteImages && !S.allowRemote.has(m.id)) banners.push(`<div class="banner"><i data-icon="image"></i><span>为保护隐私，已阻止远程图片。</span><span class="spacer"></span><button class="btn ghost" id="b-images">显示图片</button></div>`);
  $('#r-banners').innerHTML = banners.join('');
  renderIcons($('#r-banners'));
  const unlock = $('#b-unlock'); if (unlock) unlock.onclick = unlockCertificate;
  const editDraft = $('#b-edit-draft'); if (editDraft) editDraft.onclick = () => editServerDraft(m.id);
  const images = $('#b-images'); if (images) images.onclick = async () => { S.allowRemote.add(m.id); await api('allowRemoteImages', { allow: true }); renderMessage(m); };
  // Attachments
  const atts = $('#r-attachments');
  if (m.attachments.length) {
    const n = m.attachments.length, fold = n > 4, total = m.attachments.reduce((s, a) => s + (a.size || 0), 0);
    atts.innerHTML = `<div class="att-head"><i data-icon="clip"></i><span>${n} 个附件 · 共 ${size(total)}</span>` +
      (n > 1 ? '<a id="save-all">全部保存</a>' : '') + '</div><div class="att-list">' +
      m.attachments.map((a, i) => { const [c, e] = extColor(a.name); return `<div class="att${fold && i >= 3 ? ' extra hidden' : ''}" title="${esc(a.name)}">
      <span class="ext" style="background:${c}">${esc(e)}</span><span class="name">${esc(a.name)}</span><span class="size">${size(a.size)}</span>
      <button class="icon-btn" data-open="${esc(a.part)}" title="打开"><i data-icon="open"></i></button>
      <button class="icon-btn" data-save="${esc(a.part)}" title="另存为"><i data-icon="download"></i></button></div>`; }).join('') +
      (fold ? `<button class="att-more" id="att-more">+${n - 3} 个</button>` : '') + '</div>';
    renderIcons(atts);
    atts.classList.remove('hidden');
    $$('[data-open]', atts).forEach((b) => (b.onclick = () => api('openAttachment', { mid: m.id, part: b.dataset.open })
      .then(() => toast('正在用系统中的程序打开…')).catch(fail)));
    $$('[data-save]', atts).forEach((b) => (b.onclick = () => api('saveAttachment', { mid: m.id, part: b.dataset.save }).then((ok) => ok && toast('附件已保存')).catch(fail)));
    const all = $('#save-all'); if (all) all.onclick = () => api('saveAllAttachments', { mid: m.id }).then((n) => n && toast(`已保存 ${n} 个附件`)).catch(fail);
    const moreAtts = $('#att-more', atts);
    if (moreAtts) moreAtts.onclick = () => {
      const hidden = $$('.att.extra', atts).some((el) => el.classList.contains('hidden'));
      $$('.att.extra', atts).forEach((el) => el.classList.toggle('hidden', !hidden));
      moreAtts.textContent = hidden ? '收起' : `+${$$('.att.extra', atts).length} 个`;
    };
  } else { atts.classList.add('hidden'); atts.innerHTML = ''; }
  renderBody(m);
  $('.reader-scroll').scrollTop = 0;
  if (role === 'sent') loadDeliveryStatus(m, false);
}

// Sent mail: per-recipient delivery state, straight from the server's records.
async function loadDeliveryStatus(m, open) {
  const box = document.createElement('div');
  box.className = 'ds-panel'; box.id = 'ds-panel';
  const old = $('#ds-panel'); if (old) old.remove();
  $('#r-banners').after(box);
  box.innerHTML = '<div class="ds-head"><i data-icon="route"></i><span class="muted">正在查询投递状态…</span></div>';
  renderIcons(box);
  let st;
  try { st = await api('deliveryStatus', { mid: m.id }); } catch (err) {
    if (S.message !== m) return;
    box.innerHTML = `<div class="ds-head"><i data-icon="route"></i><span class="muted">投递状态暂不可用：${esc(err.message)}</span><span class="spacer"></span><button class="btn ghost" data-ds="retry">重试</button></div>`;
    renderIcons(box); $('[data-ds="retry"]', box).onclick = () => loadDeliveryStatus(m, true); return;
  }
  if (S.message !== m) return;
  // The server's own per-recipient flag decides: when no recipient is recallable the
  // button is greyed out with the reason, instead of failing after a confirmation.
  const blocked = st.rows.length > 0 && !st.recallable;
  const reason = st.read === st.rows.length ? RECALL_READ_TEXT : RECALL_BLOCKED_TEXT;
  setRecallAllowed(!blocked, reason);
  const summary = st.rows.length ? `共 ${st.rows.length} 位收件人：` + Object.entries(st.counts).map(([t, n]) => `${n} 位${t}`).join('，') : '暂无投递记录（邮件可能仍在处理中，或记录已过期）';
  box.innerHTML = `<div class="ds-head"><i data-icon="route"></i><span>${esc(summary)}</span><span class="spacer"></span>
    ${st.rows.length ? `<button class="btn ghost" data-ds="toggle">${open ? '收起' : '详情'}</button>` : ''}<button class="btn ghost" data-ds="retry" title="刷新">刷新</button></div>
    ${blocked ? `<div class="ds-note muted small"><i data-icon="undo"></i>${reason}。</div>` : ''}
    <table class="ds-table${open ? '' : ' hidden'}">${st.rows.map((r) => `<tr><td class="who"><b>${esc(r.name || r.email)}</b>${r.name ? ` <span class="muted small">${esc(r.email)}</span>` : ''}</td>
      <td><span class="ds-state ${esc(r.kind)}">${esc(r.text)}</span></td><td class="when">${esc(r.time)}</td></tr>`).join('')}</table>`;
  renderIcons(box);
  const t = $('[data-ds="toggle"]', box);
  if (t) t.onclick = () => { const tbl = $('.ds-table', box); tbl.classList.toggle('hidden'); t.textContent = tbl.classList.contains('hidden') ? '详情' : '收起'; };
  $('[data-ds="retry"]', box).onclick = () => loadDeliveryStatus(m, !$('.ds-table', box) || !$('.ds-table', box).classList.contains('hidden'));
}

const RECALL_BLOCKED_TEXT = '服务器标记这封邮件不可撤回：本邮箱未开放撤回功能（服务器策略）';
const RECALL_READ_TEXT = '收件人均已阅读，服务器不再允许撤回这封邮件';
function setRecallAllowed(allowed, reason = RECALL_BLOCKED_TEXT) {
  $$('#reader-body [data-act="recall"]').forEach((b) => {
    b.disabled = !allowed;
    b.title = allowed ? '撤回这封已发送邮件' : reason;
  });
}

async function recallMessage(m) {
  const button = $('#reader-body [data-act="recall"]');
  if (button && button.disabled) { toast(button.title, { error: true }); return; }
  if (!(await confirmDialog('撤回邮件', '确定撤回这封邮件吗？只能撤回发给本公司邮箱、且对方尚未阅读的邮件。撤回结果以服务器返回为准。', '撤回'))) return;
  let res;
  try { res = await api('recall', { mid: m.id }); } catch (err) {
    if (err && err.code === 'RECALL_UNSUPPORTED' && S.message === m) setRecallAllowed(false);
    fail(err); return;
  }
  const rows = res.rows.map((r) => `<tr><td class="who"><b>${esc(r.email)}</b></td><td><span class="ds-state ${esc(r.kind)}">${esc(r.text)}</span></td></tr>`).join('');
  const title = res.recalled === res.rows.length && res.rows.length ? '已撤回' : res.recalled ? `部分撤回（${res.recalled}/${res.rows.length}）` : '未能撤回';
  await promptDialog({ title, text: res.recalled ? '以下是服务器返回的每位收件人的撤回结果：' : '服务器返回的结果如下，邮件没有被撤回：',
    body: `<table class="ds-table">${rows || '<tr><td class="muted">服务器没有返回收件人结果</td></tr>'}</table>` });
  if (S.message === m) loadDeliveryStatus(m, true);
}

async function unscheduleMessage(m) {
  if (!(await confirmDialog('取消定时发送', '取消后这封邮件不会发出，会作为普通草稿保留在服务器草稿箱中。', '取消定时'))) return;
  try {
    await api('unschedule', { mid: m.id });
    toast('已取消定时发送，邮件保留在草稿箱');
    m.scheduled = false; renderMessage(m); loadList(true); refreshScheduled();
  } catch (err) { fail(err); }
}

function linkify(text) {
  return esc(text).replace(/(https?:\/\/[^\s<>"']+)/g, '<a href="$1">$1</a>');
}
function renderFlagButton() { $$('.reader-toolbar [data-act="flag"]').forEach((b) => b.classList.toggle('on', !!(S.message && S.message.flags.flagged))); }
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
    <style>html{background:${paper ? '#fff' : 'transparent'};${paper ? 'border-radius:10px;' : ''}overflow-y:hidden}
    body{margin:0;padding:${paper ? '18px 20px' : '0'};color:${fg};font:14.5px/1.7 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif;word-wrap:break-word;overflow-wrap:anywhere;overflow-x:auto}
    img{max-width:100%;height:auto} a{color:${dark && !paper ? '#7aa2ff' : '#2563eb'}} pre{white-space:pre-wrap}
    blockquote{margin:0 0 0 4px;padding-left:12px;border-left:3px solid #d0d5dd;color:${dark && !paper ? '#b3bac7' : '#475467'}}
    ::selection{background:#bfd3ff}</style></head><body>${html}</body></html>`;
}
$('#r-frame').addEventListener('load', () => {
  const frame = $('#r-frame');
  const doc = frame.contentDocument; if (!doc) return;
  let last = 0;
  // The frame grows to its content and never scrolls itself: one scroll area (the
  // reading pane) is easier to read long mail in than a scroller inside a scroller.
  const fit = () => {
    const h = Math.max(120, doc.documentElement.scrollHeight, doc.body.scrollHeight) + 8;
    if (Math.abs(h - last) > 2) { last = h; frame.style.height = h + 'px'; }
  };
  fit();
  setTimeout(fit, 250); setTimeout(fit, 1000);  // Fonts and data: images settle after load.
  $$('img', doc).forEach((img) => img.addEventListener('load', fit));
  try { new ResizeObserver(() => requestAnimationFrame(fit)).observe(doc.body); } catch (e) { /* optional */ }
  doc.addEventListener('keydown', (e) => handleKey(e));
});

$('#reader-body').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-act]'); if (!b || !S.message) return;
  const m = S.message;
  if (READER_MID && (await readerAct(b.dataset.act, m))) return;
  ({
    reply: () => startCompose('reply', m.id), reply_all: () => startCompose('reply_all', m.id), forward: () => startCompose('forward', m.id),
    delete: () => deleteMessage(m.id), move: () => pickFolder(m.id), unread: () => setFlags(m.id, { read: false }),
    flag: () => setFlags(m.id, { flagged: !m.flags.flagged }), find: openFind,
    recall: () => recallMessage(m), status: () => loadDeliveryStatus(m, true), unschedule: () => unscheduleMessage(m),
    print: () => printMessage(m), popout: () => popOut(m.id), more: () => moreMenu(b, m),
  }[b.dataset.act] || (() => {}))();
});

// ---------------------------------------------------------------- print and export
function printMessage(m) {
  if (m.encryption === 'locked') { toast('加密邮件需要先解锁证书才能打印', { error: true }); return; }
  api('printMessage', { mid: m.id, remote: S.allowRemote.has(m.id) }).catch(fail);
}
const PLAIN_COPY_TEXT = '导出的文件不再加密，任何能打开这个文件的人都能阅读邮件内容。请只保存到受保护的位置。';
async function exportPdf(m) {
  if (m.encryption === 'locked') { toast('加密邮件需要先解锁证书才能导出 PDF', { error: true }); return; }
  if (m.encryption === 'decrypted' && !(await confirmDialog('导出未加密的 PDF', PLAIN_COPY_TEXT, '仍然导出'))) return;
  api('exportPdf', { mid: m.id, remote: S.allowRemote.has(m.id) }).catch(fail);
}
async function exportEml(m) {
  let decrypted = false;
  if (m.encryption === 'decrypted') {
    const choice = await promptDialog({ title: '导出加密邮件', okText: '导出',
      text: '这是一封加密邮件。默认导出服务器上的原始加密邮件（.eml），需要你的证书才能打开。',
      body: `<div class="pick-radio"><label class="check"><input type="radio" name="eml-kind" value="orig" checked> 原始加密邮件（推荐）</label>
        <label class="check"><input type="radio" name="eml-kind" value="plain"> 解密后的副本（不加密保存）</label></div>`,
      collect: () => ($('input[name="eml-kind"]:checked') || {}).value });
    if (!choice) return;
    decrypted = choice === 'plain';
    if (decrypted && !(await confirmDialog('保存未加密的副本', PLAIN_COPY_TEXT, '保存解密副本'))) return;
  }
  try { if (await api('exportEml', { mid: m.id, decrypted })) toast(decrypted ? '已导出解密副本' : '邮件已导出'); } catch (err) { fail(err); }
}
function moreMenu(button, m) {
  const r = button.getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [
    { label: '作为附件转发', icon: 'clip', run: () => startCompose('forward_attach', m.id) },
    ...(m.imported ? [] : [{ label: '将收件人存为收件人组…', icon: 'contacts', run: () => groupFromMessage(m) }]),
    '-',
    { label: '打印…', icon: 'print', run: () => printMessage(m) },
    { label: '导出为 PDF…', icon: 'file', run: () => exportPdf(m) },
    { label: m.encryption === 'none' ? '导出为 .eml…' : '导出为 .eml（加密原件或解密副本）…', icon: 'download', run: () => exportEml(m) },
    ...(READER_MID ? [] : ['-', { label: '在新窗口中打开', icon: 'popout', run: () => popOut(m.id) }]),
  ]);
}
on('printed', (v) => {
  if (v.ok) toast(v.kind === 'pdf' ? 'PDF 已导出' : '已发送到打印机');
  else toast(v.message || '打印未完成', { error: true });
});

async function setFlags(id, flags) {
  const r = rowOf(id);
  const before = r ? { ...r.flags } : null;
  if (r) { Object.assign(r.flags, flags); renderList(); }
  if (S.message && S.message.id === id) { Object.assign(S.message.flags, flags); renderFlagButton(); }
  try {
    await api('setFlags', { mid: id, read: flags.read ?? null, flagged: flags.flagged ?? null });
    if ('read' in flags && r && before && before.read !== flags.read) {
      const f = folderById(r.fid); if (f) { f.unread = Math.max(0, (f.unread || 0) + (flags.read ? -1 : 1)); renderFolders(); }
    }
    if (flags.read === false) toast('已标为未读');
  } catch (err) { if (r && before) { r.flags = before; renderList(); } fail(err); }
}

function neighbour(id) {
  const i = S.items.findIndex((it) => it.ids.includes(id)); if (i < 0) return null;
  const rest = S.items[i].ids.filter((x) => x !== id);
  if (rest.length) return { id: rest[0] };
  const next = S.items[i + 1] || S.items[i - 1];
  return next ? { id: next.id } : null;
}
function removeRow(id) {
  const next = S.selected === id ? neighbour(id) : null;
  S.rows = S.rows.filter((r) => r.id !== id);
  for (const [key, rows] of S.thread) S.thread.set(key, rows.filter((r) => r.id !== id));
  renderList();
  if (S.selected === id) {
    if (next) selectMessage(next.id); else { S.selected = null; S.message = null; $('#reader-body').classList.add('hidden'); $('#reader-empty').classList.remove('hidden'); }
  }
}
async function deleteMessage(id) {
  const r = rowOf(id);
  const trash = foldersFor(r ? r.fid : S.folderId).find((f) => f.role === 'trash');
  if (r && trash && r.fid === trash.id) { toast('邮件已在已删除文件夹中。为避免误删，本应用不提供彻底删除。'); return; }
  removeRow(id);
  try {
    await api('delete', { mid: id });
    toast('已移到「已删除」', { action: trash && r ? { label: '撤销', run: () => moveMessage(id, r.fid, true) } : null });
  } catch (err) { fail(err); loadList(true); }
}
async function moveMessage(id, folderId, undo) {
  const r = rowOf(id);
  if (r && r.fid === String(folderId)) return;
  if (r && !S.search) removeRow(id);
  try {
    await api('move', { mid: id, folder_id: String(folderId) });
    if (undo) { toast('已撤销'); loadList(true); }
    else toast(`已移动到「${(folderById(folderId) || {}).name || '文件夹'}」`);
    api('folders').then((f) => { S.folders = f; renderFolders(); }).catch(() => {});
    if (boxOf(folderId)) loadShared();
  } catch (err) { fail(err); loadList(true); }
}
async function pickFolder(id) {
  const r = rowOf(id) || {};
  await promptDialog({ title: '移动到文件夹',
    body: '<div class="pick-list">' + foldersFor(r.fid || S.folderId).filter((f) => f.id !== r.fid && f.role !== 'virus').map((f) =>
      `<button data-fid="${esc(f.id)}"><i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i>${esc(f.name)}</button>`).join('') + '</div>',
    onOpen: (d, done) => $$('[data-fid]', d).forEach((b) => (b.onclick = () => { done(true); moveMessage(id, b.dataset.fid); })) });
}

// ---------------------------------------------------------------- multi-select and batch actions
function toggleItem(id, single = false) {
  if (!S.multi.size && S.selected && S.selected !== id) (threadOf(S.selected) ? [S.selected] : itemOf(S.selected).ids).forEach((m) => S.multi.add(m));
  const ids = single ? [id] : itemOf(id).ids;
  const on = ids.every((m) => S.multi.has(m));
  ids.forEach((m) => (on ? S.multi.delete(m) : S.multi.add(m)));
  S.anchor = id; renderSelection();
}
function selectRange(id) {
  const a = S.items.findIndex((it) => it.ids.includes(S.anchor || S.selected));
  const b = S.items.findIndex((it) => it.ids.includes(id));
  if (b < 0) return;
  const [lo, hi] = a < 0 ? [b, b] : [Math.min(a, b), Math.max(a, b)];
  S.multi = new Set(S.items.slice(lo, hi + 1).flatMap((it) => it.ids));
  renderSelection();
}
function selectAll() { S.multi = new Set(S.items.flatMap((it) => it.ids)); renderSelection(); }
function clearSelection(reopen = true) {
  if (!S.multi.size) return false;
  const many = S.multi.size >= 2;
  S.multi.clear(); renderSelection();
  if (reopen && many && S.selected && (!S.message || S.message.id !== S.selected)) selectMessage(S.selected, { noScroll: true });
  return true;
}
function selectedRows() { return [...S.multi].map(rowOf).filter(Boolean); }
function renderSelection() {
  $$('#mail-list .row').forEach((el) => el.classList.toggle('msel', S.multi.size > 0 && (el.dataset.child ? [el.dataset.id] : itemOf(el.dataset.id).ids).every((m) => S.multi.has(m))));
  const n = S.multi.size, many = n >= 2;
  $('#reader-multi').classList.toggle('hidden', !many);
  $('#reader-body').classList.toggle('hidden', many || !S.selected);
  $('#reader-empty').classList.toggle('hidden', many || !!S.selected);
  if (!many) return;
  const rows = selectedRows();
  const senders = [...new Map(rows.map((r) => [r.fromEmail || r.fromName, r])).values()];
  $('#multi-count').textContent = $('#multi-count-big').textContent = n;
  $('#multi-stack').innerHTML = senders.slice(0, 5).map((r) => avatarHtml(r.fromName, r.fromEmail, '')).join('') +
    (senders.length > 5 ? `<span class="avatar more">+${senders.length - 5}</span>` : '');
  const unread = rows.filter((r) => !r.flags.read).length, flagged = rows.filter((r) => r.flags.flagged).length;
  $('#multi-sub').textContent = [`来自 ${senders.length} 位发件人`, unread ? `${unread} 封未读` : '', flagged ? `${flagged} 封星标` : ''].filter(Boolean).join(' · ');
  $('#multi-flag').textContent = rows.length && flagged === rows.length ? '取消星标' : '添加星标';
}
$('#reader-multi').addEventListener('click', (e) => {
  const b = e.target.closest('[data-batch]'); if (!b) return;
  const rows = selectedRows();
  ({
    read: () => batchFlags({ read: true }), unread: () => batchFlags({ read: false }),
    flag: () => batchFlags({ flagged: !(rows.length && rows.every((r) => r.flags.flagged)) }),
    move: () => pickBatchFolder(), delete: () => batchMove(null, { delete: true }), clear: () => clearSelection(),
  }[b.dataset.batch] || (() => {}))();
});

async function batchFlags(flags) {
  const ids = [...S.multi]; const rows = selectedRows();
  const before = new Map(rows.map((r) => [r.id, { ...r.flags }]));
  const redraw = () => { renderList(); renderSelection(); };
  rows.forEach((r) => Object.assign(r.flags, flags)); redraw();
  if (S.message && S.multi.has(S.message.id)) Object.assign(S.message.flags, flags);
  try {
    const res = await api('setFlags', { mid: ids, read: flags.read ?? null, flagged: flags.flagged ?? null });
    for (const id of res.failed) { const r = rows.find((x) => x.id === id); if (r) r.flags = before.get(id); }
    applyFolders(res);
    redraw();
    const what = 'read' in flags ? (flags.read ? '标为已读' : '标为未读') : (flags.flagged ? '添加星标' : '取消星标');
    if (res.failed.length) toast(`${res.done.length} 封已${what}，${res.failed.length} 封未完成，请稍后重试`, { error: true });
    else toast(`已将 ${res.done.length} 封${what}`);
  } catch (err) { rows.forEach((r) => (r.flags = before.get(r.id))); redraw(); fail(err); }
}
function removeRows(ids) {
  const gone = new Set(ids);
  S.rows = S.rows.filter((r) => !gone.has(r.id));
  if (gone.has(S.selected)) { S.selected = null; S.message = null; }
  renderList();
}
async function batchMove(folderId, opts = {}) {
  const ids = opts.ids || [...S.multi];
  const trash = foldersFor(S.folderId).find((f) => f.role === 'trash');
  const target = opts.delete ? (trash ? trash.id : '4') : String(folderId);
  const rows = S.rows.filter((r) => ids.includes(r.id));
  const origin = new Map(rows.map((r) => [r.id, r.fid]));
  const moving = rows.filter((r) => r.fid !== target).map((r) => r.id);
  if (!moving.length) { toast(opts.delete ? '所选邮件已在已删除文件夹中。为避免误删，本应用不提供彻底删除。' : '所选邮件已在该文件夹中'); return; }
  S.multi.clear();
  if (S.search) { rows.forEach((r) => moving.includes(r.id) && (r.fid = target)); renderList(); } else removeRows(moving);
  renderSelection();
  try {
    const res = opts.delete ? await api('delete', { mid: moving }) : await api('move', { mid: moving, folder_id: target });
    applyFolders(res);
    const name = (folderById(target) || {}).name || (opts.delete ? '已删除' : '文件夹');
    toast(`已将 ${res.done.length} 封移到「${name}」` + (res.failed.length ? `，${res.failed.length} 封未完成` : ''),
      { ms: 8000, error: !!res.failed.length, action: res.done.length ? { label: '撤销', run: () => undoMove(res.done, origin) } : null });
    if (res.failed.length) loadList(true);
  } catch (err) { fail(err); loadList(true); }
}
async function undoMove(ids, origin) {
  const groups = new Map();
  for (const id of ids) { const fid = origin.get(id); if (fid) groups.set(fid, [...(groups.get(fid) || []), id]); }
  try {
    for (const [fid, group] of groups) { const res = await api('move', { mid: group, folder_id: fid }); if (res.folders.length) S.folders = res.folders; }
    renderFolders(); toast('已撤销');
  } catch (err) { fail(err); }
  if (S.search) runSearch(S.search); else loadList(true);
}
async function pickBatchFolder() {
  const n = S.multi.size;
  await promptDialog({ title: `移动 ${n} 封邮件到文件夹`,
    body: '<div class="pick-list">' + foldersFor(S.folderId).filter((f) => f.role !== 'virus').map((f) =>
      `<button data-fid="${esc(f.id)}"><i data-icon="${ROLE_ICON[f.role] || 'folder'}"></i>${esc(f.name)}</button>`).join('') + '</div>',
    onOpen: (d, done) => $$('[data-fid]', d).forEach((b) => (b.onclick = () => { done(true); batchMove(b.dataset.fid); })) });
}

// ---------------------------------------------------------------- conversation toggle
function renderThreadToggle() {
  const b = $('#btn-threads');
  b.classList.toggle('on', !!S.prefs.threads);
  b.title = S.prefs.threads ? '会话视图：已开启（点击邮件左侧的三角，在原位展开收到和发出的往来邮件）' : '会话视图：已关闭';
}
$('#btn-threads').onclick = async () => {
  S.prefs.threads = !S.prefs.threads; S.open.clear();
  renderThreadToggle(); renderList(); renderSelection();
  if (S.selected) selectMessage(S.selected, { noScroll: true });
  try { S.prefs = await api('setPrefs', { values: { threads: S.prefs.threads } }); } catch (err) { fail(err); }
};

// S/MIME signature badge: valid (with any warnings), invalid, or unverifiable.
const SIG_REASONS = { SIGNER_CERT_MISSING: '邮件中没有附带签名证书', SIGNATURE_ALGORITHM_UNSUPPORTED: '签名算法暂不支持',
  SIGNED_CONTENT_MISSING: '签名内容缺失', INVALID_SIGNED_MESSAGE: '签名结构无法识别' };
function signatureBanner(s) {
  if (!s) return '';
  if (s.status === 'invalid') return '<div class="banner bad"><i data-icon="alert"></i><span>签名无效：邮件内容在签名后被修改过，请谨慎对待。</span></div>';
  if (s.status !== 'valid') return `<div class="banner"><i data-icon="alert"></i><span>邮件带有数字签名，但无法验证（${esc(SIG_REASONS[s.code] || '原因未知')}）。</span></div>`;
  const warnings = [];
  if (s.emailMatch === false) warnings.push('签名证书上的邮箱与发件人不一致');
  if (s.timeValid === false) warnings.push('签名证书不在有效期内');
  const text = `签名有效，证书由 ${esc(s.issuer || '未知机构')} 签发` + (s.directory ? '，与公司通讯录中的证书一致' : '');
  const title = esc([s.subject, ...(s.emails || []), s.digest ? '摘要算法 ' + s.digest.toUpperCase() : ''].filter(Boolean).join('\n'));
  return warnings.length
    ? `<div class="banner" title="${title}"><i data-icon="alert"></i><span>${text}；但${esc(warnings.join('，'))}。</span></div>`
    : `<div class="banner ok" title="${title}"><i data-icon="seal"></i><span>${text}。</span></div>`;
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
    return true;
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
  S.local = 'drafts'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
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
// ---------------------------------------------------------------- scheduled mail (server drafts with a send time)
async function refreshScheduled() {
  try {
    const res = await api('scheduled');
    $('#scheduled-count').textContent = res.rows.length || '';
    if (S.local === 'scheduled') { S.rows = res.rows; S.total = res.rows.length; S.more = false; renderList(); $('#list-sub').textContent = res.rows.length ? `${res.rows.length} 封等待发出` : ''; }
  } catch (e) { if (S.local === 'scheduled') $('#mail-list').innerHTML = `<div class="list-foot">${esc(e.message)}</div>`; }
}
$('#nav-scheduled').onclick = () => {
  S.local = 'scheduled'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
  S.rows = []; renderFolders();
  $('#list-title').textContent = '定时发送'; $('#list-sub').textContent = '正在读取服务器草稿箱…';
  $('#mail-list').innerHTML = '<div class="skeleton"></div>'.repeat(3);
  refreshScheduled();
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
    const box = mode === 'new' && !mid ? currentBox() : null;
    const draft = await api('compose', box ? { mode, mid, mailbox: box.id } : { mode, mid });
    Object.assign(draft, extra);
    openComposer(draft);
  } catch (err) { fail(err); }
}

function openComposer(draft) {
  C.draft = draft; C.dirty = false; C.encrypt = draft.encrypt === true ? true : draft.encrypt === false ? false : null;
  C.sign = draft.sign === true ? true : draft.sign === false ? false : null;
  const box = $('#composer');
  box.classList.remove('hidden');
  $('#cmp-title').textContent = { new: '写邮件', reply: '回复', reply_all: '全部回复', forward: '转发', forward_attach: '作为附件转发' }[draft.title || draft.mode] || '写邮件';
  $('#cmp-status').textContent = '';
  const ids = draft.fromChoices || S.profile.identities || [S.profile.account];
  $('#cmp-from').innerHTML = ids.map((i) => `<option${i === draft.from ? ' selected' : ''}>${esc(i)}</option>`).join('');
  $('#row-from').classList.toggle('hidden', ids.length < 2);
  for (const field of ['to', 'cc', 'bcc']) setChips(field, draft[field] || []);
  $('#row-cc').classList.toggle('hidden', !(draft.cc || []).length);
  $('#row-bcc').classList.toggle('hidden', !(draft.bcc || []).length);
  $('#cmp-subject').value = draft.subject || '';
  $('#cmp-editor').innerHTML = draft.html || '<p><br></p>';
  renderAtts();
  renderEncrypt();
  $('#cmp-receipt').checked = !!draft.receipt;
  renderSchedule();
  composerOpened(draft);
  mentionsReset();
  setTimeout(() => {
    if (!(draft.to || []).length) $('.chips[data-field="to"] input').focus();
    else { const ed = $('#cmp-editor'); ed.focus(); const r = document.createRange(); r.setStart(ed, 0); r.collapse(true); const s = getSelection(); s.removeAllRanges(); s.addRange(r); }
  }, 30);
  clearInterval(C.timer);
  C.timer = setInterval(() => { if (C.dirty) saveDraft(true, false); }, 20000);  // Autosave stays local.
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
  if (C.draft) setTimeout(renderSchedule, 0);
  if (!C.draft) return;
  const on = encryptOn();
  const external = allRecipients().filter((a) => !isInternal(a));
  $('#cmp-encrypt').classList.toggle('on', on);
  $('#cmp-encrypt span').textContent = on ? '加密发送' : '不加密';
  $('#cmp-enc-hint').textContent = C.encrypt === null || C.encrypt === undefined
    ? (on ? '收件人均为同事，自动加密' : external.length ? '含外部收件人，默认不加密' : '')
    : '已手动' + (on ? '开启' : '关闭');
  const sign = signOn();
  $('#cmp-sign').classList.toggle('on', sign);
  $('#cmp-sign span').textContent = sign ? '签名' : '不签名';
}
$('#cmp-encrypt').onclick = () => { C.encrypt = !encryptOn(); C.dirty = true; renderEncrypt(); };
// ---- signature: follows encryption unless toggled on its own
function signOn() { return C.sign === null || C.sign === undefined ? encryptOn() : C.sign; }
$('#cmp-sign').onclick = () => { C.sign = !signOn(); C.dirty = true; renderEncrypt(); };
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
// opts.pick replaces adding a chip (group editor, @mention); opts.rect places the list.
function showSuggest(box, input, items, opts = {}) {
  sug.items = items; sug.index = 0; sug.box = box; sug.input = input; sug.pick = opts.pick || null;
  if (!items.length) { hideSuggest(); return; }
  const r = opts.rect || input.getBoundingClientRect();
  const el = $('#suggest');
  el.style.left = r.left + 'px'; el.style.top = r.bottom + 4 + 'px';
  el.classList.remove('hidden');
  drawSuggest();
}
function drawSuggest() {
  const el = $('#suggest');
  el.innerHTML = sug.items.map((p, i) => `<div class="sug${i === sug.index ? ' on' : ''}" data-i="${i}">${avatarHtml(p.name, p.email, 'sm')}
    <div class="t"><div>${esc(p.name || p.email)}</div><div class="muted small">${esc(p.email)}</div></div>
    <span class="src">${{ directory: '通讯录', recipient: '收件人' }[p.source] || '最近联系'}</span></div>`).join('');
  $$('.sug', el).forEach((d) => (d.onmousedown = (e) => { e.preventDefault(); pickSuggest(+d.dataset.i); }));
}
function pickSuggest(i) {
  const item = sug.items[i], input = sug.input;
  if (sug.pick) { const pick = sug.pick; hideSuggest(); pick(item); } else { addChip(sug.box, input, item); input.focus(); }
}
function hideSuggest() { $('#suggest').classList.add('hidden'); sug.input = null; sug.pick = null; }

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
  let files = Array.from(e.dataTransfer.files);
  if (e.target.closest && e.target.closest('#cmp-editor')) {
    // Pictures dropped onto the text go into the body where they were dropped.
    const images = files.filter(isBodyImage);
    files = files.filter((f) => !isBodyImage(f));
    if (images.length) await insertImageFiles(images, document.caretRangeFromPoint(e.clientX, e.clientY));
  }
  if (files.length) addAttachments(await Promise.all(files.map(readFile)));
});
$('#cmp-editor').addEventListener('paste', async (e) => {
  // Excel and Word put an HTML table AND a picture of it on the clipboard: when HTML
  // is there, let the normal paste keep the table. A pure image (a screenshot) goes
  // into the body; other pasted files become attachments.
  if (Array.from(e.clipboardData.types || []).includes('text/html')) return;
  const files = Array.from(e.clipboardData.files || []);
  if (!files.length) return;
  e.preventDefault();
  const images = files.filter(isBodyImage), others = files.filter((f) => !isBodyImage(f));
  if (images.length) await insertImageFiles(images);
  if (others.length) { addAttachments(await Promise.all(others.map(readFile))); toast('粘贴的文件已作为附件添加'); }
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

// send: the copy that leaves (mentions lose their editing-only attributes).
function collectDraft(send = false) {
  const html = cleanPicked($('#cmp-editor').innerHTML);
  const value = { ...C.draft, from: $('#cmp-from').value || C.draft.from, to: chipsValue('to'), cc: chipsValue('cc'), bcc: chipsValue('bcc'),
    subject: $('#cmp-subject').value, html: send ? outgoingHtml(html) : html };
  value.encrypt = encryptOn();
  value.sign = signOn();
  value.receipt = $('#cmp-receipt').checked;
  value.scheduleAt = C.draft.scheduleAt || null;
  return value;
}

// Scheduled send: the time is picked in local time and travels as epoch milliseconds.
function pad2(n) { return String(n).padStart(2, '0'); }
function localInput(d) { return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`; }
function scheduleText(ms) {
  const d = new Date(ms), now = new Date(), tomorrow = new Date(now.getTime() + 86400000);
  const day = sameDay(d, now) ? '今天' : sameDay(d, tomorrow) ? '明天' : `${d.getMonth() + 1}月${d.getDate()}日`;
  return `${d.getFullYear() !== now.getFullYear() ? d.getFullYear() + '年' : ''}${day} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}
function renderSchedule() {
  const at = C.draft && C.draft.scheduleAt;
  const chip = $('#cmp-schedule-chip');
  chip.classList.toggle('hidden', !at);
  $('#cmp-schedule').classList.toggle('hidden', !!at);
  if (at) $('span', chip).textContent = scheduleText(at) + ' 发送';
  const clash = !!at && encryptOn();
  chip.classList.toggle('warn', clash);
  chip.title = clash ? '服务器不接受加密邮件的定时发送：发送时会请你选择立即加密发送' : '点击修改定时时间';
  const btn = $('#cmp-send');
  if (!btn.disabled) { btn.innerHTML = at ? '<i data-icon="clock"></i>定时发送' : '<i data-icon="send"></i>发送'; renderIcons(btn); }
  renderIcons(chip);
}
async function pickSchedule() {
  const now = new Date();
  const at = (h, m, days = 0) => { const d = new Date(now); d.setDate(d.getDate() + days); d.setHours(h, m, 0, 0); return d; };
  const quick = [['1 小时后', new Date(Math.ceil((now.getTime() + 3600000) / 300000) * 300000)],
    ...(now.getHours() < 17 ? [['今天 18:00', at(18, 0)]] : []), ['明天 09:00', at(9, 0, 1)], ['明天 14:00', at(14, 0, 1)]];
  const start = C.draft.scheduleAt ? new Date(C.draft.scheduleAt) : quick[0][1];
  const value = await promptDialog({ title: '定时发送', okText: '确定',
    text: '邮件会先保存在服务器的草稿箱，到时间由服务器发出；在此之前可以随时取消。',
    body: `<div class="sched-form"><div class="sched-quick">${quick.map(([label, d]) => `<button data-at="${d.getTime()}">${label}</button>`).join('')}</div>
      <input type="datetime-local" id="sched-at" value="${localInput(start)}" min="${localInput(now)}"></div>`,
    onOpen: (d) => $$('[data-at]', d).forEach((b) => (b.onclick = () => { $('#sched-at').value = localInput(new Date(+b.dataset.at)); })),
    collect: () => { const v = $('#sched-at').value; return v ? new Date(v).getTime() : NaN; } });
  if (value === null) return;
  if (!(value > Date.now() + 120000)) { toast('定时发送时间至少要在 2 分钟之后', { error: true }); return; }
  if (value > Date.now() + 365 * 86400000) { toast('定时发送时间不能超过一年', { error: true }); return; }
  C.draft.scheduleAt = value; C.dirty = true; renderSchedule();
}
$('#cmp-schedule').onclick = pickSchedule;
$('#cmp-schedule-chip span').onclick = pickSchedule;
$('#cmp-schedule-chip button').onclick = () => { C.draft.scheduleAt = null; C.dirty = true; renderSchedule(); };
$('#cmp-receipt').onchange = () => { C.dirty = true; };
// server: a draft opened from the server's 草稿箱 is written back there (never when encrypted).
async function saveDraft(quiet, server = !!(C.draft && C.draft.serverDraftId)) {
  if (!C.draft) return null;
  try {
    const res = await api('saveDraft', { value: collectDraft(), server });
    C.draft.revision = res.revision; C.dirty = false;
    const time = new Date().toTimeString().slice(0, 5);
    if (res.serverDraftId) { C.draft.serverDraftId = res.serverDraftId; serverDraftSaved(); }
    $('#cmp-status').textContent = res.serverError ? '已保存到本机 ' + time : (res.serverDraftId ? '已保存到服务器草稿箱 ' : '已保存 ') + time;
    if (res.serverError) toast(res.serverError, { error: res.serverErrorCode !== 'DRAFT_ENCRYPTED_LOCAL' });
    else if (!quiet) toast(res.serverDraftId ? '草稿已保存到服务器草稿箱' : '草稿已保存到本机');
    refreshDraftCount();
    return res;
  } catch (err) { $('#cmp-status').textContent = '草稿保存失败'; if (!quiet) fail(err); return null; }
}
async function sendMail() {
  const value = collectDraft(true);
  if (!value.to.length && !value.cc.length && !value.bcc.length) { toast('请至少填写一个收件人', { error: true }); $('.chips[data-field="to"] input').focus(); return; }
  const bad = [...value.to, ...value.cc, ...value.bcc].map((a) => parseAddressList(a)[0]).filter((p) => !EMAIL_RE.test(p.email));
  if (bad.length) { toast(`收件人地址无效：${bad[0].email}`, { error: true }); return; }
  if (value.scheduleAt && value.scheduleAt < Date.now() + 60000) { toast('定时发送时间已过，请重新选择', { error: true }); pickSchedule(); return; }
  if (value.scheduleAt && value.encrypt) {
    // The server accepts scheduling only for ordinary mail; never fall back to plaintext silently.
    const now = await promptDialog({ title: '加密邮件不能定时发送', okText: '立即加密发送', collect: () => true,
      text: '服务器只接受普通邮件的定时发送。你可以现在就加密发送，或者取消后关闭加密再定时（关闭加密需要你自己确认）。' });
    if (!now) return;
    C.draft.scheduleAt = null; value.scheduleAt = null; renderSchedule();
  }
  if (!value.subject.trim() && !(await confirmDialog('没有主题', '这封邮件没有主题，仍要发送吗？', '仍然发送'))) return;
  const text = $('#cmp-editor').innerText;
  if (/附件|attach/i.test(text.split('原始邮件')[0]) && !(value.attachments || []).length &&
      !(await confirmDialog('忘记添加附件？', '正文提到了“附件”，但还没有添加附件。仍要发送吗？', '仍然发送'))) return;
  const btn = $('#cmp-send'); btn.disabled = true; btn.innerHTML = value.scheduleAt ? '<i data-icon="clock"></i>提交中…' : '<i data-icon="send"></i>发送中…'; renderIcons(btn);
  $('#cmp-status').textContent = (value.attachments || []).length ? '正在上传附件并发送…' : '正在发送…';
  try {
    const res = await api('send', { value, hold: true });
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
    if (res.state === 'NeedsSigningKey') {
      $('#cmp-status').textContent = '签名需要解锁个人证书';
      const go = await promptDialog({ title: '需要个人证书', text: '这封邮件要求数字签名，需要先解锁你的个人证书。',
        body: '<p class="muted small">如果不需要签名，可以关闭写信窗口顶部的“签名”后再发送。</p>',
        okText: '解锁证书…', collect: () => true });
      if (go && (await unlockCertificate())) setTimeout(sendMail, 0);
      return;
    }
    const meta = { encrypt: value.encrypt, sign: value.sign, subject: value.subject };
    if (res.state === 'Held') {
      // 撤销发送: the composer closes; the bar counts down and can bring it back.
      closeComposer(true);
      showHold(res, meta);
    } else if (res.state === 'Accepted' || res.state === 'Queued') {
      closeComposer(true);
      sendOutcome(res, meta);
    } else if (res.state === 'Unknown') {
      $('#cmp-status').textContent = '发送结果待确认';
      sendOutcome(res, meta);
    } else {
      $('#cmp-status').textContent = '发送失败，草稿已保留';
      fail({ message: res.failureMessage || '发送失败，草稿已保存在本机，可修改后重试。' });
    }
  } catch (err) { $('#cmp-status').textContent = '发送失败，草稿已保留'; fail(err); }
  finally { btn.disabled = false; renderSchedule(); }
}
function closeComposer(sent) {
  clearInterval(C.timer);
  $('#composer').classList.add('hidden'); $('#composer').classList.remove('max'); hideSuggest();
  if (!sent && C.dirty) saveDraft(true).then((res) => res && !res.serverError && toast(res.serverDraftId ? '已保存到服务器草稿箱' : '已保存到本机草稿'));
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

// ---------------------------------------------------------------- templates (local, encrypted)
async function loadTemplates() { try { S.templates = await api('templates'); } catch (e) { S.templates = []; } return S.templates; }
async function pickTemplate(title) {
  await loadTemplates();
  if (!S.templates.length) { toast('还没有模板：在写信窗口点「模板」→「将当前邮件存为模板」'); return null; }
  return promptDialog({ title,
    body: '<div class="pick-list tpl-pick">' + S.templates.map((t, i) => `<button data-i="${i}"><i data-icon="template"></i><span class="tpl-name">${esc(t.name)}</span><span class="muted small tpl-sub">${esc(t.subject || '（无主题）')}</span></button>`).join('') + '</div>',
    onOpen: (d, done) => $$('[data-i]', d).forEach((b) => (b.onclick = () => done(S.templates[+b.dataset.i]))) });
}
async function newFromTemplate() {
  const t = await pickTemplate('从模板新建'); if (!t) return;
  startCompose('new', null, { subject: t.subject, html: t.html, to: t.to, cc: t.cc, bcc: t.bcc });
}
async function applyTemplate(t) {
  const body = $('#cmp-editor').innerText.replace(/\s+/g, '');
  const typed = C.dirty && (body || $('#cmp-subject').value.trim());
  if (typed && !(await confirmDialog('使用模板', `用模板「${t.name}」替换当前的主题和正文吗？`, '替换'))) return;
  if (t.subject) $('#cmp-subject').value = t.subject;
  $('#cmp-editor').innerHTML = t.html || '<p><br></p>';
  for (const field of ['to', 'cc', 'bcc']) {
    const have = new Set(chipsValue(field).map((a) => (parseAddressList(a)[0] || {}).email));
    const box = $(`.chips[data-field="${field}"]`), input = $('input', box);
    for (const a of t[field] || []) { const p = parseAddressList(a)[0]; if (p && !have.has(p.email)) addChip(box, input, p); }
    if ((t[field] || []).length && field !== 'to') $('#row-' + field).classList.remove('hidden');
  }
  C.dirty = true; renderEncrypt();
}
async function saveAsTemplate() {
  const value = collectDraft();
  const got = await promptDialog({ title: '存为模板', okText: '保存',
    text: '保存当前邮件的主题和正文，以后可以从模板新建。模板只保存在本机，已加密；附件不会保存。',
    body: `<input type="text" id="tpl-name" maxlength="60" placeholder="模板名称" value="${esc(value.subject.slice(0, 60))}">
      <label class="check" style="display:flex;gap:8px;margin-top:10px"><input type="checkbox" id="tpl-rcpt"> 同时保存收件人、抄送和密送</label>`,
    collect: () => ({ name: $('#tpl-name').value.trim(), recipients: $('#tpl-rcpt').checked }) });
  if (!got) return;
  if (!got.name) { toast('请填写模板名称', { error: true }); return; }
  await loadTemplates();
  if (S.templates.some((t) => t.name.toLowerCase() === got.name.toLowerCase()) &&
      !(await confirmDialog('替换模板', `已有名为「${got.name}」的模板，要用当前邮件替换它吗？`, '替换'))) return;
  try {
    await api('saveTemplate', { name: got.name, recipients: got.recipients,
      value: { subject: value.subject, html: value.html, to: value.to, cc: value.cc, bcc: value.bcc } });
    loadTemplates(); toast(`已存为模板「${got.name}」`);
  } catch (err) { fail(err); }
}
async function templateMenu(button) {
  await loadTemplates();
  const r = button.getBoundingClientRect();
  const used = S.templates.slice(0, 12).map((t) => ({ label: t.name, icon: 'template', run: () => applyTemplate(t) }));
  showMenu(r.left, r.top, [...used, ...(used.length ? ['-'] : []),
    { label: '将当前邮件存为模板…', icon: 'draft', run: saveAsTemplate },
    ...(S.templates.length ? [{ label: '管理模板…', icon: 'settings', run: () => { openSettings(); $('#tpl-section').scrollIntoView(); } }] : [])]);
  const m = $('#menu'), h = m.getBoundingClientRect().height;
  m.style.top = Math.max(8, r.top - h - 4) + 'px';  // Opens upwards from the composer foot.
}
$('#cmp-template').onclick = (e) => templateMenu(e.currentTarget);
$('#btn-compose-more').onclick = (e) => {
  const r = e.currentTarget.getBoundingClientRect();
  showMenu(r.left, r.bottom + 4, [{ label: '写邮件', icon: 'pen', run: () => startCompose('new') },
    { label: '从模板新建…', icon: 'template', run: newFromTemplate },
    '-', { label: '打开 .eml 邮件文件…', icon: 'open', run: importEml }]);
};
function renderTemplates() {
  const box = $('#tpl-list');
  box.innerHTML = S.templates.length ? S.templates.map((t) => `<div class="tpl-row" data-id="${esc(t.id)}"><i data-icon="template"></i>
      <div class="tpl-text"><b>${esc(t.name)}</b><span class="muted small">${esc(t.subject || '（无主题）')}${(t.to || []).length ? ' · 含收件人' : ''} · ${esc(t.modified)}</span></div>
      <button class="btn ghost sm" data-tpl="rename">重命名</button><button class="btn ghost sm danger" data-tpl="delete">删除</button></div>`).join('')
    : '<p class="muted small">还没有模板。</p>';
  renderIcons(box);
}
$('#tpl-list').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-tpl]'); if (!b) return;
  const t = S.templates.find((x) => x.id === b.closest('.tpl-row').dataset.id); if (!t) return;
  try {
    if (b.dataset.tpl === 'rename') {
      const name = await promptDialog({ title: '重命名模板', okText: '保存', body: `<input type="text" id="tpl-rename" maxlength="60" value="${esc(t.name)}">`,
        collect: () => $('#tpl-rename').value.trim() });
      if (!name || name === t.name) return;
      S.templates = await api('renameTemplate', { template_id: t.id, name });
    } else {
      if (!(await confirmDialog('删除模板', `确定删除模板「${t.name}」吗？`, '删除'))) return;
      S.templates = await api('deleteTemplate', { template_id: t.id });
    }
    renderTemplates();
  } catch (err) { fail(err); }
});

// ---------------------------------------------------------------- settings
function openSettings() {
  const p = S.prefs;
  const d = $('#settings'); d.classList.remove('hidden'); renderIcons(d);
  $$('#theme-seg button').forEach((b) => b.classList.toggle('on', b.dataset.theme === p.theme));
  $$('#density-seg button').forEach((b) => b.classList.toggle('on', b.dataset.density === p.density));
  $('#pref-poll').value = String(p.pollSeconds);
  $('#pref-notify').checked = !!p.notify; $('#pref-markread').checked = !!p.markReadOnOpen;
  $('#pref-autostart').checked = !!p.autostart;
  $('#pref-undo').value = String(p.undoSeconds ?? 10);
  $('#pref-signature').innerHTML = p.signature || '';
  $('#settings-account').textContent = `${S.profile.name || ''} ${S.profile.account}`;
  renderCertStatus();
  renderTemplates(); loadTemplates().then(renderTemplates);
  renderGroups(); loadGroups().then(renderGroups);
  api('runtimeStatus').then((r) => { $('#runtime-note').classList.toggle('hidden', r.sandboxed); $('#data-dir').textContent = '本机数据（已加密）：' + r.dataDir; }).catch(() => {});
  api('certificateStatus').then((c) => { S.profile.certificate = c; renderCertStatus(); }).catch(() => {});
}
function renderCertStatus() {
  const c = S.profile.certificate || {};
  $('#cert-status').textContent = c.unlocked ? `个人证书已解锁 · 有效期至 ${c.expires}${c.remembered ? ' · 已记住' : ''}` : '个人证书未解锁（不影响加密发送；阅读加密邮件和签名发送需要解锁）';
  $('#btn-cert-forget').classList.toggle('hidden', !c.remembered && !c.unlocked);
}
$('#btn-cert-forget').onclick = async () => { S.profile.certificate = await api('forgetCertificate').catch(() => S.profile.certificate); renderCertStatus(); toast('已忘记证书'); };
$$('#theme-seg button').forEach((b) => (b.onclick = () => { $$('#theme-seg button').forEach((x) => x.classList.toggle('on', x === b)); applyTheme(b.dataset.theme); }));
$$('#density-seg button').forEach((b) => (b.onclick = () => { $$('#density-seg button').forEach((x) => x.classList.toggle('on', x === b)); document.documentElement.dataset.density = b.dataset.density; }));
$('#btn-account').onclick = openSettings;
$$('#settings [data-close]').forEach((b) => (b.onclick = () => { $('#settings').classList.add('hidden'); applyTheme(S.prefs.theme, S.prefs.density); }));
$('#btn-save-settings').onclick = async () => {
  const values = { theme: $('#theme-seg .on').dataset.theme, density: $('#density-seg .on').dataset.density, pollSeconds: +$('#pref-poll').value,
    notify: $('#pref-notify').checked, markReadOnOpen: $('#pref-markread').checked, autostart: $('#pref-autostart').checked, undoSeconds: +$('#pref-undo').value, signature: signatureHtml() };
  try {
    S.prefs = await api('setPrefs', { values });
    applyTheme(S.prefs.theme, S.prefs.density);
    api('startSync', { seconds: S.prefs.pollSeconds });
    $('#settings').classList.add('hidden'); toast(S.prefs.autostart === values.autostart ? '设置已保存' : '设置已保存，但本机不允许修改开机启动项');
  } catch (err) { fail(err); }
};
$('#btn-cert').onclick = () => { $('#settings').classList.add('hidden'); unlockCertificate(); };
$('#btn-logout').onclick = async () => {
  if (!(await confirmDialog('退出登录', '退出后需要重新输入密码。本机缓存保持加密保存。', '退出登录'))) return;
  $('#settings').classList.add('hidden');
  await api('logout', { forget: true }).catch(() => {});
  await api('closeReaders').catch(() => {});
  location.reload();
};

// ---------------------------------------------------------------- keyboard
function handleKey(e) {
  const t = e.target;
  if (!$('#prompt').classList.contains('hidden') || !$('#settings').classList.contains('hidden')) {
    if (e.key === 'Escape') { $$('#prompt [data-close], #settings [data-close]').forEach((b) => !b.closest('.hidden') && b.click()); }
    return;
  }
  if (READER_MID) { readerKey(e); return; }
  if (e.ctrlKey && (e.key === 'p' || e.key === 'P')) { e.preventDefault(); if (S.message && S.multi.size < 2 && !$('#reader-body').classList.contains('hidden')) printMessage(S.message); return; }
  if (e.key === 'Escape') { const menu = !$('#menu').classList.contains('hidden'); hideMenu(); closeFind(); if (!isTyping(t)) clearSelection(); void menu; }
  if (e.ctrlKey && (e.key === 'a' || e.key === 'A') && !isTyping(t) && t.ownerDocument === document && !(t.closest && t.closest('.reader-scroll, #composer'))
      && !$('#app').classList.contains('hidden') && !S.local) { e.preventDefault(); selectAll(); return; }
  if (e.ctrlKey && (e.key === 'n' || e.key === 'N')) { e.preventDefault(); startCompose('new'); return; }
  if (e.key === 'F5') { e.preventDefault(); $('#btn-refresh').click(); return; }
  if ((e.ctrlKey && (e.key === 'f' || e.key === 'F')) || (e.key === '/' && !isTyping(t))) { e.preventDefault(); $('#search').focus(); $('#search').select(); return; }
  if (isTyping(t) || e.ctrlKey || e.altKey || $('#app').classList.contains('hidden')) return;
  const m = S.message;
  // J/K walk the lines as shown: list rows, and the messages of expanded conversations.
  const lines = $$('#mail-list .row').filter((el) => !el.classList.contains('open'));
  let i = lines.findIndex((el) => el.dataset.id === S.selected);
  if (i < 0) i = lines.findIndex((el) => !el.dataset.child && itemOf(S.selected).ids.includes(el.dataset.id));
  const go = (el) => el && selectMessage(el.dataset.id, { child: !!el.dataset.child });
  const k = e.key;
  const many = S.multi.size >= 2;
  const conv = S.items.find((it) => it.key && it.ids.includes(S.selected));
  if (k === 'j' || k === 'ArrowDown') { e.preventDefault(); go(lines[Math.min(lines.length - 1, i + 1)]); }
  else if (k === 'k' || k === 'ArrowUp') { e.preventDefault(); go(lines[Math.max(0, i - 1)]); }
  else if (k === 'ArrowRight' && conv && !threadOf(S.selected) && threadSize(conv) > 1) { e.preventDefault(); toggleThread(conv.id, true); }
  else if (k === 'ArrowLeft' && threadOf(S.selected)) { e.preventDefault(); const it = threadOf(S.selected); toggleThread(it.id, false); selectMessage(it.id); }
  else if (k === 'c') startCompose('new');
  else if (k === 'Enter' && i >= 0 && !many && (t === document.body || t.closest('#mail-list'))) { e.preventDefault(); popOut(S.selected); }
  else if (k === 'x') { if (S.selected && !S.local) toggleItem(S.selected); }
  else if (many && (k === 'Delete' || k === '#')) batchMove(null, { delete: true });
  else if (many && k === 'u') batchFlags({ read: false });
  else if (many && k === 's') batchFlags({ flagged: !selectedRows().every((r) => r.flags.flagged) });
  else if (many && k === 'v') pickBatchFolder();
  else if (many || !m) return;
  else if (k === 'r') startCompose('reply', m.id);
  else if (k === 'a') startCompose('reply_all', m.id);
  else if (k === 'f') startCompose('forward', m.id);
  else if (k === 'Delete' || k === '#') deleteMessage(m.id);
  else if (k === 'u') setFlags(m.id, { read: false });
  else if (k === 's') setFlags(m.id, { flagged: !m.flags.flagged });
  else if (k === 'v') pickFolder(m.id);
}
// A pop-out window: the open message only. Escape or Ctrl+W closes the window.
function readerKey(e) {
  const t = e.target, k = e.key, m = S.message;
  if (e.ctrlKey && (k === 'p' || k === 'P')) { e.preventDefault(); if (m) printMessage(m); return; }
  if (k === 'Escape') {
    if (!$('#menu').classList.contains('hidden') || !$('#find-bar').classList.contains('hidden')) { hideMenu(); closeFind(); }
    else if ($('#composer').classList.contains('hidden')) api('closeWindow');
    return;
  }
  if (isTyping(t) || e.ctrlKey || e.altKey || !m || !$('#composer').classList.contains('hidden')) return;
  const act = { r: 'reply', a: 'reply_all', f: 'forward', Delete: 'delete', '#': 'delete', u: 'unread', s: 'flag', v: 'move' }[k];
  if (!act) return;
  e.preventDefault();
  if (['reply', 'reply_all', 'forward'].includes(act)) startCompose(act, m.id); else readerAct(act, m);
}
function isTyping(t) { return t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable); }
document.addEventListener('keydown', handleKey);
document.addEventListener('click', (e) => { if (!e.target.closest('#menu')) hideMenu(); if (!e.target.closest('#suggest') && !e.target.closest('.chips')) hideSuggest(); });
document.addEventListener('contextmenu', (e) => { if (!isTyping(e.target) && !e.target.closest('.reader-scroll')) e.preventDefault(); });

// ---------------------------------------------------------------- push events from Python
on('synced', (v) => {
  if (v.shared) S.shared = v.shared;
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
on('authRequired', () => {
  if (READER_MID) { api('notifyMain', { name: 'authRequired' }).catch(() => {}); api('closeWindow'); return; }
  api('closeReaders').catch(() => {});
  showLogin('会话已失效，请重新登录。');
});
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
    if (READER_MID) { await enterReader(READER_MID); return; }
    const saved = await api('savedLogin').catch(() => ({}));
    if (saved.remember) {
      try { await enterApp(await api('autoLogin')); return; }
      catch (err) { showLogin(err.message); return; }
    }
    showLogin();
  });
})();
