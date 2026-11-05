// ---------------------------------------------------------------- 设置 → 关于我 · 资料 (Me3–Me5)
// Reference notes the AI keeps from mail and the user manages: a catalog line goes with every
// AI call, a note's body when the mail at hand matches it or 问鸿雁 reads it. The AI writes at
// once (each change undoable in AI 最近整理); the user's lines and locked notes it never changes.
const NT = { data: null, pick: '', editing: false, query: '', busy: false, stop: false };
const NT_SRC = { mail: '从邮件整理', chat: '问鸿雁里记的', import: '导入的', '': '你写的' };
const ntDay = (ts) => ts ? new Date(ts * 1000).toISOString().slice(0, 10) : '';
const ntAgo = (ts) => {
  if (!ts) return '还没用到';
  const d = Math.floor((Date.now() / 1000 - ts) / 86400);
  return d < 1 ? '今天用到' : `${d} 天前用到`;
};
const ntStatus = (text, kind = '') => {
  const el = $('#nt-status');
  el.className = 'me-status small' + (kind ? ' ' + kind : '');
  el.innerHTML = (kind === 'busy' ? '<span class="tk-spin"></span>' : '') + esc(text);
};
const ntOp = { add: '新增', replace: '改为', remove: '删去', create: '新建' };

function meTab(name) {
  $$('[data-me-tab]').forEach((b) => b.classList.toggle('on', b.dataset.meTab === name));
  $('#nt-pane').classList.toggle('hidden', name !== 'notes');
  $('#me-pane-me').classList.toggle('hidden', name !== 'me');
  if (name === 'notes') renderNotes();
}
$$('[data-me-tab]').forEach((b) => (b.onclick = () => meTab(b.dataset.meTab)));

async function renderNotes() {
  try { NT.data = await api('notesList'); } catch (err) { NT.data = null; ntStatus((err && err.message) || '', 'error'); return; }
  const d = NT.data, live = d.notes.filter((n) => n.state !== 'archived'), old = d.notes.filter((n) => n.state === 'archived');
  $('#nt-count').textContent = live.length ? String(live.length) : '';
  $('#nt-on').checked = !!d.on;
  if (NT.pick && !d.notes.some((n) => n.id === NT.pick)) { NT.pick = ''; NT.editing = false; }
  // AI 最近整理: one line, opened on a click; never pops up by itself.
  const recent = d.recent;
  $('#nt-recent').innerHTML = recent.length ? `<details class="nt-recent-box"><summary><b>AI 最近整理</b><span class="muted small">：7 天内 ${recent.length} 处，点开可逐处撤销</span></summary>` +
    '<ul class="uc-rule-list">' + recent.map((e) => `<li><span><span class="muted small">${esc(e.title)} · ${ntOp[e.op] || ''} · ${esc(ntDay(e.at))}</span><br>` +
      (e.op === 'replace' ? `<s class="muted">${esc(e.before)}</s> → ${esc(e.after)}` : e.op === 'remove' ? `<s class="muted">${esc(e.before)}</s>` : esc(e.after)) +
      `${e.mid ? ` <a href="#" class="small" data-nt-mail="${esc(e.mid)}">来源邮件</a>` : ''}</span>` +
      `<button class="btn ghost sm" data-nt-undo="${esc(e.log_id)}" data-nt-note="${esc(e.note)}">撤销</button></li>`).join('') + '</ul></details>' : '';
  const q = NT.query.trim().toLowerCase();
  const hit = (n) => !q || [n.title, n.about, n.folder, ...n.lines.map((x) => x.text)].some((t) => String(t || '').toLowerCase().includes(q));
  const row = (n) => `<li data-nt="${esc(n.id)}" class="${n.id === NT.pick ? 'on' : ''}${n.unused ? ' nt-unused' : ''}"><span><b>${esc(n.title)}</b>` +
    `${n.locked ? ' <i data-icon="lock" title="只由我维护"></i>' : ''}${n.stale.length || n.unchecked ? ' <span class="nt-flag">可能过时</span>' : ''}` +
    `<br><span class="muted small">${esc(n.about || '（没有说明）')} · ${n.unused ? '很久没用到' : ntAgo(n.used)}</span></span></li>`;
  const folders = [...new Set(live.filter(hit).map((n) => n.folder || ''))];
  $('#nt-list').innerHTML = (d.cut > 0 ? `<p class="nt-warn small">目录太长，AI 只看到最近用到的 ${live.length - d.cut} 份</p>` : '') +
    (live.length ? folders.map((f) => `<div class="nt-folder muted small">${esc(f || '未归类')}</div><ul class="nt-items">` +
      live.filter((n) => hit(n) && (n.folder || '') === f).map(row).join('') + '</ul>').join('') || '<p class="muted small">没有找到。</p>'
      : '<p class="muted small">还没有资料。可以「新建」，或开着「理解新邮件」和「从邮件整理资料」，鸿雁读到客户组织、各模块负责人这类信息时会自动建起来。</p>') +
    (old.length ? `<details class="nt-archived"><summary class="muted small">已归档 ${old.length}</summary><ul class="nt-items">${old.filter(hit).map(row).join('')}</ul></details>` : '');
  renderIcons($('#nt-pane'));
  $$('#nt-list [data-nt]').forEach((li) => (li.onclick = () => { NT.pick = li.dataset.nt; NT.editing = false; renderNotes(); }));
  $$('#nt-recent [data-nt-undo]').forEach((b) => (b.onclick = () => api('noteUndo', { note: b.dataset.ntNote, logs: [b.dataset.ntUndo] })
    .then(() => { toast('已撤销这一处'); renderNotes(); }).catch(fail)));
  $$('#nt-pane [data-nt-mail]').forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); $('#settings').classList.add('hidden'); memOpenMail(a.dataset.ntMail); }));
  ntEditor();
}

