'use strict';
/* 鸿雁 · 邮件表格 (T2): what each row is — title | head | group | data | subtotal | total | note | blank.
   The header starts at the first row about as full as an ordinary row (messytables / frictionless),
   so titles above it are skipped; whether it IS a header is scored, not assumed (text over numbers,
   bold or a fill band, merges, header words), and a weak score means no header. Pure functions. */

const TB_TOTAL = /^(?:合\s*计|总\s*计|小\s*计|共\s*计|累\s*计|总\s*额|总\s*数|汇\s*总|平\s*均|均\s*值|合计金额|本页小计|grand\s*total|sub-?\s*total|total|sum|average|avg\.?)(?![A-Za-z])/i;
const TB_SUBTOTAL = /小\s*计|本页小计|sub-?\s*total/i;
const TB_NOTE = /^(?:注|备注|说明|附注|注意|来源|数据来源|note|notes|remark|remarks|source|\*|※|①)/i;
const TB_HEAD_WORDS = /序号|名称|名字|姓名|问题|描述|日期|时间|金额|数量|单价|总价|费用|备注|说明|状态|类型|部门|项目|内容|单位|负责人|编号|型号|规格|地点|进度|结果|原因|计划|实际|比例|合计|^(?:no\.?|name|date|time|amount|qty|quantity|price|cost|total|status|type|item|description|remark|remarks|owner|dept|department|project|unit|model|result|reason|plan|actual|rate|ratio|id|code)$/i;

function tbRowInfo(plain, cellTypes) {
  return Array.from({ length: plain.nRows }, (_, r) => {
    const cells = tbRowCells(plain, r), filled = cells.filter((x) => cellTypes[x.id] !== 'empty');
    const covered = filled.reduce((n, x) => n + x.cs, 0);
    const bold = filled.length && filled.filter((x) => x.src.b).length / filled.length >= 0.6;
    const fills = new Set(filled.map((x) => x.src.bg || ''));
    return { r, cells, filled, covered, bold, fill: fills.size === 1 ? [...fills][0] : null, merged: cells.some((x) => x.cs > 1), tall: cells.some((x) => x.rs > 1) };
  });
}
const tbFamily = (t) => (TB_NUMERIC.has(t) ? 'num' : t === 'date' || t === 'datetime' || t === 'time' ? 'date' : t === 'id' ? 'id' : t === 'empty' || t === 'missing' ? '' : 'text');

// Is row h a header over the rows below it? -> score (>= 2: yes).
function tbHeadScore(plain, cellTypes, info, h) {
  const row = info[h], below = info.slice(h + 1).filter((x) => x.filled.length).slice(0, 12);
  if (!below.length) return 0;
  let score = 0;
  const fams = row.filled.map((x) => tbFamily(cellTypes[x.id]));
  if (fams.some((f) => f === 'num' || f === 'date')) score -= 2;
  // Text above numbers / dates / codes in the same column.
  let turns = 0, same = 0;
  row.filled.forEach((x) => {
    if (tbFamily(cellTypes[x.id]) !== 'text') return;
    const under = below.flatMap((b) => b.cells.filter((y) => y.c >= x.c && y.c < x.c + x.cs)).map((y) => tbFamily(cellTypes[y.id])).filter(Boolean);
    if (!under.length) return;
    // Short words over long text count too (问题 / 描述 over sentences).
    const long = below.flatMap((b) => b.cells.filter((y) => y.c === x.c && y.cs === 1)).filter((y) => cellTypes[y.id] === 'longtext').length;
    if (under.filter((f) => f !== 'text').length / under.length >= 0.6 || (long >= under.length * 0.6 && tbVisualLength(x.text) <= 12)) turns++; else same++;
  });
  if (turns) score += 2;
  else if (same && !row.bold && !row.fill) score -= 1;
  if (row.bold && !below.slice(0, 3).every((b) => b.bold)) score += 1;
  if (row.fill && !below.slice(0, 3).some((b) => b.fill === row.fill)) score += 1;
  if (row.merged || row.tall) score += 1;
  const words = row.filled.filter((x) => TB_HEAD_WORDS.test(x.text.replace(/[（(][^）)]*[）)]/g, '').trim())).length;
  if (words && words >= row.filled.length / 3) score += 1;
  const texts = row.filled.map((x) => x.text);
  if (new Set(texts).size === texts.length && texts.every((t) => tbVisualLength(t) <= 24) && row.filled.length >= 2) score += 0.5;
  if (row.filled.some((x) => cellTypes[x.id] === 'longtext')) score -= 1;
  return score;
}

