"""问邮箱 tools: read-only search, reading and contact lookup the model may call.

The model sees short refs (M1 for mail, P1 for people), display names and domains,
never server ids or full addresses; the refs map back here, for the source cards.
Nothing here can send, move, delete or flag mail: the assistant's operations (A3) are
plans the user runs from a card.
"""
import json
import re
from datetime import datetime

from coremail_desktop.ai.attachments import attachment_texts
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
        'description': '阅读一封邮件的正文（纯文本，较长时截断）、收件人、附件名和附件里的文字（Word、Excel、PPT、PDF、文本，较长时截断）。最多读 5 封。',
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

    def __init__(self, search, read, match, folders, allowed=(), archive=None, auto_allow=None, pdf_text=None):
        """archive: like search, over the local archive (no folders); None when there is none.
        auto_allow(mid): encrypted mail the user allowed once for all (设置 → AI 助手).
        pdf_text(bytes) -> str: the text of a PDF attachment (QtPdf), or None."""
        self._search, self._read, self._match, self._archive = search, read, match, archive
        self._pdf_text = pdf_text
        self.folders, self.allowed = folders, set(allowed)
        self._auto_allow = auto_allow or (lambda mid: False)
        self.mails, self._refs = {}, {}  # ref -> summary row; mid -> ref
        self.people, self.person_names = {}, {}  # ref -> address; address -> display name
        self.read_refs, self.encrypted = [], []
        self.searches = {}  # S1 -> a server search as run: {'keywords', 'filters', 'total', 'label'}
        self.specs = SPECS

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
        self.person_names[person['email']] = person.get('name') or ''
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
        if found['total']:  # The whole server result, by ref, for an operation on all of it (A3).
            sref = next((r for r, s in self.searches.items() if s['keywords'] == keywords and s['filters'] == filters), None) \
                or f'S{len(self.searches) + 1}'
            self.searches[sref] = {'keywords': keywords, 'filters': filters, 'total': found['total'], 'label': label}
            result['search'] = sref
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
                document.encryption_state == 'locked' or row['id'] not in self.allowed and not self._auto_allow(row['id'])):
            if ref not in self.encrypted:
                self.encrypted.append(ref)
            return {'ref': ref, 'encrypted': True, 'note': '这是加密邮件，用户没有允许把正文发给 AI，不能阅读'}, label + '（加密，未读取）'
        self.read_refs.append(ref)
        header = lambda key: ', '.join(document.headers.get(key, ()))
        text, truncated = mail_text(document)
        if len(text) > READ_CHARS:
            text, truncated = text[:READ_CHARS] + '\n……（后面的内容已省略）', True
        result = {'ref': ref, 'subject': header('subject') or row.get('subject', ''), 'from': _who(header('from') or row.get('from'), 1),
                  'to': _who(header('to')), 'cc': _who(header('cc')), 'date': str(row.get('date') or header('date'))[:16],
                  'attachments': [a.name for a in document.attachments][:20], 'text': text or '（没有正文）', 'truncated': truncated}
        attached = attachment_texts(document, self._pdf_text, READ_CHARS)
        if attached:
            result['attachmentText'] = [{'name': n, 'text': t, 'truncated': cut} for n, t, cut in attached]
        return result, label

    def find_contact(self, args):
        name = _clip(args.get('name'), 50)
        if not name:
            return {'error': '需要姓名'}, '查找联系人'
        people = [self._person(p) for p in self._match(name)[:5]]
        return {'people': people}, f'查找联系人「{name}」：{len(people)} 人'


# 个人工作助理 (W1): the task center and 待我处理, read-only. Tasks are T12 (their number in
# the task center); their fields are data like mail text, never instructions.
TASK_ROWS = 40
TASK_VIEWS = ('open', 'done', 'canceled', 'all')
TASK_SPECS = [
    {'type': 'function', 'function': {
        'name': 'list_tasks',
        'description': '列出任务中心的任务：编号（如 T12）、系统、内容、状态、优先级、计划完成日期、是否已到期、负责人、下一步。'
                       'mine=true 只列负责人是用户本人或为空的。最多 40 条。',
        'parameters': {'type': 'object', 'properties': {
            'view': {'type': 'string', 'enum': list(TASK_VIEWS), 'description': 'open 未完成（默认）、done 已完成、canceled 已取消、all 全部'},
            'query': {'type': 'string', 'description': '关键词，空格分开，每个词都要在任务的某个字段里出现；可空'},
            'mine': {'type': 'boolean', 'description': '只列用户自己负责的'}}}}},
    {'type': 'function', 'function': {
        'name': 'read_task',
        'description': '读一条任务的全部字段、最近 5 条进展和关联的邮件（邮件编号可以再用 read_mail 阅读）。',
        'parameters': {'type': 'object', 'properties': {
            'ref': {'type': 'string', 'description': '任务编号，如 T12'}}, 'required': ['ref']}}},
]
PLAN_SPEC = {'type': 'function', 'function': {
    'name': 'plan_tasks',
    'description': '提议修改任务：只生成计划，显示为预览卡片，用户点「执行」后才写入。返回每项操作能否执行、会改哪些字段。'
                   '操作：progress 记录进展（text 必填，可同时改 fields 里的 status、next、blockers）；update 修改字段（fields）；'
                   'complete 标记完成并归档（note 可写交付结果）；cancel 取消并归档（reason 必填）；reopen 重新打开已归档任务（status、reason）；'
                   'create 新建任务（fields.content 必填，不填 task；可给 ref：N1、N2…，同一计划里其他操作的 text / fields 写 #N1 就指这条新任务，执行时换成真实编号）。字段名：system、content、priority（高/中/低）、status、latest、owner、'
                   'start、due、end、blockers、next、updated、acceptance。未完成状态只有：未开始、待处理、进行中、受阻、待验收。',
    'parameters': {'type': 'object', 'properties': {
        'summary': {'type': 'string', 'description': '一句话说明这次要做什么，显示在卡片上'},
        'actions': {'type': 'array', 'description': '最多 10 项；每条任务在一个计划里最多出现一次', 'items': {'type': 'object', 'properties': {
            'op': {'type': 'string', 'enum': ['progress', 'update', 'complete', 'cancel', 'reopen', 'create']},
            'task': {'type': 'string', 'description': '任务编号，如 T12；必须是 list_tasks / read_task 返回过的'},
            'ref': {'type': 'string', 'description': 'create 的占位编号 N1、N2…'},
            'text': {'type': 'string'}, 'date': {'type': 'string', 'description': 'YYYY-MM-DD，用户没说就留空（默认今天）'},
            'fields': {'type': 'object'}, 'note': {'type': 'string'}, 'reason': {'type': 'string'}, 'status': {'type': 'string'}},
            'required': ['op']}}}, 'required': ['actions']}}}