function ntEditor() {
  const box = $('#nt-edit'), n = NT.data && NT.data.notes.find((x) => x.id === NT.pick);
  if (!n && !NT.editing) { box.innerHTML = '<p class="muted small nt-empty">在左边选一份资料查看，或点「新建」。</p>'; return; }
  if (NT.editing) { ntForm(box, n); return; }
  const m = n.match || {}, matchText = [m.domains.length && `域名 ${m.domains.join('、')}`, m.addresses.length && `邮箱 ${m.addresses.join('、')}`,
    m.words.length && `主题含 ${m.words.join('、')}`].filter(Boolean).join('；');
  const archived = n.state === 'archived';
  box.innerHTML = `<div class="nt-head"><b>${esc(n.title)}</b><span class="muted small">${esc(n.folder || '未归类')} · ${esc(NT_SRC[n.src] || '')} · ${n.size} / ${NT.data.body} 字</span></div>` +
    (n.about ? `<p class="muted small">${esc(n.about)}</p>` : '') +
    `<p class="muted small">自动附上：${esc(matchText || '没有设匹配条件，只在问鸿雁时按需读取')}</p>` +
    n.stale.map((s) => `<div class="nt-stale small"><span><b>可能过时：</b>${esc(s.line)}<br><span class="muted">邮件说：${esc(s.why)}</span></span>` +
      `${s.mid ? `<a href="#" data-nt-mail="${esc(s.mid)}">打开邮件</a>` : ''}${s.fix ? `<button class="btn sm" data-nt-stale="${esc(s.id)}" data-fix="1">按邮件改</button>` : ''}` +
      `<button class="btn ghost sm" data-nt-stale="${esc(s.id)}">不是过时</button></div>`).join('') +
    (!n.stale.length && n.unchecked ? '<div class="nt-stale small"><span>超过 180 天没有核实，可能过时。</span><button class="btn ghost sm" data-nt-act="check">仍然有效</button></div>' : '') +
    (n.lines.length ? '<ul class="nt-lines">' + n.lines.map((x) => `<li class="${x.by === 'ai' ? 'ai' : ''}${/^#{1,3} /.test(x.text) ? ' h' : ''}"` +
      ` title="${x.by === 'ai' ? `AI 整理${x.mid ? '，来自 ' + ntDay(x.at) + ' 的邮件' : ''}` : '你写的'}">${esc(x.text.replace(/^#{1,3} /, ''))}` +
      `${x.by === 'ai' && x.mid ? ` <a href="#" class="small" data-nt-mail="${esc(x.mid)}">来源</a>` : ''}</li>`).join('') + '</ul>'
      : '<p class="muted small">还没有内容。</p>') +
    '<p class="muted small">浅色左边线的是 AI 整理的；你改过或新写的行 AI 以后不会改。</p>' +
    '<div class="row-gap nt-actions">' + (archived ? '<button class="btn" data-nt-act="unarchive">恢复</button>' :
      '<button class="btn" data-nt-act="edit">编辑</button><button class="btn ghost" data-nt-act="check" title="确认内容还对：刷新「最近核实」">仍然有效</button>' +
      `<button class="btn ghost" data-nt-act="lock">${n.locked ? '允许 AI 更新' : '只由我维护'}</button>` +
      '<button class="btn ghost" data-nt-act="export">导出 .md</button><button class="btn ghost" data-nt-act="archive" title="不进目录、不自动附上、AI 不再写；随时可以恢复">归档</button>') +
    '<button class="btn ghost danger" data-nt-act="remove">删除</button></div>';
  $$('[data-nt-mail]', box).forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); $('#settings').classList.add('hidden'); memOpenMail(a.dataset.ntMail); }));
  $$('[data-nt-stale]', box).forEach((b) => (b.onclick = () => api('noteStale', { note: n.id, stale: b.dataset.ntStale, fix: !!b.dataset.fix }).then(() => renderNotes()).catch(fail)));
  $$('[data-nt-act]', box).forEach((b) => (b.onclick = async () => {
    const act = b.dataset.ntAct;
    try {
      if (act === 'edit') { NT.editing = true; ntEditor(); return; }
      if (act === 'export') {
        if (await api('noteExport', { id: n.id })) toast('已导出。导出的 .md 是明文，请自己保管好');
        return;
      }
      if (act === 'remove') {
        if (!await confirmDialog(`删除「${n.title}」？`, '删除后 AI 不再参考它；不能撤销。只是暂时用不上可以「归档」。', '删除')) return;
        await api('noteRemove', { id: n.id }); NT.pick = '';
      } else {
        await api('noteState', { id: n.id, ...(act === 'check' ? { checked: true } : act === 'lock' ? { locked: !n.locked } : { archived: act === 'archive' }) });
        if (act === 'check') toast('已记为仍然有效');
      }
      renderNotes();
    } catch (err) { fail(err); }
  }));
}

