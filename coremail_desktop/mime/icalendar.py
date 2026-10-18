"""Meeting invitations: the text/calendar part of a message (RFC 5545 / iTIP).

Only what the reader's invitation card needs is read: METHOD, the first VEVENT's
summary, time, location, organizer and attendees. Times become UTC epoch
milliseconds so the page shows them in the viewer's own zone. This machine has no
tz database, so a TZID is resolved from the invitation's own VTIMEZONE block
(standard/daylight offsets and their yearly rules), then from a table of fixed
zones; an unknown zone is reported as floating local time rather than guessed.
"""
import re
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser

CALENDAR_TYPES = {'text/calendar', 'application/ics', 'text/x-vcalendar'}
MAX_ICS_BYTES = 512 * 1024
METHODS = {'REQUEST', 'CANCEL', 'REPLY', 'PUBLISH', 'COUNTER', 'REFRESH', 'ADD', 'DECLINECOUNTER'}
PARTSTATS = {'NEEDS-ACTION', 'ACCEPTED', 'DECLINED', 'TENTATIVE', 'DELEGATED'}
# Zones without daylight saving, by IANA and Windows name (Outlook writes the latter).
FIXED_OFFSETS = {'asia/shanghai': 8, 'asia/chongqing': 8, 'asia/harbin': 8, 'asia/hong_kong': 8, 'asia/macau': 8, 'asia/taipei': 8,
                 'asia/singapore': 8, 'asia/kuala_lumpur': 8, 'asia/manila': 8, 'asia/urumqi': 6, 'asia/tokyo': 9, 'asia/seoul': 9,
                 'asia/riyadh': 3, 'asia/dubai': 4, 'asia/karachi': 5, 'asia/kolkata': 5.5, 'asia/calcutta': 5.5, 'asia/dhaka': 6,
                 'asia/bangkok': 7, 'asia/jakarta': 7, 'asia/ho_chi_minh': 7, 'europe/moscow': 3, 'utc': 0, 'etc/utc': 0, 'gmt': 0,
                 'china standard time': 8, 'taipei standard time': 8, 'singapore standard time': 8, 'tokyo standard time': 9,
                 'korea standard time': 9, 'arab standard time': 3, 'arabian standard time': 4, 'india standard time': 5.5,
                 'se asia standard time': 7, 'russian standard time': 3, 'utc+08': 8, 'w. australia standard time': 8,
                 '(utc+08:00) beijing, chongqing, hong kong, urumqi': 8}
DAYS = {'MO': 0, 'TU': 1, 'WE': 2, 'TH': 3, 'FR': 4, 'SA': 5, 'SU': 6}