PLANS_PER_QUESTION = 3
# 助理·起草 (W3): the task's conversation, recipient groups (names only) and draft plans.
DRAFT_SPECS = [
    {'type': 'function', 'function': {
        'name': 'task_thread',
        'description': '任务关联邮件所在的会话，按时间从早到晚：每封的编号、发件人、日期、是否用户本人发的。另给出最后一封、最后一封别人发的、'
                       '之后用户是否回复过，以及会话里的外部联系人（编号如 P2、姓名、域名）。',
        'parameters': {'type': 'object', 'properties': {
            'task': {'type': 'string', 'description': '任务编号，如 T12'}}, 'required': ['task']}}},
    {'type': 'function', 'function': {
        'name': 'list_groups',
        'description': '用户的收件人组：编号（如 G1）、组名、人数、主送/抄送人数、成员的域名、外部人数；给了任务时按与任务会话参与人的重合人数排序。不含邮箱地址。',
        'parameters': {'type': 'object', 'properties': {
            'task': {'type': 'string', 'description': '任务编号，可空'}}}}},
    {'type': 'function', 'function': {
        'name': 'draft_reply',
        'description': '提议起草一封回复：只生成计划，显示为卡片，用户点「起草」后才打开写信窗口，由用户检查后自己发送。',
        'parameters': {'type': 'object', 'properties': {
            'mail': {'type': 'string', 'description': '要回复的邮件编号，如 M3（通常是 task_thread 给出的最后一封别人发的）'},
            'purpose': {'type': 'string', 'description': '目的，一句话，如「询问 LDAP 对接还缺的端口和 Base DN」「告知已完成」'},
            'points': {'type': 'array', 'items': {'type': 'string'}, 'description': '要写的要点，最多 8 条，只写用户说的和任务、会话里有的'},
            'group': {'type': 'string', 'description': '用户指定的收件人组编号（list_groups 返回的 G 编号），没指定就不填'},
            'all': {'type': 'boolean', 'description': '全部回复'},
            'send_at': {'type': 'string', 'description': '用户要求定时发送时填：YYYY-MM-DD HH:MM（本机时间）；只设在草稿上，仍由用户点发送'},
            'task': {'type': 'string', 'description': '相关任务编号，如 T12'}}, 'required': ['mail', 'purpose']}}},
    {'type': 'function', 'function': {
        'name': 'draft_new',
        'description': '提议起草一封新邮件（例如发给任务相关的客户）：只生成计划，用户点「起草」后才打开写信窗口。to 和 group 至少一个。',
        'parameters': {'type': 'object', 'properties': {
            'to': {'type': 'array', 'items': {'type': 'string'}, 'description': '收件人：task_thread 或 find_contact 返回的联系人编号，如 P2'},
            'group': {'type': 'string', 'description': '收件人组编号，如 G1'},
            'subject': {'type': 'string'}, 'purpose': {'type': 'string'},
            'points': {'type': 'array', 'items': {'type': 'string'}},
            'send_at': {'type': 'string', 'description': '定时发送：YYYY-MM-DD HH:MM（本机时间），没要求就不填'},
            'task': {'type': 'string', 'description': '相关任务编号'}}, 'required': ['subject', 'purpose']}}},
    {'type': 'function', 'function': {
        'name': 'suggest_links',
        'description': '任务没有关联邮件时，提议几封候选邮件：显示为卡片，用户勾选后点「关联」才关联到任务。只提议，不关联。',
        'parameters': {'type': 'object', 'properties': {
            'task': {'type': 'string', 'description': '任务编号，如 T12'},
            'mails': {'type': 'array', 'items': {'type': 'string'}, 'description': 'search_mail 返回的邮件编号，最多 5 封，最可能相关的在前'}},
            'required': ['task', 'mails']}}},
]
LINK_CANDIDATES = 5  # W4: mails offered for a task without linked mail.
# 操作类工具 (A3): mail operations proposed as a plan; the user runs (and can undo) them.
MAIL_ACTIONS = {'read': '标为已读', 'unread': '标为未读', 'flag': '加星标', 'unflag': '取消星标', 'move': '移动', 'trash': '移到已删除'}
MAIL_PLAN_LIMIT = 200
MAIL_SPEC = {'type': 'function', 'function': {
    'name': 'plan_mail',
    'description': '提议对邮件做操作：只生成计划，显示为预览卡片（动作、封数、前几封主题、目标文件夹），用户点「执行」后才操作，执行后可撤销。'
                   'action：read 标为已读、unread 标为未读、flag 加星标、unflag 取消星标、move 移动到文件夹（folder 必填）、trash 移到已删除（不是彻底删除）。'
                   '邮件用 mails（search_mail 等返回过的编号）或 search（search_mail 返回的 search 编号，代表服务器上这次搜索的全部结果），二选一；'
                   f'一次最多 {MAIL_PLAN_LIMIT} 封。本地存档里的邮件不能操作。',
    'parameters': {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': list(MAIL_ACTIONS)},
        'mails': {'type': 'array', 'items': {'type': 'string'}, 'description': '邮件编号，如 M3'},
        'search': {'type': 'string', 'description': 'search_mail 返回的 search 编号，如 S1'},
        'folder': {'type': 'string', 'description': 'move 的目标文件夹名称'},
        'summary': {'type': 'string', 'description': '一句话说明要做什么，显示在卡片上'}}, 'required': ['action']}}}
