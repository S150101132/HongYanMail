"""Between the task store and the two workbooks plus 任务数据.json.

parse_books reads the workbooks into tasks, an import report (numbers and task ids only,
never cell text) and the layout to export with; build_books writes tasks back in that
layout. Lossless means: parse(source) and parse(build(parse(source))) hold the same tasks,
in the same sheets and order, cell by cell (diff_tasks), with the same layout (diff_layout).
"""
import hashlib
import json
import math
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

from coremail_desktop.errors import AppError
from coremail_desktop.tasks import xlsx
from coremail_desktop.tasks.rules import (CANCELED, CANCELED_SHEET, DATE_FIELDS, DONE, DONE_SHEET, FIELDS, HEADERS, LABEL,
                                          OPEN_SHEET, OPEN_STATUSES, STATUSES, is_day, sheet_of)

OPEN_FILE, DONE_FILE, JSON_FILE = '未完成任务.xlsx', '已完成任务.xlsx', '任务数据.json'
BOOKS = ((OPEN_FILE, (OPEN_SHEET,)), (DONE_FILE, (DONE_SHEET, CANCELED_SHEET)))
SHEET_STATUSES = {OPEN_SHEET: OPEN_STATUSES, DONE_SHEET: (DONE,), CANCELED_SHEET: (CANCELED,)}
HEADER_ROW, FIRST_ROW = 4, 5
NOTE = '由鸿雁任务中心导出：请在鸿雁中修改，不要直接编辑本文件。'
FORMAT = 'hongyan-tasks/1'
NUMBER = re.compile(r'^-?\d+(\.\d+)?([Ee][+-]?\d+)?$')
FIELD_OF = {v: k for k, v in LABEL.items()}
RETRY_WAITS = (0.2, 0.5, 1.0)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def natural(field):
    return 'n' if field == 'id' or field in DATE_FIELDS else 's'


def _date(serial, date1904):
    base = date(1904, 1, 1) if date1904 else date(1899, 12, 30)
    return (base + timedelta(days=int(float(serial)))).isoformat()


def _serial(day):
    return str((date.fromisoformat(day) - date(1899, 12, 30)).days)


# Reading ----------------------------------------------------------------
def parse_books(books):
    """books: {file name: bytes} -> {'tasks', 'template', 'report'}. Raises on a layout that
    is not the 15-column task workbook (no guessing at columns)."""
    report = {'sheets': {}, 'total': 0, 'duplicates': [], 'mismatch': [], 'badRows': [], 'placeholders': [],
              'empty': {label: 0 for label in HEADERS}, 'isoDates': 0, 'textDates': [], 'typed': 0, 'spaces': 0,
              'dateOrder': [], 'noEnd': {DONE: [], CANCELED: 0}}
    tasks, template, seen, order = [], {'sheets': {}}, set(), 0
    for name, sheet_names in BOOKS:
        if name not in books:
            raise AppError('TASK_FOLDER_INVALID')
        book = xlsx.read(books[name])
        if [s['name'] for s in book['sheets']] != list(sheet_names):
            raise AppError('TASK_IMPORT_LAYOUT')
        template.setdefault('styles', {})[name] = book['styles'].decode('utf-8') if book['styles'] else None
        template.setdefault('themes', {})[name] = book['theme'].decode('utf-8') if book['theme'] else None
        for sheet in book['sheets']:
            header = sheet['rows'].get(HEADER_ROW, {'cells': {}})['cells']
            if tuple(header.get(i, ('', '', ''))[1] for i in range(len(HEADERS))) != HEADERS:
                raise AppError('TASK_IMPORT_LAYOUT')
            data_rows = sorted(r for r in sheet['rows'] if r >= FIRST_ROW)
            styles = {}
            for r in data_rows:  # The data style of each column: the first row that has one.
                for col, (_, _, style) in sheet['rows'][r]['cells'].items():
                    styles.setdefault(col, style)
            template['sheets'][sheet['name']] = {
                'layout': sheet['layout'], 'styles': [styles.get(i, '0') for i in range(len(HEADERS))],
                'top': {str(r): {'attrs': row['attrs'], 'cells': {str(c): list(v) for c, v in row['cells'].items()}}
                        for r, row in sheet['rows'].items() if r <= HEADER_ROW}}
            info = report['sheets'][sheet['name']] = {'count': 0, 'minId': None, 'maxId': None}
            for r in data_rows:
                cells = sheet['rows'][r]['cells']
                if not any(v[1] for v in cells.values()):
                    continue
                task = _task(cells, book['date1904'], report)
                if task is None:
                    report['badRows'].append(f'{sheet["name"]}!{r}')
                    continue
                if not any(task[f] for f in FIELDS if f != 'id'):
                    report['placeholders'].append(task['id'])
                    continue
                if task['id'] in seen:
                    report['duplicates'].append(task['id'])
                    continue
                seen.add(task['id'])
                if task['status'] not in SHEET_STATUSES[sheet['name']]:
                    report['mismatch'].append(task['id'])
                task['order'] = order
                order += 1
                _note(task, report)
                tasks.append(task)
                info['count'] += 1
                info['minId'] = min(info['minId'] or task['id'], task['id'])
                info['maxId'] = max(info['maxId'] or task['id'], task['id'])
    report['total'] = len(tasks)
    report['blocking'] = [k for k in ('duplicates', 'mismatch', 'badRows') if report[k]]
    return {'tasks': tasks, 'template': template, 'report': report}