def calendar_part(raw):
    """(bytes, filename) of the first calendar part of a raw message, or None."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except (ValueError, TypeError, RecursionError):
        return None
    for part in message.walk():
        name = part.get_filename() or ''
        if part.get_content_type() in CALENDAR_TYPES or name.lower().endswith('.ics'):
            payload = part.get_payload(decode=True)
            if payload and len(payload) <= MAX_ICS_BYTES:
                charset = part.get_content_charset() or 'utf-8'
                return payload, name, charset, (part.get_param('method') or '')
    return None


def _unfold(text):
    return re.sub(r'\r?\n[ \t]', '', text.replace('\r\n', '\n'))


def _split_params(head):
    parts, current, quoted = [], '', False
    for char in head:
        if char == '"':
            quoted = not quoted
        if char == ';' and not quoted:
            parts.append(current)
            current = ''
        else:
            current += char
    parts.append(current)
    name, params = parts[0].upper(), {}
    for item in parts[1:]:
        key, _, value = item.partition('=')
        params[key.strip().upper()] = value.strip().strip('"')
    return name, params


def _lines(text):
    """[(NAME, params, value)] of an unfolded calendar text."""
    result = []
    for line in _unfold(text).split('\n'):
        if not line.strip():
            continue
        quoted, cut = False, -1
        for i, char in enumerate(line):
            if char == '"':
                quoted = not quoted
            elif char == ':' and not quoted:
                cut = i
                break
        if cut < 0:
            continue
        name, params = _split_params(line[:cut])
        result.append((name, params, line[cut + 1:]))
    return result


def _components(lines):
    """Nested {'name', 'props': [(NAME, params, value)], 'children': [...]}."""
    root = {'name': 'ROOT', 'props': [], 'children': []}
    stack = [root]
    for name, params, value in lines:
        if name == 'BEGIN':
            node = {'name': value.strip().upper(), 'props': [], 'children': []}
            stack[-1]['children'].append(node)
            if len(stack) < 8:
                stack.append(node)
        elif name == 'END':
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1]['props'].append((name, params, value))
    return root


def _first(node, name):
    return next(((p, v) for n, p, v in node['props'] if n == name), (None, None))


def _text(value):
    if value is None:
        return ''
    return re.sub(r'\\([\\;,nN])', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value).strip()


def _offset(text):
    match = re.fullmatch(r'([+-])(\d\d)(\d\d)(\d\d)?', (text or '').strip())
    if not match:
        return None
    seconds = int(match.group(2)) * 3600 + int(match.group(3)) * 60 + int(match.group(4) or 0)
    return timedelta(seconds=seconds if match.group(1) == '+' else -seconds)


def _local(value):
    match = re.fullmatch(r'(\d{4})(\d\d)(\d\d)(?:T(\d\d)(\d\d)(\d\d)?)?(Z)?', (value or '').strip())
    if not match:
        return None, False, False
    y, mo, d, h, mi, s, z = match.groups()
    try:
        if h is None:
            return date(int(y), int(mo), int(d)), True, False
        return datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0)), False, bool(z)
    except ValueError:
        return None, False, False


def _nth_weekday(year, month, rule):
    """The day of a yearly BYDAY rule such as 2SU or -1SU in that month."""
    match = re.fullmatch(r'([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)', rule or '')
    if not match:
        return None
    nth, weekday = int(match.group(1) or 1), DAYS[match.group(2)]
    if nth > 0:
        first = date(year, month, 1)
        day = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (nth - 1))
    else:
        last = (date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1))
        day = last - timedelta(days=(last.weekday() - weekday) % 7 + 7 * (-nth - 1))
    return day if day.month == month else None


class _Zone:
    """A VTIMEZONE: its observances, each with an offset and a start rule."""

    def __init__(self, node):
        self.observances = []
        for child in node['children']:
            if child['name'] not in ('STANDARD', 'DAYLIGHT'):
                continue
            offset = _offset(_first(child, 'TZOFFSETTO')[1])
            start, _, _ = _local(_first(child, 'DTSTART')[1])
            if offset is None or not isinstance(start, datetime):
                continue
            rule = dict(item.split('=', 1) for item in (_first(child, 'RRULE')[1] or '').split(';') if '=' in item)
            self.observances.append((start, offset, rule, _offset(_first(child, 'TZOFFSETFROM')[1]) or offset))

    def _onsets(self, year):
        for start, offset, rule, before in self.observances:
            if rule.get('FREQ') == 'YEARLY' and rule.get('BYMONTH', '').isdigit():
                day = _nth_weekday(year, int(rule['BYMONTH']), rule.get('BYDAY', '').split(',')[0])
                if day:
                    yield datetime.combine(day, start.time()), offset, before
            elif start.year <= year:
                yield start, offset, before

    def offset(self, moment):
        if not self.observances:
            return None
        best = None
        for year in (moment.year - 1, moment.year):
            for onset, offset, before in self._onsets(year):
                if onset <= moment and (best is None or onset > best[0]):
                    best = (onset, offset)
        if best is None:
            return min(self.observances, key=lambda o: o[0])[3]
        return best[1]


def _instant(value, params, zones):
    """(epoch ms or None, 'YYYY-MM-DD' for a date, floating local text or None)."""
    moment, is_date, utc = _local(value)
    if moment is None:
        return None, None, None
    if is_date:
        return None, moment.isoformat(), None
    if utc:
        return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000), None, None
    tzid = (params or {}).get('TZID', '').strip()
    offset = zones[tzid].offset(moment) if tzid in zones else None
    if offset is None and tzid:
        hours = FIXED_OFFSETS.get(tzid.strip('/').lower())
        match = re.search(r'(?:GMT|UTC)\s*([+-])(\d{1,2})(?::?(\d\d))?', tzid) if hours is None else None
        if match:
            hours = (int(match.group(2)) + int(match.group(3) or 0) / 60) * (1 if match.group(1) == '+' else -1)
        offset = timedelta(hours=hours) if hours is not None else None
    if offset is None:
        return None, None, moment.strftime('%Y-%m-%d %H:%M')
    return int(moment.replace(tzinfo=timezone(offset)).timestamp() * 1000), None, None


def _person(params, value):
    email = re.sub(r'(?i)^mailto:', '', (value or '').strip())
    return {'name': _text(params.get('CN', ''))[:200], 'email': email if '@' in email and len(email) < 320 else ''}


def parse_invitation(data, charset='utf-8', part_method=''):
    """The card data of a calendar text, or None when it holds no event."""
    text = data.decode(charset, errors='replace') if isinstance(data, bytes) else str(data)
    root = _components(_lines(text))
    calendar = next((c for c in root['children'] if c['name'] == 'VCALENDAR'), None)
    if calendar is None:
        return None
    events = [c for c in calendar['children'] if c['name'] == 'VEVENT']
    if not events:
        return None
    zones = {}
    for child in calendar['children']:
        if child['name'] == 'VTIMEZONE':
            tzid = (_first(child, 'TZID')[1] or '').strip()
            if tzid:
                zones[tzid] = _Zone(child)
    # The master event, not an exception (RECURRENCE-ID), describes the meeting.
    event = next((e for e in events if _first(e, 'RECURRENCE-ID')[1] is None), events[0])
    method = (_first(calendar, 'METHOD')[1] or part_method or '').strip().upper()
    start_params, start_value = _first(event, 'DTSTART')
    end_params, end_value = _first(event, 'DTEND')
    start, start_date, start_floating = _instant(start_value, start_params, zones)
    end, end_date, end_floating = _instant(end_value, end_params, zones)
    if start_date and not end_date:
        end_date = start_date
    duration = re.fullmatch(r'P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?', (_first(event, 'DURATION')[1] or '').strip())
    if end is None and start is not None and duration and any(duration.groups()):
        w, d, h, m, s = (int(x or 0) for x in duration.groups())
        end = start + int(timedelta(weeks=w, days=d, hours=h, minutes=m, seconds=s).total_seconds() * 1000)
    organizer = _person(*_first(event, 'ORGANIZER')) if _first(event, 'ORGANIZER')[1] else {'name': '', 'email': ''}
    attendees = []
    for name, params, value in event['props']:
        if name == 'ATTENDEE' and len(attendees) < 500:
            person = _person(params, value)
            state = params.get('PARTSTAT', 'NEEDS-ACTION').upper()
            attendees.append({**person, 'role': params.get('ROLE', 'REQ-PARTICIPANT').upper()[:30],
                              'partstat': state if state in PARTSTATS else 'NEEDS-ACTION', 'rsvp': params.get('RSVP', '').upper() == 'TRUE'})
    status = (_first(event, 'STATUS')[1] or '').strip().upper()
    return {'method': method if method in METHODS else (method[:20] or 'PUBLISH'),
            'cancelled': method == 'CANCEL' or status == 'CANCELLED',
            'uid': _text(_first(event, 'UID')[1])[:500], 'sequence': int(_first(event, 'SEQUENCE')[1] or 0) if (_first(event, 'SEQUENCE')[1] or '').strip().isdigit() else 0,
            'summary': _text(_first(event, 'SUMMARY')[1])[:500], 'location': _text(_first(event, 'LOCATION')[1])[:500],
            'description': _text(_first(event, 'DESCRIPTION')[1])[:4000],
            'start': start, 'end': end, 'allDay': bool(start_date), 'startDate': start_date, 'endDate': end_date,
            'floating': start_floating, 'floatingEnd': end_floating, 'tzid': (start_params or {}).get('TZID', '')[:100],
            'organizer': organizer, 'attendees': attendees, 'status': status[:20],
            'recurring': _first(event, 'RRULE')[1] is not None, 'exceptions': len(events) - 1}


def find_invitation(raw):
    found = calendar_part(raw)
    if not found:
        return None
    data, _name, charset, method = found
    try:
        return parse_invitation(data, charset, method)
    except (ValueError, TypeError, RecursionError, LookupError):
        return None