PENDING_SPEC = {'type': 'function', 'function': {
    'name': 'pending_mail',
    'description': '「要我做」（原「待我处理」）：AI 读过新邮件后认为需要用户本人做事的邮件（要做什么、截止日期、项目），已经关联到任务的不列出。',
    'parameters': {'type': 'object', 'properties': {}}}}

# 工作记忆 (M2) and 催办 (M3): read-only like pending_mail.
MEMORY_SPEC = {'type': 'function', 'function': {
    'name': 'memory_search',
    'description': '查工作记忆：鸿雁读邮件时记下的项目事实（进展 status、决定 decision、分工 owner、关键日期 date），每条带日期和出处邮件编号。'
                   '按项目名和 / 或关键词查；history=true 时也列出已被更新取代的旧事实（看事情怎么变过来的）。',
    'parameters': {'type': 'object', 'properties': {
        'project': {'type': 'string', 'description': '项目名（用户名单里的名字或别名），可空'},
        'text': {'type': 'string', 'description': '关键词，空格分开，可空'},
        'history': {'type': 'boolean'}}}}}
FOLLOWUP_SPEC = {'type': 'function', 'function': {
    'name': 'followups',
    'description': '催办：用户最近 30 天发出、在主送里提了问、几天还没有人回复的邮件（编号、主题、收件人、已等几天）。',
    'parameters': {'type': 'object', 'properties': {}}}}
MEMORY_ROWS = 20
# 关于我 (Me2): 「记住……」 in 问鸿雁 becomes a card; the user's 记住 writes it.
REMEMBER_GROUPS = ('role', 'projects', 'people', 'ways')
REMEMBER_SPEC = {'type': 'function', 'function': {
    'name': 'remember_me',
    'description': '提议把一条关于用户本人的长期情况记进「关于我」（岗位、项目和系统里做什么不做什么、和某人的关系、工作习惯）。'
                   '只生成卡片，用户点「记住」才写入。一次性的待办不要用它。',
    'parameters': {'type': 'object', 'properties': {
        'group': {'type': 'string', 'enum': list(REMEMBER_GROUPS), 'description': 'role 我的岗位、projects 项目和系统、people 人、ways 工作方式'},
        'text': {'type': 'string', 'description': '一句话，不超过 100 字'},
        'replaces': {'type': 'string', 'description': '更新了「关于用户」里的哪一条：它的 A 编号，如 A3；新的一条就不填'}},
        'required': ['group', 'text']}}}
# G1d: what each tool may do. READ runs at once and changes nothing; CARD only proposes a plan
# the user confirms (and can undo) in the app. No tool writes or sends by itself, and a new
# tool must be declared here (tests/test_ai_ask.py checks every spec).
READ, CARD = 'read', 'card'
TOOL_KINDS = {'search_mail': READ, 'read_mail': READ, 'find_contact': READ, 'list_tasks': READ, 'read_task': READ,
              'pending_mail': READ, 'task_thread': READ, 'list_groups': READ, 'memory_search': READ, 'followups': READ,
              'plan_tasks': CARD, 'draft_reply': CARD, 'draft_new': CARD, 'suggest_links': CARD, 'plan_mail': CARD,
              'remember_me': CARD}
MCP_SPECS = [s for s in SPECS + TASK_SPECS if TOOL_KINDS[s['function']['name']] == READ]  # X1: read-only, outside the app.