// -> {roles: [role per row], conf: [0..1 per row], headStart, headEnd}.
function tbRoles(plain, cellTypes) {
  const n = plain.nRows, roles = Array(n).fill('data'), conf = Array(n).fill(0.6);
  if (!n) return { roles, conf, headStart: 0, headEnd: 0 };
  const info = tbRowInfo(plain, cellTypes);
  info.forEach((x) => { if (!x.filled.length) { roles[x.r] = 'blank'; conf[x.r] = 1; } });
  const wide = Math.max(2, Math.ceil(plain.nCols / 2));
  // Titles: one short cell (often merged across) above the first full row.
  let h = 0;
  while (h < n - 1 && plain.nCols >= 3 && (roles[h] === 'blank' || (info[h].filled.length === 1 && tbFamily(cellTypes[info[h].filled[0].id]) === 'text' && info[h].covered < plain.nCols * 0.9) ||
    (info[h].filled.length === 1 && info[h].filled[0].cs >= plain.nCols * 0.6 && info.slice(h + 1).some((x) => x.filled.length >= 2)))) {
    if (roles[h] !== 'blank') { roles[h] = 'title'; conf[h] = 0.8; }
    h++;
  }
  if (h >= n - 1 && plain.nCols >= 3) { for (let r = 0; r < n; r++) if (roles[r] === 'title') roles[r] = 'data'; h = 0; }
  // The header starts at the first row as full as an ordinary one; it is a header only if it scores.
  while (h < n && (roles[h] === 'blank' || (info[h].covered < Math.min(wide, plain.nCols) && plain.nCols > 2))) h++;
  let headEnd = h;
  if (h < n - 1) {
    const score = tbHeadScore(plain, cellTypes, info, h);
    if (score >= 2) {
      headEnd = h + Math.max(1, ...info[h].cells.map((x) => x.rs));
      // Grouped headers: a row of merges is followed by the labels under them (at most 3 rows).
      while (headEnd < n - 1 && headEnd - h < 3) {
        const next = info[headEnd], spans = info.slice(h, headEnd).some((x) => x.merged);
        const allText = next.filled.length && next.filled.every((x) => tbFamily(cellTypes[x.id]) === 'text');
        if (!(spans && allText && tbHeadScore(plain, cellTypes, info, headEnd) >= 1.5)) break;
        headEnd = Math.min(n - 1, headEnd + Math.max(1, ...next.cells.map((x) => x.rs)));
      }
      for (let r = h; r < headEnd; r++) { roles[r] = 'head'; conf[r] = Math.min(1, 0.55 + score / 10); }
    }
  }
  const bodyStart = headEnd;
  // Notes: trailing rows of one long or note-like cell.
  let end = n - 1;
  while (end > bodyStart && (roles[end] === 'blank' || (info[end].filled.length === 1 &&
    (TB_NOTE.test(info[end].filled[0].text) || cellTypes[info[end].filled[0].id] === 'longtext' || info[end].filled[0].cs >= plain.nCols * 0.6)))) {
    if (roles[end] !== 'blank') { roles[end] = 'note'; conf[end] = TB_NOTE.test(info[end].filled[0].text) ? 0.9 : 0.7; }
    end--;
  }
  const firstCol = plain.cells.filter((x) => x.c === 0 && x.r >= bodyStart && x.r <= end && roles[x.r] === 'data');
  for (let r = bodyStart; r <= end; r++) {
    if (roles[r] !== 'data') continue;
    const row = info[r], first = row.filled[0];
    if (first && TB_TOTAL.test(first.text)) { roles[r] = TB_SUBTOTAL.test(first.text) ? 'subtotal' : 'total'; conf[r] = 0.95; continue; }
    // A group: one text cell at the start (or across the row) between data rows.
    if (plain.nCols >= 3 && row.filled.length === 1 && tbFamily(cellTypes[first.id]) === 'text' && (first.c === 0 || first.cs >= plain.nCols * 0.6) &&
      info.slice(r + 1, end + 1).some((x) => x.filled.length >= 2)) { roles[r] = 'group'; conf[r] = row.bold || row.fill ? 0.9 : 0.7; }
  }
  // An unlabelled total: the last body row, first column empty where it is otherwise filled, numbers present.
  const dataRows = roles.map((x, r) => r).filter((r) => r >= bodyStart && r <= end && roles[r] === 'data');
  const last = dataRows[dataRows.length - 1];
  if (last !== undefined && dataRows.length >= 3 && last === end) {
    const lead = plain.cells.find((x) => x.r === last && x.c === 0);
    const leadEmpty = !lead || ['empty', 'missing'].includes(cellTypes[lead.id]);
    const filledElsewhere = firstCol.filter((x) => x.r !== last && cellTypes[x.id] !== 'empty').length >= (dataRows.length - 1) * 0.8;
    const numbers = info[last].filled.filter((x) => TB_NUMERIC.has(cellTypes[x.id])).length;
    if ((leadEmpty && filledElsewhere && numbers) || (info[last].bold && numbers && !info[dataRows[0]].bold)) { roles[last] = 'total'; conf[last] = 0.7; }
  }
  return { roles, conf, headStart: h, headEnd };
}
