"""Advanced-search filters (高级搜索): validating what the page sends, and the row predicate for
the filters a server did not apply (and for the local cache). Shared by the backend and the
demo server, so the fake server does not depend on the client."""
from datetime import datetime

from coremail_desktop.app.cache import date_ts
from coremail_desktop.errors import AppError

SEARCH_TEXT = ('from', 'to', 'with')  # Address substrings; 'with' is either side (往来邮件).
SEARCH_FLAGS = ('attachment', 'unread', 'flagged')


def search_filters(value):
    """Advanced-search filters from the page, validated: unknown keys are dropped."""
    value = dict(value or {})
    result = {}
    for key in SEARCH_TEXT:
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            if len(text) > 200:
                raise AppError('INVALID_SEARCH')
            result[key] = text.strip()
    if value.get('scope') == 'subject':
        result['scope'] = 'subject'
    for key in ('since', 'until'):
        text = value.get(key)
        if text:
            try:
                datetime.strptime(str(text), '%Y-%m-%d')
            except ValueError:
                raise AppError('INVALID_SEARCH') from None
            result[key] = str(text)
    if result.get('since') and result.get('until') and result['since'] > result['until']:
        raise AppError('INVALID_SEARCH')
    if value.get('folder') not in (None, ''):
        result['folder'] = str(value['folder'])
    for key in SEARCH_FLAGS:
        if value.get(key) is True:
            result[key] = True
    return result


def filter_match(filters, query=''):
    """Row predicate for the filters a server did not apply (and for the local cache)."""
    terms = [t for t in str(query).casefold().split() if t]
    since = date_ts(filters['since']) if filters.get('since') else None
    until = date_ts(filters['until']) + 86400 if filters.get('until') else None

    def match(row):
        flags = row.get('flags') or {}
        sender, rcpt = str(row.get('from', '')).casefold(), str(row.get('to', '')).casefold()
        return ((not filters.get('from') or filters['from'].casefold() in sender)
                and (not filters.get('to') or filters['to'].casefold() in rcpt)
                and (not filters.get('with') or filters['with'].casefold() in sender + ' ' + rcpt)
                and (filters.get('scope') != 'subject' or all(t in str(row.get('subject', '')).casefold() for t in terms))
                and (since is None or (row.get('ts') or 0) >= since) and (until is None or (row.get('ts') or 0) < until)
                and (not filters.get('folder') or str(row.get('fid')) == filters['folder'])
                and (not filters.get('attachment') or bool(flags.get('attached')))
                and (not filters.get('unread') or not flags.get('read'))
                and (not filters.get('flagged') or bool(flags.get('flagged'))))
    return match
