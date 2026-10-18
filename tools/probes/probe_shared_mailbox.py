"""Read-only: which shared / department mailboxes can this account open, and how?

Webmail (analysis/web-login X.03931.js module 88812) lists them with
mbox:listSharedAccessMbox {showHidden:true, v:2}; every mail API then takes
mboxa='@'+id. Its folder tree comes from mail.jsp getAllFolders, where node -6
holds the shared mailboxes with their folders. This probe checks:

- the list shape and each mailbox's ACL flags (send, rw, recallable, sso, status)
- folders: mbox:getAllFolders with mboxa in the body, and mail.jsp getAllFolders
- messages: mbox:listMessages with mboxa in the body vs in the query string
- one already-read message: getMessageInfos and getMessageData with mboxa
  (a read message, so fetching it changes no colleague's read state)

Records only key names, types, counts and booleans: no addresses, names,
subjects or content. Nothing is sent, moved or flagged.
"""
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.errors import AppError  # noqa: E402
from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession  # noqa: E402
from tools.probes.probe_smime_send2 import ask, shape  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
EXTRA = {'mbox:listSharedAccessMbox', 'mbox:getShareACL', 'oab:get', 'mbox:getMessageInfos'}


def attempt(fn):
    try:
        return True, fn()
    except AppError as error:
        return False, error.code
    except Exception as error:  # noqa: BLE001 - shape probe: record the class only
        return False, type(error).__name__


def flag(value):
    return value if type(value) in (bool, int) or value is None else type(value).__name__


def folder_summary(rows):
    if not isinstance(rows, list):
        return shape(rows)
    ids = [r.get('id') for r in rows if isinstance(r, dict)]
    return {'count': len(rows), 'id_types': sorted({type(i).__name__ for i in ids}),
            'has_inbox_1': any(str(i) == '1' or str(i).startswith('1@') for i in ids),
            'with_stats': sum(1 for r in rows if isinstance(r, dict) and isinstance(r.get('stats'), dict)),
            'item_keys': sorted({k for r in rows if isinstance(r, dict) for k in r})[:40],
            'stats_keys': sorted({k for r in rows if isinstance(r, dict) and isinstance(r.get('stats'), dict) for k in r['stats']})}


def jsp(session, func, params):
    url = urljoin(session.base_url, 'XT/jsp/mail.jsp') + '?' + urlencode({'func': func, 'sid': session.sid})
    raw, _ = session._fetch(Request(url, data=urlencode(params).encode('utf-8'),
                                    headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'text/x-json'}))
    return session._decode_response(raw)


def call_query(session, func, payload, **query):
    """Like session.call but with extra query-string parameters (webmail's &mboxa= form)."""
    url = session._rpc_url(func, **query)
    raw, _ = session._fetch(Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                    headers={'Accept': 'text/x-json', 'Content-Type': 'text/x-json; tz="Asia/Riyadh"'}))
    return session._decode_response(raw)


def list_shape(response):
    var = response.get('var') if isinstance(response, dict) else None
    rows = var.get('messages') if isinstance(var, dict) else var
    total = var.get('total') if isinstance(var, dict) else response.get('total')
    if not isinstance(rows, list):
        return {'shape': shape(response)}, []
    return {'count': len(rows), 'total_type': type(total).__name__, 'total': total if type(total) is int else None,
            'item_keys': sorted({k for r in rows if isinstance(r, dict) for k in r})[:50],
            'read_rows': sum(1 for r in rows if isinstance(r, dict) and isinstance(r.get('flags'), dict) and r['flags'].get('read') is True)}, rows


