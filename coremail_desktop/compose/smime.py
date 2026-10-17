"""Build an outgoing S/MIME (EnvelopedData) message from a draft.

The shape matches what the official client sends and what the deployment
accepted in the 2026-10-17 send-to-self: routing headers in the clear, a
top-level application/pkcs7-mime part, and the whole body (text, HTML,
attachments) inside the envelope. Bcc never appears in any header.
"""
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from coremail_desktop.compose.recipients import normalize_recipients
from coremail_desktop.crypto.backend import encrypt_envelope_der
from coremail_desktop.mime.sanitizer import html_to_text


def inner_entity(draft) -> bytes:
    body = EmailMessage()
    if getattr(draft, 'is_html', False):
        body.set_content(html_to_text(draft.body) or ' ')
        body.add_alternative(draft.body, subtype='html')
    else:
        body.set_content(draft.body)
    for attachment in draft.attachments:
        maintype, _, subtype = attachment.media_type.partition('/')
        body.add_attachment(attachment.payload, maintype=maintype, subtype=subtype or 'octet-stream',
                            filename=attachment.name)
    # Only the MIME entity is encrypted; message headers live on the outer part.
    for name in ('MIME-Version',):
        del body[name]
    return body.as_bytes(policy=body.policy.clone(linesep='\r\n'))


def build_encrypted_message(draft, certificates, display_name='') -> bytes:
    """certificates: CertificateInfo for every recipient plus the sender (for the sent copy)."""
    envelope = encrypt_envelope_der(inner_entity(draft), tuple(certificates))
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
    outer['x-cm-smime'] = 'encrypted'
    outer.set_content(envelope, maintype='application', subtype='pkcs7-mime', cte='base64',
                      disposition='attachment', filename='smime.p7m',
                      params={'smime-type': 'enveloped-data', 'name': 'smime.p7m'})
    return outer.as_bytes(policy=outer.policy.clone(linesep='\r\n'))
