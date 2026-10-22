"""任务关系 (P1): 上级 / 子任务 and 前置 / 后续 between tasks.

The relations are written in the task's own 阻塞与依赖 field, as people already do there
and as the task assistant writes them (T1b): 「依赖 #12」「前置 #12」 for a predecessor and
「上级 #8」「属于 #8」「父任务 #8」 for the parent. So every relation is in the workbook's
existing column (lossless both ways) and readable by a person. Pure functions.
"""
import re

from coremail_desktop.errors import AppError
from coremail_desktop.tasks.rules import CANCELED, DONE

DEP = re.compile(r'(?:依赖|前置|等待|先完成)\s*[:：]?\s*((?:#\s*\d{1,5}\s*[、,，和及/]?\s*)+)')
PARENT = re.compile(r'(?:上级|属于|父任务|母任务)\s*[:：]?\s*#\s*(\d{1,5})')
NUMBER = re.compile(r'#\s*(\d{1,5})')


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

