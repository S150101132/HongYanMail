'use strict';
/* 鸿雁 · 工作记忆 (M2) and its uses (M3). The 理解层 remembers the facts of the user's projects
   (backend memory_*); here: the reader's 「这个项目最近」 and task candidate, the project's
   timeline with corrections, 本周你需要知道的 above 知会, 等回复 (催办) with 起草跟进,
   the person popover's extra lines and the project list in 设置 → AI 助手. Facts are plain
   text only (esc). Loaded after understand.js and contacts.js. */

const MEM = { recent: new Map(), week: 0, weekOpen: false, brief: null, weekAll: new Set() };  // recent: project -> memoryRecent result (or a promise)
// brief: the AI summary of the week shown ({key, data | loading | failed}); weekAll: projects showing every fact
const MEM_TYPE = { status: '进展', decision: '决定', owner: '分工', date: '日期', other: '其他' };
function memReset() { MEM.recent.clear(); }
function memRecent(project) {
  if (!MEM.recent.has(project)) MEM.recent.set(project, api('memoryRecent', { project }).catch(() => ({ project: '', facts: [], pending: 0 })));
  return MEM.recent.get(project);
}

// ---------------------------------------------------------------- the reader: 这个项目最近, task candidate, 催办
const _memRenderUcStrip = renderUcStrip;
renderUcStrip = function (m) {
  _memRenderUcStrip(m);
  const old = $('#mem-strip'); if (old) old.remove();
  if (!ucOn() || !m || m.imported) return;
  const box = document.createElement('div'); box.id = 'mem-strip'; box.className = 'mem-strip';
  const wait = UC.wait && UC.wait.get(m.id);
  const c = UC.cards.get(m.id);
  const lines = [];
  if (wait) {
    lines.push(`<div class="mem-line wait"><i data-icon="clock"></i><span class="uc-text">已等 ${wait.waiting} 天没有回复${wait.toNames ? '（' + esc(wait.toNames) + '）' : ''}</span>` +
      '<button class="btn ghost" data-mem="follow" title="用 AI 起草一封礼貌的跟进邮件（全部回复，由你检查后发送）">起草跟进</button>' +
      '<button class="btn ghost" data-mem="nofollow" title="从「等回复」中移除">不用跟进</button></div>');
  }
  const linked = typeof TK !== 'undefined' && TK.mailIndex && (TK.mailIndex[m.id] || []).length;
  if (c && c.task && !linked) {
    lines.push(`<div class="mem-line"><i data-icon="tasks"></i><span class="uc-text muted">可能与任务 #${c.task} 有关${c.why ? '：' + esc(c.why) : ''}</span>` +
      `<button class="btn ghost" data-mem="link" title="把这封邮件关联到任务 #${c.task}">关联</button></div>`);
  }
  const token = MEM.strip = (MEM.strip || 0) + 1;
  const draw = (recent) => {
    if (token !== MEM.strip || !S.message || S.message.id !== m.id) return;  // Another mail, or drawn again since.
    const extra = recent && recent.facts.length ? `<div class="mem-line recent" data-mem="timeline" title="点击查看「${esc(recent.project)}」的全部记忆"><i data-icon="spark"></i>` +
      `<span class="uc-text"><b>这个项目最近：</b>${recent.facts.map((f) => esc(f.text)).join('；')}</span>` +
      (recent.pending ? `<span class="uc-due late">${recent.pending} 条待确认</span>` : '') + `<span class="uc-project">${esc(recent.project)}</span></div>` : '';
    box.innerHTML = lines.join('') + extra;
    if (!box.innerHTML) { box.remove(); return; }
    if (!box.isConnected) ($('#uc-strip') || $('#r-banners')).after(box);
    renderIcons(box);
    $$('[data-mem]', box).forEach((b) => (b.onclick = () => ({
      follow: () => memFollowup(m), nofollow: () => memNoFollow(m.id),
      link: () => api('taskLink', { id: c.task, mid: m.id }).then(() => { toast(`已关联到任务 #${c.task}`); if (typeof loadMailIndex === 'function') loadMailIndex(); renderUcStrip(m); }).catch(fail),
      timeline: () => memTimeline(recent.project),
    }[b.dataset.mem])()));
  };
  draw(null);
  if (c && c.project) memRecent(c.project).then(draw);
};

