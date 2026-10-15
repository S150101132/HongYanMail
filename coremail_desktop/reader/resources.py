"""Single-document, memory-only CID ownership and budget."""
from urllib.parse import urlsplit, unquote
from coremail_desktop.mime.parser import IMAGE_TYPES


class ReaderResourceStore:
    def __init__(self, max_bytes=64 * 1024 * 1024):
        self.max_bytes = max_bytes
        self.clear()

    def clear(self):
        self.document_id = None
        self._resources = {}

    def replace(self, document_id: str, resources: dict):
        self.clear()
        accepted = {key: (mime, bytes(data)) for key, (mime, data) in resources.items() if mime in IMAGE_TYPES}
        if sum(len(data) for _, data in accepted.values()) > self.max_bytes:
            raise ValueError('CID resource budget exceeded')
        self.document_id, self._resources = document_id, accepted

    def resolve(self, document_id: str, cid: str) -> bytes | None:
        item = self._resources.get(cid) if document_id == self.document_id else None
        return item[1] if item else None

    def resource_for_url(self, url: str):
        parsed = urlsplit(url)
        if parsed.scheme != 'coremail-cid' or parsed.hostname != self.document_id or parsed.query or parsed.fragment:
            return None
        return self._resources.get(unquote(parsed.path.lstrip('/')))
