/* 鸿雁 v0.5 · mail management: folders (create / rename / delete), 全部标为已读, spam reports
   and the sender blocklist / allowlist, permanent deletion in 已删除 and 垃圾邮件, and the
   attachment preview.
   Like rules.js it hooks into app.js by wrapping renderFolders / renderMessage / renderSelection /
   showMenu / moreMenu / deleteMessage / batchMove / readerAct, so app.js itself stays unchanged. */
'use strict';

ICONS.folderplus = '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M12 10v6M9 13h6"/>';
ICONS.checkall = '<path d="M2 12.5 6.5 17 15 8.5"/><path d="m11 16 1 1 10-10"/>';
ICONS.block = '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>';
ICONS.eye = '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>';
ICONS.purge = '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="m10 11 4 5M14 11l-4 5"/>';

const MG = {
  ctx: null,   // Row id of the row context menu being opened.
  more: null,  // Message of the 更多 menu being opened.
  role(fid) { return (folderById(fid) || {}).role; },
  purgeable(fid) { const f = folderById(fid); return !!f && !f.mailbox && (f.role === 'trash' || f.role === 'spam'); },
  current() { return !S.local && !S.search ? folderById(S.folderId) || null : null; },
  self(email) { const e = String(email || '').toLowerCase(); return !e || (S.profile.identities || []).concat(S.profile.account || []).some((i) => String(i).toLowerCase() === e); },

  // ------------------------------------------------------------ folders
  folderDialog(title, value, okText) {
    return promptDialog({ title, okText,
      body: `<input type="text" id="mg-fname" maxlength="40" spellcheck="false" placeholder="文件夹名称" value="${esc(value || '')}">
        <p class="muted small mg-hint">最长 12 个汉字或 24 个字母，不能包含 % ' " \\ / : * ? &lt; &gt; | = , ^</p>`,
      collect: () => $('#mg-fname').value.trim() || null });
  },
  // A name the server or the backend refuses reopens the dialog with what was typed.
  async nameLoop(title, value, okText, save) {
    for (let name = value; ;) {
      name = await this.folderDialog(title, name, okText);
      if (!name || name === value) return;
      try { await save(name); return; } catch (err) { fail(err); if (!['INVALID_FOLDER_NAME', 'FOLDER_EXISTS'].includes(err && err.code)) return; }
    }
  },
  createFolder(parent) {
    const p = parent ? folderById(parent) : null;
    return this.nameLoop(p ? `在「${p.name}」下新建文件夹` : '新建文件夹', '', '创建', async (name) => {
      applyFolders(await api('createFolder', { name, parent: p ? p.id : null })); toast(`已创建文件夹「${name}」`);
    });
  },
  renameFolder(f) {
    return this.nameLoop(`重命名文件夹「${f.name}」`, f.name, '保存', async (name) => {
      applyFolders(await api('renameFolder', { folder_id: f.id, name })); toast('文件夹已重命名');
    });
  },
  // Mail still in the folder is never deleted with it: it goes to 已删除 (recoverable) or
  // 收件箱 first, or the folder is kept.
  async deleteFolder(f) {
    if (S.folders.some((x) => x.parent === f.id)) { toast(`「${f.name}」下还有子文件夹，请先删除子文件夹。`, { error: true }); return; }
    const n = f.total || 0, names = { trash: '已删除', inbox: '收件箱' };
    let mail = 'trash';
    if (n) {
      mail = await promptDialog({ title: `删除文件夹「${f.name}」`, okText: '删除文件夹',
        text: `这个文件夹里还有 ${n} 封邮件。删除文件夹时，这些邮件怎么处理？`,
        body: `<label class="mg-choice"><input type="radio" name="mg-mail" value="trash" checked><span>删除文件夹及其中的 ${n} 封邮件<small class="muted">邮件移到「已删除」，仍可以在那里找回，不会彻底删除</small></span></label>
          <label class="mg-choice"><input type="radio" name="mg-mail" value="inbox"><span>删除文件夹，把 ${n} 封邮件移到「收件箱」</span></label>
          <label class="mg-choice"><input type="radio" name="mg-mail" value="keep"><span>保留文件夹和邮件，不删除</span></label>`,
        collect: () => $('input[name="mg-mail"]:checked').value });
      if (!names[mail]) return;
      toast(`正在把 ${n} 封邮件移到「${names[mail]}」…`);
    } else if (!(await confirmDialog(`删除文件夹「${f.name}」？`, '这个文件夹是空的，删除不会影响任何邮件。', '删除'))) return;
    try {
      const res = await api('deleteFolder', { folder_id: f.id, mail });
      const here = S.folderId === f.id && !S.local;
      applyFolders(res);
      toast(res.moved ? `已删除文件夹「${f.name}」，${res.moved} 封邮件已移到「${names[mail]}」` : `已删除文件夹「${f.name}」`);
      if (here) selectFolder((S.folders.find((x) => x.role === 'inbox') || S.folders[0]).id);
    } catch (err) { fail(err); this.refresh(); }
  },
  async markAllRead(fid) {
    const f = folderById(fid); if (!f) return;
    if (!f.unread) { toast(`「${f.name}」中没有未读邮件`); return; }
    try {
      const res = await api('markAllRead', { folder_id: f.id });
      applyFolders(res);
      S.rows.forEach((r) => { if (r.fid === f.id) r.flags.read = true; });
      if (S.message && S.message.fid === f.id) S.message.flags.read = true;
      renderList();
      toast(`已将「${f.name}」中的 ${res.done} 封邮件标为已读` + (res.failed ? `，${res.failed} 封未完成，请稍后重试` : ''), { error: !!res.failed });
    } catch (err) { fail(err); }
  },
  refresh(fid) {
    api('folders').then((x) => { S.folders = x; renderFolders(); }).catch(() => {});
    if (S.shared.length) loadShared();
    if (fid && fid === S.folderId && !S.local && !S.search) loadList(true);
  },
  folderItems(f) {
    const box = f.mailbox ? boxById(f.mailbox) : null, user = !box && !f.system && !f.role, items = [];
    if (user && (f.depth || 0) + 2 <= 5) items.push({ label: '新建子文件夹', icon: 'folderplus', run: () => this.createFolder(f.id) });
    if (user) items.push({ label: '重命名', icon: 'pen', run: () => this.renameFolder(f) }, { label: '删除文件夹…', icon: 'trash', run: () => this.deleteFolder(f) });
    if (items.length) items.push('-');
    if ((!box || box.rw) && f.role !== 'sent' && f.role !== 'drafts') items.push({ label: '全部标为已读', icon: 'checkall', run: () => this.markAllRead(f.id) });
    if (!box && (f.role === 'trash' || f.role === 'spam')) items.push({ label: `清空「${f.name}」…`, icon: 'purge', run: () => this.emptyFolder(f) });
    items.push({ label: '刷新', icon: 'refresh', run: () => this.refresh(f.id) });
    if (!box) items.push('-', { label: '新建文件夹', icon: 'folderplus', run: () => this.createFolder(null) });
    return items;
  },

  // ------------------------------------------------------------ permanent deletion
  // Two dialogs, each naming the count and that it cannot be undone.
  async confirmTwice(title, text, finalText) {
    if (!(await confirmDialog(title, `${text}此操作无法撤销，删除后无法找回。`, '继续'))) return false;
    const ok = $('#prompt-ok'); ok.classList.add('danger-solid');
    try { return !!(await confirmDialog('再次确认：永久删除', `${text}确定要永久删除吗？`, finalText)); } finally { ok.classList.remove('danger-solid'); }
  },
  async purge(ids, after) {
    const n = ids.length;
    if (!(await this.confirmTwice(`彻底删除 ${n} 封邮件？`, `${n} 封邮件将从服务器上永久删除，不会再出现在「已删除」中。`, `彻底删除 ${n} 封`))) return false;
    try {
      const res = await api('purge', { ids });
      applyFolders(res);
      if (after) after(res); else { removeRows(res.done); S.multi.clear(); renderSelection(); }
      toast(`已彻底删除 ${res.done.length} 封邮件` + (res.failed.length ? `，${res.failed.length} 封未完成` : ''), { error: !!res.failed.length });
      return true;
    } catch (err) { fail(err); return false; }
  },
  purgeOne(m) {
    return this.purge([m.id], (res) => {
      if (!res.done.length) return;
      if (READER_MID) { notifyMain({ kind: 'removed', id: m.id, from: m.fid, deleted: true }); api('closeWindow'); } else removeRow(m.id);
    });
  },
  async emptyFolder(f) {
    const n = f.total || 0;
    if (!n) { toast(`「${f.name}」已经是空的`); return; }
    if (!(await this.confirmTwice(`清空「${f.name}」？`, `「${f.name}」中的全部 ${n} 封邮件将从服务器上永久删除。`, `清空 ${n} 封邮件`))) return;
    try {
      const res = await api('emptyFolder', { folder_id: f.id });
      applyFolders(res);
      if (S.folderId === f.id && !S.local && !S.search) { S.rows = []; S.total = 0; S.selected = null; S.message = null; S.multi.clear(); renderList(); renderSelection(); }
      toast(`已清空「${f.name}」，永久删除了 ${res.removed} 封邮件`);
    } catch (err) { fail(err); }
  },

  // ------------------------------------------------------------ spam and sender lists
  async report(ids, spam, after) {
    const n = ids.length, many = n > 1 ? ` ${n} 封` : '';
    const value = await promptDialog({ title: spam ? `举报${many}垃圾邮件` : `${many ? many.trim() + '邮件' : '这'}不是垃圾邮件`,
      text: spam ? '邮件将移到「垃圾邮件」，并报告给服务器以改进过滤。' : '邮件将移回「收件箱」，并告诉服务器这不是垃圾邮件。',
      okText: spam ? '举报' : '移回收件箱',
      body: `<label class="check mg-check"><input type="checkbox" id="mg-block"> ${spam ? '同时把发件人加入黑名单（以后拒收其来信）' : '同时把发件人加入白名单（以后不再当作垃圾邮件）'}</label>`,
      collect: () => ({ block: $('#mg-block').checked }) });
    if (!value) return;
    try {
      const res = await api('reportSpam', { ids, spam, block: value.block });
      applyFolders(res);
      if (after) after(res);
      else if (S.search) { S.rows.forEach((r) => res.done.includes(r.id) && (r.fid = (S.folders.find((f) => f.role === (spam ? 'spam' : 'inbox')) || {}).id || r.fid)); renderList(); }
      else { removeRows(res.done); S.multi.clear(); renderSelection(); }
      const list = res.lists ? (spam ? '，发件人已加入黑名单' : '，发件人已加入白名单') : res.listError ? '，但发件人未能加入名单' : '';
      toast((spam ? `已举报 ${res.done.length} 封垃圾邮件` : `已将 ${res.done.length} 封移回收件箱`) + list + (res.failed.length ? `，${res.failed.length} 封未完成` : ''),
        { error: !!(res.failed.length || res.listError) });
    } catch (err) { fail(err); }
  },
  reportOne(m, spam) {
    return this.report([m.id], spam, (res) => {
      if (!res.done.length) return;
      if (READER_MID) { notifyMain({ kind: 'removed', id: m.id, from: m.fid, to: res.folders.find((f) => f.role === (spam ? 'spam' : 'inbox'))?.id }); api('closeWindow'); }
      else if (S.search) runSearch(S.search); else removeRow(m.id);
    });
  },
  async block(email) {
    if (!(await confirmDialog('把发件人加入黑名单？', `服务器以后会拒收 ${email} 的来信。可以在 设置 › 黑白名单 中移除。`, '加入黑名单'))) return;
    try { await api('addSenders', { kind: 'refuselist', values: [email] }); toast('已加入黑名单'); } catch (err) { fail(err); }
  },
  // Extra entries for the row, selection and 更多 menus of the user's own mail.
  mailItems(ids, sender) {
    const rows = ids.map((i) => S.rows.find((r) => r.id === i) || (S.message && S.message.id === i ? S.message : null));
    if (!rows.length || rows.some((r) => !r || r.mailbox)) return [];
    const n = ids.length > 1 ? `（${ids.length} 封）` : '', one = ids.length === 1 ? rows[0] : null, items = [];
    if (rows.every((r) => this.role(r.fid) === 'spam')) items.push({ label: '这不是垃圾邮件' + n, icon: 'inbox', run: () => (one && S.message === one ? this.reportOne(one, false) : this.report(ids, false)) });
    else if (!rows.some((r) => ['sent', 'drafts'].includes(this.role(r.fid)))) items.push({ label: '举报垃圾邮件' + n, icon: 'spam', run: () => (one && S.message === one ? this.reportOne(one, true) : this.report(ids, true)) });
    if (sender && !this.self(sender)) items.push({ label: '把发件人加入黑名单…', icon: 'block', run: () => this.block(sender) });
    if (rows.every((r) => this.purgeable(r.fid))) items.push({ label: '彻底删除' + n + '…', icon: 'purge', run: () => (one && S.message === one ? this.purgeOne(one) : this.purge(ids)) });
    return items;
  },

  // ------------------------------------------------------------ sidebar and list header
  decorate() {
    $$('#folder-list .folder[data-id], #shared-list .folder[data-id]').forEach((a) => {
      const f = folderById(a.dataset.id);
      if (f && f.depth) a.style.setProperty('--depth', f.depth);
    });
    const nav = $('#folder-list');
    if (nav && S.folders.length && !$('#mg-add-folder', nav)) {
      nav.insertAdjacentHTML('beforeend', '<a id="mg-add-folder" class="folder add-folder" title="新建文件夹"><i data-icon="folderplus"></i><span class="fname">新建文件夹</span></a>');
      renderIcons(nav);
      $('#mg-add-folder').onclick = () => this.createFolder(null);
    }
    const f = this.current(), box = f && f.mailbox ? boxById(f.mailbox) : null;
    $('#btn-allread').classList.toggle('hidden', !f || !f.unread || (box && !box.rw) || f.role === 'sent' || f.role === 'drafts');
    $('#btn-empty').classList.toggle('hidden', !f || !!box || !(f.role === 'trash' || f.role === 'spam') || !f.total);
    if (f && !$('#btn-empty').classList.contains('hidden')) $('#btn-empty').title = `清空「${f.name}」（永久删除 ${f.total} 封）`;
  },
  batchButtons() {
    const rows = selectedRows(), own = rows.length > 1 && rows.every((r) => !r.mailbox);
    const spam = own && rows.every((r) => this.role(r.fid) === 'spam');
    $('[data-mg="spam"]').classList.toggle('hidden', !own || spam || rows.some((r) => ['sent', 'drafts'].includes(this.role(r.fid))));
    $('[data-mg="notspam"]').classList.toggle('hidden', !spam);
    $('[data-mg="purge"]').classList.toggle('hidden', !own || !rows.every((r) => this.purgeable(r.fid)));
  },
};