def _task(cells, date1904, report):
    task, marks = {}, {}
    for i, field in enumerate(FIELDS):
        kind, value, _ = cells.get(i, ('', '', ''))
        if not kind:
            task[field] = '' if field != 'id' else None
            continue
        if field == 'id':
            if kind in ('n', 's') and NUMBER.match(value.strip()) and float(value) == int(float(value)) and int(float(value)) > 0:
                task['id'] = int(float(value))
                if kind != 'n' or value.strip() != str(task['id']):
                    marks['id'] = [kind, value]
            else:
                return None
        elif field in DATE_FIELDS:
            if kind == 'n' and NUMBER.match(value) and float(value) == int(float(value)):
                task[field] = _date(value, date1904)
            elif kind == 'd' and is_day(value[:10]) and value[10:] in ('', 'T00:00:00', 'T00:00:00Z', 'T00:00:00.000Z'):
                task[field] = value[:10]
                report['isoDates'] += 1
            else:
                task[field] = {'text': value}
                marks[field] = kind
        else:
            task[field] = value
            if kind != 's':
                marks[field] = kind
    if task.get('id') is None:
        return None
    task['cells'] = marks
    report['typed'] += sum(1 for f, k in marks.items() if f not in DATE_FIELDS)
    return task


def _note(task, report):
    for field, label in LABEL.items():
        value = task[field]
        if value in ('', None):
            report['empty'][label] += 1
        elif isinstance(value, dict):
            report['textDates'].append(task['id'])
        elif isinstance(value, str) and value != value.strip():
            report['spaces'] += 1
    start = task['start'] if is_day(task['start']) else None
    if start and any(is_day(task[f]) and task[f] < start for f in ('due', 'end')):
        report['dateOrder'].append(task['id'])
    if task['status'] == DONE and not task['end']:
        report['noEnd'][DONE].append(task['id'])
    elif task['status'] == CANCELED and not task['end']:
        report['noEnd'][CANCELED] += 1


# Writing ----------------------------------------------------------------
def _units(text):
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def _height(values, widths, base):
    lines = 1
    for value, width in zip(values, widths):
        if value and width:
            lines = max(lines, sum(max(1, math.ceil(_units(line) / max(1.0, width * 0.95))) for line in str(value).split('\n')))
    return str(min(409, max(base, round(lines * 15 + 3))))


def _cell(task, field, style):
    value, mark = task.get(field), (task.get('cells') or {}).get(field)
    if field == 'id':
        return (mark[0], mark[1], style) if isinstance(mark, list) else ('n', str(value), style)
    if value in ('', None):
        return ('', '', style)
    if isinstance(value, dict):
        kind = mark or 's'
        return (kind if kind != 'n' or NUMBER.match(value['text']) else 's', value['text'], style)
    if field in DATE_FIELDS:
        return ('n', _serial(value), style)
    kind = mark or 's'
    if kind == 'n' and not NUMBER.match(value) or kind == 'b' and value not in ('0', '1'):
        kind = 's'  # Edited since import: the kept cell type no longer fits the value.
    return (kind, value, style)


