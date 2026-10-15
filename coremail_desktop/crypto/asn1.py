"""Minimal DER reader for CMS structures.

This is ASN.1 parsing only: no cryptographic algorithm is implemented here. All
primitives come from pyca/cryptography.
"""


class DerError(ValueError):
    pass


def read_tlv(data: bytes, offset: int):
    """Return (tag, content_start, content_length, next_offset)."""
    if offset + 2 > len(data):
        raise DerError('truncated element')
    tag = data[offset]
    first = data[offset + 1]
    start = offset + 2
    if first & 0x80:
        count = first & 0x7F
        if count == 0:
            raise DerError('indefinite length is not valid DER')
        if start + count > len(data):
            raise DerError('truncated length')
        length = int.from_bytes(data[start:start + count], 'big')
        start += count
    else:
        length = first
    if start + length > len(data):
        raise DerError('element length exceeds input')
    return tag, start, length, start + length


def element(data: bytes, offset: int = 0):
    tag, start, length, nxt = read_tlv(data, offset)
    return tag, data[start:start + length], nxt


def decode_oid(body: bytes) -> str:
    if not body:
        raise DerError('empty OID')
    first = body[0]
    parts = [str(first // 40), str(first % 40)]
    value = 0
    for byte in body[1:]:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            parts.append(str(value))
            value = 0
    return '.'.join(parts)


def children(body: bytes):
    offset = 0
    while offset < len(body):
        tag, chunk, nxt = element(body, offset)
        yield tag, chunk
        offset = nxt


def expect(body: bytes, tag: int) -> bytes:
    got, chunk, _ = element(body, 0)
    if got != tag:
        raise DerError('expected tag 0x%02X, found 0x%02X' % (tag, got))
    return chunk


SEQUENCE = 0x30
INTEGER = 0x02
OCTET_STRING = 0x04
OID = 0x06
SET = 0x31
CONTEXT = 0xA0
