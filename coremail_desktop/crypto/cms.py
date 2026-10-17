"""CMS EnvelopedData decryption for the algorithms this deployment actually uses.

ASN.1 parsing lives in asn1.py and every cryptographic primitive comes from
pyca/cryptography: no cipher, hash or padding is implemented here. This module
exists because the library's high-level pkcs7 decrypt accepts only AES-CBC, while
this deployment encrypts content with 3DES-CBC, so the same primitives have to be
driven directly.

Structure handling follows RFC 5652 but tolerates real-world variants observed in
the supplier build:
  * EncryptedContentInfo may carry its ciphertext in an OCTET STRING or in the
    primitive context tag [0] (IMPLICIT tagging);
  * the outermost ContentInfo may be signedData wrapping the enveloped data.
"""
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import padding as symmetric_padding
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from . import asn1
from .models import CryptoError

# CMS content types (RFC 5652 section 14). Verified against an OpenSSL-generated
# envelope as well as the real message: 1.7.3 is envelopedData.
DATA = '1.2.840.113549.1.7.1'
SIGNED_DATA = '1.2.840.113549.1.7.2'
ENVELOPED_DATA = '1.2.840.113549.1.7.3'
DIGESTED_DATA = '1.2.840.113549.1.7.5'
ENCRYPTED_DATA = '1.2.840.113549.1.7.6'

DES_EDE3_CBC = '1.2.840.113549.3.7'
AES128_CBC = '2.16.840.1.101.3.4.1.2'
AES192_CBC = '2.16.840.1.101.3.4.1.22'
AES256_CBC = '2.16.840.1.101.3.4.1.42'
RSA_ENCRYPTION = '1.2.840.113549.1.1.1'

# algorithm OID -> (display name, key length, block length)
SUPPORTED_CONTENT_ALGORITHMS = {
    DES_EDE3_CBC: ('des-ede3-cbc', 24, 8),
    AES128_CBC: ('aes128-cbc', 16, 16),
    AES192_CBC: ('aes192-cbc', 24, 16),
    AES256_CBC: ('aes256-cbc', 32, 16),
}

MAX_CIPHERTEXT = 64 * 1024 * 1024
CONTEXT_0 = 0xA0
CONTEXT_PRIMITIVE_0 = 0x80
CONTEXT_2 = 0xA2
CONTEXT_PRIMITIVE_2 = 0x82


class Enveloped:
    """Parsed EncryptedContentInfo plus its recipients."""

    def __init__(self, version, recipients, algorithm, iv, ciphertext):
        self.version = version
        self.recipients = recipients
        self.algorithm = algorithm
        self.iv = iv
        self.ciphertext = ciphertext


def _collect_octets(body: bytes) -> bytes:
    """Concatenate primitive OCTET STRINGs from an IMPLICIT [0]/[2] wrapper."""
    chunks = []
    offset = 0
    while offset < len(body):
        tag, chunk, offset = asn1.element(body, offset)
        if tag in (asn1.OCTET_STRING, CONTEXT_PRIMITIVE_0, CONTEXT_PRIMITIVE_2):
            chunks.append(chunk)
        elif tag & 0x20:
            chunks.append(_collect_octets(chunk))
        else:
            chunks.append(chunk)
    return b''.join(chunks)


def parse_encrypted_content_info(body: bytes) -> tuple:
    """Return (algorithm_oid, iv, ciphertext) from an EncryptedContentInfo."""
    fields = list(asn1.children(body))
    if len(fields) < 2:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    # contentType is a plain OID; the algorithm identifier is a SEQUENCE.
    index = 0
    if fields[index][0] == asn1.OID:
        index += 1
    if index >= len(fields) or fields[index][0] != asn1.SEQUENCE:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    algorithm_fields = list(asn1.children(fields[index][1]))
    if not algorithm_fields or algorithm_fields[0][0] != asn1.OID:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    algorithm = asn1.decode_oid(algorithm_fields[0][1])
    iv = b''
    if len(algorithm_fields) > 1:
        iv = algorithm_fields[1][1]
    index += 1
    ciphertext = b''
    while index < len(fields):
        tag, chunk = fields[index]
        if tag == asn1.OCTET_STRING:
            ciphertext += chunk
        elif tag in (CONTEXT_PRIMITIVE_0, CONTEXT_PRIMITIVE_2):
            ciphertext += chunk
        elif tag in (CONTEXT_0, CONTEXT_2):
            ciphertext += _collect_octets(chunk)
        index += 1
    if not ciphertext:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    return algorithm, iv, ciphertext