async function memFollowup(m) {
  await startCompose('reply_all', m.id);
  if ($('#composer').classList.contains('hidden')) return;
  const ai = await api('aiSettings').catch(() => null);
  if (!ai || !ai.ready) return;  // No AI: the reply is open for the user to write.
  try {
    await streamIntoComposer('draftReply', { mid: m.id, idea: '跟进：这是我之前发出的邮件，对方几天没有回复。礼貌地询问进展或请对方回复，简短，不要重复原文内容', note: '', allow_encrypted: false });
  } catch (err) { if (err.code !== 'AI_CANCELLED') fail(err); }
}
async function memNoFollow(mid) {
  const set = async (undo) => {
    await api('followupDismiss', { mid, undo });
    if (S.local === 'u-wait') await showUnderstand('wait', true);
    ucRefreshStatus();
    if (S.message) renderUcStrip(S.message);
  };
  try {
    await set(false);
    if (UC.wait) UC.wait.delete(mid);
    toast('已从「等回复」中移除', { ms: 8000, action: { label: '撤销', run: () => set(true).catch(fail) } });
  } catch (err) { fail(err); }
}

// ---------------------------------------------------------------- a project's memory (时间线)
async function memTimeline(project) {
  let data, only = '';
  try { data = await api('memoryFacts', { project }); } catch (err) { fail(err); return; }
  const tag = (f) => f.project !== data.project ? `<span class="mem-sys">${esc(f.project)}</span>` : '';
  const state = { current: '', superseded: '已更新', pending: '待确认', wrong: '' };
  const source = (f) => f.sources.slice(-2).map((s) => `<a data-mid="${esc(s.mid)}" title="打开出处邮件">${esc(s.subject || '（无主题）')}</a>` +
    `${s.internal ? '' : '<span class="mem-ext">外部</span>'}`).join('、');
  const bar = () => (data.systems.length ? '<div class="mem-filter">' + ['', data.project, ...data.systems].map((n, i) =>
      `<button class="btn ghost sm${only === n ? ' on' : ''}" data-only="${esc(n)}">${i === 0 ? '全部' : i === 1 ? '整个项目' : esc(n)}</button>`).join('') + '</div>' : '') +
    (data.parent ? `<div class="mem-filter"><span class="muted small">这是「${esc(data.parent)}」下的系统</span><button class="btn ghost sm" data-whole="${esc(data.parent)}">看整个 ${esc(data.parent)}</button></div>` : '');
  const shown = () => data.facts.filter((f) => !only || f.project === only);
  const html = () => bar() + (shown().length ? '<ul class="mem-facts">' + shown().map((f) => `<li class="mem-fact ${f.state}" data-fact="${esc(f.id)}">
      <div class="mem-head">${tag(f)}<span class="mem-type">${MEM_TYPE[f.type] || f.type}</span><span class="muted small">${esc(f.date)}</span>
        ${state[f.state] ? `<span class="mem-state">${state[f.state]}</span>` : ''}${f.actor === 'user' ? '<span class="mem-state">你写的</span>' : ''}
        <span class="spacer"></span>${f.state === 'pending' ? '<button class="btn ghost sm" data-fa="accept">接受</button><button class="btn ghost sm" data-fa="reject">不接受</button>'
          : f.state === 'current' ? '<button class="icon-btn sm" data-fa="menu" title="纠正这条记忆"><i data-icon="more"></i></button>' : ''}</div>
      <div class="mem-text">${esc(f.text)}</div>${f.was ? `<div class="muted small">之前：${esc(f.was)}</div>` : ''}
      ${f.sources.length ? `<div class="muted small mem-src">出处：${source(f)}</div>` : ''}</li>`).join('') + '</ul>'
    : '<p class="muted">这里还没有记下事实。鸿雁理解新邮件时会自动记下。</p>');
  const close = $('#prompt .modal-foot [data-close]');
  close.textContent = '关闭';
  const dlg = promptDialog({ title: `工作记忆 · ${data.project}`, body: `<div id="mem-timeline">${html()}</div>` }).finally(() => { close.textContent = '取消'; });
  const wire = () => {
    const root = $('#mem-timeline'); if (!root) return;
    renderIcons(root);
    $$('[data-mid]', root).forEach((a) => (a.onclick = () => { $('#prompt [data-close]').click(); memOpenMail(a.dataset.mid); }));
    $$('[data-only]', root).forEach((b) => (b.onclick = () => { only = b.dataset.only; root.innerHTML = html(); wire(); }));
    $$('[data-whole]', root).forEach((b) => (b.onclick = async () => {
      try { data = await api('memoryFacts', { project: b.dataset.whole }); only = ''; $('#prompt-title').textContent = `工作记忆 · ${data.project}`; root.innerHTML = html(); wire(); }
      catch (err) { fail(err); }
    }));
    $$('[data-fa]', root).forEach((b) => (b.onclick = async (e) => {
      const id = b.closest('[data-fact]').dataset.fact;
      const run = async (action, text = '') => {
        try {
          await api('memoryCorrect', { fact_id: id, action, text });
          data = await api('memoryFacts', { project: data.project });
          memReset(); root.innerHTML = html(); wire(); if (S.message) renderUcStrip(S.message);
        }
        catch (err) { fail(err); }
      };
      if (b.dataset.fa !== 'menu') { run(b.dataset.fa); return; }
      const r = b.getBoundingClientRect();
      showMenu(r.left, r.bottom + 4, [
        { label: '这条不对', icon: 'close', run: () => run('wrong') },
        { label: '已过时', icon: 'clock', run: () => run('stale') },
        { label: '改成…', icon: 'pen', run: () => {
          const li = b.closest('[data-fact]'), text = $('.mem-text', li).textContent;
          $('.mem-text', li).innerHTML = `<input class="mem-edit" maxlength="80" value="${esc(text)}"><button class="btn ghost sm">保存</button>`;
          const input = $('.mem-edit', li); input.focus(); input.select();
          const save = () => input.value.trim() && input.value.trim() !== text ? run('edit', input.value.trim()) : (root.innerHTML = html(), wire());
          $('button', $('.mem-text', li)).onclick = save;
          input.onkeydown = (k) => { if (k.key === 'Enter') { k.preventDefault(); save(); } };
        } }]);
      void e;
    }));
  };
  wire();
  await dlg;
}
function memOpenMail(mid) {
  const row = rowOf(mid);
  if (row) { selectMessage(mid); return; }
  api('openReader', { mid }).catch(fail);
}

