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

    def list_folders(self):
        self._need()
        result = []
        for fid, name in self.folders.items():
            rows = [r for r in self.rows.values() if r['fid'] == fid]
            result.append(Folder(fid, name, None, {'messageCount': len(rows),
                                                   'unreadMessageCount': sum(not r['flags']['read'] for r in rows)}))
        return result

    def _summary(self, r):
        return MessageSummary(r['id'], r['fid'], r['subject'], r['from'], r['date'], r['flags'],
                              preview='本周的设计评审已经完成，请查看以下安排：邮箱工作台交互优化、草稿与阅读流程验证……',
                              recipients=r['to'], size=24000, schedule_at=r.get('scheduleAt'))

    def list_messages(self, folder_id, offset, limit=50):
        self._need()
        rows = sorted((r for r in self.rows.values() if r['fid'] == str(folder_id)), key=lambda r: r['date'], reverse=True)
        self.last_folder_total, self.last_folder_id = len(rows), str(folder_id)
        return [self._summary(r) for r in rows[offset:offset + limit]]

    def fetch_mime(self, message_id):
        self._need()
        r = self.rows.get(message_id)
        if r is None:
            raise AppError('MESSAGE_UNAVAILABLE')
        message = EmailMessage()
        message['From'], message['To'], message['Subject'] = r['from'], r['to'], r['subject']
        message['Date'] = r['date']
        message['Message-ID'] = f'<{message_id}@example.invalid>'
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

    def move_message(self, message_id, folder_id):
        self.rows[message_id]['fid'] = folder_id
        return {'message_id': message_id, 'folder_id': folder_id}

    def set_message_flags(self, message_id, flags):
        self.rows[message_id]['flags'].update(flags)
        return {'message_id': message_id, 'flags': flags}

    batch_updates = True

    def set_flags_many(self, message_ids, flags):
        for mid in message_ids:
            self.rows[mid]['flags'].update(flags)
        return list(message_ids)

    def move_many(self, message_ids, folder_id):
        for mid in message_ids:
            self.rows[mid]['fid'] = folder_id
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

    def _recipients(self, message_id):
        from email.utils import getaddresses
        row = self.rows.get(message_id)
        if row is None or row['fid'] != '3':
            raise AppError('MESSAGE_UNAVAILABLE')
        return row, [(n, a) for n, a in getaddresses([row['to']]) if a]

    def delivery_history(self, message_id):
        row, people = self._recipients(message_id)
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
