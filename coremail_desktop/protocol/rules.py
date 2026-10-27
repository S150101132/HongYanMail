"""Server shapes of mail rules, tags and the holiday auto-reply, and their client views.

Verified read-only on the deployment 2026-10-18 (analysis/rules-tags-cal-shape-2026-10-18.json,
rules-tags-shape2-2026-10-18.json):
- user:getMailRules var: [{id:int, name, continue:bool, operator:'or' (absent: all must
  match), condictions:[{field, operator, operand, ignoreCase, listOfValues, forAddress,
  extractAddress}], actions:[{type:'move', target:<folder id int>}]}]. 'condictions' is the
  server's own spelling. A sender condition is operator '=' with a comma-separated address
  list (listOfValues); a subject condition is 'contains'.
- auto-reply attributes (user:getAttrs): autorepmode 0/1, autoreptext, autorep_welcometip 0/1,
  autorep_condition {begintime, endtime ('YYYY-MM-DD HH:MM:SS', account zone), instation 0/1}.
  instation is webmail's "只对本单位用户发送回复". Titles are off here (enableAutoRepTitle false).
- mbox:listTags var: [[id, {color, name?}]]; the 9 default tags are ids '11'..'19' with only
  a colour index, and webmail names an unnamed tag after its colour (public-login.js 62565).

Rule conditions and actions are built as webmail's own rule editor builds them (analysis/web-login
S.03931.js, setting.filter.letter); add/edit/disable/delete/reorder were verified with a temporary
rule on the deployment 2026-10-18 (analysis/rules-tags-real-verification-2026-10-18.json).
"""
import re
import time

from coremail_desktop.errors import AppError

# Webmail's tag palette (public-login.js module 62565): a colour is stored as an index into
# BASE, or as a hex colour. ORDER is the colour picker's order, NAMES its labels.
BASE = ['#cc0000', '#2ea962', '#ee791a', '#30aed5', '#e36dad', '#24b3b3', '#f2d230', '#a847d7', '#888888']
ORDER = [0, 2, '#ffa83d', 6, 1, 5, '#71ae1a', 3, 7, 4, '#a8babf', '#00536a', 8, '#5d5d5d', '#000000', '#840e21', '#cf500d',
         '#a45c08', '#ac8801', '#00501e', '#005d66', '#077007', '#00204f', '#3c176d', '#5e005e']
NAMES = '红色,橙色,桃红,黄色,绿色,青色,橄榄色,蓝色,紫色,粉色,浅钢灰色,钢灰色,灰色,深灰色,黑色,深红色,深橙色,褐色,深黄色,深绿色,深青色,深橄榄色,深蓝色,深紫色,深洋红色'.split(',')
TAG_LIMIT = 100  # limitTagCount on the deployment.


def tag_color(value):
    """Stored colour -> hex."""
    text = str(value if value is not None else '').strip()
    if text.isdigit() and int(text) < len(BASE):
        return BASE[int(text)]
    if not text:
        return BASE[0]
    return text.lower() if re.fullmatch(r'#[0-9a-fA-F]{6}', text) else BASE[0]


def stored_color(hex_color):
    """Hex from the picker -> what webmail stores (a palette index where there is one)."""
    value = str(hex_color or '').lower()
    if value in BASE:
        return str(BASE.index(value))
    if not re.fullmatch(r'#[0-9a-f]{6}', value):
        raise AppError('INVALID_TAG')
    return value


def tag_view(tag_id, attrs):
    attrs = attrs if isinstance(attrs, dict) else {}
    color = tag_color(attrs.get('color'))
    key = BASE.index(color) if color in BASE else color
    name = str(attrs.get('name') or '').strip()
    if not name:
        name = (NAMES[ORDER.index(key)] if key in ORDER else '') + '标签'
    return {'id': str(tag_id), 'name': name, 'color': color, 'named': bool(attrs.get('name'))}


def tags_view(rows):
    if not isinstance(rows, list):
        raise AppError('INVALID_RESPONSE')
    result = []
    for row in rows:
        if isinstance(row, list) and row and isinstance(row[0], (str, int)) and str(row[0]):
            result.append(tag_view(row[0], row[1] if len(row) > 1 else {}))
    # Webmail sorts tags by id, newest (largest) first.
    return sorted(result, key=lambda t: (-int(re.sub(r'\D', '', t['id']) or 0) if t['id'][:1] != 'x' else -int(t['id'][1:], 16)))


def new_tag_id(existing, now=None):
    """Webmail's id for a new tag: 'x' + hex milliseconds, unique among the existing ids."""
    base = int((now if now is not None else time.time()) * 1000)
    for step in range(1000):
        candidate = 'x' + format(base + step, 'x')
        if candidate not in existing:
            return candidate
    raise AppError('INVALID_TAG')


def clean_tag_name(name):
    """Webmail's trimName: 1..24 width (CJK counts 2), no %'"\\/:*?<>|=,^."""
    name = str(name or '').strip()
    if not name or sum(2 if ord(c) > 127 else 1 for c in name) > 24 or any(c in name for c in '%\'"\\/:*?<>|=,^'):
        raise AppError('INVALID_TAG')
    return name


