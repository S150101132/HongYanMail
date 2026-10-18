"""Real-account check of rules, auto-reply and tags (writes approved by the user 2026-10-18).

Approved writes, each undone in the same run:
1. rules: add "HY-RULE-TEST" (subject contains HY-RULE-TEST -> flag), edit it, move it one place
   up and back, delete it. The user's own rules are not edited; their order is restored.
2. auto-reply: save the current settings back unchanged (stays off) -- only when the codec
   reproduces the stored attributes exactly; otherwise the write is skipped.
3. tags: send one mail to self ("HY-TAG-TEST"), create tag HY-TEST, rename it, put it on that
   mail and take it off, delete the tag.
With --respond-invite (asked separately): answer the newest self-sent invitation "tentative".

Records only pass/fail, codes, counts, key names and parameter variants -- no content.
"""
import json
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.app.backend import MailBackend  # noqa: E402
from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.protocol import rules as R  # noqa: E402

ACCOUNT = next((a for a in sys.argv[1:] if '@' in a), 'wangbiao@inspur.com')
RULE, TAG = 'HY-RULE-TEST', 'HY-TEST'
REMOVE = lambda i: ({'ids': [i]}, {'items': [i]}, {'id': i}, {'items': [{'id': i}]})  # Candidate parameter shapes.
ONLY = {a[2:] for a in sys.argv[1:] if a in ('--rules', '--autoreply', '--tags', '--respond-invite')}


def ask_password():
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring('鸿雁 · 规则/自动回复/标签验证', f'{ACCOUNT} 的密码（仅本次内存使用）:\n'
                                      '会临时添加并删除 HY-RULE-TEST 规则、HY-TEST 标签，给自己发 1 封测试信', show='*', parent=root)
    finally:
        root.destroy()