function ntForm(box, n) {
  const m = (n && n.match) || { domains: [], addresses: [], words: [] };
  box.innerHTML = `<div class="nt-head"><b>${n ? '编辑资料' : '新建资料'}</b></div>` +
    `<label class="nt-field">标题<input id="nt-title" type="text" maxlength="40" spellcheck="false" placeholder="例如「客户组织 · NG DCM&AD」" value="${esc(n ? n.title : '')}"></label>` +
    `<label class="nt-field">文件夹<input id="nt-folder" type="text" maxlength="20" spellcheck="false" list="nt-folders" placeholder="客户 / 系统 / 流程" value="${esc(n ? n.folder : '')}"></label>` +
    `<datalist id="nt-folders">${[...new Set(NT.data.notes.map((x) => x.folder).filter(Boolean))].map((f) => `<option value="${esc(f)}">`).join('')}</datalist>` +
    `<label class="nt-field">说明<input id="nt-about" type="text" maxlength="60" spellcheck="false" placeholder="什么时候该看它，例如「客户各模块负责人和 UAT 焦点人」" value="${esc(n ? n.about : '')}"></label>` +
    '<div class="nt-field">自动附上（处理这些邮件时把正文带给 AI；逗号或空格分开）' +
    `<input id="nt-domains" type="text" spellcheck="false" placeholder="邮箱域名，如 ngrid.sa（含子域名）" value="${esc(m.domains.join(', '))}">` +
    `<input id="nt-addresses" type="text" spellcheck="false" placeholder="邮箱地址" value="${esc(m.addresses.join(', '))}">` +
    `<input id="nt-words" type="text" spellcheck="false" placeholder="主题关键词（至少 3 个字），如 IPAM" value="${esc(m.words.join(', '))}"></div>` +
    `<label class="nt-field">正文（一行一条，可以用「## 小标题」分组）<textarea id="nt-text" rows="12" spellcheck="false">${esc(n ? n.lines.map((x) => x.text).join('\n') : '')}</textarea></label>` +
    `<p class="muted small" id="nt-size"></p>` +
    `<div class="row-gap"><button class="btn primary" id="nt-save">保存</button><button class="btn ghost" id="nt-cancel">取消</button></div>`;
  const size = () => { const t = $('#nt-text').value.split('\n').map((s) => s.trim()).join(''); $('#nt-size').textContent = `${t.length} / ${NT.data.body} 字`; };
  $('#nt-text').oninput = size; size();
  $('#nt-cancel').onclick = () => { NT.editing = false; ntEditor(); };
  $('#nt-save').onclick = async () => {
    const list = (id) => $(id).value.split(/[,，;；\s]+/).map((s) => s.trim()).filter(Boolean);
    try {
      const r = await api('noteSave', { id: n ? n.id : '', title: $('#nt-title').value, folder: $('#nt-folder').value, about: $('#nt-about').value,
        match: { domains: list('#nt-domains'), addresses: list('#nt-addresses'), words: $('#nt-words').value.split(/[,，;；]+/).map((s) => s.trim()).filter(Boolean) },
        text: $('#nt-text').value });
      NT.pick = r.saved; NT.editing = false; renderNotes();
    } catch (err) { fail(err); }
  };
}