# Rules --------------------------------------------------------------------------
# The client edits a subset: conditions on sender, recipients (to / cc / either), subject,
# attachment and size; actions move, mark read, flag, forward and tag. A rule that holds
# anything else is listed with editable False: switch on/off, reorder or delete only.
# Fields and operators are webmail's own (S.03931.js: Qt address fields, ii text operators).
ADDRESS_FIELDS = ('from', 'to', 'cc', 'recipients')  # recipients = 收件人或抄送.
TEXT_FIELDS = ADDRESS_FIELDS + ('subject',)
TEXT_OPS = ('contains', 'excludes', '=', '!=', 'startsWith', 'endsWith')
APPLY_ACTIONS = ('move', 'read', 'flag')  # What 对已有邮件执行 may do; never forward.
SIZE_OPS = ('>', '<')
MAX_TEXT = 1000


def _text_condition(c):
    if c.get('field') not in TEXT_FIELDS or c.get('operator') not in TEXT_OPS or not isinstance(c.get('operand'), str):
        return None
    # Webmail stores lists as 'a, b'; the page edits them as 'a,b'.
    return {'field': c['field'], 'op': c['operator'], 'value': ','.join(v.strip() for v in c['operand'].split(',') if v.strip())}


def _value_key(field, value):
    """A list value as the server matches it: an address field by its addresses (extractAddress)."""
    items = [v.strip() for v in str(value).split(',') if v.strip()]
    if field in ADDRESS_FIELDS:
        items = [(re.findall(r'<([^<>]+)>', v) or [v.strip('"\' ')])[-1] for v in items]
    return sorted({v.casefold() for v in items})


def same_rule(a, b):
    """Whether two rule views mean the same rule, however the server spelled the value lists."""
    key = lambda r: ([{**c, 'value': _value_key(c['field'], c['value'])} if 'value' in c and c['field'] in TEXT_FIELDS else c for c in r['conditions']],
                     r['actions'], r['match'], r['stop'], r['enabled'])
    return key(a) == key(b)


ATTACHED = ['attached', 'linkAttached']  # 普通附件 + 超大附件: webmail's "all attachment types".


def _attachment_condition(c):
    operand = c.get('operand')
    if c.get('field') == 'flags' and c.get('operator') == 'contains' and sorted(operand if isinstance(operand, list) else [operand]) == sorted(ATTACHED):
        return {'field': 'attachment'}
    return None


def _size_condition(c):
    # Webmail stores a size in KB (S.03931.js xi(): value * 1024^unit, unit 0 = KB); the page works in bytes.
    if c.get('field') == 'size' and c.get('operator') in SIZE_OPS and type(c.get('operand')) in (int, float) and c['operand'] > 0:
        return {'field': 'size', 'op': c['operator'], 'value': int(c['operand'] * 1024)}
    return None


def _condition_view(c):
    if not isinstance(c, dict):
        return None
    return _text_condition(c) or _attachment_condition(c) or _size_condition(c)


def _action_view(a):
    if not isinstance(a, dict):
        return None
    kind, value = a.get('type'), a.get('value')
    if kind == 'move' and type(a.get('target')) in (int, str) and str(a['target']):
        return {'type': 'move', 'folder': str(a['target'])}
    # Mark read / flag: {type:'flags', value:{read|flagged:true}} (flag verified on a webmail-made rule,
    # analysis/rules-cal-sample-shape-2026-10-18.json; webmail adds label0 when it has flag colours).
    if kind == 'flags' and isinstance(value, dict):
        if value == {'read': True}:
            return {'type': 'read'}
        if value.get('flagged') is True and set(value) <= {'flagged', 'label0'}:
            return {'type': 'flag'}
    if kind == 'forward' and isinstance(a.get('target'), str) and '@' in a['target'] and set(a) <= {'type', 'target', 'keepLocal'}:
        return {'type': 'forward', 'to': a['target']}
    if kind == 'tags' and isinstance(value, list) and len(value) == 1:
        return {'type': 'tag', 'tag': str(value[0])}
    return None


def rule_view(raw):
    """A server rule as the settings page shows it."""
    conditions = raw.get('condictions', raw.get('conditions')) or []
    actions = raw.get('actions') or []
    cv = [_condition_view(c) for c in conditions]
    av = [_action_view(a) for a in actions]
    return {'id': raw.get('id'), 'name': str(raw.get('name') or ''), 'enabled': raw.get('disabled') is not True,
            'match': 'any' if raw.get('operator') == 'or' else 'all', 'stop': raw.get('continue') is False,
            'conditions': [c for c in cv if c], 'actions': [a for a in av if a],
            'editable': bool(conditions) and all(cv) and bool(actions) and all(av) and not set(raw) - RULE_KEYS
                        and not str(raw.get('name', '')).startswith('##'),  # '##VIP' is webmail's VIP rule.
            'counts': {'conditions': len(conditions), 'actions': len(actions)}}


RULE_KEYS = {'id', 'name', 'continue', 'operator', 'disabled', 'condictions', 'conditions', 'actions'}


