'use strict';
/* 鸿雁 · 服务器巡检邮件（H1）：`[OT-MON] CHECK …` 的纯文本正文（CHANGED ITEMS / CURRENT STATUS，
   每行「检查项 级别 说明」）在阅读栏里换成一眼能看出问题的状态视图：有问题的排最前，正常的按类收起，
   原文可展开。只认这种格式，认不出就返回 null，照常显示原文。输出给阅读栏的 iframe（不跑脚本），
   所以样式都写在标签上，颜色深浅主题都看得清。No app.js globals. */

const MON_LEVELS = {  // rank: higher = worse.
  OK: { rank: 0, text: '正常', color: '#16a34a' },
  UNKNOWN: { rank: 1, text: '未知', color: '#d97706' },
  WARNING: { rank: 2, text: '警告', color: '#d97706' },
  CRITICAL: { rank: 3, text: '严重', color: '#dc2626' },
};
const MON_ALIAS = { WARN: 'WARNING', CRIT: 'CRITICAL', ERROR: 'CRITICAL', FAIL: 'CRITICAL', FAILED: 'CRITICAL', DOWN: 'CRITICAL' };
// Check name (part before ':') → [group, Chinese name]. Unknown names fall into 其他 under their own name.
const MON_CHECKS = {
  gr_members: ['数据库集群', '集群成员'], gr_queue: ['数据库集群', '复制队列'], read_only: ['数据库集群', '只读设置'], binlog: ['数据库集群', 'binlog 占用'],
  backup: ['备份', '数据库备份'],
  proxysql_writer: ['数据库代理', '写节点'], proxysql_ports: ['数据库代理', '端口'],
  bi_site: ['站点与服务', 'BI 站点'], crm_site: ['站点与服务', 'CRM 站点'], ipam_scan: ['站点与服务', 'IPAM 扫描'],
  local_disk: ['磁盘', '本机磁盘'], remote_disk: ['磁盘', '远程磁盘'],
  job_logs: ['定时任务', '任务日志'],
  monitor_self: ['巡检程序', '自检'],
};
const MON_JOBS = { meb: 'MEB 数据库备份', bi_main: 'BI 主站同步', bi_dr: 'BI 灾备', config: '配置检查', minio_main: 'MinIO 主站同步', minio_dr: 'MinIO 灾备同步' };
const MON_GROUPS = ['数据库集群', '备份', '数据库代理', '站点与服务', '磁盘', '定时任务', '巡检程序', '其他'];
const MON_HINTS = [  // Plain-language hint for messages that are not self-explanatory.
  [/no matching result line/i, '日志里没找到这次任务的结果，可能任务没有运行、还没跑完，或日志格式变了'],
  [/connection failed/i, '连不上'],
  [/not listening/i, '端口没有在监听'],
];
const monEsc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function monLevel(word) {
  const w = String(word || '').toUpperCase(), key = MON_ALIAS[w] || w;
  return MON_LEVELS[key] ? key : null;
}
// "job_logs:minio_dr   UNKNOWN no matching…" (tabs or runs of spaces) → {check, level, message}.
function monRow(line) {
  const m = line.match(/^(\S+)\s+([A-Za-z]+)\b\s*(.*)$/);
  const level = m && monLevel(m[2]);
  return level ? { check: m[1], level, message: m[3].trim() } : null;
}
function monParse(subject, text) {
  if (!/\[OT-MON\]/i.test(subject || '') || !text) return null;
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const at = lines.findIndex((l) => /^\s*CURRENT STATUS\s*$/i.test(l));
  if (at < 0) return null;
  const changes = [], rows = [];
  let section = '';
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim();
    if (/^CHANGED ITEMS$/i.test(l)) { section = 'changes'; continue; }
    if (i === at) { section = 'status'; continue; }
    if (/^Disclaimer:/i.test(l)) break;  // Company footer (English + Arabic) is not part of the report.
    if (!l || /^CHECK\s+LEVEL\s+MESSAGE$/i.test(l)) continue;
    if (section === 'changes') { const r = monRow(l.replace(/^CHANGE:\s*/i, '')); if (r) changes.push(r); }
    else if (section === 'status') { const r = monRow(l); if (r) rows.push(r); }
  }
  if (!rows.length) return null;
  const head = (subject.match(/\bCHECK\s+([A-Za-z]+)/i) || [])[1];
  return { level: monLevel(head) || rows.reduce((a, r) => (MON_LEVELS[r.level].rank > MON_LEVELS[a].rank ? r.level : a), 'OK'), changes, rows };
}

