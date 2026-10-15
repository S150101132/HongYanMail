"""Atomic DPAPI records. A failed write never acknowledges a saved revision."""
import json
import os
import re
import threading
import uuid
from pathlib import Path
from coremail_desktop.errors import AppError
from coremail_desktop.models import DraftDocument
from coremail_desktop.storage.dpapi import DataProtector

def draft_dict(draft):
    return {name: dict(value) if name == 'reference_headers' else value for name in draft.__dataclass_fields__ for value in (getattr(draft, name),)}

def decode_draft(value):
    value = dict(value)
    for key in ('to', 'cc', 'bcc'): value[key] = tuple(value[key])
    return DraftDocument(**value)

class ProtectedRecords:
    def __init__(self, directory, protector=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.protector = protector or DataProtector()
        self._lock = threading.RLock()
    def _path(self, local_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', local_id): raise AppError('INVALID_LOCAL_ID')
        return self.directory / (local_id + '.bin')
    def _read(self, local_id):
        return json.loads(self.protector.unprotect(self._path(local_id).read_bytes()).decode('utf-8'))
    def _write(self, local_id, value):
        path = self._path(local_id)
        ciphertext = self.protector.protect(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    def list_ids(self):
        return sorted(path.stem for path in self.directory.glob('*.bin'))

class DraftStore(ProtectedRecords):
    def save(self, draft):
        with self._lock:
            path = self._path(draft.local_id)
            if path.exists():
                current = self.load(draft.local_id)
                if current.revision > draft.revision or (current.revision == draft.revision and current != draft):
                    raise AppError('STALE_REVISION')
            self._write(draft.local_id, draft_dict(draft))
            return draft.revision
    def load(self, local_id):
        with self._lock: return decode_draft(self._read(local_id))