def main():
    password = ask('鸿雁 · 共享邮箱检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    session.ALLOWED = DesktopSession.ALLOWED | EXTRA
    report = {'date': time.strftime('%Y-%m-%d %H:%M'), 'mailboxes': []}
    try:
        own = adapter._call('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 20, 'returnTotal': True}, True)
        own_ids = {r.get('id') for r in (own.get('var') or []) if isinstance(r, dict)}
        ok, listed = attempt(lambda: adapter._call('mbox:listSharedAccessMbox', {'showHidden': True, 'v': 2}, True))
        report['list_ok'] = ok
        report['list_shape'] = shape(listed) if ok else listed
        rows = listed.get('var') if ok and isinstance(listed.get('var'), list) else []
        report['mailbox_count'] = len(rows)
        ok, identities = attempt(lambda: session.account_identities())
        shared_emails = {str(r.get('email', '')).casefold() for r in rows if isinstance(r, dict) and r.get('email')}
        report['identities_count'] = len(identities) if ok else identities
        report['identities_include_shared'] = sum(1 for i in identities if i.casefold() in shared_emails) if ok else None
        ok, tree = attempt(lambda: jsp(session, 'getAllFolders', {'stats': 'true'}))
        report['jsp_folders_ok'] = ok
        if ok:
            nodes = tree.get('var') if isinstance(tree.get('var'), list) else []
            shared = next((n for n in nodes if isinstance(n, dict) and n.get('id') == -6), None)
            report['jsp_folders'] = {'count': len(nodes), 'node_ids': [n.get('id') for n in nodes if isinstance(n, dict) and '@' not in str(n.get('id'))][:40],
                                     'shared_node': shared is not None,
                                     'shared_node_keys': sorted(shared) if shared else None,
                                     'shared_items': shape((shared or {}).get('items')) if shared else None}
            if shared and isinstance(shared.get('items'), list):
                report['jsp_shared_items'] = [{'keys': sorted(i), 'id_type': type(i.get('id')).__name__,
                                               'id_has_at': isinstance(i.get('id'), str) and '@' in i['id'],
                                               'children': folder_summary(i.get('children')) if 'children' in i else None,
                                               'child_ids_sample': [str(c.get('id')).split('@')[0] + ('@…' if '@' in str(c.get('id')) else '')
                                                                    for c in (i.get('children') or [])[:8] if isinstance(c, dict)],
                                               'child_mboxa_types': sorted({type(c.get('mboxa')).__name__ for c in (i.get('children') or []) if isinstance(c, dict)}),
                                               'acl': {k: flag(i.get(k)) for k in ('send', 'rw', 'recallable', 'sso', 'status', 'remote')}}
                                              for i in shared['items'][:5] if isinstance(i, dict)]
        for index, row in enumerate(rows[:5]):
            if not isinstance(row, dict):
                continue
            entry = {'index': index, 'keys': sorted(row), 'id_type': type(row.get('id')).__name__,
                     'acl': {k: flag(row.get(k)) for k in ('send', 'rw', 'recallable', 'sso', 'status', 'hidden', 'type')},
                     'has_email': isinstance(row.get('email'), str) and '@' in row.get('email', ''),
                     'has_remark': bool(row.get('remark'))}
            mboxa = '@' + str(row.get('id'))
            ok, folders = attempt(lambda: adapter._call('mbox:getAllFolders', {'stats': True, 'mboxa': mboxa}, True))
            entry['folders_body'] = folder_summary(folders.get('var')) if ok else folders
            ok, body = attempt(lambda: adapter._call('mbox:listMessages', {'fid': 1, 'mboxa': mboxa, 'start': 0, 'limit': 10, 'mode': 'count', 'order': 'date', 'desc': True, 'returnTotal': True, 'summaryWindowSize': 0}, True))
            entry['list_body_ok'] = ok
            body_rows = []
            if ok:
                entry['list_body'], body_rows = list_shape(body)
                entry['list_body_overlaps_own'] = bool(own_ids & {r.get('id') for r in body_rows if isinstance(r, dict)})
            else:
                entry['list_body'] = body
            ok, query = attempt(lambda: call_query(session, 'mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 10, 'returnTotal': True, 'summaryWindowSize': 0}, mboxa=mboxa))
            entry['list_query_ok'] = ok
            if ok:
                q_shape, q_rows = list_shape(query)
                entry['list_query'] = q_shape
                entry['list_query_same_as_body'] = [r.get('id') for r in q_rows] == [r.get('id') for r in body_rows]
                entry['list_query_same_as_own'] = {r.get('id') for r in q_rows} <= own_ids and bool(q_rows)
            else:
                entry['list_query'] = query
            read_row = next((r for r in body_rows if isinstance(r, dict) and isinstance(r.get('flags'), dict) and r['flags'].get('read') is True), None)
            if read_row:
                mid = read_row['id']
                ok, infos = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [mid], 'mboxa': mboxa}, True))
                entry['infos_ok'] = ok
                if ok:
                    entry['infos_keys'] = sorted({k for i in (infos.get('var') or []) if isinstance(i, dict) for k in i})[:60]
                ok, without = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [mid]}, True))
                entry['infos_without_mboxa'] = shape(without.get('var')) if ok else without
                raw_ok, raw = attempt(lambda: session._fetch(Request(session._rpc_url('mbox:getMessageData', mid=mid, mode='text', mboxa=mboxa)))[0])
                entry['data_query_is_mime'] = bool(raw_ok and isinstance(raw, bytes) and raw.strip() and not raw.lstrip().startswith((b'{', b'<')))
                entry['data_query_bytes'] = len(raw) if raw_ok and isinstance(raw, bytes) else raw
                raw_ok, raw = attempt(lambda: session._fetch(Request(session._rpc_url('mbox:getMessageData', mid=mid, mode='text')))[0])
                entry['data_without_mboxa_is_mime'] = bool(raw_ok and isinstance(raw, bytes) and raw.strip() and not raw.lstrip().startswith((b'{', b'<')))
                after = attempt(lambda: adapter._call('mbox:getMessageInfos', {'ids': [mid], 'mboxa': mboxa}, True))
                entry['still_read_after_fetch'] = bool(after[0] and any(isinstance(i, dict) and (i.get('flags') or {}).get('read') is True for i in after[1].get('var') or []))
            if row.get('email'):
                ok, acl = attempt(lambda: adapter._call('mbox:getShareACL', {'v': 2, 'email': row['email']}, True))
                entry['share_acl'] = shape(acl.get('var')) if ok else acl
                ok, names = attempt(lambda: adapter._call('oab:get', {'items': [row['email']]}, True))
                entry['oab_get'] = shape(names.get('var')) if ok else names
            report['mailboxes'].append(entry)
        ok, own_acl = attempt(lambda: adapter._call('mbox:getShareACL', {'v': 2, 'email': ACCOUNT}, True))
        report['own_share_acl'] = shape(own_acl.get('var')) if ok else own_acl
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'shared-mailbox-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
