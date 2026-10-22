"""工作记忆 (design 5): the facts of 理解卡片 merged into per-project memory.

A fact is kept for a project of the user's list only (the 系统 of unfinished tasks plus the
projects added by hand, with their aliases). The list has two levels: a project (NG BOSS)
and its systems (BI, CRM…, parent = the project); a fact is kept under a system when it is
about that one, under the project when about the whole. A newer fact replaces an older one when the
model says so (replaces: one of the F refs it was shown) and its mail is not older; the old
one stays as superseded,
so the history shows how things changed. Replacing a fact the user wrote, or a colleague's
fact by an external mail, is only proposed (pending) for the user to accept (D5). A fact the
user marked wrong never comes back. Pure functions: the store is memory/store.py.
"""
import re
import time
import uuid

STATES = ('current', 'superseded', 'wrong', 'pending')
ROLES = ('own', 'watch')  # 我负责 / 我关注
PROMPT_FACTS = 20  # Current facts shown to the model, newest first, to say what a new one replaces.
PROJECTS = 60
NAME = 30
_last = [0.0]


def _seq():
    """Increasing even within one clock tick: the order facts were written in."""
    _last[0] = max(time.time(), _last[0] + 1e-6)
    return _last[0]


def norm(value):
    return re.sub(r'\s+', '', value or '').casefold()


def project_list(manual, systems):
    """[{name, role, aliases, parent, source}]: the projects added or changed by hand (kv
    'projects') and the 系统 of unfinished tasks that no manual entry names (the user's own,
    so 我负责). parent: the project a system belongs to ('' for a project); only one level,
    and a system takes its project's role."""
    out = [{'name': p['name'], 'role': p.get('role') if p.get('role') in ROLES else 'watch', 'aliases': list(p.get('aliases') or []),
            'parent': p.get('parent') or '', 'source': p.get('source') if p.get('source') in ('manual', 'task') else 'manual'}
           for p in manual or () if isinstance(p, dict) and p.get('name')]
    tops = {x['name']: x for x in out if not x['parent']}
    for x in out:
        if x['parent'] not in tops or x['parent'] == x['name']:
            x['parent'] = ''  # Its project is gone: a project of its own.
    tops = {x['name']: x for x in out if not x['parent']}
    for x in out:
        if x['parent']:
            x['role'] = tops[x['parent']]['role']
    covered = {norm(n) for p in out for n in [p['name'], *p['aliases']]}
    for system in systems or ():
        if norm(system) and norm(system) not in covered:
            out.append({'name': system, 'role': 'own', 'aliases': [], 'parent': '', 'source': 'task'})
            covered.add(norm(system))
    return out[:PROJECTS]


def family(name, projects):
    """A project's own name and its systems' (a system: itself only)."""
    return {name} | {p['name'] for p in projects if p.get('parent') == name}


def top(name, projects):
    """The project a name belongs to: its parent, or itself."""
    found = next((p for p in projects if p['name'] == name), None)
    return (found or {}).get('parent') or name


def resolve(name, projects):
    """The list's name for a project the model or the user wrote, or '' when it is not listed."""
    key = norm(name)
    if not key:
        return ''
    return next((p['name'] for p in projects if key in {norm(n) for n in [p['name'], *p['aliases']]}), '')


def prompt_facts(facts, projects, limit=PROMPT_FACTS):
    """{'F1': fact}: the newest current facts of the listed projects, for the model to name
    the one a new fact replaces."""
    names = {p['name'] for p in projects}
    live = sorted((f for f in facts.values() if f['state'] == 'current' and f['project'] in names), key=lambda f: f.get('seq', 0), reverse=True)
    return {f'F{i}': f for i, f in enumerate(live[:limit], 1)}


def new_fact(project, kind, text, source, actor='ai', on_day=''):
    return {'id': 'f-' + uuid.uuid4().hex[:12], 'project': project, 'type': kind, 'text': text,
            'date': on_day or (source or {}).get('date', '')[:10], 'sources': [source] if source else [], 'state': 'current',
            'replaces': '', 'actor': actor, 'seq': _seq()}


