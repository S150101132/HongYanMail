'use strict';
/* 鸿雁 · 工作记忆 (M2) and its uses (M3). The 理解层 remembers the facts of the user's projects
   (backend memory_*); here: the reader's 「这个项目最近」 and task candidate, the project's
   timeline with corrections, 本周你需要知道的 (on 今天 → 本周概括), 等回复 (催办) with 起草跟进,
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
  // 阅读条合并 (R1b): one strip above the mail. The task candidate is a button on it; 这个项目最近
  // opens from its project name and stays open (or closed) for the next mail.
  const strip = $('#uc-strip');
  const linked = typeof TK !== 'undefined' && TK.mailIndex && (TK.mailIndex[m.id] || []).length;
  if (c && c.task && !linked) {
    const title = `可能与任务 #${c.task} 有关${c.why ? '：' + c.why : ''}。点击把这封邮件关联到这条任务`;
    if (strip) $('[data-uc="menu"]', strip).insertAdjacentHTML('beforebegin', `<button class="btn ghost" data-mem="link" title="${esc(title)}">关联 #${c.task}</button>`);
    else lines.push(`<div class="mem-line"><i data-icon="tasks"></i><span class="uc-text muted">${esc(title)}</span><button class="btn ghost" data-mem="link">关联</button></div>`);
  }
  const chip = strip && S.prefs.memoryFacts && $('.uc-project', strip);  // R3c: no facts kept, nothing to open.
  if (chip) {
    chip.classList.add('link'); chip.classList.toggle('on', memRecentOpen());
    chip.title = memRecentOpen() ? '收起「这个项目最近」' : '看这个项目最近的情况';
    chip.onclick = () => { memRecentOpen(!memRecentOpen()); renderUcStrip(m); };
  }
  const token = MEM.strip = (MEM.strip || 0) + 1;
  const draw = (recent) => {
    if (token !== MEM.strip || !S.message || S.message.id !== m.id) return;  // Another mail, or drawn again since.
    const extra = recent && recent.facts.length ? `<div class="mem-line recent" data-mem="timeline" title="点击查看「${esc(recent.project)}」的全部记忆"><i data-icon="spark"></i>` +
      `<span class="uc-text"><b>这个项目最近：</b>${recent.facts.map((f) => esc(f.text)).join('；')}</span>` +
      (recent.pending ? `<span class="uc-due late">${recent.pending} 条待确认</span>` : '') + '</div>' : '';
    box.innerHTML = lines.join('') + extra;
    if (!box.innerHTML) box.remove();
    else if (!box.isConnected) ($('#uc-strip') || $('#r-banners')).after(box);
    renderIcons(box);
    $$('[data-mem]', box).forEach((b) => (b.onclick = () => ({
      follow: () => memFollowup(m), nofollow: () => memNoFollow(m.id),
      link: () => api('taskLink', { id: c.task, mid: m.id }).then(() => { toast(`已关联到任务 #${c.task}`); if (typeof loadMailIndex === 'function') loadMailIndex(); renderUcStrip(m); }).catch(fail),
      timeline: () => memTimeline(recent.project),
    }[b.dataset.mem])()));
  };
  draw(null);
  if (strip) $$('[data-mem="link"]', strip).forEach((b) => (b.onclick = () =>
    api('taskLink', { id: c.task, mid: m.id }).then(() => { toast(`已关联到任务 #${c.task}`); if (typeof loadMailIndex === 'function') loadMailIndex(); renderUcStrip(m); }).catch(fail)));
  if (c && c.project && S.prefs.memoryFacts && memRecentOpen()) memRecent(c.project).then(draw);
};
function memRecentOpen(open) {
  try { if (open !== undefined) localStorage.setItem('hy-mem-recent', open ? '1' : '0'); return localStorage.getItem('hy-mem-recent') === '1'; }
  catch (e) { return MEM.recentOpen = open === undefined ? !!MEM.recentOpen : open; }
}

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

// ---------------------------------------------------------------- 本周你需要知道的 (今天 → 本周概括)
async function memTop(kind) {
  const top = $('#uc-top');
  if (kind !== 'week') { top.classList.add('hidden'); top.innerHTML = ''; return; }
  let week;
  try { week = await api('memoryWeek', { offset: MEM.week }); } catch (err) { top.classList.add('hidden'); return; }
  if (!memWeekHere()) return;
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
    memTop('week');
  }));
  if (MEM.weekOpen && count && !brief) memBrief(key, false);
}
// The AI summary of the week: asked once per week and set of facts (the backend keeps it too);
// without AI, or when it fails, each project shows its few key facts instead.
function memBrief(key, refresh) {
  MEM.brief = { key, loading: true };
  memTop('week');
  api('memoryWeekBrief', { offset: MEM.week, refresh }).then((data) => ({ key, data }), () => ({ key, failed: true })).then((done) => {
    if (MEM.brief && MEM.brief.key === key) { MEM.brief = done; if (memWeekHere()) memTop('week'); }
  });
}
const _memRenderUcNav = renderUcNav;
renderUcNav = function () { _memRenderUcNav(); if (!memWeekHere() && !$('#uc-top').classList.contains('hidden')) memTop(''); };
// 本周概括 (R2a): shown on 今天 → 本周概括, no longer above the inbox's 知会.
function memWeekHere() { return S.local === 'today' && typeof TD !== 'undefined' && TD.sub === 'week'; }

// ---------------------------------------------------------------- 人物卡片 (the person popover)
window.personExtra = async function (pop, person) {
  if (!ucOn() || !person.email) return;
  let card;
  try { card = await api('memoryPerson', { email: person.email, name: person.name || '' }); } catch (err) { return; }
  if (!card.on || !pop.isConnected) return;
  const list = (title, rows, text) => rows.length ? `<div class="pp-mem"><b class="small">${title}</b><ul>${rows.map((r) => `<li${r.id ? ` data-mid="${esc(r.id)}"` : ''}>${text(r)}</li>`).join('')}</ul></div>` : '';
  const html = list('关于我', (card.about || []).map((text) => ({ text })), (r) => esc(r.text)) +
    list('负责', card.owner, (f) => `${esc(f.text)}<span class="muted small"> · ${esc(f.project)}</span>`) +
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
$('#mem-facts').onchange = async () => {
  try { S.prefs = await api('setPrefs', { values: { memoryFacts: $('#mem-facts').checked } }); memReset(); if (S.message) renderUcStrip(S.message); } catch (err) { fail(err); }
};
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

// ---------------------------------------------------------------- 设置 → 关于我 (Me1)
// What Hongyan knows about the user, sent along with the AI calls. The AI drafts it once
// (生成初版, optionally from the user's own notes); only the lines the user keeps are saved.
const ME = { ref: '', data: null, busy: false, picked: new Set() };
const ME_SRC = { ref: '参考资料', mail: '邮件', dir: '通讯录', task: '任务', fix: '纠正', chat: '问鸿雁' };
const ME_EMPTY = {
  role: '还没有。例如「浪潮集团，常驻利雅得 NG 项目现场；具体职级未确认」。',
  projects: '还没有。每个系统写一句你做什么、不归你什么，例如「NG BI：报表口径和开发归我；源系统故障不归我修」。',
  people: '还没有。例如「沙特分公司领导：直接领导（姓名未确认）」「TNOC：BI 报表问题的来源之一」。',
  ways: '还没有。例如「客户邮件用英文，同事用中文」「DBA、网络、AD 的专业操作我只协调」。',
};
const meSource = (e) => e.by === 'user' ? (e.src === 'fix' ? '你纠正时写的' : e.src === 'chat' ? '你在问鸿雁里说的' : e.src ? '你改过' : '你写的') : `AI 整理自${ME_SRC[e.src] || '资料'}`;
// The run shows on the page itself — step and seconds, then the outcome — not only in a toast.
const meStatus = (text, kind = '') => {
  const el = $('#me-status');
  el.className = 'me-status small' + (kind ? ' ' + kind : '');
  el.innerHTML = (kind === 'busy' ? '<span class="tk-spin"></span>' : '') + esc(text);
};

async function renderMe() {
  try { ME.data = await api('aboutMe'); } catch (err) { ME.data = null; meStatus((err && err.message) || ''); }
  const d = ME.data;
  $('#me-draft').disabled = $('#me-ref').disabled = !d || !!ME.busy;
  if (!d) { ['pending', 'role', 'projects', 'people', 'ways'].forEach((k) => { $('#me-' + k).innerHTML = ''; }); return; }
  const titles = Object.fromEntries(d.groups.map((g) => [g.key, g.title]));
  const waiting = d.entries.filter((e) => e.state === 'pending');
  $('#me-pending').innerHTML = waiting.length ? `<div class="me-head"><b>鸿雁想记住 ${waiting.length} 条</b><span class="muted small">从最近的邮件里读到的、关于你的新情况；点「记住」才会生效</span></div>` +
    '<ul class="uc-rule-list me-list">' + waiting.map((e) => `<li data-me="${esc(e.id)}"><span><span class="muted small">${esc(titles[e.group])} · </span>${esc(e.text)}` +
      `${e.replaces ? `<span class="muted small">（取代：${esc(e.replaces)}）</span>` : ''}` +
      `${e.mid ? ` <a href="#" class="small" data-me-mail="${esc(e.mid)}">来源邮件</a>` : ''}</span>` +
      '<button class="btn ghost sm" data-me-act="no">不用</button><button class="btn sm" data-me-act="keep">记住</button></li>').join('') + '</ul>' : '';
  if (!ME.busy) $('#me-draft').textContent = d.entries.length ? '重新生成' : '生成初版';
  $('#me-draft').title = d.entries.length ? '再起草一次：只列出还没有的条目，已有的不会被改动' : 'AI 根据参考资料、通讯录、最近 3 个月的已发邮件和任务起草，你确认后才保存';
  for (const g of d.groups) {
    const items = d.entries.filter((e) => e.group === g.key && e.state === 'current');
    const past = d.entries.filter((e) => e.group === g.key && e.state === 'past');
    $('#me-' + g.key).innerHTML = `<div class="me-head"><b>${esc(g.title)}</b><span class="muted small">${items.length ? `${items.length} 条` : ''}` +
      `${g.left ? `，太长，AI 只看到前 ${items.length - g.left} 条` : ''}</span><span class="spacer"></span>` +
      (items.length > 1 ? `<button class="btn ghost sm" data-me-all="${g.key}" title="选中这一组的全部条目，再在上面点「删除所选」">全选</button>` : '') +
      `<button class="btn ghost sm" data-me-add="${g.key}">添加</button></div>` +
      (items.length ? '<ul class="uc-rule-list me-list">' + items.map((e) => `<li data-me="${esc(e.id)}">` +
        `<input type="checkbox" class="me-tick" data-me-tick="${esc(e.id)}"${ME.picked.has(e.id) ? ' checked' : ''} title="选中，可批量删除"><span>${esc(e.text)}` +
        `${e.email ? ` <span class="muted small">${esc(e.email)}</span>` : ''}<span class="muted small"> · ${esc(meSource(e))}</span></span>` +
        '<button class="btn ghost sm" data-me-act="edit">编辑</button><button class="btn ghost sm" data-me-act="del">删除</button></li>').join('') + '</ul>'
        : `<p class="muted small">${esc(ME_EMPTY[g.key])}</p>`) +
      (past.length ? `<details class="me-past"><summary class="muted small">以前的 ${past.length} 条</summary><ul class="uc-rule-list me-list">` +
        past.map((e) => `<li data-me="${esc(e.id)}"><span class="muted">${esc(e.text)}<span class="small"> · ${esc(e.until)} 被取代</span></span>` +
          '<button class="btn ghost sm" data-me-act="restore">恢复</button></li>').join('') + '</ul></details>' : '');
  }
  $$('[data-me-mail]').forEach((a) => (a.onclick = (ev) => { ev.preventDefault(); $('#settings').classList.add('hidden'); memOpenMail(a.dataset.meMail); }));
  const live = new Set(d.entries.filter((e) => e.state === 'current').map((e) => e.id));
  ME.picked.forEach((id) => { if (!live.has(id)) ME.picked.delete(id); });
  $$('[data-me-tick]').forEach((c) => (c.onchange = () => { c.checked ? ME.picked.add(c.dataset.meTick) : ME.picked.delete(c.dataset.meTick); meBulk(); }));
  $$('[data-me-all]').forEach((b) => (b.onclick = () => {
    const ids = d.entries.filter((e) => e.group === b.dataset.meAll && e.state === 'current').map((e) => e.id);
    const all = ids.every((id) => ME.picked.has(id));
    ids.forEach((id) => (all ? ME.picked.delete(id) : ME.picked.add(id)));
    $$('[data-me-tick]').forEach((c) => { c.checked = ME.picked.has(c.dataset.meTick); });
    meBulk();
  }));
  meBulk();
  $$('[data-me-add]').forEach((b) => (b.onclick = () => meEdit(b.dataset.meAdd)));
  $$('[data-me-act]').forEach((b) => (b.onclick = () => {
    const e = d.entries.find((x) => x.id === b.closest('[data-me]').dataset.me);
    if (b.dataset.meAct === 'edit') { meEdit(e.group, e); return; }
    if (b.dataset.meAct === 'keep' || b.dataset.meAct === 'no') { api('aboutPending', { id: e.id, keep: b.dataset.meAct === 'keep' }).then(() => renderMe()).catch(fail); return; }
    if (b.dataset.meAct === 'restore') { api('aboutRestore', { id: e.id }).then(() => renderMe()).catch(fail); return; }
    api('aboutEntry', { id: e.id, remove: true }).then(() => renderMe()).catch(fail);
  }));
}

async function meEdit(group, e) {
  const title = ME.data.groups.find((g) => g.key === group).title;
  const value = await promptDialog({ title: `${e ? '编辑' : '添加'} · ${title}`, okText: '保存',
    body: `<textarea id="me-text" rows="3" maxlength="120" spellcheck="false" placeholder="${esc(ME_EMPTY[group].replace(/^还没有。/, ''))}">${esc(e ? e.text : '')}</textarea>` +
      (group === 'people' ? `<input id="me-email" type="text" maxlength="200" spellcheck="false" placeholder="邮箱（可不填）" value="${esc(e ? e.email : '')}">` : ''),
    collect: () => ({ text: $('#me-text').value.trim(), email: $('#me-email') ? $('#me-email').value.trim() : '' }) });
  if (!value || !value.text) return;
  try { await api('aboutEntry', { id: e ? e.id : '', group, text: value.text, email: value.email }); renderMe(); } catch (err) { fail(err); }
}

$('#me-ref').onclick = async () => {
  const path = await api('pickReference').catch(fail);
  if (!path) return;
  ME.ref = path;
  $('#me-ref-name').innerHTML = `${esc(path.split(/[\\/]/).pop())} <a href="#" id="me-ref-clear">不用</a>`;
  $('#me-ref-clear').onclick = (ev) => { ev.preventDefault(); ME.ref = ''; $('#me-ref-name').textContent = ''; };
};

$('#me-draft').onclick = async () => {
  if (ME.busy) return;
  const button = $('#me-draft'), started = Date.now();
  let step = '第 1 步，共 2 步：读取最近 3 个月的已发邮件和通讯录（只读）';
  const tick = () => meStatus(`${step}… 已用 ${Math.round((Date.now() - started) / 1000)} 秒`, 'busy');
  ME.busy = true; button.disabled = true; button.textContent = '正在生成…';
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const read = await api('aboutPrepare');
    step = `第 2 步，共 2 步：AI 正在起草（读取了 ${read.sent} 封已发邮件${read.failed ? `，${read.failed} 封没能下载` : ''}），通常要一分钟左右`;
    tick();
    const draft = await api('aboutDraft', { reference: ME.ref });
    clearInterval(timer);
    if (!draft.entries.length) {
      meStatus(draft.ai ? '起草完成，但没有新的条目：已有的都写过了。' : '还没有设置 AI（设置 → AI 助手），通讯录里也没有你的部门和职务，没有可起草的内容。', 'warn');
      return;
    }
    meStatus(`起草了 ${draft.entries.length} 条，请在弹出的窗口里检查。`, 'ok');
    const picked = await meReview(draft);
    if (!picked || !picked.length) { meStatus(`起草了 ${draft.entries.length} 条，这次没有保存；可以再点「生成初版」。`, 'warn'); return; }
    const saved = await api('aboutAdd', { entries: picked });
    meStatus(`已保存 ${saved.added} 条。`, 'ok');
  } catch (err) {
    meStatus(`没有完成：${(err && err.message) || '操作未完成，请稍后重试。'}`, 'error');
  } finally { clearInterval(timer); ME.busy = false; renderMe(); }
};

function meReview(draft) {
  const u = draft.used || {};
  const from = [u.reference && '参考资料', u.directory && '通讯录', u.sent && `${u.sent} 封已发邮件`, u.people && `${u.people} 位常联系的人`, u.tasks && `${u.tasks} 条任务`].filter(Boolean);
  const titles = Object.fromEntries(ME.data.groups.map((g) => [g.key, g.title]));
  const rows = ME.data.groups.map((g) => {
    const items = draft.entries.map((e, i) => [e, i]).filter(([e]) => e.group === g.key);
    return items.length ? `<b class="small">${esc(titles[g.key])}</b>` + items.map(([e, i]) => `<label class="me-pick"><input type="checkbox" data-mi="${i}" checked>` +
      `<input type="text" data-mt="${i}" maxlength="120" spellcheck="false" value="${esc(e.text)}">${e.email ? `<span class="muted small">${esc(e.email)}</span>` : ''}</label>`).join('') : '';
  }).join('');
  const note = (from.length ? `参考了${from.join('、')}` : '没有可参考的材料') + (u.skipped ? `；${u.skipped} 封加密邮件没有读取（可在「AI 助手」里允许）` : '') +
    '。逐条检查：不对的改掉或取消勾选，只有勾选的会保存。' + (draft.ai ? '' : '还没有设置 AI，只取了通讯录。');
  return promptDialog({ title: '「关于我」初版', okText: '保存勾选的', body: `<p class="muted small">${esc(note)}</p><div class="me-review">${rows}</div>`,
    collect: () => draft.entries.map((e, i) => {
      if (!$(`[data-mi="${i}"]`).checked) return null;
      const text = $(`[data-mt="${i}"]`).value.trim();
      return text ? { group: e.group, text, email: e.email, src: e.src, edited: text !== e.text } : null;
    }).filter(Boolean) });
}

const _meRenderAiSettings = renderAiSettings;
renderAiSettings = function () { _meRenderAiSettings(); renderMe(); };

// 纠正时说为什么 (Me2): from the reading strip's menu; kept in 关于我 at once, as the user's own.
async function meWhy(mid, c) {
  const guess = c.kind === 'act' ? 'ways' : c.project ? 'projects' : 'ways';
  const value = await promptDialog({ title: '告诉鸿雁为什么', okText: '记住',
    body: '<p class="muted small">写一句为什么这封判断错了，鸿雁会记进「关于我」，以后理解邮件、回答和写信都参考它。例如「MAXIMO 宕机不归我修，我只协调」。</p>' +
      `<textarea id="me-why" rows="3" maxlength="120" spellcheck="false"></textarea>` +
      `<label class="pref-row">记在<select id="me-why-group">${[['ways', '工作方式'], ['projects', '项目和系统'], ['people', '人'], ['role', '我的岗位']]
        .map(([k, t]) => `<option value="${k}"${k === guess ? ' selected' : ''}>${t}</option>`).join('')}</select></label>`,
    collect: () => ({ text: $('#me-why').value.trim(), group: $('#me-why-group').value }) });
  if (!value || !value.text) return;
  try { await api('aboutEntry', { group: value.group, text: value.text, src: 'fix', mid }); toast('已记进「关于我」'); } catch (err) { fail(err); }
}

// 批量删除: tick entries (or 全选 a group), then 删除所选 once.
function meBulk() {
  const bar = $('#me-bulk'), n = ME.picked.size;
  bar.classList.toggle('hidden', !n);
  if (!n) { bar.innerHTML = ''; return; }
  bar.innerHTML = `<span>已选 ${n} 条</span><span class="spacer"></span><button class="btn ghost sm" id="me-bulk-none">取消选择</button>` +
    `<button class="btn sm danger" id="me-bulk-del">删除所选</button>`;
  $('#me-bulk-none').onclick = () => { ME.picked.clear(); $$('[data-me-tick]').forEach((c) => { c.checked = false; }); meBulk(); };
  $('#me-bulk-del').onclick = async () => {
    if (!await confirmDialog(`删除 ${n} 条？`, '删除后 AI 就不再参考这些条目；不能撤销，需要时可以重新添加。', '删除')) return;
    try { await api('aboutRemove', { ids: [...ME.picked] }); toast(`已删除 ${n} 条`); ME.picked.clear(); renderMe(); } catch (err) { fail(err); }
  };
}