def keys(value, depth=0):
    if isinstance(value, dict) and depth < 4:
        return {k: keys(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [keys(v, depth + 1) for v in value[:4]]
    return value if type(value) in (bool, int) or value is None or (isinstance(value, str) and len(value) <= 24 and '@' not in value) else type(value).__name__


def main():
    password = ask_password()
    if not password:
        return 130
    data = Path(tempfile.mkdtemp(prefix='hongyan-verify-', dir=str(ROOT / 'build')))
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'checks': {}}
    checks = report['checks']

    def check(name, fn):
        start = time.perf_counter()
        try:
            value = fn()
            checks[name] = {'status': 'passed', 'ms': round((time.perf_counter() - start) * 1000), **(value or {})}
        except AppError as error:
            checks[name] = {'status': 'failed', 'code': error.code}
        except Exception as error:  # Only the type name; no message text.
            checks[name] = {'status': 'failed', 'code': type(error).__name__}
        return checks[name]['status'] == 'passed'

    backend = MailBackend(data)
    adapter = backend.adapter
    state = {}
    try:
        if not check('login', lambda: {'ok': bool(backend.login('https://newmail.inspur.com/coremail/', ACCOUNT, password, False))}):
            return 2
        backend.folders()
        backend.tags()

        # 1. Rules ---------------------------------------------------------------
        if not ONLY or 'rules' in ONLY:
            state['order'] = [r['id'] for r in backend.rules()['rules']]
            checks['rules_before'] = {'status': 'passed', 'count': len(state['order'])}
            view = {'name': RULE, 'match': 'all', 'enabled': True, 'stop': False,
                    'conditions': [{'field': 'subject', 'op': 'contains', 'value': RULE}], 'actions': [{'type': 'flag'}]}

            def add():
                result = backend.save_rule(view)
                state['rule'] = result['saved']
                stored = adapter.mail_rules()
                raw = next(r for r in stored if r['id'] == state['rule'])
                return {'stored_keys': keys(raw), 'position': [r['id'] for r in stored].index(state['rule'])}
            if not check('rule_add', add):
                # The server may store a different action shape: record what it kept, if anything.
                raw = next((r for r in adapter.mail_rules() if r.get('name') == RULE), None)
                checks['rule_add']['stored_keys'] = keys(raw) if raw else None
                if raw:
                    state['rule'] = raw['id']
            if state.get('rule') is not None and checks['rule_add']['status'] == 'passed':
                check('rule_disable', lambda: {'enabled': next(r['enabled'] for r in backend.set_rule_enabled(state['rule'], False)['rules'] if r['id'] == state['rule'])})
                full = {**view, 'id': state['rule'], 'enabled': False,
                        'conditions': [{'field': 'subject', 'op': 'contains', 'value': RULE + '-2'}, {'field': 'attachment'},
                                       {'field': 'size', 'op': '>', 'value': 10 * 1048576}],
                        'actions': [{'type': 'flag'}, {'type': 'read'}, {'type': 'tag', 'tag': backend.tags()['tags'][-1]['id']},
                                    {'type': 'forward', 'to': ACCOUNT}]}

                def edit():
                    backend.save_rule(full)
                    raw = next(r for r in adapter.mail_rules() if r['id'] == state['rule'])
                    return {'stored_keys': keys(raw), 'editable': R.rule_view(raw)['editable']}
                check('rule_edit_all_types', edit)

                def order():
                    before = [r['id'] for r in backend.rules()['rules']]
                    up = [r['id'] for r in backend.move_rule(state['rule'], -1)['rules']]
                    down = [r['id'] for r in backend.move_rule(state['rule'], 1)['rules']]
                    return {'moved_up': up != before, 'restored': down == before}
                check('rule_order', order)
            if state.get('rule') is not None:
                def remove():
                    variants = []
                    for index, params in enumerate(REMOVE(state['rule'])):
                        name = '/'.join(f'{k}:{type(v).__name__}' + (f'[{type(v[0]).__name__}]' if isinstance(v, list) else '') for k, v in params.items())
                        try:
                            adapter._call('user:removeMailRules', params)
                        except AppError as error:
                            variants.append({name: error.code}); continue
                        gone = all(r['id'] != state['rule'] for r in adapter.mail_rules())
                        variants.append({name: gone})
                        if gone:
                            state['rule_removed'] = name
                            state['remove_index'] = index
                            break
                    if not state.get('rule_removed'):
                        raise AppError('RULE_NOT_REMOVED')
                    return {'variants': variants}
                check('rule_remove', remove)

        # 2. Auto-reply ----------------------------------------------------------------
        if not ONLY or 'autoreply' in ONLY:
            def autoreply():
                raw = adapter.auto_reply()
                view = R.autoreply_view(raw)
                attrs = R.autoreply_server(view, adapter.account_now())
                same = all(raw.get(k) == v for k, v in attrs.items())
                if not same:
                    return {'written': False, 'reason': 'codec would change stored values',
                            'differs': sorted(k for k, v in attrs.items() if raw.get(k) != v)}
                backend.set_auto_reply(view)
                after = adapter.auto_reply()
                return {'written': True, 'unchanged_after': all(after.get(k) == raw.get(k) for k in R.AUTOREPLY_ATTRS), 'mode': after.get('autorepmode')}
            check('autoreply_save_unchanged', autoreply)

        # 3. Tags -------------------------------------------------------------------------
        if not ONLY or 'tags' in ONLY:
            marker = 'HY-TAG-TEST ' + uuid.uuid4().hex[:8]

            def send():
                draft = backend.compose('new')
                draft.update(to=[ACCOUNT], subject=marker, html='<p>鸿雁标签功能验证邮件，可删除。</p>')
                result = backend.send(draft)
                if result['state'] != 'Accepted':
                    raise AppError('SEND_' + str(result['state']).upper())
                for _ in range(18):
                    time.sleep(5)
                    for row in backend.messages('1', 0, 20)['rows']:
                        if row['subject'] == marker:
                            state['mid'] = row['id']
                            return {'arrived': True, 'row_tags': row['tags']}
                raise AppError('NOT_DELIVERED_IN_90S')
            if check('tag_mail_to_self', send):
                check('tag_create', lambda: {'id_prefix': (state.setdefault('tag', backend.save_tag(TAG, '#2ea962')['saved']))[:1]})
            if state.get('tag'):
                check('tag_rename', lambda: {'ok': backend.save_tag(TAG + '2', '#30aed5', tag_id=state['tag'])['saved'] == state['tag'],
                                             'stored_keys': keys(next(t[1] for t in adapter.list_tags() if t[0] == state['tag']))})

                def apply():
                    backend.tag_messages([state['mid']], add=[state['tag']])
                    row = next(r for r in backend.messages('1', 0, 20)['rows'] if r['id'] == state['mid'])
                    listed = backend.tag_view(state['tag'])
                    raw = adapter._call('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 20, 'returnTag': True, 'summaryWindowSize': 0}, True).get('var') or []
                    raw_row = next((r for r in raw if r.get('id') == state['mid']), {})
                    return {'row_has_tag': state['tag'] in row['tags'], 'tag_field': keys(raw_row.get('tag')),
                            'tag_view_rows': len(listed['rows']), 'tag_view_has_mail': any(r['id'] == state['mid'] for r in listed['rows'])}
                check('tag_apply', apply)

                def untag():
                    backend.tag_messages([state['mid']], remove=[state['tag']])
                    row = next(r for r in backend.messages('1', 0, 20)['rows'] if r['id'] == state['mid'])
                    return {'removed': state['tag'] not in row['tags']}
                check('tag_remove_from_mail', untag)
                check('tag_delete', lambda: {'gone': all(t['id'] != state['tag'] for t in backend.delete_tag(state['tag'])['tags'])})
                state['tag_deleted'] = checks['tag_delete']['status'] == 'passed'

        # 4. Invitation answer (only with --respond-invite) ----------------------------
        if 'respond-invite' in ONLY:
            def respond():
                for row in backend.messages('1', 0, 40)['rows']:
                    # Only the test invitation the user sent from their own Gmail calendar.
                    if not row['fromEmail'].casefold().endswith('@gmail.com'):
                        continue
                    m = backend.open_message(row['id'], mark_read=False)
                    card = m.get('invitation')
                    if card and card['method'] == 'REQUEST' and not card['cancelled']:
                        meta = adapter.invitation_meta(row['id'])
                        result = backend.respond_invitation(row['id'], 'tentative')
                        after = adapter.invitation_meta(row['id']) or {}
                        info = (adapter._call('mbox:getMessageInfos', {'ids': [row['id']]}, True).get('var') or [{}])[0]
                        mine = lambda c: next((a.get('status', a.get('partStat')) for a in c.get('attendees') or [] if isinstance(a, dict)
                                              and str(a.get('email', '')).casefold() == ACCOUNT.casefold()), None)
                        return {'meta_keys': sorted(meta or {}), 'method': (meta or {}).get('meetingRequestMethod'), 'type': result['type'],
                                'status_before': (meta or {}).get('meetingRequestStatus'), 'status_after': after.get('meetingRequestStatus'),
                                'attendee_keys': sorted({k for a in after.get('attendees') or [] if isinstance(a, dict) for k in a}),
                                'my_status_before': mine(meta or {}), 'my_status_after': mine(after),
                                'partStat_on_mail': info.get('partStat') if isinstance(info, dict) else None}
                raise AppError('INVITE_UNAVAILABLE')
            check('invite_respond', respond)
    finally:
        cleanup = {}
        try:
            for raw in adapter.mail_rules():
                if str(raw.get('name', '')).startswith(RULE):
                    adapter._call('user:removeMailRules', REMOVE(raw['id'])[state.get('remove_index', 0)])
                    cleanup['rule'] = 'removed'
            if state.get('order'):
                now = [r['id'] for r in adapter.mail_rules()]
                cleanup['order_restored'] = now == state['order']
                if now != state['order'] and state.get('order_param'):
                    adapter._call('user:adjustMailRuleOrder', {state['order_param']: state['order']})
                    cleanup['order_restored'] = [r['id'] for r in adapter.mail_rules()] == state['order']
            for tag in adapter.list_tags():
                if str((tag[1] if len(tag) > 1 and isinstance(tag[1], dict) else {}).get('name', '')).startswith(TAG):
                    adapter.manage_tags('delete', [tag[0]])
                    cleanup['tag'] = 'deleted'
        except AppError as error:
            cleanup['error'] = error.code
        report['cleanup'] = cleanup
        backend.logout()
        shutil.rmtree(data, ignore_errors=True)
    out = ROOT / 'analysis' / f'{"invite-respond" if ONLY == {"respond-invite"} else "rules-tags"}-real-verification-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if all(c['status'] == 'passed' for c in checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
