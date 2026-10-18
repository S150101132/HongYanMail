"""Synthetic in-memory adapter for --demo and offline tests. Never touches the network."""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from coremail_desktop.errors import AppError
from coremail_desktop.models import Folder, MessageSummary

PEOPLE = [('产品协作组', 'product@example.invalid'), ('研发团队', 'rd@example.invalid'),
          ('张晓明', 'zhangxm@example.invalid'), ('李华', 'lihua@example.invalid'),
          ('人力资源部', 'hr@example.invalid'), ('IT 服务台', 'itsupport@example.invalid'),
          ('合作伙伴 · 外部', 'partner@external.example')]
SUBJECTS = ['项目进展与本周计划', '关于设计评审的会议纪要', '【通知】办公网络维护安排', '研发周报 · 第 42 周',
            'Re: 接口联调时间确认', '季度预算申请表（请于周五前反馈）', '新员工入职培训安排', '服务器迁移方案评审']
# One synthetic department mailbox shared with the demo user. Its message ids repeat
# the user's own ('demo0'...) on purpose: the client must keep the two apart.
SHARED_BOX = {'id': 'rd', 'email': 'rd-office@example.invalid', 'name': '研发部公共邮箱', 'send': True, 'rw': True, 'recallable': False}
SHARED_SUBJECTS = ['【部门】服务器采购需求汇总', '外部合作方接口文档更新', '部门例会时间调整', '测试环境账号申请',
                   '【提醒】季度安全检查', '客户问题反馈汇总']
BODY = ('<div style="font-family:Microsoft YaHei;font-size:14px"><p>各位同事：</p>'
        '<p>本周的设计评审已经完成，请查看以下安排：</p>'
        '<table border="1" cellpadding="6" style="border-collapse:collapse"><tr style="background:#eef4ff">'
        '<th>事项</th><th>负责人</th><th>截止</th></tr><tr><td>邮箱工作台交互优化</td><td>张晓明</td><td>10-20</td></tr>'
        '<tr><td>草稿与阅读流程验证</td><td>李华</td><td>10-22</td></tr></table>'
        '<p style="color:#b42318"><b>请注意：</b>周五下午 3 点进行集中演示。</p><p>谢谢！<br>产品协作组</p>'
        '<p style="color:#98a2b3;font-size:12px">（以上内容为合成演示数据）</p></div>')


_DEMO_KEYS = {}


def demo_certificate(email):
    """A throwaway self-signed S/MIME certificate for synthetic addresses (cached per process)."""
    email = email.casefold()
    if email not in _DEMO_KEYS:
        from datetime import datetime, timedelta, timezone
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, email), x509.NameAttribute(NameOID.EMAIL_ADDRESS, email)])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=365))
                .add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email)]), False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), True)
                .sign(key, hashes.SHA256()))
        _DEMO_KEYS[email] = (cert.public_bytes(serialization.Encoding.DER), key, cert)
    return _DEMO_KEYS[email]


OUTLOOK_DEMO_ID = 'demo3'
INVITE_ID, CANCEL_ID = 'demo10', 'demo14'  # Meeting request / cancellation (text/calendar parts).
INVITES = {INVITE_ID: '邀请：设计评审第二轮（演示）', CANCEL_ID: '已取消：服务器迁移方案沟通（演示）'}
_TINY_PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
             b'\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\xa7\xc1\x9b\xb7\x00\x00\x00\x00IEND\xaeB`\x82')


def _outlook_style(message):
    """A long reply-all thread as Outlook sends it: many recipients, signature logos
    as inline cid: images (image001.png...), and two real attachments."""
    del message['To']
    message['To'] = ', '.join(f'"同事{i}" <colleague{i}@example.invalid>' for i in range(1, 4))
    message['Cc'] = ', '.join(f'"抄送成员{i}" <cc{i}@example.invalid>' for i in range(1, 10))
    message.set_content('各位：请查看部署进展。（合成演示数据）')
    logos = ''.join(f'<img src="cid:image00{i}.png@01DC0000.00000000" width="24" height="24" alt="logo"> ' for i in range(1, 6))
    message.add_alternative('<div style="font-family:Calibri"><p>各位：</p><p>附件是本周部署进展和下一步所需支持，请审阅。</p>'
                            f'<p>谢谢！<br>项目经理</p><p>{logos}</p><p style="color:#98a2b3">（合成演示数据）</p></div>', subtype='html')
    html = message.get_payload()[1]
    for i in range(1, 6):
        html.add_related(_TINY_PNG, maintype='image', subtype='png', cid=f'<image00{i}.png@01DC0000.00000000>',
                         filename=f'image00{i}.png', disposition='inline')
    message.add_attachment(b'%PDF-1.4 demo', maintype='application', subtype='pdf', filename='部署进展报告.pdf')
    message.add_attachment(b'PK demo', maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           filename='支持需求清单.xlsx')
    return bytes(message)