def _condition_server(c):
    """Built as webmail's rule editor builds a condition (S.03931.js, setting.filter.letter)."""
    field = c.get('field')
    if field in TEXT_FIELDS:
        value = str(c.get('value') or '').strip()
        op = c.get('op') if c.get('op') in TEXT_OPS else 'contains'
        if not value or len(value) > MAX_TEXT:
            raise AppError('INVALID_RULE')
        result = {'field': field, 'operator': op, 'operand': value, 'ignoreCase': True, 'listOfValues': True}
        if field in ADDRESS_FIELDS:
            result.update(forAddress=True, extractAddress=True)
        return result
    if field == 'attachment':
        return {'field': 'flags', 'operator': 'contains', 'operand': list(ATTACHED)}
    if field == 'size':
        value = c.get('value')
        if type(value) is not int or not 1024 <= value < 2 * 1024 ** 3 or c.get('op') not in SIZE_OPS:
            raise AppError('INVALID_RULE')
        return {'field': 'size', 'operator': c['op'], 'operand': round(value / 1024)}
    raise AppError('INVALID_RULE')


def _action_server(a, folders, tags):
    kind = a.get('type')
    if kind == 'move':
        folder = str(a.get('folder') or '')
        if folder not in folders:
            raise AppError('INVALID_RULE')
        return {'type': 'move', 'target': int(folder) if folder.isdigit() else folder}
    if kind == 'read':
        return {'type': 'flags', 'value': {'read': True}}
    if kind == 'flag':
        return {'type': 'flags', 'value': {'flagged': True}}
    if kind == 'forward':
        to = str(a.get('to') or '').strip()
        if not re.fullmatch(r'[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+', to):
            raise AppError('INVALID_RULE')
        return {'type': 'forward', 'target': to, 'keepLocal': True}  # Webmail: also keep the mail here.
    if kind == 'tag':
        tag = str(a.get('tag') or '')
        if tag not in tags:
            raise AppError('INVALID_RULE')
        return {'type': 'tags', 'value': [tag]}
    raise AppError('INVALID_RULE')


def rule_server(view, folders, tags, rule_id=None):
    """A rule from the settings page as the server takes it (user:addMailRules / updateMailRules)."""
    if not isinstance(view, dict):
        raise AppError('INVALID_RULE')
    name = str(view.get('name') or '').strip()
    conditions = view.get('conditions') or []
    actions = view.get('actions') or []
    if not name or len(name) > 60 or not isinstance(conditions, list) or not isinstance(actions, list) \
            or not 0 < len(conditions) <= 10 or not 0 < len(actions) <= 6:
        raise AppError('INVALID_RULE')
    result = {'name': name, 'continue': view.get('stop') is not True, 'disabled': view.get('enabled') is False,
              'conditions': [_condition_server(c if isinstance(c, dict) else {}) for c in conditions],
              'actions': [_action_server(a if isinstance(a, dict) else {}, folders, tags) for a in actions]}
    if view.get('match') == 'any':
        result['operator'] = 'or'
    if rule_id is not None:
        result['id'] = rule_id
    return result


# Auto-reply ----------------------------------------------------------------------
AUTOREPLY_ATTRS = ['autorepmode', 'autoreptext', 'autorep_condition', 'autorep_welcometip']
MAX_REPLY = 4000
STAMP = re.compile(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d')


def autoreply_view(attrs):
    attrs = attrs if isinstance(attrs, dict) else {}
    cond = attrs.get('autorep_condition') if isinstance(attrs.get('autorep_condition'), dict) else {}
    stamp = lambda v: v if isinstance(v, str) and STAMP.fullmatch(v) else ''
    return {'enabled': attrs.get('autorepmode') in (1, '1', True), 'text': str(attrs.get('autoreptext') or ''),
            'start': stamp(cond.get('begintime')), 'end': stamp(cond.get('endtime')),
            'internalOnly': cond.get('instation') in (1, '1', True), 'welcomeTip': attrs.get('autorep_welcometip') in (1, '1', True)}


def autoreply_server(view, now_text):
    """Validated page values -> user:setAttrs attrs. now_text: the account zone's current time."""
    if not isinstance(view, dict):
        raise AppError('INVALID_AUTOREPLY')
    enabled, text = view.get('enabled') is True, str(view.get('text') or '').strip()
    start, end = str(view.get('start') or ''), str(view.get('end') or '')
    for value in (start, end):
        if value and not STAMP.fullmatch(value):
            raise AppError('INVALID_AUTOREPLY')
    if len(text) > MAX_REPLY or (enabled and (not text or not start)):  # Webmail: 开始时间未设置.
        raise AppError('INVALID_AUTOREPLY')
    if start and end and end < start:
        raise AppError('INVALID_AUTOREPLY')
    if enabled and end and end < now_text:
        raise AppError('AUTOREPLY_ENDED')  # Webmail: 结束时间不能早于当前时间.
    return {'autorepmode': 1 if enabled else 0, 'autoreptext': text,
            'autorep_condition': {'begintime': start, 'endtime': end, 'instation': 1 if view.get('internalOnly') else 0},
            'autorep_welcometip': 1 if view.get('welcomeTip') else 0}
