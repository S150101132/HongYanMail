"""Read-only: folder tree, spam/permanent-delete switches, sender lists and the unread filter.

What webmail does (analysis/web-login):
- folders: X.03931.js module 88812 -- createFolders(items) = mbox:createUserFolders
  {items:[{name, parent}], mboxa}, updateFolders(items) = mbox:updateFolders
  {items:[{id, name}|{id, parent, before|after}]}; S.03931.js deletes with
  mbox:deleteFolders {ids:[fid]}, after doEmptyFolder when the folder holds mail.
  trimName: at most 24 width units (CJK = 2), none of %'"\\/:*?<>|=,^.
- empty / purge: mbox:emptyFolder {id, permanently}; mbox:deleteMessages {ids} is
  "彻底删除" (realDeleteMails), offered when X.M.enableRealDelete.
- spam: report = mbox:updateMessageInfos {ids, attrs:{fid:5}, reportType:'spam'};
  not spam = {attrs:{fid:1}, reportType:'notspam'}; shown when X.S.enableReportSpam.
- lists: user attributes safelist / refuselist, comma separated, written with
  user:setAttrs {attrs:{refuselist:'a,b'}}; '*@domain' entries are whole domains.
- mark all read: webmail lists the unread ids (filter) and updates them.

This probe only reads. It records key names, types, counts and booleans -- never
folder names, addresses or list entries.
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from coremail_desktop.models import AccountConfig  # noqa: E402
from coremail_desktop.protocol.coremail import CoremailAdapter, DesktopSession  # noqa: E402
from tools.probes.probe_rules_tags_cal import attempt, shape  # noqa: E402
from tools.probes.probe_smime_send2 import ask  # noqa: E402

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else 'wangbiao@inspur.com'
FLAGS = ('enableReportSpam', 'enableReportPhishing', 'isReportPhishingUser', 'enableRealDelete', 'enableDelete', 'enableMoveTo',
         'enableUpdateMailStatus', 'maxFolderNestLevel', 'pref_max_folder_nest_level', 'folderNestLevel', 'maxFolderCount',
         'enableFolderManage', 'supportFolder', 'supportSubFolder', 'enableSafeList', 'enableRefuseList', 'supportBlackList')


def page_flags(session):
    raw, _ = session._fetch(Request(urljoin(session.base_url, 'XT/index.jsp') + '?' + urlencode({'sid': session.sid})))
    text = raw[:4 * 1024 * 1024].decode('utf-8', errors='replace')
    out = {}
    for key in FLAGS:
        match = re.search(r'["\']?\b' + key + r'["\']?\s*:\s*(true|false|-?\d+)', text)
        out[key] = (match.group(1) == 'true' if match.group(1) in ('true', 'false') else int(match.group(1))) if match else None
    out['other_keys'] = sorted(set(re.findall(r'\b(\w*(?:[Ss]pam|[Jj]unk|RealDelete|[Ff]older[A-Z]\w*|[Nn]est\w*|[Rr]efuse\w*|[Ss]afe[Ll]ist)\w*)\b', text)))[:60]
    return out


def list_entries(value):
    """Count and kinds of a comma-separated list attribute, without its entries."""
    if not isinstance(value, str):
        return {'type': type(value).__name__}
    items = [x for x in re.split(r',\s*', value) if x]
    return {'count': len(items), 'domain_entries': sum(x.startswith('*@') or x.startswith('@') for x in items),
            'address_entries': sum('@' in x and not x.startswith(('*@', '@')) for x in items), 'other': sum('@' not in x for x in items)}


def unread_rows(adapter, params):
    ok, listed = attempt(lambda: adapter._call('mbox:listMessages', {'fid': 1, 'start': 0, 'limit': 100, 'mode': 'count', 'order': 'date',
                                                                     'desc': True, 'returnTotal': True, 'summaryWindowSize': 0, **params}, True))
    if not ok:
        return {'ok': False, 'code': listed}
    rows = listed.get('var')
    total = listed.get('total')
    if isinstance(rows, dict):
        total, rows = rows.get('total', total), rows.get('messages')
    rows = [r for r in rows or [] if isinstance(r, dict)]
    return {'ok': True, 'total': total, 'rows': len(rows), 'unread_rows': sum(not (r.get('flags') or {}).get('read') for r in rows)}


def main():
    password = ask('鸿雁 · 文件夹/垃圾邮件/黑白名单检查（只读）', f'{ACCOUNT} 邮箱密码（仅本次内存使用，不发信、不修改）:')
    if not password:
        return 130
    adapter = CoremailAdapter()
    adapter.login(AccountConfig('primary', 'https://newmail.inspur.com/coremail/', ACCOUNT), password)
    del password
    session = adapter._session
    session.ALLOWED = DesktopSession.ALLOWED | {'user:getAttrs'}
    report = {'date': time.strftime('%Y-%m-%d %H:%M')}
    try:
        report['page_flags'] = attempt(lambda: page_flags(session))
        ok, folders = attempt(lambda: adapter._call('mbox:getAllFolders', {'stats': True}, True))
        rows = folders.get('var') if ok else []
        rows = [f for f in rows or [] if isinstance(f, dict)]
        report['folders'] = {'count': len(rows), 'keys': sorted({k for f in rows for k in f}),
                             'flag_keys': sorted({k for f in rows if isinstance(f.get('flags'), dict) for k in f['flags']}),
                             'system': sorted(str(f['id']) for f in rows if (f.get('flags') or {}).get('system')),
                             'user_ids_over_100': sum(str(f.get('id')).isdigit() and int(f['id']) >= 100 for f in rows),
                             'item_system': shape(next((f for f in rows if (f.get('flags') or {}).get('system')), None)),
                             'item_user': shape(next((f for f in rows if not (f.get('flags') or {}).get('system')), None))}
        parents = [f.get('parentId', f.get('parent')) for f in rows]
        report['folders']['with_parent'] = sum(p not in (None, 0, '0', -1, '') for p in parents)
        report['folders']['parent_values_kind'] = sorted({type(p).__name__ for p in parents})
        ok, attrs = attempt(lambda: adapter._call('user:getAttrs', {'optionalAttrIds': ['safelist', 'refuselist']}, True))
        report['lists_ok'] = ok
        if ok and isinstance(attrs.get('var'), dict):
            report['lists'] = {k: list_entries(v) for k, v in attrs['var'].items()}
        else:
            report['lists'] = attrs if not ok else shape(attrs)
        report['unread_plain'] = unread_rows(adapter, {})
        for name, params in (('filter_flags', {'filter': {'flags': {'read': False}}}), ('filter_read', {'filter': {'read': False}})):
            report['unread_' + name] = unread_rows(adapter, params)
        ok, found = attempt(lambda: session.search_messages('', 0, 100, {'fid': 1, 'read': 'false'}))
        report['unread_search'] = ({'total': found.get('total'), 'rows': len(found.get('var') or []),
                                    'unread_rows': sum(not (r.get('flags') or {}).get('read') for r in found.get('var') or [] if isinstance(r, dict))}
                                   if ok else found)
    finally:
        adapter.close()
    out = ROOT / 'analysis' / f'mail-management-shape-{time.strftime("%Y-%m-%d")}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