def build_books(tasks, template, note=NOTE):
    """{file name: bytes} of the two workbooks; note replaces the line under the title."""
    by_sheet = {}
    for task in sorted(tasks, key=lambda t: (t.get('order', 0), t['id'])):
        by_sheet.setdefault(sheet_of(task['status']), []).append(task)
    out = {}
    for name, sheet_names in BOOKS:
        sheets = []
        for sheet_name in sheet_names:
            tpl = template['sheets'][sheet_name]
            layout, styles = tpl['layout'], tpl['styles']
            widths = [0.0] * len(HEADERS)
            for col in layout.get('cols') or []:
                for i in range(int(col.get('min', 1)) - 1, min(int(col.get('max', 1)), len(HEADERS))):
                    widths[i] = float(col.get('width') or 0)
            rows = {}
            for r, row in tpl['top'].items():
                cells = {int(c): tuple(v) for c, v in row['cells'].items()}
                if int(r) == 2 and note is not None and 0 in cells:
                    cells[0] = ('s', note, cells[0][2])
                rows[int(r)] = {'attrs': row['attrs'], 'cells': cells}
            base = float(layout.get('rowHeight') or 15)
            items = by_sheet.get(sheet_name, [])
            for offset, task in enumerate(items):
                cells = {i: _cell(task, field, styles[i]) for i, field in enumerate(FIELDS)}
                height = _height([v[1] for v in cells.values()], widths, base)
                rows[FIRST_ROW + offset] = {'attrs': {'ht': height, 'customHeight': '1'}, 'cells': cells}
            if not items:  # An Excel table needs a body row: an empty one, read back as no task.
                rows[FIRST_ROW] = {'attrs': {}, 'cells': {i: ('', '', styles[i]) for i in range(len(HEADERS))}}
            sheets.append({'name': sheet_name, 'layout': layout, 'rows': rows, 'first': FIRST_ROW,
                           'last': FIRST_ROW + max(len(items), 1) - 1})
        styles = template['styles'][name]
        theme = template['themes'][name]
        out[name] = xlsx.write(sheets, styles.encode('utf-8'), theme.encode('utf-8') if theme else None, HEADER_ROW)
    return out


# Comparing --------------------------------------------------------------
def diff_tasks(left, right):
    """[{'id', 'field'}] where two task lists differ: a value, the cell type kept for it,
    its sheet, or its place in the sheet. Field names only, never values."""
    a, b = {t['id']: t for t in left}, {t['id']: t for t in right}
    found = [{'id': i, 'field': 'missing'} for i in sorted(set(a) - set(b))]
    found += [{'id': i, 'field': 'extra'} for i in sorted(set(b) - set(a))]
    for task_id in sorted(set(a) & set(b)):
        x, y = a[task_id], b[task_id]
        found += [{'id': task_id, 'field': f} for f in FIELDS if x.get(f) != y.get(f)]
        if (x.get('cells') or {}) != (y.get('cells') or {}):
            found.append({'id': task_id, 'field': 'cells'})
    rank = lambda tasks: {sheet: [t['id'] for t in sorted((t for t in tasks if sheet_of(t['status']) == sheet), key=lambda t: (t.get('order', 0), t['id']))]
                          for sheet in SHEET_STATUSES}
    ra, rb = rank(left), rank(right)
    for sheet in SHEET_STATUSES:
        if ra[sheet] != rb[sheet] and not found:
            found.append({'id': None, 'field': f'order:{sheet}'})
    return found


def _layout_key(tpl):
    layout = tpl['layout']
    top = tpl['top']
    return {'title': top.get('1', {}).get('cells', {}).get('0', ['', ''])[1],
            'header': [top.get(str(HEADER_ROW), {}).get('cells', {}).get(str(i), ['', ''])[1] for i in range(len(HEADERS))],
            'cols': [(c.get('min'), c.get('max'), c.get('width')) for c in layout.get('cols') or []],
            'pane': layout.get('pane'), 'dv': [(d['col'], d['formula']) for d in layout.get('dv') or []],
            'cf': [(cf['col'], [(r.get('type'), r.get('operator'), r.get('text'), r.get('dxfId')) for r in cf['rules']]) for cf in layout.get('cf') or []],
            'table': {k: (layout.get('table') or {}).get(k) for k in ('name', 'columns', 'style')},
            'styles': tpl['styles']}


def diff_layout(left, right):
    return [{'id': None, 'field': f'layout:{sheet}:{key}'} for sheet in SHEET_STATUSES
            for key, value in _layout_key(left['sheets'][sheet]).items() if _layout_key(right['sheets'][sheet])[key] != value]


def roundtrip(tasks, template, note=NOTE):
    """Export, read back and compare: (books, differences)."""
    books = build_books(tasks, template, note)
    again = parse_books(books)
    if again['report']['blocking']:
        return books, [{'id': None, 'field': 'blocking'}]
    return books, diff_tasks(tasks, again['tasks']) + diff_layout(template, again['template'])