$('#nt-search').oninput = () => { NT.query = $('#nt-search').value; renderNotes(); };
$('#nt-new').onclick = () => { NT.pick = ''; NT.editing = true; ntEditor(); };
$('#nt-import').onclick = async () => {
  try { const r = await api('noteImport'); if (r) { NT.pick = r.saved; NT.editing = false; toast('已导入，导入的行算你写的'); renderNotes(); } } catch (err) { fail(err); }
};
$('#nt-on').onchange = async () => {
  try { S.prefs = await api('setPrefs', { values: { notesFromMail: $('#nt-on').checked } }); } catch (err) { fail(err); }
};

// 从最近 30 天邮件整理 (Me5): shown first how many mails and tokens; runs mail by mail and can stop.
$('#nt-backfill').onclick = async () => {
  if (NT.busy) { NT.stop = true; return; }
  let plan;
  try { plan = await api('notesBackfillPlan'); } catch (err) { fail(err); return; }
  if (!plan.ids.length) { ntStatus('最近 30 天没有可以整理的邮件。', 'warn'); return; }
  if (!await confirmDialog('从最近 30 天邮件整理资料？', `会把 ${plan.ids.length} 封邮件逐封发给 AI，只整理资料、不重新分类，预计约 ${Math.round(plan.tokens / 1000)} 千 token` +
    `${plan.missing.length ? `；其中 ${plan.missing.length} 封要先下载正文（不会标为已读）` : ''}。结果都在「AI 最近整理」里，可以逐处撤销；随时可以停止。`, '开始整理')) return;
  const button = $('#nt-backfill');
  NT.busy = true; NT.stop = false; button.textContent = '停止';
  let done = 0, failed = 0, changed = 0, row = 0;  // row: failures in a row; three stop the run (AI not set, quota, offline).
  try {
    for (let i = 0; i < plan.missing.length && !NT.stop; i += 10) {
      ntStatus(`正在下载正文 ${Math.min(i + 10, plan.missing.length)} / ${plan.missing.length}…`, 'busy');
      failed += await api('understandFetch', { ids: plan.missing.slice(i, i + 10) }).catch(() => 0);
    }
    for (const mid of plan.ids) {
      if (NT.stop) break;
      ntStatus(`正在整理第 ${done + 1} / ${plan.ids.length} 封，已更新 ${changed} 处…`, 'busy');
      try { changed += (await api('notesBackfill', { mid })).changed; row = 0; } catch (err) {
        failed += 1;
        if (++row >= 3) throw err;
      }
      done += 1;
    }
    ntStatus(`${NT.stop ? '已停止' : '整理完成'}：读了 ${done} 封，更新了 ${changed} 处${failed ? `，${failed} 封没能读取` : ''}。`, 'ok');
  } catch (err) {
    ntStatus(`没有完成：${(err && err.message) || '操作未完成，请稍后重试。'}`, 'error');
  } finally { NT.busy = false; button.textContent = '从最近 30 天邮件整理'; renderNotes(); }
};

