"""周报: the week's task records gathered here, written up by the AI as an internal Chinese
report (fixed sections), rendered to HTML here (the model's text is escaped, never markup).

The week runs Sunday to Saturday (the Saudi work week starts on Sunday); dates are Riyadh.
"""
import html
import json
from datetime import date, timedelta

from coremail_desktop.errors import AppError
from coremail_desktop.tasks.rules import CANCELED, DONE, OPEN_STATUSES, is_day
from coremail_desktop.tasks.story import history_steps

MARKER = '鸿雁工作周报'
SECTIONS = ('本周完成', '本周进展', '受阻与风险', '下周计划')
REPORT_TOKENS = 2500
MAX_DAYS = 62


def week_of(today):
    """(this week's Sunday, today) and (last week's Sunday, Saturday)."""
    day = date.fromisoformat(today)
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return (start.isoformat(), today), ((start - timedelta(days=7)).isoformat(), (start - timedelta(days=1)).isoformat())


def check_range(start, end):
    if not (is_day(start) and is_day(end)) or start > end or (date.fromisoformat(end) - date.fromisoformat(start)).days > MAX_DAYS:
        raise AppError('TASK_REPORT_RANGE')
    return start, end


def _clip(value, limit):
    value = value.get('text', '') if isinstance(value, dict) else str(value or '')
    return value if len(value) <= limit else value[:limit] + '…'


def gather(tasks, events, start, end):
    """What happened between start and end (history lines and changes dated in it), and every
    unfinished task's status, next step and blockers (for 风险 and 下周计划)."""
    by_task = {}
    for _, review, e in events:
        if review == 'confirmed' and e.get('kind') in ('created', 'completed', 'canceled', 'reopened') and start <= str(e.get('date') or '') <= end:
            by_task.setdefault(e['task'], []).append({'date': e['date'], 'kind': e['kind']})
    active = []
    for task in sorted(tasks, key=lambda t: t['id']):
        steps = [{'date': d, 'text': _clip(t, 400)} for d, t, _ in history_steps(task.get('history')) if d and start <= d <= end]
        changes = by_task.get(task['id'], [])
        finished = task['status'] in (DONE, CANCELED) and is_day(task.get('end')) and start <= task['end'] <= end
        if steps or changes or finished:
            active.append({'id': task['id'], 'system': task['system'], 'content': _clip(task['content'], 200), 'status': task['status'],
                           'end': task.get('end') if finished else '', 'records': steps, 'changes': changes})
    unfinished = [{'id': t['id'], 'system': t['system'], 'content': _clip(t['content'], 200), 'status': t['status'],
                   'due': _clip(t.get('due'), 20), 'latest': _clip(t.get('latest'), 200), 'next': _clip(t.get('next'), 200),
                   'blockers': _clip(t.get('blockers'), 200)}
                  for t in sorted(tasks, key=lambda t: t['id']) if t['status'] in OPEN_STATUSES]
    return {'from': start, 'to': end, 'active': active, 'unfinished': unfinished}


SYSTEM = f"""你为用户写「{MARKER}」（中文，给公司内部的领导和同事看），只输出 JSON。
材料是这段时间的任务记录（records 是带日期的进展原文，changes 是新建/完成/取消/重新打开）和全部未完成任务。
写四节，每节若干条，每条一句话（不超过 60 个字），以「#编号 系统」开头，然后写清楚做了什么、结果或卡在哪里：
- 本周完成：这段时间内完成的任务（status 为已完成且 end 在范围内）。取消的写成「取消：原因」，也放在这里。
- 本周进展：其他有记录的任务，概括这段时间的进展，不要逐条照抄原文。
- 受阻与风险：状态为受阻的任务，以及计划完成日期已过或临近的未完成任务；写清楚等谁、需要什么协调。
- 下周计划：未完成任务中有下一步的，写下周要做什么。
规则：只依据材料，不编造结果、日期、人名或数字；没有内容的一节给空数组；同一任务在一节里只出现一次。
另外给一个邮件主题 subject（例如「Saudi-NG 工作周报（10.18–10.24）」）和一句总述 summary（不超过 60 个字）。
输出：{{"subject":"…","summary":"…","sections":[{{"title":"本周完成","items":["…"]}},{{"title":"本周进展","items":[]}},{{"title":"受阻与风险","items":[]}},{{"title":"下周计划","items":[]}}]}}"""


def messages(material):
    return [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': '材料（JSON）：\n' + json.dumps(material, ensure_ascii=False)}]


def parse(content):
    try:
        start, end = content.find('{'), content.rfind('}')
        value = json.loads(content[start:end + 1])
        sections = value['sections']
    except (ValueError, KeyError, TypeError):
        raise AppError('AI_BAD_RESPONSE') from None
    found = {s.get('title'): s.get('items') for s in sections if isinstance(s, dict)} if isinstance(sections, list) else {}
    clean = [(title, [str(i).strip()[:200] for i in (found.get(title) or []) if isinstance(i, str) and i.strip()][:30]) for title in SECTIONS]
    return {'subject': str(value.get('subject') or '').strip()[:120], 'summary': str(value.get('summary') or '').strip()[:200], 'sections': clean}


def render(report, material):
    """The report as mail HTML and plain text; every word from the model is escaped."""
    e = html.escape
    start, end = material['from'], material['to']
    subject = report['subject'] or f'工作周报（{start[5:].replace("-", ".")}–{end[5:].replace("-", ".")}）'
    parts = [f'<p style="color:#475467;font-size:13px">统计周期：{e(start)} 至 {e(end)}（沙特时间）'
             f' · 未完成任务 {len(material["unfinished"])} 项</p>']
    if report['summary']:
        parts.append(f'<p>{e(report["summary"])}</p>')
    lines = [subject, f'统计周期：{start} 至 {end}', report['summary'], '']
    for title, items in report['sections']:
        parts.append(f'<h3 style="margin:16px 0 6px;font-size:15px">{e(title)}</h3>')
        parts.append('<ul style="margin:0;padding-left:20px">' + ''.join(f'<li style="margin:3px 0">{e(i)}</li>' for i in items) + '</ul>'
                     if items else '<p style="color:#8a94a6;margin:0">无</p>')
        lines += [title] + [f'- {i}' for i in items or ['无']] + ['']
    cell = 'style="border:1px solid #d0d5dd;padding:4px 8px;text-align:left;vertical-align:top"'
    rows = ''.join(f'<tr><td {cell}>#{t["id"]}</td><td {cell}>{e(t["system"])}</td><td {cell}>{e(t["content"])}</td>'
                   f'<td {cell}>{e(t["status"])}</td><td {cell}>{e(t["next"] or "—")}</td></tr>' for t in material['unfinished'])
    if rows:
        head = ''.join(f'<th {cell}>{h}</th>' for h in ('编号', '系统', '任务', '状态', '下一步'))
        parts.append('<h3 style="margin:16px 0 6px;font-size:15px">未完成任务一览</h3>'
                     f'<table style="border-collapse:collapse;font-size:13px"><tr style="background:#f2f4f7">{head}</tr>{rows}</table>')
    return {'subject': subject, 'html': ''.join(parts) + '<p><br></p>', 'text': '\n'.join(lines).strip()}
