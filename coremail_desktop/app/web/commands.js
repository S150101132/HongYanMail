'use strict';
/* 鸿雁 · 统一命令入口 (Ctrl+K): one box for commands, folders, settings pages, simple search
   conditions and questions. Matching is local and needs no AI; what matches nothing goes to
   问邮箱 (ai.js), and in 任务 to the task assistant (tasks.js). Loaded last.
   The first half is plain functions without the page (tests run them in node). */

const CMD_SPACE = /[\s·、，,。.；;：:！!？?“”"'（）()【】\[\]]/g;
function cmdNorm(text) { return String(text || '').toLowerCase().replace(CMD_SPACE, ''); }

// -> 0 (no match) … 100 (the whole name). A name inside the query counts too ("打开设置").
function cmdScore(names, query) {
  const q = cmdNorm(query);
  if (!q) return 0;
  let best = 0;
  for (const name of names) {
    const n = cmdNorm(name);
    if (!n) continue;
    let s = 0;
    if (n === q) s = 100;
    else if (n.startsWith(q)) s = 85 - Math.min(10, n.length - q.length);
    else if (n.includes(q)) s = 70 - Math.min(10, n.length - q.length);
    else if (n.length >= 2 && q.includes(n) && q.length - n.length <= 4) s = 60 - (q.length - n.length);
    else {  // Every character in order: "写邮" → 写邮件.
      let i = 0;
      for (const ch of n) if (ch === q[i]) i++;
      if (i === q.length && q.length >= 2) s = 30;
    }
    best = Math.max(best, s);
  }
  return best;
}

// Questions go to the AI first; anything else to the commands and the search.
const CMD_QUESTION = /[?？]|吗$|吗[，,]|什么|怎么|为什么|谁|哪|多少|是否|有没有|是不是|几时|何时|总结|帮我/;
function cmdIsQuestion(text) { return CMD_QUESTION.test(text); }

const CMD_NUM = { 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10, 半: 0.5 };
function cmdDay(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
function cmdShift(today, days) { const d = new Date(today.getFullYear(), today.getMonth(), today.getDate()); d.setDate(d.getDate() - days); return d; }

// Simple search conditions in plain words: "张三 上周 带附件的报价" -> {filters: {from, since,
// until, attachment}, words: '报价', labels}. Filter names are the backend's (backend.search_filters).
function cmdParse(text, today = new Date()) {
  let rest = ` ${String(text || '')} `;
  const filters = {}, labels = [];
  const take = (re, fn) => { rest = rest.replace(re, (...m) => { fn(m); return ' '; }); };
  take(/(?:只看|只显示|显示)?未读(?:的)?/, () => { filters.unread = true; labels.push('未读'); });
  take(/(?:加了?|标了?)?星标(?:的)?|标星(?:的)?/, () => { filters.flagged = true; labels.push('星标'); });
  take(/(?:带|有|含)(?:着)?附件(?:的)?/, () => { filters.attachment = true; labels.push('有附件'); });
  take(/仅(?:搜|看)?主题|只(?:搜|看)主题/, () => { filters.scope = 'subject'; labels.push('仅主题'); });
  const range = (since, until, label) => { filters.since = cmdDay(since); if (until) filters.until = cmdDay(until); labels.push(label); };
  const monday = cmdShift(today, (today.getDay() + 6) % 7);
  take(/今天|今日/, () => range(today, null, '今天'));
  take(/昨天|昨日/, () => { const y = cmdShift(today, 1); range(y, y, '昨天'); });
  take(/上周|上个?星期|上个?礼拜/, () => range(cmdShift(monday, 7), cmdShift(monday, 1), '上周'));
  take(/本周|这周|这个?星期|这个?礼拜/, () => range(monday, null, '本周'));
  take(/上个?月/, () => range(new Date(today.getFullYear(), today.getMonth() - 1, 1), new Date(today.getFullYear(), today.getMonth(), 0), '上个月'));
  take(/本月|这个月/, () => range(new Date(today.getFullYear(), today.getMonth(), 1), null, '本月'));
  take(/今年/, () => range(new Date(today.getFullYear(), 0, 1), null, '今年'));
  take(/(?:最近|近|过去)\s*(\d{1,3}|[一两二三四五六七八九十半])\s*(?:个)?\s*(天|日|周|星期|礼拜|月|年)(?:内|以来)?|(\d{1,3}|[一两二三四五六七八九十])\s*(天|日|周|个月)内/, (m) => {
    const n = m[1] || m[3], unit = m[2] || m[4];
    const count = /\d/.test(n) ? +n : CMD_NUM[n];
    const [days, name] = ({ 天: [1, '天'], 日: [1, '天'], 周: [7, '周'], 星期: [7, '周'], 礼拜: [7, '周'], 个月: [30, '个月'], 月: [30, '个月'], 年: [365, '年'] })[unit];
    range(cmdShift(today, Math.round(count * days)), null, `近 ${count} ${name}`);
  });
  take(/(?:来自|发件人[是为]?)\s*([^\s的，,]{1,20}?)(?=\s|的|发|$)(?:的)?/, (m) => { filters.from = m[1]; labels.push('发件人：' + m[1]); });
  if (!filters.from) {
    take(/(?:^|\s)([^\s，,的]{1,20}?)(?:发来的|发给我的|发的|寄来的)/, (m) => { filters.from = m[1]; labels.push('发件人：' + m[1]); });
  }
  const words = rest.replace(/(?:帮我)?(?:搜索?一下|搜索|查找|查一下|找一下|找找|找出|找|看看|显示|列出)/g, ' ')
    .replace(/(?:所有|全部|有关|关于)(?:的)?/g, ' ').replace(/(?:的)?邮件/g, ' ').replace(/(?:^|\s)的(?=\s|$)/g, ' ')
    .replace(/\s+/g, ' ').trim();
  return { filters, words, labels };
}

// The page part --------------------------------------------------------------
if (typeof document !== 'undefined') {
  const P = { items: [], index: 0 };
  const hasMail = () => !READER_MID && S.message && S.multi.size < 2 && !$('#reader-body').classList.contains('hidden') && S.selected === S.message.id;
  const visible = (el) => !!el && !el.closest('.hidden') && el.offsetParent !== null;
  // A toolbar button as a command: its label (or its title without the shortcut), the
  // shortcut from "回复 (R)", and the full title to match on. Icon buttons have no label.
  const fromButton = (group, b) => {
    const label = b.querySelector('span') && b.querySelector('span').textContent.trim();
    const m = /^(.*?)\s*[（(]([A-Za-z0-9+ ]{1,14})[)）]\s*$/.exec(b.title || '');  // Only a key: not "（正文会发送到 AI 服务）".
    const icon = b.querySelector('[data-icon]');
    return { group, icon: icon ? icon.dataset.icon : 'mail', title: label || (m ? m[1] : b.title), key: m ? m[2] : '',
             run: () => b.click(), names: [label || '', m ? m[1] : b.title || ''] };
  };

  function commands() {
    const list = [], add = (group, icon, title, run, names = [], key = '') => list.push({ group, icon, title, run, names: [title, ...names], key });
    if (hasMail()) {
      const m = S.message;
      // The reader's own buttons as they stand, so a command does what its button does.
      // Buttons folded by 精简 (A4) count too, unless they do not apply to this mail.
      const folded = (b) => b.classList.contains('lean-hide') && !b.closest('.hidden');
      for (const b of $$('#reader-body .reader-toolbar [data-act]')) if ((visible(b) || folded(b)) && b.dataset.act !== 'more') list.push(fromButton('这封邮件', b));
      add('这封邮件', 'clip', '作为附件转发', () => startCompose('forward_attach', m.id));
      add('这封邮件', 'file', '导出为 PDF', () => exportPdf(m), ['pdf']);
      add('这封邮件', 'download', '导出为 .eml', () => exportEml(m), ['eml', '导出原件']);
      add('这封邮件', 'spark', '问问这封邮件', () => { S.chat.attach = m.id; showAsk(); }, ['问这封', '这封说了什么']);
    }
    if (S.multi.size >= 2) {
      for (const b of $$('#reader-multi [data-batch]')) if (visible(b)) list.push(fromButton(`已选 ${S.multi.size} 封`, b));
    }
    add('命令', 'spark', '问邮箱', () => showAsk(), ['ai', '提问', '对话', '聊天', 'AI 助手'], 'Ctrl+J');
    add('命令', 'pen', '写邮件', () => startCompose('new'), ['新邮件', '写信', '新建邮件', '发邮件'], 'Ctrl+N');
    add('命令', 'template', '从模板新建', () => newFromTemplate(), ['模板', '用模板写信']);
    add('命令', 'refresh', '收信', () => $('#btn-refresh').click(), ['刷新', '同步', '收邮件'], 'F5');
    add('命令', 'filter', '高级搜索', () => SF.open(), ['筛选', '条件搜索', '搜索条件']);
    if (!S.local) {
      add('命令', 'mail', '只看未读', () => $('#filter-seg [data-filter="unread"]').click(), ['未读']);
      add('命令', 'star', '只看星标', () => $('#filter-seg [data-filter="flagged"]').click(), ['星标']);
      add('命令', 'inbox', '显示全部邮件', () => $('#filter-seg [data-filter="all"]').click(), ['全部', '取消筛选']);
      add('命令', 'thread', '按会话分组（开 / 关）', () => $('#btn-threads').click(), ['会话', '会话分组', '线索']);
      const f = folderById(S.folderId);
      if (f && typeof MG !== 'undefined' && f.role !== 'sent' && f.role !== 'drafts') add('命令', 'checkall', `「${f.name}」全部标为已读`, () => MG.markAllRead(f.id), ['全部已读', '全部标为已读']);
    }
    if (typeof MG !== 'undefined') add('命令', 'folderplus', '新建文件夹', () => MG.createFolder(null), ['建文件夹']);
    if (typeof toggleSidebar === 'function') add('命令', 'sidebar', '隐藏 / 显示左侧栏', () => toggleSidebar(), ['侧栏', '左侧栏'], 'Ctrl+B');
    add('命令', 'tasks', '新建任务', () => { if (S.local !== 'tasks') $('#nav-tasks').click(); setTimeout(() => $('#tk-new').click(), 50); }, ['任务', '加任务']);
    add('命令', 'lock', '解锁个人证书', () => unlockCertificate(), ['证书', '解锁证书']);
    const theme = (t, label, names) => add('外观', 'settings', label, async () => {
      applyTheme(t); try { S.prefs = await api('setPrefs', { values: { theme: t } }); } catch (err) { fail(err); }
    }, names);
    theme('dark', '深色模式', ['暗色', '夜间模式', '黑色主题']);
    theme('light', '浅色模式', ['亮色', '白色主题']);
    theme('system', '主题跟随系统', ['跟随系统']);
    const density = (d, label, names) => add('外观', 'settings', label, async () => {
      applyTheme(S.prefs.theme, d); try { S.prefs = await api('setPrefs', { values: { density: d } }); } catch (err) { fail(err); }
    }, names);
    density('compact', '紧凑模式', ['紧凑', '密集']);
    density('comfortable', '舒适模式', ['舒适', '宽松']);
    const bars = (t, label, names) => add('外观', 'settings', label, async () => {
      try { S.prefs = await api('setPrefs', { values: { toolbar: t } }); applyTheme(S.prefs.theme, S.prefs.density); } catch (err) { fail(err); }
    }, names);
    bars('lean', '精简工具栏', ['精简', '收起按钮']);
    bars('full', '完整工具栏', ['完整', '显示全部按钮']);
    add('命令', 'settings', '设置', () => openSettings(), ['选项', '偏好']);
    for (const b of $$('#st-nav [data-tab]')) add('设置', 'settings', '设置：' + b.textContent.trim(), () => openSettings(b.dataset.tab), [b.textContent.trim()]);
    for (const f of S.folders) add('前往', ROLE_ICON[f.role] || 'folder', folderTitle(f), () => selectFolder(f.id), [f.name, '打开' + f.name]);
    for (const [key, name, sel] of LOCAL_NAV) { const el = $(sel); if (el) add('前往', el.querySelector('[data-icon]') ? el.querySelector('[data-icon]').dataset.icon : 'folder', name, () => el.click(), ['打开' + name, key]); }
    add('命令', 'close', '退出鸿雁', () => quitApp(), ['退出', '关闭程序'], 'Ctrl+Q');
    add('命令', 'user', '退出登录', () => $('#btn-logout').click(), ['注销', '切换账号']);
    return list;
  }

  // The commands that match, then what is left: the task assistant (in 任务), a search
  // with the conditions read from the words, and the question for 问邮箱.
  function rank(text) {
    const q = text.trim(), all = commands();
    if (!q) return all.filter((c) => c.group !== '前往' && c.group !== '设置' && c.group !== '外观').slice(0, 9);
    const hits = all.map((c, i) => ({ c, s: cmdScore(c.names, q), i })).filter((x) => x.s >= 30)
      .sort((a, b) => b.s - a.s || a.i - b.i).slice(0, 7).map((x) => ({ ...x.c, score: x.s }));
    const tail = [];
    if (S.local === 'tasks') tail.push({ group: '任务助手', icon: 'tasks', title: q, hint: '交给任务助手', run: () => { $('#tk-cmd').value = q; $('#tk-cmd').dispatchEvent(new Event('input')); runCommand(); } });
    const p = cmdParse(q);
    if (p.labels.length || (!cmdIsQuestion(q) && q.length <= 30)) {
      tail.push({ group: '搜索', icon: 'search', title: [...p.labels, p.words ? `「${p.words}」` : ''].filter(Boolean).join(' · '), hint: '搜索邮件',
        run: () => { SF.set(p.filters); $('#search').value = p.words; runSearch(p.words); } });
    }
    const ask = { group: 'AI', icon: 'spark', title: q, hint: '问邮箱', run: () => showAsk(q) };
    // A clear command wins; a question goes to the AI before the search; else the search first.
    const strong = hits.length && hits[0].score >= 60;
    if (cmdIsQuestion(q) && !(strong && hits[0].score === 100)) return [ask, ...tail, ...hits];
    return strong ? [...hits, ...tail, ask] : [...tail, ask, ...hits];
  }

  function render() {
    const out = $('#cp-list');
    out.innerHTML = P.items.map((c, i) => `<div class="cp-item${i === P.index ? ' on' : ''}" data-i="${i}"><i data-icon="${esc(c.icon || 'spark')}"></i>` +
      `<span class="cp-title">${c.hint ? `<b>${esc(c.hint)}：</b>` : ''}${esc(c.title)}</span>` +
      `<span class="cp-meta">${c.key ? `<kbd>${esc(c.key)}</kbd>` : ''}<span>${esc(c.group)}</span></span></div>`).join('') ||
      '<div class="cp-none muted small">没有可用的命令</div>';
    renderIcons(out);
    const on = out.querySelector('.cp-item.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  }
  function update() { P.items = rank($('#cp-input').value); P.index = 0; render(); }
  function openPalette(text = '') {
    if ($('#app').classList.contains('hidden')) return;
    hideMenu();
    $('#palette').classList.remove('hidden');
    const box = $('#cp-input'); box.value = text; update(); box.focus(); box.select();
  }
  function closePalette() { $('#palette').classList.add('hidden'); }
  function runItem(i) {
    const c = P.items[i]; if (!c) return;
    closePalette();
    try { const r = c.run(); if (r && r.catch) r.catch(fail); } catch (err) { fail(err); }
  }
  $('#cp-input').addEventListener('input', update);
  $('#cp-input').addEventListener('keydown', (e) => {
    e.stopPropagation();  // The page's own keys (J/K, Delete, Esc) stay out of the box.
    if (e.isComposing) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (P.items.length) { P.index = (P.index + (e.key === 'ArrowDown' ? 1 : P.items.length - 1)) % P.items.length; render(); }
    } else if (e.key === 'Enter') { e.preventDefault(); runItem(P.index); }
    else if (e.key === 'Escape' || ((e.key === 'k' || e.key === 'K') && e.ctrlKey)) { e.preventDefault(); closePalette(); }
  });
  $('#cp-list').addEventListener('mousemove', (e) => { const el = e.target.closest('[data-i]'); if (el && +el.dataset.i !== P.index) { P.index = +el.dataset.i; render(); } });
  $('#cp-list').addEventListener('click', (e) => { const el = e.target.closest('[data-i]'); if (el) runItem(+el.dataset.i); });
  $('#palette').addEventListener('mousedown', (e) => { if (e.target === $('#palette')) closePalette(); });
  window.CP = { open: openPalette, close: closePalette, rank, commands };
}
