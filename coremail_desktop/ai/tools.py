"""问邮箱 tools: read-only search, reading and contact lookup the model may call.

The model sees short refs (M1 for mail, P1 for people), display names and domains,
never server ids or full addresses; the refs map back here, for the source cards.
Nothing here can send, move, delete or flag mail.
"""
import json
import re
from datetime import datetime

from coremail_desktop.ai.summary import _who, mail_text
from coremail_desktop.errors import AppError

PAGE = 20
READ_CHARS = 4000
MAX_READS = 5
SNIPPET = 100
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

SPECS = [
    {'type': 'function', 'function': {
        'name': 'search_mail',
        'description': '全文搜索用户的邮件（主题、正文、附件名）：服务器上的邮件，以及官方客户端移到本机的「本地存档」（较早的邮件常在这里）。'
                       '多个关键词用空格分开，表示同时包含。返回总数和每个来源每页最多 20 封的摘要：编号、来源、主题、发件人、时间、文件夹、是否有附件、正文开头约 100 字。',
        'parameters': {'type': 'object', 'properties': {
            'keywords': {'type': 'string', 'description': '关键词，空格分开；只按发件人或日期查时可以为空'},
            'sender': {'type': 'string', 'description': '发件人：姓名、find_contact 返回的编号（如 P1）或邮箱地址的一部分'},
            'since': {'type': 'string', 'description': '起始日期 YYYY-MM-DD（含）'},
            'until': {'type': 'string', 'description': '截止日期 YYYY-MM-DD（含）'},
            'folder': {'type': 'string', 'enum': ['all', 'inbox', 'sent'], 'description': '服务器上的范围，默认 all；指定 inbox 或 sent 时不搜本地存档'},
            'source': {'type': 'string', 'enum': ['all', 'server', 'archive'], 'description': '来源：all（默认）、server 只搜服务器、archive 只搜本地存档'},
            'page': {'type': 'integer', 'description': '页码，从 1 开始'}}}}},
    {'type': 'function', 'function': {
        'name': 'read_mail',
        'description': '阅读一封邮件的正文（纯文本，较长时截断）、收件人和附件名。最多读 5 封。',
        'parameters': {'type': 'object', 'properties': {
            'ref': {'type': 'string', 'description': 'search_mail 返回的邮件编号，如 M3'}}, 'required': ['ref']}}},
    {'type': 'function', 'function': {
        'name': 'find_contact',
        'description': '在公司通讯录里按姓名查人，返回编号（如 P1）、姓名和邮箱域名；编号可作为 search_mail 的 sender。',
        'parameters': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': '姓名或其中一部分'}}, 'required': ['name']}}},
]


def _clip(value, limit):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


class MailboxTools:
    """search(pattern, offset, limit, filters) -> {'rows': [backend summary rows], 'total'};
    read(mid) -> parsed document; match(name) -> [{'name', 'email'}];
    folders: {'inbox': fid, 'sent': fid, 'names': {fid: name}};
    allowed: mail ids whose decrypted text the user agreed to send."""

    def __init__(self, search, read, match, folders, allowed=(), archive=None):
        """archive: like search, over the local archive (no folders); None when there is none."""
        self._search, self._read, self._match, self._archive = search, read, match, archive
        self.folders, self.allowed = folders, set(allowed)
        self.mails, self._refs = {}, {}  # ref -> summary row; mid -> ref
        self.people = {}  # ref -> address
        self.read_refs, self.encrypted = [], []

    def begin(self, allowed=()):
        """The next question of a conversation: refs stay, while reads, the encrypted
        list and the user's agreements start over."""
        self.allowed, self.read_refs, self.encrypted = set(allowed), [], []

    def add(self, row):
        """A mail the user brought in (the one open in the reader) -> its ref."""
        return self._ref(row)

    def note(self, ref):
        """One line naming a known mail, for the conversation's context."""
        r = self.mails[ref]
        return f'{ref}｜{_clip(r.get("subject"), 80) or "（无主题）"}｜{_who(r.get("from"), 1)}｜{str(r.get("date") or "")[:16]}'

    def run(self, name, arguments):
        """-> (result for the model, a progress line for the user)."""
        try:
            args = json.loads(arguments or '{}')
        except ValueError:
            args = None
        if not isinstance(args, dict):
            return {'error': '参数不是有效的 JSON 对象'}, '工具参数无效'
        method = {'search_mail': self.search_mail, 'read_mail': self.read_mail, 'find_contact': self.find_contact}.get(name)
        if method is None:
            return {'error': '没有这个工具'}, '工具不存在'
        return method(args)

    def _ref(self, row):
        ref = self._refs.get(row['id'])
        if ref is None:
            ref = self._refs[row['id']] = f'M{len(self._refs) + 1}'
        self.mails[ref] = row
        return ref

    def _person(self, person):
        ref = next((r for r, a in self.people.items() if a == person['email']), None) or f'P{len(self.people) + 1}'
        self.people[ref] = person['email']
        return {'ref': ref, 'name': person.get('name') or person['email'].partition('@')[0],
                'domain': person['email'].rpartition('@')[2]}

    def _sender(self, value):
        """-> (address fragment, None) or (None, result explaining why not)."""
        value = _clip(value, 100)
        if value in self.people:
            return self.people[value], None
        if '@' in value or re.fullmatch(r'[\w.+-]+', value, re.A):
            return value, None  # Part of an address, as webmail matches it.
        people = self._match(value)
        exact = [p for p in people if p.get('name') == value]
        if len(exact) == 1 or len(people) == 1:
            return (exact or people)[0]['email'], None
        if not people:
            return None, {'error': f'通讯录里没有找到「{value}」，可以把名字作为关键词搜索'}
        return None, {'ambiguous': [self._person(p) for p in people[:5]], 'note': '有多个同名或相近的人，请用编号作为 sender 重新搜索'}

    def search_mail(self, args):
        keywords = _clip(args.get('keywords'), 200)
        filters, conditions = {}, []
        if args.get('sender'):
            address, problem = self._sender(args['sender'])
            if problem:
                return problem, f'查找发件人「{_clip(args["sender"], 40)}」'
            filters['from'] = address
            conditions.append('发件人 ' + _clip(args['sender'], 40))
        for key in ('since', 'until'):
            value = str(args.get(key) or '').strip()
            if value:
                try:
                    if not _DATE.match(value):
                        raise ValueError
                    datetime.strptime(value, '%Y-%m-%d')
                except ValueError:
                    return {'error': f'{key} 必须是 YYYY-MM-DD'}, '日期格式无效'
                filters[key] = value
        if filters.get('since') and filters.get('until'):
            conditions.append(f'{filters["since"]} 至 {filters["until"]}')
        elif filters.get('since') or filters.get('until'):
            conditions.append(('自 ' + filters['since']) if filters.get('since') else ('至 ' + filters['until']))
        folder = args.get('folder') or 'all'
        if folder in ('inbox', 'sent') and self.folders.get(folder):
            filters['folder'] = self.folders[folder]
            conditions.append('收件箱' if folder == 'inbox' else '已发送')
        page = args.get('page') if type(args.get('page')) is int and 1 <= args.get('page') <= 10 else 1
        label = '搜索' + (f'「{keywords}」' if keywords else '') + ('（' + '，'.join(conditions) + '）' if conditions else '')
        if not keywords and not filters:
            return {'error': '需要关键词，或发件人、日期、文件夹中的至少一项'}, label
        source = args.get('source') if args.get('source') in ('server', 'archive') else 'all'
        if source == 'archive' and self._archive is None:
            return {'error': '没有设置本地存档，只能搜索服务器'}, label
        offset, found, stored = (page - 1) * PAGE, {'rows': [], 'total': 0}, {'rows': [], 'total': 0}
        if source != 'archive':
            try:
                found = self._search(keywords, offset, PAGE, filters)
            except AppError as error:
                if error.code not in ('INVALID_SEARCH', 'INVALID_PAGE'):
                    raise
                if source == 'server' or self._archive is None:
                    return {'error': '服务器不接受这组搜索条件，请换一种写法'}, label
        if source != 'server' and self._archive is not None and not filters.get('folder'):
            stored = self._archive(keywords, offset, PAGE, {k: v for k, v in filters.items() if k != 'folder'})
        names = self.folders.get('names') or {}
        view = lambda r, archived: {
            'ref': self._ref(r), 'source': '本地存档' if archived else '服务器', 'subject': _clip(r.get('subject'), 200) or '（无主题）',
            'from': _who(r.get('from'), 1), 'date': str(r.get('date') or '')[:16],
            'folder': '本地存档' if archived else names.get(str(r.get('fid')), ''),
            'attachment': bool((r.get('flags') or {}).get('attached')), 'snippet': _clip(r.get('preview'), SNIPPET)}
        rows = [view(r, False) for r in found['rows']] + [view(r, True) for r in stored['rows']]
        total = found['total'] + stored['total']
        more = offset + len(found['rows']) < found['total'] or offset + len(stored['rows']) < stored['total']
        where = f'（其中本地存档 {stored["total"]} 封）' if stored['total'] else ''
        result = {'total': total, 'page': page, 'rows': rows, 'more': more}
        if not total:
            result['hint'] = ('没有结果：每个词都必须出现。请减少关键词，或换成邮件里可能用的说法（多为英文，如 sign-off、UAT）'
                              + ('；指定 folder 时不搜本地存档' if filters.get('folder') else ''))
        elif stored['total'] > len(stored['rows']) or found['total'] > len(found['rows']):
            result['hint'] = '结果较多，本页只是一部分：本地存档按相关度排列（主题、附件名命中在前），服务器按时间排列；可以加关键词缩小范围'
        return result, f'{label}：{total} 封{where}'

    def read_mail(self, args):
        ref = _clip(args.get('ref'), 10).upper()
        row = self.mails.get(ref)
        if row is None:
            return {'error': f'没有编号 {ref}，请先用 search_mail 搜索'}, f'阅读 {ref}（编号无效）'
        label = f'阅读 {ref}「{_clip(row.get("subject"), 40)}」'
        if ref in self.read_refs:
            return {'error': f'{ref} 已经读过了'}, label
        if len(self.read_refs) >= MAX_READS:
            return {'error': f'本次最多阅读 {MAX_READS} 封，请根据已有信息回答'}, label + '（已达上限）'
        document = self._read(row['id'])
        if document.encryption_state in ('decrypted', 'locked') and (
                document.encryption_state == 'locked' or row['id'] not in self.allowed):
            if ref not in self.encrypted:
                self.encrypted.append(ref)
            return {'ref': ref, 'encrypted': True, 'note': '这是加密邮件，用户没有允许把正文发给 AI，不能阅读'}, label + '（加密，未读取）'
        self.read_refs.append(ref)
        header = lambda key: ', '.join(document.headers.get(key, ()))
        text, truncated = mail_text(document)
        if len(text) > READ_CHARS:
            text, truncated = text[:READ_CHARS] + '\n……（后面的内容已省略）', True
        return ({'ref': ref, 'subject': header('subject') or row.get('subject', ''), 'from': _who(header('from') or row.get('from'), 1),
                 'to': _who(header('to')), 'cc': _who(header('cc')), 'date': str(row.get('date') or header('date'))[:16],
                 'attachments': [a.name for a in document.attachments][:20], 'text': text or '（没有正文）', 'truncated': truncated},
                label)

    def find_contact(self, args):
        name = _clip(args.get('name'), 50)
        if not name:
            return {'error': '需要姓名'}, '查找联系人'
        people = [self._person(p) for p in self._match(name)[:5]]
        return {'people': people}, f'查找联系人「{name}」：{len(people)} 人'