class DemoAdapter:
    supports_reply_headers = True
    supports_encrypted_submission = False

    def __init__(self):
        now = datetime(2026, 10, 17, 10, 30)
        self.folders = {'1': '收件箱', '2': '草稿箱', '3': '已发送', '4': '已删除', '5': '垃圾邮件', '100': '项目归档'}
        self.rows = {}
        for i in range(140):
            name, addr = PEOPLE[i % len(PEOPLE)]
            fid = '1' if i < 120 else ('3' if i < 132 else '100')
            when = now - timedelta(hours=i * 5 + (i % 3))
            self.rows[f'demo{i}'] = {'id': f'demo{i}', 'fid': fid, 'subject': SUBJECTS[i % len(SUBJECTS)] + '（演示）',
                                     'from': f'"{name}" <{addr}>' if fid != '3' else '"演示用户" <demo@example.invalid>',
                                     'to': 'demo@example.invalid' if fid != '3' else f'"{name}" <{addr}>',
                                     'date': when.strftime('%Y-%m-%d %H:%M:%S'),
                                     'flags': {'read': i % 4 != 0, 'attached': i % 5 == 1, 'flagged': i % 11 == 2}}
            if fid == '3':
                self.rows[f'demo{i}']['flags'].update(read=True, rcptQueued=True, rcptSucceed=True, rcptRead=i % 2 == 0)
        # One message waiting on the server's schedule (drafts folder, flags.scheduleDelivery).
        self.rows['demo-sched'] = {'id': 'demo-sched', 'fid': '2', 'subject': '周报提醒（演示定时邮件）', 'from': '"演示用户" <demo@example.invalid>',
                                   'to': '"研发团队" <rd@example.invalid>', 'date': (now + timedelta(hours=20)).strftime('%Y-%m-%d %H:%M:%S'),
                                   'flags': {'read': True, 'scheduleDelivery': True},
                                   'scheduleAt': (now + timedelta(hours=20)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        shared_rows = {}
        for i in range(16):
            name, addr = PEOPLE[(i + 2) % len(PEOPLE)]
            fid = '1' if i < 13 else '3'
            when = now - timedelta(hours=i * 7 + 1)
            shared_rows[f'demo{i}'] = {'id': f'demo{i}', 'fid': fid, 'subject': SHARED_SUBJECTS[i % len(SHARED_SUBJECTS)] + '（演示）',
                                       'from': f'"{name}" <{addr}>' if fid == '1' else '"研发部公共邮箱" <rd-office@example.invalid>',
                                       'to': 'rd-office@example.invalid' if fid == '1' else f'"{name}" <{addr}>',
                                       'date': when.strftime('%Y-%m-%d %H:%M:%S'),
                                       'flags': {'read': i % 3 != 0 or fid == '3', 'attached': i % 4 == 1, 'flagged': i == 2}}
        self.shared = {'@rd': {'info': dict(SHARED_BOX), 'folders': {'1': '收件箱', '2': '草稿箱', '3': '已发送', '4': '已删除'},
                               'rows': shared_rows}}
        # Meeting invitations: a request and a cancellation (text/calendar parts).
        # Two existing inbox rows carry them, so folder counts stay as the other tests expect.
        for mid, subject in INVITES.items():
            self.rows[mid].update(subject=subject, **{'from': '"张晓明" <zhangxm@example.invalid>'})
        # Server-shaped mail rules, auto-reply attributes and tags (see protocol/rules.py).
        self.rules = [{'id': 100, 'name': '项目归档', 'continue': True,
                       'condictions': [{'field': 'from', 'operator': '=', 'operand': 'product@example.invalid,rd@example.invalid', 'ignoreCase': True,
                                        'listOfValues': True, 'forAddress': True, 'extractAddress': True}],
                       'actions': [{'type': 'move', 'target': 100}]},
                      {'id': 102, 'name': '周报', 'continue': True, 'operator': 'or',
                       'condictions': [{'field': 'subject', 'operator': 'contains', 'operand': '周报', 'ignoreCase': True, 'listOfValues': True},
                                       {'field': 'subject', 'operator': 'contains', 'operand': 'weekly', 'ignoreCase': True, 'listOfValues': True}],
                       'actions': [{'type': 'flags', 'value': {'read': True}}]},
                      {'id': 103, 'name': '密级邮件提醒', 'continue': False,
                       'condictions': [{'field': 'securityLevel', 'operator': '=', 'operand': '2'}], 'actions': [{'type': 'move', 'target': 1}]}]
        self.attrs = {'autorepmode': 0, 'autoreptext': '10 月 20 日至 10 月 24 日休假，期间邮件回复会延迟。紧急事务请联系李华。（演示）',
                      'autorep_condition': {'begintime': '2026-10-20 00:00:00', 'endtime': '2026-10-24 23:59:59', 'instation': 1},
                      'autorep_welcometip': 1, 'time_zone': 'Asia/Shanghai'}
        self.tags = [['x19a1f0c0001', {'name': '待跟进', 'color': '0'}], ['x19a1f0c0002', {'name': '项目 A', 'color': '3'}]]
        self.tags += [[str(19 - i), {'color': str(c)}] for i, c in enumerate([0, 2, 6, 1, 5, 4, 3, 7, 8])]
        for mid, tags in (('demo1', ['x19a1f0c0001']), ('demo2', ['x19a1f0c0002']), ('demo5', ['x19a1f0c0001', 'x19a1f0c0002']),
                          ('demo9', ['16']), (INVITE_ID, ['x19a1f0c0002'])):
            self.rows[mid]['tag'] = tags
        self.responses = []
        self.last_folder_total = None
        self.last_folder_id = None
        self.signed_in = False
        self.sent = []
        self.sent_payloads = []
        self._counter = 0

    def login(self, account, password):
        self.signed_in = True

    def close(self):
        self.signed_in = False

    def _need(self):
        if not self.signed_in:
            raise AppError('NOT_AUTHENTICATED')

    def get_account_identities(self):
        return {'demo@example.invalid'}

    def display_name(self):
        return '演示用户'

    def _store(self, mboxa=''):
        """(folders, rows) of the user's own mailbox, or of a shared one named by mboxa."""
        if not mboxa:
            return self.folders, self.rows
        if mboxa not in self.shared:
            raise AppError('RPC_REJECTED')
        return self.shared[mboxa]['folders'], self.shared[mboxa]['rows']

    def list_shared_mailboxes(self):
        self._need()
        return [dict(box['info']) for box in self.shared.values()]

    def list_folders(self, mboxa=''):
        self._need()
        folders, store = self._store(mboxa)
        result = []
        for fid, name in folders.items():
            rows = [r for r in store.values() if r['fid'] == fid]
            result.append(Folder(fid, name, None, {'messageCount': len(rows),
                                                   'unreadMessageCount': sum(not r['flags']['read'] for r in rows)}))
        return result

    def _summary(self, r):
        return MessageSummary(r['id'], r['fid'], r['subject'], r['from'], r['date'], r['flags'],
                              preview='本周的设计评审已经完成，请查看以下安排：邮箱工作台交互优化、草稿与阅读流程验证……',
                              recipients=r['to'], size=24000, schedule_at=r.get('scheduleAt'), tags=tuple(r.get('tag') or ()))

    def list_messages(self, folder_id, offset, limit=50, mboxa=''):
        self._need()
        rows = sorted((r for r in self._store(mboxa)[1].values() if r['fid'] == str(folder_id)), key=lambda r: r['date'], reverse=True)
        self.last_folder_total, self.last_folder_id = len(rows), str(folder_id) + mboxa
        return [self._summary(r) for r in rows[offset:offset + limit]]

    def fetch_mime(self, message_id, mboxa=''):
        self._need()
        r = self._store(mboxa)[1].get(message_id)
        if r is None:
            raise AppError('MESSAGE_UNAVAILABLE')
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = r['from'], r['to'], r['subject']
        message['Date'] = r['date']
        message['Message-ID'] = f'<{message_id}{mboxa.replace("@", ".")}@example.invalid>'
        if message_id == OUTLOOK_DEMO_ID and not mboxa:
            return _outlook_style(message)
        if message_id in INVITES and not mboxa:
            cancel = message_id == CANCEL_ID
            message.set_content('张晓明 邀请你参加会议。（合成演示数据）')
            message.add_alternative(_demo_ics(cancel), subtype='calendar', params={'method': 'CANCEL' if cancel else 'REQUEST'})
            return bytes(message)
        message.set_content('各位同事：本周的设计评审已经完成。（合成演示数据）')
        message.add_alternative(BODY, subtype='html')
        if r['flags'].get('attached'):
            message.add_attachment('演示附件内容\n'.encode('utf-8'), maintype='text', subtype='plain', filename='会议纪要.txt')
        return bytes(message)

    # Mirrors the deployment: every advanced-search filter is applied by the server.
    SEARCH_FILTERS = frozenset({'from', 'to', 'with', 'scope', 'since', 'until', 'folder', 'attachment', 'unread', 'flagged'})

    def search_messages(self, pattern, offset, limit=50, filters=None):
        from coremail_desktop.app.backend import filter_match
        from coremail_desktop.app.cache import date_ts
        filters = dict(filters or {})
        if not pattern.strip() and not filters:
            raise AppError('INVALID_SEARCH')
        match = filter_match(filters, pattern)
        text = (lambda r: r['subject']) if filters.get('scope') == 'subject' else (lambda r: r['subject'] + r['from'])
        rows = sorted((r for r in self.rows.values() if pattern.casefold() in text(r).casefold()
                       and match({**r, 'ts': date_ts(r['date'])})), key=lambda r: r['date'], reverse=True)
        return {'messages': [self._summary(r) for r in rows[offset:offset + limit]], 'total': len(rows)}

    # Synthetic directory: a root, departments and people (example.invalid only).
    DIRECTORY = [('d/rd', '研发中心', [('d/rd-fe', '前端组', []), ('d/rd-be', '后端组', [])]),
                 ('d/product', '产品部', []), ('d/hr', '人力资源部', []), ('d/it', 'IT 服务部', [])]
    STAFF = {'d/rd': [('研发团队', 'rd@example.invalid', '公共邮箱')],
             'd/rd-fe': [('张晓明', 'zhangxm@example.invalid', '前端工程师'), ('王芳', 'wangfang@example.invalid', '高级工程师'),
                         ('赵磊', 'zhaolei@example.invalid', '工程师')],
             'd/rd-be': [('李华', 'lihua@example.invalid', '架构师'), ('陈静', 'chenjing@example.invalid', '工程师'),
                         ('刘洋', 'liuyang@example.invalid', '测试工程师')],
             'd/product': [('产品协作组', 'product@example.invalid', '公共邮箱'), ('孙悦', 'sunyue@example.invalid', '产品经理'),
                           ('周婷', 'zhouting@example.invalid', '交互设计师')],
             'd/hr': [('人力资源部', 'hr@example.invalid', '公共邮箱'), ('吴敏', 'wumin@example.invalid', 'HRBP')],
             'd/it': [('IT 服务台', 'itsupport@example.invalid', '公共邮箱'), ('郑凯', 'zhengkai@example.invalid', '运维工程师')]}

    def directory_tree(self):
        self._need()
        build = lambda nodes: [{'id': i, 'name': n, 'children': build(c)} for i, n, c in nodes]
        return [{'id': 'd', 'name': '演示公司', 'children': build(self.DIRECTORY)}]

    def _dept_path(self, dn):
        def find(nodes, trail):
            for i, n, c in nodes:
                if i == dn:
                    return trail + [n]
                found = find(c, trail + [n])
                if found:
                    return found
            return None
        return ' / '.join(['演示公司'] + (find(self.DIRECTORY, []) or []))

    def directory_members(self, dn):
        self._need()
        rows = [{'name': n, 'email': a, 'title': t, 'dept': self._dept_path(dn)} for n, a, t in self.STAFF.get(dn, [])]
        return {'rows': rows, 'total': len(rows)}

    def personal_contacts(self):
        self._need()
        return [{'name': '合作伙伴 · 外部', 'email': 'partner@external.example'}, {'name': '供应商联系人', 'email': 'vendor@external.example'}]

    def move_message(self, message_id, folder_id, mboxa=''):
        self._store(mboxa)[1][message_id]['fid'] = folder_id
        return {'message_id': message_id, 'folder_id': folder_id}

    def set_message_flags(self, message_id, flags, mboxa=''):
        self._store(mboxa)[1][message_id]['flags'].update(flags)
        return {'message_id': message_id, 'flags': flags}

    batch_updates = True

    def set_flags_many(self, message_ids, flags, mboxa=''):
        for mid in message_ids:
            self._store(mboxa)[1][mid]['flags'].update(flags)
        return list(message_ids)

    def move_many(self, message_ids, folder_id, mboxa=''):
        for mid in message_ids:
            self._store(mboxa)[1][mid]['fid'] = folder_id
        return list(message_ids)

    def match_contacts(self, keyword, limit=8):
        people = dict.fromkeys(PEOPLE + [(n, a) for staff in self.STAFF.values() for n, a, _ in staff])
        return [{'name': n, 'email': a} for n, a in people if keyword.casefold() in (n + a).casefold()][:limit]

    def create_compose(self, draft):
        self._counter += 1
        return f'compose{self._counter}'

    def deliver(self, compose_id, draft):
        self.sent.append(draft)
        self._counter += 1
        mid = f'demo-new{self._counter}'
        scheduled = bool(getattr(draft, 'schedule_at', None))
        self.rows[mid] = {'id': mid, 'fid': '2' if scheduled else '3', 'subject': draft.subject, 'from': '"演示用户" <demo@example.invalid>',
                          'to': ', '.join(draft.to), 'date': datetime(2026, 10, 17, 10, 31).strftime('%Y-%m-%d %H:%M:%S'),
                          'flags': {'read': True, 'scheduleDelivery': True} if scheduled else {'read': True, 'rcptQueued': True},
                          'scheduleAt': draft.schedule_at}
        return {'code': 'S_OK', **({'scheduledSent': {'mid': mid}} if scheduled else {})}

    supports_scheduled_encrypted = False

    def scheduled_messages(self, limit=100):
        return [self._summary(r) for r in self.rows.values() if r['fid'] == '2' and r['flags'].get('scheduleDelivery')][:limit]

    def unschedule(self, message_id):
        row = self.rows.get(message_id)
        if not row or not row['flags'].get('scheduleDelivery'):
            raise AppError('NOT_SCHEDULED')
        row['flags']['scheduleDelivery'] = False
        row['scheduleAt'] = None
        return {'message_id': message_id, 'draft_id': message_id}

    def _recipients(self, message_id, mboxa=''):
        from email.utils import getaddresses
        row = self._store(mboxa)[1].get(message_id)
        if row is None or row['fid'] != '3':
            raise AppError('MESSAGE_UNAVAILABLE')
        return row, [(n, a) for n, a in getaddresses([row['to']]) if a]

    def delivery_history(self, message_id, mboxa=''):
        row, people = self._recipients(message_id, mboxa)
        result = []
        for name, addr in people:
            external = not addr.endswith('@example.invalid')
            code = row.get('recall') or (200 if external else 509 if row['flags'].get('rcptRead') else 100)
            result.append({'to': addr, 'name': name, 'result': code if code > 99 else 100, 'recall': code if code < 99 else 0,
                           'recallable': not external and code == 100, 'time': row['date']})
        return result

    def recall_message(self, message_id):
        row, people = self._recipients(message_id)
        states = {a: 0 if not a.endswith('@example.invalid') else 5 if row['flags'].get('rcptRead') else 2 for _, a in people}
        if all(code == 2 for code in states.values()):
            row['recall'] = 2
        return {'cancelled': False, 'states': states}

    supports_encrypted_submission = True
    _keys = {}

    def public_certificates(self, emails):
        """Synthetic company directory: every example.invalid colleague has a certificate."""
        return {e.casefold(): demo_certificate(e)[0] for e in emails if e.casefold().endswith('@example.invalid')}

    def submit_encrypted(self, payload, recipients, draft=None):
        self.sent.append(draft)
        self.sent_payloads.append(payload)
        return {'code': 'S_OK'}

    def find_sent_candidates(self, job, limit=50):
        return []

    # Rules, auto-reply, tags, invitations: server shapes, kept in memory.
    def mail_rules(self):
        self._need()
        return [dict(r) for r in self.rules]

    def add_mail_rule(self, rule):
        rule = {**rule}
        rule['condictions'] = rule.pop('conditions')
        rule['id'] = max([r['id'] for r in self.rules] + [100]) + 1
        self.rules.append(rule)
        return True

    def update_mail_rule(self, rule):
        rule = {**rule}
        if 'conditions' in rule:
            rule['condictions'] = rule.pop('conditions')
        i = next(i for i, r in enumerate(self.rules) if r['id'] == rule['id'])
        self.rules[i] = {**self.rules[i], **rule}
        return True

    def remove_mail_rule(self, rule_id):
        self.rules = [r for r in self.rules if r['id'] != rule_id]
        return True

    def move_mail_rule(self, rule_id, ref_id):
        ids = [r['id'] for r in self.rules]
        i, j = ids.index(rule_id), ids.index(ref_id)
        self.rules[i], self.rules[j] = self.rules[j], self.rules[i]
        return True

    def set_mail_rule_disabled(self, rule_id, disabled):
        return self.update_mail_rule({'id': rule_id, 'disabled': disabled})

    def auto_reply(self):
        self._need()
        return {**self.attrs, 'autorep_condition': dict(self.attrs['autorep_condition'])}

    def set_auto_reply(self, attrs):
        self.attrs.update(attrs)
        return True

    def account_now(self):
        return datetime(2026, 10, 17, 10, 30).strftime('%Y-%m-%d %H:%M:%S')

    def list_tags(self, mboxa=''):
        self._need()
        return [[t[0], dict(t[1])] for t in self.tags]

    def manage_tags(self, op, payload):
        if op == 'add':
            self.tags.extend([list(p) for p in payload])
        elif op == 'update':
            for tag_id, attrs in payload:
                next(t for t in self.tags if t[0] == tag_id)[1].update(attrs)
        else:
            self.tags = [t for t in self.tags if t[0] not in payload]
            for r in self.rows.values():
                r['tag'] = [t for t in r.get('tag') or [] if t not in payload]
        return True

    def update_message_tags(self, message_ids, add=(), remove=(), mboxa=''):
        for mid in message_ids:
            row = self._store(mboxa)[1][mid]
            row['tag'] = [t for t in row.get('tag') or [] if t not in remove] + [t for t in add if t not in (row.get('tag') or [])]
        return list(message_ids)

    def tagged_messages(self, tag_id, offset, limit=50):
        rows = sorted((r for r in self.rows.values() if tag_id in (r.get('tag') or []) and r['fid'] != '4'), key=lambda r: r['date'], reverse=True)
        return {'messages': [self._summary(r) for r in rows[offset:offset + limit]], 'total': len(rows)}

    def respond_invitation(self, message_id, answer):
        if message_id != INVITE_ID:
            raise AppError('INVITE_UNAVAILABLE')
        self.responses.append((message_id, answer))
        return {'answer': answer, 'type': {'tentative': 2, 'accept': 3, 'decline': 4}[answer]}


def _demo_ics(cancel):
    start = '20261021T100000' if cancel else '20261020T150000'
    end = '20261021T110000' if cancel else '20261020T163000'
    lines = ['BEGIN:VCALENDAR', 'PRODID:-//Hongyan demo//CN', 'VERSION:2.0', 'METHOD:' + ('CANCEL' if cancel else 'REQUEST'),
             'BEGIN:VTIMEZONE', 'TZID:China Standard Time', 'BEGIN:STANDARD', 'DTSTART:16010101T000000', 'TZOFFSETFROM:+0800',
             'TZOFFSETTO:+0800', 'END:STANDARD', 'END:VTIMEZONE', 'BEGIN:VEVENT',
             'ORGANIZER;CN=张晓明:mailto:zhangxm@example.invalid',
             'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=演示用户:mailto:demo@example.invalid',
             'ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=李华:mailto:lihua@example.invalid',
             'ATTENDEE;ROLE=OPT-PARTICIPANT;PARTSTAT=TENTATIVE;CN=王芳:mailto:wangfang@example.invalid',
             'SUMMARY:' + ('服务器迁移方案沟通' if cancel else '设计评审第二轮'), 'LOCATION:' + ('线上会议' if cancel else '研发中心 3 号会议室'),
             'DESCRIPTION:' + (BSL + 'n').join(['议程：', '1. 交互稿评审', '2. 接口联调计划（合成演示数据）']),
             f'DTSTART;TZID=China Standard Time:{start}', f'DTEND;TZID=China Standard Time:{end}',
             'UID:demo-' + ('cancel' if cancel else 'invite') + '@example.invalid', 'SEQUENCE:' + ('1' if cancel else '0'),
             'STATUS:' + ('CANCELLED' if cancel else 'CONFIRMED'), 'END:VEVENT', 'END:VCALENDAR', '']
    return CRLF.join(lines)


BSL, CRLF = chr(92), chr(13) + chr(10)
