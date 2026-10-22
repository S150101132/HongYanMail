"""任务中心: the operations the page calls. Every change is checked against the rules,
written in one transaction with its timeline event, exported (Excel + JSON) and verified
by reading the store back; the reply carries the full list of unfinished tasks.

Modes: 'off' (nothing imported), 'trial' (演练: exports go to HongyanData, the task folder
is only read), 'live' (正式: hongyan is the only writer and exports to the task folder).
"""
import copy
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from coremail_desktop.errors import AppError
from coremail_desktop.tasks import plan, transfer
from coremail_desktop.tasks.rules import (CANCELED, DATE_FIELDS, DONE, FIELDS, LABEL, OPEN_STATUSES, OWNER_DEFAULT, STATUSES,
                                          append_history, blank_task, check_blocked, check_fields, day, is_day, missing, overdue,
                                          riyadh_today, sheet_of, text)

EDITABLE = tuple(f for f in FIELDS if f not in ('id', 'history'))
BACKUPS = 14
NAMES = (transfer.OPEN_FILE, transfer.DONE_FILE, transfer.JSON_FILE)
UNDOABLE = ('edited', 'progress', 'completed', 'canceled', 'reopened', 'external')
LINE_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}[：:\s]*')
KIND_TEXT = {'edited': '编辑', 'progress': '记录进展', 'completed': '标记完成', 'canceled': '取消任务', 'reopened': '重新打开', 'external': 'Excel 外部修改'}


