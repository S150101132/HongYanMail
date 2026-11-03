'use strict';
/* 鸿雁 · 邮件表格 (T2): what kind of value each cell and column holds. What the source told us
   comes first (Excel's x:num and number format); text is parsed only without it. A column takes
   a type when 90% of its non-empty cells agree (frictionless' field confidence); N/A, -, 无 and
   blanks do not vote. Identifier evidence (leading zeros, fixed long digits, phones, an ID header)
   beats "parses as a number". Pure functions. */

const TB_NUM = '[-+−]?(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?';
const TB_CUR = '[$¥￥€£₹]|US\\$|HK\\$|S\\$|SAR|USD|RMB|CNY|EUR|GBP|AED|HKD|JPY|QAR|KWD|BHD|OMR|EGP|INR|SGD|CHF|元|美元|欧元|英镑|里亚尔|万元|亿元';
const TB_UNIT = '台|个|件|套|人|名|位|天|日|周|月|年|小时|分钟|秒|次|份|张|页|箱|批|项|条|辆|块|只|吨|公斤|千克|克|米|公里|千米|厘米|毫米|平方米|㎡|m²|立方米|升|毫升|' +
  'kg|g|t|km|m|cm|mm|l|ml|h|hr|hrs|min|mins|sec|pcs|pc|sets?|units?|days?|hours?|months?|years?|GB|MB|TB|KB|Gbps|Mbps|W|kW|kWh|V|°C|℃|人天|人月|人次';
