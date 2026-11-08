"""邮件时间 (Z1): the one place that knows which zone a mail's time is in.

Stored convention: the server, the cache, the local archive and the adapter all carry a mail's
time as Riyadh text 'YYYY-MM-DD HH:MM:SS' — the zone every JSON-RPC request names in its tz
header (verified 2026-10-17), so nothing stored moves when the user travels. Shown, and 「今天」
counted, in the computer's zone (GLOSSARY.md 邮件时间); the UI's mailTime() reads the same text.
"""
import re
from datetime import datetime, timedelta, timezone

from coremail_desktop.errors import AppError

# Saudi Arabia keeps +03:00 all year (no daylight saving), so no tz database is needed.
STORE_ZONE = timezone(timedelta(hours=3), 'Asia/Riyadh')
STORE_ZONE_NAME = 'Asia/Riyadh'
FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d')
# The account's own time_zone attribute (Asia/Shanghai on the deployment) is what the form-route
# search writes and what scheduleDate / auto-reply dates are read in. Only zones with a fixed offset
# are listed: without tz data a zone with daylight saving cannot be converted safely.
FIXED_ZONES = {'Asia/Shanghai':8,'Asia/Chongqing':8,'Asia/Harbin':8,'Asia/Urumqi':6,'Asia/Hong_Kong':8,'Asia/Macau':8,'Asia/Taipei':8,
               'Asia/Singapore':8,'Asia/Kuala_Lumpur':8,'Asia/Manila':8,'Asia/Tokyo':9,'Asia/Seoul':9,'Asia/Riyadh':3,'Asia/Dubai':4,
               'Asia/Karachi':5,'Asia/Kolkata':5.5,'Asia/Calcutta':5.5,'Asia/Dhaka':6,'Asia/Bangkok':7,'Asia/Jakarta':7,'Asia/Ho_Chi_Minh':7,
               'Europe/Moscow':3,'Africa/Nairobi':3,'UTC':0,'Etc/UTC':0,'GMT':0,'Etc/GMT':0}


def _read(text, zone):
    for fmt in FORMATS:
        try:
            return datetime.strptime(str(text)[:19], fmt).replace(tzinfo=zone)
        except ValueError:
            continue
    return None


def moment(text):
    """Stored text -> the moment it names; None when it is not a date."""
    return _read(text, STORE_ZONE)


def stored(text, zone):
    """Server text written in zone -> stored Riyadh text; anything unreadable stays as it is."""
    value = _read(text, zone)
    return value.astimezone(STORE_ZONE).strftime('%Y-%m-%d %H:%M:%S') if value else text


def account_zone(name):
    """The account's time_zone attribute -> a fixed-offset zone; SCHEDULE_ZONE_UNSUPPORTED otherwise."""
    if isinstance(name, str) and name in FIXED_ZONES:
        return timezone(timedelta(hours=FIXED_ZONES[name]))
    match = re.fullmatch(r'(?:GMT|UTC)?([+-])(\d{1,2})(?::?(\d\d))?', name.strip()) if isinstance(name, str) else None
    if match and int(match.group(2)) <= 14:
        hours = int(match.group(2)) + int(match.group(3) or 0) / 60
        return timezone(timedelta(hours=hours if match.group(1) == '+' else -hours))
    raise AppError('SCHEDULE_ZONE_UNSUPPORTED')
