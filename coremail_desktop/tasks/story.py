"""事件演进图: a task's story as steps, newest first.

The steps come from the 进展与历史记录 column (one dated line, with any following undated
lines, is one step; imported tasks carry their whole past there) and from changes made in
hongyan that left no line there (edits, undo) and mail linked on its own. Each step gets a
short title, a one-line summary and a kind (for its icon): from the AI when it is set up
(kept, encrypted, so each step is named once), else from the text itself.
"""
import hashlib
import json
import re
from collections import Counter

from coremail_desktop.errors import AppError

PREFIX = re.compile(r'^(?:用户(?:已)?(?:确认|反馈|更正|表示|说明)?|本轮|补充)[：:，,\s]*')
LINE = re.compile(r'^(\d{4}-\d{2}-\d{2})[：:\s]*(.*)$')
KINDS = ('start', 'analysis', 'contact', 'wait', 'blocked', 'progress', 'done', 'cancel', 'reopen', 'change', 'mail')
TITLE_TOKENS = 1500
BATCH = 40
EVENT_KIND = {'created': 'start', 'completed': 'done', 'canceled': 'cancel', 'reopened': 'reopen', 'edited': 'change',
              'undone': 'change', 'external': 'change', 'progress': 'progress'}
EVENT_TITLE = {'created': '新建任务', 'edited': '修改任务信息', 'undone': '撤销修改', 'external': '采纳 Excel 修改', 'completed': '完成',
               'canceled': '取消', 'reopened': '重新打开', 'progress': '记录进展'}
FIELD_TEXT = {'system': '系统', 'content': '任务内容', 'priority': '优先级', 'status': '状态', 'latest': '最新进展', 'owner': '负责人',
              'start': '开始日期', 'due': '计划完成日期', 'end': '结束日期', 'blockers': '阻塞与依赖', 'next': '下一步',
              'updated': '最近更新日期', 'history': '历史记录', 'acceptance': '验收标准'}


def key_of(day, text):
    return hashlib.sha256(f'{day}\n{text}'.encode('utf-8')).hexdigest()[:20]


def history_steps(history):
    """[(day or '', text, first line)] in the order written."""
    steps = []
    for raw in str(history or '').split('\n'):
        found = LINE.match(raw.strip())
        if found:
            steps.append([found.group(1), found.group(2).strip(), raw.strip()])
        elif raw.strip():
            if steps:
                steps[-1][1] += '\n' + raw.strip()
            else:
                steps.append(['', raw.strip(), raw.strip()])
    return [tuple(s) for s in steps]


def _changes_text(changes):
    names = [FIELD_TEXT.get(f, f) for f in changes if f not in ('updated', 'history')]
    return '修改了' + '、'.join(names) if names else ''


def added_line(change):
    """The first line a change appended to the history column, or '' when it did not append."""
    if change and isinstance(change[1], str) and isinstance(change[0], str) and change[1].startswith(change[0]):
        return change[1][len(change[0]):].lstrip('\n').split('\n')[0].strip()
    return ''


def undone_seqs(events):
    """Events that were undone, and the undo events themselves (both left out of the story and the report)."""
    undone = {e.get('undoes') for _, _, e in events if e.get('undoes')}
    return undone | {seq for seq, _, e in events if e.get('kind') == 'undone'}


def visible_lines(history, events):
    """[(order, (day, text, first line))] of the history column without the lines an undone
    change added and the 撤销 lines (they stay in the column). events: this task's, oldest first."""
    lines = history_steps(history)
    skip = undone_seqs(events)
    hidden = Counter(added for added in (added_line((e.get('changes') or {}).get('history')) for seq, _, e in events if seq in skip) if added)
    kept = []
    for order in reversed(range(len(lines))):  # The latest copy of a hidden line is the undone one.
        if hidden[lines[order][2]]:
            hidden[lines[order][2]] -= 1
        else:
            kept.append(order)
    return [(order, lines[order]) for order in sorted(kept)]