def parse_enveloped_data(der: bytes) -> Enveloped:
    """Parse a ContentInfo whose content type is envelopedData."""
    tag, content_info, _ = asn1.element(der, 0)
    if tag != asn1.SEQUENCE:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    fields = list(asn1.children(content_info))
    if not fields or fields[0][0] != asn1.OID:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    content_type = asn1.decode_oid(fields[0][1])
    if content_type != ENVELOPED_DATA:
        raise CryptoError('UNSUPPORTED_ENCRYPTION_FORMAT')
    if len(fields) < 2 or fields[1][0] != CONTEXT_0:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    return parse_enveloped_content(fields[1][1])


def _unwrap_explicit(body: bytes) -> bytes:
    """Skip an EXPLICIT [n] wrapper that contains exactly one SEQUENCE.

    RFC 5652 defines content as IMPLICIT, but the supplier build wraps it in an
    explicit tag, so both shapes must parse.
    """
    try:
        inner = list(asn1.children(body))
    except asn1.DerError:
        return body
    if len(inner) == 1 and inner[0][0] == asn1.SEQUENCE:
        return inner[0][1]
    return body


def parse_enveloped_content(body: bytes) -> Enveloped:
    fields = list(asn1.children(_unwrap_explicit(body)))
    if len(fields) < 3:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    version = int.from_bytes(fields[0][1], 'big') if fields[0][0] == asn1.INTEGER else 0
    recipients = _recipient_entries(fields[1])
    algorithm, iv, ciphertext = parse_encrypted_content_info(fields[-1][1])
    return Enveloped(version, recipients, algorithm, iv, ciphertext)


def _recipient_entries(field) -> list:
    tag, body = field
    if tag == asn1.SET:
        return [chunk for _, chunk in asn1.children(body)]
    if tag in (CONTEXT_0, CONTEXT_2):
        try:
            inner_tag, inner_body, _ = asn1.element(body, 0)
        except asn1.DerError:
            return [body]
        if inner_tag == asn1.SET:
            return [chunk for _, chunk in asn1.children(inner_body)]
        return [chunk for _, chunk in asn1.children(body)]
    raise CryptoError('INVALID_ENCRYPTED_MESSAGE')


def _key_transport_fields(entry: bytes):
    """Extract (algorithm_oid, wrapped_key) from a KeyTransRecipientInfo.

    Fields are selected by type rather than by position, because the recipient
    identifier is itself a SEQUENCE and can be mistaken for the algorithm.
    """
    try:
        fields = list(asn1.children(entry))
    except asn1.DerError:
        return None, None
    algorithm = None
    wrapped = None
    for tag, chunk in fields:
        if tag == asn1.SEQUENCE and algorithm is None:
            candidate = list(asn1.children(chunk))
            if candidate and candidate[0][0] == asn1.OID:
                algorithm = asn1.decode_oid(candidate[0][1])
        elif tag == asn1.OCTET_STRING and wrapped is None:
            wrapped = chunk
    return algorithm, wrapped


def unwrap_content_keys(recipient_entries, private_key, key_size: int) -> list:
    """Return candidate content keys, most plausible first.

    RSA PKCS#1 v1.5 unwrap cannot prove which recipient entry belongs to this key:
    unwrapping a foreign entry with the wrong key usually fails, but can by chance
    yield a byte string of the right length. Length filtering plus PKCS#7 padding
    validation by the caller is what actually confirms the key.
    """
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise CryptoError('CERT_ALGORITHM_UNSUPPORTED')
    matching, other, unsupported = [], [], False
    for entry in recipient_entries:
        algorithm, wrapped = _key_transport_fields(entry)
        if algorithm is None or not wrapped:
            continue
        if algorithm != RSA_ENCRYPTION:
            unsupported = True
            continue
        try:
            candidate = private_key.decrypt(wrapped, asymmetric_padding.PKCS1v15())
        except Exception:
            continue
        if len(candidate) == key_size:
            matching.append(candidate)
        else:
            other.append(candidate)
    if not matching and not other:
        if unsupported:
            raise CryptoError('ENCRYPTED_ALGORITHM_UNSUPPORTED')
        raise CryptoError('DECRYPT_FAILED')
    # Wrong-key unwraps are kept as a fallback only; the caller validates padding.
    return matching + other


def strip_pkcs7_padding(padded: bytes, block_size: int) -> bytes:
    """Remove RFC 5652 / PKCS#7 trailing padding.

    Done explicitly rather than through a library unpadder: the high-level helper
    rejects some valid short paddings, and this is a length check on bytes already
    produced by a real cipher, not a cryptographic operation.
    """
    if not padded or len(padded) % block_size:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    count = padded[-1]
    if not 1 <= count <= block_size:
        raise CryptoError('DECRYPT_FAILED')
    if padded[-count:] != bytes([count]) * count:
        raise CryptoError('DECRYPT_FAILED')
    return padded[:-count]


