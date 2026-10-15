import uuid
from coremail_desktop.models import DraftDocument
from coremail_desktop.compose.recipients import normalize_recipients

class ComposeService:
    @staticmethod
    def new_draft(identity):
        return DraftDocument(uuid.uuid4().hex, 0, 'new', identity, (), (), (), '', '', {})

def _headers(original, name):
    return next((value for key, value in original.headers.items() if key.lower() == name.lower()), ())

def build_reply(original, identities, reply_all=False):
    identities = {value.casefold() for value in identities}
    identity = sorted(identities)[0] if identities else ''
    target = normalize_recipients(_headers(original, 'reply-to') or _headers(original, 'from'))
    to = list(target)
    cc = []
    if reply_all:
        to.extend(normalize_recipients(_headers(original, 'to')))
        cc.extend(normalize_recipients(_headers(original, 'cc')))
    seen = set(identities)
    def unique(values):
        result = []
        for address in values:
            if address.casefold() not in seen:
                seen.add(address.casefold()); result.append(address)
        return tuple(result)
    to, cc = unique(to), unique(cc)
    message_id = ' '.join(_headers(original, 'message-id'))
    refs = ' '.join(_headers(original, 'references'))
    reference_headers = {'In-Reply-To': message_id, 'References': (refs + ' ' + message_id).strip()} if message_id else {}
    subject = next(iter(_headers(original, 'subject')), '')
    if not subject.lower().startswith('re:'): subject = 'Re: ' + subject
    return DraftDocument(uuid.uuid4().hex, 0, 'reply_all' if reply_all else 'reply', identity, to, cc, (), subject, '\n\n' + '\n'.join('> ' + line for line in original.text.splitlines()), reference_headers, original.encryption_state != 'none')

def build_forward(original, identity):
    subject = next(iter(_headers(original, 'subject')), '')
    return DraftDocument(uuid.uuid4().hex, 0, 'forward', identity, (), (), (), 'Fwd: ' + subject, '\n\n---------- 转发正文（不含原附件） ----------\n' + original.text, {}, original.encryption_state != 'none')