class AssistantTools(MailboxTools):
    """MailboxTools plus the task center. tasks(view, query) -> task dicts (center fields,
    'overdue'); task(id) -> center.get(id) with each link's 'row' (a summary row or None);
    mine(task) -> bool; pending() -> [summary row with 'card'], or None when the 理解层 is off;
    plan(actions) -> center.preview with 'message' on errors (W2), or None: read-only;
    thread(id) -> {'rows': [summary row + 'mine', 'people'], 'internal': domains} and
    groups(addresses) -> [group without addresses] for drafting (W3), or None;
    actions: mail operations may be proposed (A3, 设置 → 允许 AI 提议邮件操作), with
    folders['targets'] ([{'id', 'name', 'role'}], the user's own) and folders['trash']."""

    def __init__(self, *args, tasks, task, mine, pending=None, plan=None, thread=None, groups=None, actions=False, now=datetime.now,
                 memory=None, followups=None, about=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tasks, self._task, self._mine, self._pending, self._plan = tasks, task, mine, pending, plan
        self._memory, self._followups, self._about = memory, followups, about  # about() -> {'A1': {'id', 'text'}}
        self._thread, self._groups, self._actions, self._now = thread, groups, actions, now
        self.task_refs = {}  # T12 -> {'id', 'system', 'content', 'status', 'due'}
        self.plans, self._plan_count = [], 0  # This question's proposals: {'ref', 'kind', 'summary', ...}.
        self.group_refs, self.mail_task, self.thread_people = {}, {}, {}  # G1 -> id; M3 -> task id; T12 -> addresses
        self.specs = SPECS + TASK_SPECS + ([PENDING_SPEC] if pending else []) + ([PLAN_SPEC] if plan else []) + \
            (DRAFT_SPECS if thread and groups else []) + ([MAIL_SPEC] if actions else []) + \
            ([MEMORY_SPEC] if memory else []) + ([FOLLOWUP_SPEC] if followups else []) + ([REMEMBER_SPEC] if about else [])

    def begin(self, allowed=()):
        super().begin(allowed)
        self.plans = []

    def allow_actions(self, on):
        """The setting as it is for this question: plan_mail offered or not."""
        self._actions = bool(on)
        self.specs = [s for s in self.specs if s is not MAIL_SPEC] + ([MAIL_SPEC] if on else [])

    def run(self, name, arguments):
        drafting = ('task_thread', 'list_groups', 'draft_reply', 'draft_new', 'suggest_links')
        if name not in ('list_tasks', 'read_task', 'pending_mail', 'plan_tasks', 'plan_mail', 'memory_search', 'followups', 'remember_me') + drafting:
            return super().run(name, arguments)
        try:
            args = json.loads(arguments or '{}')
        except ValueError:
            args = None
        if not isinstance(args, dict):
            return {'error': '参数不是有效的 JSON 对象'}, '工具参数无效'
        if name == 'pending_mail' and not self._pending:
            return {'error': '没有开启「理解新邮件」，没有要我做的列表'}, '要我做（未开启）'
        if name in ('memory_search', 'followups') and not (self._memory if name == 'memory_search' else self._followups):
            return {'error': '没有开启「理解新邮件」，没有工作记忆'}, '工作记忆（未开启）'
        if name == 'plan_tasks' and not self._plan:
            return {'error': '现在只能查看任务，不能修改'}, '拟定任务操作（不可用）'
        if name in drafting and not (self._thread and self._groups):
            return {'error': '现在不能起草邮件'}, '起草（不可用）'
        if name == 'remember_me' and not self._about:
            return {'error': '现在不能记进「关于我」，请告诉用户在 设置 → 关于我 里添加'}, '记住（不可用）'
        if name == 'plan_mail' and not self._actions:
            return {'error': '用户没有允许 AI 提议邮件操作，请告诉用户自己在列表里操作'}, '拟定邮件操作（未开启）'
        return {'list_tasks': self.list_tasks, 'read_task': self.read_task, 'pending_mail': self.pending_mail,
                'plan_tasks': self.plan_tasks, 'task_thread': self.task_thread, 'list_groups': self.list_groups,
                'draft_reply': self.draft_reply, 'draft_new': self.draft_new, 'suggest_links': self.suggest_links,
                'plan_mail': self.plan_mail, 'memory_search': self.memory_search, 'followups': self.followups,
                'remember_me': self.remember_me}[name](args)

    def remember_me(self, args):
        """Me2: a 关于我 line the user asked to keep, shown as a card; nothing is written here."""
        group, text = args.get('group'), _clip(args.get('text'), 120)
        if group not in REMEMBER_GROUPS or not text:
            return {'error': 'group 是 role / projects / people / ways 之一，text 不能为空'}, '提议记住（参数无效）'
        known, ref = self._about() or {}, _clip(args.get('replaces'), 6).upper()
        if ref and ref not in known:
            return {'error': f'{ref} 不是「关于用户」里的编号；新的一条就不填 replaces'}, '提议记住（编号无效）'
        was = known.get(ref) or {}
        plan = self._new_plan('remember', '记进「关于我」', group=group, text=text, replaces=was.get('id', ''), was=was.get('text', ''))
        if plan is None:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划'}, '提议记住（已达上限）'
        return ({'plan': plan['ref'], 'note': '已作为卡片显示，用户点「记住」才写入「关于我」。回答里一两句话说明，不要说已经记住。'},
                f'提议记住：{_clip(text, 30)}')

    def add_task(self, task):
        """A task the user brought in (the one selected in 任务), or one a tool returned -> its ref."""
        ref = f'T{task["id"]}'
        self.task_refs[ref] = {k: task.get(k) or '' for k in ('id', 'system', 'content', 'status', 'due')}
        return ref

    def task_note(self, ref):
        t = self.task_refs[ref]
        return f'{ref}｜{_clip(t["system"], 30)}｜{_clip(t["content"], 80)}｜{t["status"]}' + (f'｜计划完成 {t["due"]}' if t['due'] else '')

    def _task_row(self, t):
        return {'ref': self.add_task(t), 'system': _clip(t.get('system'), 40), 'content': _clip(t.get('content'), 80),
                'status': t.get('status', ''), 'priority': t.get('priority', ''), 'due': t.get('due', ''), 'overdue': bool(t.get('overdue')),
                'owner': _clip(t.get('owner'), 40), 'next': _clip(t.get('next'), 80), 'mine': self._mine(t)}

    def list_tasks(self, args):
        view = args.get('view') if args.get('view') in TASK_VIEWS else 'open'
        query = _clip(args.get('query'), 100)
        mine = args.get('mine') is True
        found = [t for t in self._tasks(view, query) if not mine or self._mine(t)]
        # Overdue first, then those with a due date (soonest first), then the rest by number.
        found.sort(key=lambda t: (not t.get('overdue'), not t.get('due'), t.get('due') or '', t['id']))
        rows = [self._task_row(t) for t in found[:TASK_ROWS]]
        label = '查看任务' + {'open': '', 'done': '（已完成）', 'canceled': '（已取消）', 'all': '（全部）'}[view] + \
            (f'「{query}」' if query else '') + ('，只看我负责的' if mine else '')
        result = {'total': len(found), 'rows': rows}
        if len(found) > len(rows):
            result['hint'] = f'只列出前 {TASK_ROWS} 条，可以加关键词缩小范围'
        return result, f'{label}：{len(found)} 条'

    def read_task(self, args):
        ref = _clip(args.get('ref'), 10).upper()
        if not re.fullmatch(r'T\d{1,5}', ref):
            return {'error': '任务编号的格式是 T 加数字，如 T12'}, f'读任务 {ref}（编号无效）'
        try:
            d = self._task(int(ref[1:]))
        except AppError as error:
            if error.code != 'TASK_NOT_FOUND':
                raise
            return {'error': f'没有任务 {ref}'}, f'读任务 {ref}（不存在）'
        t = d['task']
        self.add_task(t)
        fields = {k: _clip(v, 600) for k, v in t.items() if k not in ('id', 'history') and str(v or '').strip()}
        events = [{'date': str(e.get('date') or e.get('at') or '')[:10], 'kind': e.get('kind', ''), 'text': _clip(e.get('text') or e.get('note'), 300)}
                  for e in d.get('events', []) if e.get('text') or e.get('note')][:5]
        mails = []
        for link in d.get('links', [])[:10]:
            row = link.get('row') or {'id': link.get('mid'), 'subject': link.get('subject'), 'date': link.get('date'), 'from': ''}
            if row.get('id'):
                mails.append({'ref': self._ref(row), 'subject': _clip(row.get('subject'), 120) or '（无主题）',
                              'from': _who(row.get('from'), 1), 'date': str(row.get('date') or '')[:16]})
        return ({'ref': ref, 'fields': fields, 'overdue': bool(d.get('overdue')), 'mine': self._mine(t), 'progress': events, 'mails': mails},
                f'读任务 {ref}「{_clip(t.get("content"), 30)}」')

    def pending_mail(self, args):
        rows = self._pending()
        out = [{'ref': self._ref(r), 'subject': _clip(r.get('subject'), 120) or '（无主题）', 'from': _who(r.get('from'), 1),
                'date': str(r.get('date') or '')[:16], 'ask': _clip(r['card'].get('ask'), 120), 'gist': _clip(r['card'].get('gist'), 120),
                'due': r['card'].get('due') or '', 'project': _clip(r['card'].get('project'), 40)} for r in rows[:30]]
        return {'total': len(rows), 'rows': out}, f'要我做：{len(rows)} 封'

    def memory_search(self, args):
        """callback(project, text, history) -> (project as listed, [fact with 'rows']) or None."""
        project, text = _clip(args.get('project'), 30), _clip(args.get('text'), 100)
        found = self._memory(project, text, args.get('history') is True)
        if found is None:
            return {'error': '没有开启「理解新邮件」，没有工作记忆'}, '工作记忆（未开启）'
        name, facts = found
        if project and not name:
            return {'error': f'项目名单里没有「{project}」，可以不填项目只用关键词查', 'total': 0}, f'查工作记忆「{project}」：不在名单里'
        rows = [{'project': f['project'], 'type': f['type'], 'text': f['text'], 'date': f['date'],
                 **({'state': '已被取代' if f['state'] == 'superseded' else '待确认'} if f['state'] != 'current' else {}),
                 'sources': [self._ref(r) for r in f['rows'][:3]]} for f in facts[:MEMORY_ROWS]]
        label = '查工作记忆' + (f'「{name}」' if name else '') + (f'「{text}」' if text else '')
        return {'total': len(facts), 'facts': rows}, f'{label}：{len(facts)} 条'

    def followups(self, args):
        """callback() -> [sent summary row with 'waiting' (days) and 'toNames']."""
        rows = self._followups()
        out = [{'ref': self._ref(r), 'subject': _clip(r.get('subject'), 120) or '（无主题）', 'to': _clip(r.get('toNames'), 120),
                'date': str(r.get('date') or '')[:16], 'waiting_days': r.get('waiting', 0)} for r in rows[:20]]
        return {'total': len(rows), 'rows': out}, f'等回复：{len(rows)} 封'

    def plan_tasks(self, args):
        """A proposal only: checked like the task center's preview and kept for the page's
        card; nothing is written until the user presses 执行 there."""
        from coremail_desktop.tasks.assistant import MAX_ACTIONS, check_actions
        items = args.get('actions')
        if not isinstance(items, list) or not items or len(items) > MAX_ACTIONS:
            return {'error': f'actions 需要 1–{MAX_ACTIONS} 项操作'}, '拟定任务操作（参数无效）'
        if len(self.plans) >= PLANS_PER_QUESTION:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划，请把操作合并到一个计划里'}, '拟定任务操作（已达上限）'
        actions, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                return {'error': '每项操作必须是对象'}, '拟定任务操作（参数无效）'
            action = {k: v for k, v in item.items() if k not in ('task', 'id', 'mails')}  # mails: set by the app, never the model.
            if item.get('op') != 'create':
                ref = _clip(item.get('task') or item.get('id'), 10).upper()
                ref = 'T' + ref if ref.isdigit() else ref
                if ref not in self.task_refs:
                    return ({'error': f'任务 {ref or "（没有编号）"} 没有在这次对话中查到。请先用 list_tasks 或 read_task 找到它；'
                                      '有两条以上可能时先问用户'}, f'拟定任务操作（{ref or "编号"}未知）')
                if ref in seen:
                    return {'error': f'{ref} 在计划里出现了两次，请合并成一项'}, '拟定任务操作（编号重复）'
                seen.add(ref)
                action['id'] = int(ref[1:])
            actions.append(action)
        try:
            actions = check_actions(actions)
        except AppError:
            return {'error': '操作的格式不对，请按工具说明重写'}, '拟定任务操作（参数无效）'
        if len(actions) < len(items):
            return {'error': 'op 只能是 progress、update、complete、cancel、reopen、create'}, '拟定任务操作（参数无效）'
        try:
            preview = self._plan(actions)
        except AppError as error:
            return {'error': '任务中心还不能修改（' + error.code + '），请告诉用户先在任务中心完成导入'}, '拟定任务操作（任务中心不可用）'
        ref = self._new_plan('tasks', args.get('summary'), actions=actions, preview=preview)['ref']
        out = [{'op': p['op'], 'task': f'T{p["id"]}' if p.get('id') and p['op'] != 'create' else '',
                'ok': not p.get('error') and bool(p.get('changes')), 'changes': sorted(p.get('changes') or ()),
                **({'problem': p.get('message') or p['error']} if p.get('error') else
                   {} if p.get('changes') else {'problem': '没有需要修改的内容'})} for p in preview]
        ready = sum(1 for o in out if o['ok'])
        note = ('计划已作为预览卡片显示在你的回答下面，用户点「执行」后才会写入。回答里简短说明计划，请用户确认；不要说已经改好了。'
                if ready else '这个计划没有可以执行的操作：在回答里说明原因，需要时向用户补问。')
        return {'plan': ref, 'items': out, 'note': note}, f'拟定任务操作：{ready} 项待确认' + (f'，{len(out) - ready} 项不能执行' if len(out) > ready else '')

    def _task_ref(self, value):
        ref = _clip(value, 10).upper()
        ref = 'T' + ref if ref.isdigit() else ref
        return ref if ref in self.task_refs else None

    def _new_plan(self, kind, summary, **data):
        if len(self.plans) >= PLANS_PER_QUESTION:
            return None
        self._plan_count += 1
        plan = {'ref': f'P{self._plan_count}', 'kind': kind, 'summary': _clip(summary, 120), 'actions': [], **data}
        self.plans.append(plan)
        return plan

    @staticmethod
    def _points(value):
        return [p for p in (_clip(x, 120) for x in (value if isinstance(value, list) else [])[:8] if isinstance(x, str)) if p]

    def task_thread(self, args):
        ref = self._task_ref(args.get('task'))
        if ref is None:
            return {'error': '任务编号没有在这次对话中查到，请先用 list_tasks 或 read_task'}, '查看任务会话（编号未知）'
        found = self._thread(int(ref[1:]))
        rows, internal = found['rows'], found['internal']
        label = f'查看 {ref} 的邮件会话'
        if not rows:
            return ({'task': ref, 'mails': [], 'hint': '这条任务没有关联邮件（或关联的邮件不在本机缓存里）。按任务内容用 search_mail 搜索，'
                                                       f'挑最多 {LINK_CANDIDATES} 封最可能相关的调用 suggest_links，请用户勾选后关联；没找到就告诉用户。'},
                    label + '：没有关联邮件')
        mails = []
        for r in rows:
            m = self._ref(r)
            self.mail_task[m] = int(ref[1:])
            mails.append({'ref': m, 'subject': _clip(r.get('subject'), 120) or '（无主题）', 'from': _who(r.get('from'), 1),
                          'date': str(r.get('date') or '')[:16], 'mine': bool(r['mine'])})
        others = [m for m in mails if not m['mine']]
        last_other = others[-1] if others else None
        replied = bool(last_other) and any(m['mine'] for m in mails[mails.index(last_other) + 1:])
        people, seen = [], set()
        for r in rows:
            for p in r['people']:
                key = p['email'].casefold()
                if key in seen:
                    continue
                seen.add(key)
                if p['email'].rpartition('@')[2].casefold() not in internal:
                    people.append(self._person(p))
        self.thread_people[ref] = sorted(seen)
        return ({'task': ref, 'mails': mails, 'last': mails[-1]['ref'], 'last_from_other': last_other and last_other['ref'],
                 'replied_after_it': replied, 'external_contacts': people[:10]},
                f'{label}：{len(mails)} 封')

    def list_groups(self, args):
        ref = self._task_ref(args.get('task')) if args.get('task') else None
        groups = self._groups(self.thread_people.get(ref, ()))
        out = []
        for g in groups[:30]:
            gref = next((r for r, i in self.group_refs.items() if i == g['id']), None) or f'G{len(self.group_refs) + 1}'
            self.group_refs[gref] = g['id']
            out.append({'ref': gref, 'name': _clip(g['name'], 40), 'count': g['count'], 'to': g['to'], 'cc': g['cc'],
                        'domains': g['domains'], 'external': g['external'], **({'overlap': g['overlap']} if ref else {})})
        return {'groups': out}, f'查看收件人组：{len(out)} 个'

    def _group(self, value):
        """-> (group id or '', problem or None)."""
        if not value:
            return '', None
        gref = _clip(value, 10).upper()
        if gref not in self.group_refs:
            return '', {'error': f'没有收件人组 {gref}，请先用 list_groups 查到组编号；组名找不到或不唯一时问用户'}
        return self.group_refs[gref], None

    def _draft_task(self, args, mail=None):
        ref = self._task_ref(args.get('task')) if args.get('task') else None
        return int(ref[1:]) if ref else self.mail_task.get(mail, 0)

    def draft_reply(self, args):
        mref = _clip(args.get('mail'), 10).upper()
        row = self.mails.get(mref)
        if row is None:
            return {'error': f'没有邮件编号 {mref}，请先用 task_thread 或 search_mail 找到要回复的邮件'}, '起草回复（编号无效）'
        group, problem = self._group(args.get('group'))
        if problem:
            return problem, '起草回复（收件人组未知）'
        purpose, points = _clip(args.get('purpose'), 100), self._points(args.get('points'))
        if not purpose:
            return {'error': '需要 purpose（目的）'}, '起草回复（参数无效）'
        at, problem = self._send_at(args.get('send_at'))
        if problem:
            return problem, '起草回复（定时无效）'
        task = self._draft_task(args, mref)
        people = self.thread_people.get(f'T{task}', ()) if task else ()
        sender = row.get('fromEmail') or ''
        plan = self._new_plan('draft', f'回复《{_clip(row.get("subject"), 40) or "（无主题）"}》', reply=True, mid=row['id'],
                              mode='reply_all' if args.get('all') is True else 'reply', group=group, task=task, people=list(people),
                              subject=row.get('subject') or '', purpose=purpose, points=points, scheduleAt=at,
                              mail={'ref': mref, 'subject': row.get('subject') or '（无主题）',
                                    'fromName': row.get('fromName') or _who(row.get('from'), 1).partition('（')[0],
                                    'domain': sender.rpartition('@')[2], 'date': str(row.get('date') or '')[:16]})
        if plan is None:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划'}, '起草回复（已达上限）'
        return ({'plan': plan['ref'], 'note': '起草计划已作为卡片显示，用户可以改要点和收件人（含收件人组），点「起草」后才打开写信窗口，'
                                              '由用户自己发送。回答里一两句话说明，不要说已经写好或发出。'},
                f'拟定回复：{mref}' + ('（收件人组）' if group else ''))

    def draft_new(self, args):
        refs = [_clip(r, 10).upper() for r in (args.get('to') if isinstance(args.get('to'), list) else [])[:10]]
        unknown = [r for r in refs if r not in self.people]
        if unknown:
            return {'error': f'联系人编号 {"、".join(unknown)} 无效：收件人只能用 task_thread 或 find_contact 返回的编号'}, '起草新邮件（收件人无效）'
        group, problem = self._group(args.get('group'))
        if problem:
            return problem, '起草新邮件（收件人组未知）'
        subject, purpose = _clip(args.get('subject'), 200), _clip(args.get('purpose'), 100)
        if not refs and not group:
            return {'error': 'to 和 group 至少要有一个'}, '起草新邮件（没有收件人）'
        if not subject or not purpose:
            return {'error': '需要 subject 和 purpose'}, '起草新邮件（参数无效）'
        at, problem = self._send_at(args.get('send_at'))
        if problem:
            return problem, '起草新邮件（定时无效）'
        task = self._draft_task(args)
        contacts = [{'name': self.person_names.get(self.people[r], ''), 'email': self.people[r]} for r in dict.fromkeys(refs)]
        plan = self._new_plan('draft', f'新邮件《{subject}》', reply=False, contacts=contacts, group=group, task=task,
                              people=list(self.thread_people.get(f'T{task}', ())), subject=subject, purpose=purpose,
                              points=self._points(args.get('points')), scheduleAt=at)
        if plan is None:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划'}, '起草新邮件（已达上限）'
        return ({'plan': plan['ref'], 'note': '起草计划已作为卡片显示，用户勾选收件人后点「起草」才打开写信窗口，由用户自己发送。不要说已经写好或发出。'},
                f'拟定新邮件《{_clip(subject, 30)}》')

    def suggest_links(self, args):
        """W4: candidates for a task without linked mail; the user picks them on the card."""
        ref = self._task_ref(args.get('task'))
        if ref is None:
            return {'error': '任务编号没有在这次对话中查到，请先用 list_tasks 或 read_task'}, '提议关联邮件（编号未知）'
        refs = list(dict.fromkeys(_clip(r, 10).upper() for r in (args.get('mails') if isinstance(args.get('mails'), list) else [])))
        unknown = [r for r in refs if r not in self.mails]
        if not refs or len(refs) > LINK_CANDIDATES or unknown:
            return ({'error': f'mails 需要 1–{LINK_CANDIDATES} 个 search_mail 返回过的邮件编号' + (f'；{"、".join(unknown)} 无效' if unknown else '')},
                    '提议关联邮件（参数无效）')
        mails = [{'ref': r, 'mid': self.mails[r]['id'], 'subject': self.mails[r].get('subject') or '（无主题）',
                  'fromName': self.mails[r].get('fromName') or _who(self.mails[r].get('from'), 1).partition('（')[0],
                  'date': str(self.mails[r].get('date') or '')[:16]} for r in refs]
        plan = self._new_plan('link', f'给 #{ref[1:]} 关联邮件', task=int(ref[1:]), mails=mails)
        if plan is None:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划'}, '提议关联邮件（已达上限）'
        return ({'plan': plan['ref'], 'note': '候选已作为卡片显示，用户勾选后点「关联」才关联。回答里一两句话说明，不要说已经关联；关联之前不要起草回复。'},
                f'提议关联邮件：{len(mails)} 封候选')

    def _send_at(self, value):
        """定时 on a draft (A3): -> (epoch ms or None, problem or None). Local time, from
        5 minutes to a year ahead; the user still presses 发送."""
        value = _clip(value, 20)
        if not value:
            return None, None
        try:
            at = datetime.strptime(value, '%Y-%m-%d %H:%M')
        except ValueError:
            return None, {'error': 'send_at 的格式是 YYYY-MM-DD HH:MM'}
        ahead = (at - self._now()).total_seconds()
        if not 300 <= ahead <= 366 * 86400:
            return None, {'error': '定时发送的时间要在 5 分钟之后、一年之内；时间已过就问用户'}
        return int(at.timestamp() * 1000), None

    def _mail_rows(self, args):
        """The mails of a plan: refs, or a search's whole server result -> (rows, problem)."""
        refs = list(dict.fromkeys(_clip(r, 10).upper() for r in (args.get('mails') if isinstance(args.get('mails'), list) else [])[:MAIL_PLAN_LIMIT + 1]))
        sref = _clip(args.get('search'), 10).upper()
        if bool(refs) == bool(sref):
            return None, {'error': 'mails 和 search 必须给一个，且只给一个'}
        if refs:
            unknown = [r for r in refs if r not in self.mails]
            if unknown:
                return None, {'error': f'邮件编号 {"、".join(unknown[:5])} 无效：只能用这次对话里工具返回过的编号'}
            if len(refs) > MAIL_PLAN_LIMIT:
                return None, {'error': f'一次最多 {MAIL_PLAN_LIMIT} 封'}
            return [self.mails[r] for r in refs], None
        search = self.searches.get(sref)
        if search is None:
            return None, {'error': f'没有搜索 {sref}：先用 search_mail 搜索，再用它返回的 search 编号'}
        if search['total'] > MAIL_PLAN_LIMIT:
            return None, {'error': f'{sref} 有 {search["total"]} 封，一次最多 {MAIL_PLAN_LIMIT} 封：请加关键词、发件人或日期缩小范围，或请用户在列表里操作'}
        rows = []
        while len(rows) < search['total']:
            page = self._search(search['keywords'], len(rows), 100, search['filters'])['rows']
            if not page:
                break
            rows += page
        return rows[:MAIL_PLAN_LIMIT], None

    def _target(self, name):
        """move's folder by name -> ({'id', 'name'}, problem): the user's own folders, not
        已发送、草稿箱、已删除 (trash is its own action) or 病毒."""
        targets = [f for f in self.folders.get('targets') or () if f.get('role') not in ('sent', 'drafts', 'trash', 'virus')]
        name = _clip(name, 60).casefold()
        found = [f for f in targets if f['name'].casefold() == name] or [f for f in targets if name and name in f['name'].casefold()]
        if len(found) == 1:
            return found[0], None
        names = '、'.join(f['name'] for f in targets[:30])
        return None, {'error': (f'有几个文件夹都像「{name}」' if found else f'没有文件夹「{name}」') + f'。可以移到：{names}；不确定就问用户'}

    def plan_mail(self, args):
        """A proposal only (A3): the mails, what happens to them and where; the page shows a
        card and the user runs it (the list's batch path) and can undo it."""
        action = args.get('action')
        if action not in MAIL_ACTIONS:
            return {'error': 'action 只能是 ' + '、'.join(MAIL_ACTIONS)}, '拟定邮件操作（参数无效）'
        folder = None
        if action == 'move':
            folder, problem = self._target(args.get('folder'))
            if problem:
                return problem, '拟定邮件操作（文件夹未知）'
        rows, problem = self._mail_rows(args)
        if problem:
            return problem, '拟定邮件操作（邮件无效）'
        local = [r for r in rows if str(r['id']).startswith(('arc:', 'eml:'))]
        rows = [r for r in rows if r not in local]
        if not rows:
            return {'error': '这些都是本地存档里的邮件，不能操作'}, '拟定邮件操作（本地存档）'
        flags = lambda r: r.get('flags') or {}
        already = {'read': lambda r: flags(r).get('read') is True, 'unread': lambda r: flags(r).get('read') is False,
                   'flag': lambda r: flags(r).get('flagged') is True, 'unflag': lambda r: flags(r).get('flagged') is False,
                   'move': lambda r: str(r.get('fid')) == folder['id'], 'trash': lambda r: str(r.get('fid')) == str(self.folders.get('trash'))}[action]
        todo = [r for r in rows if not already(r)]
        what = MAIL_ACTIONS[action] + (f'到「{folder["name"]}」' if folder else '')
        if not todo:
            return {'error': f'这 {len(rows)} 封已经是这样了（{what}），不需要操作'}, '拟定邮件操作（无需操作）'
        names = self.folders.get('names') or {}
        samples = [{'subject': r.get('subject') or '（无主题）', 'fromName': r.get('fromName') or _who(r.get('from'), 1).partition('（')[0],
                    'date': str(r.get('date') or '')[:16], 'folder': names.get(str(r.get('fid')), ''), 'mid': r['id']} for r in todo[:5]]
        plan = self._new_plan('mail', args.get('summary') or f'{what} {len(todo)} 封', op=action, mids=[r['id'] for r in todo],
                              folder=folder['id'] if folder else '', folderName=folder['name'] if folder else '', samples=samples,
                              skipped=len(rows) - len(todo), local=len(local), what=what)
        if plan is None:
            return {'error': f'一次回答最多提出 {PLANS_PER_QUESTION} 个计划，请合并'}, '拟定邮件操作（已达上限）'
        return ({'plan': plan['ref'], 'count': len(todo), 'skipped': len(rows) - len(todo), 'local': len(local),
                 'examples': [_clip(s['subject'], 60) for s in samples[:3]],
                 'note': '计划已作为预览卡片显示，用户点「执行」后才操作，执行后可撤销。回答里一两句话说明封数和动作，请用户确认；不要说已经做了。'},
                f'拟定邮件操作：{what} {len(todo)} 封')
