'use strict';
/* 鸿雁 · 邮件表格 (T2): the copy that leaves. Outlook for Windows draws mail with Word: it ignores
   white-space, min-width and rgba and only partly follows width, text-align and padding. So, as
   juice does, each table's CSS is repeated in the old attributes it does follow — align, valign,
   bgcolor, width (pixels), nowrap, cellpadding / cellspacing / border — and the editor's own
   marks (data-hy-*, the scroll frame of a wide table) are removed. */

function tbMailHtml(html) {
  if (!/<table[\s>]|data-hy-/i.test(html)) return html;
  const box = document.createElement('template');
  box.innerHTML = html;
  const root = box.content;
  root.querySelectorAll('div[data-hy-scroll]').forEach((d) => d.replaceWith(...d.childNodes));
  root.querySelectorAll('table').forEach((table) => {
    if (!table.hasAttribute('cellpadding')) table.setAttribute('cellpadding', '0');
    if (!table.hasAttribute('cellspacing')) table.setAttribute('cellspacing', '0');
    if (!table.hasAttribute('border')) table.setAttribute('border', '0');
    const w = parseInt(table.style.width, 10);
    if (w && /px$/.test(table.style.width)) table.setAttribute('width', String(w));
    table.querySelectorAll('td,th').forEach((cell) => {
      if (cell.closest('table') !== table) return;
      const st = cell.style;
      if (st.textAlign && !cell.hasAttribute('align')) cell.setAttribute('align', st.textAlign);
      if (st.verticalAlign && !cell.hasAttribute('valign')) cell.setAttribute('valign', st.verticalAlign);
      const bg = tbHex(st.backgroundColor);
      if (bg && !cell.hasAttribute('bgcolor')) cell.setAttribute('bgcolor', bg);
      if (st.whiteSpace === 'nowrap') cell.setAttribute('nowrap', '');
      // The cell's whole width (its CSS width is the content box): what Word lays out by.
      const cw = parseInt(st.width, 10);
      if (cw && /px$/.test(st.width)) cell.setAttribute('width', String(cw + (parseInt(st.paddingLeft, 10) || 0) + (parseInt(st.paddingRight, 10) || 0) + 1));
      const ch = parseInt(st.height, 10);
      if (ch && /px$/.test(st.height)) cell.setAttribute('height', String(ch + (parseInt(st.paddingTop, 10) || 0) + (parseInt(st.paddingBottom, 10) || 0) + 1));
    });
  });
  root.querySelectorAll('*').forEach((el) => {
    for (const name of Array.from(el.attributes, (a) => a.name)) if (name.startsWith('data-hy-')) el.removeAttribute(name);
  });
  return box.innerHTML;
}
