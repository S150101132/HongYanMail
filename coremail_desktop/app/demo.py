"""Synthetic in-memory adapter for --demo and offline tests. Never touches the network."""
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftAttachment, Folder, MessageSummary

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
    from coremail_desktop.app.demo_office import demo_xlsx
    message.add_attachment(demo_xlsx(), maintype='application', subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
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
        self.parents = {}  # Sub-folder id -> parent id.
        self.lists = {'safelist': ['partner@external.example'], 'refuselist': ['*@spam.example', 'ads@promo.example']}
        self.reports = []
        self.last_folder_total = None
        self.last_folder_id = None
        self.signed_in = False
        self.sent = []
        self.sent_payloads = []
        self._counter = 0
        self.offline = False  # True: every server call fails as if the network were down.
        self.saved_drafts = []
        # Two server drafts saved elsewhere (webmail / phone), one with a table and an attachment.
        for i, (subject, body) in enumerate([
                ('季度预算草案（演示服务器草稿）', '<p>各位：</p><p>预算草案如下，请补充：</p><table border="1" cellpadding="6" '
                 'style="border-collapse:collapse"><tr><th>项目</th><th>金额</th></tr><tr><td>设备</td><td>12 万</td></tr></table>'),
                ('周五例会提纲（手机草稿）', '<p>1. 进度回顾</p><p>2. 风险</p>')]):
            mid = f'demo-draft{i}'
            self.rows[mid] = {'id': mid, 'fid': '2', 'subject': subject, 'from': '"演示用户" <demo@example.invalid>',
                              'to': '"研发团队" <rd@example.invalid>', 'date': (now - timedelta(hours=3 + i)).strftime('%Y-%m-%d %H:%M:%S'),
                              'flags': {'read': True, 'draft': True, 'attached': i == 0}, 'html': body,
                              'files': (DraftAttachment('预算明细.txt', 'text/plain', '设备 12 万（演示）'.encode('utf-8')),) if i == 0 else ()}

    def login(self, account, password):
        self.signed_in = True

    def close(self):
        self.signed_in = False

    def _need(self):
        if self.offline:
            raise AppError('NETWORK_ERROR')
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
                                                   'unreadMessageCount': sum(not r['flags']['read'] for r in rows)},
                                 None if mboxa else self.parents.get(fid), int(fid) <= 6))
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
        if r.get('cc'):
            message['Cc'] = r['cc']
        message['Message-ID'] = f'<{message_id}{mboxa.replace("@", ".")}@example.invalid>'
        if message_id == OUTLOOK_DEMO_ID and not mboxa:
            return _outlook_style(message)
        if message_id in INVITES and not mboxa:
            cancel = message_id == CANCEL_ID
            message.set_content('张晓明 邀请你参加会议。（合成演示数据）')
            message.add_alternative(_demo_ics(cancel), subtype='calendar', params={'method': 'CANCEL' if cancel else 'REQUEST'})
            return bytes(message)
        message.set_content('各位同事：本周的设计评审已经完成。（合成演示数据）')
        if r.get('html'):
            # Mail written in this session keeps its own body; its pictures as related parts.
            from coremail_desktop.compose.inline import extract_inline, fill_inline
            template, images = extract_inline(r['html'])
            message.add_alternative(fill_inline(template, [f'cid:img{i}@demo.invalid' for i in range(len(images))]), subtype='html')
            for i, image in enumerate(images):
                maintype, _, subtype = image.media_type.partition('/')
                message.get_payload()[1].add_related(image.payload, maintype=maintype, subtype=subtype,
                                                     cid=f'<img{i}@demo.invalid>', filename=image.name, disposition='inline')
            for a in r.get('files', ()):
                maintype, _, subtype = a.media_type.partition('/')
                message.add_attachment(a.payload, maintype=maintype, subtype=subtype, filename=a.name)
            return bytes(message)
        message.add_alternative(BODY, subtype='html')
        if r['flags'].get('attached'):
            message.add_attachment('演示附件内容\n'.encode('utf-8'), maintype='text', subtype='plain', filename='会议纪要.txt')
        if message_id == PREVIEW_DEMO_ID and not mboxa:  # Previewable kinds: images, a PDF, HTML and Office files.
            from coremail_desktop.app import demo_office as office
            ooxml = 'vnd.openxmlformats-officedocument.'
            message.add_attachment(_demo_chart(), maintype='image', subtype='png', filename='进度图表.png')
            message.add_attachment(_demo_pdf(), maintype='application', subtype='pdf', filename='评审报告.pdf')
            message.add_attachment(office.demo_xlsx(), maintype='application', subtype=ooxml + 'spreadsheetml.sheet', filename='支持需求清单.xlsx')
            message.add_attachment(office.demo_docx(_demo_chart(200, 125)), maintype='application', subtype=ooxml + 'wordprocessingml.document',
                                   filename='评审纪要.docx')
            message.add_attachment(office.demo_pptx(), maintype='application', subtype=ooxml + 'presentationml.presentation', filename='项目周报.pptx')
            message.add_attachment(office.demo_html(), maintype='text', subtype='html', filename='周报.html')
            message.add_attachment(office.demo_svg(), maintype='image', subtype='svg+xml', filename='标志.svg')
            message.add_attachment(office.demo_bmp(), maintype='image', subtype='bmp', filename='截图.bmp')
            message.add_attachment(b'\xd0\xcf\x11\xe0 legacy', maintype='application', subtype='vnd.ms-excel', filename='旧版报表.xls')
        return bytes(message)

    # Mirrors the deployment: every advanced-search filter is applied by the server.
    SEARCH_FILTERS = frozenset({'from', 'to', 'with', 'scope', 'since', 'until', 'folder', 'attachment', 'unread', 'flagged'})

    def search_messages(self, pattern, offset, limit=50, filters=None, snippet=False):
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
                 ('d/product', '产品部', []), ('d/hr', '人力资源部', []), ('d/it', 'IT 服务部', []),
                 # As on the real server: a parent with nobody of its own, people only in
                 # its leaves, and a leaf where nobody is registered.
                 ('d/sales', '销售中心', [('d/sales-n', '华北区', []), ('d/sales-s', '华南区', []), ('d/sales-w', '西部区', [])])]
    STAFF = {'d/rd': [('研发团队', 'rd@example.invalid', '公共邮箱')],
             'd/rd-fe': [('张晓明', 'zhangxm@example.invalid', '前端工程师'), ('王芳', 'wangfang@example.invalid', '高级工程师'),
                         ('赵磊', 'zhaolei@example.invalid', '工程师')],
             'd/rd-be': [('李华', 'lihua@example.invalid', '架构师'), ('陈静', 'chenjing@example.invalid', '工程师'),
                         ('刘洋', 'liuyang@example.invalid', '测试工程师')],
             'd/product': [('产品协作组', 'product@example.invalid', '公共邮箱'), ('孙悦', 'sunyue@example.invalid', '产品经理'),
                           ('周婷', 'zhouting@example.invalid', '交互设计师')],
             'd/hr': [('人力资源部', 'hr@example.invalid', '公共邮箱'), ('吴敏', 'wumin@example.invalid', 'HRBP')],
             'd/it': [('IT 服务台', 'itsupport@example.invalid', '公共邮箱'), ('郑凯', 'zhengkai@example.invalid', '运维工程师')],
             'd/sales-n': [('马超', 'machao@example.invalid', '区域经理'), ('黄蕾', 'huanglei@example.invalid', '销售代表')],
             'd/sales-s': [('林峰', 'linfeng@example.invalid', '区域经理'), ('何静', 'hejing@example.invalid', '销售代表'),
                           ('郭明', 'guoming@example.invalid', '售前工程师')]}

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

    def person_details(self, email):
        """Synthetic directory details in the real server's shape: fake numbers and a made-up address."""
        self._need()
        for dn, staff in self.STAFF.items():
            for i, (name, address, title) in enumerate(staff):
                if address.casefold() != email.casefold():
                    continue
                n = sum(len(s) for s in self.STAFF.values()) + i
                public = title == '公共邮箱'
                return {'uid': 'demo-' + address.split('@')[0], 'name': name, 'email': address, 'title': title, 'dept': self._dept_path(dn),
                        'mobile': '' if public else f'100-0000-{n:04d}', 'phone': f'0000-{8000 + n}',
                        'fax': '', 'address': '演示园区 A 座 %d 层' % (3 + len(dn) % 4)}
        return None

    def head_image(self, uid):
        """A generated gradient PNG for two demo people; everyone else has no head image."""
        import struct
        import zlib
        if uid not in ('demo-zhangxm', 'demo-sunyue'):
            return None
        size, hue = 64, (37, 99, 235) if uid == 'demo-zhangxm' else (219, 39, 119)
        rows = b''.join(b'\x00' + bytes(c for x in range(size) for c in (
            min(255, hue[0] + y * 2), min(255, hue[1] + x), min(255, hue[2] + (x + y) // 4))) for y in range(size))
        chunk = lambda tag, data: struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))

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

    # Folder management, permanent deletion, spam reports and sender lists.
    def create_folder(self, name, parent=None, mboxa=''):
        self._need()
        self._counter += 1
        fid = str(200 + self._counter)
        self.folders[fid] = name
        if parent:
            self.parents[fid] = str(parent)
        return fid

    def rename_folder(self, folder_id, name, mboxa=''):
        self.folders[str(folder_id)] = name
        return True

    def delete_folder(self, folder_id, mboxa=''):
        if any(r['fid'] == str(folder_id) for r in self.rows.values()):
            raise AppError('FOLDER_NOT_EMPTY')
        self.folders.pop(str(folder_id))
        self.parents.pop(str(folder_id), None)
        return True

    def delete_messages(self, message_ids, mboxa=''):
        for mid in message_ids:
            self._store(mboxa)[1].pop(mid, None)
        return list(message_ids)

    def empty_folder(self, folder_id, mboxa=''):
        store = self._store(mboxa)[1]
        for mid in [m for m, r in store.items() if r['fid'] == str(folder_id)]:
            del store[mid]
        return True

    def report_spam(self, message_ids, spam=True):
        self.reports.append(('spam' if spam else 'notspam', list(message_ids)))
        return self.move_many(message_ids, '5' if spam else '1')

    def unread_ids(self, folder_id, mboxa='', limit=5000):
        rows = self._store(mboxa)[1].values()
        return [r['id'] for r in sorted(rows, key=lambda r: r['date'], reverse=True) if r['fid'] == str(folder_id) and not r['flags']['read']][:limit]

    def sender_lists(self):
        self._need()
        return {k: list(v) for k, v in self.lists.items()}

    def set_sender_lists(self, lists):
        self.lists.update({k: list(v) for k, v in lists.items()})
        return True

    def match_contacts(self, keyword, limit=8):
        people = dict.fromkeys(PEOPLE + [(n, a) for staff in self.STAFF.values() for n, a, _ in staff])
        return [{'name': n, 'email': a} for n, a in people if keyword.casefold() in (n + a).casefold()][:limit]

    def create_compose(self, draft):
        self._need()
        self._counter += 1
        return f'compose{self._counter}'

    def _consume(self, draft):
        # Like CoremailAdapter._retire_draft: the replaced server draft moves to the trash.
        if draft is not None and draft.server_draft_id in self.rows:
            self.rows[draft.server_draft_id]['fid'] = '4'

    def deliver(self, compose_id, draft):
        self._need()
        self.sent.append(draft)
        self._consume(draft)
        self._counter += 1
        mid = f'demo-new{self._counter}'
        scheduled = bool(getattr(draft, 'schedule_at', None))
        self.rows[mid] = {'id': mid, 'fid': '2' if scheduled else '3', 'subject': draft.subject, 'from': '"演示用户" <demo@example.invalid>',
                          'to': ', '.join(draft.to), 'cc': ', '.join(draft.cc), 'date': datetime(2026, 10, 17, 10, 31).strftime('%Y-%m-%d %H:%M:%S'),
                          'flags': {'read': True, 'scheduleDelivery': True} if scheduled else {'read': True, 'rcptQueued': True},
                          'scheduleAt': draft.schedule_at, 'html': draft.body if draft.is_html else None, 'files': draft.attachments}
        return {'code': 'S_OK', **({'scheduledSent': {'mid': mid}} if scheduled else {})}

    def save_server_draft(self, draft):
        """A fresh compose saved as a draft; the draft it replaces goes to the trash."""
        if draft.encryption_required or draft.signature_required:
            raise AppError('DRAFT_ENCRYPTED_LOCAL')
        self.create_compose(draft)
        self._consume(draft)
        self.saved_drafts.append(draft)
        self._counter += 1
        mid = f'demo-draft-s{self._counter}'
        self.rows[mid] = {'id': mid, 'fid': '2', 'subject': draft.subject, 'from': '"演示用户" <demo@example.invalid>',
                          'to': ', '.join(draft.to), 'cc': ', '.join(draft.cc), 'date': datetime(2026, 10, 17, 10, 32).strftime('%Y-%m-%d %H:%M:%S'),
                          'flags': {'read': True, 'draft': True, 'attached': bool(draft.attachments)},
                          'html': draft.body, 'files': draft.attachments}
        return mid

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
        self._need()
        return {e.casefold(): demo_certificate(e)[0] for e in emails if e.casefold().endswith('@example.invalid')}

    def submit_encrypted(self, payload, recipients, draft=None):
        self._need()
        self.sent.append(draft)
        self._consume(draft)
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

    @staticmethod
    def _rule_hit(c, r):
        """The server matches rules itself; the demo approximates its text and address operators."""
        field, op, operand = c.get('field'), c.get('operator'), c.get('operand')
        if field == 'flags':
            return bool(r['flags'].get('attached'))
        if field == 'size':
            return 24000 / 1024 > operand if op == '>' else 24000 / 1024 < operand
        source = {'from': r['from'], 'to': r['to'], 'cc': r.get('cc', ''), 'recipients': f"{r['to']},{r.get('cc', '')}", 'subject': r['subject']}.get(field)
        if source is None:
            return False
        values = [v.strip().casefold() for v in str(operand).split(',') if v.strip()]
        address = lambda text: re.findall(r'[^\s<>",]+@[^\s<>",]+', text)
        texts = address(source.casefold()) if c.get('forAddress') else [source.casefold()]
        if c.get('forAddress'):  # `"Name" <addr>` values match on the address (extractAddress).
            values = [(address(v) or [v])[0] for v in values]
        test = {'contains': lambda t, v: v in t, 'excludes': lambda t, v: v not in t, '=': str.__eq__, '!=': str.__ne__,
                'startsWith': str.startswith, 'endsWith': str.endswith}[op]
        if op in ('excludes', '!='):
            return all(test(t, v) for t in texts for v in values)
        return any(test(t, v) for t in texts for v in values)

    def rule_matches(self, rule_id, folder_id, offset=0, limit=100):
        self._need()
        rule = next((r for r in self.rules if r['id'] == rule_id), None)
        if rule is None:
            raise AppError('RPC_REJECTED')
        conds = rule.get('condictions') or []
        join = any if rule.get('operator') == 'or' else all
        rows = sorted((r for r in self.rows.values() if r['fid'] == str(folder_id) and conds and join(self._rule_hit(c, r) for c in conds)),
                      key=lambda r: r['date'], reverse=True)
        return {'messages': [self._summary(r) for r in rows[offset:offset + limit]], 'total': len(rows)}

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


PREVIEW_DEMO_ID = 'demo1'


class DemoAiClient:
    """Stand-in for the AI service in demo mode: a fixed-shape summary built from the
    prompt itself, no network."""
    model = '演示'

    def chat(self, messages, max_tokens=800, json_mode=False):
        import json
        import re
        import time
        time.sleep(0.6)  # Long enough to see the progress state.
        text = messages[-1]['content']
        if '为工作任务的演进图命名每个环节' in messages[0]['content']:  # 事件演进图 titles, from each step's own words.
            steps = json.loads(text)['环节']
            items = [{'id': s['id'], 'title': re.split(r'[，。；：:,\s]', s['text'])[0][:8] or '进展', 'summary': s['text'][:40],
                      'kind': 'done' if '完成' in s['text'] else 'wait' if '等待' in s['text'] else 'contact' if '邮件' in s['text'] else 'progress'}
                     for s in steps]
            return json.dumps({'items': items}, ensure_ascii=False), 0
        if '鸿雁工作周报' in messages[0]['content']:
            data = json.loads(text.partition('\n')[2])
            done = [f'#{t["id"]} {t["system"]} {t["content"][:30]}' for t in data['active'] if t['end']]
            moving = [f'#{t["id"]} {t["system"]} {t["records"][-1]["text"][:40]}' for t in data['active'] if t['records'] and not t['end']]
            plan = [f'#{t["id"]} {t["system"]} {t["next"][:40]}' for t in data['unfinished'] if t['next']]
            blocked = [f'#{t["id"]} {t["system"]} {t["blockers"][:40] or "受阻"}' for t in data['unfinished'] if t['status'] == '受阻']
            return json.dumps({'subject': f'工作周报（演示，{data["from"]} 至 {data["to"]}）', 'summary': '演示模式：由本机根据任务记录生成，没有联网。',
                               'sections': [{'title': '本周完成', 'items': done}, {'title': '本周进展', 'items': moving},
                                            {'title': '受阻与风险', 'items': blocked}, {'title': '下周计划', 'items': plan}]}, ensure_ascii=False), 0
        if '鸿雁写信助手' in messages[0]['content'] and '回复方向' in messages[0]['content'] and 'ideas' in messages[0]['content']:
            return json.dumps({'ideas': [{'label': '同意并确认', 'hint': '答应对方的要求，确认时间（演示）'},
                                         {'label': '先问清楚', 'hint': '请对方补充缺少的信息（演示）'},
                                         {'label': '婉拒或改期', 'hint': '说明原因，提出别的时间（演示）'}]}, ensure_ascii=False), 0
        if '今天 GitHub Trending' in messages[0]['content']:
            from coremail_desktop.ai.trending import demo
            return demo(messages), 0
        if '鸿雁每周概要助手' in messages[0]['content']:
            from coremail_desktop.ai.weekly import demo
            return demo(messages), 0
        if '会话（同一主题的多封往来邮件' in messages[0]['content']:
            count = len(re.findall(r'^邮件 \d+$', text, re.M))
            return json.dumps({'conclusion': f'这个会话共 {count} 封邮件（演示模式：由本机生成，没有联网）。',
                               'timeline': [{'n': n, 'text': f'第 {n} 封邮件。'} for n in range(1, count + 1)][:8],
                               'open': [], 'actions': []}, ensure_ascii=False), 0
        if '鸿雁的邮件理解助手' in messages[0]['content']:  # 理解层: a card from the subject's words.
            subject = (re.search(r'主题：(.*)', text) or [None, ''])[1].replace('（演示）', '').strip()
            topic = re.sub(r'^(?:Re|回复)\s*[:：]\s*|【[^】]*】', '', subject).strip() or '邮件'
            listed = re.search(r'我负责的项目：(.*)', text)
            project = (re.split(r'[、（]', listed.group(1))[0] if listed and '（无）' not in listed.group(1) else '') or '演示项目'
            system = (re.search(r'（系统：([^、）]+)', listed.group(1)) or [None, project])[1] if listed else project
            known = re.search(r'^(F\d+) \[' + re.escape(project) + r'\] .*本周进展', text, re.M)  # 工作记忆: a newer status replaces it.
            if re.search(r'周报|纪要|进展', subject):
                card = {'kind': 'know', 'sure': True, 'gist': f'{topic}：本周进展和下周安排', 'ask': '', 'due': '',
                        'facts': [{'project': project, 'type': 'status', 'text': f'{topic}：本周进展已更新（演示）', 'replaces': known.group(1) if known else ''},
                                  {'project': system, 'type': 'owner', 'text': '张晓明负责接口联调（演示）'}]}
            elif re.search(r'请|确认|申请|评审', subject):
                due = '2026-10-23' if '周五' in subject else ''
                card = {'kind': 'act', 'sure': True, 'gist': f'{topic}，需要你反馈', 'ask': f'就「{topic[:16]}」回复意见', 'due': due,
                        'facts': [{'project': system, 'type': 'date', 'text': f'{topic[:20]}：{due or "本周"}前反馈（演示）'}]}
                task = re.search(r'^#(\d+) .*' + re.escape(topic[:2]), text, re.M) if len(topic) >= 2 else None
                if task:
                    card['task'] = {'id': int(task.group(1)), 'why': '主题相同（演示）'}
            elif re.search(r'通知|培训|维护', subject):
                card = {'kind': 'ignore', 'sure': True, 'gist': f'{topic}（例行通知）', 'ask': '', 'due': ''}
            else:
                card = {'kind': 'know', 'sure': False, 'gist': topic[:30], 'ask': '', 'due': ''}
            return json.dumps({'project': project if card['kind'] != 'ignore' else '', 'facts': [], **card}, ensure_ascii=False), 0
        if '鸿雁任务助手' in messages[0]['content']:
            mail = next((m['content'] for m in messages if m['role'] == 'user' and '请根据下面的邮件' in m['content']), '')
            if mail:
                return json.dumps(self._mail_plan(messages[1]['content'], mail), ensure_ascii=False), 0
            return json.dumps(self._task_plan(text), ensure_ascii=False), 0
        subject = (re.search(r'主题：(.*)', text) or [None, ''])[1].strip() or '（无主题）'
        body = text.partition('正文：\n')[2].rsplit('\n', 1)[0]
        lines = [l.strip() for l in body.splitlines() if len(l.strip()) > 6][:3]
        value = {'conclusion': f'这是一封关于「{subject}」的演示邮件（演示模式：摘要由本机生成，没有联网）。',
                 'points': [l[:60] for l in lines] or ['邮件正文很短。'],
                 'actions': ['按邮件要求回复发件人。'] if re.search(r'请|麻烦|务必|回复', body) else []}
        value.update(for_me='用户在这封邮件中的位置：主送' in text and bool(value['actions']), who='' if '位置：主送' in text else '主送的同事')
        return json.dumps(value, ensure_ascii=False), 0

    @staticmethod
    def _mail_plan(rules, mail):
        """转为任务 in demo mode: update the task the user named, else a new task from the subject."""
        import re
        subject = (re.search(r'主题：(.*)', mail) or [None, '（无主题）'])[1].strip()
        named = re.search(r'用这些邮件更新任务 #(\d+)|与任务 #(\d+) 的已关联邮件', rules)
        note = '（演示模式：本机生成，没有联网。）'
        if named:
            named = re.search(r'\d+', named.group(0))
            return {'reply': f'演示模式：用这封邮件更新 #{named.group(0)}。{note}', 'questions': [], 'candidates': [], 'fit': 'yes',
                    'actions': [{'op': 'progress', 'id': int(named.group(0)), 'text': f'收到邮件「{subject}」。', 'date': '', 'latest': True, 'fields': {}}]}
        return {'reply': f'演示模式：根据邮件新建任务。{note}', 'questions': [], 'candidates': [],
                'actions': [{'op': 'create', 'fields': {'content': subject, 'status': '未开始'}}]}

    @staticmethod
    def _task_plan(text):
        """自然语言操作 in demo mode: the first task number and a few keywords, scripted."""
        import re
        found = re.search(r'(?<![\d.])(\d{1,4})(?![\d.])', text)
        status = next((s for s in ('受阻', '进行中', '未开始', '待处理', '待验收') if s in text), '')
        if re.search(r'周报|总结|汇报', text):
            return {'reply': '演示模式：生成本周周报。', 'questions': [], 'actions': [], 'report': {'from': '', 'to': ''}}
        if not found and re.search(r'哪些|查|找|列出|看看', text):
            return {'reply': f'演示模式：按「{status or text}」筛选任务（本机生成，没有联网）。', 'questions': [], 'actions': [],
                    'show': {'view': 'open' if status else 'all', 'query': status, 'ids': []}}
        if not found:
            return {'reply': '演示模式：没有找到任务编号。', 'questions': ['请说明是哪一条任务（编号）？'], 'actions': []}
        task_id = int(found.group(1))
        words = text.replace(found.group(1), '', 1).strip(' ：:，,。')
        if re.search(r'完成|搞定|关闭', text):
            action = {'op': 'complete', 'id': task_id, 'date': '', 'note': ''}
        elif re.search(r'取消|不做了', text):
            action = {'op': 'cancel', 'id': task_id, 'reason': words or '用户取消', 'date': ''}
        else:
            action = {'op': 'progress', 'id': task_id, 'text': words, 'date': '', 'latest': True, 'fields': {}}
        return {'reply': f'演示模式：对 #{task_id} 执行「{action["op"]}」（本机生成，没有联网）。', 'questions': [], 'actions': [action]}

    def stream(self, messages, on_delta, cancelled=lambda: False, max_tokens=800):
        """写信助手 and 起草回复 in demo mode: a scripted text, a few characters at a time."""
        import time
        from coremail_desktop.errors import AppError
        system, text = messages[0]['content'], messages[-1]['content']
        if '起草一封工作邮件的回复' in system:
            out = '您好：\n\n邮件已收到，谢谢。我们会按邮件中的要求尽快处理，【待补充：具体时间】前给您答复。\n\n（演示模式：由本机生成，没有联网。）'
            points = [line[2:] for line in text.splitlines() if line.startswith('- ')]
            if points:  # 助理 drafts carry their points.
                out = '您好：\n\n' + '\n'.join(f'{i}. {p}' for i, p in enumerate(points, 1)) + '\n\n盼复，谢谢。\n\n（演示模式：由本机生成，没有联网。）'
        elif '用户口述了这封邮件要说什么' in system:  # 口述成信: the words, as a letter.
            said = messages[1]['content'].partition('用户的口述：\n')[2].split('\n\n要')[0].split('\n<<<')[0].strip()
            want = text.partition('修改意见：')[2].split('\n')[0]
            out = f'您好：\n\n{said}\n\n如有问题，请随时与我联系。谢谢！\n\n（演示模式：由本机生成，没有联网{f"；已按「{want}」修改" if want else ""}。）'
        elif '起草一封新邮件的正文' in system:
            out = 'Dear all,\n\nFollowing up on this task: 【待补充：具体内容】.\n\nBest regards\n\n（演示模式：由本机生成，没有联网。）'
        else:
            body = text.split('\n', 2)[2].rsplit('\n', 1)[0] if text.count('\n') >= 3 else text
            out = body.strip() + '\n\n（演示模式：这是本机生成的改写示例，没有联网。）'
        for i in range(0, len(out), 6):
            if cancelled():
                raise AppError('AI_CANCELLED')
            time.sleep(0.04)
            on_delta(out[i:i + 6])
        return out, 0

    def complete(self, messages, tools=None, max_tokens=800, json_mode=False):
        """问邮箱 in demo mode: search with the question's first words, read the top
        hit, answer citing it. Scripted, no network."""
        import json
        import re
        import time
        time.sleep(0.5)
        results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
        call = lambda name, args: ({'content': '', 'tool_calls': [{'id': f'call{len(results)}', 'name': name,
                                                                    'arguments': json.dumps(args, ensure_ascii=False)}]}, 0)
        question = next(m['content'] for m in reversed(messages) if m['role'] == 'user')
        here = next((re.match(r'用户正在看的邮件是 (M\d+)｜([^｜]*)', m['content']) for m in messages
                     if m['role'] == 'system' and m['content'].startswith('用户正在看的邮件是')), None)
        if here and re.search(r'这封|这件事|它', question):
            if tools and not results:
                return call('read_mail', {'ref': here.group(1)})
            read = results[-1] if results else {}
            gist = read.get('text', '')[:60].replace('\n', ' ') if not read.get('encrypted') else '（加密邮件，没有阅读正文）'
            return {'content': f'演示模式：你正在看的「{here.group(2)}」开头写的是：{gist} [{here.group(1)}]\n（演示模式由本机生成，没有联网。）',
                    'tool_calls': []}, 0
        task = next((re.match(r'用户附带了当前任务 (T\d+)｜', m['content']) for m in messages
                     if m['role'] == 'system' and m['content'].startswith('用户附带了当前任务')), None)
        offered = {s['function']['name'] for s in tools or ()}
        op = re.search(r'标为已读|标记为已读|标为未读|取消星标|星标|移到|移动到|删除', question)
        if op and ('plan_mail' in offered or results) and not re.search(r'任务', question):  # 操作类工具 A3: search, then a plan.
            action = {'标为已读': 'read', '标记为已读': 'read', '标为未读': 'unread', '取消星标': 'unflag', '星标': 'flag',
                      '移到': 'move', '移动到': 'move', '删除': 'trash'}[op.group(0)]
            quoted = re.search(r'「(.+?)」', question)
            if not results:
                return call('search_mail', {'keywords': quoted.group(1) if quoted else '评审'})
            if len(results) == 1:
                if not results[0].get('search'):
                    return {'content': '演示模式：没有找到要操作的邮件。（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
                folder = re.search(r'(?:移到|移动到)「?([^」，。]+?)」?(?:文件夹)?$', question)
                return call('plan_mail', {'action': action, 'search': results[0]['search'], 'folder': folder.group(1) if folder else '',
                                          'summary': '演示'})
            plan = results[-1]
            if 'error' in plan:
                return {'content': f'演示模式：这项操作不能执行：{plan["error"]}', 'tool_calls': []}, 0
            return {'content': f'演示模式：找到 {plan["count"]} 封，已拟好计划，请在下面的卡片上核对后点「执行」，执行后可以撤销。\n'
                               '（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
        named = re.search(r'#\s*(\d{1,5})|任务\s*(\d{1,5})', question)
        target = f'T{named.group(1) or named.group(2)}' if named else task.group(1) if task else ''
        group = re.search(r'回给(.+?组)', question)
        drafting = re.search(r'回.{0,4}邮件|回一封|回给|问问|起草', question)
        if target and drafting and ('draft_reply' in offered or any('mails' in r for r in results)):
            done = {'plan' if 'plan' in r else 'groups' if 'groups' in r else 'other' for r in results}
            thread = next((r for r in results if 'mails' in r), None)
            if thread is None:  # 个人工作助理 W3: the task's conversation, a group if named, then a draft plan.
                return call('task_thread', {'task': target})
            if not thread['mails']:  # W4: candidates to link, from a search.
                found = next((r for r in results if 'rows' in r), None)
                if 'suggest_links' in offered and found is None:
                    return call('search_mail', {'keywords': '评审'})
                if found and found['rows'] and 'plan' not in done:
                    return call('suggest_links', {'task': target, 'mails': [r['ref'] for r in found['rows'][:3]]})
                if 'plan' in done:
                    return {'content': f'演示模式：这条任务还没有关联邮件。我按任务内容找到几封可能相关的，请在下面的卡片上勾选后点「关联」，'
                                       f'再让我起草 [{target}]\n（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
                return {'content': f'演示模式：这条任务没有关联邮件，也没有找到相关的邮件 [{target}]', 'tool_calls': []}, 0
            if group and 'groups' not in done:
                return call('list_groups', {'task': target})
            if 'plan' not in done:
                found = next((r for r in results if 'groups' in r), {'groups': []})['groups']
                ref = next((g['ref'] for g in found if group and g['name'] == group.group(1)), '')
                if group and not ref:
                    return {'content': f'演示模式：没有找到收件人组「{group.group(1)}」，请确认组名。', 'tool_calls': []}, 0
                return call('draft_reply', {'mail': thread['last_from_other'] or thread['last'], 'task': target, 'group': ref,
                                            'purpose': '告知已完成' if '完成' in question else '询问还缺的具体信息',
                                            'points': ['请对方确认还缺哪些信息', '说明我们这边的进度']})
            return {'content': f'演示模式：已拟好回复计划，请在下面的卡片上选收件人、核对要点后点「起草」[{target}]\n（演示模式由本机生成，没有联网。）',
                    'tool_calls': []}, 0
        change = re.search(r'完成|搞定|关闭|取消|不做了|记一下|进展|改为|改成', question)
        if change and target and ('plan_tasks' in offered or results):  # 个人工作助理 W2: a plan to confirm.
            if not results:
                return call('read_task', {'ref': target})
            if len(results) == 1:
                if 'error' in results[0]:
                    return {'content': f'演示模式：没有找到任务 {target}。', 'tool_calls': []}, 0
                if re.search(r'完成|搞定|关闭', question):
                    action = {'op': 'complete', 'task': target}
                elif re.search(r'取消|不做了', question):
                    action = {'op': 'cancel', 'task': target, 'reason': '用户说不做了'}
                else:
                    words = re.sub(r'^.*?(记一下|进展|改为|改成)[：:，, ]*', '', question).strip(' 。') or question
                    action = {'op': 'progress', 'task': target, 'text': words}
                return call('plan_tasks', {'summary': f'{ {"complete": "完成", "cancel": "取消", "progress": "记进展"}[action["op"]]} #{target[1:]}（演示）','actions': [action]})
            plan = results[-1]
            if not any(i.get('ok') for i in plan.get('items', [])):
                problem = next((i.get('problem') for i in plan.get('items', []) if i.get('problem')), '') or plan.get('error', '')
                return {'content': f'演示模式：这项操作不能执行：{problem} [{target}]', 'tool_calls': []}, 0
            return {'content': f'演示模式：已拟好计划，请在下面的卡片上核对后点「执行」[{target}]\n（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
        if task and re.search(r'这个任务|该任务|这条任务|此任务|这项任务', question):  # 个人工作助理: the task brought along.
            if tools and not results:
                return call('read_task', {'ref': task.group(1)})
            f = (results[-1] if results else {}).get('fields') or {}
            return {'content': f'演示模式：这条任务「{f.get("content", "")[:40]}」现在是{f.get("status", "")}' +
                    (f'，下一步：{f["next"][:40]}' if f.get('next') else '') + f' [{task.group(1)}]\n（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
        if 'list_tasks' in offered and re.search(r'任务|要我做|待办|待我处理', question):
            if not results:
                return call('list_tasks', {'mine': True})
            if len(results) == 1 and 'pending_mail' in offered:
                return call('pending_mail', {})
            lines = [f'{i}. {r["content"][:30]}（{r["status"]}' + (f'，{r["due"]} 前' if r['due'] else '') + f'）[{r["ref"]}]'
                     for i, r in enumerate(results[0]['rows'][:5], 1)]
            mails = [f'- {r["ask"] or r["subject"]}' + (f'（{r["due"]} 前）' if r['due'] else '') + f' [{r["ref"]}]'
                     for r in (results[1]['rows'] if len(results) > 1 else [])[:3]]
            text = f'演示模式：你负责的未完成任务有 {results[0]["total"]} 条：\n' + '\n'.join(lines) if lines else '演示模式：没有你负责的未完成任务。'
            if mails:
                text += '\n待我处理的邮件：\n' + '\n'.join(mails)
            return {'content': text + '\n（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
        if tools and not results:
            words = re.findall(r'[A-Za-z0-9]+|[一-龥]{2}', question)
            return call('search_mail', {'keywords': words[0] if words else question[:2]})
        rows = (results[0].get('rows') or []) if results else []
        if tools and rows and len(results) == 1:
            return call('read_mail', {'ref': rows[0]['ref']})
        if not rows:
            return {'content': '演示模式：没有找到相关的邮件。（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0
        read = results[-1] if len(results) > 1 else {}
        text = f'演示模式：找到 {results[0]["total"]} 封相关邮件，最相关的是「{rows[0]["subject"]}」，由 {rows[0]["from"]} 发出 [{rows[0]["ref"]}]。'
        if read.get('encrypted'):
            text += '\n这封是加密邮件，没有阅读正文。'
        elif read.get('text'):
            text += '\n正文开头：' + read['text'][:60].replace('\n', ' ') + f' [{rows[0]["ref"]}]'
        return {'content': text + '\n（演示模式由本机生成，没有联网。）', 'tool_calls': []}, 0


def _demo_chart(width=480, height=300):
    """A synthetic bar chart PNG (no text) for the attachment preview."""
    import struct
    import zlib
    bars = [(60, 120, (37, 99, 235)), (150, 200, (124, 58, 237)), (240, 90, (5, 150, 105)), (330, 170, (234, 88, 12))]
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = (255, 255, 255) if (x + y) % 2 or y % 40 else (226, 232, 240)
            for left, h, c in bars:
                if left <= x < left + 60 and height - 30 - h <= y < height - 30:
                    color = c
            if y == height - 30 or x == 30:
                color = (100, 116, 139)
            rows += bytes(color)
    chunk = lambda tag, data: struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(rows))) + chunk(b'IEND', b''))


def _demo_pdf():
    """A small two-page PDF (Helvetica text and boxes only) for the attachment preview."""
    pages = []
    for n, lines in enumerate((['Design Review Report (demo)', 'Hongyan mail client - synthetic data', '1. Sidebar layout', '2. Folder management',
                                '3. Spam handling', '4. Attachment preview'], ['Page 2', 'Action items', '- Verify on the real server', '- Package and test'])):
        text = ''.join(f'BT /F1 {22 if i == 0 else 13} Tf 72 {760 - i * 34} Td ({s}) Tj ET\n' for i, s in enumerate(lines))
        boxes = '0.15 0.39 0.92 rg 72 300 120 160 re f 0.49 0.23 0.93 rg 212 300 120 220 re f 0.02 0.59 0.41 rg 352 300 120 100 re f\n' if n == 0 else ''
        pages.append(text + boxes)
    count = len(pages)
    objects = ['<< /Type /Catalog /Pages 2 0 R >>',
               '<< /Type /Pages /Kids [' + ' '.join(f'{4 + 2 * i} 0 R' for i in range(count)) + f'] /Count {count} >>',
               '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i, content in enumerate(pages):
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>')
        objects.append(f'<< /Length {len(content.encode())} >>\nstream\n{content}endstream')
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for i, body in enumerate(objects):
        offsets.append(len(out))
        out += f'{i + 1} 0 obj\n{body}\nendobj\n'.encode()
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    out += f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return bytes(out)


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