// 打开资料: from 问鸿雁's 「据资料」 / 「已记进资料」 lines.
function ntOpen(id) {
  openSettings('me');
  NT.pick = id; NT.editing = false;
  meTab('notes');
}

// 问鸿雁 (Me5): 已记进资料《标题》：新增 3 行 · 查看 · 撤销 — written at once, undone here in one go.
function noteSavedLine(n) {
  const parts = [n.created && '新建', n.added && `新增 ${n.added} 行`, n.replaced && `改 ${n.replaced} 行`, n.removed && `删 ${n.removed} 行`].filter(Boolean).join('、');
  return `<div class="chat-note-saved small" data-note-saved="${esc(n.note)}" data-logs="${esc(n.logs.join(','))}"><i data-icon="check"></i>` +
    `<span>已记进资料<a href="#" data-note-open="${esc(n.note)}">《${esc(n.title)}》</a>${parts ? '：' + esc(parts) : ''}</span><span class="spacer"></span>` +
    '<a href="#" data-note-open-btn>查看</a><a href="#" data-note-undo>撤销</a></div>';
}
document.addEventListener('click', (ev) => {
  const open = ev.target.closest('[data-note-open]');
  if (open) { ev.preventDefault(); ntOpen(open.dataset.noteOpen); return; }
  const line = ev.target.closest('[data-note-saved]');
  if (!line) return;
  if (ev.target.closest('[data-note-open-btn]')) { ev.preventDefault(); ntOpen(line.dataset.noteSaved); return; }
  if (ev.target.closest('[data-note-undo]')) {
    ev.preventDefault();
    api('noteUndo', { note: line.dataset.noteSaved, logs: line.dataset.logs.split(',').filter(Boolean) })
      .then(() => { line.innerHTML = '<span class="muted">已撤销，资料恢复到记之前的样子。</span>'; }).catch(fail);
  }
});

// 从「人」移到资料 (Me5): the ticked 关于我 entries go to a note as the user's lines.
async function meToNote(ids) {
  let d;
  try { d = await api('notesList'); } catch (err) { fail(err); return; }
  const live = d.notes.filter((n) => n.state !== 'archived');
  const value = await promptDialog({ title: `把 ${ids.length} 条移到资料`, okText: '移过去',
    body: '<p class="muted small">按行追加到一份资料里（算你写的），原条目变成「以前的」，随时可以恢复。适合某个客户的整张组织表、各模块负责人这类名单。</p>' +
      `<label class="pref-row">放进<select id="me-to-note"><option value="">新建一份…</option>${live.map((n) => `<option value="${esc(n.id)}">${esc(n.title)}</option>`).join('')}</select></label>` +
      '<input id="me-to-title" type="text" maxlength="40" spellcheck="false" placeholder="新资料的标题，例如「客户组织 · NG DCM&AD」">',
    onOpen: () => { const sync = () => $('#me-to-title').classList.toggle('hidden', !!$('#me-to-note').value); $('#me-to-note').onchange = sync; sync(); },
    collect: () => ({ note: $('#me-to-note').value, title: $('#me-to-title').value.trim() }) });
  if (!value || (!value.note && !value.title)) return;
  try {
    const r = await api('aboutToNote', { ids, ...value });
    ME.picked.clear(); toast(`已移到资料：${r.added} 行`); renderMe();
  } catch (err) { fail(err); }
}
