"""Read-only decoder for the official Coremail client's local archive (archmails/*.cmpack).

The layout follows the user's verified coremail-reader (decode_cache.mjs, 2026-10-12):

  .cmpack  'CMDR' directory, 1024-byte pages; entries of 64 bytes (data page number,
           record name '*.data'); the first 15 at 576, the rest on one extension page.
           Each entry points at a 'PGFN' page: record length and one contiguous data extent.
  record   'cmms' header (decoded length at 16), then 'CMCD' chunks (output and input
           sizes, 32-byte header, one Zstandard frame each), a zero chunk and a 'CMCT'
           table repeating every chunk's position and sizes. Older records are a bare
           Zstandard frame instead.
  original the decoded bytes cut to the length, XOR-masked with one byte: the key is the
           one that turns the start into RFC 822 headers.

Anything else is refused (ARCHIVE_UNSUPPORTED) rather than guessed at; files are only
ever opened for reading.
"""
import io
import os
import re
import struct

from coremail_desktop.errors import AppError

PAGE = 1024
PACK_LIMIT = 512 * 1024 * 1024
RECORD_LIMIT = 64 * 1024 * 1024
OUTPUT_LIMIT = 128 * 1024 * 1024
MAGIC = b'\x28\xb5\x2f\xfd'
_FIRST = re.compile(rb'^[A-Za-z][A-Za-z0-9-]*:')
_KNOWN = re.compile(rb'^(?:From|Subject|Date|Received|Return-Path|Message-ID|MIME-Version):', re.I | re.M)
_TABLES = [bytes(b ^ key for b in range(256)) for key in range(256)]


def _bad():
    return AppError('ARCHIVE_UNSUPPORTED')


def entries(buffer):
    """-> [{name, offset, length}] or {name, error} for a directory entry that is not usable."""
    if len(buffer) < 1536 or buffer[:4] != b'CMDR' or struct.unpack_from('<I', buffer, 260)[0] != PAGE:
        raise _bad()
    extension, extra, count = struct.unpack_from('<QQQ', buffer, 512)
    if count > 31 or (count > 15 and (extra != 1 or not extension)):
        raise _bad()
    result = []
    for i in range(count):
        at = 576 + i * 64 if i < 15 else 512 + extension * PAGE + (i - 15) * 64
        if at + 64 > len(buffer):
            raise _bad()
        name = buffer[at + 8:at + 64].split(b'\0', 1)[0].decode('utf-8', 'replace')
        block = struct.unpack_from('<Q', buffer, at)[0]
        page = 512 + block * PAGE
        try:
            if not block or page + PAGE > len(buffer) or buffer[page:page + 4] != b'PGFN':
                raise ValueError('page')
            if struct.unpack_from('<Q', buffer, page + 8)[0] != 0:
                raise ValueError('fragmented')
            length = struct.unpack_from('<Q', buffer, page + 24)[0]
            data_block, pages = struct.unpack_from('<QQ', buffer, page + 64)
            offset = 512 + data_block * PAGE
            if not data_block or not pages or pages * PAGE < length:
                raise ValueError('extent')
            if not name.endswith('.data') or not length or offset + length > len(buffer):
                raise ValueError('bounds')
            result.append({'name': name, 'offset': offset, 'length': length})
        except ValueError as error:
            result.append({'name': name or f'#{i}', 'error': str(error)})
    return result


def _zstd(data, limit):
    import zstandard
    try:
        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data), read_across_frames=False)
        out = reader.read(limit + 1)
    except zstandard.ZstdError:
        raise _bad() from None
    if len(out) > limit:
        raise _bad()
    return out


def unmask(raw):
    head = raw[:4096]
    for key, table in enumerate(_TABLES):
        probe = head.translate(table) if key else head
        if _FIRST.match(probe) and _KNOWN.search(probe):
            return raw.translate(table) if key else raw
    raise _bad()


def decode(record, limit=OUTPUT_LIMIT):
    """One archived record -> the original message bytes."""
    if len(record) > RECORD_LIMIT:
        raise _bad()
    if record[:4] != b'cmms':
        start = record.find(MAGIC)
        if start < 0:
            raise _bad()
        return unmask(_zstd(record[start:], limit))
    if len(record) < 64:
        raise _bad()
    total = struct.unpack_from('<Q', record, 16)[0]
    if total > limit:
        raise _bad()
    chunks, table, at, size = [], [], 64, 0
    while True:
        if at + 32 > len(record) or record[at:at + 4] != b'CMCD':
            raise _bad()
        output, given = struct.unpack_from('<II', record, at + 4)
        if not output and not given:
            trailer = at + 32 + len(table) * 16
            if trailer + 8 > len(record) or record[trailer:trailer + 4] != b'CMCT' \
                    or struct.unpack_from('<I', record, trailer + 4)[0] != len(table) * 16:
                raise _bad()
            for i, expected in enumerate(table):
                if struct.unpack_from('<QII', record, at + 32 + i * 16) != expected:
                    raise _bad()
            break
        if not output or not given or at + 32 + given > len(record) or size + output > limit:
            raise _bad()
        chunk = _zstd(record[at + 32:at + 32 + given], output)
        if len(chunk) != output:
            raise _bad()
        chunks.append(chunk)
        table.append((at, output, given))
        size += output
        at += 32 + given
    if size < total or size - total > 15:
        raise _bad()
    return unmask(b''.join(chunks)[:total])


def _stamp(path):
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def read_pack(path):
    """-> (bytes, (size, mtime_ns)). ARCHIVE_CHANGED if the client wrote it meanwhile."""
    before = _stamp(path)
    if before[0] > PACK_LIMIT:
        raise _bad()
    with open(path, 'rb') as handle:
        data = handle.read()
    if _stamp(path) != before or len(data) != before[0]:
        raise AppError('ARCHIVE_CHANGED')
    return data, before


def read_record(path, offset, length, stamp):
    """The original of one record, provided the pack is still the one catalogued."""
    try:
        if _stamp(path) != tuple(stamp):
            raise AppError('ARCHIVE_CHANGED')
        with open(path, 'rb') as handle:
            handle.seek(offset)
            record = handle.read(length)
    except OSError:
        raise AppError('ARCHIVE_NOT_FOUND') from None
    if len(record) != length:
        raise AppError('ARCHIVE_CHANGED')
    return decode(record)
