'use strict';
/* 鸿雁 · 本地存档: the official client's archived mail (read-only), its settings and
   scanning. Rows come from the backend's encrypted catalog and open like other mail
   (as read-only messages). Uses app.js globals. */

S.archive = { folder: '', messages: 0, scanning: null };

function renderArchiveNav() {
  const a = S.archive;
  $('#nav-archive').classList.toggle('hidden', !a.folder);
  $('#nav-archive').classList.toggle('on', S.local === 'archive');
  $('#archive-count').textContent = a.scanning ? '…' : '';
}
const _archiveRenderFolders = renderFolders;
renderFolders = function () { _archiveRenderFolders(); renderArchiveNav(); };

function archiveStatusText(a) {
  if (!a.folder) return '未设置。';
  if (a.scanning) return a.scanning.total ? `正在扫描：${a.scanning.done} / ${a.scanning.total} 个存档包…` : '正在扫描…';
  const last = a.lastScan;
  if (!last) return '还没有扫描。';
  return `已收录 ${a.messages} 封邮件（${last.packs} 个存档包），上次扫描 ${last.at}` +
    (last.failedCount ? `；${last.failedCount} 处无法读取，已跳过` : '') + '。';
}
function applyArchiveStatus(a) {
  S.archive = { ...S.archive, ...a };
  if (!$('#settings').classList.contains('hidden') || a.folder !== undefined) {
    if (document.activeElement !== $('#archive-folder')) $('#archive-folder').value = S.archive.folder || '';
    $('#archive-status').textContent = archiveStatusText(S.archive);
    $('#archive-rescan').classList.toggle('hidden', !S.archive.folder);
    $('#archive-off').classList.toggle('hidden', !S.archive.folder);
  }
  renderArchiveNav();
}

async function scanArchive(quiet) {
  if (S.archive.scanning) return;
  applyArchiveStatus({ scanning: { done: 0, total: 0 } });
  try {
    const result = await api('archiveScan');
    applyArchiveStatus({ ...result, scanning: null });
    if (!quiet) toast(`本地存档扫描完成：共 ${result.messages} 封` + (result.failed.length ? `，${result.lastScan.failedCount} 处无法读取` : ''));
    if (S.local === 'archive') showArchive();
  } catch (err) {
    applyArchiveStatus({ scanning: null });
    if (!quiet || err.code !== 'ARCHIVE_SCANNING') fail(err);
  }
}
on('archiveProgress', (p) => { if (p) applyArchiveStatus({ scanning: p }); });

async function showArchive() {
  S.local = 'archive'; S.search = null; S.listToken++; S.multi.clear(); renderSelection();
  const token = S.listToken;
  S.rows = []; S.more = false; S.total = null; $('#search').value = '';
  renderFolders();
  $('#list-title').textContent = '本地存档';
  $('#list-sub').textContent = S.archive.scanning ? '正在扫描…' : '只读 · 只在这台电脑上';
  $('#mail-list').innerHTML = '<div class="skeleton"></div>'.repeat(4);
  try {
    const view = await api('archiveView');
    if (token !== S.listToken) return;
    S.rows = view.rows; S.total = view.total;
    renderList();
    $('#list-sub').textContent = view.total ? `${view.total} 封 · 只读，只在这台电脑上` : '';
    if (!view.total) $('#mail-list').innerHTML = `<div class="list-foot">${S.archive.scanning ? '正在扫描本地存档，完成后显示在这里' : '本地存档里还没有邮件。请在「设置 → 本地存档」中扫描。'}</div>`;
  } catch (err) { if (token === S.listToken) $('#mail-list').innerHTML = `<div class="list-foot">${esc(err.message)}</div>`; }
}
$('#nav-archive').onclick = showArchive;

// ---------------------------------------------------------------- settings
function renderArchiveSettings() { api('archiveStatus').then(applyArchiveStatus).catch(() => {}); }
const _archiveOpenSettings = openSettings;
openSettings = function (...args) { const r = _archiveOpenSettings(...args); renderArchiveSettings(); return r; };
$('#archive-pick').onclick = async () => {
  const folder = await api('pickFolder', { title: '选择官方客户端的邮箱文件夹（其中有 archmails）' }).catch(fail);
  if (folder) $('#archive-folder').value = folder;
};
$('#archive-save').onclick = async () => {
  try {
    applyArchiveStatus(await api('setArchiveFolder', { folder: $('#archive-folder').value }));
    if (S.archive.folder) scanArchive(false); else toast('已停用本地存档');
  } catch (err) { fail(err); }
};
$('#archive-rescan').onclick = () => scanArchive(false);
$('#archive-off').onclick = async () => {
  if (!(await confirmDialog('停用本地存档', '左侧将不再显示「本地存档」，搜索和问邮箱也不再包含这些邮件。官方客户端的文件不受影响，以后可以重新选择。', '停用'))) return;
  try {
    applyArchiveStatus(await api('setArchiveFolder', { folder: '' }));
    if (S.local === 'archive') selectFolder(S.folderId);
  } catch (err) { fail(err); }
};

// On start: show the entry, and pick up newly archived mail (unchanged packs are skipped).
const _archiveEnterApp = enterApp;
enterApp = async function (...args) {
  const r = await _archiveEnterApp(...args);
  try {
    const status = await api('archiveStatus');
    applyArchiveStatus(status);
    if (status.folder && status.lastScan) setTimeout(() => scanArchive(true), 8000);
  } catch (e) { /* The archive is optional. */ }
  return r;
};
