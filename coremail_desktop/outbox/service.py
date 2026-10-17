"""A durable send intent precedes any possibly irreversible submission."""
import hashlib
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from coremail_desktop.models import SendJob
from coremail_desktop.errors import AppError
from coremail_desktop.compose.recipients import normalize_recipients

class OutboxService:
    def __init__(self,adapter,journal,prepare_encrypted=None):
        self.adapter, self.journal = adapter,journal
        self.prepare_encrypted = prepare_encrypted
        self._lock = threading.RLock()
        self._snapshots = {(job.draft.local_id,job.draft.revision):job.local_id for job in journal.recover() if job.state != 'Failed'}
    def request_send(self,draft):
        with self._lock:
            key = (draft.local_id,draft.revision)
            if key in self._snapshots: return self._snapshots[key]
            # Dataclass maps and recipient tuples are immutable snapshot values.
            job = SendJob(uuid.uuid4().hex,draft)
            self.journal.persist(job)
            self._snapshots[key] = job.local_id
            try:
                envelope = normalize_recipients(draft.to + draft.cc + draft.bcc)
                if not envelope: raise AppError('RECIPIENT_REQUIRED')
                normalize_recipients((draft.from_identity,))
                if draft.encryption_required:
                    if self.prepare_encrypted is None: raise AppError('ENCRYPTION_REQUIRED')
                    if not getattr(self.adapter,'supports_encrypted_submission',False): raise AppError('ENCRYPTED_SUBMISSION_UNVERIFIED')
                    payload = self.prepare_encrypted(draft)
                    if not isinstance(payload,bytes) or not payload: raise AppError('INVALID_ENCRYPTED_PAYLOAD')
                    job = replace(job,payload=payload,payload_digest=hashlib.sha256(payload).hexdigest(),encrypted=True,envelope_recipients=envelope)
                else:
                    job = replace(job,compose_id=self.adapter.create_compose(draft),envelope_recipients=envelope)
            except Exception as error:
                code=error.code if isinstance(error,AppError) else 'INTERNAL_ERROR'
                # This branch precedes deliver. A user-requested retry is safe;
                # accepted/unknown/submitting snapshots remain non-replayable.
                try: self.journal.persist(replace(job,state='Failed',failure_code=code))
                finally: self._snapshots.pop(key,None)
                if code in ('SESSION_EXPIRED','NOT_AUTHENTICATED'): raise
                return job.local_id
            job = replace(job,state='Submitting',submitted_at=datetime.now(timezone.utc).isoformat())
            try: self.journal.persist(job)
            except Exception:
                self._snapshots.pop(key,None)  # deliver has not run.
                raise
            try:
                response = self.adapter.submit_encrypted(job.payload,envelope,draft) if job.encrypted else self.adapter.deliver(job.compose_id,draft)
                if not isinstance(response,dict) or response.get('code') != 'S_OK': raise AppError('INVALID_RESPONSE')
            except Exception:
                job = replace(job,state='Unknown')
            else:
                job = replace(job,state='Accepted')
            # If this write fails, prior durable Submitting becomes Unknown on recovery.
            try:
                self.journal.persist(job)
            except Exception:
                raise AppError('SEND_OUTCOME_UNKNOWN', category='unknown') from None
            return job.local_id
    def recover_pending(self):
        return [job for job in self.journal.recover() if job.state in ('Unknown','Preparing','Submitting','Reconciling')]
    def reconcile(self,local_id):
        with self._lock:
            job = self.journal.load(local_id)
            if job.state != 'Unknown': return job
            self.journal.persist(replace(job,state='Reconciling'))
            matches = []
            try:
                for candidate in self.adapter.find_sent_candidates(job,50):
                    if candidate.subject != job.draft.subject: continue
                    raw = self.adapter.fetch_mime(candidate.remote_id)
                    message = BytesParser(policy=policy.default).parsebytes(raw)
                    # Bcc and encrypted envelopes cannot be positively identified with this evidence.
                    if job.encrypted or job.draft.bcc: continue
                    addresses = lambda name: {addr.casefold() for _,addr in getaddresses([str(message.get(name,''))])}
                    body = message.get_body(preferencelist=('plain',)) if message.is_multipart() else message
                    text = body.get_content() if body and body.get_content_type() == 'text/plain' else None
                    if (addresses('From') == {job.draft.from_identity.casefold()} and addresses('To') == {x.casefold() for x in job.draft.to} and addresses('Cc') == {x.casefold() for x in job.draft.cc} and text is not None and text.replace('\r\n','\n').rstrip('\n') == job.draft.body.replace('\r\n','\n').rstrip('\n') and message.get('Message-ID')):
                        matches.append(candidate)
            except Exception:
                pass
            # Without a correlation id returned by submission, identical historical mail
            # is ambiguous even if exactly one candidate exists. Never guess acceptance.
            job = replace(job,state='Unknown')
            self.journal.persist(job)
            return job