const TB_MONTH = 'jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december';
const TB_RE = {
  missing: /^(n\/?a|na|n\.a\.|null|none|nil|tbd|tba|无|暂无|空|待定|不适用|[-–—\/\\.…?？*]+)$/i,
  number: new RegExp(`^\\(?${TB_NUM}\\)?(?:\\s*(?:万|亿|千|百万|k|K))?$`),
  percent: new RegExp(`^\\(?${TB_NUM}\\s*[%‰％]\\)?$`),
  money: new RegExp(`^\\(?(?:(?:${TB_CUR})\\s*${TB_NUM}(?:\\s*(?:万|亿|k|K|m|M))?|${TB_NUM}(?:\\s*(?:万|亿|k|K))?\\s*(?:${TB_CUR}))\\)?$`, 'i'),
  quantity: new RegExp(`^${TB_NUM}\\s*(?:${TB_UNIT})$`, 'i'),
  date: new RegExp('^(?:(?:19|20)\\d{2}(?:[-/年]\\d{1,2}(?:[-/月]\\d{1,2}日?)?|\\.\\d{1,2}\\.\\d{1,2})|\\d{1,2}[-/.]\\d{1,2}[-/.]\\d{2,4}|\\d{1,2}月\\d{1,2}日|' +
    `\\d{1,2}[ -](?:${TB_MONTH})[a-z]*\\.?[ -,]*\\d{2,4}|(?:${TB_MONTH})[a-z]*\\.? \\d{1,2},? \\d{4}|(?:${TB_MONTH})[a-z]*[ -]\\d{2,4}|Q[1-4][ -]?\\d{4}|\\d{4}[ -]?Q[1-4])$`, 'i'),
  time: /^\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?$/i,
  id: /^(?:0\d+|\d{9,}|\+?\(?\d{1,4}\)?[\s-]\d{2,5}(?:[\s-]\d{2,5}){1,3}|\d+(?:-\d+){2,}|(?=[^\s]*\d)(?=[^\s]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9._:/#-]{1,31})$/,
};
const TB_ID_HEAD = /编号|编码|单号|号码|代码|工号|电话|手机|座机|传真|邮编|卡号|账号|帐号|订单号|合同号|批号|序列号|\b(?:id|code|sn|s\/n|serial|tel|phone|mobile|fax|account|po|ref|no\.?)\b/i;
const TB_INDEX_HEAD = /^(?:序号?|行号|项次|no\.?|#|item|s\/n|sn|sr\.?\s*no\.?)$/i;
const TB_MONEY_HEAD = /金额|费用|价|元）|元\)|（元|\(元|成本|预算|收入|支出|报销|amount|cost|price|budget|fee|revenue|expense|usd|sar|rmb|cny|eur/i;
const TB_NUMERIC = new Set(['int', 'decimal', 'money', 'percent', 'quantity']);
const TB_TEXTISH = new Set(['text', 'longtext']);

// Number-format class from Excel's mso-number-format (see source.js).
function tbFormatKind(fmt) {
  const f = String(fmt || '').replace(/\\/g, '').toLowerCase();
  if (!f || f === 'general') return '';
  if (f === '@' || f === 'text' || /^"?0{2,}"?$/.test(f)) return 'text';
  if (/%|percent/.test(f)) return 'percent';
  if (/[$¥￥€£]|\[\$|currency|accounting/.test(f)) return 'money';
  if (/^(fixed|standard|number)$/.test(f)) return 'number';
  if (/^(short|medium|long|general) date$/.test(f)) return 'date';  // Excel's named formats.
  if (/^(short|medium|long) time$/.test(f)) return 'time';
  if (/(^|[^a-z])(y{2,4}|m{1,4}|d{1,4})([^a-z]|$)|年|月|日|short date|long date/.test(f) && !/^[#0,.]+$/.test(f)) return /h{1,2}|:mm|ss/.test(f) && /d|y/.test(f) ? 'datetime' : /h{1,2}:|:mm/.test(f) && !/d|y/.test(f) ? 'time' : 'date';
  if (/^[#0,.\s_()\-"]+$/.test(f)) return 'number';
  return '';
}

// One cell: empty | missing | int | decimal | money | percent | quantity | date | time | datetime | id | text | longtext.
function tbCellType(text, hint = {}) {
  const t = tbCleanText(text);
  if (!t) return 'empty';
  if (TB_RE.missing.test(t)) return 'missing';
  const kind = tbFormatKind(hint.fmt);
  const flat = t.replace(/\s+/g, ' ');
  if (kind === 'text') return /^[\d\s+()-]+$/.test(flat) ? 'id' : flat.length > 40 || t.includes('\n') ? 'longtext' : 'text';
  if (TB_RE.percent.test(flat) || (kind === 'percent' && hint.num)) return 'percent';
  if (TB_RE.money.test(flat) || (kind === 'money' && hint.num)) return 'money';
  if (/\d/.test(flat) && /\s\d{1,2}:\d{2}/.test(flat) && TB_RE.date.test(flat.split(' ')[0]) && TB_RE.time.test(flat.split(' ').slice(1).join(' '))) return 'datetime';
  if (TB_RE.date.test(flat) || ((kind === 'date' || kind === 'datetime') && hint.num)) return kind === 'datetime' ? 'datetime' : 'date';
  if (TB_RE.time.test(flat) || (kind === 'time' && hint.num)) return 'time';
  // A code, a phone, a leading zero or a long digit run is an identifier — unless Excel formats it as a number.
  if (TB_RE.id.test(flat) && !(hint.num && kind === 'number') && (!TB_RE.number.test(flat) || /^0\d/.test(flat) || /^\d{9,}$/.test(flat))) return 'id';
  if (TB_RE.number.test(flat)) return /\.\d/.test(flat) ? 'decimal' : 'int';
  if (TB_RE.quantity.test(flat)) return 'quantity';
  if (hint.num) return 'decimal';
  return tbVisualLength(t) > 40 || t.includes('\n') ? 'longtext' : 'text';
}
function tbCellTypes(plain) { return plain.cells.map((x) => tbCellType(x.text, x)); }

// A column's type from its body cells (roles: per row, from structure.js; omitted: every row but the first).
// -> [{type, conf, head, maxLen, avgLen}]; type: number | money | percent | quantity | date | time |
// datetime | index | id | status | text | longtext | mixed | empty.
function tbColumnTypes(plain, cellTypes, roles) {
  const body = (r) => (roles ? roles[r] === 'data' : r > 0);
  const anyData = roles ? roles.includes('data') : plain.nRows > 1;
  const cols = [];
  for (let c = 0; c < plain.nCols; c++) {
    const cells = tbColCells(plain, c).filter((x) => (anyData ? body(x.r) : !roles || !['head', 'title', 'note'].includes(roles[x.r])));
    const head = plain.cells.filter((x) => roles && roles[x.r] === 'head' && x.c <= c && c < x.c + x.cs).map((x) => x.text).join(' ');
    const types = cells.map((x) => cellTypes[x.id]).filter((t) => t !== 'empty' && t !== 'missing');
    const texts = cells.filter((x) => !['empty', 'missing'].includes(cellTypes[x.id])).map((x) => x.text);
    const n = types.length, count = (pred) => types.filter(pred).length;
    const lengths = texts.map(tbVisualLength), maxLen = Math.max(0, ...lengths), avgLen = n ? lengths.reduce((a, b) => a + b, 0) / n : 0;
    const col = { type: 'empty', conf: 1, head, maxLen, avgLen };
    cols.push(col);
    if (!n) continue;
    const share = { num: count((t) => TB_NUMERIC.has(t)), date: count((t) => t === 'date' || t === 'datetime'), time: count((t) => t === 'time'), id: count((t) => t === 'id'), text: count((t) => TB_TEXTISH.has(t)) };
    const agree = (k) => share[k] / n >= 0.9 || (n <= 10 && n - share[k] <= 1 && share[k] >= 3);
    const ints = count((t) => t === 'int');
    // Identifiers first: their evidence beats "it parses as a number".
    if (TB_INDEX_HEAD.test(head.trim()) && ints + share.id >= n * 0.9) { Object.assign(col, { type: 'index', conf: 0.95 }); continue; }
    if ((share.id >= Math.max(1, n * 0.1) || TB_ID_HEAD.test(head)) && (share.id + ints) / n >= 0.9) { Object.assign(col, { type: 'id', conf: 0.9 }); continue; }
    if (!roles && ints === n && n >= 3 && cells.every((x, i) => /^\d+$/.test(x.text) && +x.text === i + 1)) { Object.assign(col, { type: 'index', conf: 0.8 }); continue; }
    if (roles && ints === n && n >= 3 && texts.every((t, i) => +t === +texts[0] + i) && maxLen <= 4) { Object.assign(col, { type: 'index', conf: 0.85 }); continue; }
    if (agree('num')) {
      const sub = count((t) => t === 'percent') === share.num ? 'percent' : count((t) => t === 'money') >= share.num / 2 ? 'money'
        : count((t) => t === 'quantity') >= share.num / 2 ? 'quantity' : TB_MONEY_HEAD.test(head) ? 'money' : 'number';
      Object.assign(col, { type: sub, conf: share.num / n });
    } else if (agree('date')) Object.assign(col, { type: count((t) => t === 'datetime') > share.date / 2 ? 'datetime' : 'date', conf: share.date / n });
    else if (agree('time')) Object.assign(col, { type: 'time', conf: share.time / n });
    else if (agree('id')) Object.assign(col, { type: 'id', conf: share.id / n });
    else if (agree('text')) {
      const distinct = new Set(texts).size;
      if (n >= 3 && maxLen <= 12 && avgLen <= 8 && distinct < n && distinct <= Math.max(3, Math.ceil(n / 2))) Object.assign(col, { type: 'status', conf: 0.8 });
      else Object.assign(col, { type: count((t) => t === 'longtext') || avgLen > 24 ? 'longtext' : 'text', conf: share.text / n });
    } else Object.assign(col, { type: 'mixed', conf: 0.5 });
  }
  return cols;
}
