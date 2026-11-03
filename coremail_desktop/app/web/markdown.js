'use strict';
/* 鸿雁 · 粘贴 Markdown：复制来的 Markdown 文本（AI 回答、README、笔记）粘进写信正文时排成格式。
   Styles are inline so the recipient's mail client shows the same thing; lines are a mid
   grey in hex (Outlook ignores rgba) that reads on light and dark backgrounds. No app.js globals. */

const MD_STYLE = {
  h: ['font-size:20px', 'font-size:18px', 'font-size:16px', 'font-size:15px', 'font-size:14px', 'font-size:14px'],
  code: 'font-family:Consolas,Menlo,monospace;font-size:90%;background:rgba(127,127,127,.15);padding:1px 4px;border-radius:3px',
  pre: 'font-family:Consolas,Menlo,monospace;font-size:13px;line-height:1.5;background:rgba(127,127,127,.1);border:1px solid #b0b7c3;border-radius:4px;padding:8px 12px;margin:8px 0;white-space:pre-wrap;word-break:break-word',
  quote: 'margin:8px 0 8px 2px;padding:0 0 0 10px;border-left:3px solid #b0b7c3;opacity:.85',
  table: 'border-collapse:collapse;margin:8px 0',
  cell: 'border:1px solid #b0b7c3;padding:4px 10px',
  hr: 'border:none;border-top:1px solid #b0b7c3;margin:12px 0',
};
const mdEsc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const MD_FENCE = /^\s{0,3}(```|~~~)/;
const MD_HEAD = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const MD_HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const MD_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const MD_QUOTE = /^\s{0,3}>\s?/;
const MD_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const mdIndent = (line) => line.match(/^\s*/)[0].length;
const mdTable = (lines, i) => lines[i].includes('|') && i + 1 < lines.length && lines[i + 1].includes('|') && MD_SEP.test(lines[i + 1]);

// Plain text that is clearly Markdown: a heading, code fence, table, quote, bold, link or code.
// A bare numbered list alone is not enough — that is how people write 要点 for the AI to turn
// into a letter, and it should stay as typed.
function mdLooksLike(text) {
  if (!text || text.length > 200000) return false;
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  return lines.some((l, i) => MD_HEAD.test(l) || MD_FENCE.test(l) || MD_QUOTE.test(l) || mdTable(lines, i)) ||
    /\*\*[^*\n]+\*\*|__[^_\n]+__|\[[^\]\n]+\]\((https?:|mailto:)[^)\s]+\)|`[^`\n]+`/.test(text);
}

function mdInline(text) {
  const keep = [];
  const hold = (html) => `\u0000${keep.push(html) - 1}\u0000`;
  const safe = (url) => /^(https?:|mailto:)/i.test(url) ? url : null;
  let s = mdEsc(text);
  s = s.replace(/`([^`]+)`/g, (_, c) => hold(`<code style="${MD_STYLE.code}">${c}</code>`));
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, alt, url) => safe(url) ? hold(`<a href="${url}">${alt || url}</a>`) : m);
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^)]*&quot;)?\)/g, (m, t, url) => safe(url) ? hold(`<a href="${url}">${mdEmph(t)}</a>`) : m);
  s = s.replace(/&lt;((?:https?|mailto):[^\s&]+)&gt;/g, (_, url) => hold(`<a href="${url}">${url}</a>`));
  s = s.replace(/(^|[\s(（：:])(https?:\/\/[^\s<)）\u0000]+[^\s<)）\u0000.,;:!?，。；：！？])/g, (_, pre, url) => pre + hold(`<a href="${url}">${url}</a>`));
  s = mdEmph(s);
  while (/\u0000\d+\u0000/.test(s)) s = s.replace(/\u0000(\d+)\u0000/g, (_, n) => keep[n]);
  return s;
}
function mdEmph(s) {
  return s.replace(/\*\*(?=\S)([^\n]*?\S)\*\*/g, '<b>$1</b>').replace(/(^|[^\w])__(?=\S)([^\n]*?\S)__(?!\w)/g, '$1<b>$2</b>')
    .replace(/~~(?=\S)([^\n]*?\S)~~/g, '<s>$1</s>')
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, '$1<i>$2</i>').replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, '$1<i>$2</i>');
}

function mdCells(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function mdList(lines, i) {
  const base = mdIndent(lines[i]), ordered = /\d/.test(lines[i].match(MD_ITEM)[2]);
  const items = [];
  while (i < lines.length) {
    const m = lines[i].match(MD_ITEM);
    if (!m || m[1].length !== base || /\d/.test(m[2]) !== ordered) break;
    const item = { text: [m[3]], kids: [] };
    i++;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) {
        let j = i; while (j < lines.length && !lines[j].trim()) j++;
        if (j < lines.length && mdIndent(lines[j]) > base) { item.kids.push(''); i++; continue; }
        break;
      }
      if (mdIndent(line) > base) { item.kids.push(line); i++; continue; }
      if (MD_ITEM.test(line) || MD_HEAD.test(line) || MD_FENCE.test(line) || MD_QUOTE.test(line) || MD_HR.test(line)) break;
      if (item.kids.length) break;
      item.text.push(line.trim()); i++;  // A lazy continuation line.
    }
    items.push(item);
  }
  const html = items.map((it) => {
    let text = it.text.map(mdInline).join('<br>');
    text = text.replace(/^\[ \]\s+/, '☐ ').replace(/^\[[xX]\]\s+/, '☑ ');
    const kids = it.kids.filter((k, n, a) => k || (n && n < a.length - 1));
    const pad = Math.min(...kids.filter(Boolean).map(mdIndent));
    return `<li>${text}${kids.length ? mdBlocks(kids.map((k) => k.slice(pad))) : ''}</li>`;
  }).join('');
  return { html, ordered, i };
}

function mdBlocks(lines) {
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = line.match(MD_FENCE))) {
      const body = [];
      for (i++; i < lines.length && !lines[i].trimStart().startsWith(m[1]); i++) body.push(lines[i]);
      i++;
      out.push(`<pre style="${MD_STYLE.pre}"><code>${mdEsc(body.join('\n'))}</code></pre>`);
    } else if ((m = line.match(MD_HEAD))) {
      const n = m[1].length;
      out.push(`<h${n} style="${MD_STYLE.h[n - 1]};margin:14px 0 6px;font-weight:600">${mdInline(m[2])}</h${n}>`);
      i++;
    } else if (MD_HR.test(line)) {
      out.push(`<hr style="${MD_STYLE.hr}">`); i++;
    } else if (mdTable(lines, i)) {
      const head = mdCells(line), align = mdCells(lines[i + 1]).map((c) => c.endsWith(':') ? (c.startsWith(':') ? 'center' : 'right') : 'left');
      const cell = (tag, c, n) => `<${tag} style="${MD_STYLE.cell};text-align:${align[n] || 'left'}${tag === 'th' ? ';font-weight:600;background:#eef1f5;color:#1f2328' : ''}">${mdInline(c)}</${tag}>`;
      const rows = [];
      for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) rows.push(mdCells(lines[i]));
      out.push(`<table style="${MD_STYLE.table}"><thead><tr>${head.map((c, n) => cell('th', c, n)).join('')}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${head.map((_, n) => cell('td', r[n] || '', n)).join('')}</tr>`).join('')}</tbody></table>`);
    } else if (MD_QUOTE.test(line)) {
      const body = [];
      for (; i < lines.length && MD_QUOTE.test(lines[i]); i++) body.push(lines[i].replace(MD_QUOTE, ''));
      out.push(`<blockquote style="${MD_STYLE.quote}">${mdBlocks(body)}</blockquote>`);
    } else if ((m = line.match(MD_ITEM))) {
      const first = parseInt(m[2], 10);
      const list = mdList(lines, i);
      i = list.i;
      out.push(list.ordered ? `<ol${first !== 1 ? ` start="${first}"` : ''} style="margin:6px 0;padding-left:28px">${list.html}</ol>`
        : `<ul style="margin:6px 0;padding-left:28px">${list.html}</ul>`);
    } else {
      const body = [];
      for (; i < lines.length && lines[i].trim(); i++) {
        if (body.length && (MD_HEAD.test(lines[i]) || MD_FENCE.test(lines[i]) || MD_QUOTE.test(lines[i]) || MD_ITEM.test(lines[i]) || MD_HR.test(lines[i]) || mdTable(lines, i))) break;
        body.push(lines[i].trim());
      }
      out.push(`<p style="margin:0 0 8px">${body.map(mdInline).join('<br>')}</p>`);
    }
  }
  return out.join('');
}

function mdToHtml(text) {
  return mdBlocks(text.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n'));
}
