import uuid
from coremail_desktop.models import DraftDocument
from coremail_desktop.compose.recipients import normalize_recipients

class ComposeService:
    @staticmethod
    def new_draft(identity):
        return DraftDocument(uuid.uuid4().hex, 0, 'new', identity, (), (), (), '', '', {})

def _headers(original, name):
    return next((value for key, value in original.headers.items() if key.lower() == name.lower()), ())


def _quote_context(original):
    lines=[]
    for label,key in [('发件人','from'),('时间','date'),('收件人','to'),('抄送','cc'),('主题','subject')]:
        values=_headers(original,key)
        if values: lines.append(label+'：'+', '.join(values))
    return '\n'.join(lines)

def build_reply(original, identities, reply_all=False, from_identity=None):
    identities = {value.casefold() for value in identities}
    identity = from_identity if from_identity and from_identity.casefold() in identities else (sorted(identities)[0] if identities else '')
    target = normalize_recipients(_headers(original, 'reply-to') or _headers(original, 'from'))
    if not _headers(original,'reply-to') and target and all(address.casefold() in identities for address in target):
        target = normalize_recipients(_headers(original, 'to')) or target
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
    if not to and not cc and target: to=tuple(target)
    message_id = ' '.join(_headers(original, 'message-id'))
    refs = ' '.join(_headers(original, 'references'))
    reference_headers = {'In-Reply-To': message_id, 'References': (refs + ' ' + message_id).strip()} if message_id else {}
    subject = next(iter(_headers(original, 'subject')), '')
    if not subject.lower().startswith('re:'): subject = 'Re: ' + subject
    quote='\n\n---------- 原始邮件 ----------\n'+_quote_context(original)+'\n\n'+'\n'.join('> '+line for line in original.text.splitlines())
    return DraftDocument(uuid.uuid4().hex, 0, 'reply_all' if reply_all else 'reply', identity, to, cc, (), subject, quote, reference_headers, original.encryption_state != 'none', original.remote_id)

def build_forward(original, identity):
    subject = next(iter(_headers(original, 'subject')), '')
    return DraftDocument(uuid.uuid4().hex, 0, 'forward', identity, (), (), (), 'Fwd: ' + subject, '\n\n---------- 转发正文（不含原附件） ----------\n' + _quote_context(original)+'\n\n'+original.text, {}, original.encryption_state != 'none')
