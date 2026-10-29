"""Offline, read-only: which signature algorithms do the cached signed messages use?

Reads the message bodies already in the app's local cache (the data folder the app
picks: HONGYAN_DATA_DIR, else %LOCALAPPDATA%/CoremailDesktopPreview, else HongyanData
beside the program; *.db opened read-only, no network). Encrypted bodies are decrypted with the remembered personal
certificate, or the pfx password typed in a local dialog. For every signer it records
only algorithm OIDs, the key type/size/curve and the verifier's status code, grouped
with counts; never content, subjects, addresses or certificate names.
"""
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from email import policy
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from coremail_desktop.crypto import asn1  # noqa: E402
from coremail_desktop.crypto.backend import decrypt_smime  # noqa: E402
from coremail_desktop.crypto.cms import CONTEXT_0, looks_like_signed_data  # noqa: E402
from coremail_desktop.crypto.formats import detect_envelope_format  # noqa: E402
from coremail_desktop.crypto.service import CryptoService  # noqa: E402
from coremail_desktop.crypto.signature import (SIGNATURE_MIME_TYPES, SIGNED_MIME_TYPES, _find_certificate,  # noqa: E402
                                               _raw_children, _signed_parts, unwrap_signed)
from coremail_desktop.storage.dpapi import DataProtector  # noqa: E402

DATA = next((Path(p) for p in (os.environ.get('HONGYAN_DATA_DIR'),
                                os.path.join(os.environ.get('LOCALAPPDATA', ''), 'CoremailDesktopPreview'))
             if p and (Path(p) / 'cache').is_dir()), ROOT / 'HongyanData')
NAMES = {'1.3.14.3.2.26': 'sha1', '2.16.840.1.101.3.4.2.4': 'sha224', '2.16.840.1.101.3.4.2.1': 'sha256',
         '2.16.840.1.101.3.4.2.2': 'sha384', '2.16.840.1.101.3.4.2.3': 'sha512', '1.2.840.113549.2.5': 'md5',
         '1.2.156.10197.1.401': 'sm3', '1.2.156.10197.1.501': 'sm2-with-sm3', '1.2.156.10197.1.301.1': 'sm2-sign',
         '1.2.156.10197.1.301': 'sm2', '1.2.840.10045.2.1': 'ec-public-key',
         '1.2.840.113549.1.1.1': 'rsa', '1.2.840.113549.1.1.5': 'sha1-rsa', '1.2.840.113549.1.1.11': 'sha256-rsa',
         '1.2.840.113549.1.1.12': 'sha384-rsa', '1.2.840.113549.1.1.13': 'sha512-rsa', '1.2.840.113549.1.1.14': 'sha224-rsa',
         '1.2.840.113549.1.1.10': 'rsassa-pss', '1.2.840.10045.4.3.2': 'ecdsa-sha256', '1.2.840.10045.4.3.3': 'ecdsa-sha384',
         '1.3.101.112': 'ed25519', '1.3.101.113': 'ed448'}


def named(oid):
    return f'{oid} ({NAMES[oid]})' if oid in NAMES else oid


def ask(title, prompt):
    import tkinter
    from tkinter import simpledialog
    root = tkinter.Tk(); root.withdraw(); root.attributes('-topmost', True)
    try:
        return simpledialog.askstring(title, prompt, show='*', parent=root)
    finally:
        root.destroy()


def cached_bodies(protector):
    for db in sorted((DATA / 'cache').glob('*.db')):
        key_path = db.with_suffix('.key')
        if db.name.endswith(('.archive.db', '.memory.db')) or not key_path.exists():
            continue
        aead = AESGCM(protector.unprotect(key_path.read_bytes()))
        connection = sqlite3.connect(f'file:{db.as_posix()}?mode=ro', uri=True)
        try:
            rows = connection.execute('SELECT mid, blob FROM bodies').fetchall()
        finally:
            connection.close()
        for mid, blob in rows:
            try:
                yield aead.decrypt(blob[:12], blob[12:], ('body:' + mid).encode('utf-8'))
            except Exception:
                continue


def unlock(protector):
    crypto = CryptoService()
    try:
        record = json.loads(protector.unprotect((DATA / 'certificate.bin').read_bytes()).decode('utf-8'))
        crypto.unlock(record['path'], record['password'])
        return crypto
    except Exception:
        pass
    pfx = next((p for base in (ROOT, *ROOT.parents) for p in sorted(base.glob('*.pfx'))), None)
    password = ask('鸿雁 · 签名算法诊断', '个人证书 (.pfx) 密码（仅本次内存使用，用于解开加密邮件）:\n取消则只看未加密的签名邮件') if pfx else None
    if password is None:
        return None
    try:
        crypto.unlock(str(pfx), password)
        return crypto
    except Exception:
        return None