def decrypt_content(ciphertext: bytes, key: bytes, algorithm: str, iv: bytes) -> bytes:
    if len(ciphertext) > MAX_CIPHERTEXT:
        raise CryptoError('MESSAGE_TOO_LARGE')
    if algorithm not in SUPPORTED_CONTENT_ALGORITHMS:
        raise CryptoError('ENCRYPTED_ALGORITHM_UNSUPPORTED')
    name, key_size, block_size = SUPPORTED_CONTENT_ALGORITHMS[algorithm]
    if len(key) < key_size or not ciphertext or len(ciphertext) % block_size:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    if len(iv) != block_size:
        raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
    try:
        if algorithm == DES_EDE3_CBC:
            # 3DES now lives in the decrepit namespace; the primitive is still
            # supported even though the high-level pkcs7 layer refuses it.
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
            primitive = TripleDES(key[:key_size])
        else:
            primitive = algorithms.AES(key[:key_size])
        decryptor = Cipher(primitive, modes.CBC(iv)).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
    except UnsupportedAlgorithm:
        raise CryptoError('ENCRYPTED_ALGORITHM_UNSUPPORTED') from None
    except ValueError:
        raise CryptoError('DECRYPT_FAILED') from None
    plaintext = strip_pkcs7_padding(padded, block_size)
    if len(plaintext) > MAX_CIPHERTEXT:
        raise CryptoError('MESSAGE_TOO_LARGE')
    return plaintext


SIGNED_DATA_MIME_TYPES = ('application/pkcs7-mime', 'application/x-pkcs7-mime')


def unwrap_mime_entity(mime_bytes: bytes) -> bytes:
    """Return the DER of a MIME-wrapped CMS entity, or the input unchanged.

    Decrypted content is itself a complete MIME entity (application/pkcs7-mime),
    so one base64 layer has to be removed before the DER can be parsed.
    """
    from email import policy
    from email.parser import BytesParser
    try:
        message = BytesParser(policy=policy.default).parsebytes(mime_bytes)
    except Exception:
        return mime_bytes
    if message.get_content_type() in SIGNED_DATA_MIME_TYPES:
        payload = message.get_payload(decode=True)
        if payload:
            return payload
    return mime_bytes


def signed_data_encapsulated(der: bytes) -> bytes:
    """Return the eContent OCTET STRING of a signedData ContentInfo (b'' if detached)."""
    try:
        tag, content_info, _ = asn1.element(der, 0)
        if tag != asn1.SEQUENCE:
            return b''
        fields = list(asn1.children(content_info))
        if len(fields) < 2 or fields[1][0] != CONTEXT_0:
            return b''
        signed = list(asn1.children(_unwrap_explicit(fields[1][1])))
        if len(signed) < 3:
            return b''
        encap = signed[2]
        if encap[0] != asn1.SEQUENCE:
            return b''
        for tag, chunk in list(asn1.children(encap[1]))[1:]:
            if tag in (asn1.OCTET_STRING, CONTEXT_PRIMITIVE_0):
                return chunk
            if tag == CONTEXT_0:
                # eContent may be wrapped again in an explicit context tag.
                for inner_tag, inner in asn1.children(chunk):
                    if inner_tag in (asn1.OCTET_STRING, CONTEXT_PRIMITIVE_0):
                        return inner
                return _collect_octets(chunk)
    except (asn1.DerError, IndexError):
        return b''
    return b''


def signed_data_summary(der: bytes) -> dict:
    """Report the digest and signature algorithms of a signedData container.

    Reported only: this module does not verify signatures, so the interface must
    not treat these values as evidence of authenticity.
    """
    summary = {'digest_algorithms': [], 'signature_algorithms': [], 'signer_count': 0}
    try:
        tag, content_info, _ = asn1.element(der, 0)
        fields = list(asn1.children(content_info))
        if len(fields) < 2 or fields[1][0] != CONTEXT_0:
            return summary
        signed = list(asn1.children(_unwrap_explicit(fields[1][1])))
        if len(signed) < 2:
            return summary
        for tag, chunk in asn1.children(signed[1][1]):
            inner = list(asn1.children(chunk))
            if inner and inner[0][0] == asn1.OID:
                summary['digest_algorithms'].append(asn1.decode_oid(inner[0][1]))
        signer_set = signed[-1]
        if signer_set[0] in (asn1.SET, CONTEXT_0):
            body = signer_set[1]
            if signer_set[0] == CONTEXT_0:
                inner = list(asn1.children(body))
                body = inner[0][1] if inner else body
            for tag, chunk in asn1.children(body):
                summary['signer_count'] += 1
                for field_tag, field in asn1.children(chunk):
                    if field_tag == asn1.SEQUENCE:
                        inner = list(asn1.children(field))
                        if inner and inner[0][0] == asn1.OID:
                            summary['signature_algorithms'].append(asn1.decode_oid(inner[0][1]))
    except (asn1.DerError, IndexError):
        return summary
    return summary