// "job_logs:minio_dr" → {group, name, target}; "remote_disk:root@10.0.0.1" → target 10.0.0.1.
function monName(check) {
  const [base, ...rest] = check.split(':'), target = rest.join(':').replace(/^[^@]*@/, '');
  const [group, name] = MON_CHECKS[base] || ['其他', base];
  if (base === 'job_logs' && target) return { group, name: MON_JOBS[target] || target, target: '' };
  return { group, name, target };
}
function monBadge(level) {
  const L = MON_LEVELS[level];
  return `<span style="display:inline-block;min-width:34px;text-align:center;padding:0 7px;border-radius:9px;font-size:12px;line-height:18px;font-weight:600;color:#fff;background:${L.color}">${L.text}</span>`;
}
// Percentages (disk use) get a small bar; the bar only turns amber/red from 80% / 90%.
function monMessage(msg) {
  return monEsc(msg).replace(/(\S+ )?(\d{1,3})%/g, (all, label, n) => {
    const p = Math.min(100, +n), c = p >= 90 ? '#dc2626' : p >= 80 ? '#d97706' : '#64748b';
    return `${label || ''}<span style="display:inline-block;width:56px;height:6px;border-radius:3px;background:rgba(127,127,127,.25);vertical-align:middle;margin:0 4px 2px">` +
      `<span style="display:block;width:${p}%;height:6px;border-radius:3px;background:${c}"></span></span><b>${p}%</b>`;
  });
}
function monHint(msg) {
  const h = MON_HINTS.find(([re]) => re.test(msg));
  return h ? h[1] : '';
}
function monLine(r, withHint) {
  const n = monName(r.check), hint = withHint && monHint(r.message);
  return `<tr><td style="padding:4px 10px 4px 0;white-space:nowrap;vertical-align:top">${monBadge(r.level)}</td>` +
    `<td style="padding:4px 12px 4px 0;white-space:nowrap;vertical-align:top;min-width:150px"><b>${monEsc(n.name)}</b>${n.target ? ` <span style="opacity:.6">${monEsc(n.target)}</span>` : ''}</td>` +
    `<td style="padding:4px 0;vertical-align:top;font-family:Consolas,Menlo,monospace;font-size:12.5px;opacity:.85">${monMessage(r.message)}` +
    `${hint ? `<div style="font-family:inherit;font-size:13px;opacity:1;margin-top:2px;color:${MON_LEVELS[r.level].color}">${monEsc(hint)}</div>` : ''}</td></tr>`;
}
const MON_TABLE = 'border-collapse:collapse;font-size:13.5px;line-height:1.5';

function monitorReport(subject, text) {
  const rep = monParse(subject, text);
  if (!rep) return null;
  const bad = rep.rows.filter((r) => r.level !== 'OK').sort((a, b) => MON_LEVELS[b.level].rank - MON_LEVELS[a.level].rank);
  const L = MON_LEVELS[rep.level], ok = rep.rows.length - bad.length;
  const counts = Object.keys(MON_LEVELS).filter((k) => k !== 'OK').map((k) => [k, bad.filter((r) => r.level === k).length]).filter(([, n]) => n);
  const title = bad.length ? `${bad.length} 项需要看` : '全部正常';
  const sub = [`共 ${rep.rows.length} 项`, `正常 ${ok}`, ...counts.map(([k, n]) => `${MON_LEVELS[k].text} ${n}`)].join(' · ');
  let html = `<div style="border-radius:10px;padding:14px 16px;margin-bottom:14px;border:1px solid ${L.color};background:${L.color}1a">` +
    `<div style="font-size:20px;font-weight:700;color:${L.color}">${bad.length ? '⚠' : '✓'} ${title}</div><div style="opacity:.75;font-size:13px">${sub}</div></div>`;
  if (rep.changes.length) {
    html += `<div style="font-weight:600;margin:4px 0">和上一封相比有变化</div><table style="${MON_TABLE};margin-bottom:12px">${rep.changes.map((r) => monLine(r, false)).join('')}</table>`;
  }
  if (bad.length) html += `<div style="font-weight:600;margin:4px 0">需要看</div><table style="${MON_TABLE};margin-bottom:12px">${bad.map((r) => monLine(r, true)).join('')}</table>`;
  const groups = MON_GROUPS.map((g) => [g, rep.rows.filter((r) => monName(r.check).group === g)]).filter(([, rs]) => rs.length);
  html += `<div style="font-weight:600;margin:4px 0">全部检查项</div>` + groups.map(([g, rs]) => {
    const worst = rs.reduce((a, r) => (MON_LEVELS[r.level].rank > MON_LEVELS[a].rank ? r.level : a), 'OK');
    const nOk = rs.filter((r) => r.level === 'OK').length;
    return `<details${worst === 'OK' ? '' : ' open'} style="border-top:1px solid rgba(127,127,127,.25);padding:6px 0">` +
      `<summary style="cursor:pointer;list-style-position:inside"><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${MON_LEVELS[worst].color};margin:0 6px 1px 2px"></span>` +
      `<b>${g}</b> <span style="opacity:.6;font-size:13px">${nOk}/${rs.length} 正常</span></summary>` +
      `<table style="${MON_TABLE};margin:4px 0 2px 16px">${rs.map((r) => monLine(r, false)).join('')}</table></details>`;
  }).join('');
  const raw = text.replace(/\r\n?/g, '\n').split(/\n\s*Disclaimer:/i)[0].trim();
  html += `<details style="border-top:1px solid rgba(127,127,127,.25);padding:6px 0;margin-top:6px"><summary style="cursor:pointer;opacity:.7">看原文</summary>` +
    `<pre style="font-family:Consolas,Menlo,monospace;font-size:12px;line-height:1.5;white-space:pre-wrap;margin:6px 0 0">${monEsc(raw)}</pre></details>`;
  return html;
}