def signature_der(raw):
    """The SignedData DER of a signed MIME entity, or None."""
    message = BytesParser(policy=policy.default).parsebytes(raw)
    kind = message.get_content_type()
    if kind == 'multipart/signed' and str(message.get_param('protocol', '')).lower() in SIGNATURE_MIME_TYPES:
        parts = message.get_payload()
        return parts[1].get_payload(decode=True) if isinstance(parts, list) and len(parts) == 2 else None
    if kind in SIGNED_MIME_TYPES:
        der = message.get_payload(decode=True)
        return der if der and looks_like_signed_data(der) else None
    return None


def key_shape(cert):
    if cert is None:
        return {'cert': 'missing'}
    key = cert.public_key()
    shape = {'cert_signature': named(cert.signature_algorithm_oid.dotted_string)}
    if isinstance(key, rsa.RSAPublicKey):
        shape.update(key='rsa', bits=key.key_size)
    elif isinstance(key, ec.EllipticCurvePublicKey):
        shape.update(key='ec', curve=key.curve.name, bits=key.key_size)
    elif isinstance(key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey, dsa.DSAPublicKey)):
        shape.update(key=type(key).__name__)
    else:
        shape.update(key=type(key).__name__)
    return shape


def raw_cert_key_oids(der):
    """SubjectPublicKeyInfo algorithm and parameter OIDs straight from the DER, for
    keys pyca refuses to load (SM2 curves)."""
    tbs = list(asn1.children(asn1.element(der, 0)[1]))[0][1]
    fields = list(asn1.children(tbs))
    if fields and fields[0][0] == CONTEXT_0:
        fields = fields[1:]
    algorithm = list(asn1.children(list(asn1.children(fields[5][1]))[0][1]))
    return [named(asn1.decode_oid(body)) for tag, body in algorithm if tag == asn1.OID]


def describe_signers(der):
    _, certificates, signers = _signed_parts(der)
    found = []
    for info in signers:
        parts = list(_raw_children(info))
        digest = asn1.decode_oid(asn1.expect(parts[2][1], asn1.OID))
        index = 4 if parts[3][0] == CONTEXT_0 else 3
        signature = asn1.decode_oid(asn1.expect(parts[index][1], asn1.OID))
        entry = {'digest': named(digest), 'signature': named(signature), 'signed_attrs': index == 4}
        try:
            entry.update(key_shape(_find_certificate(parts[1][0], parts[1][1], certificates)))
        except Exception:
            entry['cert'] = 'unloadable'
            entry['cert_key_oids'] = [oid for der_cert in certificates[:1] for oid in raw_cert_key_oids(der_cert)]
        found.append(entry)
    return found


def main():
    protector = DataProtector()
    crypto = None
    counts, totals = Counter(), Counter()
    for raw in cached_bodies(protector):
        totals['cached_bodies'] += 1
        layer = 'outer'
        try:
            der = signature_der(raw)
            if der is None and detect_envelope_format(raw) == 'smime-enveloped-data':
                totals['encrypted'] += 1
                if crypto is None and not totals['unlock_tried']:
                    totals['unlock_tried'] += 1
                    crypto = unlock(protector)
                if crypto is None:
                    totals['encrypted_skipped_locked'] += 1
                    continue
                raw = decrypt_smime(raw, crypto._certificate, crypto._key)
                der, layer = signature_der(raw), 'inside_encryption'
                totals['inner_type:' + BytesParser(policy=policy.default).parsebytes(raw).get_content_type()] += 1
            if der is None:
                if b'pkcs7-signature' in raw.lower() or b'signed-data' in raw.lower():
                    totals['signature_marker_not_top_level:' + layer] += 1
                continue
            totals['signed'] += 1
            report = unwrap_signed(raw)
            code = (report[1].status + ':' + report[1].code) if report else 'not_recognised'
            for signer in describe_signers(der):
                counts[json.dumps({'layer': layer, 'result': code, **signer}, ensure_ascii=False, sort_keys=True)] += 1
        except Exception as error:
            totals['error:' + type(error).__name__] += 1
    totals.pop('unlock_tried', None)
    result = {'date': time.strftime('%Y-%m-%d %H:%M'), 'data': DATA.name, 'totals': dict(totals),
              'signers': [{'count': n, **json.loads(k)} for k, n in counts.most_common()]}
    out = ROOT / 'analysis' / f'signature-algorithms-{time.strftime("%Y-%m-%d")}.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print('\n已写入', out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
