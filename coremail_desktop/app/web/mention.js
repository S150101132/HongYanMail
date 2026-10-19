'use strict';
/* 鸿雁 · v0.7 @mentions in the body. Typing @ in the composer offers the same
   suggestions as the recipient fields; choosing one inserts a highlighted "@姓名"
   (ordinary HTML text, readable in any client) and adds the person to 主送 when
   they are not a recipient yet. Removing the last @ of someone asks whether to
   take them off the recipients too. Uses app.js globals. */

const MENTION_STYLE = 'color:#1d4ed8;background:#e8effd;border-radius:3px;padding:0 2px;font-weight:600';
// "@" at the start or after a space/punctuation (so "name@company.com" is not a mention).
const MENTION_AT = /(?:^|[\s (（,，、:：;；])@([^\s @<>,;，；:：]{0,24})$/;
const MN = { node: null, start: 0, query: '', timer: null, token: 0, seen: new Map(), asking: false };
const editorEl = $('#cmp-editor');

function mentionsIn() {
  const found = new Map();
  for (const s of $$('.hy-mention[data-mention]', editorEl)) {
    const key = s.dataset.mention.toLowerCase();
    found.set(key, { email: s.dataset.mention, name: s.textContent.replace(/^@/, '') });
  }
  return found;
}
// Called when the composer opens (new mail, a draft, an undone send).
function mentionsReset() {
  hideSuggestIfMention();
  $$('.hy-mention', editorEl).forEach((s) => (s.contentEditable = 'false'));
  MN.seen = mentionsIn();
}
function hideSuggestIfMention() { if (sug.input === editorEl) hideSuggest(); MN.node = null; }

function mentionQuery() {
  const s = getSelection();
  if (!s.rangeCount || !s.isCollapsed) return null;
  const node = s.anchorNode;
  if (!node || node.nodeType !== 3 || !editorEl.contains(node) || node.parentElement.closest('.hy-mention')) return null;
  const before = node.data.slice(0, s.anchorOffset);
  const m = MENTION_AT.exec(before);
  return m ? { node, start: s.anchorOffset - m[1].length - 1, query: m[1] } : null;
}
function caretRect() {
  const s = getSelection(); const r = s.getRangeAt(0).cloneRange();
  const rect = r.getClientRects()[0] || r.getBoundingClientRect();
  return { left: rect.left, bottom: rect.bottom || rect.top + 18 };
}
function currentRecipients() {
  return ['to', 'cc'].flatMap((f) => $$(`.chips[data-field="${f}"] .chip`)).map((c) => ({ name: c.dataset.name, email: c.dataset.email, source: 'recipient' }));
}
editorEl.addEventListener('input', () => {
  const q = mentionQuery();
  clearTimeout(MN.timer);
  if (!q) { hideSuggestIfMention(); checkRemovedMentions(); return; }
  Object.assign(MN, q);
  const rect = caretRect();
  if (!q.query) {
    // Just "@": the people already on the mail, until a name is typed.
    const items = currentRecipients().filter((p) => EMAIL_RE.test(p.email)).slice(0, 8);
    if (items.length) showSuggest(null, editorEl, items, { rect, pick: insertMention }); else hideSuggestIfMention();
    return;
  }
  MN.timer = setTimeout(async () => {
    const token = ++MN.token;
    const typed = q.query.toLowerCase();
    const mine = currentRecipients().filter((p) => (p.name || '').toLowerCase().includes(typed) || p.email.toLowerCase().includes(typed));
    const found = await api('suggest', { query: q.query }).catch(() => []);
    if (token !== MN.token || !MN.node || MN.query !== q.query) return;
    const have = new Set(mine.map((p) => p.email.toLowerCase()));
    const items = [...mine, ...found.filter((p) => !have.has(p.email.toLowerCase()))].slice(0, 8);
    if (items.length) showSuggest(null, editorEl, items, { rect, pick: insertMention }); else hideSuggestIfMention();
  }, 160);
});
// Arrow keys / Enter / Tab / Esc steer the list while it is open over the text.
editorEl.addEventListener('keydown', (e) => {
  if (sug.input !== editorEl || $('#suggest').classList.contains('hidden')) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault(); sug.index = (sug.index + (e.key === 'ArrowDown' ? 1 : -1) + sug.items.length) % sug.items.length; drawSuggest();
  } else if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault(); e.stopPropagation(); pickSuggest(sug.index);
  } else if (e.key === 'Escape') {
    e.preventDefault(); e.stopPropagation(); hideSuggestIfMention();
  }
});
editorEl.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== editorEl) hideSuggestIfMention(); }, 150));

function insertMention(p) {
  const node = MN.node, start = MN.start, end = start + 1 + MN.query.length;
  MN.node = null;
  if (!node || !node.isConnected || node.data.slice(start, end) !== '@' + MN.query) return;
  const name = (p.name || p.email.split('@')[0]).replace(/^@/, '');
  editorEl.focus();
  // Placed by hand: insertHTML moves a non-editable span out of its paragraph.
  const r = document.createRange(); r.setStart(node, start); r.setEnd(node, end);
  r.deleteContents();
  const span = document.createElement('span');
  span.className = 'hy-mention'; span.dataset.mention = p.email; span.contentEditable = 'false';
  span.setAttribute('style', MENTION_STYLE); span.textContent = '@' + name;
  const space = document.createTextNode(' ');
  r.insertNode(space); r.insertNode(span);
  const s = getSelection(); s.removeAllRanges(); s.collapse(space, 1);
  MN.seen = mentionsIn();
  const lower = p.email.toLowerCase();
  if (!allRecipients().some((a) => a.toLowerCase() === lower)) {
    const box = $('.chips[data-field="to"]');
    addChip(box, $('input', box), { name: p.name || '', email: p.email });
    toast(`已把 ${name} 加入主送`);
  }
  C.dirty = true;
}

// The last "@someone" deleted: offer to take them off 主送/抄送 as well.
async function checkRemovedMentions() {
  if (MN.asking) return;
  const now = mentionsIn();
  const gone = [...MN.seen.entries()].filter(([key]) => !now.has(key)).map(([, v]) => v);
  MN.seen = now;
  const chipsOf = (email) => ['to', 'cc'].flatMap((f) => $$(`.chips[data-field="${f}"] .chip`)).filter((c) => c.dataset.email.toLowerCase() === email.toLowerCase());
  for (const person of gone) {
    if (!chipsOf(person.email).length) continue;
    const s = getSelection(); const keep = s.rangeCount ? s.getRangeAt(0).cloneRange() : null;
    MN.asking = true;
    const yes = await confirmDialog('同时移除收件人？', `正文中已没有“@${person.name}”。要把 ${person.name}（${person.email}）也从收件人中移除吗？`, '移除收件人');
    MN.asking = false;
    if (yes) { chipsOf(person.email).forEach((c) => c.remove()); C.dirty = true; renderEncrypt(); toast(`已从收件人中移除 ${person.name}`); }
    editorEl.focus();
    if (keep && keep.startContainer.isConnected) { s.removeAllRanges(); s.addRange(keep); }
  }
}

// What leaves the composer: the mention stays highlighted text; the editing-only
// attributes (and the address it was picked from) are not sent.
function outgoingHtml(html) {
  return html.replace(/<span\b[^>]*\bhy-mention\b[^>]*>/g, (tag) => tag.replace(/\scontenteditable="false"/, '').replace(/\sdata-mention="[^"]*"/, ''));
}