# JSON -------------------------------------------------------------------
def build_json(tasks, events, links):
    """任务数据.json: every task, its timeline and its mail links; readable and diffable."""
    by_task = {}
    for seq, review, event in events:
        by_task.setdefault(event['task'], []).append({
            '序号': seq, '审核': review, **{k: v for k, v in event.items() if k not in ('task', 'changes')},
            '变更': {LABEL.get(f, f): v for f, v in (event.get('changes') or {}).items()}})
    linked = {}
    for link in links:
        linked.setdefault(link['task'], []).append({k: v for k, v in link.items() if k != 'task'})
    rows = []
    for task in sorted(tasks, key=lambda t: t['id']):
        row = {LABEL[f]: task.get(f) for f in FIELDS}
        row['_内部'] = {'order': task.get('order', 0), 'cells': task.get('cells') or {}}
        row['时间线'] = by_task.get(task['id'], [])
        row['关联邮件'] = linked.get(task['id'], [])
        rows.append(row)
    counts = {status: sum(1 for t in tasks if t['status'] == status) for status in STATUSES}
    body = {'format': FORMAT, 'counts': {'未完成': sum(counts[s] for s in OPEN_STATUSES), DONE: counts[DONE],
                                         CANCELED: counts[CANCELED], '合计': len(tasks)}, 'tasks': rows}
    return (json.dumps(body, ensure_ascii=False, indent=1) + '\n').encode('utf-8')


def parse_json(data):
    """任务数据.json -> (tasks, events, links) for a full restore."""
    try:
        body = json.loads(data.decode('utf-8'))
        if body.get('format') != FORMAT:
            raise ValueError
        tasks, events, links = [], [], []
        for row in body['tasks']:
            task = {f: row[LABEL[f]] for f in FIELDS}
            task['order'] = row['_内部']['order']
            task['cells'] = row['_内部']['cells']
            if not isinstance(task['id'], int) or task['status'] not in STATUSES:
                raise ValueError
            tasks.append(task)
            for item in row.get('时间线') or []:
                event = {k: v for k, v in item.items() if k not in ('序号', '审核', '变更')}
                event['changes'] = {FIELD_OF.get(k, k): v for k, v in (item.get('变更') or {}).items()}
                events.append((task['id'], item.get('审核') or 'confirmed', event))
            for link in row.get('关联邮件') or []:
                links.append({**link, 'task': task['id']})
        return tasks, events, links
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
        raise AppError('TASK_IMPORT_INVALID') from None


# Files ------------------------------------------------------------------
def locked(folder, names):
    return any((Path(folder) / ('~$' + name)).exists() for name in names)


def _retry(action):
    """Antivirus, the search indexer or Explorer's preview hold a file for a moment."""
    for wait in RETRY_WAITS + (None,):
        try:
            return action()
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def _write(path, data, mode='wb'):
    with open(path, mode) as stream:
        stream.write(data)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def _put(temp, target, data):
    """Move the written copy over the target; when another program keeps the target open
    (but lets others write) the content is written in place instead."""
    try:
        _retry(lambda: os.replace(temp, target))
    except PermissionError:
        if not target.exists():
            raise
        _retry(lambda: _write(target, data, 'r+b'))
        temp.unlink(missing_ok=True)


def write_files(folder, files):
    """Replace the files in folder all together: each is written beside its target first;
    if a later replace fails the earlier ones are put back. Errors say which step failed
    (AppError.category: step, error type, Windows error number) for the export log."""
    folder = Path(folder)
    staged, done, step = [], [], 'write'
    try:
        for name, data in files.items():
            temp = folder / (name + '.hongyan-tmp')
            staged.append((name, temp))
            _retry(lambda: _write(temp, data))
        step = 'replace'
        for name, temp in staged:
            target = folder / name
            before = target.read_bytes() if target.exists() else None
            _put(temp, target, files[name])
            done.append((target, before))
    except OSError as error:
        for target, before in reversed(done):
            try:
                if before is None:
                    target.unlink()
                else:
                    _write(target, before)
            except OSError:
                pass
        for _, temp in staged:
            try:
                Path(temp).unlink(missing_ok=True)
            except OSError:
                pass
        detail = f'{step}:{type(error).__name__}:{getattr(error, "winerror", None) or error.errno}'
        # The folder takes no new file at all: no permission (or security software); a file
        # that cannot be replaced: another program has it open.
        code = ('TASK_EXPORT_IN_USE' if step == 'replace' else 'TASK_EXPORT_DENIED') if isinstance(error, PermissionError) else 'TASK_EXPORT_FAILED'
        raise AppError(code, category=detail) from None
