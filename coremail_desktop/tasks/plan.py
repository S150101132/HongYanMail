"""任务关系与甘特图 (P1): 上级 / 子任务 and 前置 / 后续 between tasks.

The relations are written in the task's own 阻塞与依赖 field, as people already do there
and as the task assistant writes them (T1b): 「依赖 #12」「前置 #12」 for a predecessor and
「上级 #8」「属于 #8」「父任务 #8」 for the parent. So every relation is in the workbook's
existing column (lossless both ways) and readable by a person. Pure functions.
"""
import re
from datetime import date, timedelta

from coremail_desktop.errors import AppError
from coremail_desktop.tasks.rules import CANCELED, DONE, OPEN_STATUSES, is_day, overdue

DEP = re.compile(r'(?:依赖|前置|等待|先完成)\s*[:：]?\s*((?:#\s*\d{1,5}\s*[、,，和及/]?\s*)+)')
PARENT = re.compile(r'(?:上级|属于|父任务|母任务)\s*[:：]?\s*#\s*(\d{1,5})')
NUMBER = re.compile(r'#\s*(\d{1,5})')
GANTT_DAYS = 400  # The longest span drawn; a bar beyond it is cut at the edge.


def links(task):
    """(parent or None, [predecessors]) written in one task's 阻塞与依赖."""
    text = str(task.get('blockers') or '')
    parent = PARENT.search(text)
    deps = []
    for group in DEP.finditer(text):
        deps += [int(n) for n in NUMBER.findall(group.group(1)) if int(n) not in deps]
    own = task['id']
    return (int(parent.group(1)) if parent and int(parent.group(1)) != own else None), [d for d in deps if d != own]


def relations(tasks):
    """{id: {parent, children, deps, next, missing}} for every task; numbers that name no task
    are listed in missing (shown, never followed)."""
    out = {t: {'parent': None, 'children': [], 'deps': [], 'next': [], 'missing': []} for t in tasks}
    for task_id, task in tasks.items():
        parent, deps = links(task)
        rel = out[task_id]
        if parent is not None:
            if parent in tasks:
                rel['parent'] = parent
                out[parent]['children'].append(task_id)
            else:
                rel['missing'].append(parent)
        for dep in deps:
            if dep in tasks:
                rel['deps'].append(dep)
                out[dep]['next'].append(task_id)
            else:
                rel['missing'].append(dep)
    return out


def cycle(tasks, task_id, blockers):
    """Would this 阻塞与依赖 text make a loop (a task above or before itself)?"""
    trial = {**tasks, task_id: {**tasks.get(task_id, {'id': task_id}), 'id': task_id, 'blockers': blockers}}
    rel = relations(trial)
    for edge in ('parent', 'deps'):
        seen, todo = set(), [task_id]
        while todo:
            current = todo.pop()
            following = [rel[current]['parent']] if edge == 'parent' else rel[current]['deps']
            for nxt in following:
                if nxt == task_id:
                    return True
                if nxt is not None and nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
    return False


def check(tasks, task_id, blockers):
    if cycle(tasks, task_id, blockers):
        raise AppError('TASK_RELATION_CYCLE')


def rollup(tasks, rel, task_id):
    """A parent's progress from its subtasks (all levels, canceled ones left out)."""
    seen, todo, done, total = set(), list(rel[task_id]['children']), 0, 0
    while todo:
        child = todo.pop()
        if child in seen:
            continue
        seen.add(child)
        todo += rel[child]['children']
        status = tasks[child]['status']
        if status != CANCELED:
            total += 1
            done += status == DONE
    return {'done': done, 'total': total}


def view(tasks, task_id):
    """The 关系 of one task for its detail pane."""
    rel = relations(tasks)[task_id]
    brief = lambda i: {'id': i, 'content': tasks[i].get('content', '')[:60], 'status': tasks[i]['status']}
    return {'parent': brief(rel['parent']) if rel['parent'] else None, 'children': [brief(i) for i in sorted(rel['children'])],
            'deps': [brief(i) for i in rel['deps']], 'next': [brief(i) for i in sorted(rel['next'])], 'missing': rel['missing'],
            'rollup': rollup(tasks, relations(tasks), task_id) if rel['children'] else None,
            'waiting': [i for i in rel['deps'] if tasks[i]['status'] != DONE]}


def unblocked(tasks, finished):
    """After finished (task ids now 已完成): the 受阻 tasks whose predecessors are all done now."""
    rel = relations(tasks)
    found = []
    for task_id in sorted({n for f in finished if f in rel for n in rel[f]['next']}):
        task = tasks[task_id]
        if task['status'] == '受阻' and all(tasks[d]['status'] == DONE for d in rel[task_id]['deps']):
            found.append({'id': task_id, 'content': task.get('content', '')[:60], 'rev': task.get('rev')})
    return found


def gantt(tasks, today, ids=None):
    """甘特图 rows: the tasks with a start or a planned date (ids: only these, e.g. the list on
    screen), parents first with their subtasks below them. A bar runs from 开始日期 to
    计划完成日期 (or 结束日期 when done); a task with one date is a one-day mark."""
    rel = relations(tasks)
    chosen = [t for t in tasks.values() if (ids is None or t['id'] in ids) and (is_day(t.get('start')) or is_day(t.get('due')) or is_day(t.get('end')))]
    keep = {t['id'] for t in chosen}
    rows, placed = [], set()

    def bar(task):
        first = task['start'] if is_day(task.get('start')) else None
        last = task['end'] if task['status'] == DONE and is_day(task.get('end')) else task['due'] if is_day(task.get('due')) else None
        first, last = first or last or task.get('end'), last or first or task.get('end')
        if last < first:
            first, last = last, first
        return first, last

    def place(task, depth):
        if task['id'] in placed:
            return
        placed.add(task['id'])
        first, last = bar(task)
        r = rel[task['id']]
        rows.append({'id': task['id'], 'content': task.get('content', '')[:80], 'system': task.get('system', ''), 'status': task['status'],
                     'start': first, 'end': last, 'depth': depth, 'late': overdue(task, today), 'deps': [d for d in r['deps'] if d in keep],
                     'waiting': any(tasks[d]['status'] != DONE for d in r['deps']), 'parent': r['parent'] if r['parent'] in keep else None,
                     'rollup': rollup(tasks, rel, task['id']) if r['children'] else None})
        for child in sorted((tasks[c] for c in r['children'] if c in keep), key=lambda t: bar(t)[0]):
            place(child, depth + 1)

    roots = [t for t in chosen if rel[t['id']]['parent'] not in keep]
    for task in sorted(roots, key=lambda t: (bar(t)[0], t['id'])):
        place(task, 0)
    if not rows:
        return {'rows': [], 'from': today, 'to': today, 'today': today}
    first = min(min(r['start'] for r in rows), today)
    last = max(max(r['end'] for r in rows), today)
    if (date.fromisoformat(last) - date.fromisoformat(first)).days > GANTT_DAYS:
        first = max(first, (date.fromisoformat(today) - timedelta(days=GANTT_DAYS // 2)).isoformat())
        last = (date.fromisoformat(first) + timedelta(days=GANTT_DAYS)).isoformat()
    return {'rows': rows, 'from': first, 'to': last, 'today': today, 'open': list(OPEN_STATUSES)}
