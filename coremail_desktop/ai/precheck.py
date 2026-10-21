"""本机发送前检查: rules run on every send, no network, no AI.

Kept quiet on purpose: a rule reports only what it is fairly sure of (a past date in a
future-tense phrase, a greeting naming nobody among the 主送 people, an outside address
in the body that is not a recipient). Issues share the AI check's shape.
"""
import re
from datetime import date, timedelta

FULL = re.compile(r'(?<!\d)(20\d\d)\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*[日号]?')
SHORT = re.compile(r'(?<![\d年/.-])(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]')
SOON = re.compile(r'将于|将在|请于|请在|定于|计划|安排|截止|最晚|之前|以前|前完成|前提交|前回复|前反馈|届时|会在|准备在|预计'
                  r'|\bby\b|\bbefore\b|\bdeadline\b|\bdue\b|\bwill\b|\bplan', re.I)
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)')
TITLES = re.compile(r'(?:总|工|经理|老师|主任|部长|处长|科长|院长|局长|主管|博士|教授|先生|女士|小姐|总监|董事长|同学|兄|姐|哥)$')
GROUPS = re.compile(r'^(?:各位|大家|全体|领导|同事|老师们|all|team|everyone|sir|madam|colleagues)', re.I)
WINDOW = 15
OLD_DAYS = 180  # A year-less date further back than this probably means next year.


def _day(year, month, day):
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def past_dates(text, today):
    issues, seen = [], set()
    found = [(m, _day(*m.groups())) for m in FULL.finditer(text)]
    found += [(m, _day(today.year, *m.groups())) for m in SHORT.finditer(text)
              if not any(f.start() <= m.start() < f.end() for f, _ in found)]
    for match, value in found:
        if value is None or value >= today or today - value > timedelta(days=OLD_DAYS) or match.group(0) in seen:
            continue
        around = text[max(0, match.start() - WINDOW):match.end() + WINDOW]
        if SOON.search(around):
            seen.add(match.group(0))
            issues.append({'kind': 'date', 'quote': match.group(0).strip(),
                           'advice': f'{match.group(0).strip()} 已经过去（今天是 {today.month}月{today.day}日），请确认日期。'})
    return issues


def greeting_name(text):
    """The name a greeting line addresses ('张总，你好' -> '张', 'Dear Tom,' -> 'Tom'), or ''."""
    line = next((l.strip() for l in text.splitlines() if l.strip()), '')
    if len(line) > 30:
        return ''
    english = re.match(r'^(?:dear|hi|hello)\s+(?:mr\.?|ms\.?|mrs\.?|dr\.?)?\s*([A-Za-z][A-Za-z-]{1,30})\s*[,，:!]?\s*$', line, re.I)
    if english:
        return '' if GROUPS.match(english.group(1)) else english.group(1)
    if re.match(r'^(?:你好|您好)|^(?:hi|hello|dear)\b', line, re.I):
        return ''
    chinese = re.match(r'^(尊敬的)?\s*([一-龥]{1,6}?)\s*[,，:：!！]?\s*(你好|您好|好)?\s*[,，:：!！]?\s*$', line)
    if not chinese:
        return ''
    # Only a clear greeting: 你好/您好, 尊敬的, a title (张总) or a name ending in a colon
    # (王彪：). '好的！' and '收到，' are not greetings.
    if not (chinese.group(1) or chinese.group(3) in ('你好', '您好') or TITLES.search(chinese.group(2)) or re.search(r'[:：]$', line)):
        return ''
    name = TITLES.sub('', chinese.group(2))
    return '' if not name or GROUPS.match(chinese.group(2)) or len(name) > 4 else name


def greeting(text, to):
    """to: [{'name', 'email'}] of the 主送 people."""
    name = greeting_name(text)
    if not name or not to:
        return []
    folded = name.casefold()
    for person in to:
        label, mail = (person.get('name') or '').casefold(), (person.get('email') or '').casefold()
        if re.match(r'^[a-z]', folded):
            if folded in label or folded in mail.partition('@')[0]:
                return []
        elif not re.search(r'[一-龥]', label) or label.startswith(folded) or folded in label:
            return []  # A recipient without a Chinese name cannot be ruled out.
    line = next(l.strip() for l in text.splitlines() if l.strip())
    return [{'kind': 'greeting', 'quote': line, 'advice': f'称呼「{line}」中的「{name}」不在主送人中，请确认收件人或称呼。'}]


def outside_addresses(text, recipients, own_domains):
    known = {(r or '').casefold() for r in recipients}
    issues = []
    for match in EMAIL.finditer(text):
        address = match.group(0).casefold().rstrip('.')
        domain = address.rpartition('@')[2]
        if address in known or domain in own_domains or any(i['quote'] == address for i in issues):
            continue
        issues.append({'kind': 'address', 'quote': address, 'advice': f'正文中的外部邮箱 {address} 不在收件人中，请确认是否应该出现在这里。'})
    return issues[:3]


def check(text, to, recipients, own_domains, today):
    """text: the user's own words; to: 主送 [{'name','email'}]; recipients: every address."""
    today = today if isinstance(today, date) else date.fromisoformat(today)
    return past_dates(text, today) + greeting(text, to) + outside_addresses(text, recipients, {d.casefold() for d in own_domains or ()})
