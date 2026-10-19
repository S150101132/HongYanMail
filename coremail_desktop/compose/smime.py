"""Build an outgoing S/MIME message from a draft.

The encrypted shape matches what the official client sends and what the
deployment accepted in the 2026-10-17 send-to-self: routing headers in the clear,
a top-level application/pkcs7-mime part, and the whole body (text, HTML,
attachments) inside the envelope. Signed and encrypted mail is signed first: the
envelope holds an opaque signed-data entity, as in the official client's mail.
Signed-only mail is multipart/signed with a detached signature, so clients
without S/MIME still show the body. Bcc never appears in any header.
"""
import base64
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from coremail_desktop.compose.inline import extract_inline, fill_inline
from coremail_desktop.compose.recipients import normalize_recipients
from coremail_desktop.crypto.backend import encrypt_envelope_der
from coremail_desktop.crypto.signature import sign_detached, sign_opaque
from coremail_desktop.mime.sanitizer import html_to_text


def inner_entity(draft, seven_bit=False) -> bytes:
    """seven_bit: base64 text parts, so relays cannot re-encode signed content."""
    body = EmailMessage()
    cte = {'cte': 'base64'} if seven_bit else {}
    if getattr(draft, 'is_html', False):
        # Body images become multipart/related parts referenced by cid:.
        template, images = extract_inline(draft.body)
        cids = [make_msgid(domain='hongyan.invalid')[1:-1] for _ in images]
        body.set_content(html_to_text(template) or ' ', **cte)
        body.add_alternative(fill_inline(template, ['cid:' + c for c in cids]), subtype='html', **cte)
        html_part = body.get_payload()[1]
        for cid, image in zip(cids, images):
            maintype, _, subtype = image.media_type.partition('/')
            html_part.add_related(image.payload, maintype=maintype, subtype=subtype, cid=f'<{cid}>',
                                  filename=image.name, disposition='inline')
    else:
        body.set_content(draft.body, **cte)
    for attachment in draft.attachments:
        if attachment.media_type == 'message/rfc822':
            # A forwarded message is attached as a message (7bit/8bit), never re-encoded base64.
            from email import policy as _policy
            from email.parser import BytesParser
            body.add_attachment(BytesParser(policy=_policy.default).parsebytes(attachment.payload), filename=attachment.name)
            continue
        maintype, _, subtype = attachment.media_type.partition('/')
        body.add_attachment(attachment.payload, maintype=maintype, subtype=subtype or 'octet-stream',
                            filename=attachment.name)
    # Only the MIME entity is encrypted; message headers live on the outer part.
    for name in ('MIME-Version',):
        del body[name]
    return body.as_bytes(policy=body.policy.clone(linesep='\r\n'))


def opaque_signed_entity(content: bytes, signer) -> bytes:
    """application/pkcs7-mime; smime-type=signed-data wrapping content."""
    part = EmailMessage()
    part.set_content(sign_opaque(content, *signer), maintype='application', subtype='pkcs7-mime', cte='base64',
                     disposition='attachment', filename='smime.p7m',
                     params={'smime-type': 'signed-data', 'name': 'smime.p7m'})
    del part['MIME-Version']
    return part.as_bytes(policy=part.policy.clone(linesep='\r\n'))


def _headers(draft, display_name):
    sender = normalize_recipients((draft.from_identity,))[0]
    outer = EmailMessage()
    outer['From'] = f'{display_name} <{sender}>' if display_name else sender
    if draft.to:
        outer['To'] = ', '.join(normalize_recipients(draft.to))
    if draft.cc:
        outer['Cc'] = ', '.join(normalize_recipients(draft.cc))
    outer['Subject'] = draft.subject
    outer['Date'] = formatdate(localtime=True)
    outer['Message-ID'] = make_msgid(domain=sender.rpartition('@')[2])
    for name in ('In-Reply-To', 'References'):
        value = draft.reference_headers.get(name)
        if value:
            outer[name] = value
    if getattr(draft, 'request_receipt', False):
        outer['Disposition-Notification-To'] = sender  # What the server adds for requestReadReceipt.
    return outer


def build_encrypted_message(draft, certificates, display_name='', signer=None) -> bytes:
    """certificates: CertificateInfo for every recipient plus the sender (for the sent copy).
    signer: (certificate, key, chain) to sign before encrypting, or None."""
    content = inner_entity(draft)
    if signer is not None:
        content = opaque_signed_entity(content, signer)
    envelope = encrypt_envelope_der(content, tuple(certificates))
    outer = _headers(draft, display_name)
    outer['x-cm-smime'] = 'encrypted'
    outer.set_content(envelope, maintype='application', subtype='pkcs7-mime', cte='base64',
                      disposition='attachment', filename='smime.p7m',
                      params={'smime-type': 'enveloped-data', 'name': 'smime.p7m'})
    return outer.as_bytes(policy=outer.policy.clone(linesep='\r\n'))


def build_signed_message(draft, signer, display_name='') -> bytes:
    """multipart/signed (RFC 8551): the first part is signed byte for byte, so the
    message is assembled by hand rather than re-serialized."""
    content = inner_entity(draft, seven_bit=True)
    signature = base64.encodebytes(sign_detached(content, *signer)).replace(b'\n', b'\r\n')
    boundary = '----=_hongyan_signed_' + uuid.uuid4().hex
    outer = _headers(draft, display_name)
    outer['MIME-Version'] = '1.0'
    outer['Content-Type'] = ('multipart/signed; protocol="application/pkcs7-signature"; micalg=sha-256; '
                             f'boundary="{boundary}"')
    fold = outer.policy.clone(linesep='\r\n').fold_binary
    head = b''.join(fold(name, value) for name, value in outer.items()) + b'\r\n'
    mark = boundary.encode('ascii')
    return (head + b'This is an S/MIME signed message\r\n\r\n--' + mark + b'\r\n' + content
            + b'\r\n--' + mark + b'\r\nContent-Type: application/pkcs7-signature; name="smime.p7s"\r\n'
            b'Content-Transfer-Encoding: base64\r\nContent-Disposition: attachment; filename="smime.p7s"\r\n\r\n'
            + signature + b'\r\n--' + mark + b'--\r\n')