class TaskCenter:
    def __init__(self, directory, protector, clock=None):
        from coremail_desktop.tasks.store import TaskStore
        self.dir = Path(directory)
        self.store = TaskStore(self.dir / 'tasks.db', protector)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._ctx = threading.local()  # Per thread: a dry run (plan preview) and who acts (user / ai).
        self._daily_backup()

    def today(self):
        return riyadh_today(self.clock())

    def _settings(self):
        return {'folder': '', 'mode': 'off', **(self.store.get('settings') or {})}

    def close(self):
        self.store.close()

    # Views ---------------------------------------------------------------
    def counts(self):
        tasks = self.store.tasks.values()
        done = sum(1 for t in tasks if t['status'] == DONE)
        canceled = sum(1 for t in tasks if t['status'] == CANCELED)
        return {'open': len(self.store.tasks) - done - canceled, 'done': done, 'canceled': canceled, 'total': len(self.store.tasks)}

    def status(self):
        settings = self._settings()
        return {**settings, 'counts': self.counts(), 'pending': self.store.pending_count(), 'today': self.today(),
                'export': self._export_view(), 'lastImport': (self.store.get('import') or {}).get('summary'),
                'trialFolder': str(self.dir / 'export-trial')}

    def _export_view(self):
        state = self.store.get('export') or {}
        return {k: state.get(k) for k in ('state', 'at', 'external', 'message', 'folder', 'code')}

    def _row(self, task, today):
        return {'id': task['id'], 'system': task['system'], 'content': task['content'], 'status': task['status'],
                'priority': task['priority'], 'due': _shown(task['due']), 'end': _shown(task['end']),
                'updated': _shown(task['updated']), 'overdue': overdue(task, today)}

    def overview(self, view='open', query=''):
        """Rows of one sheet ('open', 'done', 'canceled') or 'all'; query: every word somewhere in the task."""
        wanted = {'open': OPEN_STATUSES, 'done': (DONE,), 'canceled': (CANCELED,), 'all': STATUSES}.get(view)
        if wanted is None:
            raise AppError('TASK_INVALID')
        words = [w for w in str(query or '').casefold().split() if w][:20]
        today = self.today()
        rows = []
        for task in self.store.tasks.values():
            if task['status'] not in wanted:
                continue
            if words:
                hay = ' '.join([str(task['id'])] + [_shown(task[f]) for f in FIELDS if f != 'id']).casefold()
                if not all(w in hay for w in words):
                    continue
            rows.append(self._row(task, today))
        waiting = {}
        for _, _, e in self.store.events(review='pending'):
            waiting[e['task']] = waiting.get(e['task'], 0) + 1
        for row in rows:
            row['pending'] = waiting.get(row['id'], 0)
        rows.sort(key=lambda r: r['id'])
        return {'rows': rows, 'counts': self.counts(), 'pending': self.store.pending_count(), 'export': self._export_view(),
                'mode': self._settings()['mode']}

    def get(self, task_id):
        task = self._task(task_id)
        events = [{'seq': seq, 'review': review, **{k: v for k, v in e.items() if k != 'task'},
                   'changes': {f: v for f, v in (e.get('changes') or {}).items()}}
                  for seq, review, e in self.store.events(task['id'])]
        events.reverse()
        return {'task': {f: _shown(task[f]) if f in DATE_FIELDS else task[f] for f in FIELDS},
                'rev': self.store.revs[task['id']], 'events': events, 'links': self.store.links(task['id']),
                'missing': missing(task), 'overdue': overdue(task, self.today()), 'sheet': sheet_of(task['status']),
                'labels': LABEL}

    def _task(self, task_id):
        try:
            task = self.store.tasks.get(int(task_id))
        except (TypeError, ValueError):
            task = None
        if task is None:
            raise AppError('TASK_NOT_FOUND')
        return task

    def story(self, task_id):
        """事件演进图: the steps newest first, each with its kept AI title or one from its text;
        'missing' lists the steps the AI has not named yet."""
        from coremail_desktop.tasks import story
        task = self._task(task_id)
        steps = story.build(task, self.store.events(task['id']), self.store.links(task['id']))
        kept = self.store.get('storyTitles') or {}
        missing = []
        for step in steps:
            name = kept.get(step['key'])
            if name is None or name.get('v', 1) < story.TITLE_VERSION:
                missing.append(step['key'])
                name = story.fallback(step)
            step.update(name)
        changes = [(seq, e) for seq, review, e in self.store.events(task['id']) if review == 'confirmed' and e.get('changes')]
        last = changes[-1] if changes else None
        pending = [{'seq': seq, **{k: e.get(k) for k in ('kind', 'date', 'note', 'basis', 'changes', 'mails', 'action')}}
                   for seq, review, e in self.store.events(task['id']) if review == 'pending']
        return {'id': task['id'], 'status': task['status'], 'steps': steps, 'missing': missing, 'pending': pending[::-1],
                'undo': last[0] if last and last[1].get('kind') in UNDOABLE else None}

    def keep_titles(self, titles):
        """AI step titles, kept (encrypted) so each step is named once; the newest 5000."""
        kept = self.store.get('storyTitles') or {}
        kept.update(titles)
        self.store.set('storyTitles', dict(list(kept.items())[-5000:]))

    def pending(self):
        """Changes waiting for review (from an Excel file edited outside hongyan)."""
        return [{'seq': seq, **{k: v for k, v in e.items()}} for seq, _, e in self.store.events(review='pending')]

    # Changes -------------------------------------------------------------
    def _write(self, kind, changed, rev, note='', on_day='', extra_events=()):
        """changed: the new task dicts (one, or none for a new task's own event). Records the
        event, exports, verifies; returns the reply every change gets."""
        batch = uuid.uuid4().hex[:12]
        events, tasks, expect = [], [], {}
        actor, basis = getattr(self._ctx, 'actor', 'user'), getattr(self._ctx, 'basis', '')
        for new in changed:
            old = self.store.tasks.get(new['id'])
            before = old or blank_task(new['id'])
            changes = {f: [before.get(f), new.get(f)] for f in FIELDS if before.get(f) != new.get(f)}
            if not changes:
                continue
            if 'blockers' in changes:  # 上级 / 依赖 #N (P1) must not loop back to the task.
                plan.check({**self.store.tasks, **{t['id']: t for t in tasks}}, new['id'], new.get('blockers') or '')
            new['cells'] = {f: v for f, v in (new.get('cells') or {}).items() if f not in changes}
            tasks.append(new)
            event = {'kind': kind, 'date': on_day or self.today(), 'actor': actor, 'batch': batch, 'changes': changes, 'note': note}
            if basis:
                event['basis'] = basis  # The sentence the change was made from (自然语言操作).
            event.update(getattr(self._ctx, 'extra', None) or {})  # e.g. the mails it came from, the event it undoes.
            events.append((new['id'], 'confirmed', event))
            if old is not None:
                expect[new['id']] = rev
        if getattr(self._ctx, 'dry', False):  # Plan preview: what would change, nothing written.
            return {'id': changed[0]['id'] if changed else None, 'kind': kind, 'changes': events[0][2]['changes'] if events else {}}
        events += [(tid, review, {**e, 'batch': batch}) for tid, review, e in extra_events]
        if not tasks and not extra_events:
            return self._reply(None)
        mails = (getattr(self._ctx, 'extra', None) or {}).get('mails') or []
        links = []
        for task in tasks:  # A change made from mail also links that mail to the task.
            known = {l['mid'] for l in self.store.links(task['id'])}
            links += [{**m, 'task': task['id'], 'at': time.time()} for m in mails if m['mid'] not in known]
        self.store.commit(tasks=tasks, events=events, expect=expect, links=links)
        return self._reply(batch, kind, [t['id'] for t in tasks] or [tid for tid, _, _ in extra_events])

    def _reply(self, batch, kind='', ids=()):
        export = self.export(message=_message(kind, ids)) if batch else self._export_view()
        return {'verify': self.verify(), 'export': export, 'ids': list(ids)}

    def _open_task(self, task_id, rev):
        task = self._task(task_id)
        if self.store.revs.get(task['id']) != rev:
            raise AppError('TASK_CONFLICT')
        return task, copy.deepcopy(task)

    def _next_order(self):
        return max((t.get('order', 0) for t in self.store.tasks.values()), default=-1) + 1

    def create(self, fields, date=''):
        self._writable()
        fields = dict(fields or {})
        fields.setdefault('owner', OWNER_DEFAULT)
        fields.setdefault('status', '未开始')
        clean = check_fields(fields, EDITABLE)
        on_day = day(date) or self.today()
        task = {**blank_task(max(self.store.tasks, default=0) + 1), **clean, 'order': self._next_order(), 'cells': {}}
        if not task['content'].strip():
            raise AppError('TASK_INVALID')
        if 'updated' not in fields:
            task['updated'] = on_day
        check_blocked(task)
        return self._write('created', [task], None, on_day=on_day)

    def update(self, task_id, rev, fields, note='', date=''):
        """Edit fields of a task (status only among the unfinished ones). The history column
        is append-only: rewriting it is a correction that needs a reason (kept with the old text)."""
        self._writable()
        old, task = self._open_task(task_id, rev)
        if not isinstance(fields, dict):
            raise AppError('TASK_INVALID')
        # Only what the user changed: an untouched value (e.g. a date kept as text) is not re-checked.
        fields = {k: v for k, v in fields.items() if not (k in FIELDS and _shown(old.get(k)) == v)}
        note = text(note).strip()
        if 'history' in fields:
            if not note:
                raise AppError('TASK_REASON_REQUIRED')
            task['history'] = text(fields.pop('history'))
        if old['status'] not in OPEN_STATUSES and 'status' in fields and fields['status'] != old['status']:
            raise AppError('TASK_STATUS_INVALID')  # Archived tasks change status by 重新打开 only.
        clean = check_fields(fields, EDITABLE)
        task.update(clean)
        on_day = day(date) or self.today()
        if 'updated' not in fields and any(task[f] != old[f] for f in FIELDS):
            task['updated'] = on_day
        if task['status'] == '受阻' and old['status'] != '受阻':
            check_blocked(task)
        return self._write('edited', [task], rev, note=note, on_day=on_day)

    def progress(self, task_id, rev, text_value, date='', latest=True, fields=None):
        """记录进展: one dated line after the history; optionally the same text as 最新进展, and
        status / 下一步 / 阻塞与依赖 at the same time."""
        self._writable()
        old, task = self._open_task(task_id, rev)
        line = text(text_value, required=True).strip()
        on_day = day(date) or self.today()
        task.update(check_fields(dict(fields or {}), ('status', 'next', 'blockers')) if old['status'] in OPEN_STATUSES
                    else _no_fields(fields))
        task['history'] = append_history(old['history'], on_day, line + self._source())
        if latest:
            task['latest'] = f'{on_day}：{line}'
        task['updated'] = on_day
        if task['status'] == '受阻' and old['status'] != '受阻':
            check_blocked(task)
        return self._write('progress', [task], rev, on_day=on_day)

    def complete(self, task_id, rev, date='', note=''):
        """标记完成: the end date is the given date, else today in Riyadh (confirmed 2026-10-15)."""
        self._writable()
        old, task = self._open_task(task_id, rev)
        if old['status'] not in OPEN_STATUSES:
            raise AppError('TASK_STATUS_INVALID')
        end = day(date) or self.today()
        note = text(note).strip()
        task.update(status=DONE, end=end, updated=end, order=self._next_order(),
                    history=append_history(old['history'], end, '已完成' + (f'：{note}' if note else '。') + self._source()))
        return self._write('completed', [task], rev, note=note, on_day=end)

    def cancel(self, task_id, rev, reason, date=''):
        """取消任务: needs a reason; the end date is set only when one is given (an old end date stays)."""
        self._writable()
        old, task = self._open_task(task_id, rev)
        if old['status'] not in OPEN_STATUSES:
            raise AppError('TASK_STATUS_INVALID')
        reason = text(reason).strip()
        if not reason:
            raise AppError('TASK_REASON_REQUIRED')
        on_day = day(date) or self.today()
        task.update(status=CANCELED, updated=on_day, order=self._next_order(),
                    history=append_history(old['history'], on_day, f'已取消：{reason}' + self._source()))
        if date:
            task['end'] = on_day
        return self._write('canceled', [task], rev, note=reason, on_day=on_day)

    def reopen(self, task_id, rev, status='进行中', reason=''):
        self._writable()
        old, task = self._open_task(task_id, rev)
        if old['status'] in OPEN_STATUSES or status not in OPEN_STATUSES:
            raise AppError('TASK_STATUS_INVALID')
        reason = text(reason).strip()
        on_day = self.today()
        task.update(status=status, end='', updated=on_day, order=self._next_order(),
                    history=append_history(old['history'], on_day, '重新打开' + (f'：{reason}' if reason else '。')))
        check_blocked(task)
        return self._write('reopened', [task], rev, note=reason, on_day=on_day)

    def link(self, task_id, mail):
        """关联邮件 (by hand): the mail's id, Message-ID, subject and date as they were."""
        self._writable()
        task = self._task(task_id)
        mail = {k: str(mail.get(k) or '')[:500] for k in ('mid', 'messageId', 'subject', 'date', 'source')}
        if not mail['mid']:
            raise AppError('TASK_INVALID')
        if any(l['mid'] == mail['mid'] for l in self.store.links(task['id'])):
            return self._reply(None)
        batch = uuid.uuid4().hex[:12]
        self.store.commit(links=[{**mail, 'task': task['id'], 'at': time.time()}],
                          events=[(task['id'], 'confirmed', {'kind': 'linked', 'date': self.today(), 'actor': 'user', 'batch': batch,
                                                             'changes': {}, 'note': mail['subject'][:200]})])
        return self._reply(batch, 'linked', [task['id']])

    def unlink(self, task_id, link_id):
        self._writable()
        task = self._task(task_id)
        found = next((l for l in self.store.links(task['id']) if l['id'] == link_id), None)
        if found is None:
            raise AppError('TASK_NOT_FOUND')
        batch = uuid.uuid4().hex[:12]
        self.store.commit(unlink=[link_id], events=[(task['id'], 'confirmed', {'kind': 'unlinked', 'date': self.today(), 'actor': 'user',
                                                                               'batch': batch, 'changes': {}, 'note': found.get('subject', '')[:200]})])
        return self._reply(batch, 'unlinked', [task['id']])

    def suggest(self, action, preview, basis, note=''):
        """An AI change kept for review (待确认): nothing changes until it is accepted."""
        self.store.commit(events=[(action['id'], 'pending', {
            'kind': 'suggested', 'date': self.today(), 'actor': 'ai', 'action': action, 'changes': preview.get('changes') or {},
            'mails': action.get('mails') or [], 'basis': text(basis).strip()[:300], 'note': text(note).strip()[:500]})])

    def review(self, seq, accept):
        """Accept or reject a change waiting for review: one read from an Excel file edited
        outside hongyan, or an AI suggestion (which then runs like a confirmed plan)."""
        self._writable()
        found = self.store.events_by_seq([seq])
        if not found or found[0][1] != 'pending':
            raise AppError('TASK_NOT_FOUND')
        seq, _, event = found[0]
        if not accept:
            self.store.commit(reviews=[(seq, 'rejected')])
            return self._reply(None)
        if event.get('kind') == 'suggested':
            from coremail_desktop.tasks.assistant import check_actions
            result = self.run_plan(check_actions([event['action']]), event.get('basis') or '')
            if result['failed']:
                raise AppError(result['failed'][0]['code'])
            self.store.commit(reviews=[(seq, 'accepted')])
            return result
        current = self.store.tasks.get(event['task'])
        if event.get('create'):
            if current is not None:
                raise AppError('TASK_CONFLICT')
            task = {**event['create'], 'order': self._next_order()}
        else:
            if current is None:
                raise AppError('TASK_NOT_FOUND')
            task = copy.deepcopy(current)
            for field, (before, after) in event['changes'].items():
                if task.get(field) != before:
                    raise AppError('TASK_CONFLICT')  # Changed here since: decide on the newer value by hand.
                task[field] = after
                task['cells'].pop(field, None)
            if sheet_of(task['status']) != sheet_of(current['status']):
                task['order'] = self._next_order()
        self.store.commit(tasks=[task], reviews=[(seq, 'confirmed')],
                          expect={task['id']: self.store.revs[task['id']]} if current else None)
        return self._reply(uuid.uuid4().hex[:12], 'external', [task['id']])

    # 自然语言操作 (tasks/assistant.py): a plan is previewed, then run after the user confirms.
    def _source(self):
        from coremail_desktop.tasks.assistant import source_note
        return source_note((getattr(self._ctx, 'extra', None) or {}).get('mails'))

    def undo(self, seq):
        """撤销这次修改: only the task's latest change, and only while the changed fields still
        hold what it wrote. A new task cannot be undone (its number stays); the undo is recorded.
        A line it added to the history column stays there, followed by a line saying it was
        undone; the 演进图 hides both (story.build)."""
        self._writable()
        found = self.store.events_by_seq([seq])
        if not found or found[0][1] != 'confirmed' or found[0][2].get('kind') not in UNDOABLE:
            raise AppError('TASK_UNDO_UNAVAILABLE')
        seq, _, event = found[0]
        latest = [s for s, review, e in self.store.events(event['task']) if review == 'confirmed' and e.get('changes')]
        task = self.store.tasks.get(event['task'])
        if task is None or not latest or latest[-1] != seq:
            raise AppError('TASK_UNDO_UNAVAILABLE')
        new = copy.deepcopy(task)
        for field, (before, after) in event['changes'].items():
            if task.get(field) != after:
                raise AppError('TASK_UNDO_UNAVAILABLE')
            new[field] = before
        from coremail_desktop.tasks.story import added_line
        added = added_line(event['changes'].get('history'))
        if added:
            gist = (LINE_DATE.sub('', added) or added)[:60]
            new['history'] = append_history(task['history'], self.today(), f'撤销{KIND_TEXT.get(event["kind"], "修改")}：{gist}')
        if sheet_of(new['status']) != sheet_of(task['status']):
            new['order'] = self._next_order()
        self._ctx.extra = {'undoes': seq}
        try:
            return self._write('undone', [new], self.store.revs[task['id']], note=f'撤销{KIND_TEXT.get(event["kind"], "修改")}（{event.get("date", "")}）')
        finally:
            self._ctx.extra = None

    def _dispatch(self, action):
        op, rev = action['op'], None
        if op != 'create':
            rev = self.store.revs.get(self._task(action['id'])['id'])
        if op == 'progress':
            return self.progress(action['id'], rev, action['text'], action['date'], action['latest'], action['fields'])
        if op == 'update':
            return self.update(action['id'], rev, action['fields'], action['note'])
        if op == 'complete':
            return self.complete(action['id'], rev, action['date'], action['note'])
        if op == 'cancel':
            return self.cancel(action['id'], rev, action['reason'], action['date'])
        if op == 'reopen':
            return self.reopen(action['id'], rev, action['status'], action['reason'])
        return self.create(action['fields'])

    def preview(self, actions):
        """Each action checked by its operation without writing: its changes, or the error code."""
        from coremail_desktop.tasks.assistant import resolve_refs
        self._writable()
        out, next_id = [], max(self.store.tasks, default=0) + 1
        creates = [a for a in actions if a['op'] == 'create']
        refs = {a['ref']: next_id + i for i, a in enumerate(creates) if a.get('ref')}  # run_plan creates new tasks first.
        self._ctx.dry = True
        try:
            for action in actions:
                item = {'op': action['op'], 'id': action.get('id')}
                self._ctx.extra = {'mails': action['mails']} if action.get('mails') else None
                try:
                    resolved = resolve_refs(action, refs)
                    if resolved is None:
                        raise AppError('TASK_PLAN_REF')
                    found = self._dispatch(resolved)
                    item.update(found)
                    if action['op'] == 'create':
                        item['id'] = next_id  # Numbers are given when it runs; several new tasks count on.
                        next_id += 1
                    if action['op'] != 'create':
                        task = self.store.tasks[action['id']]
                        item.update(system=task['system'], content=task['content'], status=task['status'])
                except AppError as error:
                    item['error'] = error.code
                out.append(item)
        finally:
            self._ctx.dry, self._ctx.extra = False, None
        return out

    def run_plan(self, actions, basis):
        """Run a confirmed plan, new tasks first (so #N1 in the others gets their number), then
        the rest in order; each operation is checked, saved, exported and verified like a
        manual one, and records the sentence it came from."""
        from coremail_desktop.tasks.assistant import resolve_refs
        self._writable()
        done, failed, last, refs = [], [], None, {}
        self._ctx.actor, self._ctx.basis = 'ai', text(basis).strip()[:300]
        try:
            for action in sorted(actions, key=lambda a: a['op'] != 'create'):
                try:
                    self._ctx.extra = {'mails': action['mails']} if action.get('mails') else None
                    resolved = resolve_refs(action, refs)
                    if resolved is None:
                        raise AppError('TASK_PLAN_REF')
                    last = self._dispatch(resolved)
                    if action.get('ref') and last.get('ids'):
                        refs[action['ref']] = last['ids'][0]
                    done.append({'op': action['op'], 'id': (last.get('ids') or [action.get('id')])[0]})
                except AppError as error:
                    failed.append({'op': action['op'], 'id': action.get('id'), 'code': error.code})
        finally:
            self._ctx.actor, self._ctx.basis, self._ctx.extra = 'user', '', None
        export = {**self._export_view(), 'message': _plan_message(done)}  # Each operation has exported already.
        return {'done': done, 'failed': failed, 'verify': self.verify(), 'export': export, 'ids': [d['id'] for d in done]}

    def _writable(self):
        if self._settings()['mode'] == 'off':
            raise AppError('TASK_NOT_IMPORTED')

    # Verification --------------------------------------------------------
    def verify(self):
        """Read every task back from the file and compare it with what was meant to be saved;
        then the full list of unfinished tasks and the counts (the rule after each change)."""
        disk = self.store.on_disk()
        memory = self.store.tasks
        if set(disk) != set(memory) or any(disk[i][1] != memory[i] or disk[i][0] != self.store.revs[i] for i in disk):
            raise AppError('TASK_VERIFY_FAILED')
        if any(t['status'] not in STATUSES for t in memory.values()):
            raise AppError('TASK_VERIFY_FAILED')
        unfinished = sorted((t for t in memory.values() if t['status'] in OPEN_STATUSES), key=lambda t: t['id'])
        return {'open': [{'id': t['id'], 'system': t['system'], 'content': t['content'], 'status': t['status']} for t in unfinished],
                'counts': self.counts(), 'at': self.clock().astimezone().strftime('%Y-%m-%d %H:%M:%S')}

    # Import, export, restore --------------------------------------------
    def set_folder(self, folder):
        settings = self._settings()
        folder = str(folder or '').strip()
        if settings['mode'] == 'live' and folder != settings['folder']:
            raise AppError('TASK_FOLDER_LIVE')
        if folder and (len(folder) > 500 or not Path(folder).is_dir()):
            raise AppError('TASK_FOLDER_INVALID')
        self.store.set('settings', {**settings, 'folder': folder})
        return self.status()

    def _books(self):
        folder = self._settings()['folder']
        if not folder or not Path(folder).is_dir():
            raise AppError('TASK_FOLDER_INVALID')
        books = {}
        for name in (transfer.OPEN_FILE, transfer.DONE_FILE):
            path = Path(folder) / name
            if not path.is_file():
                raise AppError('TASK_FOLDER_INVALID')
            books[name] = path.read_bytes()
        return Path(folder), books

    def import_preview(self):
        """The report on the folder's workbooks and the round-trip check; nothing is stored."""
        _, books = self._books()
        parsed = transfer.parse_books(books)
        _, diffs = transfer.roundtrip(parsed['tasks'], parsed['template'])
        return {'report': parsed['report'], 'differences': diffs[:50], 'differenceCount': len(diffs),
                'ok': not parsed['report']['blocking'] and not diffs, 'mode': self._settings()['mode'],
                'files': {name: len(data) for name, data in books.items()}}

    def import_books(self, mode):
        """演练 or 正式 import of the folder's workbooks; replaces what the store holds (a copy
        of the store is kept first). Switching to 正式 exports to the folder at once."""
        if mode not in ('trial', 'live'):
            raise AppError('TASK_INVALID')
        folder, books = self._books()
        parsed = transfer.parse_books(books)
        report = parsed['report']
        if report['duplicates']:
            raise AppError('TASK_IMPORT_DUPLICATE')
        if report['blocking']:
            raise AppError('TASK_IMPORT_INVALID')
        _, diffs = transfer.roundtrip(parsed['tasks'], parsed['template'])
        if diffs:
            raise AppError('TASK_ROUNDTRIP_FAILED')
        if self.store.tasks:
            self.backup('import')
        today = self.today()
        hashes = {name: transfer.sha(data) for name, data in books.items()}
        existing = folder / transfer.JSON_FILE
        if existing.is_file():
            hashes[transfer.JSON_FILE] = transfer.sha(existing.read_bytes())
        events = [(t['id'], 'confirmed', {'kind': 'imported', 'date': today, 'actor': 'import', 'changes': {},
                                          'note': f'{transfer.OPEN_FILE if t["status"] in OPEN_STATUSES else transfer.DONE_FILE}'}) for t in parsed['tasks']]
        summary = {'at': datetime.now().strftime('%Y-%m-%d %H:%M'), 'mode': mode, 'counts': {s: v['count'] for s, v in report['sheets'].items()},
                   'total': report['total']}
        self.store.commit(tasks=parsed['tasks'], events=events, replace=True,
                          kv={'template': parsed['template'], 'settings': {**self._settings(), 'mode': mode},
                              'import': {'summary': summary, 'report': report, 'hashes': hashes},
                              'export': {'files': hashes if mode == 'live' else {}, 'state': 'ok'}})
        return {'status': self.status(), 'export': self.export(message='切换到鸿雁任务中心' if mode == 'live' else ''),
                'verify': self.verify(), 'report': report}

    def stop_live(self):
        """回滚: stop writing to the task folder (the store is kept; Excel there is up to date)."""
        settings = self._settings()
        if settings['mode'] == 'live':
            self.store.set('settings', {**settings, 'mode': 'trial'})
        return self.status()

    def export(self, message=''):
        """Write the workbooks and 任务数据.json: to the task folder when 正式, else to the trial
        folder. Never over a file changed outside hongyan, nor while Excel has one open."""
        settings = self._settings()
        state = self.store.get('export') or {}
        if settings['mode'] == 'off':
            return self._export_view()
        live = settings['mode'] == 'live'
        folder = Path(settings['folder']) if live else self.dir / 'export-trial'
        tasks = list(self.store.tasks.values())
        try:
            books, diffs = transfer.roundtrip(tasks, self.store.get('template'))
            data = transfer.build_json(tasks, self.store.events(), self.store.links())
            again, _, _ = transfer.parse_json(data)
            if diffs or transfer.diff_tasks(tasks, again):
                raise AppError('TASK_ROUNDTRIP_FAILED')
            files = {**books, transfer.JSON_FILE: data}
            folder.mkdir(parents=True, exist_ok=True)
            known = state.get('files') or {}
            if live:
                if transfer.locked(folder, files):
                    return self._save_export(state, 'locked', folder, message)
                changed = [name for name in files if (folder / name).is_file()
                           and transfer.sha((folder / name).read_bytes()) not in (known.get(name), transfer.sha(files[name]))]
                if changed:
                    return self._save_export(state, 'external', folder, message, external=changed)
            todo = {name: data for name, data in files.items()
                    if not (folder / name).is_file() or transfer.sha((folder / name).read_bytes()) != transfer.sha(data)}
            if todo:
                transfer.write_files(folder, todo)
            state['files'] = {name: transfer.sha(data) for name, data in files.items()} if live else {}
            if live and todo:  # What the folder now says: the base for telling outside edits from ours.
                self.store.set('base', {str(t['id']): {f: t[f] for f in FIELDS} for t in tasks})
            return self._save_export(state, 'ok', folder, message if todo else '', written=sorted(todo))
        except AppError as error:
            self._log_export(error)
            outcome = {'TASK_EXPORT_LOCKED': 'locked', 'TASK_EXPORT_IN_USE': 'in_use', 'TASK_EXPORT_DENIED': 'denied'}.get(error.code, 'failed')
            return self._save_export(state, outcome, folder, message, code=error.code)

    def _log_export(self, error):
        """One line per failed export: when, the code and which step (no task content)."""
        try:
            with open(self.dir / 'export-errors.log', 'a', encoding='utf-8') as stream:
                stream.write(f'{datetime.now():%Y-%m-%d %H:%M:%S} {error.code} {error.category}\n')
        except OSError:
            pass

    def _save_export(self, state, outcome, folder, message, external=None, written=None, code=None):
        state = {**state, 'state': outcome, 'folder': str(folder), 'external': external or [],
                 'message': message or ('' if outcome == 'ok' else state.get('message', '')), 'code': code}
        if outcome == 'ok':
            state['at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.store.set('export', state)
        return {**self._export_view(), 'written': written or [], 'code': code}

    def resolve_external(self, action):
        """The task folder changed outside hongyan: 'import' reads the differences as changes
        to review (then exports); 'overwrite' keeps a copy of those files and exports."""
        settings = self._settings()
        if settings['mode'] != 'live':
            raise AppError('TASK_INVALID')
        folder = Path(settings['folder'])
        state = self.store.get('export') or {}
        displaced = self.dir / 'displaced' / datetime.now().strftime('%Y%m%d-%H%M%S')
        displaced.mkdir(parents=True, exist_ok=True)
        for name in NAMES:
            if (folder / name).is_file():
                (displaced / name).write_bytes((folder / name).read_bytes())
        found = 0
        if action == 'import':
            found = self._read_external(folder)
        elif action != 'overwrite':
            raise AppError('TASK_INVALID')
        state['files'] = {name: transfer.sha((folder / name).read_bytes()) for name in NAMES if (folder / name).is_file()}
        self.store.set('export', state)
        return {'pending': found, 'export': self.export(message='处理任务文件夹中的外部修改'), 'status': self.status()}

    def _read_external(self, folder):
        books = {name: (folder / name).read_bytes() for name in (transfer.OPEN_FILE, transfer.DONE_FILE)}
        parsed = transfer.parse_books(books)
        if parsed['report']['blocking']:
            raise AppError('TASK_IMPORT_INVALID')
        events = []
        base = self.store.get('base') or {}
        for task in parsed['tasks']:
            current = self.store.tasks.get(task['id'])
            known = base.get(str(task['id']))
            if current is not None and known is not None:  # Only what was changed outside since our last export.
                changes = {f: [current[f], task[f]] for f in FIELDS if task[f] != known[f] and task[f] != current[f]}
                if changes:
                    events.append((task['id'], 'pending', {'kind': 'external', 'date': self.today(), 'actor': 'excel', 'note': '', 'changes': changes}))
                continue
            if current is None:
                events.append((task['id'], 'pending', {'kind': 'external', 'date': self.today(), 'actor': 'excel', 'note': '',
                                                       'changes': {f: ['', task[f]] for f in FIELDS if task[f] not in ('', None)},
                                                       'create': task}))
                continue
            changes = {f: [current[f], task[f]] for f in FIELDS if current[f] != task[f]}
            if changes:
                events.append((task['id'], 'pending', {'kind': 'external', 'date': self.today(), 'actor': 'excel', 'note': '', 'changes': changes}))
        if events:
            self.store.commit(events=events)
        return len(events)

    def compare(self):
        """演练对照: where the folder's workbooks (still written elsewhere) differ from the store."""
        _, books = self._books()
        parsed = transfer.parse_books(books)
        diffs = transfer.diff_tasks(list(self.store.tasks.values()), parsed['tasks'])
        return {'differences': [d for d in diffs if not d['field'].startswith('order')][:200], 'count': len(diffs),
                'folderCounts': {s: v['count'] for s, v in parsed['report']['sheets'].items()}, 'counts': self.counts()}

    def restore(self, mode='live'):
        """Full restore from the folder's 任务数据.json (tasks and timeline), e.g. on a new PC;
        the layout comes from the folder's workbooks."""
        if mode not in ('trial', 'live'):
            raise AppError('TASK_INVALID')
        folder, books = self._books()
        path = folder / transfer.JSON_FILE
        if not path.is_file():
            raise AppError('TASK_FOLDER_INVALID')
        raw = path.read_bytes()
        tasks, events, links = transfer.parse_json(raw)
        template = transfer.parse_books(books)['template']
        _, diffs = transfer.roundtrip(tasks, template)
        if diffs:
            raise AppError('TASK_ROUNDTRIP_FAILED')
        if self.store.tasks:
            self.backup('restore')
        hashes = {name: transfer.sha(data) for name, data in books.items()}
        hashes[transfer.JSON_FILE] = transfer.sha(raw)
        self.store.commit(tasks=tasks, events=events, links=links, replace=True,
                          kv={'template': template, 'settings': {**self._settings(), 'mode': mode},
                              'export': {'files': hashes if mode == 'live' else {}, 'state': 'ok'}})
        return {'status': self.status(), 'export': self.export(message='从任务数据.json 恢复'), 'verify': self.verify()}

    # Backups -------------------------------------------------------------
    def backup(self, why):
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.store.backup(self.dir / 'backup' / f'tasks-{why}-{stamp}.db')

    def _daily_backup(self):
        if not self.store.tasks:
            return
        folder = self.dir / 'backup'
        today = datetime.now().strftime('%Y%m%d')
        if any(folder.glob(f'tasks-daily-{today}-*.db')):
            return
        try:
            self.backup('daily')
            daily = sorted(folder.glob('tasks-daily-*.db'))
            for old in daily[:-BACKUPS]:
                old.unlink(missing_ok=True)
                old.with_suffix('.key').unlink(missing_ok=True)
        except OSError:
            pass  # A missed daily copy must not keep the tasks from opening.


def _shown(value):
    if isinstance(value, dict):
        return value.get('text', '')
    return value if value is not None else ''


def _no_fields(fields):
    if fields:
        raise AppError('TASK_STATUS_INVALID')
    return {}


def _plan_message(done):
    verbs = {'progress': ('更新', '进展'), 'update': ('更新', ''), 'complete': ('归档', '完成'), 'cancel': ('取消', ''),
             'reopen': ('重新打开', ''), 'create': ('新增', '任务')}
    parts = []
    for item in done[:5]:
        verb, suffix = verbs[item['op']]
        parts.append(f'{verb}{item["id"]}{suffix}')
    return '、'.join(parts) + '并核验任务清单（鸿雁）' if parts else ''


def _message(kind, ids):
    """A suggested git commit message for the batch (the user commits)."""
    if not ids:
        return ''
    verbs = {'created': '新增', 'edited': '更新', 'progress': '更新', 'completed': '归档', 'canceled': '取消', 'reopened': '重新打开',
             'linked': '关联邮件到', 'unlinked': '取消关联邮件', 'external': '采纳外部修改', 'imported': '导入'}
    suffix = {'progress': '进展', 'completed': '完成', 'created': '任务'}.get(kind, '')
    return f'{verbs.get(kind, "更新")}{"和".join(str(i) for i in ids[:5])}{suffix}并核验任务清单（鸿雁）'