// ---------------------------------------------------------------- sender blocklist / allowlist (设置 › 黑白名单)
const SL = {
  lists: { safelist: [], refuselist: [] },
  async load() {
    const pane = $('#st-senders');
    pane.innerHTML = '<p class="muted">正在读取…</p>';
    try { this.lists = await api('senderLists'); this.render(); } catch (err) { pane.innerHTML = `<p class="muted">${esc(err.message)}</p>`; }
  },
  render() {
    const pane = $('#st-senders');
    const section = (kind, title, note) => `<section class="sl-sec" data-kind="${kind}">
      <h3>${title}<span class="muted small">${this.lists[kind].length} 项</span></h3><p class="muted small">${note}</p>
      <div class="sl-add"><input type="text" placeholder="name@example.com 或 *@example.com（整个域名）" spellcheck="false"><button class="btn primary" data-add>添加</button></div>
      <div class="sl-list">${this.lists[kind].map((v) => `<div class="sl-row"><span title="${esc(v)}">${esc(v)}</span><button class="icon-btn" data-remove="${esc(v)}" title="移除"><i data-icon="close"></i></button></div>`).join('')
        || '<p class="muted small sl-empty">名单是空的</p>'}</div></section>`;
    pane.innerHTML = section('refuselist', '黑名单', '服务器会拒收来自这些地址或域名的邮件。') +
      section('safelist', '白名单', '来自这些地址或域名的邮件不会被当作垃圾邮件。') +
      '<p class="muted small">名单保存在服务器上，与网页版邮箱「设置 › 反垃圾」中的黑名单、白名单相同。同一地址只能在一个名单里。</p>';
    renderIcons(pane);
    $$('.sl-sec', pane).forEach((sec) => {
      const kind = sec.dataset.kind, input = $('input', sec);
      const add = () => { const v = input.value.trim(); if (v) this.add(kind, v); };
      $('[data-add]', sec).onclick = add;
      input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } };
      $$('[data-remove]', sec).forEach((b) => (b.onclick = () => this.remove(kind, b.dataset.remove)));
    });
  },
  async add(kind, value) {
    try { this.lists = await api('addSenders', { kind, values: [value] }); this.render(); toast(kind === 'refuselist' ? '已加入黑名单' : '已加入白名单'); } catch (err) { fail(err); }
  },
  async remove(kind, value) {
    try {
      this.lists = await api('removeSender', { kind, value }); this.render();
      toast(`已从${kind === 'refuselist' ? '黑' : '白'}名单移除`, { action: { label: '撤销', run: () => this.add(kind, value) } });
    } catch (err) { fail(err); }
  },
};

