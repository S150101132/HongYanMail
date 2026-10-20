"""The task-management rules of the Saudi-NG task folder (AGENTS.md, 任务管理说明.md)
as checks and defaults. Pure functions: no storage, no files."""
import re
from datetime import date, datetime, timedelta, timezone

from coremail_desktop.errors import AppError

FIELDS = ('id', 'system', 'content', 'priority', 'status', 'latest', 'owner', 'start', 'due', 'end',
          'blockers', 'next', 'updated', 'history', 'acceptance')
HEADERS = ('编号', '系统', '任务内容', '优先级', '状态', '最新进展', '负责人／责任边界', '开始日期', '计划完成日期',
           '结束日期', '阻塞与依赖', '下一步', '最近更新日期', '进展与历史记录', '完成／验收标准')
LABEL = dict(zip(FIELDS, HEADERS))
DATE_FIELDS = ('start', 'due', 'end', 'updated')
TEXT_FIELDS = tuple(f for f in FIELDS if f != 'id' and f not in DATE_FIELDS)
OPEN_STATUSES = ('未开始', '待处理', '进行中', '受阻', '待验收')
STATUSES = OPEN_STATUSES + ('已完成', '已取消')
PRIORITIES = ('高', '中', '低', '')
DONE, CANCELED = '已完成', '已取消'
OPEN_SHEET, DONE_SHEET, CANCELED_SHEET = '未完成任务', '已完成任务', '已取消任务'
OWNER_DEFAULT = '用户本人'  # Confirmed 2026-10-15: the user is the default owner.
TEXT_LIMIT = 20000
# Saudi Arabia keeps +03:00 all year (no daylight saving), so no tz database is needed.
RIYADH = timezone(timedelta(hours=3), 'Asia/Riyadh')
DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')  # Not storable in a workbook.


def riyadh_today(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(RIYADH).date().isoformat()


def sheet_of(status):
    return DONE_SHEET if status == DONE else CANCELED_SHEET if status == CANCELED else OPEN_SHEET


def is_day(value):
    if not isinstance(value, str) or not DAY.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def day(value, required=False):
    """A date typed by the user: 'YYYY-MM-DD', or '' when not required."""
    value = str(value or '').strip()
    if not value and not required:
        return ''
    if not is_day(value):
        raise AppError('TASK_INVALID')
    return value


def text(value, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str) or len(value) > TEXT_LIMIT or (required and not value.strip()) or CONTROL.search(value):
        raise AppError('TASK_INVALID')
    return value.replace('\r\n', '\n')


def check_fields(fields, allowed):
    """User edits: only known fields, valid values; dates must be real dates."""
    if not isinstance(fields, dict) or set(fields) - set(allowed):
        raise AppError('TASK_INVALID')
    clean = {}
    for name, value in fields.items():
        if name in DATE_FIELDS:
            clean[name] = day(value)
        elif name == 'priority':
            if value not in PRIORITIES:
                raise AppError('TASK_INVALID')
            clean[name] = value
        elif name == 'status':
            if value not in STATUSES:
                raise AppError('TASK_STATUS_INVALID')
            if value not in OPEN_STATUSES:
                raise AppError('TASK_STATUS_INVALID')  # Done and canceled only through their own actions.
            clean[name] = value
        else:
            clean[name] = text(value, required=name == 'content')
    return clean


def check_blocked(task):
    """受阻 needs its blocker written down (only checked when a task becomes blocked)."""
    if task['status'] == '受阻' and not str(task.get('blockers') or '').strip():
        raise AppError('TASK_BLOCKER_REQUIRED')


def append_history(history, on_day, line):
    """One 'YYYY-MM-DD：…' line after the existing text, which stays as it was."""
    entry = f'{on_day}：{line.strip()}'
    return f'{history}\n{entry}' if history else entry


def overdue(task, today):
    due = task.get('due')
    return task['status'] in OPEN_STATUSES and is_day(due) and due < today


def missing(task):
    """Fields left open for confirmation (shown as 待确认, never filled in)."""
    return [f for f in ('owner', 'acceptance') if not str(task.get(f) or '').strip()]


def blank_task(task_id):
    return {'id': task_id, **{f: '' for f in FIELDS if f != 'id'}}