// ---------------------------------------------------------------- 本周你需要知道的 (above 需要知道)
async function memTop(kind) {
  const top = $('#uc-top');
  if (kind !== 'know') { top.classList.add('hidden'); top.innerHTML = ''; return; }
  let week;
  try { week = await api('memoryWeek', { offset: MEM.week }); } catch (err) { top.classList.add('hidden'); return; }
  if (S.local !== 'u-know') return;
  const count = week.groups.reduce((n, g) => n + g.facts.length, 0);
  const label = MEM.week ? '上周' : '本周';
  if (!count && !MEM.week && !MEM.weekOpen) { top.classList.add('hidden'); top.innerHTML = ''; return; }
  top.classList.remove('hidden');
  const key = `${MEM.week}|${week.from}|${count}`, brief = MEM.brief && MEM.brief.key === key ? MEM.brief : null;
  const fact = (f, g) => `<li>${f.project !== g.project ? `<span class="mem-sys">${esc(f.project)}</span>` : ''}<span class="mem-type">${MEM_TYPE[f.type] || f.type}</span>${esc(f.text)}${f.was ? `<span class="muted small">（之前：${esc(f.was)}）</span>` : ''}` +
    `${f.state === 'superseded' ? '<span class="muted small">（后来又更新了）</span>' : ''}</li>`;
  const group = (g) => {
    const sum = brief && brief.data && brief.data.projects[g.project], all = MEM.weekAll.has(g.project);
    const head = sum ? `${sum.brief ? `<p class="mem-week-brief">${esc(sum.brief)}</p>` : ''}${sum.points.length ? `<ul class="mem-week-points">${sum.points.map((p) => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}`
      : (brief && brief.loading ? '<p class="muted small">正在概括…</p>' : '') + (all ? '' : `<ul>${g.key.map((id) => g.facts.find((f) => f.id === id)).filter(Boolean).map((f) => fact(f, g)).join('')}</ul>`);
    const more = g.facts.length > (sum ? 0 : g.key.length) ? `<a class="mem-week-more" data-mw="all" data-project="${esc(g.project)}">${all ? '收起明细' : `看全部 ${g.facts.length} 条`}</a>` : '';
    return `<div class="mem-week-group"><a class="mem-week-project" data-mw="project" data-project="${esc(g.project)}">${esc(g.project)}</a>${head}` +
      (all ? `<ul class="mem-week-all">${g.facts.map((f) => fact(f, g)).join('')}</ul>` : '') + more + '</div>';
  };
  top.innerHTML = `<div class="mem-week-head" data-mw="toggle"><i data-icon="chevron" class="mem-chev${MEM.weekOpen ? ' open' : ''}"></i><b>${label}你需要知道的</b>` +
    `<span class="muted small">${count ? `${week.groups.length} 个项目 · ${count} 条` : '没有新的事实'}（${esc(week.from.slice(5))} – ${esc(week.to.slice(5))}）</span><span class="spacer"></span>` +
    `<button class="btn ghost sm" data-mw="other">${MEM.week ? '本周' : '上周'}</button></div>` +
    (MEM.weekOpen ? '<div class="mem-week">' + week.groups.map(group).join('') +
      (brief && brief.data ? `<p class="muted small mem-week-foot">AI 概括 · ${esc(brief.data.at || '')}<a data-mw="refresh">重新概括</a></p>` : '') + '</div>' : '');
  renderIcons(top);
  $$('[data-mw]', top).forEach((b) => (b.onclick = (e) => {
    e.stopPropagation();
    if (b.dataset.mw === 'toggle') MEM.weekOpen = !MEM.weekOpen;
    else if (b.dataset.mw === 'other') { MEM.week = MEM.week ? 0 : 1; MEM.weekOpen = true; }
    else if (b.dataset.mw === 'all') { MEM.weekAll.has(b.dataset.project) ? MEM.weekAll.delete(b.dataset.project) : MEM.weekAll.add(b.dataset.project); }
    else if (b.dataset.mw === 'refresh') { memBrief(key, true); return; }
    else { memTimeline(b.dataset.project); return; }
    memTop('know');
  }));
  if (MEM.weekOpen && count && !brief) memBrief(key, false);
}
// The AI summary of the week: asked once per week and set of facts (the backend keeps it too);
// without AI, or when it fails, each project shows its few key facts instead.
function memBrief(key, refresh) {
  MEM.brief = { key, loading: true };
  memTop('know');
  api('memoryWeekBrief', { offset: MEM.week, refresh }).then((data) => ({ key, data }), () => ({ key, failed: true })).then((done) => {
    if (MEM.brief && MEM.brief.key === key) { MEM.brief = done; if (S.local === 'u-know') memTop('know'); }
  });
}
const _memRenderUcNav = renderUcNav;
renderUcNav = function () { _memRenderUcNav(); if (S.local !== 'u-know' && !$('#uc-top').classList.contains('hidden')) memTop(''); };

// ---------------------------------------------------------------- 人物卡片 (the person popover)
window.personExtra = async function (pop, person) {
  if (!ucOn() || !person.email) return;
  let card;
  try { card = await api('memoryPerson', { email: person.email, name: person.name || '' }); } catch (err) { return; }
  if (!card.on || !pop.isConnected) return;
  const list = (title, rows, text) => rows.length ? `<div class="pp-mem"><b class="small">${title}</b><ul>${rows.map((r) => `<li${r.id ? ` data-mid="${esc(r.id)}"` : ''}>${text(r)}</li>`).join('')}</ul></div>` : '';
  const html = list('负责', card.owner, (f) => `${esc(f.text)}<span class="muted small"> · ${esc(f.project)}</span>`) +
    list('等我处理', card.waiting, (r) => esc(r.ask || r.subject)) +
    list('等他回复', card.chase, (r) => `${esc(r.subject)}<span class="muted small"> · 已等 ${r.waiting} 天</span>`) +
    list('最近往来', card.recent, (r) => `${esc(r.subject || '（无主题）')}<span class="muted small"> · ${esc(String(r.date).slice(5, 10))}</span>`);
  if (!html) return;
  const old = $('.pp-mem-box', pop); if (old) old.remove();
  const box = document.createElement('div'); box.className = 'pp-mem-box'; box.innerHTML = html;
  $('.pp-actions', pop).before(box);
  $$('[data-mid]', box).forEach((li) => (li.onclick = () => { closePersonPop(); memOpenMail(li.dataset.mid); }));
};

// ---------------------------------------------------------------- 设置 → 工作记忆 (the project list)
async function renderMemProjects(on) {
  const box = $('#mem-projects');
  $('#mem-add').disabled = $('#mem-new').disabled = !on;
  if (!on) { box.innerHTML = '<p class="muted small">开启「理解新邮件」后可用。</p>'; return; }
  let projects;
  try { projects = await api('memoryProjects'); } catch (err) { box.innerHTML = ''; return; }
  const tops = projects.filter((p) => !p.parent);
  box.innerHTML = projects.length ? '<ul class="uc-rule-list">' + projects.map((p) => `<li data-project="${esc(p.name)}" class="${p.parent ? 'mem-system' : ''}">
      <span>${p.parent ? '<span class="muted">└ </span>' : ''}<b>${esc(p.name)}</b>${p.aliases.length ? `<span class="muted small">（也叫 ${p.aliases.map(esc).join('、')}）</span>` : ''}
        <span class="muted small"> · ${p.parent ? '系统 · ' : p.systems ? `${p.systems} 个系统 · ` : ''}${p.source === 'task' ? '来自任务 · ' : ''}${p.facts} 条记忆${p.pending ? `，${p.pending} 条待确认` : ''}</span></span>
      ${p.parent ? '' : `<select data-mp="role" title="我负责的项目里，进展默认要你知道；我关注的项目只记事实"><option value="own"${p.role === 'own' ? ' selected' : ''}>我负责</option><option value="watch"${p.role === 'watch' ? ' selected' : ''}>我关注</option></select>`}
      <button class="btn ghost sm" data-mp="view">查看</button><button class="btn ghost sm" data-mp="rename" title="改名；旧名字自动作为别名，邮件和任务里写旧名字照样归到这里">改名</button>
      <button class="btn ghost sm" data-mp="alias" title="把别的叫法合并到这里">别名</button>
      ${p.systems ? '' : `<button class="btn ghost sm" data-mp="parent" title="${p.parent ? '换到别的项目，或作为独立项目' : '作为某个项目下的系统'}">${p.parent ? '移到…' : '归到项目…'}</button>`}
      ${p.source === 'manual' ? '<button class="btn ghost sm" data-mp="remove" title="从名单中移除（已记下的事实保留）">移除</button>' : ''}</li>`).join('') + '</ul>'
    : '<p class="muted small">名单是空的：任务中心还没有未完成的任务，也没有手动添加的项目。</p>';
  $$('[data-mp]', box).forEach((el) => {
    const name = el.closest('[data-project]').dataset.project, p = projects.find((x) => x.name === name);
    const save = (args) => api('memoryProject', { name, ...args }).then(() => { memReset(); renderMemProjects(true); }).catch(fail);
    if (el.dataset.mp === 'role') { el.onchange = () => save({ role: el.value }); return; }
    el.onclick = async () => {
      if (el.dataset.mp === 'view') { memTimeline(name); return; }
      if (el.dataset.mp === 'remove') { save({ remove: true }); return; }
      if (el.dataset.mp === 'parent') {
        const r = el.getBoundingClientRect();
        showMenu(r.left, r.bottom + 4, [...tops.filter((t) => t.name !== name && t.name !== p.parent).map((t) => ({ label: `归到「${t.name}」`, icon: 'folder', run: () => save({ parent: t.name }) })),
          ...(p.parent ? [{ label: '作为独立项目', icon: 'undo', run: () => save({ parent: '' }) }] : [])]);
        return;
      }
      if (el.dataset.mp === 'rename') {
        const value = await promptDialog({ title: `给「${name}」改名`, okText: '改名',
          body: `<p class="muted small">「${esc(name)}」会作为别名保留：邮件里、任务的「系统」里写旧名字，照样归到这里；已记下的事实一起改过来。任务中心里的「系统」字段不会被修改。</p>` +
            `<input id="mem-rename" type="text" maxlength="30" spellcheck="false" value="${esc(name)}">`,
          collect: () => $('#mem-rename').value.trim() });
        if (value && value !== name) save({ rename: value });
        return;
      }
      const value = await promptDialog({ title: `「${name}」的别名`, okText: '保存',
        body: '<p class="muted small">邮件里对同一个项目或系统的其他叫法，一行一个；名单里同名的会合并进来，它的记忆也一起移过来。</p>' +
          `<textarea id="mem-aliases" rows="4" spellcheck="false">${esc(p.aliases.join('\n'))}</textarea>`,
        collect: () => $('#mem-aliases').value.split(/\n|[,，、]/).map((a) => a.trim()).filter(Boolean) });
      if (value) save({ aliases: value });
    };
  });
}
$('#mem-add').onclick = async () => {
  const name = $('#mem-new').value.trim();
  if (!name) return;
  try { await api('memoryProject', { name }); $('#mem-new').value = ''; memReset(); renderMemProjects(true); } catch (err) { fail(err); }
};
$('#mem-new').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); $('#mem-add').click(); } });