// ---------------------------------------------------------------- attachment preview
// Images arrive as data: URIs of bytes the backend recognised (SVG only sanitised, shown as an
// <img>, which never runs script); PDFs as one PNG per page rendered by QtPdf in the shell; text as
// plain text; HTML files, Word and PowerPoint as sanitised HTML and workbooks as one table per
// sheet, both shown in a frame with the mail body's rules: no scripts, no network unless the user
// asks for remote pictures. Legacy .doc/.xls/.ppt and the rest open with the system's program.
const PV_EXT = ('png jpg jpeg gif webp bmp ico avif tif tiff svg pdf html htm xhtml docx docm dotx dotm xlsx xlsm xltx xltm pptx pptm ppsx ppsm potx ' +
  'txt log csv tsv md markdown json xml ini cfg conf yaml yml eml toml properties rst tex srt vtt ics vcf diff patch sql py pyw js mjs ts tsx jsx vue ' +
  'java kt kts gradle groovy scala c h cc cpp cxx hpp cs go rs rb php pl lua r swift m dart sh bash bat cmd ps1 psm1 css scss sass less proto ' +
  'graphql dockerfile env gitignore editorconfig reg nfo asc pem crt').split(' ');
const PV = {
  m: null, a: null, kind: null, zoom: 1, fit: true, page: 0, pages: 1, token: 0, res: null, sheet: 0, remote: false,
  ZOOMS: [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4],
  can(name) { const m = /\.([a-z0-9]+)$/i.exec(name || ''); return !!m && PV_EXT.includes(m[1].toLowerCase()); },
  isOpen() { const d = $('#preview'); return !!d && !d.classList.contains('hidden'); },
  build() {
    if ($('#preview')) return;
    document.body.insertAdjacentHTML('beforeend', `<div id="preview" class="modal preview hidden" role="dialog" aria-label="附件预览"><div class="pv-card">
      <div class="pv-head"><span id="pv-ext" class="ext"></span><b id="pv-name"></b><span id="pv-size" class="muted small"></span><span class="spacer"></span>
        <span id="pv-pager" class="pv-group hidden"><button class="icon-btn" data-pv="prev" title="上一页 (←)"><i data-icon="chevron" class="flip"></i></button><span id="pv-page"></span><button class="icon-btn" data-pv="next" title="下一页 (→)"><i data-icon="chevron"></i></button></span>
        <button id="pv-remote" class="btn ghost hidden" data-pv="remote" title="这个文件引用了网络上的图片，默认不加载"><i data-icon="image"></i><span>显示网络图片</span></button>
        <span id="pv-zoom" class="pv-group"><button class="icon-btn" data-pv="out" title="缩小 (-)">−</button><button class="pv-pct" data-pv="fit" id="pv-pct" title="适合窗口 (0)">适合</button><button class="icon-btn" data-pv="in" title="放大 (+)">+</button></span>
        <button class="btn ghost" data-pv="open" title="用系统中的程序打开"><i data-icon="open"></i><span>打开</span></button>
        <button class="btn ghost" data-pv="save" title="另存为"><i data-icon="download"></i><span>保存</span></button>
        <button class="icon-btn" data-pv="close" title="关闭 (Esc)"><i data-icon="close"></i></button></div>
      <div id="pv-body" class="pv-body" tabindex="-1"></div><div id="pv-tabs" class="pv-tabs hidden" role="tablist" aria-label="工作表"></div></div></div>`);
    const d = $('#preview');
    renderIcons(d);
    d.addEventListener('click', (e) => {
      const b = e.target.closest('[data-pv]');
      if (!b) { if (e.target === d) this.close(); return; }
      if (b.dataset.sheet) { this.showSheet(Number(b.dataset.sheet)); return; }
      this.act(b.dataset.pv);
    });
    $('#pv-body').addEventListener('wheel', (e) => { if (e.ctrlKey) { e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1 : -1); } }, { passive: false });
    let timer = null;
    window.addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(() => this.isOpen() && this.layout(true), 200); });
  },
  act(name) {
    const { m, a } = this;
    ({
      close: () => this.close(), prev: () => this.go(-1), next: () => this.go(1), in: () => this.zoomBy(1), out: () => this.zoomBy(-1),
      fit: () => { this.fit = true; this.zoom = 1; this.layout(true); },
      open: () => openWithSystem(m, a),
      remote: () => { this.remote = true; $('#pv-remote').classList.add('hidden'); this.frame(this.res.html, this.res.doc); },
      save: () => api('saveAttachment', { mid: m.id, part: a.part }).then((ok) => ok && toast('附件已保存')).catch(fail),
    }[name] || (() => {}))();
  },
  isMail(a) { return (a.type || '').toLowerCase() === 'message/rfc822' || /\.eml$/i.test(a.name || ''); },
  async open(m, a) {
    // An attached mail is read as a mail (headers, body, its own attachments) in a pop-out reader.
    if (this.isMail(a)) { try { await api('openAttachedMail', { mid: m.id, part: a.part }); } catch (err) { fail(err); } return; }
    this.build();
    const token = ++this.token, body = $('#pv-body');
    Object.assign(this, { m, a, kind: null, zoom: 1, fit: true, page: 0, pages: 1, res: null, sheet: 0, remote: false });
    const [color, ext] = extColor(a.name);
    $('#pv-ext').textContent = ext; $('#pv-ext').style.background = color;
    $('#pv-name').textContent = a.name; $('#pv-name').title = a.name; $('#pv-size').textContent = size(a.size);
    body.innerHTML = '<div class="pv-msg muted">正在载入…</div>';
    $('#pv-pager').classList.add('hidden'); $('#pv-zoom').classList.add('hidden'); $('#pv-remote').classList.add('hidden'); $('#pv-tabs').classList.add('hidden');
    $('#preview').classList.remove('hidden'); body.focus();
    try {
      const res = await api('previewAttachment', { mid: m.id, part: a.part });
      if (token !== this.token) return;
      this.kind = res.kind; this.res = res;
      if (res.kind === 'html') {
        this.frame(res.html, res.doc);
        $('#pv-remote').classList.toggle('hidden', !res.hasRemoteImages);
      } else if (res.kind === 'sheet') {
        $('#pv-tabs').innerHTML = res.sheets.map((s, i) => `<button role="tab" data-sheet="${i}" title="${esc(s.name)}">${esc(s.name)}</button>`).join('');
        $('#pv-tabs').classList.remove('hidden');
        this.showSheet(0);
      } else if (res.kind === 'image') {
        body.innerHTML = '<img class="pv-img" alt="">';
        const img = $('img', body); img.onload = () => this.layout(); img.src = res.data;
      } else if (res.kind === 'text') {
        body.innerHTML = '<pre class="pv-text"></pre>';
        $('pre', body).textContent = res.text + (res.truncated ? '\n\n……（文件较大，只显示前 2 MB）' : '');
      } else {
        body.innerHTML = '<img class="pv-pdf" alt="">';
      }
      $('#pv-zoom').classList.remove('hidden');
      await this.layout(true);
    } catch (err) {
      if (token !== this.token) return;
      body.innerHTML = `<div class="pv-msg"><p>${esc(err.message)}</p><button class="btn primary" data-pv="open">用系统中的程序打开</button></div>`;
    }
  },
  // HTML in a sandboxed frame (scripts never enabled) whose CSP forbids every fetch except data:
  // pictures, and remote ones only after 显示网络图片. Links open in the browser (shell).
  frame(html, doc, note) {
    const body = $('#pv-body');
    body.innerHTML = `<iframe class="pv-frame" sandbox="allow-same-origin" title="${esc(this.a.name)}"></iframe>`;
    const frame = $('iframe', body);
    const csp = `default-src 'none'; style-src 'unsafe-inline'; img-src data: ${this.remote ? 'https: http:' : ''}; font-src data:`;
    frame.addEventListener('load', () => {
      const d = frame.contentDocument; if (!d) return;
      d.addEventListener('keydown', (e) => { if (e.key === 'Escape' || ((e.ctrlKey || e.metaKey) && /^[-+=0]$/.test(e.key))) { this.key(e); } });
      d.addEventListener('wheel', (e) => { if (e.ctrlKey) { e.preventDefault(); this.zoomBy(e.deltaY < 0 ? 1 : -1); } }, { passive: false });
      this.layout();
    });
    frame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}">
      <style>${PV_CSS.base}${PV_CSS[doc] || ''}</style></head><body class="${esc(doc || '')}">${note ? `<div class="pv-note">${esc(note)}</div>` : ''}${html}</body></html>`;
  },
  showSheet(i) {
    const s = this.res.sheets[i]; if (!s) return;
    this.sheet = i;
    $$('#pv-tabs button').forEach((b) => b.classList.toggle('on', Number(b.dataset.sheet) === i));
    const note = s.note === 'chart' ? '这是图表工作表，鸿雁里不能显示，请用系统中的程序打开。'
      : !s.rows ? '这个工作表是空的。' : s.truncated ? '工作表较大，这里只显示前 2000 行、100 列；完整内容请用系统中的程序打开。' : '';
    this.frame(s.html, 'sheet', note);
  },
  close() {
    if (!this.isOpen()) return;
    this.token++;
    $('#preview').classList.add('hidden'); $('#pv-body').innerHTML = '';
    if (this.kind === 'pdf') api('closePreview').catch(() => {});
    this.kind = null;
  },
  go(step) {
    if (this.kind !== 'pdf') return;
    const page = Math.max(0, Math.min(this.pages - 1, this.page + step));
    if (page !== this.page) { this.page = page; this.layout(true); $('#pv-body').scrollTop = 0; }
  },
  scale() {
    const img = $('#pv-body .pv-img');
    return this.kind === 'image' && img && img.naturalWidth ? img.clientWidth / img.naturalWidth : this.zoom;
  },
  zoomBy(dir) {
    if (!this.kind) return;
    const now = this.fit ? this.scale() : this.zoom;
    const next = dir > 0 ? this.ZOOMS.find((z) => z > now + 0.01) : [...this.ZOOMS].reverse().find((z) => z < now - 0.01);
    if (!next) return;
    this.fit = false; this.zoom = next; this.layout(true);
  },
  async layout(render) {
    const body = $('#pv-body'), room = { w: Math.max(200, body.clientWidth - 48), h: Math.max(200, body.clientHeight - 48) };
    $('#pv-pct').textContent = this.fit ? '适合' : Math.round(this.zoom * 100) + '%';
    if (this.kind === 'image') {
      const img = $('.pv-img', body); if (!img || !img.naturalWidth) return;
      if (this.fit) { img.style.width = ''; img.style.maxWidth = room.w + 'px'; img.style.maxHeight = room.h + 'px'; }
      else { img.style.maxWidth = img.style.maxHeight = 'none'; img.style.width = Math.round(img.naturalWidth * this.zoom) + 'px'; }
    } else if (this.kind === 'text') {
      $('.pv-text', body).style.fontSize = (13 * (this.fit ? 1 : this.zoom)).toFixed(1) + 'px';
    } else if (this.kind === 'html' || this.kind === 'sheet') {
      const d = ($('.pv-frame', body) || {}).contentDocument;
      if (d && d.documentElement) d.documentElement.style.zoom = String(this.fit ? 1 : this.zoom);
    } else if (this.kind === 'pdf' && render) {
      // Zoom is relative to the page fitted to the window's width.
      const css = Math.round(room.w * (this.fit ? 1 : this.zoom)), token = this.token;
      try {
        const res = await api('previewPdfPage', { mid: this.m.id, part: this.a.part, page: this.page, width: Math.round(css * (devicePixelRatio || 1)) });
        if (token !== this.token) return;
        Object.assign(this, { page: res.page, pages: res.pages });
        const img = $('.pv-pdf', body); img.src = res.data; img.style.width = css + 'px';
        $('#pv-page').textContent = `${res.page + 1} / ${res.pages}`;
        $('#pv-pager').classList.toggle('hidden', res.pages < 2);
        $('[data-pv="prev"]').disabled = res.page === 0; $('[data-pv="next"]').disabled = res.page >= res.pages - 1;
      } catch (err) { if (token === this.token) body.innerHTML = `<div class="pv-msg"><p>${esc(err.message)}</p><button class="btn primary" data-pv="open">用系统中的程序打开</button></div>`; }
    }
  },
  key(e) {
    const k = e.key;
    if (k === 'Escape') this.close();
    else if (k === 'ArrowLeft' || k === 'PageUp') this.go(-1);
    else if (k === 'ArrowRight' || k === 'PageDown') this.go(1);
    else if (k === '+' || k === '=') this.zoomBy(1);
    else if (k === '-') this.zoomBy(-1);
    else if (k === '0') this.act('fit');
    else return;  // Other keys (Ctrl+C on selected text, scrolling) keep their default.
    e.preventDefault();
  },
};
// The preview frame's own styles (it inherits nothing from the app).
const PV_CSS = {
  base: `html{background:#fff}body{margin:0;color:#1d2433;font:14px/1.65 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif;overflow-wrap:anywhere}
    img{max-width:100%;height:auto}a{color:#2563eb}pre{white-space:pre-wrap}::selection{background:#bfd3ff}
    .pv-note{position:sticky;top:0;left:0;z-index:3;padding:7px 14px;background:#fffbeb;color:#92400e;border-bottom:1px solid #fde68a;font-size:12.5px}`,
  html: 'body{padding:18px 22px}',
  docx: `html{background:#eef0f3}body{max-width:794px;margin:20px auto;padding:56px 64px;background:#fff;box-shadow:0 1px 6px rgba(0,0,0,.15);box-sizing:border-box}
    p{margin:0 0 .45em;white-space:pre-wrap}h1,h2,h3,h4,h5,h6{margin:.8em 0 .4em;line-height:1.35}
    table.wt{border-collapse:collapse;margin:.6em 0;max-width:100%}table.wt td{border:1px solid #b8bec8;padding:4px 8px;vertical-align:top}table.wt td p{margin:0}`,
  pptx: `html{background:#eef0f3}body{padding:8px 16px 24px}
    section.slide{position:relative;max-width:880px;min-height:180px;margin:16px auto;padding:30px 44px;background:#fff;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.15)}
    section.slide h2{margin:0 0 14px;font-size:22px}.sn{position:absolute;top:10px;right:14px;color:#98a2b3;font-size:12px}
    section.slide p{margin:0 0 .4em}.empty{color:#98a2b3}table.wt{border-collapse:collapse;margin:.6em 0}table.wt td{border:1px solid #b8bec8;padding:4px 10px}table.wt td p{margin:0}`,
  sheet: `body{font-size:13px}table.xl{border-collapse:separate;border-spacing:0;table-layout:fixed;width:max-content}
    table.xl td,table.xl th{border-right:1px solid #e3e6eb;border-bottom:1px solid #e3e6eb;padding:2px 6px;height:20px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:600px}
    table.xl td.n{text-align:right;font-variant-numeric:tabular-nums}table.xl td[colspan],table.xl td[rowspan]{text-align:center;vertical-align:middle}
    table.xl thead th{position:sticky;top:0;z-index:2;background:#f3f4f6;color:#667085;font-weight:500;text-align:center}
    table.xl tbody th{position:sticky;left:0;z-index:1;background:#f3f4f6;color:#667085;font-weight:500;text-align:center}
    table.xl thead th.xc{left:0;z-index:3}col.xh{width:44px}`,
};
// 打开 copies the file to the data folder and hands it to the system on a worker thread.
function openWithSystem(m, a) {
  return api('openAttachment', { mid: m.id, part: a.part }).then(() => toast(`正在用系统中的程序打开「${a.name}」…`)).catch(fail);
}

// While the preview is open its keys go to it, not to the mail list behind it.
window.addEventListener('keydown', (e) => { if (PV.isOpen()) { PV.key(e); e.stopImmediatePropagation(); } }, true);

// ---------------------------------------------------------------- hooks into app.js
(function listHead() {
  $('#btn-threads').insertAdjacentHTML('beforebegin',
    '<button id="btn-allread" class="icon-btn hidden" title="全部标为已读"><i data-icon="checkall"></i></button>' +
    '<button id="btn-empty" class="icon-btn danger hidden" title="清空"><i data-icon="purge"></i></button>');
  renderIcons($('.list-title-row'));
  $('#btn-allread').onclick = () => MG.current() && MG.markAllRead(MG.current().id);
  $('#btn-empty').onclick = () => MG.current() && MG.emptyFolder(MG.current());
  $('#reader-multi .reader-toolbar .spacer').insertAdjacentHTML('beforebegin',
    '<button class="tb hidden" data-mg="spam" title="举报垃圾邮件"><i data-icon="spam"></i><span>举报垃圾</span></button>' +
    '<button class="tb hidden" data-mg="notspam" title="这不是垃圾邮件"><i data-icon="inbox"></i><span>不是垃圾邮件</span></button>' +
    '<button class="tb danger hidden" data-mg="purge" title="彻底删除"><i data-icon="purge"></i><span>彻底删除</span></button>');
  renderIcons($('#reader-multi'));
  $('#reader-multi').addEventListener('click', (e) => {
    const b = e.target.closest('[data-mg]'); if (!b || S.multi.size < 2) return;
    const ids = [...S.multi];
    ({ spam: () => MG.report(ids, true), notspam: () => MG.report(ids, false), purge: () => MG.purge(ids) }[b.dataset.mg])();
  });
})();

const _mgRenderFolders = renderFolders;
renderFolders = function () { _mgRenderFolders(); MG.decorate(); };
const _mgRenderSelection = renderSelection;
renderSelection = function () { _mgRenderSelection(); if (S.multi.size >= 2) MG.batchButtons(); };

const _mgRenderMessage = renderMessage;
renderMessage = function (m) {
  _mgRenderMessage(m);
  if (!m.mailbox && MG.role(m.fid) === 'spam') {
    $('#r-banners').insertAdjacentHTML('afterbegin', '<div class="banner"><i data-icon="spam"></i><span>这封邮件在「垃圾邮件」中。请勿轻信其中的链接和附件。</span><span class="spacer"></span><button class="btn ghost" id="b-notspam">这不是垃圾邮件</button></div>');
    renderIcons($('#r-banners'));
    $('#b-notspam').onclick = () => MG.reportOne(m, false);
  }
  // Attachments: a 预览 button where the viewer can show the file; the name opens it too.
  $$('#r-attachments .att').forEach((row) => {
    const part = ($('[data-open]', row) || {}).dataset?.open;
    const a = m.attachments.find((x) => x.part === part);
    if (!a || !(PV.can(a.name) || PV.isMail(a))) return;
    $('[data-open]', row).insertAdjacentHTML('beforebegin', `<button class="icon-btn" data-preview title="预览"><i data-icon="eye"></i></button>`);
    renderIcons(row);
    const show = () => PV.open(m, a);
    $('[data-preview]', row).onclick = show;
    const name = $('.name', row); name.classList.add('pv-link'); name.onclick = show;
    row.ondblclick = (e) => { if (!e.target.closest('button, .name')) show(); };
  });
};
const _mgSelectMessage = selectMessage;
selectMessage = function (id, opts) { PV.close(); return _mgSelectMessage(id, opts); };

// Context menus: right-click on a folder; extra entries on mail rows and in 更多.
$('#side-scroll').addEventListener('contextmenu', (e) => {
  const a = e.target.closest('.folder[data-id]');
  if (!a && !e.target.closest('#folder-list')) return;
  e.preventDefault();
  if (!a) { showMenu(e.clientX, e.clientY, [{ label: '新建文件夹', icon: 'folderplus', run: () => MG.createFolder(null) }]); return; }
  const f = folderById(a.dataset.id); if (f) showMenu(e.clientX, e.clientY, MG.folderItems(f));
});
$('#mail-list').addEventListener('contextmenu', (e) => {
  const row = e.target.closest('.row');
  MG.ctx = row && !S.local ? row.dataset.id : null;
}, true);
const _mgShowMenu = showMenu;
showMenu = function (x, y, items) {
  const id = MG.ctx, m = MG.more; MG.ctx = MG.more = null;
  let extra = [];
  if (id) {
    const ids = S.multi.size > 1 && S.multi.has(id) ? [...S.multi] : [id];
    const r = S.rows.find((q) => q.id === id);
    extra = MG.mailItems(ids, ids.length === 1 && r ? r.fromEmail : '');
  } else if (m) extra = MG.mailItems([m.id], (m.from[0] || {}).email);
  _mgShowMenu(x, y, extra.length ? items.concat(['-'], extra) : items);
};
const _mgMoreMenu = moreMenu;
moreMenu = function (button, m) { MG.more = m; _mgMoreMenu(button, m); };

// Delete in 已删除 is 彻底删除 (with its double confirmation); elsewhere it still moves to 已删除.
const _mgDeleteMessage = deleteMessage;
deleteMessage = function (id) {
  const r = S.rows.find((x) => x.id === id) || (S.message && S.message.id === id ? S.message : null);
  if (r && !r.mailbox && MG.role(r.fid) === 'trash') return MG.purge([id], (res) => res.done.forEach(removeRow));
  return _mgDeleteMessage(id);
};
const _mgBatchMove = batchMove;
batchMove = function (folderId, opts = {}) {
  const ids = opts.ids || [...S.multi], rows = S.rows.filter((r) => ids.includes(r.id));
  if (opts.delete && rows.length && rows.every((r) => !r.mailbox && MG.role(r.fid) === 'trash')) return MG.purge(ids);
  return _mgBatchMove(folderId, opts);
};
const _mgReaderAct = readerAct;
readerAct = async function (act, m) {
  if (act === 'delete' && !m.mailbox && MG.role(m.fid) === 'trash') { MG.purgeOne(m); return true; }
  return _mgReaderAct(act, m);
};

// 设置 › 黑白名单, after the pages rules.js adds.
(function senderPane() {
  if (typeof addSettingsPage !== 'function') return;
  addSettingsPage('senders', '黑白名单', 'filter');
  const show = RL.show;
  RL.show = (name) => { show(name); if (name === 'senders') SL.load(); };
})();