def merge(facts, items, source, refs, projects):
    """The facts to write for one mail's card facts. source: {mid, messageId, subject, date,
    from, internal}; refs: prompt_facts as shown to the model."""
    out = {}
    for item in items or ():
        project = resolve(item.get('project'), projects)
        if not project:
            continue  # Only the user's projects are remembered (5.2).
        key = norm(item['text'])
        same = [f for f in list(facts.values()) + list(out.values()) if f['project'] == project and norm(f['text']) == key]
        if any(f['state'] == 'wrong' for f in same):
            continue
        live = next((f for f in same if f['state'] in ('current', 'pending')), None)
        if live:
            if source and all(s.get('mid') != source.get('mid') for s in live['sources']):
                out[live['id']] = {**live, 'sources': (live['sources'] + [source])[-10:]}
            continue
        fact = new_fact(project, item['type'], item['text'], source)
        old = refs.get(item.get('replaces') or '')
        old = out.get(old['id'], old) if old else None
        if old and old['project'] == project and old['state'] == 'current':
            colleague = any(s.get('internal') for s in old['sources'])
            if fact['date'] and old.get('date') and fact['date'] < old['date']:
                out[fact['id']] = {**fact, 'state': 'superseded'}  # An older mail read later: history only.
                continue
            if old.get('actor') == 'user' or (colleague and not source.get('internal')):
                fact['state'] = 'pending'  # 待确认: the user decides.
            else:
                out[old['id']] = {**old, 'state': 'superseded'}
            fact['replaces'] = old['id']
        out[fact['id']] = fact
    return list(out.values())


def correct(facts, fact_id, action, text=''):
    """The user's word on one fact -> the facts to write. wrong: 这条不对; stale: 已过时;
    edit: 改成 text (the user's fact replaces it); accept / reject: a pending replacement."""
    fact = facts.get(fact_id)
    if fact is None:
        return None
    if action == 'wrong':
        return [{**fact, 'state': 'wrong'}]
    if action == 'stale':
        return [{**fact, 'state': 'superseded'}]
    if action == 'edit':
        new = new_fact(fact['project'], fact['type'], text, None, actor='user', on_day=fact.get('date', ''))
        new.update(replaces=fact['id'], sources=fact['sources'])
        return [{**fact, 'state': 'superseded'}, new]
    if action in ('accept', 'reject') and fact['state'] == 'pending':
        if action == 'reject':
            return [{**fact, 'state': 'wrong'}]
        old = facts.get(fact.get('replaces'))
        return [{**fact, 'state': 'current'}] + ([{**old, 'state': 'superseded'}] if old and old['state'] == 'current' else [])
    return None


def timeline(facts, project, projects=()):
    """Every fact of a project (with its systems') except the wrong ones, newest first; the
    replaced text alongside."""
    names = family(project, projects)
    rows = sorted((f for f in facts.values() if f['project'] in names and f['state'] != 'wrong'),
                  key=lambda f: (f.get('date') or '', f.get('seq', 0)), reverse=True)
    return [{**f, 'was': (facts.get(f.get('replaces')) or {}).get('text', '')} for f in rows]


def week(facts, start, end, projects=()):
    """本周你需要知道的: per project (a system's facts under its project), the facts dated
    start..end (new, or changing an older one)."""
    groups = {}
    for f in sorted(facts.values(), key=lambda f: (f.get('date') or '', f.get('seq', 0))):
        if f['state'] in ('current', 'superseded') and start <= (f.get('date') or '') <= end:
            groups.setdefault(top(f['project'], projects), []).append({**f, 'was': (facts.get(f.get('replaces')) or {}).get('text', '')})
    return [{'project': p, 'facts': rows[::-1], 'key': key_facts(rows[::-1])}
            for p, rows in sorted(groups.items(), key=lambda kv: -max(f.get('seq', 0) for f in kv[1]))]


KEY_ORDER = ('decision', 'date', 'owner', 'status', 'other')


def key_facts(rows, count=3):
    """The few facts of a week's project worth reading first when there is no AI brief:
    current ones (not replaced later the same week), decisions and dates before progress,
    newest first; ids."""
    current = [f for f in rows if f['state'] == 'current']
    rank = lambda f: KEY_ORDER.index(f['type']) if f['type'] in KEY_ORDER else len(KEY_ORDER)
    return [f['id'] for f in sorted(current, key=rank)[:count]]  # sorted is stable: rows are newest first.


def search(facts, project='', text='', history=False, projects=()):
    """memory_search: facts of a project (as resolved; a project with its systems) whose text
    has every word; current ones unless history."""
    words = [norm(w) for w in (text or '').split() if norm(w)]
    names = family(project, projects) if project else None
    rows = [f for f in facts.values() if f['state'] != 'wrong' and (history or f['state'] == 'current')
            and (names is None or f['project'] in names) and all(w in norm(f['text']) + norm(f['project']) for w in words)]
    return sorted(rows, key=lambda f: (f.get('date') or '', f.get('seq', 0)), reverse=True)