def peel_message(der: bytes, private_key, max_layers: int = 4) -> dict:
    """Peel encryption and signature layers down to the readable message.

    Returns a dict with the final bytes, the content algorithms used, and what was
    unwrapped. Signature layers are reported, not verified: this module performs no
    signature validation, which is tracked separately.
    """
    layers = []
    current = der
    for _ in range(max_layers):
        if looks_like_signed_data(current):
            content = signed_data_encapsulated(current)
            if not content:
                # Detached signature: nothing further to read.
                return {'bytes': b'', 'layers': layers + ['signed-data-detached'],
                        'algorithms': [layer['algorithm'] for layer in layers],
                        'signature_status': 'present_not_verified'}
            layers.append({'layer': 'signed-data', 'algorithm': None,
                           'bytes': len(content)})
            current = content
            continue
        if looks_like_enveloped_data(current):
            plaintext, name = decrypt_message(current, private_key)
            layers.append({'layer': 'enveloped-data', 'algorithm': name,
                           'bytes': len(plaintext)})
            current = unwrap_mime_entity(plaintext)
            continue
    result = {'bytes': current,
              'layers': layers,
              'algorithms': [layer['algorithm'] for layer in layers if layer['algorithm']],
              'signed': any(layer['layer'] == 'signed-data' for layer in layers),
              'signature_status': 'present_not_verified' if any(
                  layer['layer'] == 'signed-data' for layer in layers) else 'unsigned'}
    return result


def looks_like_enveloped_data(der: bytes) -> bool:
    try:
        tag, body, _ = asn1.element(der, 0)
        if tag != asn1.SEQUENCE:
            return False
        fields = list(asn1.children(body))
        return bool(fields) and fields[0][0] == asn1.OID \
            and asn1.decode_oid(fields[0][1]) == ENVELOPED_DATA
    except (asn1.DerError, IndexError):
        return False


def looks_like_signed_data(der: bytes) -> bool:
    try:
        tag, body, _ = asn1.element(der, 0)
        if tag != asn1.SEQUENCE:
            return False
        fields = list(asn1.children(body))
        return bool(fields) and fields[0][0] == asn1.OID \
            and asn1.decode_oid(fields[0][1]) == SIGNED_DATA
    except (asn1.DerError, IndexError):
        return False


def inner_content_of_signed_data(der: bytes) -> bytes:
    """Return the encapsulated content of a signedData ContentInfo (b'' if detached)."""
    return signed_data_encapsulated(der)


def declared_content_algorithm(der: bytes) -> str | None:
    """Return the content-encryption OID an envelope declares, or None.

    Lets the caller distinguish "the provider cannot do this algorithm" from "the
    private key does not match this message" when both surface as ValueError.
    """
    try:
        return parse_enveloped_data(der).algorithm
    except (CryptoError, asn1.DerError, IndexError):
        return None


def decrypt_message(der: bytes, private_key) -> tuple:
    """Decrypt an envelope, unwrapping one level of signedData if present.

    Returns (plaintext, content_algorithm_name).
    """
    if looks_like_signed_data(der):
        content = inner_content_of_signed_data(der)
        if not content:
            raise CryptoError('INVALID_ENCRYPTED_MESSAGE')
        der = content
    enveloped = parse_enveloped_data(der)
    if enveloped.algorithm not in SUPPORTED_CONTENT_ALGORITHMS:
        raise CryptoError('ENCRYPTED_ALGORITHM_UNSUPPORTED')
    name, key_size, unused_block = SUPPORTED_CONTENT_ALGORITHMS[enveloped.algorithm]
    candidates = unwrap_content_keys(enveloped.recipients, private_key, key_size)
    last_error = None
    for candidate in candidates:
        if len(candidate)<key_size:
            # Implicit RSA rejection can yield synthetic short key material.
            # The envelope parsed correctly; this is a key mismatch.
            last_error=CryptoError('DECRYPT_FAILED')
            continue
        try:
            plaintext = decrypt_content(enveloped.ciphertext, candidate,
                                       enveloped.algorithm, enveloped.iv)
        except CryptoError as error:
            # A candidate that fails padding validation is simply the wrong key.
            last_error = error
            continue
        return plaintext, name
    raise last_error or CryptoError('DECRYPT_FAILED')
