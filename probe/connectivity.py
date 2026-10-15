"""Throwaway Coremail compatibility probe, not a complete mail client.

Only fixed self-addressed test messages are sent. Reports never contain normal
mail subjects, bodies, mailbox identifiers, credentials, or session tokens.
"""
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, getaddresses
import hashlib
import time
import uuid


def _messages(envelope):
    value = envelope.get('var')
    if isinstance(value, list):
        return value
    # Some server versions wrap the returned collection with pagination.
    if isinstance(value, dict) and isinstance(value.get('messages'), list):
        return value['messages']
    raise ValueError('Unexpected message-list response structure')


def _list(session, folder_id, limit):
    return _messages(session.call('mbox:listMessages', {
        'fid': folder_id, 'start': 0, 'limit': limit, 'mode': 'count',
        'order': 'date', 'desc': True, 'returnTotal': True,
        'returnTag': True, 'summaryWindowSize': 0,
    }))


def _mime(raw):
    if not isinstance(raw, bytes) or not raw:
        raise ValueError('No raw message returned')
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not message.get('From') or not any(message.get(k) for k in ('To', 'Subject', 'Date', 'Message-ID')):
        raise ValueError('Response was not a recognizable MIME message')
    return message


def verify_read(session, folder_id=0, limit=5):
    folders = session.call('mbox:getAllFolders', {}).get('var')
    if not isinstance(folders, list):
        raise ValueError('Unexpected folder-list response structure')
    messages = _list(session, folder_id, limit)
    report = {'folder_count': len(folders), 'listed_messages': len(messages)}
    if not messages:
        report['read'] = {'status': 'skipped_empty_inbox'}
        return report
    mid = messages[0].get('id')
    if not isinstance(mid, str) or not mid:
        raise ValueError('Message identifier was absent')
    raw = session.get_message(mid)
    _mime(raw)
    report['read'] = {'status': 'passed', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return report


def _account(account):
    address = parseaddr(account)[1]
    if address != account or '\r' in account or '\n' in account or account.count('@') != 1:
        raise ValueError('Use one plain email address for your own account')
    return address


def build_test_payload(account, marker):
    account = _account(account)
    return {
        'action': 'deliver', 'returnInfo': True, 'autosaveHitCounter': True,
        'attrs': {
            'account': account, 'to': [account], 'cc': [], 'bcc': [],
            'subject': '[Coremail compatibility test] ' + marker,
            'content': 'This is a self-addressed connectivity test.\nMarker: ' + marker + '\n',
            'isHtml': False, 'saveSentCopy': True, 'attachments': [],
        },
    }


def _body_text(message):
    if message.is_multipart():
        return '\n'.join(_body_text(part) for part in message.iter_parts())
    if message.get_content_maintype() != 'text':
        return ''
    content = message.get_content()
    return content if isinstance(content, str) else ''


def verify_roundtrip(session, account, folder_id=0, timeout_seconds=45, interval_seconds=3):
    if folder_id != 0:
        raise ValueError('Delivery verification is restricted to the Coremail inbox')
    account = _account(account)
    marker = 'cmprobe-' + uuid.uuid4().hex
    payload = build_test_payload(account, marker)
    initialized = session.call('%mbox/compose', {
        'action': 'continue', 'returnInfo': True, 'attrs': payload['attrs'],
    })
    value = initialized.get('var')
    compose_id = value if isinstance(value, str) else value.get('id') if isinstance(value, dict) else None
    if not isinstance(compose_id, str) or not compose_id:
        raise ValueError('Compose initialization did not return an identifier')
    payload['id'] = compose_id
    session.call('%mbox/compose', payload)
    report = {
        'marker': marker,
        'send': {'status': 'accepted'},
        'delivery': {'status': 'not_observed'},
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for candidate in _list(session, folder_id, 30):
            if candidate.get('subject') != payload['attrs']['subject']:
                continue
            mid = candidate.get('id')
            if not isinstance(mid, str) or not mid:
                continue
            raw = session.get_message(mid)
            message = _mime(raw)
            sender = parseaddr(str(message.get('From', '')))[1]
            recipients = [address.casefold() for _, address in getaddresses([str(message.get('To', ''))])]
            if (sender.casefold() == account.casefold()
                    and account.casefold() in recipients
                    and str(message.get('Subject', '')) == payload['attrs']['subject']
                    and marker in _body_text(message)):
                report['delivery'] = {'status': 'passed', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                return report
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval_seconds, remaining))
    return report