def build(task, events, links):
    """The steps of one task, newest first. events: [(seq, review, event)] oldest first.
    An undone change and the undo itself stay in 变更记录 and the history column, not here."""
    skip = undone_seqs(events)
    lines = history_steps(task.get('history'))
    by_line = {}
    extra = []
    for seq, review, e in events:
        added = added_line((e.get('changes') or {}).get('history'))
        if seq in skip or e.get('kind') in ('imported', 'linked', 'unlinked') or review == 'rejected':
            continue
        info = {'seq': seq, 'event': e.get('kind'), 'actor': e.get('actor'), 'review': review, 'mails': e.get('mails') or [],
                'basis': e.get('basis') or '', 'changes': e.get('changes') or {}, 'note': e.get('note') or '', 'at': e.get('at') or 0}
        if added:
            by_line.setdefault(added, info)
            continue
        if review != 'confirmed':
            continue  # A change waiting for review is not part of the story yet.
        text = e.get('note') or _changes_text(e.get('changes') or {}) or EVENT_TITLE.get(e.get('kind'), '修改')
        if e.get('kind') == 'created':
            text = '新建任务：' + str((e.get('changes') or {}).get('content', ['', task.get('content', '')])[1])[:200]
        extra.append({'day': e.get('date') or '', 'text': text, 'source': 'event', 'kind': EVENT_KIND.get(e.get('kind'), 'change'), **info})
    steps = []
    for order, (day, text, first) in visible_lines(task.get('history'), events):
        info = by_line.get(first, {})
        kind = EVENT_KIND.get(info.get('event'), 'progress') if info else 'progress'
        steps.append({'day': day, 'text': text, 'source': 'history', 'kind': kind, 'order': order, **info})
    linked = {m['mid'] for s in steps + extra for m in s.get('mails') or []}
    for link in links:
        if link['mid'] not in linked:
            extra.append({'day': str(link.get('date') or '')[:10], 'text': f'关联邮件：{link.get("subject") or "（无主题）"}', 'source': 'mail',
                          'kind': 'mail', 'mails': [{k: link.get(k, '') for k in ('mid', 'subject', 'date', 'from')}], 'at': link.get('at') or 0})
    for i, s in enumerate(extra):
        s['order'] = len(lines) + i
    steps += extra
    for s in steps:
        s['key'] = key_of(s['day'], s['text'])
    # Newest first: by day, then by when it was written; undated lines (old imports) at the end.
    steps.sort(key=lambda s: (s['day'] or '0000', s.get('at') or 0, s['order']), reverse=True)
    return steps


# Titles --------------------------------------------------------------------
def fallback(step):
    """A title from the text itself when no AI title is kept."""
    if step['source'] == 'event' and step.get('event') in EVENT_TITLE:
        title = EVENT_TITLE[step['event']]
    elif step['source'] == 'mail':
        title = '关联邮件'
    else:
        text = PREFIX.sub('', step['text'])
        title = re.split(r'[，。；;,.!！?？：:\n（(]', text, maxsplit=1)[0].strip() or '进展'
        title = title if len(title) <= 14 else title[:14] + '…'
    summary = step['text'].replace('\n', ' ')
    return {'title': title, 'summary': summary if len(summary) <= 60 else summary[:60] + '…', 'kind': step['kind'], 'ai': False}


TITLE_VERSION = 2  # Kept titles of an older prompt are made again.
SYSTEM = """你为工作任务的演进图命名每个环节，只输出 JSON。读者只扫一眼标题，就要知道任务走到了哪一步、结果如何。
title：4～10 个字，写这一步的进度节点和结果（做了什么／得到什么结论／卡在哪里），像里程碑名称。
  好的例子：「申请开通网络」「网络未通，待客户」「客户确认漏洞关闭」「完成 NGINX 升级」「改由 302 跟踪」「取消：需求不明确」。
  不要以「用户确认」「用户反馈」「用户更正」「本轮」「补充」开头，不要照抄原文第一句，不要只写「进展」「更新」「邮件证据补充」这类没有信息量的词。
summary：一句话，不超过 30 个字，补充最关键的细节（对象、对方、数字），同样不要以「用户确认」开头。
kind：只能是 start 开始/新建、analysis 分析排查、contact 沟通联系、wait 等待对方、blocked 受阻、progress 一般进展、done 完成、
cancel 取消、reopen 重新打开、change 修改信息、mail 邮件 之一。
只依据给出的文字，不加推断，不编造结果。输出：{"items":[{"id":"…","title":"…","summary":"…","kind":"…"}]}"""


def title_messages(task, steps):
    items = [{'id': s['key'], 'date': s['day'], 'text': s['text'][:400]} for s in steps[:BATCH]]
    head = {'task': task['content'][:200], 'system': task['system'], 'status': task['status']}
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps({'任务': head, '环节': items}, ensure_ascii=False)}]


def parse_titles(content, keys):
    try:
        start, end = content.find('{'), content.rfind('}')
        value = json.loads(content[start:end + 1])
        items = value['items']
    except (ValueError, KeyError, TypeError):
        raise AppError('AI_BAD_RESPONSE') from None
    found = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get('id') not in keys:
            continue
        title, summary = str(item.get('title') or '').strip()[:16], str(item.get('summary') or '').strip()[:60]
        if title:
            found[item['id']] = {'title': title, 'summary': summary, 'kind': item.get('kind') if item.get('kind') in KINDS else 'progress',
                                 'ai': True, 'v': TITLE_VERSION}
    return found
